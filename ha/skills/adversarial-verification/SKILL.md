---
name: adversarial-verification
description: Multi-pass adversarial verification harness that raises confidence in a change before it ships by trying to refute its claims with evidence. Use whenever you need high confidence that an implementation is correct, safe, and complete — before opening a PR, before approving a review, or whenever a change touches risky surface area such as auth, crypto, data migration, money, or external input.
allowed-tools: Read, Grep, Glob, Agent
---

<!-- ported from hv/skills/adversarial-verification/SKILL.md @ hv 0.1.0 (implementer -> general-purpose; nesting note reworded for ha) -->

# Adversarial verification

A single self-review tends to rationalize its own work. This harness instead
tries to **prove the change wrong** from several independent angles. A change is
trusted only after it survives refutation. This raises confidence; it does not
guarantee correctness — same-model verifiers make correlated mistakes, so their
agreement is weaker evidence than it looks. Independence comes from **execution
evidence** (tests, type checks, static analysis) and, where available, a different
model — not from adding more same-model voters or rounds.

Use this on a completed-but-unshipped change (your own, or a PR under review).
Because it dispatches `verifier` subagents, run it from a top-level skill context
(the `implement` / `review-pr` main loop), not from inside a subagent, so it can
fan out cleanly and keep the fan-out predictable.

## The loop

Run rounds until the change is clean or you hit the round cap (default **2** —
round 2 re-checks the fixes, since a fix can surface a second-order problem;
beyond that, diminishing returns usually mean the change needs rethinking, not
more rounds). **One round = one wave**: every subagent of a round, the
completeness critic included, goes out in a single `Agent` message. Serial waves
are the dominant latency cost of this harness — add parallel width, not rounds.

**Review mode** (called from `review-pr`): exactly one wave plus at most one
scoped follow-up wave for unsettled claims, and **no edits** — skip step 5;
failed claims become blocking findings.

### 1. Enumerate claims

List the specific claims the change makes. Typical claims:

- **Correctness** — it does what the spec asked, for all relevant inputs.
- **Safety** — it introduces no security regression (input→sink, authz, secrets).
- **Completeness** — every requirement and call site is handled; nothing stubbed.
- **Consistency** — it follows existing repo conventions; no contradiction.
- **No regression** — existing tests/behavior still hold.

Scale the claim set to the change. A one-line fix needs correctness + no-regression;
an auth change needs all five.

### 2. Dispatch verifiers + the completeness critic in one wave (refute-oriented)

For each claim, dispatch a `verifier` subagent with a distinct lens, **in
parallel** (one `Agent` message, multiple calls). Tell each one to *try to break
the claim* — but a REFUTED verdict must carry a **concrete counter-example or
failing evidence**; with no evidence either way the verdict is UNCERTAIN, never
UPHELD (the same rule the `verifier` agent itself states). A panel that rejects
without evidence manufactures false findings and fix-churn — evidence-gated
refutation is what keeps the panel's precision. For higher-stakes claims, put
**3 or more verifiers** on the same claim with different lenses (e.g. correctness,
edge-cases, security) and take a majority — an odd number breaks ties, and three
distinct lenses is the smallest panel that catches failure modes a single
reviewer is blind to. Scale **lens diversity** with the stakes, not raw headcount.

Why parallel + distinct lenses: diverse lenses buy **coverage** — failure modes a
single lens is blind to. They do not buy statistically independent votes (same-model
errors correlate), so a majority of verifiers agreeing is not proof; a concrete
counter-example or failing command is. Ask each verifier to ground its verdict in
something it ran.

### 3. Judge

- A claim **fails** if a verifier returns REFUTED with a concrete counter-example,
  or if a majority of its verifiers refute it.
- `UNCERTAIN` with a real gap is treated as a fail for risky claims (auth, data
  loss, money, external input) — do not ship on "probably fine" there.

### 4. Completeness critic (same wave as step 2)

Dispatched alongside the per-claim verifiers — not after them — one more subagent
asks the inverse question:
**"What did everyone miss?"** — a requirement not turned into a claim, a modality
not tested, a call site not checked, an error path ignored. Its findings become
new claims for the next round (or, in review mode, the scoped follow-up wave).

### 5. Fix and repeat

For every failed claim, apply the minimal fix the verifier specified (yourself,
or via a general-purpose subagent for a file-disjoint slice), then start a new
round. Re-verify — a fix can introduce a new break.

Stop when a full round produces no REFUTED/UNCERTAIN verdicts and the
completeness critic finds nothing new, or when you reach the round cap.

## Output

Report the final verdict with evidence, not assertions:

```
## Verification result: PASS | PASS-WITH-NOTES | FAIL

## Claims checked
- [PASS|FAIL] <claim> (<n> verifiers, <lens(es)>) — <one-line evidence>

## Fixes applied this run
- path:line — <what changed and which claim it closed>

## Residual risk (if PASS-WITH-NOTES)
- <what remains unverified and why it's acceptable to ship>
```

If you hit the round cap with unresolved high-risk failures, the honest result
is **FAIL** — say so and surface the blocker. Shipping an unverified risky change
is the failure mode this skill exists to prevent.

## Cost control

Verification fan-out multiplies tokens. Match rigor to risk: low-risk, isolated
changes get a single correctness + no-regression pass; reserve the ≥3-verifier,
multi-lens treatment for changes the `analyzer` flagged HIGH risk.
