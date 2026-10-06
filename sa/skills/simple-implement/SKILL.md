---
name: simple-implement
description: Takes a plan — a file path or plain-text task — and drives ONE simple feature to an open PR fast, with a human approval gate before any code is written. Use for a small-to-medium, well-scoped implementation task where you want to move quickly and review separately afterward. Stops at PR; run /sa:review-pr to review. Invoke explicitly with /sa:simple-implement.
argument-hint: '<plan-file-path | natural-language task>'
model: sonnet
disable-model-invocation: true
effort: medium
allowed-tools: Read, Edit, Write, Bash, Grep, Glob, Agent, AskUserQuestion
---

# Simple implement

## Assignment
$ARGUMENTS

You own one simple feature from plan to an open PR. Move fast, but **write nothing
until the human approves the plan**, and never claim a step passed without evidence.
You stop once the PR is open — reviewing is a separate `/sa:review-pr` step.

## Context (auto-injected)
- Repo root: !`git rev-parse --show-toplevel 2>/dev/null`
- Current branch: !`git branch --show-current 2>/dev/null`
- Base branch: !`bash "${CLAUDE_SKILL_DIR}/scripts/detect-base-branch.sh" 2>/dev/null`
- Conventions: !`test -f CLAUDE.md && echo "CLAUDE.md present — read it before editing" || echo "no CLAUDE.md"`

## Phase 1 — Digest the plan

If `$ARGUMENTS` is a path to an existing file, `Read` it; otherwise treat the text as
the plan. Restate, in 2-3 sentences, the **goal**, the **scope**, and the **success
criteria** in your own words. This is read-only — you are still in the main checkout.

## Phase 2 — Inspect the codebase (read-only)

Dispatch the built-in **`Explore`** subagent (one, or several in parallel if scope is
uncertain) to map: which files change, the existing patterns/conventions to follow, which
tests cover the area, and what could break. Resolve small ambiguities from the code and
**record each assumption in one line** (it carries into the PR body's Notes).

## Phase 3 — Clarify gate (ask only when it matters)

If a **material** ambiguity remains that you genuinely cannot settle from the code — a
requirement readable two ways, an unmeasurable success criterion, an unstated error/edge
behavior with real consequences — ask the human with **`AskUserQuestion`** (concrete
options, not open-ended). Skip this phase entirely when there is nothing material to ask.

## Phase 4 — Present the plan and get approval (HARD GATE)

Present the concrete plan:
- branch name (`feat/<slug>` or `fix/<slug>`),
- the files to create/modify,
- the approach and the test strategy,
- any assumptions.

Then call **`AskUserQuestion`** with options **"Approve & implement"** / **"Adjust"**.
**Nothing is written and no worktree is created until "Approve".** On "Adjust", revise
and re-ask. Do not proceed to Phase 5 without approval.

## Phase 5 — Create the isolated worktree (only after approval)

```bash
CLAUDE_SKILL_SA_DIR="${CLAUDE_SKILL_DIR}"   # sa-recognizable alias of the built-in skill dir
eval "$(bash "$CLAUDE_SKILL_SA_DIR/scripts/new-worktree.sh" "feat/<slug>")"
echo "Implementing in: $WORKTREE_PATH on $BRANCH"
```

`new-worktree.sh` prints `WORKTREE_PATH=...` and `BRANCH=...`; `eval` makes both shell
variables available. The worktree is created under `.claude/worktrees/sa/<slug>`.

**Absolute-path rule for the rest of the run (MUST — this is the one easy way to corrupt
the user's working copy):** the main session's cwd stays in the main checkout, NOT this
worktree. So from here on:
- Every `Edit`/`Write` **MUST** target an absolute path under `$WORKTREE_PATH`. Never edit
  a relative path and never edit a path outside `$WORKTREE_PATH` — that would change the
  user's real working copy, and the secret-guard hook will not catch it.
- Every test/build runs as `cd "$WORKTREE_PATH" && <cmd>` in a single Bash call.
- Every git op uses `git -C "$WORKTREE_PATH" ...`.
- Each `implementer` subagent you dispatch is given `$WORKTREE_PATH` and the same rule.

## Phase 6 — Implement (red-green, fast)

Plan the smallest change that fully satisfies the success criteria, then build:
- **Single file / tightly coupled**: implement it yourself, editing absolute paths under
  `$WORKTREE_PATH`.
- **3+ independent files**: partition into **file-disjoint slices** and dispatch one
  **`implementer`** subagent per slice in parallel (one `Agent` message, multiple calls).
  **Pass each implementer the absolute `$WORKTREE_PATH`** and its exact file set; then
  integrate the seams yourself.

**Red-green is mandatory for every behavior change** (yours and each implementer's):
(1) write the failing test first, (2) run it and capture the failure — it must fail for
the *expected* reason, (3) write the minimal implementation, (4) run it green and capture
the pass. A test that never failed proves nothing. Skip only when there is no testable
behavior (docs, comments, pure config) — and say so in the PR Notes.

| Rationalization | Reality |
|---|---|
| "Too simple to test" | Simple changes break too — and the reviewer blocks untested behavior changes anyway. |
| "I'll add the test after" | A test written after proves what the code *does*, not what it *should* do. |
| "The suite will catch it" | The suite proves the tree is green, not that *your* change is covered. |

The `code-review` standards skill auto-activates — follow it (quality, test rigor,
security, consistency). Update any docs the repo keeps in step with the code (within scope).

## Phase 7 — Verify the build (objective gate, evidence required)

```bash
cd "$WORKTREE_PATH" && {
  if [ -f Makefile ] && grep -qE '^(check|test|ci):' Makefile; then make check 2>&1 || make test 2>&1
  elif [ -f package.json ]; then npm test 2>&1 || npm run test 2>&1
  elif [ -f pyproject.toml ]; then { command -v uv >/dev/null && uv run pytest 2>&1; } || pytest 2>&1
  elif [ -f go.mod ]; then go test ./... 2>&1
  elif [ -f Cargo.toml ]; then cargo test 2>&1
  else echo "No standard check found — verify the touched paths manually"; fi
}
```

Fix failures. If still red after a fix attempt, open the PR as a **draft** documenting it.

## Phase 8 — Pre-PR cross-check (risk-scaled, cheap)

The build gate proves the tree is green; this is an **independent** look before the PR —
an error the author makes is often one the author cannot see, and catching it here is far
cheaper than a review round-trip.

**Commit first** — uncommitted and untracked work is invisible both to
`git diff <base>...HEAD` and to the verifier, so an uncommitted change would sail through
this gate unreviewed:

```bash
git -C "$WORKTREE_PATH" add -A
git -C "$WORKTREE_PATH" commit -m "$(cat <<'EOF'
<type>(<scope>): <concise summary>

<what changed and why>

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
BASE="$(bash "${CLAUDE_SKILL_DIR}/scripts/detect-base-branch.sh" "$WORKTREE_PATH")"
git -C "$WORKTREE_PATH" diff --stat "origin/$BASE...HEAD"   # this diff is what gets graded
```

Grade that diff with this inline heuristic, in order — no extra agent, seconds:

1. **RISKY** — touches a risky surface (canonical list in the `code-review` skill — the
   single source of truth), including config that changes permissions, secrets, auth,
   or CI. **RISKY overrides size.**
2. **TRIVIAL** — not RISKY, and docs/comments only, or ≤~25 changed code lines covered
   by a test (existing or added in this diff). `review-pr` uses the same definition.
3. **NORMAL** — everything else.

Then dispatch `verifier` subagent(s), passing each the absolute `$WORKTREE_PATH`, the
base branch, **and the plan's success criteria from Phase 1** (the claim references them),
and tell them the build gate already ran green on this commit — targeted checks only, no
full-suite re-run:

- **TRIVIAL** → skip; the build gate is the gate.
- **NORMAL** → **one** verifier, claim: *"the diff satisfies the stated success criteria
  for all relevant inputs, and no call site or consumer outside the diff was missed."*
- **RISKY** → **two** verifiers in parallel, blind to each other, distinct lenses:
  (1) correctness — construct a concrete counter-example; (2) security — trace external
  input to its sinks, check authz.

A report with no `## Verdict` line (truncated or partial) counts as **UNCERTAIN**.
On **REFUTED**: fix, re-run the Phase 7 build gate (a fix after the gate is otherwise
never built), commit the fix, then re-verify only the refuted claim with one verifier. **Hard cap: one fix round.** If a claim stays REFUTED — or UNCERTAIN on a RISKY
diff — open the PR as a **draft** with the finding in Notes. Never loop.

## Phase 9 — Open the PR (end of this skill)

The work is already committed (Phase 8). Push and open the PR:

```bash
git -C "$WORKTREE_PATH" push -u origin "$BRANCH"
BASE="$(bash "${CLAUDE_SKILL_DIR}/scripts/detect-base-branch.sh" "$WORKTREE_PATH")"
# add --draft if checks are red
gh pr create --base "$BASE" --head "$BRANCH" --title "<type>: <summary>" --body "$(cat <<'EOF'
## Summary
<what this PR does, in one or two sentences>

## Changes
- <change>

## Verification
- <command run> → <result>

## Notes
<assumptions; risk: <TRIVIAL|NORMAL|RISKY> — pre-PR check: <skipped|verdicts>; if draft: the blocker and what the human should decide>
EOF
)"
```

Always create the PR (a draft on failure beats a silent half-run). **This is the end of
`simple-implement` — do not review here.** Print a one-line hand-off telling the user to run
`/sa:review-pr <pr>` when they want the (separate, on-demand) review guardrail.

## Final report

```
result: <feature> — PR <url> (<ready|draft>) implemented in <worktree>; build: PASS|FAIL; pre-PR check (risk <grade>): <skipped|UPHELD|draft: reason>. Next: /sa:review-pr <pr> to review.
```
