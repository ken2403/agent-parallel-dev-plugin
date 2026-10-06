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
more rounds). **One round = one wave** in a single `Agent` message — serial waves
are this harness's dominant latency cost, so add width, not rounds.

**Review mode** (`review-pr`): the caller fixes its own panel (the critic only on HIGH),
runs one wave plus at most one scoped follow-up wave, and makes **no edits** — skip step 4;
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

### 2. Dispatch one wave: per-claim verifiers + the completeness critic

In **one `Agent` message**, dispatch a `verifier` per claim, each with a distinct lens
and told to *try to break the claim*. For higher-stakes claims use **3 distinct lenses**
(e.g. correctness, edge-cases, security) — lens diversity buys coverage, not independent
votes. In the same message, dispatch the **completeness critic**: a `verifier` whose
claim is "this claim and lens list is complete" (pass it the list) — REFUTED means a
requirement not turned into a claim, an untested modality, an unchecked call site, or an
ignored error path, with evidence.

### 3. Judge

- A report with no `## Verdict` line (truncated or partial) = **UNCERTAIN**.
- A claim **fails** on a REFUTED that carries a concrete counter-example or failing
  evidence; a bare REFUTED is UNCERTAIN.
- UNCERTAIN with a real gap is a fail for risky claims (auth, data loss, money,
  external input) — do not ship on "probably fine" there.
- Critic findings become new claims for the next round.

### 4. Fix and repeat

For every failed claim, apply the minimal fix (yourself, or a general-purpose
subagent for a file-disjoint slice), then start a new round — a fix can introduce a
new break. Stop when a round has no REFUTED/UNCERTAIN and the critic adds nothing, or
at the round cap.

**At the cap, nothing is re-verified**: a fix applied in the last round, or a critic
claim it raised, is unverified. Report it as residual risk (PASS-WITH-NOTES at best);
on a risky claim the result is **FAIL**.

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
multi-lens treatment for changes graded HIGH risk (by the `analyzer`, or by
`review-pr`'s own grade).
