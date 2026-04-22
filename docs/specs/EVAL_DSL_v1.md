# EVAL_DSL_v1

The evaluation DSL is a thin rubric layer over the predicate DSL.

## Rubric line shape

Each rubric line is a JSON object with:

- `id`
- `label`
- `weight`
- `failure_class`
- `scoring_type`
- `success_predicate`
- `partial_credit_predicates`
- `failure_predicates`
- `deadline_or_window`
- `evidence_requirements`

## Scoring types

### binary

- Awards the full weight if `success_predicate` matches and all evidence requirements are satisfied.
- Awards `0` otherwise.

### count_fraction

- `partial_credit_predicates` is an array.
- Score is `weight * matched_count / total_count`.
- If no evidence refs are collected, score is `0`.

### thresholded

- `partial_credit_predicates` is an ordered array of `{ score, predicate }`.
- Highest matching score wins, capped by `weight`.
- `success_predicate` may be omitted when thresholded predicates are exhaustive.

### bounded_penalty

- Start from `weight`.
- For each matching failure predicate, subtract its authored `penalty`.
- Clamp at `[0, weight]`.

## Evidence requirements

Each rubric line may declare:

- `min_refs`
- `require_event_ref`
- `require_state_transition_ref`

If evidence requirements are not met, the line scores zero.

## Failure taxonomy

Each rubric line contributes to exactly one primary `failure_class`:

- `discovery`
- `alignment`
- `commitment`
- `timing`
- `prioritization`
- `relationship`

The evaluator aggregates unmet weight by failure class to produce the failure breakdown.
