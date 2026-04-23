# TPM Bundle Performance Summary V2

`BundlePerformanceSummary` V2 is the current canonical bundle-level output for `agent bundle-eval` and `summarize-bundle`.

The artifact paths are:
- `bundle_performance_summary.json`
- `bundle_performance_summary.md`

Consumers should branch on `schema_version`, not file name. The current value is:
- `tpm_bundle_performance_summary_v2`

## Design intent

The bundle summary is deterministic aggregation over per-seed run summaries. It exists to answer the model-comparison question directly:
- what the model usually does across the official seed bundle
- what stays weak even when the seed changes
- whether misses cluster around signals, stakeholders, or deadline windows
- whether the bundle-level conclusion is clean or polluted by harness noise

## Top-level shape

- `bundle_header`
- `headline`
- `aggregate_capability_assessment`
- `aggregate_competency_profile`
- `seed_consistency`
- `recurring_root_causes`
- `stakeholder_failure_patterns`
- `signal_coverage_consistency`
- `driver_signal_consistency`
- `private_note_audit_aggregate`
- `reference_divergence_patterns`
- `window_miss_recurrence`
- `confidence_scope`
- `top_recurring_failure_themes`
- `harness_health`
- `runs`
- `dimension_highlights`
- `narrative`

## Deterministic aggregation

The bundle summary does not run a separate grader. It aggregates the already-deterministic per-seed run summaries.

Its authority comes from:
- the per-seed `tpm_performance_summary.json` files
- the fixed seed bundle
- deterministic summary logic over those run summaries

## `headline`

`headline` is the bundle score snapshot:
- `mean_score`
- `worst_score`
- `best_score`
- `stdev`
- `score_possible`

## `aggregate_capability_assessment`

This is the top-line TPM verdict across the bundle:
- `rating`
- `direct_answer`
- `confidence_scope`

The current confidence scope is higher only when the bundle has multiple seeds, bounded variance, and clean harness health.

## `aggregate_competency_profile`

This is the cross-seed rollup of the stable TPM competencies and outcome dimensions.

Each row includes:
- `id`
- `label`
- `mean_score`
- `worst_score`
- `best_score`
- `spread`
- `stdev`
- `band`

## `seed_consistency`

`seed_consistency` is the high-level hygiene summary:
- protocol failure count
- coverage miss count
- whether score variance stayed within the allowed band

## `recurring_root_causes`

This section tracks which deterministic root-cause findings recur across the bundle and how costly they are.

Each row includes:
- `id`
- `title`
- `count`
- `mean_lost_points`
- `share_of_runs`
- `seeds`

## Stakeholder and signal consistency

`stakeholder_failure_patterns` answers which important actors were never contacted, contacted too late, or left with unanswered direct questions across seeds.

`signal_coverage_consistency` answers how often critical signals surfaced and how often surfaced signals converted into plan changes.

`driver_signal_consistency` is the same view narrowed to actor-driver signals.

## `window_miss_recurrence`

This section shows which deadline windows repeatedly failed across seeds, including the affected seeds. It is the bundle-level counterpart to the run-level `window_scorecards[]`.

## `runs[]`

`runs[]` gives one comparison row per seed. Each row includes:
- the seed, score, and score percent
- outcome verdict and capability rating
- critical-path status
- critical-signal surfaced and converted counts
- critical actors never contacted or contacted after deadline
- unanswered direct-question count
- windows hit and missed
- top root cause and top failure theme
- overall, model, and harness status
- the per-seed summary path

## `dimension_highlights`

`dimension_highlights` extracts the most reviewer-useful slices of the competency profile:
- `stable_strengths`
- `stable_weaknesses`
- `seed_sensitive`

## `harness_health`

`harness_health` aggregates recurring harness or authoring issues so reviewers can distinguish a model problem from evaluation-surface noise.

## Narrative layer

`narrative` is a deterministic rendered summary over the bundle fields above. It is explanatory, not authoritative.
