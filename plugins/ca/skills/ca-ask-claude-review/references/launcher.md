# Launcher and scope

Requires macOS or Linux, Python 3.9+, Git, and the requested authenticated model CLI. The reviewer does not need
the ca plugin or prior session state. The launcher checks required CLI controls before model launch;
unsupported versions fail with the missing option. It never bypasses sandbox/permission controls.

```text
peer-review.py --reviewer codex|claude --repo PATH
               [--base REF] [--focus TEXT] [--timeout SECONDS] [--prepare-only]
```

Default base resolution: cached `origin/HEAD`; otherwise a unique remote HEAD; otherwise an
unambiguous local/cached main or master. Ambiguity fails with `--base` guidance. No automatic fetch:
the report records the resolved base SHA. Fetch the intended remote beforehand if freshness matters.
The comparison starts at `merge-base(base, HEAD)`, so default-branch-only changes are not attributed
to the feature. Explicit `--base` is useful for repositories without remote HEAD or named main/master.

The current worktree is authoritative: staged and unstaged edits are combined, so an edit staged and
then undone locally cancels out. Non-ignored untracked files are included. Git's real index, refs,
worktrees, and checkout are never changed. Unmerged index entries fail with an actionable error.
The launcher reads files twice and checks HEAD/index state to reject changes during capture.

A unique system temporary directory contains `subject.json`, `changes.diff`, `snapshot/`, bundled
review standards, logs, `status.json`, and a validated `review.json` on success. Artifacts are private
to the user, retained for inspection, and never written into the repository. snapshot_id binds the
report to the captured contents, base, and focus. Any later edit requires a new review.

Ignored untracked files and `.ca`/worktree/session state are excluded. Secret-shaped paths use the
existing guard's patterns with template exceptions; this is a path filter, not a secret scanner.
Symlinks are recorded as link-target text and never followed. Binary/non-UTF-8 changes, submodules,
files over 1 MiB, and excluded changed paths are listed as omissions; their presence prevents approve.
An oversized or special file whose unchanged state cannot be established is conservatively omitted.
Raw file bytes are compared without Git clean filters, textconv, or repository program execution. The
capture has a 64 MiB total bound and fails rather than silently truncating a large repository.
Submodules are always reported as unsupported omissions, even if the gitlink appears unchanged:
their nested working files are not inspected, so repositories containing them cannot get approve.
Checkout transforms such as Git LFS, `eol`, `ident`, and `working-tree-encoding` are not normalized.
Their raw working bytes can appear as changes or omissions even when ordinary Git diff is clean.

Both launchers start fresh sessions. Codex uses an isolated CODEX_HOME, file-based auth when
available (or OPENAI_API_KEY), read-only sandbox, disabled ambient configuration/integrations,
and approvals=never. Claude uses safe/restricted mode, only Read/Grep/Glob tools, no MCP or skills,
and no session persistence. Claude supports default-location OAuth/CLI login, ANTHROPIC_API_KEY,
or CLAUDE_CODE_OAUTH_TOKEN; custom config directories, API gateways, Bedrock and Vertex environment
configuration are not forwarded by this launcher. Admin-managed policy still
applies. These controls isolate conversation and tool behavior; they are not a separate OS account.
No repository test execution is attempted by the reviewer. Tests are assessed from source.

The default timeout is 600 seconds, configurable with `--timeout`. Timeout or interruption kills
the child process group. Failed launch/output leaves status=unavailable and no review.json; failure
is never converted into a clean review or a same-model fallback. Model API network access must be
permitted by the invoking environment. `CODEX_BIN` and `CLAUDE_BIN` may select installed binaries.

CLI controls were checked against local help and the official
[Codex CLI reference](https://developers.openai.com/codex/cli/reference/) and
[Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference).
