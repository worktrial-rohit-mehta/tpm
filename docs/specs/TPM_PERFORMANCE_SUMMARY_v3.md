# TPM Performance Summary V3

`TPMPerformanceSummary` V3 is the current canonical run-level output for live TPM-agent evaluation.

It supersedes [TPM Performance Summary V2](./TPM_PERFORMANCE_SUMMARY_v2.md). The artifact paths stay the same:
- `tpm_performance_summary.json`
- `tpm_performance_summary.md`

Consumers should branch on `schema_version`, not file name. The current value is:
- `tpm_performance_summary_v3`

## Design intent

V3 keeps deterministic scoring as the authority layer, but expands the run handoff so a reviewer can answer four questions directly:
- what score components were earned or missed
- whether the model actually moved the critical path
- which signals and stakeholders mattered most
- whether the failure was model behavior, harness friction, or scenario authoring noise

## Top-level shape

- `run_header`
- `scenario_context`
- `score_breakdown`
- `capability_assessment`
- `outcome_verdict`
- `critical_path_result`
- `root_cause_findings`
- `stakeholder_engagement`
- `signal_coverage`
- `window_scorecards`
- `missed_opportunities`
- `reference_path_diff`
- `evidence_catalog`
- `rubric_failure_appendix`
- `failure_dossiers`
- `tpm_competency_profile`
- `outcome_profile`
- `decisive_timeline`
- `key_successes`
- `key_failures`
- `improvement_opportunities`
- `run_health`
- `narrative`
- `evidence_appendix`
- `raw_scoring_appendix`
- `judge_input_bundle`

## Deterministic authority

The deterministic evaluator remains the only source of:
- official score
- rubric line success and failure
- outcome verdict inputs
- evidence references
- coverage miss state
- recoverability state

V3 is a structured deterministic interpretation of those outputs. It does not change scenario semantics or benchmark scores.

## `run_header`

`run_header` carries the stable run metadata needed to compare or audit runs:
- scenario id and digests
- validation and closure freshness status
- seed, adapter, model, and prompt-pack version
- total score and percent
- turns taken and termination reason
- simulated elapsed time
- paths to the persisted run artifacts

## `score_breakdown`

`score_breakdown` is the main answer to "where did the points go?"

It contains:
- `total_awarded`
- `total_possible`
- `total_unearned`
- `score_percent`
- `groups[]`
- `earned_lines[]`
- `missed_lines[]`

`groups[]` aggregates rubric lines by failure class so reviewers can tell whether the score shortfall was mostly timing, discovery, commitment quality, or relationship handling.

## `capability_assessment`

`capability_assessment` gives the short direct answer:
- `rating`
- `headline`
- `direct_answer`
- `confidence_scope`
- `primary_root_causes`
- `key_supporting_metrics`

It is still deterministic. The optional judge may paraphrase it in `narrative`, but may not override it.

## `root_cause_findings[]`

`root_cause_findings[]` is the richer deterministic diagnosis layer. It is capped and ordered by impact so the most important structural misses are shown first.

Each finding includes:
- `id`
- `title`
- `severity`
- `headline`
- `what_happened`
- `why_it_mattered`
- `impacted_rubric_lines`
- `impacted_milestones`
- `lost_points_total`
- `supporting_metrics`
- `signal_refs`
- `action_refs`
- `counterfactual_step`
- `counterfactual_refs`

This layer is meant to answer "what was the actual TPM mistake pattern here?" rather than just "which rubric line failed?"

## `failure_dossiers[]`

`failure_dossiers[]` remains the primary short-form failure handoff. It is still the highest-signal compact summary for a lab or reviewer who wants the top missed windows first.

Each dossier includes:
- `id`
- `kind`
- `severity`
- `rubric_line_id`
- `title`
- `lost_points`
- `deadline_label`
- `deadline_at`
- `headline`
- `why_it_matters`
- `signal_refs`
- `example_action_refs`
- `contributing_patterns`
- `metrics`
- `deterministic_fix_hint`

## `signal_coverage`

`signal_coverage` answers whether the model noticed and converted the right TPM clues.

It contains:
- `signals[]`
- `summary_metrics`

Per-signal rows track:
- whether the signal surfaced at all
- the first surface event
- whether the signal converted into a plan-changing action
- the expected action families, actors, and deadlines implied by that signal

## `stakeholder_engagement`

`stakeholder_engagement` answers whether the TPM contacted the right people at the right time.

It contains:
- `actors[]`
- `summary_metrics`

Per-actor rows track:
- decision rights
- relevant deadlines
- first cue, first read, and first outbound contact
- outbound and inbound counts
- unanswered direct questions
- whether the actor was engaged before the relevant deadline
- whether the actor was critical to the outcome

## `window_scorecards[]`

`window_scorecards[]` is the deterministic deadline audit.

Each row captures:
- the window id and title
- start and end time
- the required state change
- recoverability
- action mix before the deadline
- actor coverage before the deadline
- whether the state was actually achieved
- the miss reason when it was not

## `reference_path_diff`

`reference_path_diff` highlights where the run diverged from the authored strong path. It is not the score authority, but it is useful for counterfactual analysis and reviewer walkthroughs.

## Evidence and judge boundaries

`evidence_catalog[]` normalizes the concrete `event:`, `action:`, `message:`, and `doc:` refs used across the summary so reviewers and the judge are working from the same bounded evidence substrate.

`judge_input_bundle` is the curated, deterministic package passed to the optional explanatory judge. The judge may summarize the run, but it must not:
- change score
- change outcome verdict
- invent hidden state
- cite refs outside `allowed_evidence_refs`

## Compatibility

V3 preserves the high-level fields that earlier consumers depended on:
- `outcome_verdict`
- `critical_path_result`
- `failure_dossiers`
- `key_failures`
- `improvement_opportunities`
- `tpm_competency_profile`
- `outcome_profile`
- `run_health`
- `narrative`

V3 adds the richer deterministic scaffolding around those fields instead of replacing them.
