---
name: resolve-conflicts
description: Resolves merge conflicts between a feature branch and its base branch, in an isolated worktree, parallelizing across files with implementer subagents when 3+ files conflict. Use when a PR or feature branch is behind/conflicting with the base branch and you want the conflicts resolved, verified, and pushed.
argument-hint: '[pr-number | branch name]'
model: opus
disable-model-invocation: true
effort: high
allowed-tools: Read, Edit, Write, Bash, Grep, Glob, Agent
---

# Resolve conflicts

## Input
$ARGUMENTS

You merge the base branch into a feature branch and resolve the conflicts **correctly** —
preserving the intent of *both* sides, never blindly picking one. All work happens in an
**isolated worktree**, never the user's main checkout. A wrong conflict resolution silently
corrupts code, so verify before you push.

## Step 1 — Resolve the target branch and its isolated worktree

`$ARGUMENTS` is a PR number or a branch name (default: the current branch). Get the branch,
then reuse its existing sa worktree or create one — **never** edit the main checkout.

```bash
ARG="<pr-number | branch | empty>"
CLAUDE_SKILL_SA_DIR="${CLAUDE_SKILL_DIR}"   # sa-recognizable alias of the built-in skill dir
if [ -z "$ARG" ]; then ARG="$(gh pr view --json number --jq .number 2>/dev/null)"; fi   # no arg -> current branch's PR
if printf '%s' "$ARG" | grep -qE '^[0-9]+$'; then
  PR="$ARG"; BRANCH="$(gh pr view "$PR" --json headRefName --jq .headRefName)"
else
  PR=""; BRANCH="${ARG:-$(git branch --show-current)}"
fi
eval "$(bash "$CLAUDE_SKILL_SA_DIR/scripts/attach-or-create-worktree.sh" "$BRANCH")"
echo "Resolving in: $WORKTREE_PATH on $BRANCH (reused=$REUSED)"
```

If `attach-or-create-worktree.sh` exits non-zero (e.g. the branch is checked out in the main
checkout), **stop and report**; do not fall back to resolving in place.

**Absolute-path rule for the rest of the run (MUST):** the main session's cwd is NOT this
worktree, and a `cd` does not persist between Bash calls. So from here on:
- Every `Edit`/`Write` **MUST** target an absolute path under `$WORKTREE_PATH`.
- Every git/test/build runs as `git -C "$WORKTREE_PATH" ...` or `cd "$WORKTREE_PATH" && <cmd>`
  in a single Bash call.
- Each `implementer` subagent you dispatch is given the absolute `$WORKTREE_PATH`.

## Step 2 — Merge the base branch and surface the conflicts

```bash
BASE="$(bash "$CLAUDE_SKILL_SA_DIR/scripts/detect-base-branch.sh" "$WORKTREE_PATH")"
echo "Base branch: $BASE"
git -C "$WORKTREE_PATH" fetch origin "$BASE" --quiet
git -C "$WORKTREE_PATH" merge --no-edit "origin/$BASE" || true   # conflicts are expected
echo "=== Conflicting files ==="
git -C "$WORKTREE_PATH" diff --name-only --diff-filter=U
```

If there are **no** conflicting files, the merge already succeeded — skip to Step 5 (verify)
and finish. If the working tree was dirty and the merge refused to start, stop and report;
do not force it.

## Step 3 — Plan each resolution (intent first, before editing)

For every conflicted file, read the conflict hunks (`<<<<<<<` / `=======` / `>>>>>>>`) and
the surrounding code, and decide the resolution **strategy** per hunk — keep ours, keep
theirs, or **combine** both (the common correct answer: both sides changed for a reason).
Record one line per file: the strategy and why. Resolving a conflict by deleting one side's
real change is a silent regression — don't.

## Step 4 — Resolve (in `$WORKTREE_PATH`)

- **1–2 conflicted files**: resolve them yourself, editing absolute paths under
  `$WORKTREE_PATH`, then `git -C "$WORKTREE_PATH" add <file>`.
- **3+ conflicted files**: partition into file-disjoint slices and dispatch one
  `implementer` subagent per slice in parallel (one `Agent` message), **passing each the
  absolute `$WORKTREE_PATH`**, the exact files it owns, and the per-hunk strategy. Each
  resolves and `git add`s only its own files; then you integrate.

The `code-review` standards auto-activate — a resolution must not introduce a new bug or
drop either side's intent.

## Step 5 — Integration review (CRITICAL) + verify the build

Conflict resolution is exactly where parallel edits silently break each other, and a wrong
resolution is silent corruption — so this check uses the opus-tier `deep-verifier`, not the
cheap fan-out `verifier`. Before committing, dispatch a `deep-verifier` subagent, scoped to the staged merge
(`diff HEAD` + `status --short` — nothing is committed yet), against
the claim *"the merge is
resolved correctly — no hunk lost either side's intent, no conflict markers remain, the
seams between independently-resolved files are coherent"*. Also confirm no markers survive
and run the build:

```bash
# No leftover conflict markers anywhere in the resolved tree
git -C "$WORKTREE_PATH" diff --check
grep -rnE '^(<<<<<<<|=======|>>>>>>>)' "$WORKTREE_PATH" --exclude-dir=.git && echo "MARKERS REMAIN — fix before commit" || echo "no markers"

cd "$WORKTREE_PATH" && {
  if [ -f Makefile ] && grep -qE '^(check|test|ci):' Makefile; then make check 2>&1 || make test 2>&1
  elif [ -f package.json ]; then npm test 2>&1 || npm run test 2>&1
  elif [ -f pyproject.toml ]; then { command -v uv >/dev/null && uv run pytest 2>&1; } || pytest 2>&1
  elif [ -f go.mod ]; then go test ./... 2>&1
  elif [ -f Cargo.toml ]; then cargo test 2>&1
  else echo "No standard check found — verify the touched paths manually"; fi
}
```

If the verifier refutes the resolution or checks fail, fix and re-review before committing.

## Step 6 — Commit the merge and push

Only once markers are gone, the verifier upholds the resolution, and checks pass:

```bash
git -C "$WORKTREE_PATH" add -A
git -C "$WORKTREE_PATH" commit --no-edit   # completes the merge commit
git -C "$WORKTREE_PATH" push
```

If a PR was resolved (given or auto-detected), leave a short note:

```bash
[ -n "$PR" ] && gh pr comment "$PR" --body "Merged origin/$BASE and resolved conflicts:
- <file> — <strategy>
Verification: <result>"
```

## If it goes wrong

Abort cleanly without touching anything else — the worktree isolates the damage:

```bash
git -C "$WORKTREE_PATH" merge --abort
```

Then report what conflicted and why you stopped.

## Final report

```
result: PR/branch <id> — merged origin/<base>, resolved <k> conflicted files in <worktree>; verifier <UPHELD|REFUTED>, checks <green/red>, <pushed|stopped>.
```
