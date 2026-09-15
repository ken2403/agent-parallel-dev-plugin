"""Hermetic behavior tests: real Git, temporary repositories, fake model processes."""
import importlib.util
import contextlib
import errno
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

CA = Path(__file__).resolve().parents[2]
SCRIPT = CA / "claude/skills/ask-codex-review/scripts/peer-review.py"
spec = importlib.util.spec_from_file_location("peer_review", SCRIPT)
peer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(peer)


class PeerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ca-peer-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.write("app.py", "value = 1\n")
        self.write("removed.txt", "remove me\n")
        self.write(".gitignore", "ignored/\n")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        self.git("switch", "-qc", "feature")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], stderr=subprocess.DEVNULL)

    def write(self, name, text):
        dest = self.repo / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)

    def prepare(self, base=None):
        packet = Path(tempfile.mkdtemp(dir=self.root))
        return packet, peer.prepare(self.repo, packet, base, "")

    def review(self, subject):
        return {"schema_version": "ca_peer_review.v1", "snapshot_id": subject["snapshot_id"],
                "verdict": "approve", "summary": "No defects found in inspected code.",
                "coverage": "full", "findings": [], "limitations": ["Tests not executed."],
                "verification": [{"dimension": d, "result": "pass", "evidence": "Inspected code."}
                                 for d in sorted(peer.DIMENSIONS)]}

    def test_committed_staged_unstaged_untracked_deleted_and_ignored(self):
        self.write("commit.txt", "committed\n")
        self.git("add", ".")
        self.git("commit", "-qm", "feature")
        self.write("app.py", "value = 2\n")
        self.git("add", "app.py")
        self.write("app.py", "value = 3\n")
        self.write("new file.txt", "untracked\n")
        self.write("ignored/data", "not included\n")
        (self.repo / "removed.txt").unlink()
        before = self.git("status", "--porcelain=v1", "-z")
        index = (self.repo / ".git/index").read_bytes()
        packet, subject = self.prepare()
        self.assertEqual(set(subject["changes"]), {"app.py", "commit.txt", "new file.txt", "removed.txt"})
        self.assertEqual((packet / "snapshot/app.py").read_text(), "value = 3\n")
        self.assertFalse((packet / "snapshot/ignored/data").exists())
        self.assertIn("-value = 1", (packet / "changes.diff").read_text())
        self.assertEqual(before, self.git("status", "--porcelain=v1", "-z"))
        self.assertEqual(index, (self.repo / ".git/index").read_bytes())

    def test_default_branch_divergence_is_not_a_feature_change(self):
        self.write("feature.txt", "feature\n")
        self.git("add", ".")
        self.git("commit", "-qm", "feature")
        self.git("switch", "-q", "main")
        self.write("upstream.txt", "only on default branch\n")
        self.git("add", ".")
        self.git("commit", "-qm", "upstream")
        self.git("switch", "-q", "feature")
        _, subject = self.prepare()
        self.assertEqual(subject["changes"], ["feature.txt"])
        self.assertNotEqual(subject["merge_base"], subject["base_sha"])

    def test_nonstandard_remote_default_and_explicit_base(self):
        self.git("update-ref", "refs/remotes/origin/trunk", "main")
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
        self.write("app.py", "new\n")
        self.assertEqual(self.prepare()[1]["base_ref"], "refs/remotes/origin/trunk")
        self.assertEqual(self.prepare("main")[1]["base_ref"], "main")

    def test_ambiguous_default_requires_base(self):
        self.git("branch", "master", "main")
        self.write("app.py", "new\n")
        with self.assertRaisesRegex(peer.ReviewError, "--base"):
            self.prepare()
        self.prepare("main")

    def test_multiple_remote_defaults_require_explicit_base(self):
        for remote in ("alpha", "beta"):
            self.git("update-ref", f"refs/remotes/{remote}/main", "main")
            self.git("symbolic-ref", f"refs/remotes/{remote}/HEAD", f"refs/remotes/{remote}/main")
        self.write("app.py", "new\n")
        with self.assertRaisesRegex(peer.ReviewError, "multiple remote defaults"):
            self.prepare()
        self.assertEqual(self.prepare("main")[1]["base_ref"], "main")

    def test_no_changes_and_staged_then_undone(self):
        with self.assertRaisesRegex(peer.ReviewError, "no net changes"):
            self.prepare()
        self.write("app.py", "staged\n")
        self.git("add", "app.py")
        self.write("app.py", "value = 1\n")
        with self.assertRaisesRegex(peer.ReviewError, "no net changes"):
            self.prepare()

    def test_assume_unchanged_does_not_hide_local_edits(self):
        self.git("update-index", "--assume-unchanged", "app.py")
        self.write("app.py", "hidden from git diff\n")
        self.assertIn("app.py", self.prepare()[1]["changes"])

    def test_git_clean_filter_is_never_executed(self):
        self.git("config", "filter.probe.clean", "touch FILTER_EXECUTED; cat")
        self.write(".gitattributes", "app.py filter=probe\n")
        self.write("app.py", "new\n")
        self.assertIn("app.py", self.prepare()[1]["changes"])
        self.assertFalse((self.repo / "FILTER_EXECUTED").exists())

    def test_raw_comparison_does_not_normalize_checkout_attributes(self):
        self.write(".gitattributes", "app.py text eol=crlf\n")
        self.git("add", ".gitattributes")
        self.git("commit", "-qm", "attributes")
        self.git("branch", "-f", "main", "HEAD")
        (self.repo / "app.py").write_bytes(b"value = 1\r\n")
        packet, subject = self.prepare()
        self.assertEqual(subject["changes"], ["app.py"])
        self.assertEqual((packet / "snapshot/app.py").read_bytes(), b"value = 1\r\n")

    def test_unchanged_submodule_is_explicitly_unsupported(self):
        oid = self.git("rev-parse", "HEAD").decode().strip()
        self.git("update-index", "--add", "--cacheinfo", f"160000,{oid},vendor")
        self.git("commit", "-qm", "gitlink")
        self.git("branch", "-f", "main", "HEAD")
        (self.repo / "vendor").mkdir()
        _, subject = self.prepare()
        self.assertFalse(subject["changes"])
        self.assertEqual(subject["omissions"], [{"file": "vendor", "reason": "submodule"}])
        with self.assertRaisesRegex(peer.ReviewError, "contradictory approve"):
            peer.validate(self.review(subject), subject)

    def test_non_utf8_changes_are_explicitly_omitted(self):
        (self.repo / "latin.txt").write_bytes(b"word=\xff\n")
        self.git("add", ".")
        self.git("commit", "-qm", "latin baseline")
        self.git("branch", "-f", "main", "HEAD")
        (self.repo / "latin.txt").write_bytes(b"word=\xfe\n")
        _, subject = self.prepare()
        self.assertEqual(subject["omissions"], [{"file": "latin.txt", "reason": "non-UTF-8 file"}])
        self.assertFalse(subject["changes"])

    def test_unchanged_large_file_does_not_block_review(self):
        self.write("big.txt", "x" * (peer.LIMIT + 1))
        self.git("add", ".")
        self.git("commit", "-qm", "large baseline")
        self.git("branch", "-f", "main", "HEAD")
        self.write("app.py", "new\n")
        packet, subject = self.prepare()
        self.assertEqual(subject["changes"], ["app.py"])
        self.assertFalse(subject["omissions"])
        self.assertFalse((packet / "snapshot/big.txt").exists())
        peer.validate(self.review(subject), subject)

    def test_total_capture_limit_fails_instead_of_truncating(self):
        self.write("app.py", "new\n")
        with patch.object(peer, "TOTAL_LIMIT", 20), self.assertRaisesRegex(peer.ReviewError, "capture exceeds"):
            self.prepare()

    def test_eloop_parent_is_an_explicit_omission(self):
        original = peer.os.open
        def open_with_eloop(path, *args, **kwargs):
            if path == "folder":
                raise OSError(errno.ELOOP, "symlink loop")
            return original(path, *args, **kwargs)
        with patch.object(peer.os, "open", open_with_eloop):
            self.assertIn("ancestor", peer.current_file(self.repo, "folder/file")[2])

    def test_protected_assume_unchanged_edit_is_an_omission(self):
        self.write(".env", "old-secret\n")
        self.git("add", ".")
        self.git("commit", "-qm", "protected baseline")
        self.git("branch", "-f", "main", "HEAD")
        self.git("update-index", "--assume-unchanged", ".env")
        self.write(".env", "new-secret\n")
        self.write("app.py", "new\n")
        packet, subject = self.prepare()
        self.assertEqual(subject["omissions"], [{"file": ".env", "reason": "protected path"}])
        self.assertFalse((packet / "snapshot/.env").exists())

    def test_unmerged_index_is_rejected(self):
        self.write("app.py", "feature\n")
        self.git("add", ".")
        self.git("commit", "-qm", "feature")
        self.git("switch", "-q", "main")
        self.write("app.py", "default\n")
        self.git("add", ".")
        self.git("commit", "-qm", "default")
        self.git("switch", "-q", "feature")
        with self.assertRaises(subprocess.CalledProcessError):
            self.git("merge", "main")
        with self.assertRaisesRegex(peer.ReviewError, "unmerged"):
            self.prepare()

    def test_rename_mode_and_hostile_filename(self):
        self.git("mv", "removed.txt", "renamed.txt")
        name = "odd\n`touch PWNED` $(touch PWNED).txt"
        self.write(name, "inert\n")
        (self.repo / "app.py").chmod(0o755)
        packet, subject = self.prepare()
        self.assertIn(name, subject["changes"])
        self.assertIn("100644 -> 100755", (packet / "changes.diff").read_text())
        self.assertFalse((self.repo / "PWNED").exists())
        self.assertIn("removed.txt", subject["changes"])
        self.assertIn("renamed.txt", subject["changes"])

    def test_sensitive_binary_large_and_symlink_are_omissions(self):
        self.write(".env", "DO_NOT_EXPORT=real\n")
        self.write(".env.example", "KEY=example\n")
        self.write(".ca/reviews/old.json", "old findings\n")
        self.write("big.txt", "x" * (peer.LIMIT + 1))
        (self.repo / "binary").write_bytes(b"a\0b")
        outside = self.root / "outside"
        outside.write_text("DO_NOT_READ")
        (self.repo / "link").symlink_to(outside)
        packet, subject = self.prepare()
        omitted = {o["file"] for o in subject["omissions"]}
        self.assertEqual(omitted, {".env", ".ca/reviews/old.json", "big.txt", "binary", "link"})
        self.assertFalse((packet / "snapshot/.env").exists())
        self.assertFalse((packet / "snapshot/.ca").exists())
        self.assertFalse((packet / "snapshot/link").is_symlink())
        self.assertNotIn("DO_NOT_READ", (packet / "snapshot/link").read_text())
        self.assertIn(".env.example", subject["changes"])

    def test_directory_symlink_is_never_followed(self):
        self.write("folder/file", "original")
        self.git("add", ".")
        self.git("commit", "-qm", "folder")
        (self.repo / "folder/file").unlink()
        (self.repo / "folder").rmdir()
        external = self.root / "outside"
        external.mkdir()
        (external / "file").write_text("DO_NOT_READ")
        (self.repo / "folder").symlink_to(external, target_is_directory=True)
        packet, subject = self.prepare()
        self.assertFalse((packet / "snapshot/folder/file").exists())
        self.assertTrue(subject["omissions"])

    def test_changed_capture_is_rejected(self):
        self.write("app.py", "new\n")
        original = peer.capture
        calls = []
        def moving(*args):
            result = original(*args)
            calls.append(1)
            if len(calls) == 1:
                self.write("app.py", "moved\n")
            return result
        with patch.object(peer, "capture", moving), self.assertRaisesRegex(peer.ReviewError, "changed during"):
            self.prepare()

    def test_head_movement_during_capture_is_rejected(self):
        self.write("app.py", "new\n")
        original = peer.current_file
        moved = []
        def moving(*args, **kwargs):
            result = original(*args, **kwargs)
            if not moved:
                self.git("commit", "--allow-empty", "-qm", "moved")
                moved.append(True)
            return result
        with patch.object(peer, "current_file", moving), self.assertRaisesRegex(peer.ReviewError, "HEAD changed"):
            self.prepare()

    def test_snapshot_binding_changes_after_edit(self):
        self.write("app.py", "first\n")
        first_packet, first = self.prepare()
        self.write("app.py", "second\n")
        _, second = self.prepare()
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertEqual((first_packet / "snapshot/app.py").read_text(), "first\n")
        with self.assertRaisesRegex(peer.ReviewError, "another snapshot"):
            peer.validate(self.review(first), second)

    def test_validation_fail_closed(self):
        self.write("app.py", "new\n")
        _, subject = self.prepare()
        good = self.review(subject)
        peer.validate(good, subject)
        bads = []
        bads.append(dict(good, coverage="partial"))
        bads.append(dict(good, verdict="request_changes"))
        bads.append(dict(good, verification=good["verification"][:3]))
        bads.append(dict(good, verification=[dict(v, result="fail") for v in good["verification"]]))
        bads.append(dict(good, extra="unknown"))
        bads.append(dict(good, findings=[{"id": "R001", "blocking": "true"}]))
        for bad in bads:
            with self.subTest(bad=bad), self.assertRaises(peer.ReviewError):
                peer.validate(bad, subject)
        subject["omissions"] = [{"file": "big", "reason": "large"}]
        with self.assertRaises(peer.ReviewError):
            peer.validate(good, subject)

    def test_valid_shape_cannot_bypass_finding_safety_checks(self):
        self.write("app.py", "new\n")
        _, subject = self.prepare()
        finding = {"id": "R001", "blocking": False, "severity": "minor", "file": "app.py",
                   "line": 1, "title": "Concrete issue", "evidence": "Source evidence",
                   "recommended_fix": "Specific fix"}
        good = dict(self.review(subject), findings=[finding])
        peer.validate(good, subject)
        for entries in ([dict(finding, id="X1")], [finding, finding],
                        [dict(finding, file="../etc/passwd")], [dict(finding, file="/abs")],
                        [dict(finding, blocking=True)]):
            with self.subTest(entries=entries), self.assertRaises(peer.ReviewError):
                peer.validate(dict(good, findings=entries), subject)

    def fake_model(self, mode):
        fake = self.root / "model"
        fake.write_text("#!/usr/bin/env python3\n" + "MODE = " + repr(mode) + "\n" + '''
import json, os, pathlib, subprocess, sys, time
args = sys.argv[1:]
if '--help' in args:
    print('--safe-mode --restricted --tools --allowedTools --strict-mcp-config --disable-slash-commands --no-session-persistence --json-schema --ignore-user-config --ignore-rules --ephemeral --disable --sandbox --output-schema --output-last-message')
    sys.exit(0)
if args == ['features', 'list']:
    print("\\n".join(f + ' stable false' for f in %s))
    sys.exit(0)
prompt = sys.stdin.read()
assert 'standards.md' in prompt
assert '--resume' not in args and '--continue' not in args
if MODE == 'fail':
    print('model failed')
    sys.exit(3)
if MODE == 'oversized':
    print('x' * (1024 * 1024 + 1))
    sys.exit(0)
if MODE == 'missing_output':
    sys.exit(0)
if MODE == 'timeout':
    child = subprocess.Popen([sys.executable, '-c', 'import time, pathlib; time.sleep(2); pathlib.Path("escaped").write_text("alive")'])
    pathlib.Path('child.pid').write_text(str(child.pid))
    time.sleep(30)
subject = json.loads(pathlib.Path('subject.json').read_text())
result = {'schema_version': 'ca_peer_review.v1', 'snapshot_id': subject['snapshot_id'],
          'verdict': 'approve', 'summary': 'No defects found.', 'coverage': 'full',
          'findings': [], 'limitations': ['Tests not run.'],
          'verification': [{'dimension': d, 'result': 'pass', 'evidence': 'Inspected source.'}
                           for d in ['quality', 'test_rigor', 'security', 'consistency']]}
if MODE == 'malformed':
    result.pop('verification')
if '--safe-mode' in args:
    assert args[args.index('--tools') + 1] == 'Read,Grep,Glob'
    assert args[args.index('--permission-mode') + 1] == 'dontAsk'
    envelope = {'subtype': 'success', 'is_error': False, 'structured_output': result}
    if MODE == 'envelope_error':
        envelope['is_error'] = True
    if MODE == 'bad_subtype':
        envelope['subtype'] = 'error'
    if MODE == 'no_structure':
        envelope.pop('structured_output')
    print(json.dumps(envelope))
else:
    assert args[args.index('--sandbox') + 1] == 'read-only'
    assert 'approval_policy="never"' in args
    assert pathlib.Path(os.environ['CODEX_HOME']).name == 'codex-home'
    pathlib.Path(args[args.index('--output-last-message') + 1]).write_text(json.dumps(result))
''' % repr(peer.FEATURES))
        fake.chmod(0o755)
        return str(fake)

    def test_both_model_adapters_and_zero_context(self):
        self.write("app.py", "new\n")
        for reviewer in ("claude", "codex"):
            packet, subject = self.prepare()
            (packet / "reviewer.md").write_text("Read standards.md. Fresh review.")
            peer.write_json(packet / "schema.json", peer.schema())
            with patch.dict(os.environ, {reviewer.upper() + "_BIN": self.fake_model("ok")}):
                result = peer.invoke(reviewer, packet, 10)
            peer.validate(result, subject)

    def main_run(self, reviewer="claude", mode="ok", prepare=False):
        self.write("app.py", "new\n")
        packet = Path(tempfile.mkdtemp(dir=self.root))
        argv = [str(SCRIPT), "--repo", str(self.repo), "--reviewer", reviewer]
        if prepare:
            argv.append("--prepare-only")
        with patch.object(sys, "argv", argv), patch.object(peer.tempfile, "mkdtemp", return_value=str(packet)), \
                patch.dict(os.environ, {reviewer.upper() + "_BIN": self.fake_model(mode)}), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = peer.main()
        return rc, packet

    def test_main_assembles_standards_and_prepare_only_state(self):
        rc, packet = self.main_run(prepare=True)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads((packet / "status.json").read_text())["status"], "prepared")
        refs = SCRIPT.parent.parent / "references"
        for source in [refs / "reviewer.md", refs / "standards.md", *sorted((refs / "standards").glob("*.md"))]:
            self.assertEqual((packet / source.relative_to(refs)).read_bytes(), source.read_bytes())
        self.assertEqual(json.loads((packet / "schema.json").read_text()), peer.model_schema())
        self.assertFalse((packet / "review.json").exists())
        self.assertFalse((packet / "stdout.log").exists())

    def test_main_publishes_only_valid_completed_reviews(self):
        for reviewer in ("claude", "codex"):
            rc, packet = self.main_run(reviewer=reviewer)
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads((packet / "status.json").read_text())["status"], "completed")
            peer.validate(json.loads((packet / "review.json").read_text()),
                          json.loads((packet / "subject.json").read_text()))

    def test_main_failure_and_invalid_output_have_unavailable_status(self):
        for mode in ("fail", "malformed"):
            rc, packet = self.main_run(mode=mode)
            self.assertEqual(rc, 1)
            self.assertEqual(json.loads((packet / "status.json").read_text())["status"], "unavailable")
            self.assertFalse((packet / "review.json").exists())

    def test_model_envelopes_and_missing_or_oversized_results_fail_closed(self):
        for mode in ("envelope_error", "bad_subtype", "no_structure", "oversized"):
            rc, packet = self.main_run(mode=mode)
            self.assertEqual(rc, 1, mode)
            self.assertEqual(json.loads((packet / "status.json").read_text())["status"], "unavailable")
            self.assertFalse((packet / "review.json").exists())
        rc, packet = self.main_run(reviewer="codex", mode="missing_output")
        self.assertEqual(rc, 1)
        self.assertFalse((packet / "review.json").exists())

    def test_missing_cli_and_unsupported_isolation_fail_before_launch(self):
        packet = Path(tempfile.mkdtemp(dir=self.root))
        with patch.dict(os.environ, {"CLAUDE_BIN": str(self.root / "absent")}):
            with self.assertRaisesRegex(peer.ReviewError, "not found"):
                peer.launch_command("claude", packet)
        fake = self.fake_model("ok")
        original = peer.run
        def missing_feature(argv, **kwargs):
            if argv[1:] == ["features", "list"]:
                return b""
            return original(argv, **kwargs)
        with patch.dict(os.environ, {"CODEX_BIN": fake}), patch.object(peer, "run", missing_feature):
            with self.assertRaisesRegex(peer.ReviewError, "missing isolation control"):
                peer.launch_command("codex", packet)
        with patch.dict(os.environ, {"CLAUDE_BIN": fake}), patch.object(peer, "run", return_value=b""):
            with self.assertRaisesRegex(peer.ReviewError, "unsupported Claude CLI"):
                peer.launch_command("claude", packet)

    def test_failure_and_malformed_output_are_not_reviews(self):
        self.write("app.py", "new\n")
        for mode in ("fail", "malformed"):
            packet, subject = self.prepare()
            (packet / "reviewer.md").write_text("Read standards.md.")
            with patch.dict(os.environ, {"CLAUDE_BIN": self.fake_model(mode)}), self.assertRaises(peer.ReviewError):
                result = peer.invoke("claude", packet, 10)
                peer.validate(result, subject)
            self.assertFalse((packet / "review.json").exists())

    def test_timeout_kills_descendants(self):
        self.write("app.py", "new\n")
        packet, _ = self.prepare()
        (packet / "reviewer.md").write_text("Read standards.md.")
        with patch.dict(os.environ, {"CLAUDE_BIN": self.fake_model("timeout")}), self.assertRaisesRegex(peer.ReviewError, "timed out"):
            peer.invoke("claude", packet, 1)
        self.assertTrue((packet / "child.pid").exists())
        time.sleep(2)
        self.assertFalse((packet / "escaped").exists(), "descendant survived the timeout")

    def test_sigterm_marks_unavailable_and_kills_child_group(self):
        self.write("app.py", "new\n")
        env = dict(os.environ, CLAUDE_BIN=self.fake_model("timeout"), TMPDIR=str(self.root))
        process = subprocess.Popen([sys.executable, str(SCRIPT), "--reviewer", "claude",
                                    "--repo", str(self.repo)], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        packet = Path(process.stdout.readline().strip().removeprefix("Artifacts: "))
        deadline = time.monotonic() + 10
        while not (packet / "child.pid").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue((packet / "child.pid").exists())
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=10)
        self.assertEqual(process.returncode, 1)
        self.assertEqual(json.loads((packet / "status.json").read_text())["status"], "unavailable")
        self.assertFalse((packet / "review.json").exists())
        time.sleep(2)
        self.assertFalse((packet / "escaped").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
