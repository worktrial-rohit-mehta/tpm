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

## Failure Taxonomy

Each rubric line maps to one primary failure class:
- `timing`
- `alignment`
- `discovery`
- `commitment`
- `relationship`
- `prioritization`

Per-run reports aggregate “points lost” by failure class so reviewers can see not just that a model failed, but **how** it failed as a TPM.

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

## Official Scenario: `northstar_launch_week`

Northstar is the only fully calibrated official scenario in V1.

It stresses:
- timing failure
- discovery failure
- commitment failure

The decisive questions are:
- did the TPM surface backend infeasibility and security review in time?
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

Current measured readiness output:
- `golden`: mean `85.0`, stdev `0.0`
- `competent_but_imperfect`: mean `59.5`, stdev `4.5`

That bounded variance is why the smaller official bundle is acceptable for this single-scenario harness demo.

### Current readiness bands

- `golden`: `85.0`
- `competent_but_imperfect`: `61.0`
- `busywork`: `15.0`
- `false_green`: `26.0`
- `spray_and_pray`: `9.0`

The important property is separation:
- strong coordination beats competent-but-incomplete work
- competent-but-incomplete work beats performative activity
- false certainty and noisy coordination score badly for the intended reasons

## Lightweight Scenario: `internal_rollout_smoke`

The smoke scenario exists to:
- prove the abstractions generalize beyond Northstar
- provide a cheaper and faster regression target

It is **not** part of the headline official benchmark claim.

Its role in grading is:
- schema/coverage/smoke validation
- fast scripted sanity checks
- quick live-agent smoke evaluation

It should not be used to claim calibrated TPM capability.

## Live-Agent Evaluation Reporting

Live TPM-agent runs emit:
- per-run score
- protocol-failure flag
- prompt pack version
- model metadata
- structured action log
- trace paths

Bundle evaluation across official seeds emits:
- mean score
- worst-case seed score
- per-seed scores
- protocol-failure info per run

This keeps the model-comparison question clean:
- the runtime is fixed
- the agent harness is fixed
- the model changes

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

The JSON report contains:
- total score
- rubric breakdown
- failure breakdown
- decisive moments
- recoverability
- coverage-miss flag
- trace paths

## Why This Grader Is Defensible

The grader is defensible because:
- it is deterministic
- it is predicate-driven
- it is evidence-backed
- it does not trust free-text keywords
- it separates visible state from hidden truth
- it records enough trace data to audit “did the agent have enough information?”

V1 still does **not** claim statistical authority from one deep scenario. What it does claim is that the grading semantics are explicit, inspectable, and stable enough to support serious TPM evaluation experiments.
