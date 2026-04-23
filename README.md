# TPM Harness V1

This repository ships a **deterministic TPM evaluation harness** with:
- one deep official scenario: `northstar_launch_week`
- one lightweight generalization/smoke scenario: `internal_rollout_smoke`
- a fixed TPM agent harness for cross-model comparison
- a canonical `TPMPerformanceSummary` / `BundlePerformanceSummary` reporting surface
- an explanatory-only LLM judge layer for evidence-cited TPM diagnosis
- an offline, proposal-based authoring workflow for generating and freezing benchmark artifacts

V1 is intentionally framed as an **engineering demonstration of the harness primitives**, not a calibrated frontier-model benchmark:
- one scenario is not enough to claim authoritative pass rates
- the scoring weights are authored hypotheses, not universal truth
- the architecture is designed to scale to more scenarios, but V1 proves the primitives with one deep scenario plus one smoke scenario

## What Ships

- explicit simulated time decoupled from wall-clock latency
- SQLite as the single source of truth
- a discrete-event runtime with deterministic replay
- structured state for facts, beliefs, commitments, relationships, execution, and time
- frozen coworker behavior through authored context families and response envelopes
- dual traces:
  - agent-perspective trace
  - omniscient trace
- checkpoint and fork support
- a standardized TPM agent harness
- one concrete OpenAI-backed live model adapter
- a stable TPM competency model for user-facing evaluation
- canonical run and bundle summaries derived from deterministic scoring
- an offline, LLM-assisted authoring workflow with proposal validation and explicit promotion

## Scenarios

- `northstar_launch_week`
  - the only deep, readiness-calibrated official benchmark scenario in V1
  - includes five reference trajectories:
    - `golden`
    - `competent_but_imperfect`
    - `busywork`
    - `false_green`
    - `spray_and_pray`
- `internal_rollout_smoke`
  - a lightweight generalization/smoke scenario
  - intentionally smaller and faster to run
  - used for regression checks and authoring sanity checks, not headline benchmark claims

## Runtime Model

The TPM interacts through:
- chat
- calendar / meetings / transcripts
- task tracker
- docs
- private notes

Important runtime constraints:
- coworkers are **bounded policy actors**, not live runtime LLMs
- free-text bodies are **non-authoritative**
- structured acts and slots carry the official semantics
- hidden facts are surfaced only through authored predicates
- scoring is evidence-backed and deterministic
- the LLM judge layer is **explanatory only** and never changes official scores

## Reviewer Start Here

If you want the fastest reviewer path, do this first:

1. install the deterministic harness
2. run the smoke benchmark and the calibration checks
3. read the architecture and grading docs
4. optionally run one live-model episode

Deterministic install:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

Fast reviewer commands:

```bash
python3 -m tpm_sim list-scenarios
python3 -m tpm_sim benchmark \
  --scenario internal_rollout_smoke \
  --script examples/internal_rollout_smoke/smoke.tpm
python3 -m tpm_sim coverage-report --scenario northstar_launch_week
python3 -m tpm_sim readiness --scenario northstar_launch_week
```

What to look for:
- `internal_rollout_smoke` should run quickly and produce a high scripted score, proving the basic harness flow works end to end
- `coverage-report` should show that authored reachable cells are covered
- `readiness` should separate `golden`, `competent_but_imperfect`, `false_green`, `busywork`, and `spray_and_pray` for the intended reasons

Reviewer-facing docs:
- `docs/architecture.md`
- `docs/grading.md`
- `docs/specs/TPM_PERFORMANCE_SUMMARY_v3.md`
- `docs/specs/TPM_BUNDLE_PERFORMANCE_SUMMARY_v2.md`

If you also want to inspect a live-model run, copy `.env.example` to `.env`, set `OPENAI_API_KEY` and `TPM_AGENT_MODEL`, install the OpenAI extra, and run:

```bash
python3 -m tpm_sim agent run \
  --scenario northstar_launch_week \
  --seed 11
```

## Quickstart

Requirements:
- Python `>=3.9`

For the deterministic harness only, no external services are required.

Deterministic harness install:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

Live TPM-agent runs and OpenAI-backed authoring synthesis need the OpenAI extra plus a repo-root `.env` file:

```bash
cp .env.example .env
pip install -e '.[openai]'
```

Populate `.env` with at least:
- `OPENAI_API_KEY`
- `TPM_AGENT_MODEL`

Optional:
- `TPM_AUTHORING_MODEL`
- `OPENAI_BASE_URL`
- `TPM_ARTIFACT_DIR`

The CLI auto-loads `.env` on startup without overriding variables you already exported in your shell.

Run a quick deterministic smoke check:

```bash
python3 -m tpm_sim benchmark \
  --scenario internal_rollout_smoke \
  --script examples/internal_rollout_smoke/smoke.tpm
```

Run the full test suite:

```bash
python3 -m unittest discover -s tests -v
```

## Workflow 1: Deterministic Harness

List bundled scenarios:

```bash
python3 -m tpm_sim list-scenarios
```

Initialize a run:

```bash
python3 -m tpm_sim init \
  --db .artifacts/demo.sqlite \
  --scenario northstar_launch_week \
  --seed 11 \
  --coverage-enforcement strict \
  --force
```

Open the shell:

```bash
python3 -m tpm_sim shell --db .artifacts/demo.sqlite
```

Replay the strong reference trajectory:

```bash
python3 -m tpm_sim replay \
  --db .artifacts/demo.sqlite \
  --script examples/golden.tpm
```

Export a scored report and both traces:

```bash
python3 -m tpm_sim eval \
  --db .artifacts/demo.sqlite \
  --export-prefix .artifacts/demo_run
```

Run the official scripted bundle evaluation:

```bash
python3 -m tpm_sim benchmark \
  --scenario northstar_launch_week \
  --script examples/golden.tpm
```

Run the authored readiness gate:

```bash
python3 -m tpm_sim readiness --scenario northstar_launch_week
```

`readiness` only applies to scenarios that ship the full five-trajectory calibration bundle.
In V1 that means `northstar_launch_week`. Smoke-only scenarios fail fast with a clear message and should be exercised with `benchmark` against their authored smoke script instead.

Inspect authored NPC coverage:

```bash
python3 -m tpm_sim coverage-report --scenario northstar_launch_week
```

Quick smoke run on the lightweight scenario:

```bash
python3 -m tpm_sim benchmark \
  --scenario internal_rollout_smoke \
  --script examples/internal_rollout_smoke/smoke.tpm
```

## Workflow 2: Live TPM Agent

The benchmark question is:

> How does this model do as a TPM, holding the harness and scenarios constant?

The live TPM-agent path keeps fixed:
- observation packing
- deterministic working-memory view
- prompt pack
- action schema
- repair policy
- stop conditions

The harness auto-loads a repo-root `.env` file on startup without overriding variables you already exported in your shell. Agent commands use `TPM_AGENT_MODEL` as the default live TPM model. OpenAI-backed authoring commands use `TPM_AUTHORING_MODEL` when set and otherwise fall back to `TPM_AGENT_MODEL`. `--model` still overrides the env on any individual command.

Every live TPM-agent run now emits:
- `tpm_performance_summary.json`
- `tpm_performance_summary.md`
- `judge_input_bundle.json`

The canonical run summary currently uses `schema_version = tpm_performance_summary_v3`.
It answers the TPM question with explicit deterministic sections for:
- `score_breakdown`
- `capability_assessment`
- outcome verdict
- critical-path result
- `root_cause_findings`
- `failure_dossiers`
- `stakeholder_engagement`
- `signal_coverage`
- `window_scorecards`
- `missed_opportunities`
- `reference_path_diff`
- TPM competency and outcome profiles
- explicit overall/model/harness health flags
- evidence-backed appendices and judge inputs

`failure_dossiers[]` remains the primary short-form lab-facing diagnostic surface. It deterministically explains:
- what high-value outcome or window was missed
- what agent-visible signals existed before the miss
- what the model did instead
- which recurring behavior patterns contributed
- which fixed remediation hint best fits the miss

V3 adds richer deterministic scaffolding around that surface so a reviewer can inspect:
- the exact score components
- the strongest root-cause hypotheses
- which critical signals were surfaced but not converted
- which stakeholders were contacted too late or not at all
- which deadline windows were missed and why

The optional LLM judge remains explanatory only. It summarizes the deterministic findings and cannot change score or outcome verdict.

Then run:

```bash
python3 -m tpm_sim agent run \
  --scenario northstar_launch_week \
  --seed 11
```

Before execution starts, the CLI now prints a compact scenario preflight so you can scan the premise, cast, hidden pressures, deadlines, and run configuration in the terminal.

`agent run` now streams the live event timeline to `stderr` by default while the episode is running, including omniscient simulation events beyond just the TPM agent. Use `--stream-events none` to silence it or `--stream-events agent` to limit the stream to agent-visible events.

Run the live TPM agent across the official seed bundle:

```bash
python3 -m tpm_sim agent bundle-eval \
  --scenario northstar_launch_week
```

`agent bundle-eval` writes:
- `bundle_performance_summary.json`
- `bundle_performance_summary.md`

The canonical bundle summary currently uses `schema_version = tpm_bundle_performance_summary_v2`.
It aggregates the per-seed run summaries into:
- mean / best / worst score and variance
- aggregate capability and competency profile
- recurring root-cause patterns
- stakeholder and signal consistency summaries
- deadline-window recurrence
- harness-health rollups
- per-seed comparison rows

Replay a previous live-agent run:

```bash
python3 -m tpm_sim agent replay \
  --run-dir .artifacts/agent_runs/<run-id>
```

Agent run artifacts are persisted under `.artifacts/agent_runs/...` and include:
- the canonical TPM performance summary
- the canonical judge input bundle
- the final deterministic run report
- traces
- raw prompt/response log
- structured decisions
- protocol-failure metadata

You can regenerate the canonical summary for any existing run directory:

```bash
python3 -m tpm_sim summarize-run \
  --run-dir .artifacts/agent_runs/<run-id>
```

And regenerate the aggregate bundle summary for a bundle directory:

```bash
python3 -m tpm_sim summarize-bundle \
  --bundle-dir .artifacts/agent_runs/<bundle-id>
```

## Workflow 3: Offline Authoring

Authoring is a separate offline workflow. The human maintains a **structured authoring brief**. The pipeline then separates:
- deterministic scenario and coverage-contract compilation
- LLM-assisted semantic authoring
- deterministic validation and closure checks

Nothing becomes official benchmark truth without:
- deterministic validation
- closure-suite checks
- human diff review
- explicit accept

Initialize a proposal:

```bash
python3 -m tpm_sim author init \
  --brief authoring/briefs/internal_rollout_smoke.json \
  --proposal-dir .artifacts/proposals/internal_rollout_smoke
```

`author init` now prints the full brief-level scenario overview. After `author synthesize-world`, the CLI prints the full candidate overview of what the synthesized world actually built. The other authoring commands refresh the derived operator briefing artifacts and print concise stage summaries unless `--json` is used.

Use the default offline fixture-backed synthesis path:

```bash
python3 -m tpm_sim author synthesize-world --proposal-dir .artifacts/proposals/internal_rollout_smoke
python3 -m tpm_sim author compile-contract --proposal-dir .artifacts/proposals/internal_rollout_smoke
python3 -m tpm_sim author synthesize-semantics --proposal-dir .artifacts/proposals/internal_rollout_smoke
python3 -m tpm_sim author compile-coverage --proposal-dir .artifacts/proposals/internal_rollout_smoke
python3 -m tpm_sim author synthesize-trajectories --proposal-dir .artifacts/proposals/internal_rollout_smoke
```

Validate the proposal:

```bash
python3 -m tpm_sim author validate --proposal-dir .artifacts/proposals/internal_rollout_smoke
```

Run the stricter closure checks:

```bash
python3 -m tpm_sim author closure-suite --proposal-dir .artifacts/proposals/internal_rollout_smoke
```

Diff it against the accepted scenario:

```bash
python3 -m tpm_sim author diff --proposal-dir .artifacts/proposals/internal_rollout_smoke
```

Promote a validated proposal:

```bash
python3 -m tpm_sim author accept \
  --proposal-dir .artifacts/proposals/internal_rollout_smoke \
  --examples-root examples
```

If you want live LLM-assisted synthesis instead of fixtures, use:

```bash
python3 -m tpm_sim author synthesize-world \
  --proposal-dir .artifacts/proposals/internal_rollout_smoke \
  --adapter openai
```

If you want authoring to use a different model than the TPM agent under test, set `TPM_AUTHORING_MODEL` in `.env` or pass `--model` on the individual authoring command. If `TPM_AUTHORING_MODEL` is unset, authoring falls back to `TPM_AGENT_MODEL`.

And then:

```bash
python3 -m tpm_sim author compile-contract \
  --proposal-dir .artifacts/proposals/internal_rollout_smoke

python3 -m tpm_sim author synthesize-semantics \
  --proposal-dir .artifacts/proposals/internal_rollout_smoke \
  --adapter openai

python3 -m tpm_sim author compile-coverage \
  --proposal-dir .artifacts/proposals/internal_rollout_smoke

python3 -m tpm_sim author synthesize-trajectories \
  --proposal-dir .artifacts/proposals/internal_rollout_smoke \
  --adapter openai
```

For compatibility, `author synthesize-coverage` still works as an alias for `author synthesize-semantics`, but the canonical model is:
- `coverage_contract.json`: deterministic reachable interaction situations
- `coverage_semantics.json`: LLM-authored response semantics for those situations
- `npc_coverage.json`: compiled runtime artifact

## Shell Commands

The shell is a thin client over the same structured environment API the TPM agent uses.

```text
status
people
inbox
observe
tasks
calendar
docs list
docs open DOC-ID
docs write TYPE | TITLE | BODY
notes write TITLE | BODY
notes write TITLE | ref1,ref2 | BODY
chat list
chat open THREAD_OR_ACTOR
chat send TARGET | ACT_ID | key=value,... | BODY
calendar schedule 30m | maya,andrew | TITLE | key=value,... | AGENDA
meeting act MEETING_ID | ACT_ID | key=value,... | BODY
task note TASK-ID | NOTE
task owner TASK-ID | OWNER-ID
task target TASK-ID | YYYY-MM-DDTHH:MM:SS
wait 60m
wait next 120m
coverage
score
log
checkpoint LABEL
fork LABEL | OUT_DB_PATH | [SEED]
quit
```

## Calibration Hygiene

The README does not treat copied numbers as the source of truth. Reviewers should regenerate the current outputs from the checked-in accepted assets.

Use these commands as the source of truth:

```bash
python3 -m tpm_sim readiness --scenario northstar_launch_week
python3 -m tpm_sim coverage-report --scenario northstar_launch_week
python3 -m tpm_sim benchmark --scenario internal_rollout_smoke --script examples/internal_rollout_smoke/smoke.tpm
```

Accepted scenarios also ship `validation.json` and `closure_report.json` alongside the runtime bundle.
Both files include the scenario bundle digest and compiled coverage digest, and the loader freshness-checks them at runtime.
If the scenario, authored coverage, or runtime-fingerprinted spec artifacts change without regenerating those reports, the runtime marks them `stale` instead of silently treating them as authoritative.

For current command-derived example outcomes and a reviewer audit path, see `docs/grading.md`.

## Repository Layout

```text
authoring/
  briefs/
  fixtures/
docs/
  architecture.md
  grading.md
  specs/
examples/
  internal_rollout_smoke/
tests/
tpm_sim/
  agent/
  authoring/
  cli.py
  environment.py
  engine.py
  evaluator.py
  model_client.py
  predicate.py
  scenario.py
  storage.py
  scenarios/
    northstar_launch_week/
    internal_rollout_smoke/
```

## Runtime-Fingerprinted Spec Artifacts

These files are part of the runtime benchmark definition and scenario digest:
- `docs/specs/ACT_TAXONOMY_v1.md`
- `docs/specs/PREDICATE_DSL_v1.md`
- `docs/specs/CONTEXT_FAMILY_SCHEMA_v1.json`
- `docs/specs/EVAL_DSL_v1.md`

## Reporting And Reviewer Contracts

These files are reviewer-facing contracts and explanations for the emitted reports:
- `docs/specs/TPM_COMPETENCY_MODEL_v1.md`
- `docs/specs/TPM_PERFORMANCE_SUMMARY_v3.md`
- `docs/specs/TPM_BUNDLE_PERFORMANCE_SUMMARY_v2.md`
- `docs/specs/TPM_JUDGE_IO_v1.md`

## Non-Claims

V1 does **not** claim:
- a calibrated frontier-model TPM benchmark
- statistical authority from one deep scenario
- open-ended multi-week or multi-month company simulation
- general-purpose company simulation platform
- live runtime model-based coworker behavior

The value of V1 is that the harness semantics, agent harness, and authoring workflow are explicit, replayable, inspectable, and difficult to confuse with prompt-only roleplay.
