---
name: implement
description: Implement an approved ha plan for ONE feature, thoroughly, and open a PR. Use after /ha:plan (or with a plan file or task) when you want the feature built with ha's full rigor rather than a quick hand-off — for a non-trivial change you intend to review and merge. Stops at PR; run /ha:review-pr for the independent review. Invoke explicitly with /ha:implement. Requires the superpowers plugin.
argument-hint: '<plan-file-path | natural-language task>'
disable-model-invocation: true
effort: high
allowed-tools: Read, Edit, Write, Bash, Grep, Glob, Agent, WebFetch
---

# Implement

## Assignment
$ARGUMENTS

You drive ONE feature from an approved plan to an open PR, built thoroughly. This
skill **leverages superpowers rather than re-implementing it**: the per-task
implement→review→fix loop is delegated to `superpowers:subagent-driven-development`;
the build gate to `superpowers:verification-before-completion`; red-path debugging
to `superpowers:systematic-debugging`. ha's own delta is worktree isolation, a
**risk-scaled adversarial gate before the PR**, and the always-open-PR finish. You
stop at the PR — the independent review is the separate `/ha:review-pr` step.
Requires the `superpowers` plugin.

## Context (auto-injected)
- Repo root: !`git rev-parse --show-toplevel 2>/dev/null`
- Current branch: !`git branch --show-current 2>/dev/null`
- Base branch: !`bash "${CLAUDE_SKILL_DIR}/scripts/detect-base-branch.sh" 2>/dev/null`
- Conventions: !`test -f CLAUDE.md && echo "CLAUDE.md present — read it before editing" || echo "no CLAUDE.md"`

## Phase 1 — Digest the plan + restate

If `$ARGUMENTS` is a path, `Read` it; otherwise treat it as the task. Restate the
goal, scope, success criteria, and the plan's **risk grade** (LOW/MEDIUM/HIGH — it
scales Phase 4). **Discrepancy gate:** if the plan contradicts what the code
actually is, stop and ask the human — do not silently "fix" the plan. Read-only
(main checkout).

## Phase 2 — Isolate in a worktree

```bash
CLAUDE_SKILL_HA_DIR="${CLAUDE_SKILL_DIR}"   # ha-recognizable alias of the built-in skill dir
eval "$(bash "$CLAUDE_SKILL_HA_DIR/scripts/new-worktree.sh" "feat/<slug>")"
[ -n "${WORKTREE_PATH:-}" ] || { echo "error: worktree was not resolved — aborting"; exit 1; }
echo "Implementing in: $WORKTREE_PATH on $BRANCH (reused=$REUSED)"
# Keep ha's and SDD's scratch out of the PR via the repo-local (uncommitted) exclude.
# This is the shared info/exclude, so add each pattern at most once (no dup growth):
EXCLUDE="$(git -C "$WORKTREE_PATH" rev-parse --git-path info/exclude)"
case "$EXCLUDE" in /*) ;; *) EXCLUDE="$WORKTREE_PATH/$EXCLUDE";; esac
mkdir -p "$(dirname "$EXCLUDE")"
for p in '.ha/' '.superpowers/'; do grep -qxF "$p" "$EXCLUDE" 2>/dev/null || printf '%s\n' "$p" >> "$EXCLUDE"; done
```

`new-worktree.sh` runs a `superpowers:using-git-worktrees` Step 0 check (reuses an
existing linked worktree instead of nesting) and otherwise creates
`.claude/worktrees/ha/<slug>`. ha uses script-created **persistent** worktrees here,
not native `EnterWorktree` — see `references/pre-pr-gate.md` for why.

**Absolute-path rule (MUST — the one easy way to corrupt the user's working copy):**
the main session cwd stays in the main checkout, NOT this worktree. So: every
`Edit`/`Write` targets an absolute path under `$WORKTREE_PATH`; every test/build runs
as `cd "$WORKTREE_PATH" && <cmd>` in one Bash call; every git op uses
`git -C "$WORKTREE_PATH"`; every subagent is given `$WORKTREE_PATH` and the same rule.

## Phase 3 — Per-task loop (delegate to SDD)

**REQUIRED SUB-SKILL:** Use `superpowers:subagent-driven-development` to execute the
plan task-by-task — its fresh-implementer-per-task dispatch, its two-verdict task
review, its fix loop, its `task-brief`/`review-package` scripts, its progress
ledger, and **its own risk-scaled model selection** (do not override the model
choices it makes). All work happens inside `$WORKTREE_PATH`.

**Use SDD's own workspace defaults — do NOT redirect its paths.** SDD writes its
ledger, task briefs, and review packages under `.superpowers/sdd/`; leave them
there (Phase 2 already excludes that dir from the PR). Redirecting only some of its
paths would split its workspace and make resume-after-compaction read the wrong
ledger and re-run finished tasks — the single most expensive SDD failure. The one
thing ha overrides is **scope**: stop SDD after its per-task loop completes. Do
**NOT** let it run its final whole-branch review or invoke
`superpowers:finishing-a-development-branch`; ha owns the finish (Phases 4–6). State
this to SDD explicitly when you invoke it — it is a self-contained skill whose own
text ends by invoking finishing-a-development-branch, so the stop instruction must
be loud.

If the plan is a bare task with no task breakdown, first produce a short task list
(or run `/ha:plan`) — SDD needs tasks to iterate over. An inline task list written
here skips `/ha:plan`'s front-loaded test rigor, so it must carry that rigor itself:
give every task explicit RED→GREEN test steps (the failing assertion, then the
implementation). **REQUIRED SUB-SKILL:** Use `superpowers:test-driven-development`
for tasks generated this way.

## Phase 4 — Pre-PR adversarial gate (risk-scaled — NOT a second full review)

SDD's per-task reviews are constructive and per-task. This gate adds the one thing
they lack — **refutation of the assembled whole** — and nothing more. It is
deliberately **lighter** than `/ha:review-pr` (the independent post-PR review), so
the same bytes are not reviewed twice by the same method. Operate on the full diff
`git -C "$WORKTREE_PATH" diff <base>...HEAD`, scaled to the plan's risk grade:

- **LOW / isolated** → the build gate (Phase 5) plus ONE `verifier` refuting the
  single claim "this change is correct and introduces no regression." No multi-round
  loop, no panel.
- **MEDIUM** → `adversarial-verification` on the central claims, **one** round; a
  second round only if round 1 applied fixes (a fix can introduce a new break).
- **HIGH** (analyzer-flagged: a risky surface per the `code-review` canonical list,
  or a broad refactor) → the full `adversarial-verification` treatment — ≥3 `verifier`s
  with distinct lenses + the completeness critic, **in one wave** — at most 2 rounds
  (its own cap; no outer loop around it).

Every wave goes out as a single `Agent` message, and the verifiers are told the build
gate owns the full test suite — they run targeted checks only.

**Evaluate findings before applying them** — a verifier verdict is data, not
orders (the same `superpowers:receiving-code-review` discipline `apply-feedback`
uses): verify each finding against the code first, and drop one that is factually
wrong, recording why. Then apply every **verified** Critical/Important finding (a
fix spanning 3+ file-disjoint, dependency-free spots may fan out to parallel
general-purpose subagents — `superpowers:dispatching-parallel-agents`; never two
on the same file). **Match
rigor to risk** — do not run the heavy multi-round panel on a trivial change; that
is `adversarial-verification`'s own rule and the analyzer already graded this work.
Detail and the (b)-vs-(c) rationale in `references/pre-pr-gate.md`.

## Phase 5 — Verify the build (objective gate, evidence required)

**REQUIRED SUB-SKILL:** Use `superpowers:verification-before-completion` — identify
the command that proves the build, run it FRESH, read the exit code and output
before any "passing" claim.

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

**Confirm the plan's test rigor actually landed:** every edge-case/failure-mode test
the plan required exists and passes; if the plan set a coverage expectation and the
toolchain supports it (`pytest --cov`, `go test -cover`, `jest --coverage`), run it
and confirm the touched code is covered. A feature is not done if its planned tests
were never written. On red: **REQUIRED SUB-SKILL:** Use
`superpowers:systematic-debugging` to find the root cause before patching. If still
red after a real fix attempt, open the PR as a **draft** documenting the blocker.

## Phase 6 — Open the PR (end of this skill)

```bash
git -C "$WORKTREE_PATH" add -A   # .ha/ and .superpowers/ are excluded (Phase 2)
git -C "$WORKTREE_PATH" commit -m "$(cat <<'EOF'
<type>(<scope>): <concise summary>

<what changed and why>

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
git -C "$WORKTREE_PATH" push -u origin "$BRANCH"
# add --draft if checks are red or a high-risk claim is unresolved
gh pr create --head "$BRANCH" --title "<type>: <summary>" --body "$(cat <<'EOF'
## Summary
<what this PR does>

## Changes
- <change>

## Verification
- <command run> → <result>
- Pre-PR gate (risk <LOW|MED|HIGH>): <verifier verdicts / PASS>

## Notes
<assumptions; if draft: the blocker and what the human should decide>
EOF
)"
```

Always create the PR (a draft beats a silent half-run). **This is the end of
`implement` — the independent review is `/ha:review-pr`.**

## Final report

```
result: <feature> — PR <url> (<ready|draft>); SDD tasks complete, pre-PR gate (risk <grade>) <PASS|notes>; build PASS|FAIL. Next: /ha:review-pr <pr>.
```
