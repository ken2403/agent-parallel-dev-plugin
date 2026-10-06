---
name: apply-feedback
description: Address review feedback on a PR and push the fixes back, rigorously — feedback is evaluated against the code, not obeyed blindly. Use after /ha:review-pr requests changes, or to act on human review comments on a PR. Invoke explicitly with /ha:apply-feedback. Requires the superpowers plugin.
argument-hint: '[pr-number] [feedback text]'
disable-model-invocation: true
effort: high
allowed-tools: Read, Edit, Write, Bash, Grep, Glob, Agent
---

# Apply feedback

## Input
$ARGUMENTS

You turn review feedback into committed fixes on the PR's branch — correctly,
without thrashing. All edits happen in an **isolated worktree**, never the user's
main checkout. Requires the `superpowers` plugin.

## Step 1 — Gather the feedback

```bash
PR="<pr-number from the arguments, or empty to auto-detect>"
[ -n "$PR" ] || PR="$(gh pr view --json number --jq .number 2>/dev/null)"  # no number given -> current branch's PR
[ -n "$PR" ] || { echo "no PR number given and none found for the current branch" >&2; exit 1; }
gh pr view "$PR" --json headRefName,reviews,comments
gh pr diff "$PR"
```

Combine PR review comments, the latest `/ha:review-pr` output, and any feedback
passed in the input. Note the PR's `headRefName` — that is the branch you fix.

## Step 2 — Apply the receiving-code-review discipline

**REQUIRED SUB-SKILL:** Use `superpowers:receiving-code-review`. Feedback is data
to evaluate, not orders to obey. For each item: restate it, **verify it against
the code before acting**, and implement one item at a time, testing each. Reply to
inline comments in their thread, not as a top-level PR comment.

**No performative agreement (prohibition).** This is a discipline, not a style note:

- Do **not** write "You're absolutely right!", "Great point!", "Great catch!",
  "Thanks!" or any praise/gratitude. State the fix, or push back.
- Do **not** implement an item you have not verified against the code.

| Rationalization | Reality |
|---|---|
| "The reviewer is senior, just do it." | A wrong suggestion implemented is still a bug; verify, then implement or push back with reasoning. |
| "Agreeing is faster / friendlier." | Performative agreement adds no information and hides whether you actually checked. |
| "It's a small change, no need to verify." | Small unverified changes are exactly how regressions slip in. |

If an item is wrong, unclear, or conflicts with a prior decision: **push back with
technical reasoning** (cite the code/tests), or ask — do not implement something
incorrect. Drop items that are mistaken or already handled, and say why.

## Step 3 — Locate or create the isolated worktree (do NOT touch the main checkout)

```bash
CLAUDE_SKILL_HA_DIR="${CLAUDE_SKILL_DIR}"   # ha-recognizable alias of the built-in skill dir
BRANCH="$(gh pr view "$PR" --json headRefName --jq .headRefName)"
eval "$(bash "$CLAUDE_SKILL_HA_DIR/scripts/attach-or-create-worktree.sh" "$BRANCH")"
[ -n "${WORKTREE_PATH:-}" ] || { echo "error: worktree was not resolved — aborting"; exit 1; }
echo "Working in: $WORKTREE_PATH on $BRANCH (reused=$REUSED)"
```

`attach-or-create-worktree.sh` **reuses** an existing isolated worktree on the
branch, **creates** one under `.claude/worktrees/ha/<slug>` if none exists, and
**refuses** (non-zero) if the branch is checked out in the main checkout. If it
exits non-zero, stop and report; do not fall back to editing in place.

**Absolute-path rule (MUST):** the main session cwd is NOT this worktree, and a
`cd` does not persist between Bash calls. So: every `Edit`/`Write` targets an
absolute path under `$WORKTREE_PATH`; every test/build runs as
`cd "$WORKTREE_PATH" && <cmd>` in one Bash call; every git op uses
`git -C "$WORKTREE_PATH"`; every subagent is given the absolute `$WORKTREE_PATH`.

## Step 4 — Apply the fixes (in `$WORKTREE_PATH`)

- **1–2 files**: fix them yourself, editing absolute paths under `$WORKTREE_PATH`. A
  fix that changes behavior follows strict TDD (`superpowers:test-driven-development`):
  failing test first, then the fix — same discipline the parallel path gets.
- **3+ independent files**: partition into file-disjoint slices and dispatch one
  **general-purpose** subagent per slice in parallel (one `Agent` message), each
  told to follow strict TDD (`superpowers:test-driven-development`) and the
  `code-review` standards, and given the absolute `$WORKTREE_PATH` and its exact
  file set. Keep slices disjoint so parallel edits don't collide
  (`superpowers:dispatching-parallel-agents`). Integrate the seams yourself.

The `code-review` standards auto-activate — a fix must not introduce a new problem.

## Step 5 — Re-verify

Run the relevant tests/lint/build as `cd "$WORKTREE_PATH" && <cmd>` and capture
output. For anything that was a correctness or security comment, re-review the
fixed area with a `verifier` subagent, scoped to the **uncommitted** fixes
(`diff HEAD` + `status --short` — Step 6 has not committed them yet) — the whole point of feedback is that the
first pass missed something, so confirm the fix actually closes it.

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
