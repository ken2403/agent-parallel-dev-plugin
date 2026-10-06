---
name: apply-feedback
description: Addresses review feedback on a PR and updates it, parallelizing across files with implementer subagents when feedback spans 3+ independent files. Use after /sa:review-pr requests changes, or to act on human review comments and push fixes back.
argument-hint: '[pr-number] [feedback text]'
model: sonnet
disable-model-invocation: true
effort: medium
allowed-tools: Read, Edit, Write, Bash, Grep, Glob, Agent
---

# Apply feedback

## Input
$ARGUMENTS

You turn review feedback into committed fixes on the PR's branch — correctly, without
thrashing. Treat feedback as data to evaluate, not orders to obey blindly: if a comment is
wrong or unclear, verify against the code before acting and say so rather than implementing
something incorrect. All edits happen in an **isolated worktree**, never the user's main
checkout.

## Step 1 — Gather the feedback

```bash
PR="<pr-number from the arguments, or empty to auto-detect>"
[ -n "$PR" ] || PR="$(gh pr view --json number --jq .number 2>/dev/null)"  # no number given -> current branch's PR
[ -n "$PR" ] || { echo "no PR number given and none found for the current branch" >&2; exit 1; }
gh pr view "$PR" --json headRefName,reviews,comments
gh pr diff "$PR"
```

Combine PR review comments, the latest `/sa:review-pr` output, and any feedback passed in
the input. Note the PR's `headRefName` — that is the branch you will fix.

## Step 2 — Locate or create the isolated worktree (do NOT touch the main checkout)

`simple-implement` usually already left an isolated worktree for this branch under
`.claude/worktrees/sa/<slug>`. Reuse it if present; otherwise cut a fresh one. Either way
you work in a worktree, **never** `gh pr checkout` into the user's main working copy.

```bash
CLAUDE_SKILL_SA_DIR="${CLAUDE_SKILL_DIR}"   # sa-recognizable alias of the built-in skill dir
BRANCH="$(gh pr view "$PR" --json headRefName --jq .headRefName)"
eval "$(bash "$CLAUDE_SKILL_SA_DIR/scripts/attach-or-create-worktree.sh" "$BRANCH")"
echo "Working in: $WORKTREE_PATH on $BRANCH (reused=$REUSED)"
```

`attach-or-create-worktree.sh` prints `WORKTREE_PATH=...`, `BRANCH=...`, and `REUSED=0|1`;
`eval` makes them shell variables. It **reuses** an existing isolated worktree on the
branch, **creates** one under `.claude/worktrees/sa/<slug>` if none exists, and **refuses**
(non-zero) if the branch is checked out in the main checkout — so this skill can never edit
the user's working copy. If it exits non-zero, stop and report; do not fall back to editing
in place.

**Absolute-path rule for the rest of the run (MUST):** the main session's cwd is NOT this
worktree, and a `cd` does not persist between Bash calls. So from here on:
- Every `Edit`/`Write` **MUST** target an absolute path under `$WORKTREE_PATH`. Never edit a
  relative path or a path outside `$WORKTREE_PATH` — that would change the user's real
  working copy, and the secret-guard hook will not catch it.
- Every test/build runs as `cd "$WORKTREE_PATH" && <cmd>` in a single Bash call.
- Every git op uses `git -C "$WORKTREE_PATH" ...`.
- Each `implementer` subagent you dispatch is given the absolute `$WORKTREE_PATH`.

## Step 3 — Triage into a fix list

Group feedback into discrete fixes. For each: the file(s), what's wrong, and the intended
change. Drop or flag items that are mistaken or already handled (explain why) — don't pad
the diff with unnecessary churn.

## Step 4 — Apply fixes (in `$WORKTREE_PATH`)

- **1–2 files**: fix them yourself, editing absolute paths under `$WORKTREE_PATH`.
- **3+ independent files**: partition into file-disjoint slices and dispatch one
  `implementer` subagent per slice in parallel (one `Agent` message), **passing each the
  absolute `$WORKTREE_PATH`**, then integrate. Keep slices disjoint so parallel edits don't
  collide.

The `code-review` standards auto-activate — a fix must not introduce a new problem.

## Step 5 — Re-verify

Run the relevant tests/lint/build as `cd "$WORKTREE_PATH" && <cmd>` and capture output. A
fix that changes behavior follows the same red-green rule as the original build: failing
test first, then the fix. For anything that was a correctness or security comment,
re-review the fixed area with a `verifier` subagent, scoped to the **uncommitted** fixes
(`diff HEAD` + `status --short` — Step 6 has not committed them yet) — the whole point of feedback is that
the first pass missed something, so confirm the fix actually closes it. If the fix
addressed a **security-blocking** finding and the verifier returns REFUTED or UNCERTAIN,
escalate once to a `deep-verifier` subagent on that claim before pushing.

## Step 6 — Update the PR

```bash
git -C "$WORKTREE_PATH" add -A
git -C "$WORKTREE_PATH" commit -m "fix: address review feedback on PR #$PR"
git -C "$WORKTREE_PATH" push
gh pr comment "$PR" --body "Addressed review feedback:
- <fix> (<file>)
Verification: <result>"
```

result: PR #<n> updated — <k> fixes applied in <worktree> (reused|created), checks <green/red>.
