# Architecture

## Design Goal

The harness is a **bounded, event-driven, partially observable TPM evaluation environment**. The central question is:

> How does a model do as a TPM when the environment, agent harness, and scenarios are held constant?

This is deliberately **not** a free-form roleplay simulator and **not** an open-ended company sandbox. The implementation keeps the world model explicit enough that a reviewer can answer:
- what advanced synchronously with a TPM action
- what advanced asynchronously in the background
- what the TPM could observe
- what the scorer used as evidence
- what parts of the system are benchmark truth versus authoring tooling

## System Layers

V1 now has five layers:

### 1. Deterministic runtime kernel

This is the simulation core:
- SQLite-backed state
- discrete-event clock
- structured acts
- frozen NPC context families
- evaluator
- traces

It is the authoritative benchmark runtime.

### 2. Standardized TPM agent harness

This is the fixed interface used to compare models:
- canonical observation packing
- bounded recent interaction history
- deterministic working-memory view
- fixed prompt pack
- one action per turn
- one repair attempt for invalid outputs

This layer is part of the benchmark definition, not per-model customization.

### 3. Canonical performance summary and judge layer

This is the reporting layer that turns raw deterministic scoring into a TPM-native answer:
- canonical `TPMPerformanceSummary`
- canonical `BundlePerformanceSummary`
- deterministic behavior diagnostics
- explanatory-only LLM judge narrative

Deterministic scoring remains authoritative. The judge layer is allowed to explain and synthesize, but never to change benchmark truth.

### 4. Offline authoring workflow

This is not runtime. It exists to generate and curate benchmark truth:
- structured authoring briefs
- proposal directories
- offline synthesis of candidate world / coverage / trajectories
- validation, diff, and explicit accept

Runtime uses only the accepted frozen artifacts.

### 5. Scenario assets

V1 ships:
- one deep official scenario: `northstar_launch_week`
- one lightweight smoke/generalization scenario: `internal_rollout_smoke`

The second scenario proves the abstractions generalize and gives a cheaper test target. It is not part of the headline benchmark claim.

## Core Runtime Choices

### Single-node, SQLite-backed runtime

Everything runs in one process with one SQLite file per run.

Why:
- deterministic replay
- local setup with no service mesh
- cheap checkpoint and fork support
- easy auditing

SQLite stores both visible and hidden world state. There is no prompt-only hidden truth.

### Discrete-event time

Time is continuous in timestamp representation but moves through **discrete jumps**:
- effectful TPM actions consume simulated time
- explicit `wait` actions advance time
- due events are processed in deterministic order

This preserves:
- meaningful delay semantics
- inspectable causality
- replay stability

### Frozen runtime semantics

Runtime coworker behavior is frozen through authored context families and response envelopes.

LLM use is reserved for:
- live TPM model evaluation
- explanatory-only run and bundle narratives
- offline authoring synthesis

LLMs are **not** used for:
- runtime NPC behavior
- runtime grading
- hidden-truth generation

That split is the key benchmark discipline in V1.

## Canonical Environment API

The programmatic environment is the canonical TPM interface. The shell is only a thin client over it.

The fixed API is:
- `reset(scenario_id, seed, coverage_enforcement)`
- `observe()`
- `step(action)`
- `score()`
- `export_report(prefix)`
- `checkpoint(label)`
- `fork(checkpoint_ref, seed_override=None)`

Every live TPM model and every shell/scripted trajectory now goes through the same structured action dispatch path.

## State Model

The runtime stores six first-class state domains.

### 1. World facts

Hidden truth and authored constraints:
- feasibility blockers
- approvals required
- leverage windows
- competing priorities
- actor private drivers such as stakeholder incentives, sensitivities, and relationship-biased coordination preferences

### 2. Belief state

Per-actor structured beliefs:
- `belief_key`
- `belief_value`
- `confidence`
- `freshness_window_min`
- `updated_at`
- `source_ref`

Belief is the official answer to “who knew what, and when?”

Some hidden truths are authored as `actor_private_driver` facts. Those do not surface directly from actor metadata. Instead, visible doc/thread/meeting cues emit low-confidence belief signals that can accumulate until the TPM has enough evidence to treat the private driver as surfaced benchmark truth.

### 3. Commitments

Thin first-class ledger:
- owner
- audience
- subject
- scope
- status
- confidence
- due time
- preconditions
- source
- feasibility

Commitments may be invalid or stale. That is intentional.

### 4. Relationship / influence

Actor-to-actor relationship state, focused in V1 on:
- trust

This is enough to model responsiveness costs, escalation cost, and whether noisy coordination is damaging.

### 5. Execution state

Execution is modeled as:
- task state machines with true checkpoint state
- visible tracker state
- milestones with recoverability

### 6. Temporal state

Temporal state includes:
- global clock
- work hours
- actor availability
- pending events
- critical windows

## Three Parallel Views

The simulation explicitly separates:

### True execution state

What is actually happening:
- checkpoint
- remaining work
- true blockers

### Shared artifact state

What the tools say:
- task tracker status
- docs
- meeting transcripts
- notes

### Per-actor belief state

What a specific actor believes after interacting with visible surfaces.

## Synchronization Rules

These rules are fixed:

- `True -> Tracker` only through authored progression rules or explicit tool updates
- `True -> Belief` never happens directly
- `Tracker -> Belief` only through reads or informing acts
- `Doc/Transcript/Thread -> Belief` only through reads or direct delivery

This is what makes partial observability legible instead of hand-wavy.

## TPM as an Actor

The TPM is an explicit actor row with:
- actor id `tpm`
- `policy_type = external_agent`
- calendar participation
- chat membership
- note ownership

That keeps the world symmetric enough for:
- inbound chats to the TPM
- meetings with the TPM as attendee
- relationship edges into the TPM

## Tool Surfaces

V1 exposes:
- chat
- calendar / meetings
- task tracker
- docs
- private notes

Important semantic choice:
- free-text bodies are stored for realism
- **structured acts and slots are authoritative**

The body text is not used for semantic credit in V1.

## Standardized TPM Agent Harness

The harness exists to make cross-model comparison meaningful.

Every model sees the same:
- observation schema
- bounded recent history
- deterministic extractive working-memory view
- prompt pack
- action schema
- repair policy
- stop conditions

The working-memory view is intentionally **extractive only**. It can summarize:
- surfaced facts
- visible open commitments
- unresolved blockers
- visible deadlines/windows
- pending meetings
- milestone/task summaries
- actor directory and canonical target ids
- visible precondition fragments for milestone lines
- pending replies and open coordination needs
- last meaningful stakeholder responses

It does **not** contain:
- prioritization
- suggested next actions
- hidden truth
- model-written summaries

This keeps the harness useful without doing TPM judgment for the model.

## Agent Adapter Layer

The agent subsystem is provider-agnostic:
- adapter interface
- runner
- run record
- prompt pack
- model client abstraction

V1 ships one concrete adapter:
- `OpenAIResponsesAgentAdapter`

The runner contract is strict:
- one model turn chooses one action
- one repair attempt for invalid output
- second invalid output ends the run with protocol failure
- every prompt/response is persisted locally

The benchmark result is still computed from the environment state, not from model self-report.

## Stable TPM Competency Model

The user-facing evaluation surface is intentionally stable across scenarios.

Competency dimensions:
- Discovery & Situation Awareness
- Critical Path Prioritization
- Decision & Tradeoff Management
- Commitment & Dependency Management
- Stakeholder Alignment & Communication
- Escalation & Influence

Outcome dimensions:
- Outcome Attainment
- Timing / Optionality Preservation

Scenario-local rubric lines still exist, but only as the evidence substrate that rolls up into these stable dimensions.

## Predicate DSL

The predicate DSL in `docs/specs/PREDICATE_DSL_v1.md` is shared by:
- hidden-fact surfacing
- milestone readiness
- rubric predicates
- context-family guards
- readiness checks

This is the main defense against keyword heuristics and prompt spaghetti.

## Context Families and NPC Coverage

Each accepted scenario now has three source-of-truth artifacts:
- `scenario.json`
- `coverage_contract.json`
- `coverage_semantics.json`

plus one explanatory artifact:
- `operator_briefing.json`

and one compiled runtime artifact:
- `npc_coverage.json`

The split is deliberate:
- `scenario.json` defines world, policy, and evaluation
- `coverage_contract.json` deterministically enumerates reachable NPC interaction situations
- `coverage_semantics.json` contains the LLM-authored semantic realization for those situations
- `operator_briefing.json` is a deterministic human/operator briefing used by CLI overviews and pre-run preflights
- `npc_coverage.json` is compiled deterministically for the runtime engine

For hidden agendas, the authoring pattern is:
- store the latent motive as an `actor_private_driver` fact in `scenario.json`
- leak it through cue-shaped `belief_signals` in `coverage_semantics.json`
- let the runtime accumulate those cues into surfaced truth only after the confidence threshold is crossed

At runtime, `npc_coverage.json` defines frozen NPC context families. A family matches on:
- actor
- surface
- incoming act
- banded state such as trust / pressure / timing
- optional predicate guard

If a family matches:
- the engine deterministically selects a weighted response envelope
- effects are applied
- text is rendered deterministically

If no family matches:
- permissive authoring runs log a coverage gap and use a conservative fallback
- strict runs fail

Coverage is measured against authored reachable cells, not inferred from vibes.

## Meetings

Meetings are semantic coordination surfaces, not free-form transcript simulators.

A productive TPM-attended meeting requires:
- preparation through `meeting.propose`
- the right attendees
- authored preconditions
- up to 2 TPM in-meeting acts

Meeting outcomes are semantic first. The transcript is a deterministic rendering of those semantic outcomes.

## Event Queue and Ordering

Pending events are stored in SQLite and processed by:
- `timestamp`
- `phase_priority`
- `insertion_sequence`

This ordering is part of determinism. It avoids ambiguous same-minute edge cases.

## Checkpoint and Fork

Checkpoint and fork are built into the core because they are evaluation primitives, not later polish.

They enable:
- ablations
- counterfactual comparisons
- authoring/debug replay
- deterministic branching from the same state

## Traces and Reports

Each run exports:
- `agent_trace`
- `omniscient_trace`
- deterministic `report.json`
- canonical `tpm_performance_summary.json`
- canonical `tpm_performance_summary.md`

Bundle evaluation additionally exports:
- `bundle_performance_summary.json`
- `bundle_performance_summary.md`

The deterministic report is evidence-backed at the rubric-line level. The canonical run summary currently uses `schema_version = tpm_performance_summary_v3`, and the canonical bundle summary uses `schema_version = tpm_bundle_performance_summary_v2`.

The run summary is the user-facing answer to “how did this model do as a TPM?” and is derived from the deterministic report plus optional explanatory judge output. The current report contracts are documented in:
- `docs/specs/TPM_PERFORMANCE_SUMMARY_v3.md`
- `docs/specs/TPM_BUNDLE_PERFORMANCE_SUMMARY_v2.md`

The bundle-level summary stays deterministic and cross-seed focused. It answers:
- how the model compares against itself across seeds
- which TPM dimensions are consistently weak versus seed-sensitive
- which stakeholder and signal failures recur often enough to treat as structural
- whether bundle conclusions are cleanly attributable to the model versus harness noise

## Deterministic Diagnostics

Every run summary includes deterministic behavior diagnostics that separate:
- model behavior issues
- harness interface issues
- scenario authoring issues

Current diagnostics include:
- repeated same-target same-act loops
- artifact churn
- tracker churn
- escalation repetition
- approval-before-preconditions
- unresolved reply loops
- alias normalization corrections
- protocol repair count
- coverage miss count

This is what lets the harness say “the model behaved poorly” versus “the scenario still has an authoring closure issue.”

## Offline Authoring Workflow

Authoring is deliberately proposal-based.

The human-maintained intent source is a **structured authoring brief**.

Each cast member can now declare `private_drivers[]` so authoring intent captures not just public role and notes, but also the hidden stakeholder incentives or sensitivities the TPM is expected to infer from visible cues.

The authoring pipeline stages are:
- `author init`
- `author synthesize-world`
- `author compile-contract`
- `author synthesize-semantics`
- `author compile-coverage`
- `author synthesize-trajectories`
- `author validate`
- `author closure-suite`
- `author gap-fill`
- `author diff`
- `author accept`

Each proposal lives in its own directory and contains:
- candidate scenario bundle
- candidate coverage contract
- candidate coverage semantics
- compiled runtime coverage
- candidate trajectories
- operator briefing artifacts
- validation report
- closure report
- diff summary
- review summary
- manifest

Nothing mutates official benchmark truth until an explicit accept step.

## Why This Architecture Is Defensible

The important claims V1 can honestly make are:
- the runtime semantics are explicit and deterministic
- the TPM agent harness is standardized across models
- the benchmark truth is frozen and auditable
- the authoring workflow is disciplined and proposal-based
- the system already supports one deep official scenario and one lighter generalization scenario

That is enough to make V1 a serious harness demonstration without overclaiming calibrated benchmark maturity.
