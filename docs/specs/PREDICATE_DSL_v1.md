# PREDICATE_DSL_v1

This document defines the JSON predicate DSL used by:

- hidden-fact surfacing rules
- task and milestone progression rules
- NPC context-family guards
- rubric scoring rules
- readiness-gate checks

## Core model

Predicates are JSON objects. A predicate evaluates against:

- the current SQLite-backed world state
- the run traces
- the current simulated time
- optional runtime context, such as an NPC context family match request

Each predicate returns:

- `matched: bool`
- `evidence_refs: list[str]`
- `matched_predicates: list[str]`

Rubric lines without evidence refs score zero even if the predicate matched.

## Boolean composition

### all_of

```json
{ "all_of": [P1, P2, P3] }
```

Matches if every child predicate matches.

### any_of

```json
{ "any_of": [P1, P2] }
```

Matches if at least one child predicate matches.

### not

```json
{ "not": P }
```

Matches if the child predicate does not match.

## Temporal wrappers

### before

```json
{ "before": { "time": "2026-05-05T12:00:00", "predicate": P } }
```

Matches if the wrapped predicate has evidence strictly before the given timestamp.

### after

```json
{ "after": { "time": "2026-05-05T12:00:00", "predicate": P } }
```

Matches if the wrapped predicate has evidence at or after the given timestamp.

### within

```json
{
  "within": {
    "start": "2026-05-05T09:00:00",
    "end": "2026-05-05T12:00:00",
    "predicate": P
  }
}
```

Matches if the wrapped predicate has evidence in the closed interval.

### eventually_before

```json
{
  "eventually_before": {
    "time": "2026-05-06T12:00:00",
    "predicate": P
  }
}
```

Alias for `before` used when the author wants intent to be explicit.

## Quantifiers

### count_at_least

```json
{
  "count_at_least": {
    "count": 2,
    "predicates": [P1, P2, P3]
  }
}
```

Matches if at least `count` child predicates match.

## State predicates

### surfaced

```json
{ "surfaced": "backend_infeasible_for_friday" }
```

Matches if the fact has a non-null `surfaced_at`.

### fact_state

```json
{
  "fact_state": {
    "fact_id": "backend_infeasible_for_friday",
    "field": "surfaced_by",
    "equals": "message:27"
  }
}
```

Matches against fields inside the fact state JSON or top-level fact columns.

### milestone_state

```json
{
  "milestone_state": {
    "milestone_id": "scope_aligned",
    "field": "status",
    "equals": "done"
  }
}
```

### task_true_state

```json
{
  "task_true_state": {
    "task_id": "backend_api",
    "field": "checkpoint",
    "equals": "done"
  }
}
```

### task_tracker_state

```json
{
  "task_tracker_state": {
    "task_id": "backend_api",
    "field": "status",
    "equals": "blocked"
  }
}
```

### project_state

```json
{
  "project_state": {
    "field": "customer_confidence",
    "equals": "stable"
  }
}
```

### relationship_state

```json
{
  "relationship_state": {
    "actor_id": "maya",
    "target_actor_id": "tpm",
    "field": "trust",
    "gte": 0.4
  }
}
```

### commitment_state

```json
{
  "commitment_state": {
    "commitment_id": "backend_descoped_eta",
    "field": "status",
    "in": ["tentative", "committed", "fulfilled"]
  }
}
```

### belief_known

```json
{
  "belief_known": {
    "actor_id": "rohit",
    "belief_key": "project.launch_scope",
    "equals": "descoped_pilot",
    "min_confidence": 0.6,
    "fresh_within_min": 240
  }
}
```

Matches if the latest belief record for the actor/key satisfies the value, confidence, and freshness constraints.

### critical_window_open

```json
{ "critical_window_open": "security_cutoff" }
```

Matches if the current simulation time is within the authored window.

### window_state

```json
{
  "window_state": {
    "window_id": "security_cutoff",
    "field": "closed",
    "equals": false
  }
}
```

## Trace predicates

### action_occurred

```json
{
  "action_occurred": {
    "actor_id": "tpm",
    "surface": "chat",
    "act_id": "request.feasibility",
    "slots": {
      "task_id": "backend_api",
      "target_actor_id": "maya"
    }
  }
}
```

Matches over the `actions` table. `slots` is a partial match against stored action slots.

### event_occurred

```json
{
  "event_occurred": {
    "event_type": "fact_signal",
    "where": {
      "fact_id": "backend_infeasible_for_friday",
      "observer_id": "tpm"
    }
  }
}
```

Matches over the event log. `where` may match top-level fields or payload keys.

### productive_meeting

```json
{ "productive_meeting": "launch_tradeoff_huddle" }
```

Matches if the meeting completed and the meeting metadata indicates at least one authored outcome fired.

## Evidence semantics

- `action_occurred` evidence refs are `action:<id>`
- `event_occurred` evidence refs are `event:<id>`
- belief evidence refs are `belief:<id>`
- state-derived predicates such as milestones and commitments must attach at least one supporting `event:<id>` or `state_transition:<id>` reference

## Matching notes

- Equality supports `equals`, `in`, `gte`, `lte`, `gt`, `lt`
- Missing fields are treated as non-matches
- Time comparisons are always done against authored local-time ISO timestamps with second precision
