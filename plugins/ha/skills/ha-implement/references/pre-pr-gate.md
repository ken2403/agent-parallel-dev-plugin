# Risk-scaled assembled-change gate

Per-task review cannot prove cross-task integration. This gate challenges the final branch before PR creation without duplicating the later independent PR review.

## Scale

- LOW: one reviewer for correctness plus no regression; one round.
- MEDIUM: correctness/integration and test/compatibility reviewers plus completeness, in one wave; second round only after fixes.
- HIGH: correctness, security/abuse, and migration/ordering reviewers plus completeness, in one wave; maximum two rounds in `$ha-implement`.

One round is one parallel wave; the completeness critic gets the claim list and shares the wave. At the cap, a last-round fix is unverified: on a risky claim the gate fails and the PR opens as a draft.

The canonical risky-surface list lives in `$ha-code-review`. Blast radius, irreversible effects, and broad refactors can raise the grade.

## Evidence gate

Review the base-to-head diff, any uncommitted fixes (`diff HEAD` plus `status --short`), and relevant unchanged call sites. Refutation requires a counterexample or check. `UNCERTAIN` is a blocker only when uncertainty concerns a risky surface or required behavior. Verify every finding before fixing it.

## Separation from final review

This is an author-side shipping gate. It may fix the branch and ensures a PR is not obviously broken. `$ha-review-pr` is a separate, SHA-bound, preferably fresh-thread review after the PR exists and remains mandatory before `$ha-merge-pr`.
