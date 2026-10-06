# Pre-PR adversarial gate (ha's `implement` Phase 4)

This is the delta `implement` adds **on top of** `superpowers:subagent-driven-development`.
SDD already runs a constructive, two-verdict review per task. This gate adds the one
thing per-task review structurally cannot give — **refutation of the assembled
whole** — and is otherwise kept as small as the risk allows.

## Why this is NOT a second `/ha:review-pr`

There are two whole-change reviews in the ha lifecycle, and they are different on
purpose:

- **(b) this gate** — runs inside `implement`, before the PR exists. It is the
  *author's* last self-check. Because it is the author reviewing their own work, it
  is the **less independent** of the two, so it is deliberately **light and
  risk-scaled**: just enough refutation to open the PR in good faith.
- **(c) `/ha:review-pr`** — runs after the PR, as a genuinely independent second
  opinion that assumes nothing the PR claims. This is where the **full-strength**
  adversarial review lives.

(c) carries the weight; (b) is scaled to the plan's risk grade and only adds the
refutation angle. Never make (b) a clone of (c) — two identical heavy reviews of one
diff are recomputation, not thoroughness.

## Risk-scaled rounds

The plan's `analyzer` risk grade governs how much (b) does:

| Risk | Gate |
|---|---|
| LOW / isolated | build gate + ONE `verifier` on "correct + no regression"; a refuted claim is fixed and re-checked once. No panel. |
| MEDIUM | `adversarial-verification`, 1 round; a 2nd round only if round 1 applied fixes or the critic added claims. |
| HIGH | full `adversarial-verification` (≥3 verifiers, distinct lenses, completeness critic — all in one wave), at most 2 rounds. |

**One loop, cap 2, critic in-wave** — never wrap `adversarial-verification` in an outer
loop. Serial waves, not verifier count, dominate wall-clock; add width, not rounds. A
fix applied in the last round is unverified residual risk; any FAIL makes the PR a draft.

## The discipline: match rigor to risk (not "always two rounds")

The failure this gate guards against is **both** directions:

- **Do not skip the gate.** SDD's per-task reviews are blind to the integrated
  whole; opening the PR with zero whole-change refutation defeats the gate.
- **Do not over-run it.** Forcing the ≥3-verifier multi-round panel on a one-line,
  LOW-risk change is ceremony, not rigor — and it contradicts
  `adversarial-verification`'s own "match rigor to risk" rule and wastes the
  analyzer grade ha computed. Scale the verifier *count* and *rounds* to risk.

| Rationalization | Reality |
|---|---|
| "SDD already reviewed every task." | Per-task review can't see cross-task interactions or the assembled whole; that's exactly what (b) checks. |
| "It's HIGH risk, so I'll just eyeball it." | HIGH risk is the case the multi-lens panel exists for; don't downgrade it. |
| "It's a one-liner, I'll run the full panel to be safe." | Wrong direction — one verifier + build is the right gate for LOW risk; the full review is `/ha:review-pr` later. |

## SDD scope contract

When you invoke `superpowers:subagent-driven-development` in Phase 3, it normally
ends by running a final whole-branch review and then
`superpowers:finishing-a-development-branch`. **ha takes over instead:** stop SDD
after its per-task loop, then run this gate and ha's own PR finish (Phases 4–6). Say
this to SDD loudly when invoking it — its own text ends by invoking
finishing-a-development-branch, so a quiet instruction can lose to SDD's bolded
terminal step.

**Use SDD's own workspace paths.** SDD keeps its ledger/briefs/review-packages under
`.superpowers/sdd/`. Do not redirect them — redirecting only some splits the
workspace and breaks resume-after-compaction (SDD reads its default ledger path and
re-runs finished tasks). `implement` Phase 2 instead adds `.ha/` and `.superpowers/`
to the worktree's local `info/exclude`, so the scratch never reaches the PR while
SDD's internals stay untouched.

## Why script-created persistent worktrees (not native EnterWorktree)

`superpowers:using-git-worktrees` prefers a native worktree tool (e.g.
`EnterWorktree`) when one exists. ha deliberately does **not** use it here:

- ha's worktree must **outlive the session** — it persists until the PR is reviewed
  and merged, possibly across sessions. Native worktrees are harness-managed and
  auto-cleaned on session exit, which would destroy in-flight, unmerged work.
- `/ha:clean-worktrees` reclaims worktrees by their predictable path
  `.claude/worktrees/ha/<slug>` and verifies the PR merged first. Native worktrees
  live elsewhere, outside that contract.

`new-worktree.sh` still honors `using-git-worktrees` **Step 0** (reuse an existing
linked worktree instead of nesting) — the part that applies regardless of mechanism.
