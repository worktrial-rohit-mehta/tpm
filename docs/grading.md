# Grading

## Philosophy

The evaluator rewards **improved outcomes and sound coordination**, not activity for its own sake.

The intended ordering is:
1. outcomes and readiness
2. timing and judgment
3. activity only insofar as it changes the world

This is why the grader keys off:
- structured state transitions
- predicate matches
- evidence refs

and not:
- message counts
- doc counts
- free-text keyword density

## Two Evaluation Loops

V1 uses two distinct loops:

### Scripted calibration

This is how the environment itself is validated.

Northstar ships with authored reference trajectories:
- `golden`
- `competent_but_imperfect`
- `busywork`
- `false_green`
- `spray_and_pray`

These are used for:
- readiness gating
- variance characterization
- proving that the harness differentiates meaningful TPM behavior from busywork

### Live TPM-agent evaluation

This is how labs answer:

> How does my model do as a TPM?

For live evaluation, the environment, agent harness, prompt pack, and seed bundle stay fixed. The model is the primary variable.

## Scoring Model

The evaluator is deterministic and state-derived.

It supports the scoring types defined in `docs/specs/EVAL_DSL_v1.md`:
- `binary`
- `count_fraction`
- `thresholded`
- `bounded_penalty`

Each rubric line references predicate-DSL expressions instead of bespoke scenario Python hidden inside the grader.

For reviewers, score components are inspectable at three layers:
- `score_breakdown.groups[]`, `earned_lines[]`, and `missed_lines[]` show how raw rubric points were earned or left on the table
- `tpm_competency_profile[]` and `outcome_profile[]` roll those rubric lines into stable TPM-facing dimensions
- `evidence_appendix.rubric_lines[]` plus `evidence_catalog[]` keep the credited evidence auditable

## Evidence Requirement

Rubric credit is only valid when the matched predicate also carries enough evidence.

Every rubric line stores:
- awarded points
- evidence refs
- matched predicate refs
- deadline/window metadata
- explanation text

Rubric items without valid evidence award zero points.

This is a hard grading rule, not a reporting nicety.

## Stable TPM Competency Model

The raw rubric still exists, but it is no longer the primary user-facing evaluation surface.

V1 now reports stable TPM competencies:
- `Discovery & Situation Awareness`
- `Critical Path Prioritization`
- `Decision & Tradeoff Management`
- `Commitment & Dependency Management`
- `Stakeholder Alignment & Communication`
- `Escalation & Influence`

and two stable outcome dimensions:
- `Outcome Attainment`
- `Timing / Optionality Preservation`

Each scenario-local rubric line contributes to one or more of those stable dimensions through:
- `competency_tags`
- `measurement_rationale`
- `success_meaning`
- `failure_meaning`

This makes the criteria defensible in two ways:
- the scenario can still be specific about what mattered in that situation
- the user-facing output stays stable enough to compare models as TPMs, not just as “who got a 61 on this exact scenario”

## Failure Taxonomy

The legacy failure classes still exist inside scenario configs:
- `timing`
- `alignment`
- `discovery`
- `commitment`
- `relationship`
- `prioritization`

But they are now treated as lower-level authoring and debugging metadata. User-facing summaries instead surface:
- `competency_gaps`
- `outcome_gaps`
- decisive failures with evidence refs

## Reward-Hacking Protections

The current harness explicitly resists:

### Wait-it-out behavior

Waiting through important windows loses timing and outcome points.

### Meeting spam

Meetings do not score by existing. Only downstream state changes matter.

### Tracker-edit gaming

Tracker edits change visible state, not ground truth.

### Document spam

Docs matter only if they change beliefs, commitments, or milestone outcomes.

### Private-note stuffing

Private notes carry **zero** score weight.

### Keyword gaming

Free-text bodies are non-authoritative. Semantic credit comes from structured acts and authored predicates.

### Harness versus model confusion

Run summaries now include deterministic diagnostics that explicitly split:
- `model_behavior_issues`
- `harness_interface_issues`
- `scenario_authoring_issues`

This prevents obvious harness or authoring defects from being misread as TPM incompetence.

## Official Scenario: `northstar_launch_week`

Northstar is the only fully calibrated official scenario in V1.

It stresses:
- timing failure
- discovery failure
- commitment failure

The decisive questions are:
- did the TPM surface backend infeasibility and security review in time?
- did they pick up the hidden stakeholder drivers that changed how those conversations needed to be handled?
- did they secure the approval path before the cutoff?
- did they convert descoping from a vague idea into a shared, credible plan?
- did they avoid externally optimistic commitments before the path was real?

### Official seed bundle

Northstar’s official seed bundle is:
- `11`
- `29`
- `47`

The headline benchmark result is the **mean score across the official seed bundle**.

The report also includes:
- worst-case seed score
- per-seed scores
- decisive moments
- recoverability summary
- trace paths

### Variance characterization

Northstar ships a 20-seed variance characterization to justify the smaller official bundle.

Readiness output is intentionally command-derived instead of hardcoded in this document:

```bash
python3 -m tpm_sim readiness --scenario northstar_launch_week
```

The important properties are:
- strong coordination beats competent-but-incomplete work
- competent-but-incomplete work beats performative activity
- false certainty and noisy coordination score badly for the intended reasons
- golden and competent variance stay bounded across the 20-seed characterization

That bounded variance is why the smaller official bundle is acceptable for this single-scenario harness demo.

Accepted `validation.json` and `closure_report.json` artifacts are also freshness-checked against the current scenario bundle and compiled coverage digests.
If authored scenario inputs or grading specs move without regenerating those reports, the loader marks them `stale` rather than trusting them as current calibration evidence.

## Lightweight Scenario: `internal_rollout_smoke`

The smoke scenario exists to:
- prove the abstractions generalize beyond Northstar
- provide a cheaper and faster regression target

It is **not** part of the headline official benchmark claim.

Its role in grading is:
- schema/coverage/smoke validation
- fast scripted sanity checks
- quick live-agent smoke evaluation

It does **not** ship the full five-trajectory readiness bundle, so `python3 -m tpm_sim readiness --scenario internal_rollout_smoke` is expected to fail fast with a calibration-specific error.

It should not be used to claim calibrated TPM capability.

## Live-Agent Evaluation Reporting

Live TPM-agent runs now emit one canonical run artifact:
- `tpm_performance_summary.json`
- `tpm_performance_summary.md`

The current run artifact uses `schema_version = tpm_performance_summary_v3`.
That summary now includes:
- run header
- score breakdown
- capability assessment
- outcome verdict
- critical path result
- root-cause findings
- top failure dossiers
- stakeholder engagement
- signal coverage
- window scorecards
- missed opportunities
- reference-path diff
- TPM competency profile
- decisive timeline
- top strengths
- top failures
- improvement opportunities
- run health
- evidence appendix
- raw scoring appendix

`failure_dossiers[]` remains the highest-signal short-form run-level field. Each dossier deterministically captures:
- the missed line or interruption
- points lost
- deadline/window metadata
- agent-visible signal refs
- example TPM action refs
- recurring contributing patterns
- a fixed remediation hint

Run health is also split into:
- `overall_status`
- `model_status`
- `harness_status`

This makes the output more useful for frontier-lab feedback because the report can now distinguish “model found the signal but kept making the wrong move” from “the run was interrupted by harness or coverage issues.”

Bundle evaluation across official seeds emits:
- `bundle_performance_summary.json`
- `bundle_performance_summary.md`

The current bundle artifact uses `schema_version = tpm_bundle_performance_summary_v2`.
That bundle summary includes:
- mean / best / worst score and seed variance
- per-seed comparison tables
- aggregate competency and outcome profile
- recurring root-cause patterns across seeds
- critical-signal and stakeholder-handling consistency tables
- deadline-window recurrence and harness-health summary

The bundle report remains deterministic today. It is intended to answer the model-comparison question directly:
- what usually goes wrong
- what stays weak even when the seed changes
- whether the model reliably notices and acts on the right TPM clues
- whether the result is a model problem or a harness problem

This keeps the model-comparison question clean:
- the runtime is fixed
- the agent harness is fixed
- the model changes
- the output surface is TPM-native rather than rubric-native

## Explanatory Judge Layer

V1 includes an optional explanatory-only LLM judge layer.

Its role is:
- synthesize an evidence-cited TPM narrative
- turn deterministic evidence into a readable diagnosis

It may **not**:
- change scores
- invent evidence
- invent hidden causes
- override deterministic conclusions

If the judge output is invalid or unavailable, the system falls back to a deterministic template narrative.

## Report Shape

Running:

```bash
python3 -m tpm_sim eval --db .artifacts/demo.sqlite --export-prefix .artifacts/demo_run
```

emits:
- `.report.json`
- `.agent_trace.jsonl`
- `.omniscient_trace.jsonl`

Live-agent runs additionally persist:
- `agent_run.json`
- captured prompt/response history
- `tpm_performance_summary.json`
- `tpm_performance_summary.md`
- `judge_input_bundle.json`

The deterministic report contains:
- total score
- rubric breakdown
- decisive moments
- recoverability
- coverage-miss flag
- trace paths

The canonical TPM summary contains:
- score breakdown
- capability assessment
- TPM competency rollup
- outcome rollup
- deterministic diagnostics
- evidence-backed TPM diagnosis

## Reviewer Audit Path

If you want the shortest defensible grading audit, run:

```bash
python3 -m tpm_sim readiness --scenario northstar_launch_week
python3 -m tpm_sim coverage-report --scenario northstar_launch_week
python3 -m tpm_sim benchmark --scenario internal_rollout_smoke --script examples/internal_rollout_smoke/smoke.tpm
```

Current command-derived snapshot from the checked-in accepted assets:
- `readiness --scenario northstar_launch_week` reports `golden = 81.67`, `competent_but_imperfect = 59.34`, `false_green = 26.0`, `busywork = 13.33`, `spray_and_pray = 9.0`, and all readiness gates passing
- `coverage-report --scenario northstar_launch_week` reports `Coverage: 43 / 43 (1.000)` with `Critical uncovered: 0`
- `benchmark --scenario internal_rollout_smoke --script examples/internal_rollout_smoke/smoke.tpm` reports `Mean score: 90.83`, `Worst seed: 87.5`, with per-seed scores `92.5`, `92.5`, and `87.5`

Treat those numbers as a snapshot of the current accepted assets, not as a hand-maintained constant. Re-run the commands above before citing them externally.

The reward-hacking resistance is visible in that separation:
- `golden` materially beats `competent_but_imperfect`
- `competent_but_imperfect` materially beats `false_green`, `busywork`, and `spray_and_pray`
- the weak trajectories do not recover by spamming meetings, docs, tracker edits, or optimistic language

## Why This Grader Is Defensible

The grader is defensible because:
- it is deterministic
- it is predicate-driven
- it is evidence-backed
- it does not trust free-text keywords
- it separates visible state from hidden truth
- it records enough trace data to audit “did the agent have enough information?”
- it surfaces stable TPM competencies rather than only scenario-local rubric lines
- it can provide a readable narrative without giving the LLM authority over benchmark truth

V1 still does **not** claim statistical authority from one deep scenario. What it does claim is that the grading semantics are explicit, inspectable, and stable enough to support serious TPM evaluation experiments.
