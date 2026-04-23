# TPM Competency Model V1

This document defines the stable, user-facing TPM evaluation dimensions for V1. Scenario-local rubric lines remain the deterministic evidence substrate, but benchmark results are summarized through these dimensions so a reviewer can answer "how does this model do as a TPM?" without reading raw rubric ids.

## Design goals

- keep deterministic evaluator authority
- present stable TPM-specific dimensions across scenarios
- separate outcome movement from operating style
- make each measurement defensible in architecture review

## Competency dimensions

### `discovery_situation_awareness`
- Measures: whether the TPM surfaces hidden facts, blockers, and changed reality early enough to matter.
- Counts: fact discovery, blocker discovery, reading the right artifacts, noticing changed stakeholder state, and inferring high-signal stakeholder private drivers from visible cues.
- Does not count: lucky outcomes without evidence of discovery, or text churn that never changes what the TPM knows.

### `critical_path_prioritization`
- Measures: whether the TPM focuses effort on the real bottleneck instead of side work or coordination theater.
- Counts: spending turns on gating work, avoiding distraction overinvestment, sequencing around the highest-leverage next step.
- Does not count: message volume, tracker churn, or meetings that do not move a bottleneck.

### `decision_tradeoff_management`
- Measures: whether the TPM drives the right tradeoffs and converges the org on the feasible path.
- Counts: scope decisions, descoping, tradeoff framing, forcing clarity where the path is ambiguous.
- Does not count: status narration without a path decision.

### `commitment_dependency_management`
- Measures: whether the TPM turns information into credible commitments and closes the dependency edges needed to execute.
- Counts: approvals at the right moment, feasible ETA commitments, dependency handling, avoiding invalid promises.
- Does not count: dates or approvals that exist only in prose without supporting state.

### `stakeholder_alignment_communication`
- Measures: whether the TPM keeps the right actors on the same story and uses communication to reduce misalignment.
- Counts: shared belief convergence, explicit decision communication, updates that change stakeholder understanding.
- Does not count: a large number of messages without belief convergence.

### `escalation_influence`
- Measures: whether the TPM uses escalation and influence effectively without escalating too early or burning trust.
- Counts: using sponsor/manager pressure only when normal coordination is insufficient, preserving usable trust while pushing.
- Does not count: escalation spam or authority requests before preconditions are ready.

## Outcome dimensions

### `outcome_attainment`
- Measures: whether the TPM actually moved the scenario to the intended milestone outcomes.
- Counts: scenario-defined milestone completion and outcome-bearing commitments.
- Does not count: process motion that never changes project state.

### `timing_optionality_preservation`
- Measures: whether the TPM acted soon enough to preserve leverage windows, recoverability, and credible alternatives.
- Counts: beating deadlines, acting before cutoffs, avoiding the point where the scenario becomes unrecoverable.
- Does not count: eventually doing the right thing after the useful window has already closed.

## Mapping rule

Each scenario-local rubric line must declare:
- `competency_tags`
- `measurement_rationale`
- `success_meaning`
- `failure_meaning`

`competency_tags` may include both competency dimensions and outcome dimensions. A line can inform multiple dimensions. Dimension scores are computed independently over the full set of rubric lines tagged to that dimension; they are not intended to sum to 100.
