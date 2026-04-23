# TPM Judge IO V1

The explanatory judge is optional and non-authoritative. It consumes a deterministic `JudgeInputBundle` and produces a structured narrative supplement for `TPMPerformanceSummary`.

## Inputs

`JudgeInputBundle` includes:
- scenario context
- competency definitions
- deterministic run header
- deterministic outcome verdict
- deterministic failure dossiers
- deterministic competency and outcome profiles
- deterministic key successes and failures
- deterministic improvement opportunities
- deterministic run health and behavior diagnostics
- curated decisive timeline
- curated trace excerpt
- `allowed_evidence_refs`

The judge does not receive unconstrained raw logs as its only input. In the current run summary, the intended primary failure-analysis substrate is the deterministic `failure_dossiers[]` section plus the cited `event:` and `action:` refs attached to those dossiers.

## Outputs

The judge must return JSON with:
- `executive_summary`
- `top_strengths[]`
- `top_failures[]`
- `improvement_opportunities[]`

Each item must include:
- `title`
- `explanation`
- `evidence_refs[]`

## Validation

Judge outputs are rejected if:
- they are not valid JSON under schema
- they reference evidence refs not present in `allowed_evidence_refs`
- they fail structured validation for any required field

If validation fails, the system falls back to the deterministic narrative template.
