---
name: ha-adversarial-verification
description: Refute load-bearing claims about a design, implementation, conflict resolution, or PR using bounded independent read-only Codex subagents and evidence-gated adjudication. Use for risk-scaled HA design red-teams, assembled-change pre-PR gates, independent PR reviews, and any risky correctness, security, completeness, or no-regression claim.
license: MIT
---

# HA adversarial verification

Try to prove the change wrong. Resolve this skill directory from the loaded `SKILL.md` path. Read `references/verifier-protocol.md` and `references/agent-control.md` before dispatch.

## 1. Enumerate claims

State specific falsifiable claims for correctness, safety, completeness, compatibility, and no regression. Scale them to the change; do not review generic qualities without a concrete claim.

## 2. Choose bounded rigor

- `LOW`: one verifier, one round.
- `MEDIUM`: two distinct lenses plus completeness check; second round only after a fix.
- `HIGH`: three distinct lenses plus completeness critic; maximum two rounds, and a caller's lower limit wins.

**One round is one parallel wave.** Start every verifier of the round together, the completeness critic included: it needs the claim and lens list, not the others' results. Wall-clock is set by serial waves, not by how many reviewers run side by side. Every prompt includes exact root/PR, claim, lens, evidence format, no edits, no comments, no nested agents, and no human questions. The caller's main agent remains the only adjudicator.

## 3. Judge evidence

- `REFUTED` requires a concrete counterexample, failing check, or path/line proof.
- `UPHELD` requires positive evidence, not absence of findings.
- `UNCERTAIN` means the evidence could not settle the claim. Treat it as failure on risky surfaces.
- A missing, truncated, or malformed verdict is `UNCERTAIN`; a `REFUTED` without evidence is `UNCERTAIN`, never a failure by itself.
- Verifiers running the same model make correlated mistakes: they add coverage, not independent votes. Majority voting never overrides concrete evidence; prefer deterministic evidence (a failing test, a command, a grep hit) and investigate disagreement in the main agent.

The completeness critic (in the same wave) asks what requirement, call site, ordering, error path, or test the claim list missed. Its evidence-backed findings become claims for the next round.

## 4. Fix and repeat

Verify findings before editing. Use one bounded writer at a time for minimal fixes, then re-run affected checks and claims. Stop on a clean round or the round cap. At the cap nothing is re-verified: a fix or critic claim from the last round is unverified residual risk — `PASS-WITH-NOTES` at best, and `FAIL` on a risky claim, never provisional approval.

Report claims, verdicts, reviewer lenses, exact evidence/check output, fixes, and residual risk.
