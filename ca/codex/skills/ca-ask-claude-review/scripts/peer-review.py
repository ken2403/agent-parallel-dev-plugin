#!/usr/bin/env python3
"""Capture branch + local changes, then request one fresh advisory review (stdlib only)."""
import argparse
import difflib
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile

LIMIT = 1024 * 1024
TOTAL_LIMIT = 64 * LIMIT
DIMENSIONS = {"quality", "test_rigor", "security", "consistency"}
FEATURES = ("multi_agent", "apps", "browser_use", "browser_use_external",
            "browser_use_full_cdp_access", "computer_use", "hooks", "image_generation",
            "in_app_browser", "plugins", "remote_plugin", "plugin_sharing",
            "skill_mcp_dependency_install")


class ReviewError(Exception):
    pass


def run(argv, cwd=None, env=None):
    result = subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=30)
    if result.returncode:
        raise ReviewError(f"{argv[0]} failed: {result.stderr.decode(errors='replace')[-2000:]}")
    return result.stdout


def git(repo, *args):
    # No external diff/textconv, optional index refresh, or inherited alternate Git environment.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1")
    # The option also makes older Git versions fail before touching the repository, rather
    # than silently ignoring an unsupported environment variable and starting a lazy fetch.
    return run(["git", "--no-lazy-fetch", "-c", "core.fsmonitor=false", "-C", str(repo), *args], env=env)


def resolve(repo, ref):
    return git(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()


def default_base(repo):
    for ref in ("refs/remotes/origin/HEAD",):
        try:
            return git(repo, "symbolic-ref", "--quiet", ref).decode().strip()
        except ReviewError:
            pass
    heads = git(repo, "for-each-ref", "--format=%(symref)", "refs/remotes").decode().splitlines()
    heads = sorted(set(filter(None, heads)))
    if len(heads) == 1:
        return heads[0]
    if len(heads) > 1:
        raise ReviewError("multiple remote defaults; specify --base REF")
    candidates = []
    for branch in ("main", "master"):
        for prefix in ("refs/remotes/origin/", "refs/heads/"):
            try:
                resolve(repo, prefix + branch)
                candidates.append(prefix + branch)
                break
            except ReviewError:
                pass
    if len(candidates) != 1:
        raise ReviewError("cannot determine an unambiguous default branch; specify --base REF")
    return candidates[0]


def excluded(path):
    parts = Path(path).parts
    if any(p in {".git", ".ca", ".superpowers"} for p in parts):
        return "review/session state"
    if ".claude" in parts and "worktrees" in parts:
        return "worktree state"
    name = parts[-1]
    if not name.endswith((".example", ".sample", ".template", ".dist")):
        if any(p in path for p in (".env", "credentials", "secret", "id_rsa", "id_ed25519",
                                   ".pem", ".p12", ".pfx", ".keystore")):
            return "protected path"
    return None


def safe_path(path):
    p = Path(path)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise ReviewError("unsafe repository path")
    return p


def current_file(repo, name, algorithm="sha1"):
    path = safe_path(name)
    # Anchor every component with openat + O_NOFOLLOW, including parent directory races.
    parent = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[:-1]:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            except FileNotFoundError:
                return None, None, None, None
            except OSError as error:
                if error.errno == errno.ENOTDIR:
                    info = os.stat(part, dir_fd=parent, follow_symlinks=False)
                    if stat.S_ISREG(info.st_mode):
                        return None, None, None, None  # Former child path was deleted.
                if error.errno in (errno.ENOTDIR, errno.ELOOP):
                    return None, None, "symlink or non-directory ancestor", None
                raise
            os.close(parent)
            parent = child
        try:
            info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return None, None, None, None
        if stat.S_ISLNK(info.st_mode):
            return "120000", os.fsencode(os.readlink(path.name, dir_fd=parent)), "symlink", None
        if stat.S_ISDIR(info.st_mode):
            return None, None, None, None  # Former regular file was replaced by a directory.
        if not stat.S_ISREG(info.st_mode):
            return None, None, "submodule or special file", None
        mode = "100755" if info.st_mode & 0o111 else "100644"
        if info.st_size > TOTAL_LIMIT:
            metadata = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            return mode, None, "file exceeds 64 MiB hashing limit", "metadata:" + repr(metadata)
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ReviewError("file type changed during capture")
            digest = hashlib.new(algorithm, b"blob " + str(info.st_size).encode() + b"\0")
            chunks, size = [], 0
            while True:
                chunk = handle.read(LIMIT)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                if info.st_size <= LIMIT:
                    chunks.append(chunk)
                if size > TOTAL_LIMIT:
                    raise ReviewError("file grew during capture; retry")
    finally:
        os.close(parent)
    if size != info.st_size:
        raise ReviewError("file changed size during capture; retry")
    return mode, (b"".join(chunks) if size <= LIMIT else None), ("file exceeds 1 MiB" if size > LIMIT else None), digest.hexdigest()


def capture(repo, base_sha, head):
    index = git(repo, "ls-files", "--stage", "-z")
    if any(entry.split(b"\t", 1)[0].split()[-1] != b"0" for entry in index.split(b"\0") if entry):
        raise ReviewError("unmerged index entries; resolve conflicts before review")
    flags = git(repo, "ls-files", "-t", "-z")
    skipped = {os.fsdecode(entry[2:]) for entry in flags.split(b"\0") if entry.startswith(b"S ")}
    tree = {}
    for record in git(repo, "ls-tree", "-r", "-z", base_sha).split(b"\0"):
        if record:
            meta, name = record.split(b"\t", 1)
            mode, kind, oid = meta.decode().split()
            tree[os.fsdecode(name)] = (mode, kind, oid)
    tracked = git(repo, "ls-files", "-z").split(b"\0")
    untracked = git(repo, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
    names = set(tree) | {os.fsdecode(n) for n in tracked + untracked if n}
    files, changes, omissions, fingerprints = {}, {}, [], []
    total = 0
    for name in sorted(names):
        safe_path(name)
        old = tree.get(name)
        algorithm = "sha256" if old and len(old[2]) == 64 else "sha1"
        mode, data, reason, fingerprint = current_file(repo, name, algorithm)
        if name in skipped and mode is None:
            raise ReviewError(f"unavailable skip-worktree path {name!r}; expand the sparse checkout before review")
        if fingerprint is None and data is not None:
            blob = b"blob " + str(len(data)).encode() + b"\0" + data
            fingerprint = hashlib.new(algorithm, blob).hexdigest()
        fingerprints.append((name, mode, reason, fingerprint))
        old_mode = old[0] if old else None
        old_data = None
        # Compare raw blobs ourselves: worktree git diff invokes arbitrary clean filters and
        # can hide assume-unchanged/skip-worktree edits. No repository programs run here.
        changed = bool(reason) or old is not None or data is not None
        if old and old[1] == "blob":
            if fingerprint is not None:
                changed = fingerprint != old[2] or mode != old_mode
        protected = excluded(name)
        if protected:
            if changed:
                omissions.append({"file": name, "reason": protected})
            continue  # Never export protected bytes, even when Git flags hide their changes.
        if old and old[1] != "blob":
            reason = "submodule"
        if data is not None:
            total += len(data)
            files[name] = (mode, data)
        # Read baseline blobs only for changed paths; callers remain in the snapshot.
        if changed and old and old[1] == "blob":
            size = int(git(repo, "cat-file", "-s", old[2]))
            if size > LIMIT:
                reason = "baseline file exceeds 1 MiB"
            else:
                old_data = git(repo, "cat-file", "blob", old[2])
                total += len(old_data)
        if total > TOTAL_LIMIT:
            raise ReviewError("capture exceeds 64 MiB; review a smaller repository")
        if not changed:
            continue
        if old_mode == "120000":
            reason = "symlink"
        if any(b"\0" in d for d in (data, old_data) if d is not None):
            reason = "binary file"
        try:
            for content in (data, old_data):
                if content is not None:
                    content.decode("utf-8")
        except UnicodeDecodeError:
            reason = "non-UTF-8 file"
        if reason:
            omissions.append({"file": name, "reason": reason})
        elif (mode, data) != (old_mode, old_data):
            changes[name] = (old_mode, old_data, mode, data)
    state = hashlib.sha256(index + flags + json.dumps(fingerprints).encode()
                           + json.dumps(sorted(os.fsdecode(n) for n in untracked if n)).encode()).hexdigest()
    if resolve(repo, "HEAD") != head:
        raise ReviewError("HEAD changed during capture; retry")
    return files, changes, omissions, state


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def prepare(repo, packet, base, focus):
    root = Path(git(repo, "rev-parse", "--show-toplevel").decode().strip())
    head = resolve(root, "HEAD")
    base_ref = base or default_base(root)
    base_sha = resolve(root, base_ref)
    merge_base = git(root, "merge-base", base_sha, head).decode().strip()
    captured = capture(root, merge_base, head)
    if captured != capture(root, merge_base, head):
        raise ReviewError("working files or index changed during capture; retry")
    files, changes, omissions, state = captured
    if not changes and not omissions:
        raise ReviewError("no net changes against the default branch merge base")
    snapshot = packet / "snapshot"
    snapshot.mkdir()
    digest = hashlib.sha256()
    digest.update(state.encode())  # Bind omitted-file fingerprints without exporting their contents.
    digest.update(json.dumps([base_sha, merge_base, head, focus, omissions], sort_keys=True).encode())
    for name, (mode, data) in sorted(files.items()):
        dest = snapshot / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)  # Symlinks become inert link-target text, never actual links.
        digest.update(json.dumps([name, mode, hashlib.sha256(data).hexdigest()]).encode())
    patch = []
    for name, (old_mode, old, mode, new) in changes.items():
        label = json.dumps(name, ensure_ascii=True)
        patch.append(f"\ndiff {label}\nmode {old_mode or 'absent'} -> {mode or 'absent'}\n")
        for line in difflib.unified_diff((old or b"").decode("utf-8", "replace").splitlines(True),
                     (new or b"").decode("utf-8", "replace").splitlines(True),
                     fromfile=f"base/{label}", tofile=f"snapshot/{label}"):
            patch.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    diff = "".join(patch)
    digest.update(diff.encode())
    (packet / "changes.diff").write_text(diff, encoding="utf-8")
    subject = {"snapshot_id": digest.hexdigest(), "base_ref": base_ref, "base_sha": base_sha,
               "merge_base": merge_base, "head_sha": head, "focus": focus,
               "changes": list(changes), "omissions": omissions,
               "scope": "merge-base to current working files, including non-ignored untracked files",
               "base_freshness": "local cached refs; no fetch performed"}
    write_json(packet / "subject.json", subject)
    return subject


def schema():
    def obj(properties):
        return {"type": "object", "properties": properties, "required": list(properties),
                "additionalProperties": False}
    string = {"type": "string", "minLength": 1}
    finding = obj({"id": string, "blocking": {"type": "boolean"},
                   "severity": {"type": "string", "enum": ["blocker", "major", "minor"]},
                   "file": {"type": ["string", "null"]},
                   "line": {"type": ["integer", "null"], "minimum": 1},
                   "title": string, "evidence": string, "recommended_fix": string})
    verification = obj({"dimension": {"type": "string", "enum": sorted(DIMENSIONS)},
                        "result": {"type": "string", "enum": ["pass", "fail", "unknown"]},
                        "evidence": string})
    return obj({"schema_version": {"type": "string", "enum": ["ca_peer_review.v1"]},
                "snapshot_id": string, "verdict": {"type": "string", "enum": ["approve", "request_changes", "blocked"]},
                "summary": string, "coverage": {"type": "string", "enum": ["full", "partial"]},
                "findings": {"type": "array", "items": finding, "maxItems": 50},
                "verification": {"type": "array", "items": verification, "minItems": 4},
                "limitations": {"type": "array", "items": string}})


def model_schema():
    # Providers accept different constraint subsets. Keep strict object structure on the wire;
    # enforce string/numeric/array bounds authoritatively in validate_shape after model completion.
    def strip(value):
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items()
                    if k not in {"minLength", "minimum", "minItems", "maxItems"}}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value
    return strip(schema())


def validate_shape(value, spec):
    types = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
    actual = ("null" if value is None else "boolean" if type(value) is bool else
              "integer" if type(value) is int else "string" if isinstance(value, str) else
              "object" if isinstance(value, dict) else "array" if isinstance(value, list) else "invalid")
    if actual not in types or ("enum" in spec and value not in spec["enum"]):
        raise ReviewError("invalid review field type/value")
    if actual == "object":
        if set(value) != set(spec["required"]):
            raise ReviewError("missing or unknown review fields")
        for key, child in spec["properties"].items():
            validate_shape(value[key], child)
    if actual == "array":
        if not spec.get("minItems", 0) <= len(value) <= spec.get("maxItems", 1000):
            raise ReviewError("invalid review array length")
        for item in value:
            validate_shape(item, spec["items"])
    if actual == "string" and (len(value.strip()) < spec.get("minLength", 0) or len(value) > 16000):
        raise ReviewError("empty or oversized review text")
    if actual == "integer" and value < spec.get("minimum", value):
        raise ReviewError("invalid review line")


def validate(data, subject):
    validate_shape(data, schema())
    if data["snapshot_id"] != subject["snapshot_id"]:
        raise ReviewError("review belongs to another snapshot")
    if {v["dimension"] for v in data["verification"]} != DIMENSIONS:
        raise ReviewError("review omitted a required dimension")
    ids = set()
    for item in data["findings"]:
        if not re.fullmatch(r"R[0-9]{3}", item["id"]) or item["id"] in ids:
            raise ReviewError("invalid or duplicate finding ID")
        ids.add(item["id"])
        if item["file"] is not None:
            safe_path(item["file"])
    blocking = any(f["blocking"] for f in data["findings"])
    if data["verdict"] == "approve" and (blocking or subject["omissions"] or data["coverage"] != "full"
                                         or any(v["result"] == "fail" for v in data["verification"])):
        raise ReviewError("contradictory approve")
    if data["verdict"] == "request_changes" and not blocking:
        raise ReviewError("request_changes without blocking findings")
    if subject["omissions"] and data["coverage"] != "partial":
        raise ReviewError("full coverage despite omitted changed files")


def launch_command(reviewer, packet, runtime):
    binary = shutil.which(os.environ.get(reviewer.upper() + "_BIN", reviewer))
    if not binary:
        raise ReviewError(f"{reviewer} CLI not found")
    env = {k: v for k, v in os.environ.items() if k in {
        "PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "TERM",
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
        "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR"}}
    if reviewer == "claude":
        required = ["--safe-mode", "--restricted", "--tools", "--allowedTools", "--strict-mcp-config",
                    "--disable-slash-commands", "--no-session-persistence", "--json-schema"]
        help_text = run([binary, "--help"]).decode()
        for flag in required:
            if flag not in help_text:
                raise ReviewError(f"unsupported Claude CLI: missing {flag}; update the CLI")
        command = [binary, "-p", "--safe-mode", "--restricted", "--tools", "Read,Grep,Glob",
                   "--allowedTools", "Read,Grep,Glob", "--permission-mode", "dontAsk",
                   "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                   "--disable-slash-commands", "--no-session-persistence", "--no-chrome",
                   "--output-format", "json", "--json-schema", json.dumps(model_schema())]
    else:
        help_text = run([binary, "exec", "--help"]).decode()
        for flag in ("--ignore-user-config", "--ignore-rules", "--ephemeral", "--disable",
                     "--sandbox", "--output-schema", "--output-last-message"):
            if flag not in help_text:
                raise ReviewError(f"unsupported Codex CLI: missing {flag}; update the CLI")
        features = run([binary, "features", "list"]).decode()
        for feature in FEATURES:
            if not re.search(r"^" + re.escape(feature) + r"\s", features, re.M):
                raise ReviewError(f"unsupported Codex CLI: missing isolation control {feature}")
        if packet.resolve() == runtime.resolve() or packet.resolve() in runtime.resolve().parents:
            raise ReviewError("authentication runtime must be outside the review packet")
        isolated = runtime / "codex-home"
        isolated.mkdir()
        auth = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
        if auth.is_file():
            (isolated / "auth.json").symlink_to(auth.resolve())
        env["CODEX_HOME"] = str(isolated)
        # Child-only home keeps ~/.agents/skills and global instruction discovery out.
        child_home = runtime / "home"
        child_home.mkdir()
        env["HOME"] = str(child_home)
        command = [binary, "exec", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
                   "--ephemeral", "--sandbox", "read-only", "-c", 'approval_policy="never"',
                   "-c", 'web_search="disabled"', "-c", 'project_doc_max_bytes=0',
                   "-c", 'shell_environment_policy.inherit="core"',
                   "-c", 'shell_environment_policy.ignore_default_excludes=false',
                   "--output-schema", str(packet / "schema.json"),
                   "--output-last-message", str(packet / "last-message.json")]
        for feature in FEATURES:
            command += ["--disable", feature]
        command.append("-")
    return command, env


def invoke(reviewer, packet, timeout):
    # Auth and CLI state are disposable siblings, never descendants of the review inputs.
    with tempfile.TemporaryDirectory(prefix="ca-peer-runtime-", dir=packet.parent) as directory:
        return invoke_process(reviewer, packet, timeout, Path(directory))


def invoke_process(reviewer, packet, timeout, runtime):
    command, env = launch_command(reviewer, packet, runtime)
    prompt = (packet / "reviewer.md").read_text() + "\nReview this packet at " + json.dumps(str(packet)) + ".\n"
    # Regular log files avoid unbounded RAM buffering. Only bounded tails are reported on failure.
    with (packet / "stdout.log").open("wb") as stdout, (packet / "stderr.log").open("wb") as stderr:
        child = subprocess.Popen(command, cwd=packet, env=env, stdin=subprocess.PIPE,
                                 stdout=stdout, stderr=stderr, start_new_session=True)
        def stop(signum=None, frame=None):
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if signum is not None:
                raise ReviewError("review interrupted")
        previous = {s: signal.signal(s, stop) for s in (signal.SIGINT, signal.SIGTERM)}
        try:
            child.communicate(prompt.encode(), timeout=timeout)
            if child.returncode:
                raise ReviewError(f"{reviewer} exited {child.returncode}; see stdout.log and stderr.log")
        except subprocess.TimeoutExpired:
            raise ReviewError(f"{reviewer} timed out after {timeout}s; review not performed")
        finally:
            stop()
            child.wait()
            for s, handler in previous.items():
                signal.signal(s, handler)
    raw = packet / ("stdout.log" if reviewer == "claude" else "last-message.json")
    if not raw.is_file() or raw.stat().st_size > LIMIT:
        raise ReviewError("missing or oversized model output")
    data = json.loads(raw.read_text())
    if reviewer == "claude":
        if not isinstance(data, dict) or data.get("is_error") or data.get("subtype") != "success":
            raise ReviewError("Claude did not complete a successful review")
        data = data.get("structured_output")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviewer", choices=["claude", "codex"], required=True)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base")
    parser.add_argument("--focus", default="")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if args.timeout < 1 or len(args.focus) > 16000:
        parser.error("timeout must be positive; focus must be at most 16000 characters")
    packet = Path(tempfile.mkdtemp(prefix="ca-peer-review-")).resolve()
    print(f"Artifacts: {packet}", flush=True)
    try:
        subject = prepare(args.repo.resolve(), packet, args.base, args.focus)
        references = Path(__file__).resolve().parent.parent / "references"
        for name in ("reviewer.md", "standards.md"):
            shutil.copyfile(references / name, packet / name)
        shutil.copytree(references / "standards", packet / "standards")
        write_json(packet / "schema.json", model_schema())
        print(f"Scope: {subject['base_ref']} ({subject['merge_base']}) -> working files; "
              f"{len(subject['changes'])} changed, {len(subject['omissions'])} omitted", flush=True)
        if args.prepare_only:
            write_json(packet / "status.json", {"status": "prepared", "reviewer": args.reviewer})
            return 0
        data = invoke(args.reviewer, packet, args.timeout)
        validate(data, subject)
        write_json(packet / "review.json", data)
        write_json(packet / "status.json", {"status": "completed", "reviewer": args.reviewer})
        print(f"Review: {data['verdict']} — {packet / 'review.json'}")
        return 0
    except (ReviewError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        write_json(packet / "status.json", {"status": "unavailable", "reason": str(error), "reviewer": args.reviewer})
        print(f"Review not performed: {error}\nArtifacts: {packet}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
