from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Iterable, Optional

from tpm_sim.common import from_iso
from tpm_sim.scenario import load_scenario_bundle
from tpm_sim.script_dsl import parse_script_command
from tpm_sim.storage import open_store


PERFORMANCE_SUMMARY_VERSION = "tpm_performance_summary_v3"
BUNDLE_PERFORMANCE_SUMMARY_VERSION = "tpm_bundle_performance_summary_v2"
COMPETENCY_MODEL_VERSION = "tpm_competency_model_v1"
READ_ACTION_PREFIX = "read."
WRITE_ACTION_TYPES = {
    "chat.send",
    "docs.write",
    "notes.write",
    "task.note",
    "task.set_owner",
    "task.set_target",
    "meeting.propose",
    "meeting.act",
}
SIGNAL_EVENT_TYPES = {
    "fact_signal": 0,
    "agenda_signal.observed": 1,
    "commitment.updated": 2,
    "npc.message_sent": 3,
}
INTENT_FAMILY_PRIORITIES = {
    "scope_tradeoff": 0,
    "decision_alignment": 1,
    "approval_request": 2,
    "feasibility_alignment": 3,
    "eta_request": 4,
    "runbook_cleanup": 5,
    "clarification_loop": 6,
    "status_only": 7,
    "read_only": 8,
    "wait": 9,
    "other": 10,
}
NOTE_AUDIT_STATUS_LABELS = {
    "followed_through": "followed through",
    "revisited_only": "revisited only",
    "not_followed_through": "not followed through",
    "unscoped": "unscoped",
}
FIX_HINT_LABELS = {
    "defer approval until preconditions are met": "Defer approval until preconditions are met",
    "consume and incorporate replies before re-asking": "Consume and incorporate replies before re-asking",
    "force the path decision before downstream coordination": "Force the path decision before downstream coordination",
    "convert alignment into explicit commitment immediately": "Convert alignment into explicit commitment immediately",
    "reallocate turns to the gating path": "Reallocate turns to the gating path",
}
FAILURE_CLASS_LABELS = {
    "timing": "Milestone outcomes and timing",
    "discovery": "Discovery",
    "commitment": "Commitment quality",
    "relationship": "Relationship handling",
    "prioritization": "Critical-path prioritization",
}
FAILURE_CLASS_ORDER = {
    "timing": 0,
    "discovery": 1,
    "commitment": 2,
    "relationship": 3,
    "prioritization": 4,
}
RUN_TRACE_FILENAMES = {
    "agent_trace": "benchmark_run.agent_trace.jsonl",
    "omniscient_trace": "benchmark_run.omniscient_trace.jsonl",
}

DIMENSION_DEFINITIONS: list[dict[str, str]] = [
    {
        "id": "discovery_situation_awareness",
        "label": "Discovery & Situation Awareness",
        "kind": "competency",
        "description": "How well the TPM surfaced hidden facts, blockers, and changes in the environment early enough to matter.",
        "counts": "Fact surfacing, blocker discovery, reading the right artifacts, and recognizing changed stakeholder state.",
        "does_not_count": "Lucky outcomes with no evidence of discovery, or verbose artifact churn that never changes what the TPM knows.",
    },
    {
        "id": "critical_path_prioritization",
        "label": "Critical Path Prioritization",
        "kind": "competency",
        "description": "How well the TPM focused effort on the real bottleneck instead of low-leverage coordination or side quests.",
        "counts": "Moving gating tasks, avoiding distraction overinvestment, and sequencing work around the most valuable next step.",
        "does_not_count": "Activity volume, tracker churn, or meetings that do not materially move the critical path.",
    },
    {
        "id": "decision_tradeoff_management",
        "label": "Decision & Tradeoff Management",
        "kind": "competency",
        "description": "How well the TPM drove the right tradeoffs and converged the team on the feasible path under constraint.",
        "counts": "Scope decisions, descoping moves, tradeoff framing, and forcing clarity where the path is ambiguous.",
        "does_not_count": "Status narration without a concrete path decision.",
    },
    {
        "id": "commitment_dependency_management",
        "label": "Commitment & Dependency Management",
        "kind": "competency",
        "description": "How well the TPM turned information into credible commitments and secured the dependency edges needed to execute.",
        "counts": "Approval requests at the right moment, feasible ETA commitments, dependency handling, and avoiding invalid promises.",
        "does_not_count": "A promise that exists only in prose, or a target date with no supporting commitment.",
    },
    {
        "id": "stakeholder_alignment_communication",
        "label": "Stakeholder Alignment & Communication",
        "kind": "competency",
        "description": "How well the TPM kept key stakeholders on the same story and used communication to reduce misalignment.",
        "counts": "Getting the right actors to share the right belief state, explicit decision communication, and coordination that changes shared understanding.",
        "does_not_count": "A large number of messages without belief convergence.",
    },
    {
        "id": "escalation_influence",
        "label": "Escalation & Influence",
        "kind": "competency",
        "description": "How well the TPM used escalation and influence to unblock work without burning trust or escalating prematurely.",
        "counts": "Escalating when normal coordination is insufficient and using authority channels at the right time.",
        "does_not_count": "Escalation spam, repeated pings, or authority requests before preconditions are ready.",
    },
    {
        "id": "outcome_attainment",
        "label": "Outcome Attainment",
        "kind": "outcome",
        "description": "Whether the TPM actually moved the project to the intended milestone outcomes in this scenario.",
        "counts": "Scenario-defined milestone completion and outcome-bearing commitments.",
        "does_not_count": "Process motion that never changes the final project state.",
    },
    {
        "id": "timing_optionality_preservation",
        "label": "Timing / Optionality Preservation",
        "kind": "outcome",
        "description": "Whether the TPM acted soon enough to preserve leverage windows, recoverability, and credible alternatives.",
        "counts": "Beating scenario deadlines, acting before cutoffs, and avoiding the point where the project becomes unrecoverable.",
        "does_not_count": "Eventually doing the right thing after the useful window has already closed.",
    },
]

DIMENSION_BY_ID = {item["id"]: item for item in DIMENSION_DEFINITIONS}
COMPETENCY_IDS = [item["id"] for item in DIMENSION_DEFINITIONS if item["kind"] == "competency"]
OUTCOME_IDS = [item["id"] for item in DIMENSION_DEFINITIONS if item["kind"] == "outcome"]


def export_run_summary(
    run_dir: str | Path,
    *,
    judge_client: Any | None = None,
    judge_model: str | None = None,
    write_files: bool = True,
) -> dict[str, Any]:
    run_path = Path(run_dir)
    payload = json.loads((run_path / "agent_run.json").read_text())
    run_record = payload["run"]
    report_path = resolve_run_artifact_path(run_path, run_record.get("report_path"), default_name="benchmark_run.report.json")
    report = json.loads(report_path.read_text())
    original_trace_paths = dict(report.get("trace_paths", {}))
    trace_paths = dict(original_trace_paths)
    for trace_key, default_name in RUN_TRACE_FILENAMES.items():
        resolved_trace = maybe_resolve_run_artifact_path(run_path, trace_paths.get(trace_key), default_name=default_name)
        if resolved_trace is not None:
            trace_paths[trace_key] = str(resolved_trace)
    report["trace_paths"] = trace_paths
    run_record["output_dir"] = str(run_path)
    run_record["report_path"] = str(report_path)
    run_record["agent_log_path"] = str(run_path / "agent_run.json")
    payload["run"] = run_record
    scenario_bundle = load_scenario_bundle(run_record["scenario_id"])
    summary = build_run_summary(
        report,
        agent_payload=payload,
        scenario_bundle=scenario_bundle,
        judge_client=judge_client,
        judge_model=judge_model,
    )
    if write_files:
        if trace_paths != original_trace_paths:
            report_path.write_text(json.dumps(report, indent=2, sort_keys=True))
        summary_json = run_path / "tpm_performance_summary.json"
        summary_md = run_path / "tpm_performance_summary.md"
        judge_input_json = run_path / "judge_input_bundle.json"
        judge_output_json = run_path / "judge_output.json"
        summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True))
        summary_md.write_text(render_run_summary(summary))
        judge_input_json.write_text(json.dumps(summary["judge_input_bundle"], indent=2, sort_keys=True))
        run_record.pop("judge_output_path", None)
        if summary["narrative"].get("source") == "llm_judge":
            judge_output_json.write_text(json.dumps(summary["narrative"], indent=2, sort_keys=True))
            run_record["judge_output_path"] = str(judge_output_json)
        elif judge_output_json.exists():
            judge_output_json.unlink()
        run_record["summary_path"] = str(summary_json)
        run_record["summary_markdown_path"] = str(summary_md)
        run_record["judge_input_path"] = str(judge_input_json)
        payload["run"] = run_record
        (run_path / "agent_run.json").write_text(json.dumps(payload, indent=2, sort_keys=True))
    return summary


def maybe_resolve_run_artifact_path(
    run_dir: str | Path,
    recorded_path: str | Path | None,
    *,
    default_name: str | None = None,
) -> Path | None:
    run_path = Path(run_dir)
    candidates: list[Path] = []
    if recorded_path:
        recorded = Path(recorded_path)
        candidates.append(recorded)
        candidates.append(run_path / recorded.name)
    if default_name:
        candidates.append(run_path / default_name)
    seen: set[str] = set()
    for candidate in candidates:
        candidate_key = str(candidate)
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        if candidate.exists():
            return candidate
    return None


def resolve_run_artifact_path(
    run_dir: str | Path,
    recorded_path: str | Path | None,
    *,
    default_name: str | None = None,
) -> Path:
    resolved = maybe_resolve_run_artifact_path(run_dir, recorded_path, default_name=default_name)
    if resolved is not None:
        return resolved
    target = recorded_path or default_name or "<unknown>"
    raise FileNotFoundError(f"Run artifact not found under {run_dir}: {target}")


def export_bundle_summary(
    bundle_dir: str | Path,
    run_summaries: list[dict[str, Any]],
    *,
    scenario_id: str,
    model: str,
    seed_bundle: list[int],
    write_files: bool = True,
) -> dict[str, Any]:
    summary = build_bundle_summary(run_summaries, scenario_id=scenario_id, model=model, seed_bundle=seed_bundle)
    if write_files:
        bundle_path = Path(bundle_dir)
        bundle_path.mkdir(parents=True, exist_ok=True)
        json_path = bundle_path / "bundle_performance_summary.json"
        md_path = bundle_path / "bundle_performance_summary.md"
        json_path.write_text(json.dumps(summary, indent=2, sort_keys=True))
        md_path.write_text(render_bundle_summary(summary))
    return summary


def summarize_existing_run(run_dir: str | Path, *, judge_client: Any | None = None, judge_model: str | None = None) -> dict[str, Any]:
    return export_run_summary(run_dir, judge_client=judge_client, judge_model=judge_model, write_files=True)


def summarize_existing_bundle(bundle_dir: str | Path) -> dict[str, Any]:
    bundle_path = Path(bundle_dir)
    run_summaries: list[dict[str, Any]] = []
    scenario_id = None
    model = None
    seed_bundle: list[int] = []
    for run_json in sorted(bundle_path.glob("seed*/tpm_performance_summary.json")):
        summary = json.loads(run_json.read_text())
        run_summaries.append(summary)
        header = summary["run_header"]
        scenario_id = scenario_id or header["scenario_id"]
        model = model or header.get("model")
        seed_bundle.append(int(header["seed"]))
    if not run_summaries:
        raise RuntimeError(f"No per-seed run summaries found under {bundle_dir}.")
    return export_bundle_summary(bundle_dir, run_summaries, scenario_id=scenario_id, model=model or "unknown", seed_bundle=seed_bundle, write_files=True)


def build_run_summary(
    report: dict[str, Any],
    *,
    agent_payload: dict[str, Any] | None = None,
    scenario_bundle: dict[str, Any] | None = None,
    judge_client: Any | None = None,
    judge_model: str | None = None,
) -> dict[str, Any]:
    scenario_bundle = scenario_bundle or load_scenario_bundle(report["scenario_id"])
    scenario = scenario_bundle["scenario"]
    run_record = (agent_payload or {}).get("run", {})
    termination_reason = _normalized_termination_reason(run_record)
    simulated_end_time = run_record.get("simulated_end_time", report["time"])
    omniscient_trace = _load_trace_rows(report.get("trace_paths", {}).get("omniscient_trace"))
    agent_trace = _load_trace_rows(report.get("trace_paths", {}).get("agent_trace"))
    visible_trace = _select_visible_trace(agent_trace, omniscient_trace)
    action_log_rows = _load_action_rows(run_record)
    message_rows = _load_message_rows(run_record)
    document_rows = _load_document_rows(run_record)
    merged_action_rows = _merge_decision_action_rows(agent_payload, action_log_rows)
    diagnostics = build_behavior_diagnostics(merged_action_rows, omniscient_trace, scenario, action_log_rows=action_log_rows)
    simulated_minutes_elapsed = _simulated_minutes_elapsed(scenario["start_at"], simulated_end_time)
    turns_taken = int(run_record.get("turns_taken") or 0)
    simulated_minutes_per_turn = round(simulated_minutes_elapsed / turns_taken, 2) if turns_taken else 0.0
    score_breakdown = _build_score_breakdown(report["rubric"])
    dimension_scores = _build_dimension_scores(report["rubric"])
    competency_profile = [
        dimension_scores[item_id]
        for item_id in COMPETENCY_IDS
        if item_id in dimension_scores and float(dimension_scores[item_id]["weight"]) > 0
    ]
    outcome_profile = [
        dimension_scores[item_id]
        for item_id in OUTCOME_IDS
        if item_id in dimension_scores and float(dimension_scores[item_id]["weight"]) > 0
    ]
    critical_path = _build_critical_path_result(report["rubric"], outcome_profile)
    run_health = _build_run_health(report, run_record, diagnostics)
    outcome_verdict = _outcome_verdict(critical_path, dimension_scores, run_health)
    rubric_failure_dossiers = _build_failure_dossiers(
        report["rubric"],
        report=report,
        scenario=scenario,
        run_record=run_record,
        run_health=run_health,
        diagnostics=diagnostics,
        critical_path=critical_path,
        agent_trace=visible_trace,
        merged_action_rows=merged_action_rows,
    )
    signal_coverage = _build_signal_coverage(scenario, visible_trace, merged_action_rows)
    stakeholder_engagement = _build_stakeholder_engagement(scenario, message_rows, merged_action_rows, visible_trace)
    window_scorecards = _build_window_scorecards(report, scenario, report["rubric"], merged_action_rows)
    missed_opportunities = _build_missed_opportunities(
        scenario,
        visible_trace,
        message_rows,
        merged_action_rows,
        stakeholder_engagement,
        signal_coverage,
        window_scorecards,
    )
    reference_path_diff = _build_reference_path_diff(report["scenario_id"], scenario, merged_action_rows)
    root_cause_findings = _build_root_cause_findings(
        report["rubric"],
        rubric_failure_dossiers=rubric_failure_dossiers,
        diagnostics=diagnostics,
        signal_coverage=signal_coverage,
        stakeholder_engagement=stakeholder_engagement,
        window_scorecards=window_scorecards,
        missed_opportunities=missed_opportunities,
        reference_path_diff=reference_path_diff,
        merged_action_rows=merged_action_rows,
    )
    capability_assessment = _build_capability_assessment(
        report,
        outcome_verdict=outcome_verdict,
        critical_path=critical_path,
        root_cause_findings=root_cause_findings,
        stakeholder_engagement=stakeholder_engagement,
        signal_coverage=signal_coverage,
        window_scorecards=window_scorecards,
    )
    key_successes = _key_successes(report["rubric"], dimension_scores)
    key_failures = _project_key_failures(root_cause_findings, rubric_failure_dossiers=rubric_failure_dossiers)
    improvements = _project_improvement_opportunities(root_cause_findings, rubric_failure_dossiers=rubric_failure_dossiers)
    decisive_timeline = _decisive_timeline(report, visible_trace, report["rubric"])
    summary = {
        "schema_version": PERFORMANCE_SUMMARY_VERSION,
        "run_header": {
            "scenario_id": report["scenario_id"],
            "scenario_digest": report["scenario_digest"],
            "compiled_coverage_digest": report.get("compiled_coverage_digest", report["scenario_digest"]),
            "validation_status": report.get("validation_status", {"status": "unknown", "passed": False, "fresh": False}),
            "closure_status": report.get("closure_status", {"status": "unknown", "passed": False, "fresh": False}),
            "seed": run_record.get("seed"),
            "adapter": run_record.get("adapter"),
            "model": run_record.get("model"),
            "prompt_pack_version": run_record.get("prompt_pack_version"),
            "time": report["time"],
            "score": report["total_score"],
            "score_possible": score_breakdown["total_possible"],
            "score_percent": score_breakdown["score_percent"],
            "turns_taken": run_record.get("turns_taken"),
            "max_turns": run_record.get("max_turns"),
            "termination_reason": termination_reason,
            "simulated_end_time": simulated_end_time,
            "simulated_minutes_elapsed": simulated_minutes_elapsed,
            "simulated_minutes_per_turn": simulated_minutes_per_turn,
            "report_path": run_record.get("report_path"),
            "agent_log_path": run_record.get("agent_log_path"),
        },
        "scenario_context": {
            "title": scenario["world"]["project"]["name"],
            "summary": scenario["world"]["project"]["description"],
            "primary_failure_classes": scenario["evaluation"].get("primary_failure_classes", []),
            "competency_model_version": COMPETENCY_MODEL_VERSION,
        },
        "score_breakdown": score_breakdown,
        "capability_assessment": capability_assessment,
        "outcome_verdict": outcome_verdict,
        "critical_path_result": critical_path,
        "root_cause_findings": root_cause_findings,
        "stakeholder_engagement": stakeholder_engagement,
        "signal_coverage": signal_coverage,
        "window_scorecards": window_scorecards,
        "missed_opportunities": missed_opportunities,
        "reference_path_diff": reference_path_diff,
        "evidence_catalog": [],
        "rubric_failure_appendix": rubric_failure_dossiers,
        "failure_dossiers": rubric_failure_dossiers,
        "tpm_competency_profile": competency_profile,
        "outcome_profile": outcome_profile,
        "decisive_timeline": decisive_timeline,
        "key_successes": key_successes,
        "key_failures": key_failures,
        "improvement_opportunities": improvements,
        "run_health": run_health,
        "narrative": _deterministic_narrative(
            capability_assessment,
            root_cause_findings,
            stakeholder_engagement,
            signal_coverage,
            window_scorecards,
            reference_path_diff,
            key_successes,
        ),
        "evidence_appendix": {
            "competency_definitions": DIMENSION_DEFINITIONS,
            "rubric_lines": report["rubric"],
        },
        "raw_scoring_appendix": {
            "total_score": report["total_score"],
            "legacy_failure_breakdown": report.get("failure_breakdown", {}),
            "recoverability": report.get("recoverability", {}),
            "coverage_miss": report.get("coverage_miss", False),
            "trace_paths": report.get("trace_paths", {}),
            "private_note_audit_notes": diagnostics.get("private_note_audit_rows", []),
        },
    }
    summary["evidence_catalog"] = _build_evidence_catalog(
        summary,
        visible_trace=visible_trace,
        omniscient_trace=omniscient_trace,
        merged_action_rows=merged_action_rows,
        message_rows=message_rows,
        document_rows=document_rows,
    )
    summary["judge_input_bundle"] = _build_judge_input_bundle(
        summary,
        diagnostics,
        decisive_timeline,
        omniscient_trace,
        visible_trace,
        merged_action_rows,
    )
    summary["narrative"] = _maybe_apply_judge(summary, judge_client=judge_client, judge_model=judge_model)
    return summary


def build_bundle_summary(
    run_summaries: list[dict[str, Any]],
    *,
    scenario_id: str,
    model: str,
    seed_bundle: list[int],
) -> dict[str, Any]:
    scores = [float(item["run_header"]["score"]) for item in run_summaries]
    run_rows = [_build_bundle_run_row(run) for run in run_summaries]
    score_possible = next(
        (
            float(run.get("score_breakdown", {}).get("total_possible"))
            for run in run_summaries
            if run.get("score_breakdown", {}).get("total_possible") is not None
        ),
        100.0 if run_summaries else 0.0,
    )
    competency_profile = []
    for dimension_id in COMPETENCY_IDS + OUTCOME_IDS:
        rows = []
        for run in run_summaries:
            for item in run.get("tpm_competency_profile", []) + run.get("outcome_profile", []):
                if item["id"] == dimension_id:
                    rows.append(item)
                    break
        if not rows:
            continue
        competency_profile.append(
            {
                "id": dimension_id,
                "label": DIMENSION_BY_ID[dimension_id]["label"],
                "mean_score": round(mean([float(item["score"]) for item in rows]), 2),
                "worst_score": round(min(float(item["score"]) for item in rows), 2),
                "best_score": round(max(float(item["score"]) for item in rows), 2),
                "spread": round(max(float(item["score"]) for item in rows) - min(float(item["score"]) for item in rows), 2),
                "stdev": round(pstdev([float(item["score"]) for item in rows]), 3) if len(rows) > 1 else 0.0,
                "band": _band_from_score(mean([float(item["score"]) for item in rows])),
            }
        )
    recurring_failures = Counter()
    recurring_health = Counter()
    recurring_root_causes: dict[str, dict[str, Any]] = {}
    recurring_failure_details: dict[str, dict[str, Any]] = {}
    stakeholder_patterns: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "name": None,
            "never_contacted": 0,
            "after_deadline": 0,
            "unanswered_questions": 0,
            "seeds_never_contacted": [],
            "seeds_after_deadline": [],
            "seeds_with_unanswered_questions": [],
        }
    )
    signal_consistency: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "criticality": "supporting",
            "kind": "fact",
            "label": None,
            "surfaced": 0,
            "converted": 0,
            "runs": 0,
            "seeds_surfaced": [],
            "seeds_converted": [],
        }
    )
    private_note_counts = Counter()
    runs_with_any_notes = 0
    runs_with_followed_through_notes = 0
    reference_patterns = Counter()
    window_miss_recurrence = Counter()
    window_titles: dict[str, str] = {}
    window_miss_seeds: dict[str, list[int]] = defaultdict(list)
    for run in run_summaries:
        seed = int(run["run_header"]["seed"])
        for item in run.get("key_failures", []):
            recurring_failures[item["id"]] += 1
            recurring_failure_details.setdefault(
                item["id"],
                {
                    "title": item.get("title") or item["id"],
                    "summary": item.get("summary"),
                },
            )
        for item in run.get("run_health", {}).get("harness_interface_issues", []):
            recurring_health[item] += 1
        for item in run.get("run_health", {}).get("scenario_authoring_issues", []):
            recurring_health[item] += 1
        for finding in run.get("root_cause_findings", []):
            entry = recurring_root_causes.setdefault(
                finding["id"],
                {
                    "id": finding["id"],
                    "title": finding["title"],
                    "count": 0,
                    "mean_lost_points": 0.0,
                    "total_lost_points": 0.0,
                    "seeds": [],
                },
            )
            entry["count"] += 1
            entry["total_lost_points"] += float(finding.get("lost_points_total", 0.0))
            entry["seeds"].append(seed)
        metrics = run.get("stakeholder_engagement", {}).get("summary_metrics", {})
        stakeholder_rows = run.get("stakeholder_engagement", {}).get("actors", [])
        stakeholder_name_by_id = {
            str(actor.get("actor_id")): actor.get("name")
            for actor in stakeholder_rows
            if isinstance(actor, dict) and actor.get("actor_id")
        }
        for actor_id in metrics.get("critical_actors_never_contacted", []):
            entry = stakeholder_patterns[str(actor_id)]
            entry["name"] = entry["name"] or stakeholder_name_by_id.get(str(actor_id))
            entry["never_contacted"] += 1
            entry["seeds_never_contacted"].append(seed)
        for actor_id in metrics.get("critical_actors_contacted_after_deadline", []):
            entry = stakeholder_patterns[str(actor_id)]
            entry["name"] = entry["name"] or stakeholder_name_by_id.get(str(actor_id))
            entry["after_deadline"] += 1
            entry["seeds_after_deadline"].append(seed)
        for actor in stakeholder_rows:
            unanswered = len(actor.get("unanswered_direct_questions", []))
            if unanswered:
                entry = stakeholder_patterns[str(actor["actor_id"])]
                entry["name"] = entry["name"] or actor.get("name")
                entry["unanswered_questions"] += unanswered
                entry["seeds_with_unanswered_questions"].append(seed)
        for signal in run.get("signal_coverage", {}).get("signals", []):
            signal_id = str(signal["signal_id"])
            signal_consistency[signal_id]["criticality"] = signal.get("criticality", "supporting")
            signal_consistency[signal_id]["kind"] = signal.get("kind", "fact")
            signal_consistency[signal_id]["label"] = signal.get("label", signal_id)
            signal_consistency[signal_id]["runs"] += 1
            if signal.get("surfaced"):
                signal_consistency[signal_id]["surfaced"] += 1
                signal_consistency[signal_id]["seeds_surfaced"].append(seed)
            if signal.get("converted_to_plan_change"):
                signal_consistency[signal_id]["converted"] += 1
                signal_consistency[signal_id]["seeds_converted"].append(seed)
        note_audit = run.get("run_health", {}).get("behavior_diagnostics", {}).get("private_note_audit", {})
        total_notes_written = int(note_audit.get("total_notes_written") or 0)
        followed_through = int(note_audit.get("followed_through") or 0)
        if total_notes_written:
            runs_with_any_notes += 1
        if followed_through:
            runs_with_followed_through_notes += 1
        for key in (
            "total_notes_written",
            "structured_notes_written",
            "followed_through",
            "revisited_only",
            "not_followed_through",
            "unscoped_notes",
        ):
            private_note_counts[key] += int(note_audit.get(key) or 0)
        reference = run.get("reference_path_diff")
        if reference and reference.get("expected_step"):
            reference_patterns[f"{reference.get('expected_step')} -> {reference.get('actual_step')}"] += 1
        for window in run.get("window_scorecards", []):
            if not window.get("state_achieved", {}).get("achieved"):
                window_miss_recurrence[str(window["window_id"])] += 1
                window_titles[str(window["window_id"])] = str(window.get("title") or window["window_id"])
                window_miss_seeds[str(window["window_id"])].append(seed)
    for entry in recurring_root_causes.values():
        count = max(1, int(entry["count"]))
        entry["mean_lost_points"] = round(float(entry["total_lost_points"]) / count, 2)
        entry["share_of_runs"] = round(count / len(run_summaries), 3) if run_summaries else 0.0
        entry["seeds"] = _sorted_seed_list(entry.get("seeds", []))
        del entry["total_lost_points"]
    score_stdev = round(pstdev(scores), 3) if len(scores) > 1 else 0.0
    clean_bundle = not recurring_health and not any(run.get("run_health", {}).get("protocol_failure") for run in run_summaries)
    confidence_scope = "multi_seed_supported" if len(run_summaries) > 1 and clean_bundle and score_stdev < 15 else "single_run_directional"
    mean_score = round(mean(scores), 2) if scores else 0.0
    if mean_score >= 75:
        aggregate_rating = "strong"
    elif mean_score >= 45:
        aggregate_rating = "mixed"
    else:
        aggregate_rating = "poor"
    aggregate = {
        "schema_version": BUNDLE_PERFORMANCE_SUMMARY_VERSION,
        "bundle_header": {
            "scenario_id": scenario_id,
            "model": model,
            "seed_bundle": seed_bundle,
            "seed_count": len(seed_bundle),
        },
        "headline": {
            "mean_score": mean_score,
            "worst_score": round(min(scores), 2) if scores else 0.0,
            "best_score": round(max(scores), 2) if scores else 0.0,
            "stdev": score_stdev,
            "score_possible": score_possible,
        },
        "aggregate_capability_assessment": {
            "rating": aggregate_rating,
            "direct_answer": (
                "Across the seed bundle, this model looks strong as a TPM in this scenario."
                if aggregate_rating == "strong"
                else "Across the seed bundle, this model shows mixed TPM performance in this scenario."
                if aggregate_rating == "mixed"
                else "Across the seed bundle, this model performs poorly as a TPM in this scenario."
            ),
            "confidence_scope": confidence_scope,
        },
        "aggregate_competency_profile": competency_profile,
        "seed_consistency": {
            "protocol_failures": sum(1 for run in run_summaries if run.get("run_health", {}).get("protocol_failure")),
            "coverage_misses": sum(1 for run in run_summaries if run.get("run_health", {}).get("coverage_miss")),
            "score_variance_ok": score_stdev < 15,
        },
        "recurring_root_causes": sorted(
            recurring_root_causes.values(),
            key=lambda item: (int(item["count"]), float(item["mean_lost_points"])),
            reverse=True,
        )[:6],
        "stakeholder_failure_patterns": [
            {
                "actor_id": actor_id,
                "name": counts.get("name"),
                "never_contacted": counts["never_contacted"],
                "after_deadline": counts["after_deadline"],
                "unanswered_questions": counts["unanswered_questions"],
                "seeds_never_contacted": _sorted_seed_list(counts.get("seeds_never_contacted", [])),
                "seeds_after_deadline": _sorted_seed_list(counts.get("seeds_after_deadline", [])),
                "seeds_with_unanswered_questions": _sorted_seed_list(counts.get("seeds_with_unanswered_questions", [])),
                "runs_affected": len(
                    set(
                        _sorted_seed_list(
                            (counts.get("seeds_never_contacted", []))
                            + (counts.get("seeds_after_deadline", []))
                            + (counts.get("seeds_with_unanswered_questions", []))
                        )
                    )
                ),
            }
            for actor_id, counts in sorted(
                stakeholder_patterns.items(),
                key=lambda item: (item[1]["never_contacted"] + item[1]["after_deadline"] + item[1]["unanswered_questions"], item[0]),
                reverse=True,
            )
            if (counts["never_contacted"] + counts["after_deadline"] + counts["unanswered_questions"]) > 0
        ],
        "signal_coverage_consistency": [
            {
                "signal_id": signal_id,
                "label": row.get("label") or signal_id,
                "criticality": row["criticality"],
                "kind": row.get("kind", "fact"),
                "surfaced_rate": round(row["surfaced"] / row["runs"], 3) if row["runs"] else 0.0,
                "converted_rate": round(row["converted"] / row["runs"], 3) if row["runs"] else 0.0,
                "runs": row["runs"],
                "seeds_surfaced": _sorted_seed_list(row.get("seeds_surfaced", [])),
                "seeds_converted": _sorted_seed_list(row.get("seeds_converted", [])),
            }
            for signal_id, row in sorted(signal_consistency.items(), key=lambda item: (item[1]["criticality"] != "critical", item[0]))
        ],
        "driver_signal_consistency": [
            {
                "signal_id": signal_id,
                "label": row.get("label") or signal_id,
                "criticality": row["criticality"],
                "kind": row.get("kind", "fact"),
                "surfaced_rate": round(row["surfaced"] / row["runs"], 3) if row["runs"] else 0.0,
                "converted_rate": round(row["converted"] / row["runs"], 3) if row["runs"] else 0.0,
                "runs": row["runs"],
                "seeds_surfaced": _sorted_seed_list(row.get("seeds_surfaced", [])),
                "seeds_converted": _sorted_seed_list(row.get("seeds_converted", [])),
            }
            for signal_id, row in sorted(signal_consistency.items(), key=lambda item: (item[1]["criticality"] != "critical", item[0]))
            if row.get("kind") == "driver"
        ],
        "private_note_audit_aggregate": {
            key: int(private_note_counts.get(key) or 0)
            for key in (
                "total_notes_written",
                "structured_notes_written",
                "followed_through",
                "revisited_only",
                "not_followed_through",
                "unscoped_notes",
            )
        }
        | {
            "runs_with_any_notes": runs_with_any_notes,
            "runs_with_followed_through_notes": runs_with_followed_through_notes,
            "mean_notes_written": round((private_note_counts["total_notes_written"] / len(run_summaries)), 2)
            if run_summaries
            else 0.0,
            "mean_followed_through": round((private_note_counts["followed_through"] / len(run_summaries)), 2)
            if run_summaries
            else 0.0,
        },
        "reference_divergence_patterns": [
            {"pattern": pattern, "count": count}
            for pattern, count in reference_patterns.most_common(5)
        ],
        "window_miss_recurrence": [
            {
                "window_id": window_id,
                "title": window_titles.get(window_id, window_id),
                "count": count,
                "seeds": _sorted_seed_list(window_miss_seeds.get(window_id, [])),
            }
            for window_id, count in window_miss_recurrence.most_common(6)
        ],
        "confidence_scope": confidence_scope,
        "top_recurring_failure_themes": [
            {
                "id": key,
                "title": recurring_failure_details.get(key, {}).get("title", key),
                "summary": recurring_failure_details.get(key, {}).get("summary"),
                "count": count,
            }
            for key, count in recurring_failures.most_common(5)
        ],
        "harness_health": {
            "status": "clean" if not recurring_health else "attention_needed",
            "issues": [{"issue": key, "count": count} for key, count in recurring_health.most_common(5)],
        },
        "runs": run_rows,
    }
    aggregate["dimension_highlights"] = _build_bundle_dimension_highlights(aggregate["aggregate_competency_profile"])
    aggregate["narrative"] = _deterministic_bundle_narrative(aggregate)
    return aggregate


def _sorted_seed_list(values: Iterable[Any]) -> list[int]:
    if not values:
        return []
    seeds: list[int] = []
    for value in values:
        try:
            seeds.append(int(value))
        except (TypeError, ValueError):
            continue
    return sorted(set(seeds))


def _build_bundle_run_row(run: dict[str, Any]) -> dict[str, Any]:
    run_header = run.get("run_header", {})
    score_breakdown = run.get("score_breakdown", {})
    run_health = run.get("run_health", {})
    signal_rows = run.get("signal_coverage", {}).get("signals", [])
    stakeholder_metrics = run.get("stakeholder_engagement", {}).get("summary_metrics", {})
    critical_rows = [row for row in signal_rows if row.get("criticality") == "critical"]
    critical_observed = [row for row in critical_rows if row.get("surfaced")]
    critical_converted = [row for row in critical_observed if row.get("converted_to_plan_change")]
    driver_rows = [row for row in signal_rows if row.get("kind") == "driver"]
    driver_observed = [row for row in driver_rows if row.get("surfaced")]
    driver_converted = [row for row in driver_observed if row.get("converted_to_plan_change")]
    windows = run.get("window_scorecards", [])
    windows_hit = sum(1 for row in windows if row.get("state_achieved", {}).get("achieved"))
    top_root_cause = next((item for item in run.get("root_cause_findings", []) if isinstance(item, dict)), None)
    top_failure_theme = next((item for item in run.get("key_failures", []) if isinstance(item, dict)), None)
    score_value = float(run_header.get("score") or 0.0)
    score_possible = float(score_breakdown.get("total_possible") or run_header.get("score_possible") or 0.0)
    score_percent = score_breakdown.get("score_percent", run_header.get("score_percent"))
    if score_percent is None and score_possible > 0:
        score_percent = round((score_value / score_possible) * 100, 1)
    return {
        "seed": run_header.get("seed"),
        "score": score_value,
        "score_possible": score_possible,
        "score_percent": float(score_percent or 0.0),
        "outcome_verdict": run.get("outcome_verdict", {}).get("headline"),
        "capability_rating": run.get("capability_assessment", {}).get("rating"),
        "critical_path_status": run.get("critical_path_result", {}).get("status"),
        "critical_signals_observed": len(critical_observed),
        "critical_signals_total": len(critical_rows),
        "critical_signals_converted": len(critical_converted),
        "critical_signals_unsurfaced": [str(row["signal_id"]) for row in critical_rows if not row.get("surfaced")],
        "critical_signals_observed_not_converted": [
            str(row["signal_id"]) for row in critical_observed if not row.get("converted_to_plan_change")
        ],
        "driver_signals_observed": len(driver_observed),
        "driver_signals_total": len(driver_rows),
        "driver_signals_converted": len(driver_converted),
        "critical_actors_never_contacted": [str(item) for item in stakeholder_metrics.get("critical_actors_never_contacted", [])],
        "critical_actors_contacted_after_deadline": [
            str(item) for item in stakeholder_metrics.get("critical_actors_contacted_after_deadline", [])
        ],
        "unanswered_direct_questions": len(stakeholder_metrics.get("direct_questions_left_unanswered", [])),
        "windows_hit": windows_hit,
        "windows_total": len(windows),
        "missed_windows": [
            str(row.get("title") or row.get("window_id"))
            for row in windows
            if not row.get("state_achieved", {}).get("achieved")
        ],
        "top_root_cause": (
            {
                "id": top_root_cause.get("id"),
                "title": top_root_cause.get("title") or top_root_cause.get("id"),
                "lost_points": float(top_root_cause.get("lost_points_total", top_root_cause.get("lost_points", 0.0)) or 0.0),
            }
            if top_root_cause
            else None
        ),
        "top_failure_theme": (
            {
                "id": top_failure_theme.get("id"),
                "title": top_failure_theme.get("title") or top_failure_theme.get("id"),
                "summary": top_failure_theme.get("summary"),
            }
            if top_failure_theme
            else None
        ),
        "overall_status": run_health.get(
            "overall_status",
            "attention_needed" if run_health.get("protocol_failure") or run_health.get("coverage_miss") else "clean",
        ),
        "model_status": run_health.get("model_status"),
        "harness_status": run_health.get(
            "harness_status",
            "attention_needed" if run_health.get("protocol_failure") or run_health.get("coverage_miss") else "clean",
        ),
        "summary_path": run_header.get("summary_path"),
    }


def _build_bundle_dimension_highlights(profile: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    stable_strengths = [
        item
        for item in sorted(profile, key=lambda row: (float(row["mean_score"]), -float(row["stdev"])), reverse=True)
        if float(item["mean_score"]) >= 75 and float(item["stdev"]) <= 10
    ][:3]
    stable_weaknesses = [
        item
        for item in sorted(profile, key=lambda row: (float(row["mean_score"]), -float(row["stdev"])))
        if float(item["mean_score"]) < 45 and float(item["stdev"]) <= 10
    ][:3]
    seed_sensitive = [
        item
        for item in sorted(profile, key=lambda row: (float(row["spread"]), float(row["stdev"])), reverse=True)
        if float(item["spread"]) >= 20 or float(item["stdev"]) >= 10
    ][:3]
    return {
        "stable_strengths": stable_strengths,
        "stable_weaknesses": stable_weaknesses,
        "seed_sensitive": seed_sensitive,
    }


def _render_metric_number(value: Any) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if numeric.is_integer():
        return str(int(numeric))
    return f"{numeric:.2f}".rstrip("0").rstrip(".")


def _render_iso_datetime(value: str | None) -> str:
    if not value:
        return "unknown"
    try:
        return from_iso(value).strftime("%a %Y-%m-%d %H:%M")
    except Exception:
        return str(value)


def _minutes_between(start_at: str | None, end_at: str | None) -> int | None:
    if not start_at or not end_at:
        return None
    try:
        delta = from_iso(end_at) - from_iso(start_at)
    except Exception:
        return None
    return int(delta.total_seconds() // 60)


def _render_confidence_scope(scope: str | None) -> str:
    if scope == "multi_seed_supported":
        return "Higher confidence. This conclusion is supported by multiple seeds with clean harness health and bounded variance."
    return "Low confidence. This is a single-seed directional readout, so treat it as evidence about this run rather than a stable model-level conclusion."


def _render_outbound_counts(outbound_by_actor: dict[str, Any]) -> str:
    if not outbound_by_actor:
        return "none"
    ordered = sorted(outbound_by_actor.items(), key=lambda item: (-int(item[1]), item[0]))
    return ", ".join(f"{actor} {count}" for actor, count in ordered)


def _render_signal_name(row: dict[str, Any]) -> str:
    signal_id = str(row.get("signal_id") or "signal")
    label = str(row.get("label") or signal_id)
    if label == signal_id:
        return signal_id
    return f"{label} [{signal_id}]"


def _render_note_status(note: dict[str, Any]) -> str:
    status = str(note.get("status") or "unknown")
    details = [NOTE_AUDIT_STATUS_LABELS.get(status, status.replace("_", " "))]
    first_non_read_touch = note.get("first_non_read_touch_action_ref")
    first_touch = note.get("first_touch_action_ref")
    reread = note.get("reread_note_action_ref")
    if first_non_read_touch:
        details.append(f"influenced later work via {first_non_read_touch}")
    elif first_touch:
        details.append(f"revisited via {first_touch}")
    elif reread:
        details.append(f"re-read via {reread}")
    refs = [str(ref) for ref in note.get("refs") or [] if ref]
    if refs:
        details.append(f"refs: {', '.join(refs)}")
    return "; ".join(details)


def render_run_summary(summary: dict[str, Any]) -> str:
    capability = summary.get("capability_assessment", {})
    score_breakdown = summary.get("score_breakdown", {})
    findings = summary.get("root_cause_findings", [])[:4]
    stakeholder = summary.get("stakeholder_engagement", {})
    stakeholder_rows = stakeholder.get("actors", [])
    stakeholder_metrics = stakeholder.get("summary_metrics", {})
    windows = summary.get("window_scorecards", [])
    signals = summary.get("signal_coverage", {}).get("signals", [])
    run_health = summary.get("run_health", {})
    note_audit = run_health.get("behavior_diagnostics", {}).get("private_note_audit", {})
    private_note_rows = summary.get("raw_scoring_appendix", {}).get("private_note_audit_notes", [])
    narrative = summary.get("narrative", {})
    reference_path_diff = summary.get("reference_path_diff") or {}
    total_possible = score_breakdown.get("total_possible", summary["run_header"].get("score_possible", 0.0))
    total_unearned = score_breakdown.get("total_unearned", max(0.0, float(total_possible or 0.0) - float(summary["run_header"]["score"])))
    direct_answer = narrative.get("direct_answer") or capability.get("direct_answer", summary["outcome_verdict"]["headline"])
    lines = [
        f"Scenario: {summary['run_header']['scenario_id']}",
        f"Seed: {summary['run_header'].get('seed')}",
        f"Model: {summary['run_header'].get('model')}",
        (
            f"Score: {_render_metric_number(summary['run_header']['score'])} / {_render_metric_number(total_possible)} "
            f"({_render_metric_number(score_breakdown.get('score_percent', summary['run_header'].get('score_percent', 0.0)))}%)"
        ),
        "",
        f"Capability verdict: {capability.get('headline', summary['outcome_verdict']['headline'])}",
        f"Direct answer: {direct_answer}",
        "",
        "Score breakdown:",
    ]
    if score_breakdown:
        lines.append(
            f"- This benchmark uses {_render_metric_number(total_possible)} rubric points. This run earned {_render_metric_number(score_breakdown.get('total_awarded', summary['run_header']['score']))} and left {_render_metric_number(total_unearned)} unearned."
        )
        for group in score_breakdown.get("groups", []):
            lines.append(
                f"- {group['label']}: {_render_metric_number(group['awarded'])} / {_render_metric_number(group['weight'])}"
            )
    lines.extend(["", "Top root-cause findings:"])
    if findings:
        lines.append(
            f"- Each finding below shows how many of the run's {_render_metric_number(total_unearned)} unearned points it helps explain. These totals overlap and should not be summed."
        )
        for finding in findings:
            impacted = ", ".join(
                f"{item['label']} ({_render_metric_number(item['lost_points'])})"
                for item in finding.get("impacted_rubric_lines", [])[:4]
            ) or "none"
            lines.append(f"- {finding['title']} [{finding['severity']}]")
            lines.append(
                f"  Explains {_render_metric_number(finding['lost_points_total'])} / {_render_metric_number(total_unearned)} unearned points."
            )
            lines.append(f"  {finding['headline']}")
            lines.append(f"  Impacted scoring lines: {impacted}")
    else:
        lines.append("- none")
    lines.extend(["", "What the model actually did:"])
    top_contacted_actor_id = stakeholder_metrics.get("top_contacted_actor_id")
    top_contacted_actor_share = float(stakeholder_metrics.get("top_contacted_actor_share") or 0.0)
    total_outbound = sum(int(row.get("outbound_count") or 0) for row in stakeholder_rows)
    if total_outbound:
        actor_counts = ", ".join(
            f"{row['actor_id']} {int(row.get('outbound_count') or 0)}"
            for row in sorted(
                [row for row in stakeholder_rows if int(row.get("outbound_count") or 0) > 0],
                key=lambda item: (-int(item.get("outbound_count") or 0), item["actor_id"]),
            )
        )
        lines.append(f"- Outbound stakeholder coordination: {actor_counts}.")
    if top_contacted_actor_id and total_outbound:
        lines.append(
            f"- {top_contacted_actor_id} absorbed {_render_metric_number(round(top_contacted_actor_share * 100, 1))}% of outbound stakeholder coordination."
        )
    for row in stakeholder_rows:
        if row.get("criticality_to_outcome") != "critical":
            continue
        actor_id = row["actor_id"]
        outbound_count = int(row.get("outbound_count") or 0)
        if outbound_count == 0:
            lines.append(f"- {actor_id} was a critical stakeholder and received no proactive TPM outreach.")
            continue
        detail_parts = [
            f"{actor_id} received {outbound_count} outbound message{'s' if outbound_count != 1 else ''}",
            f"first contacted at {_render_iso_datetime(row.get('first_outbound_at'))}",
        ]
        cue_delta = _minutes_between(row.get("first_cue_at"), row.get("first_outbound_at"))
        if cue_delta is not None:
            if cue_delta > 0:
                detail_parts.append(
                    f"{cue_delta} minutes after the first cue at {_render_iso_datetime(row.get('first_cue_at'))}"
                )
            elif cue_delta < 0:
                detail_parts.append(
                    f"{abs(cue_delta)} minutes before the first recorded cue at {_render_iso_datetime(row.get('first_cue_at'))}"
                )
        unanswered = len(row.get("unanswered_direct_questions", []))
        if unanswered:
            detail_parts.append(f"{unanswered} direct question{'s' if unanswered != 1 else ''} left unanswered")
        lines.append(f"- {'. '.join(detail_parts)}.")
    if reference_path_diff.get("summary"):
        lines.append(f"- Reference-path divergence: {reference_path_diff['summary']}")
    lines.extend(["", "What a strong TPM would have done instead:"])
    counterfactual_path = narrative.get("counterfactual_path", [])
    if counterfactual_path:
        for item in counterfactual_path[:4]:
            lines.append(f"- {item['explanation']}")
    elif reference_path_diff.get("missed_expected_steps_before_deadline"):
        for step in reference_path_diff["missed_expected_steps_before_deadline"][:3]:
            lines.append(f"- {step}")
    else:
        for finding in findings[:3]:
            lines.append(f"- {finding['counterfactual_step']}")
    lines.extend(["", "Supporting data:"])
    lines.append("- Deadline windows:")
    for window in windows[:3]:
        status = "hit" if window.get("state_achieved", {}).get("achieved") else "missed"
        lines.append(
            f"  {window['title']} ({_render_iso_datetime(window.get('start_at'))} -> {_render_iso_datetime(window.get('end_at'))}): {status}. "
            f"The TPM took {window['actions_taken']} successful actions before the deadline. "
            f"Outbound coordination before the deadline: {_render_outbound_counts(window.get('outbound_by_actor', {}))}. "
            f"{window.get('miss_reason') or window.get('required_state_change')}"
        )
    lines.append("- Critical signals:")
    critical_signals = [row for row in signals if row.get("criticality") == "critical"]
    observed = [row["signal_id"] for row in critical_signals if row.get("surfaced")]
    converted = [row["signal_id"] for row in critical_signals if row.get("surfaced") and row.get("converted_to_plan_change")]
    not_observed = [row["signal_id"] for row in critical_signals if not row.get("surfaced")]
    observed_not_converted = [
        row["signal_id"] for row in critical_signals if row.get("surfaced") and not row.get("converted_to_plan_change")
    ]
    lines.append(f"  Observed in the trace: {len(observed)} / {len(critical_signals)}")
    lines.append(f"  Converted into plan changes: {len(converted)} / {len(critical_signals)}")
    if observed_not_converted:
        lines.append(f"  Observed but not converted: {', '.join(observed_not_converted)}")
    if not_observed:
        lines.append(f"  Not observed at all: {', '.join(not_observed)}")
    driver_signals = [row for row in signals if row.get("kind") == "driver"]
    lines.append("- Stakeholder drivers / hidden motives:")
    if driver_signals:
        surfaced_drivers = [row for row in driver_signals if row.get("surfaced")]
        acted_on_drivers = [row for row in driver_signals if row.get("converted_to_plan_change")]
        lines.append(f"  Surfaced: {len(surfaced_drivers)} / {len(driver_signals)}")
        lines.append(f"  Acted on after surfacing: {len(acted_on_drivers)} / {len(driver_signals)}")
        for row in driver_signals:
            owner_ids = [str(actor_id) for actor_id in row.get("expected_actors") or [] if actor_id]
            suffix_parts = []
            if owner_ids:
                suffix_parts.append(f"owner={', '.join(owner_ids)}")
            if row.get("deadline_label"):
                suffix_parts.append(f"deadline={row['deadline_label']}")
            suffix_text = f" ({'; '.join(suffix_parts)})." if suffix_parts else ""
            if row.get("surfaced"):
                surfaced_text = f"surfaced {_render_iso_datetime(row.get('first_surfaced_at'))}."
                if row.get("converted_to_plan_change"):
                    acted_on_refs = ", ".join(str(ref) for ref in (row.get("conversion_action_refs") or [])[:3])
                    acted_on_text = f" Acted on via {acted_on_refs}."
                else:
                    acted_on_text = " Surfaced but not acted on in the next decision steps."
                lines.append(f"  {_render_signal_name(row)}: {surfaced_text}{acted_on_text}{suffix_text}")
            else:
                lines.append(f"  {_render_signal_name(row)}: not surfaced.{suffix_text}")
    else:
        lines.append("  No stakeholder driver clues are tracked for this scenario.")
    lines.append("- TPM private notes:")
    total_notes_written = int(note_audit.get("total_notes_written") or 0)
    if total_notes_written <= 0:
        lines.append("  No private notes were written.")
    else:
        lines.append(
            "  "
            f"Written={total_notes_written}; structured={int(note_audit.get('structured_notes_written') or 0)}; "
            f"followed through={int(note_audit.get('followed_through') or 0)}; "
            f"revisited only={int(note_audit.get('revisited_only') or 0)}; "
            f"not followed through={int(note_audit.get('not_followed_through') or 0)}; "
            f"unscoped={int(note_audit.get('unscoped_notes') or 0)}."
        )
        for note in private_note_rows:
            note_doc_id = str(note.get("note_doc_id") or note.get("note_action_ref") or "note")
            lines.append(f"  {note_doc_id}: {_render_note_status(note)}.")
    lines.extend(["", "Confidence / limitations:"])
    lines.append(
        f"- Confidence: {_render_confidence_scope(capability.get('confidence_scope', 'single_run_directional'))}"
    )
    lines.append(
        f"- Run end: {_render_termination_reason(summary['run_header'].get('termination_reason'))}; "
        f"turns={summary['run_header'].get('turns_taken')} / {summary['run_header'].get('max_turns')}; "
        f"simulated stop time={_render_iso_datetime(summary['run_header'].get('simulated_end_time'))}"
    )
    lines.extend(["", "Audit appendix:"])
    lines.append(f"- outcome verdict: {summary['outcome_verdict']['headline']}")
    lines.append(f"- critical path: {summary['critical_path_result']['status']}")
    if run_health.get("model_behavior_issues"):
        lines.append(f"- model issues: {', '.join(run_health['model_behavior_issues'])}")
    if summary.get("rubric_failure_appendix"):
        for dossier in summary["rubric_failure_appendix"][:3]:
            lines.append(f"- rubric failure: {dossier['title']} lost={dossier['lost_points']}")
    return "\n".join(lines)


def render_bundle_summary(summary: dict[str, Any]) -> str:
    narrative = summary.get("narrative", {})
    run_rows = summary.get("runs", [])
    capability_profile = summary.get("aggregate_competency_profile", [])
    critical_signal_rows = [
        item for item in summary.get("signal_coverage_consistency", []) if item.get("criticality") == "critical"
    ]
    driver_signal_rows = summary.get("driver_signal_consistency", [])
    lines = [
        f"Scenario: {summary['bundle_header']['scenario_id']}",
        f"Model: {summary['bundle_header']['model']}",
        f"Seeds: {_render_seed_list(summary['bundle_header'].get('seed_bundle', []))}",
        f"Mean score: {_render_metric_number(summary['headline']['mean_score'])} / {_render_metric_number(summary['headline'].get('score_possible', 100))}",
        f"Best score: {_render_metric_number(summary['headline']['best_score'])} / {_render_metric_number(summary['headline'].get('score_possible', 100))}",
        f"Worst score: {_render_metric_number(summary['headline']['worst_score'])} / {_render_metric_number(summary['headline'].get('score_possible', 100))}",
        f"Stdev: {summary['headline']['stdev']}",
        "",
        f"Capability verdict: {summary.get('aggregate_capability_assessment', {}).get('direct_answer', 'No aggregate verdict.')}",
        f"Confidence: {_render_confidence_scope(summary.get('confidence_scope', 'single_run_directional'))}",
    ]
    if narrative.get("direct_answer"):
        lines.append(f"Direct answer: {narrative['direct_answer']}")
    if narrative.get("executive_summary"):
        lines.append(f"Executive summary: {narrative['executive_summary']}")
    if narrative.get("top_findings"):
        lines.extend(["", "Cross-seed takeaways:"])
        for item in narrative["top_findings"][:4]:
            lines.append(f"- {item['title']}: {item['explanation']}")
    if run_rows:
        lines.extend(["", "Per-seed comparison:"])
        lines.extend(
            _render_markdown_table(
                [
                    "Seed",
                    "Score",
                    "Capability",
                    "Outcome",
                    "Critical signals",
                    "Driver clues",
                    "Stakeholder handling",
                    "Windows",
                    "Biggest miss",
                ],
                [
                    [
                        _render_metric_number(row.get("seed")),
                        (
                            f"{_render_metric_number(row['score'])} / {_render_metric_number(row.get('score_possible', 0))} "
                            f"({_render_metric_number(row.get('score_percent', 0))}%)"
                        ),
                        str(row.get("capability_rating") or "unknown"),
                        str(row.get("outcome_verdict") or row.get("critical_path_status") or "unknown"),
                        (
                            f"{row.get('critical_signals_observed', 0)}/{row.get('critical_signals_total', 0)} seen, "
                            f"{row.get('critical_signals_converted', 0)}/{row.get('critical_signals_total', 0)} acted"
                        ),
                        (
                            f"{row.get('driver_signals_observed', 0)}/{row.get('driver_signals_total', 0)} seen, "
                            f"{row.get('driver_signals_converted', 0)}/{row.get('driver_signals_total', 0)} acted"
                        ),
                        _render_run_stakeholder_cell(row),
                        f"{row.get('windows_hit', 0)}/{row.get('windows_total', 0)} hit",
                        str((row.get("top_root_cause") or {}).get("title") or (row.get("top_failure_theme") or {}).get("title") or "none"),
                    ]
                    for row in run_rows
                ],
            )
        )
    if capability_profile:
        lines.extend(["", "Capability profile:"])
        lines.extend(
            _render_markdown_table(
                ["Dimension", "Mean", "Worst", "Best", "Spread", "Stdev", "Read"],
                [
                    [
                        item["label"],
                        _render_metric_number(item["mean_score"]),
                        _render_metric_number(item["worst_score"]),
                        _render_metric_number(item["best_score"]),
                        _render_metric_number(item.get("spread", 0)),
                        _render_metric_number(item["stdev"]),
                        item["band"],
                    ]
                    for item in capability_profile
                ],
            )
        )
    if summary.get("recurring_root_causes"):
        lines.extend(["", "Recurring root causes:"])
        lines.extend(
            _render_markdown_table(
                ["Root cause", "Runs", "Seeds", "Mean lost points"],
                [
                    [
                        item["title"],
                        f"{item['count']}/{summary['bundle_header'].get('seed_count', len(run_rows))}",
                        _render_seed_list(item.get("seeds", [])),
                        _render_metric_number(item["mean_lost_points"]),
                    ]
                    for item in summary["recurring_root_causes"][:6]
                ],
            )
        )
    if critical_signal_rows:
        lines.extend(["", "Critical signal consistency:"])
        lines.extend(
            _render_markdown_table(
                ["Signal", "Surfaced", "Acted on", "Seeds surfaced", "Seeds acted on"],
                [
                    [
                        _render_signal_name(item),
                        _render_rate_cell(len(item.get("seeds_surfaced", [])), summary["bundle_header"].get("seed_count", item.get("runs", 0))),
                        _render_rate_cell(len(item.get("seeds_converted", [])), summary["bundle_header"].get("seed_count", item.get("runs", 0))),
                        _render_seed_list(item.get("seeds_surfaced", [])),
                        _render_seed_list(item.get("seeds_converted", [])),
                    ]
                    for item in critical_signal_rows[:8]
                ],
            )
        )
    if driver_signal_rows:
        lines.extend(["", "Driver clue consistency:"])
        lines.extend(
            _render_markdown_table(
                ["Driver clue", "Surfaced", "Acted on", "Seeds surfaced", "Seeds acted on"],
                [
                    [
                        _render_signal_name(item),
                        _render_rate_cell(len(item.get("seeds_surfaced", [])), summary["bundle_header"].get("seed_count", item.get("runs", 0))),
                        _render_rate_cell(len(item.get("seeds_converted", [])), summary["bundle_header"].get("seed_count", item.get("runs", 0))),
                        _render_seed_list(item.get("seeds_surfaced", [])),
                        _render_seed_list(item.get("seeds_converted", [])),
                    ]
                    for item in driver_signal_rows[:6]
                ],
            )
        )
    if summary.get("stakeholder_failure_patterns"):
        lines.extend(["", "Stakeholder failure patterns:"])
        lines.extend(
            _render_markdown_table(
                ["Stakeholder", "Never contacted", "After deadline", "Unanswered questions", "Affected seeds"],
                [
                    [
                        _render_actor_name(item),
                        _render_count_with_seed_refs(item.get("never_contacted", 0), item.get("seeds_never_contacted", [])),
                        _render_count_with_seed_refs(item.get("after_deadline", 0), item.get("seeds_after_deadline", [])),
                        _render_count_with_seed_refs(
                            item.get("unanswered_questions", 0), item.get("seeds_with_unanswered_questions", [])
                        ),
                        _render_seed_list(
                            _sorted_seed_list(
                                item.get("seeds_never_contacted", [])
                                + item.get("seeds_after_deadline", [])
                                + item.get("seeds_with_unanswered_questions", [])
                            )
                        ),
                    ]
                    for item in summary["stakeholder_failure_patterns"][:6]
                ],
            )
        )
    if summary.get("window_miss_recurrence"):
        lines.extend(["", "Deadline-window misses:"])
        lines.extend(
            _render_markdown_table(
                ["Window", "Missed in runs", "Seeds"],
                [
                    [
                        item.get("title") or item["window_id"],
                        f"{item['count']}/{summary['bundle_header'].get('seed_count', len(run_rows))}",
                        _render_seed_list(item.get("seeds", [])),
                    ]
                    for item in summary["window_miss_recurrence"][:6]
                ],
            )
        )
    note_audit = summary.get("private_note_audit_aggregate", {})
    if note_audit:
        lines.extend(["", "Private note audit:"])
        lines.extend(
            _render_markdown_table(
                ["Metric", "Value"],
                [
                    ["Runs with notes", f"{note_audit.get('runs_with_any_notes', 0)}/{summary['bundle_header'].get('seed_count', len(run_rows))}"],
                    ["Runs with follow-through", _render_metric_number(note_audit.get("runs_with_followed_through_notes", 0))],
                    ["Mean notes written", _render_metric_number(note_audit.get("mean_notes_written", 0))],
                    ["Mean followed-through notes", _render_metric_number(note_audit.get("mean_followed_through", 0))],
                ],
            )
        )
    lines.extend(["", "Bundle health:"])
    harness_health = summary.get("harness_health", {})
    if harness_health.get("status") == "clean":
        lines.append("- Harness health was clean across the seed bundle.")
    else:
        issues = ", ".join(f"{item['issue']} ({item['count']})" for item in harness_health.get("issues", [])) or "unknown issues"
        lines.append(f"- Harness health needs attention: {issues}.")
    dimension_highlights = summary.get("dimension_highlights", {})
    if dimension_highlights.get("stable_strengths"):
        labels = ", ".join(item["label"] for item in dimension_highlights["stable_strengths"])
        lines.append(f"- Stable strengths: {labels}.")
    if dimension_highlights.get("stable_weaknesses"):
        labels = ", ".join(item["label"] for item in dimension_highlights["stable_weaknesses"])
        lines.append(f"- Stable weaknesses: {labels}.")
    if dimension_highlights.get("seed_sensitive"):
        labels = ", ".join(item["label"] for item in dimension_highlights["seed_sensitive"])
        lines.append(f"- Seed-sensitive dimensions: {labels}.")
    if summary.get("reference_divergence_patterns"):
        lines.append(
            "- Reference divergence patterns: "
            + "; ".join(
                f"{item['pattern']} ({item['count']})" for item in summary["reference_divergence_patterns"][:5]
            )
            + "."
        )
    if narrative.get("limitations"):
        lines.extend(["", "Limitations:"])
        for item in narrative["limitations"][:3]:
            lines.append(f"- {item['title']}: {item['explanation']}")
    return "\n".join(lines)


def _deterministic_bundle_narrative(summary: dict[str, Any]) -> dict[str, Any]:
    total_runs = int(summary.get("bundle_header", {}).get("seed_count") or len(summary.get("runs", [])) or 0)
    confidence_scope = str(summary.get("confidence_scope") or "single_run_directional")
    direct_answer = summary.get("aggregate_capability_assessment", {}).get("direct_answer", "No aggregate verdict.")
    headline = summary.get("headline", {})
    recurring_root_causes = summary.get("recurring_root_causes", [])
    dimension_highlights = summary.get("dimension_highlights", {})
    stakeholder_patterns = summary.get("stakeholder_failure_patterns", [])
    critical_signal_rows = [
        item for item in summary.get("signal_coverage_consistency", []) if item.get("criticality") == "critical"
    ]
    run_rows = summary.get("runs", [])
    best_run = max(run_rows, key=lambda row: float(row.get("score") or 0.0), default=None)
    worst_run = min(run_rows, key=lambda row: float(row.get("score") or 0.0), default=None)
    executive_parts = [
        (
            f"Across {total_runs} seed{'s' if total_runs != 1 else ''}, the model averaged "
            f"{_render_metric_number(headline.get('mean_score', 0))} / {_render_metric_number(headline.get('score_possible', 100))} "
            f"with best {_render_metric_number(headline.get('best_score', 0))}, "
            f"worst {_render_metric_number(headline.get('worst_score', 0))}, and stdev {_render_metric_number(headline.get('stdev', 0))}."
        )
    ]
    if recurring_root_causes:
        top_root = recurring_root_causes[0]
        executive_parts.append(
            f"The main recurring failure was {top_root['title']}, appearing in {top_root['count']}/{max(total_runs, 1)} seeds."
        )
    gap_explanation = _bundle_run_gap_explanation(best_run, worst_run)
    if gap_explanation:
        executive_parts.append(
            f"Best seed {best_run['seed']} beat seed {worst_run['seed']} on more than raw score: {gap_explanation}"
        )
    majority = max(1, (total_runs + 1) // 2)
    top_findings: list[dict[str, Any]] = []
    if recurring_root_causes:
        top_root = recurring_root_causes[0]
        top_findings.append(
            {
                "title": "Structural failure pattern",
                "explanation": (
                    f"{top_root['title']} shows up in {top_root['count']}/{max(total_runs, 1)} seeds "
                    f"(seeds {_render_seed_list(top_root.get('seeds', []))}) and costs about "
                    f"{_render_metric_number(top_root.get('mean_lost_points', 0))} points when present."
                ),
                "seed_refs": top_root.get("seeds", []),
            }
        )
    stable_weaknesses = dimension_highlights.get("stable_weaknesses", [])
    if stable_weaknesses:
        top_findings.append(
            {
                "title": "Consistently weak TPM dimensions",
                "explanation": (
                    f"{', '.join(item['label'] for item in stable_weaknesses[:3])} stayed weak across seeds, "
                    "so the model's misses are not just a single-seed artifact."
                ),
                "seed_refs": summary.get("bundle_header", {}).get("seed_bundle", []),
            }
        )
    blind_spots = [item for item in critical_signal_rows if len(item.get("seeds_surfaced", [])) < majority]
    acted_too_late = [
        item
        for item in critical_signal_rows
        if len(item.get("seeds_surfaced", [])) >= majority and len(item.get("seeds_converted", [])) < majority
    ]
    if blind_spots:
        top_findings.append(
            {
                "title": "Critical clues are not surfaced reliably",
                "explanation": (
                    f"The model still misses critical cues like {_render_signal_name(blind_spots[0])}; "
                    f"it only surfaced in seeds {_render_seed_list(blind_spots[0].get('seeds_surfaced', [])) or 'none'}."
                ),
                "seed_refs": blind_spots[0].get("seeds_surfaced", []),
            }
        )
    elif acted_too_late:
        top_findings.append(
            {
                "title": "Surfacing does not reliably become action",
                "explanation": (
                    f"{_render_signal_name(acted_too_late[0])} is often noticed but not consistently turned into plan changes "
                    f"(acted in seeds {_render_seed_list(acted_too_late[0].get('seeds_converted', []))})."
                ),
                "seed_refs": acted_too_late[0].get("seeds_converted", []),
            }
        )
    recurring_actor = next(
        (
            item
            for item in stakeholder_patterns
            if item.get("never_contacted", 0) >= majority
            or item.get("after_deadline", 0) >= majority
            or item.get("runs_affected", 0) >= majority
        ),
        None,
    )
    if recurring_actor:
        top_findings.append(
            {
                "title": "Stakeholder handling is part of the failure mode",
                "explanation": (
                    f"{_render_actor_name(recurring_actor)} is mishandled across seeds: "
                    f"never_contacted={recurring_actor.get('never_contacted', 0)}, "
                    f"after_deadline={recurring_actor.get('after_deadline', 0)}, "
                    f"unanswered_questions={recurring_actor.get('unanswered_questions', 0)}."
                ),
                "seed_refs": _sorted_seed_list(
                    recurring_actor.get("seeds_never_contacted", [])
                    + recurring_actor.get("seeds_after_deadline", [])
                    + recurring_actor.get("seeds_with_unanswered_questions", [])
                ),
            }
        )
    if gap_explanation and best_run and worst_run:
        score_gap = float(best_run.get("score") or 0.0) - float(worst_run.get("score") or 0.0)
        top_findings.append(
            {
                "title": "Best-vs-worst seed gap is about early clue conversion",
                "explanation": (
                    f"Seed {best_run['seed']} beat seed {worst_run['seed']} by {_render_metric_number(score_gap)} points. {gap_explanation}"
                ),
                "seed_refs": [best_run.get("seed"), worst_run.get("seed")],
            }
        )
    supporting_data: list[dict[str, Any]] = []
    stable_strengths = dimension_highlights.get("stable_strengths", [])
    if stable_strengths:
        supporting_data.append(
            {
                "title": "Stable strengths",
                "explanation": f"Most reliable strengths: {', '.join(item['label'] for item in stable_strengths[:3])}.",
                "seed_refs": summary.get("bundle_header", {}).get("seed_bundle", []),
            }
        )
    if summary.get("window_miss_recurrence"):
        top_window = summary["window_miss_recurrence"][0]
        supporting_data.append(
            {
                "title": "Repeated deadline miss",
                "explanation": (
                    f"{top_window.get('title') or top_window['window_id']} was missed in "
                    f"{top_window['count']}/{max(total_runs, 1)} seeds."
                ),
                "seed_refs": top_window.get("seeds", []),
            }
        )
    harness_health = summary.get("harness_health", {})
    supporting_data.append(
        {
            "title": "Harness health",
            "explanation": (
                "Harness health was clean across the bundle."
                if harness_health.get("status") == "clean"
                else "Harness health needs attention, so some bundle conclusions may be partially confounded by infrastructure noise."
            ),
            "seed_refs": [],
        }
    )
    limitations = [
        {
            "title": "Confidence scope",
            "explanation": _render_confidence_scope(confidence_scope),
        }
    ]
    if harness_health.get("status") != "clean":
        issues = ", ".join(f"{item['issue']} ({item['count']})" for item in harness_health.get("issues", [])) or "unknown issues"
        limitations.append(
            {
                "title": "Harness caveat",
                "explanation": f"Bundle health was not fully clean: {issues}.",
            }
        )
    return {
        "source": "deterministic_template",
        "direct_answer": direct_answer,
        "executive_summary": " ".join(executive_parts),
        "top_findings": top_findings[:4],
        "supporting_data": supporting_data[:4],
        "limitations": limitations[:3],
    }


def _render_seed_list(values: Iterable[Any]) -> str:
    seeds = _sorted_seed_list(values)
    return ", ".join(str(seed) for seed in seeds) if seeds else "none"


def _bundle_run_gap_explanation(best_run: dict[str, Any] | None, worst_run: dict[str, Any] | None) -> str | None:
    if not best_run or not worst_run or best_run.get("seed") == worst_run.get("seed"):
        return None
    details: list[str] = []
    if int(best_run.get("critical_signals_converted") or 0) != int(worst_run.get("critical_signals_converted") or 0):
        details.append(
            f"it converted {best_run.get('critical_signals_converted', 0)}/{best_run.get('critical_signals_total', 0)} "
            f"critical signals versus {worst_run.get('critical_signals_converted', 0)}/{worst_run.get('critical_signals_total', 0)}"
        )
    elif int(best_run.get("critical_signals_observed") or 0) != int(worst_run.get("critical_signals_observed") or 0):
        details.append(
            f"it surfaced {best_run.get('critical_signals_observed', 0)}/{best_run.get('critical_signals_total', 0)} "
            f"critical signals versus {worst_run.get('critical_signals_observed', 0)}/{worst_run.get('critical_signals_total', 0)}"
        )
    if int(best_run.get("windows_hit") or 0) != int(worst_run.get("windows_hit") or 0):
        details.append(
            f"it hit {best_run.get('windows_hit', 0)}/{best_run.get('windows_total', 0)} deadline windows versus "
            f"{worst_run.get('windows_hit', 0)}/{worst_run.get('windows_total', 0)}"
        )
    best_missed = len(best_run.get("critical_actors_never_contacted", []))
    worst_missed = len(worst_run.get("critical_actors_never_contacted", []))
    if best_missed != worst_missed:
        details.append(
            f"it left {best_missed} critical stakeholder{'s' if best_missed != 1 else ''} untouched versus {worst_missed}"
        )
    best_unanswered = int(best_run.get("unanswered_direct_questions") or 0)
    worst_unanswered = int(worst_run.get("unanswered_direct_questions") or 0)
    if best_unanswered != worst_unanswered:
        details.append(
            f"it left {best_unanswered} unanswered direct question{'s' if best_unanswered != 1 else ''} versus {worst_unanswered}"
        )
    return "; ".join(details[:2]) if details else None


def _render_rate_cell(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "0/0"
    return f"{numerator}/{denominator} ({_render_metric_number(round((numerator / denominator) * 100, 1))}%)"


def _render_actor_name(row: dict[str, Any]) -> str:
    actor_id = str(row.get("actor_id") or "unknown")
    name = str(row.get("name") or "").strip()
    return f"{name} [{actor_id}]" if name and name != actor_id else actor_id


def _render_run_stakeholder_cell(row: dict[str, Any]) -> str:
    parts = []
    if row.get("critical_actors_never_contacted"):
        parts.append(f"missed {_render_list_inline(row['critical_actors_never_contacted'])}")
    if row.get("critical_actors_contacted_after_deadline"):
        parts.append(f"late {_render_list_inline(row['critical_actors_contacted_after_deadline'])}")
    unanswered = int(row.get("unanswered_direct_questions") or 0)
    if unanswered:
        parts.append(f"{unanswered} unanswered")
    return "; ".join(parts) if parts else "none"


def _render_list_inline(values: Iterable[Any]) -> str:
    items = [str(value) for value in values if str(value)]
    return ", ".join(items) if items else "none"


def _render_count_with_seed_refs(count: int, seeds: Iterable[Any]) -> str:
    rendered_seeds = _render_seed_list(seeds)
    if rendered_seeds == "none":
        return str(count)
    return f"{count} ({rendered_seeds})"


def _render_markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    def _cell(value: Any) -> str:
        text = str(value)
        return text.replace("|", "\\|").replace("\n", "<br>")

    table = [
        "| " + " | ".join(_cell(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        table.append("| " + " | ".join(_cell(value) for value in row) + " |")
    return table


def build_behavior_diagnostics(
    decision_rows: list[dict[str, Any]],
    omniscient_trace: list[dict[str, Any]],
    scenario: dict[str, Any],
    *,
    action_log_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    action_rows = list(decision_rows)
    read_count = sum(1 for row in action_rows if str(row["action_type"]).startswith("read."))
    write_count = sum(1 for row in action_rows if row["action_type"] in WRITE_ACTION_TYPES)
    wait_count = sum(1 for row in action_rows if str(row["action_type"]).startswith("wait."))
    artifact_churn = sum(1 for row in action_rows if row["action_type"] == "docs.write")
    tracker_churn = sum(1 for row in action_rows if row["action_type"] in {"task.note", "task.set_owner", "task.set_target"})
    escalation_actions = [row for row in action_rows if row["act_id"] in {"escalate.to_manager", "escalate.to_sponsor"}]
    escalation_repetition = _count_repeated_pairs((row["target"], row["act_id"]) for row in escalation_actions)
    repeated_loops = _find_repeated_action_loops(action_rows)
    approval_before_preconditions = _count_approval_before_scope(action_rows, omniscient_trace, scenario)
    reply_loops = _count_unresolved_reply_loops(action_rows, omniscient_trace)
    alias_normalizations = sum(1 for row in omniscient_trace if row.get("event_type") == "thread.normalized")
    coverage_misses = sum(1 for row in omniscient_trace if row.get("event_type") == "coverage.miss")
    batched_replies = sum(
        max(0, int((row.get("payload") or {}).get("batched_message_count", 1)) - 1)
        for row in omniscient_trace
        if row.get("event_type") == "npc.message_sent"
    )
    protocol_repairs = sum(int(row.get("repair_attempts", 0)) for row in action_rows)
    chat_minutes_by_cost_key: dict[str, int] = {}
    if action_log_rows:
        counter: Counter[str] = Counter()
        for row in action_log_rows:
            metadata = row.get("metadata", {})
            if row.get("surface") != "chat" or row.get("actor_id") != "tpm":
                continue
            cost_key = str(metadata.get("cost_key") or "send_chat")
            counter[cost_key] += int(metadata.get("cost_minutes") or row.get("duration_minutes") or 0)
        chat_minutes_by_cost_key = dict(sorted(counter.items()))
    private_note_audit = _build_private_note_audit(action_rows)
    diagnostics = {
        "counts": {
            "reads": read_count,
            "writes": write_count,
            "waits": wait_count,
            "artifact_churn": artifact_churn,
            "tracker_churn": tracker_churn,
            "escalation_repetition": escalation_repetition,
            "repeated_action_loops": len(repeated_loops),
            "approval_before_preconditions": approval_before_preconditions,
            "unresolved_reply_loops": reply_loops,
            "alias_normalization_corrections": alias_normalizations,
            "protocol_repairs": protocol_repairs,
            "coverage_miss_count": coverage_misses,
            "batched_npc_reply_count": batched_replies,
        },
        "repeated_action_loops": repeated_loops,
        "chat_minutes_by_cost_key": chat_minutes_by_cost_key,
        "private_note_audit": private_note_audit["counts"],
        "private_note_audit_rows": private_note_audit["notes"],
    }
    return diagnostics


def _build_score_breakdown(rubric_lines: list[dict[str, Any]]) -> dict[str, Any]:
    total_possible = round(sum(float(line["weight"]) for line in rubric_lines), 2)
    total_awarded = round(sum(float(line["awarded"]) for line in rubric_lines), 2)
    groups: dict[str, dict[str, Any]] = {}
    for line in rubric_lines:
        failure_class = str(line.get("failure_class") or "other")
        group = groups.setdefault(
            failure_class,
            {
                "id": failure_class,
                "label": FAILURE_CLASS_LABELS.get(failure_class, failure_class.replace("_", " ").title()),
                "awarded": 0.0,
                "weight": 0.0,
                "lines": [],
            },
        )
        group["awarded"] = round(float(group["awarded"]) + float(line["awarded"]), 2)
        group["weight"] = round(float(group["weight"]) + float(line["weight"]), 2)
        group["lines"].append(
            {
                "id": line["id"],
                "label": line["label"],
                "awarded": float(line["awarded"]),
                "weight": float(line["weight"]),
                "lost_points": round(float(line["weight"]) - float(line["awarded"]), 2),
            }
        )
    ordered_groups = sorted(
        groups.values(),
        key=lambda item: (FAILURE_CLASS_ORDER.get(item["id"], 99), -float(item["weight"]), item["id"]),
    )
    earned_lines = sorted(
        [
            {
                "id": line["id"],
                "label": line["label"],
                "awarded": float(line["awarded"]),
                "weight": float(line["weight"]),
            }
            for line in rubric_lines
            if float(line["awarded"]) > 0
        ],
        key=lambda item: (float(item["awarded"]), float(item["weight"])),
        reverse=True,
    )
    missed_lines = sorted(
        [
            {
                "id": line["id"],
                "label": line["label"],
                "awarded": float(line["awarded"]),
                "weight": float(line["weight"]),
                "lost_points": round(float(line["weight"]) - float(line["awarded"]), 2),
            }
            for line in rubric_lines
            if float(line["awarded"]) < float(line["weight"])
        ],
        key=lambda item: (float(item["lost_points"]), float(item["weight"])),
        reverse=True,
    )
    return {
        "total_awarded": total_awarded,
        "total_possible": total_possible,
        "total_unearned": round(total_possible - total_awarded, 2),
        "score_percent": round((total_awarded / total_possible) * 100, 2) if total_possible else 0.0,
        "groups": ordered_groups,
        "earned_lines": earned_lines,
        "missed_lines": missed_lines,
    }


def _build_dimension_scores(rubric_lines: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    scores: dict[str, dict[str, Any]] = {}
    for dimension in DIMENSION_DEFINITIONS:
        tagged = [line for line in rubric_lines if dimension["id"] in line.get("competency_tags", [])]
        possible = round(sum(float(line["weight"]) for line in tagged), 2)
        achieved = round(sum(float(line["awarded"]) for line in tagged), 2)
        score = round((achieved / possible) * 100, 2) if possible else 0.0
        scores[dimension["id"]] = {
            "id": dimension["id"],
            "label": dimension["label"],
            "kind": dimension["kind"],
            "score": score,
            "awarded": achieved,
            "weight": possible,
            "band": _band_from_score(score),
            "description": dimension["description"],
            "counts": dimension["counts"],
            "does_not_count": dimension["does_not_count"],
            "contributing_rubric_lines": [
                {
                    "id": line["id"],
                    "label": line["label"],
                    "awarded": line["awarded"],
                    "weight": line["weight"],
                    "measurement_rationale": line.get("measurement_rationale", ""),
                    "evidence_refs": line.get("evidence_refs", []),
                }
                for line in tagged
            ],
        }
    return scores


def _build_critical_path_result(rubric_lines: list[dict[str, Any]], outcome_profile: list[dict[str, Any]]) -> dict[str, Any]:
    outcome_lines = [line for line in rubric_lines if "outcome_attainment" in line.get("competency_tags", [])]
    achieved = [line for line in outcome_lines if float(line["awarded"]) >= float(line["weight"])]
    missed = sorted(
        [line for line in outcome_lines if float(line["awarded"]) < float(line["weight"])],
        key=lambda item: (float(item["weight"]) - float(item["awarded"])),
        reverse=True,
    )
    outcome_score = next((item["score"] for item in outcome_profile if item["id"] == "outcome_attainment"), 0.0)
    timing_score = next((item["score"] for item in outcome_profile if item["id"] == "timing_optionality_preservation"), 0.0)
    if missed and not achieved:
        status = "failed_to_move_critical_path"
    elif missed:
        status = "partial_critical_path_progress"
    else:
        status = "critical_path_moved"
    return {
        "status": status,
        "outcome_attainment_score": outcome_score,
        "timing_optionality_score": timing_score,
        "achieved_outcomes": [
            {"id": line["id"], "label": line["label"], "summary": line.get("success_meaning", line["label"])}
            for line in achieved
        ],
        "missed_outcomes": [
            {"id": line["id"], "label": line["label"], "summary": line.get("failure_meaning", line["label"])}
            for line in missed[:5]
        ],
    }


def _build_run_health(report: dict[str, Any], run_record: dict[str, Any], diagnostics: dict[str, Any]) -> dict[str, Any]:
    model_issues: list[str] = []
    if diagnostics["counts"]["repeated_action_loops"] > 0:
        model_issues.append("repeated coordination loops")
    if diagnostics["counts"]["artifact_churn"] >= 3:
        model_issues.append("document churn")
    if diagnostics["counts"]["tracker_churn"] >= 4:
        model_issues.append("tracker churn")
    if diagnostics["counts"]["approval_before_preconditions"] > 0:
        model_issues.append("approval-before-preconditions")
    if diagnostics["counts"]["unresolved_reply_loops"] > 0:
        model_issues.append("re-asking without incorporating replies")
    if diagnostics["counts"]["reads"] and diagnostics["counts"]["writes"] > diagnostics["counts"]["reads"] * 2:
        model_issues.append("write-heavy coordination without enough learning")

    harness_issues: list[str] = []
    if diagnostics["counts"]["alias_normalization_corrections"] > 0:
        harness_issues.append("target normalization corrections were needed")
    if diagnostics["counts"]["protocol_repairs"] > 0:
        harness_issues.append("protocol repair path triggered")

    scenario_issues: list[str] = []
    if diagnostics["counts"]["coverage_miss_count"] > 0 or report.get("coverage_miss"):
        scenario_issues.append("coverage miss observed")

    if run_record.get("protocol_failure") or report.get("coverage_miss"):
        overall_status = "protocol_failure"
    elif model_issues or harness_issues or scenario_issues:
        overall_status = "attention_needed"
    else:
        overall_status = "clean"
    model_status = "attention_needed" if model_issues else "clean"
    harness_status = "attention_needed" if harness_issues or scenario_issues else "clean"

    return {
        "status": overall_status,
        "overall_status": overall_status,
        "model_status": model_status,
        "harness_status": harness_status,
        "termination_reason": _normalized_termination_reason(run_record),
        "simulated_end_time": run_record.get("simulated_end_time", report.get("time")),
        "protocol_failure": bool(run_record.get("protocol_failure")),
        "protocol_failure_reason": run_record.get("protocol_failure_reason"),
        "coverage_miss": bool(report.get("coverage_miss")),
        "model_behavior_issues": model_issues,
        "harness_interface_issues": harness_issues,
        "scenario_authoring_issues": scenario_issues,
        "chat_minutes_by_cost_key": diagnostics.get("chat_minutes_by_cost_key", {}),
        "behavior_diagnostics": {
            key: value
            for key, value in diagnostics.items()
            if key != "private_note_audit_rows"
        },
    }


def _load_action_rows(run_record: dict[str, Any]) -> list[dict[str, Any]]:
    output_dir = run_record.get("output_dir")
    if not output_dir:
        return []
    db_path = Path(output_dir) / "run.sqlite"
    if not db_path.exists():
        return []
    store = open_store(str(db_path))
    try:
        rows = []
        for row in store.actions():
            rows.append(
                {
                    "id": int(row["id"]),
                    "at": row["at"],
                    "actor_id": row["actor_id"],
                    "surface": row["surface"],
                    "act_id": row["act_id"],
                    "slots": json.loads(row["slots_json"] or "{}"),
                    "body": row["body"],
                    "duration_minutes": row["duration_minutes"],
                    "metadata": json.loads(row["metadata_json"] or "{}"),
                }
            )
        return rows
    finally:
        store.close()


def _load_message_rows(run_record: dict[str, Any]) -> list[dict[str, Any]]:
    output_dir = run_record.get("output_dir")
    if not output_dir:
        return []
    db_path = Path(output_dir) / "run.sqlite"
    if not db_path.exists():
        return []
    store = open_store(str(db_path))
    try:
        rows = store.fetchall("SELECT * FROM messages ORDER BY created_at ASC, id ASC")
        return [
            {
                "id": int(row["id"]),
                "thread_id": row["thread_id"],
                "surface": row["surface"],
                "sender_id": row["sender_id"],
                "act_id": row["act_id"],
                "slots": json.loads(row["slots_json"] or "{}"),
                "body": row["body"],
                "created_at": row["created_at"],
                "unread_for_tpm": bool(row["unread_for_tpm"]),
                "metadata": json.loads(row["metadata_json"] or "{}"),
            }
            for row in rows
        ]
    finally:
        store.close()


def _load_document_rows(run_record: dict[str, Any]) -> list[dict[str, Any]]:
    output_dir = run_record.get("output_dir")
    if not output_dir:
        return []
    db_path = Path(output_dir) / "run.sqlite"
    if not db_path.exists():
        return []
    store = open_store(str(db_path))
    try:
        rows = store.fetchall("SELECT * FROM documents ORDER BY updated_at ASC, id ASC")
        return [
            {
                "id": row["id"],
                "type": row["type"],
                "title": row["title"],
                "author_id": row["author_id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "content": row["content"],
                "metadata": json.loads(row["metadata_json"] or "{}"),
            }
            for row in rows
        ]
    finally:
        store.close()


def _simulated_minutes_elapsed(start_at: str, end_at: str) -> int:
    return max(0, int((from_iso(end_at) - from_iso(start_at)).total_seconds() // 60))


def _merge_decision_action_rows(agent_payload: dict[str, Any] | None, action_log_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decisions = (agent_payload or {}).get("decisions", [])
    persisted_rows = [row for row in action_log_rows if row.get("actor_id") == "tpm"]
    persisted_by_ref = {f"action:{row['id']}": row for row in persisted_rows}
    persisted_index = 0
    used_action_ids: set[int] = set()
    merged_rows: list[dict[str, Any]] = []
    for turn in decisions:
        decision = turn.get("decision", {})
        action = decision.get("action", {})
        args = action.get("arguments", {})
        step_succeeded = turn.get("step_result") is not None
        persisted = None
        executed_action_ref = turn.get("executed_action_ref")
        if step_succeeded and isinstance(executed_action_ref, str):
            candidate = persisted_by_ref.get(executed_action_ref)
            if candidate is not None:
                persisted = candidate
                used_action_ids.add(int(candidate["id"]))
        if step_succeeded and persisted is None:
            while persisted_index < len(persisted_rows) and int(persisted_rows[persisted_index]["id"]) in used_action_ids:
                persisted_index += 1
            if persisted_index < len(persisted_rows):
                persisted = persisted_rows[persisted_index]
                used_action_ids.add(int(persisted["id"]))
                persisted_index += 1
        persisted_slots = persisted.get("slots", {}) if persisted else {}
        persisted_metadata = persisted.get("metadata", {}) if persisted else {}
        resolved_action_ref = f"action:{persisted['id']}" if persisted else None
        merged_rows.append(
            {
                "turn": turn.get("turn"),
                "time": turn.get("observation_time"),
                "action_type": action.get("action_type"),
                "target": args.get("target"),
                "act_id": args.get("act_id"),
                "doc_id": args.get("doc_id") or persisted_slots.get("doc_id") or persisted_metadata.get("doc_id"),
                "task_id": args.get("task_id") or persisted_slots.get("task_id"),
                "meeting_id": args.get("meeting_id") or persisted_slots.get("meeting_id"),
                "refs": list(args.get("refs") or persisted_metadata.get("refs") or []),
                "validation_errors": turn.get("validation_errors") or [],
                "repair_attempts": turn.get("repair_attempts", 0),
                "step_succeeded": step_succeeded,
                "executed_action_ref": executed_action_ref,
                "action_id": persisted.get("id") if persisted else None,
                "action_ref": executed_action_ref if isinstance(executed_action_ref, str) and executed_action_ref in persisted_by_ref else resolved_action_ref,
                "surface": persisted.get("surface") if persisted else None,
                "duration_minutes": persisted.get("duration_minutes") if persisted else None,
                "body": persisted.get("body") if persisted else str(args.get("body", "")),
                "slots": persisted_slots,
                "metadata": persisted_metadata,
                "thread_id": persisted_slots.get("thread_id") or persisted_metadata.get("thread_id"),
                "target_actor_id": persisted_metadata.get("target_actor_id"),
            }
        )
    return merged_rows


def _build_private_note_audit(action_rows: list[dict[str, Any]]) -> dict[str, Any]:
    successful_rows = [row for row in action_rows if row.get("step_succeeded")]
    counts = {
        "total_notes_written": 0,
        "structured_notes_written": 0,
        "followed_through": 0,
        "revisited_only": 0,
        "not_followed_through": 0,
        "unscoped_notes": 0,
    }
    notes: list[dict[str, Any]] = []
    for index, row in enumerate(successful_rows):
        if row.get("action_type") != "notes.write":
            continue
        counts["total_notes_written"] += 1
        refs = [str(ref) for ref in row.get("refs") or []]
        note_doc_id = str(row.get("doc_id") or row.get("metadata", {}).get("doc_id") or "")
        note_reread = None
        first_touch = None
        first_touch_refs: list[str] = []
        first_non_read_touch = None
        first_non_read_touch_refs: list[str] = []
        touch_action_refs: list[str] = []
        for later in successful_rows[index + 1 :]:
            if later.get("action_type") == "read.doc" and note_doc_id and _row_doc_id(later) == note_doc_id and note_reread is None:
                note_reread = later
            if later.get("action_type") == "notes.write":
                continue
            matched_refs = [ref for ref in refs if _action_touches_note_ref(later, ref)]
            if not matched_refs:
                continue
            if later.get("action_ref"):
                touch_action_refs.append(str(later["action_ref"]))
            if first_touch is None:
                first_touch = later
                first_touch_refs = matched_refs
            if not _is_read_action(later) and first_non_read_touch is None:
                first_non_read_touch = later
                first_non_read_touch_refs = matched_refs
        if not refs:
            status = "unscoped"
            counts["unscoped_notes"] += 1
        else:
            counts["structured_notes_written"] += 1
            if first_non_read_touch is not None:
                status = "followed_through"
                counts["followed_through"] += 1
            elif first_touch is not None:
                status = "revisited_only"
                counts["revisited_only"] += 1
            else:
                status = "not_followed_through"
                counts["not_followed_through"] += 1
        notes.append(
            {
                "note_doc_id": note_doc_id or None,
                "note_action_ref": row.get("action_ref"),
                "created_turn": row.get("turn"),
                "created_at": row.get("time"),
                "refs": refs,
                "status": status,
                "reread_note": bool(note_reread),
                "reread_note_action_ref": note_reread.get("action_ref") if note_reread else None,
                "reread_note_at": note_reread.get("time") if note_reread else None,
                "first_touch_action_ref": first_touch.get("action_ref") if first_touch else None,
                "first_touch_at": first_touch.get("time") if first_touch else None,
                "first_touch_refs": first_touch_refs,
                "first_non_read_touch_action_ref": first_non_read_touch.get("action_ref") if first_non_read_touch else None,
                "first_non_read_touch_at": first_non_read_touch.get("time") if first_non_read_touch else None,
                "first_non_read_touch_refs": first_non_read_touch_refs,
                "touch_action_refs": _unique_refs(touch_action_refs),
            }
        )
    return {"counts": counts, "notes": notes}


def _action_touches_note_ref(row: dict[str, Any], ref: str) -> bool:
    kind, ref_id = _split_note_ref(ref)
    if kind == "actor":
        if row.get("action_type") not in {"chat.send", "read.thread"}:
            return False
        return str(row.get("target_actor_id") or row.get("target") or "") == ref_id
    if kind == "thread":
        if row.get("action_type") not in {"chat.send", "read.thread"}:
            return False
        return str(row.get("thread_id") or "") == ref_id
    if kind == "task":
        if row.get("action_type") not in {"task.note", "task.set_owner", "task.set_target"}:
            return False
        return _row_task_id(row) == ref_id
    if kind == "doc":
        if row.get("action_type") not in {"read.doc", "docs.write"}:
            return False
        return _row_doc_id(row) == ref_id
    if kind == "meeting":
        if row.get("action_type") not in {"meeting.propose", "meeting.act"}:
            return False
        return _row_meeting_id(row) == ref_id
    return False


def _split_note_ref(ref: str) -> tuple[str, str]:
    kind, _, ref_id = str(ref).partition(":")
    return kind, ref_id


def _row_doc_id(row: dict[str, Any]) -> str:
    return str(row.get("doc_id") or row.get("slots", {}).get("doc_id") or row.get("metadata", {}).get("doc_id") or "")


def _row_task_id(row: dict[str, Any]) -> str:
    return str(row.get("task_id") or row.get("slots", {}).get("task_id") or "")


def _row_meeting_id(row: dict[str, Any]) -> str:
    return str(row.get("meeting_id") or row.get("slots", {}).get("meeting_id") or "")


def _build_failure_dossiers(
    rubric_lines: list[dict[str, Any]],
    *,
    report: dict[str, Any],
    scenario: dict[str, Any],
    run_record: dict[str, Any],
    run_health: dict[str, Any],
    diagnostics: dict[str, Any],
    critical_path: dict[str, Any],
    agent_trace: list[dict[str, Any]],
    merged_action_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    dossiers: list[dict[str, Any]] = []
    selected_line_ids: set[str] = set()
    if run_health.get("protocol_failure") or report.get("coverage_miss"):
        dossiers.append(
            _build_interruption_dossier(
                report,
                run_record=run_record,
                run_health=run_health,
                diagnostics=diagnostics,
                critical_path=critical_path,
                merged_action_rows=merged_action_rows,
            )
        )
    missed_lines = [line for line in rubric_lines if float(line["weight"]) > float(line["awarded"])]
    critical_lines = sorted(
        [line for line in missed_lines if _is_critical_window_line(line)],
        key=lambda line: (float(line["weight"]) - float(line["awarded"]), float(line["weight"])),
        reverse=True,
    )
    supporting_lines = sorted(
        [line for line in missed_lines if line["id"] not in {item["id"] for item in critical_lines}],
        key=lambda line: (float(line["weight"]) - float(line["awarded"]), float(line["weight"])),
        reverse=True,
    )
    for line in critical_lines:
        if len(dossiers) >= 3:
            break
        selected_line_ids.add(str(line["id"]))
        dossiers.append(
            _build_rubric_failure_dossier(
                line,
                scenario=scenario,
                diagnostics=diagnostics,
                critical_path=critical_path,
                agent_trace=agent_trace,
                merged_action_rows=merged_action_rows,
            )
        )
    for line in supporting_lines:
        if len(dossiers) >= 3:
            break
        if str(line["id"]) in selected_line_ids:
            continue
        selected_line_ids.add(str(line["id"]))
        dossiers.append(
            _build_rubric_failure_dossier(
                line,
                scenario=scenario,
                diagnostics=diagnostics,
                critical_path=critical_path,
                agent_trace=agent_trace,
                merged_action_rows=merged_action_rows,
            )
        )
    return dossiers[:3]


def _build_interruption_dossier(
    report: dict[str, Any],
    *,
    run_record: dict[str, Any],
    run_health: dict[str, Any],
    diagnostics: dict[str, Any],
    critical_path: dict[str, Any],
    merged_action_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    example_action_refs = [row["action_ref"] for row in merged_action_rows if row.get("action_ref")][:3]
    contributing_patterns = _build_contributing_patterns(
        None,
        diagnostics=diagnostics,
        merged_action_rows=merged_action_rows,
        deadline_at=None,
        critical_path_failed=critical_path.get("status") != "critical_path_moved",
    )
    fix_hint = _select_fix_hint(None, contributing_patterns)
    protocol_reason = run_health.get("protocol_failure_reason")
    if protocol_reason:
        why_it_matters = str(protocol_reason)
    elif report.get("coverage_miss"):
        why_it_matters = "Benchmark coverage miss interrupted the run before the TPM could complete the benchmark path."
    else:
        why_it_matters = "Run was interrupted before the TPM could complete the benchmark path."
    headline = why_it_matters.rstrip(".")
    if example_action_refs:
        headline = f"{headline}, after {len(example_action_refs)} successful actions had already been taken."
    else:
        headline = f"{headline}."
    return {
        "id": "run_interruption",
        "kind": "run_interruption",
        "severity": "critical",
        "rubric_line_id": None,
        "title": "Run interrupted before the benchmark path completed",
        "lost_points": round(max(0.0, 100.0 - float(report.get("total_score", 0.0))), 2),
        "deadline_label": None,
        "deadline_at": None,
        "headline": headline,
        "why_it_matters": why_it_matters,
        "signal_refs": [],
        "example_action_refs": example_action_refs,
        "contributing_patterns": contributing_patterns,
        "metrics": {
            "first_signal_at": None,
            "first_action_at": merged_action_rows[0]["time"] if merged_action_rows else None,
            "minutes_from_first_signal_to_deadline": None,
            "actions_before_deadline": None,
            "reads_before_deadline": None,
            "writes_before_deadline": None,
        },
        "deterministic_fix_hint": fix_hint,
    }


def _build_rubric_failure_dossier(
    line: dict[str, Any],
    *,
    scenario: dict[str, Any],
    diagnostics: dict[str, Any],
    critical_path: dict[str, Any],
    agent_trace: list[dict[str, Any]],
    merged_action_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    deadline_at = _line_deadline_at(line, scenario)
    signal_refs = _resolve_signal_refs(line, agent_trace, deadline_at)
    contributing_patterns = _build_contributing_patterns(
        line,
        diagnostics=diagnostics,
        merged_action_rows=merged_action_rows,
        deadline_at=deadline_at,
        critical_path_failed=critical_path.get("status") != "critical_path_moved",
    )
    example_action_refs = _select_example_action_refs(
        line,
        contributing_patterns=contributing_patterns,
        merged_action_rows=merged_action_rows,
        deadline_at=deadline_at,
    )
    metrics = _build_dossier_metrics(
        signal_refs,
        example_action_refs,
        agent_trace=agent_trace,
        merged_action_rows=merged_action_rows,
        deadline_at=deadline_at,
    )
    headline = _build_dossier_headline(line, contributing_patterns, metrics)
    lost_points = round(float(line["weight"]) - float(line["awarded"]), 2)
    kind = "missed_critical_window" if _is_critical_window_line(line) else "supporting_failure"
    fix_hint = _select_fix_hint(line, contributing_patterns)
    return {
        "id": f"failure.{line['id']}",
        "kind": kind,
        "severity": _dossier_severity(kind, lost_points),
        "rubric_line_id": line["id"],
        "title": line["label"],
        "lost_points": lost_points,
        "deadline_label": line.get("deadline_or_window"),
        "deadline_at": deadline_at,
        "headline": headline,
        "why_it_matters": line.get("failure_meaning", line["label"]),
        "signal_refs": signal_refs,
        "example_action_refs": example_action_refs,
        "contributing_patterns": contributing_patterns,
        "metrics": metrics,
        "deterministic_fix_hint": fix_hint,
    }


def _dossier_severity(kind: str, lost_points: float) -> str:
    if kind == "run_interruption" or lost_points >= 20:
        return "critical"
    if lost_points >= 10:
        return "high"
    return "medium"


def _is_critical_window_line(line: dict[str, Any]) -> bool:
    tags = set(line.get("competency_tags", []))
    return "outcome_attainment" in tags or "timing_optionality_preservation" in tags


def _line_deadline_at(line: dict[str, Any], scenario: dict[str, Any]) -> str | None:
    success_predicate = line.get("success_predicate") or {}
    before = success_predicate.get("before") if isinstance(success_predicate, dict) else None
    if isinstance(before, dict):
        explicit = before.get("time")
        if explicit:
            return explicit
    deadline_label = str(line.get("deadline_or_window") or "")
    if deadline_label:
        window = _scenario_window_by_id(scenario, deadline_label)
        if window is not None:
            return str(window.get("end_at") or "") or None
        milestone = _scenario_milestone_by_id(scenario, deadline_label)
        if milestone is not None:
            return str(milestone.get("due_at") or "") or None
    milestone_id = _line_success_milestone_id(line)
    if milestone_id:
        milestone = _scenario_milestone_by_id(scenario, milestone_id)
        if milestone is not None:
            return str(milestone.get("due_at") or "") or None
    task_id = _line_success_task_id(line)
    if task_id:
        for task in scenario.get("world", {}).get("tasks", []):
            if isinstance(task, dict) and task.get("id") == task_id:
                return str(task.get("due_at") or "") or None
    return None


def _scenario_window_by_id(scenario: dict[str, Any], window_id: str) -> dict[str, Any] | None:
    for row in scenario.get("world", {}).get("windows", []):
        if isinstance(row, dict) and row.get("id") == window_id:
            return row
    return None


def _scenario_milestone_by_id(scenario: dict[str, Any], milestone_id: str) -> dict[str, Any] | None:
    for row in scenario.get("world", {}).get("milestones", []):
        if isinstance(row, dict) and row.get("id") == milestone_id:
            return row
    return None


def _line_success_milestone_id(line: dict[str, Any]) -> str | None:
    before = (line.get("success_predicate") or {}).get("before")
    predicate = before.get("predicate") if isinstance(before, dict) else line.get("success_predicate")
    if not isinstance(predicate, dict):
        return None
    milestone_state = predicate.get("milestone_state")
    if isinstance(milestone_state, dict):
        value = milestone_state.get("milestone_id")
        return str(value) if value else None
    all_of = predicate.get("all_of")
    if isinstance(all_of, list):
        for item in all_of:
            if not isinstance(item, dict):
                continue
            milestone_state = item.get("milestone_state")
            if isinstance(milestone_state, dict) and milestone_state.get("milestone_id"):
                return str(milestone_state["milestone_id"])
    return None


def _line_success_task_id(line: dict[str, Any]) -> str | None:
    before = (line.get("success_predicate") or {}).get("before")
    predicate = before.get("predicate") if isinstance(before, dict) else line.get("success_predicate")
    if not isinstance(predicate, dict):
        return None
    task_state = predicate.get("task_true_state")
    if isinstance(task_state, dict):
        value = task_state.get("task_id")
        return str(value) if value else None
    all_of = predicate.get("all_of")
    if isinstance(all_of, list):
        for item in all_of:
            if not isinstance(item, dict):
                continue
            task_state = item.get("task_true_state")
            if isinstance(task_state, dict) and task_state.get("task_id"):
                return str(task_state["task_id"])
    return None


def _build_contributing_patterns(
    line: dict[str, Any] | None,
    *,
    diagnostics: dict[str, Any],
    merged_action_rows: list[dict[str, Any]],
    deadline_at: str | None,
    critical_path_failed: bool,
) -> list[dict[str, Any]]:
    patterns: list[dict[str, Any]] = []
    line_text = _line_text(line)
    concern_is_approval_or_dependency = (
        line is None
        or _is_critical_window_line(line)
        or any(token in line_text for token in ("approval", "scope", "commit", "dependency"))
    )
    if diagnostics["counts"]["approval_before_preconditions"] > 0 and concern_is_approval_or_dependency:
        refs = _sample_action_refs(
            merged_action_rows,
            deadline_at=deadline_at,
            predicate=lambda row: row.get("act_id") == "request.approval",
        )
        patterns.append(
            {
                "kind": "approval_before_preconditions",
                "count": int(diagnostics["counts"]["approval_before_preconditions"]),
                "action_refs": refs,
            }
        )
    if diagnostics["counts"]["unresolved_reply_loops"] > 0:
        patterns.append(
            {
                "kind": "reasking_without_new_information",
                "count": int(diagnostics["counts"]["unresolved_reply_loops"]),
                "action_refs": _reasking_action_refs(merged_action_rows, deadline_at=deadline_at),
            }
        )
    repeated_loops = _relevant_repeated_loops(diagnostics.get("repeated_action_loops", []), deadline_at=deadline_at)
    if repeated_loops:
        action_refs = _unique_refs(ref for loop in repeated_loops for ref in loop.get("action_refs", []))[:3]
        patterns.append(
            {
                "kind": "repeated_coordination_loop",
                "count": int(sum(int(loop.get("count", 0)) for loop in repeated_loops)),
                "action_refs": action_refs,
            }
        )
    if (
        diagnostics["counts"]["reads"]
        and diagnostics["counts"]["writes"] > diagnostics["counts"]["reads"] * 2
        and critical_path_failed
    ):
        patterns.append(
            {
                "kind": "write_heavy_coordination",
                "count": int(diagnostics["counts"]["writes"]),
                "action_refs": _sample_action_refs(
                    merged_action_rows,
                    deadline_at=deadline_at,
                    predicate=lambda row: row.get("action_type") in WRITE_ACTION_TYPES,
                ),
            }
        )
    return patterns


def _select_example_action_refs(
    line: dict[str, Any],
    *,
    contributing_patterns: list[dict[str, Any]],
    merged_action_rows: list[dict[str, Any]],
    deadline_at: str | None,
) -> list[str]:
    priority_refs = _unique_refs(ref for pattern in contributing_patterns for ref in pattern.get("action_refs", []))
    if priority_refs:
        return priority_refs[:3]
    line_text = _line_text(line)
    if any(token in line_text for token in ("approval", "scope", "commit")) or _is_critical_window_line(line):
        refs = _sample_action_refs(
            merged_action_rows,
            deadline_at=deadline_at,
            predicate=lambda row: row.get("act_id") == "request.approval",
        )
        if refs:
            return refs
    refs = _sample_action_refs(
        merged_action_rows,
        deadline_at=deadline_at,
        predicate=lambda row: row.get("action_type") == "chat.send",
    )
    if refs:
        return refs
    return _sample_action_refs(merged_action_rows, deadline_at=deadline_at, predicate=lambda row: True)


def _sample_action_refs(
    merged_action_rows: list[dict[str, Any]],
    *,
    deadline_at: str | None,
    predicate,
) -> list[str]:
    refs: list[str] = []
    for row in merged_action_rows:
        if deadline_at and str(row.get("time")) > deadline_at:
            continue
        if not row.get("action_ref") or not predicate(row):
            continue
        refs.append(str(row["action_ref"]))
        if len(refs) >= 3:
            break
    return refs


def _reasking_action_refs(merged_action_rows: list[dict[str, Any]], *, deadline_at: str | None) -> list[str]:
    seen_pairs: Counter[tuple[str, str]] = Counter()
    refs: list[str] = []
    for row in merged_action_rows:
        if deadline_at and str(row.get("time")) > deadline_at:
            continue
        if row.get("action_type") != "chat.send" or not row.get("target") or not row.get("act_id"):
            continue
        key = (str(row["target"]), str(row["act_id"]))
        seen_pairs[key] += 1
        if seen_pairs[key] >= 2 and row.get("action_ref"):
            refs.append(str(row["action_ref"]))
        if len(refs) >= 3:
            break
    return refs


def _relevant_repeated_loops(repeated_loops: list[dict[str, Any]], *, deadline_at: str | None) -> list[dict[str, Any]]:
    relevant = []
    for loop in repeated_loops:
        action_times = [str(value) for value in loop.get("action_times", [])]
        if deadline_at and action_times and min(action_times) > deadline_at:
            continue
        relevant.append(loop)
    return relevant


def _resolve_signal_refs(line: dict[str, Any], agent_trace: list[dict[str, Any]], deadline_at: str | None) -> list[str]:
    if not agent_trace:
        return []
    keywords = _keywords_for_text(_line_text(line))
    candidates = []
    for row in agent_trace:
        event_type = str(row.get("event_type"))
        if event_type not in SIGNAL_EVENT_TYPES or row.get("id") is None:
            continue
        if deadline_at and str(row.get("at")) > deadline_at:
            continue
        summary = str(row.get("summary") or "")
        payload = json.dumps(row.get("payload") or {}, sort_keys=True)
        candidate_text = f"{summary} {payload}".lower()
        overlap = sum(1 for keyword in keywords if keyword in candidate_text)
        candidates.append(
            (
                -overlap,
                SIGNAL_EVENT_TYPES[event_type],
                str(row.get("at")),
                int(row["id"]),
                row,
            )
        )
    if not candidates:
        return []
    selected = [item[-1] for item in sorted(candidates)[:3]]
    return [f"event:{row['id']}" for row in selected]


def _build_dossier_metrics(
    signal_refs: list[str],
    example_action_refs: list[str],
    *,
    agent_trace: list[dict[str, Any]],
    merged_action_rows: list[dict[str, Any]],
    deadline_at: str | None,
) -> dict[str, Any]:
    event_index = {f"event:{row['id']}": row for row in agent_trace if row.get("id") is not None}
    action_index = {str(row["action_ref"]): row for row in merged_action_rows if row.get("action_ref")}
    first_signal_at = None
    if signal_refs:
        signal_times = [str(event_index[ref]["at"]) for ref in signal_refs if ref in event_index]
        if signal_times:
            first_signal_at = min(signal_times)
    first_action_at = None
    if example_action_refs:
        action_times = [str(action_index[ref]["time"]) for ref in example_action_refs if ref in action_index]
        if action_times:
            first_action_at = min(action_times)
    if deadline_at:
        window_rows = [row for row in merged_action_rows if row.get("action_ref") and str(row.get("time")) <= deadline_at]
        actions_before_deadline = len(window_rows)
        reads_before_deadline = sum(1 for row in window_rows if _is_read_action(row))
        writes_before_deadline = sum(1 for row in window_rows if _is_write_action(row))
    else:
        actions_before_deadline = None
        reads_before_deadline = None
        writes_before_deadline = None
    minutes_from_first_signal_to_deadline = None
    if first_signal_at and deadline_at:
        minutes_from_first_signal_to_deadline = max(
            0,
            int((from_iso(deadline_at) - from_iso(first_signal_at)).total_seconds() // 60),
        )
    return {
        "first_signal_at": first_signal_at,
        "first_action_at": first_action_at,
        "minutes_from_first_signal_to_deadline": minutes_from_first_signal_to_deadline,
        "actions_before_deadline": actions_before_deadline,
        "reads_before_deadline": reads_before_deadline,
        "writes_before_deadline": writes_before_deadline,
    }


def _build_dossier_headline(
    line: dict[str, Any],
    contributing_patterns: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> str:
    base = str(line.get("failure_meaning", line["label"])).rstrip(".")
    if contributing_patterns:
        first_pattern = contributing_patterns[0]
        clauses = {
            "approval_before_preconditions": f"after {first_pattern['count']} premature approval asks",
            "reasking_without_new_information": f"after {first_pattern['count']} re-asks without incorporating replies",
            "repeated_coordination_loop": f"after repeating the same coordination loop {first_pattern['count']} times",
            "write_heavy_coordination": f"after spending {first_pattern['count']} write actions without enough learning",
        }
        clause = clauses.get(first_pattern["kind"])
        if clause:
            return f"{base}, {clause}."
    if metrics.get("first_signal_at"):
        return f"{base}, despite a first visible signal at {metrics['first_signal_at']}."
    return f"{base}."


def _select_fix_hint(line: dict[str, Any] | None, contributing_patterns: list[dict[str, Any]]) -> str:
    pattern_kinds = {item["kind"] for item in contributing_patterns}
    if "approval_before_preconditions" in pattern_kinds:
        return "defer approval until preconditions are met"
    if "reasking_without_new_information" in pattern_kinds:
        return "consume and incorporate replies before re-asking"
    line_text = _line_text(line)
    if any(token in line_text for token in ("scope", "aligned", "decision", "tradeoff")) or (line and _is_critical_window_line(line)):
        return "force the path decision before downstream coordination"
    if any(token in line_text for token in ("commit", "eta", "approval")):
        return "convert alignment into explicit commitment immediately"
    return "reallocate turns to the gating path"


def _project_key_failures(
    root_cause_findings: list[dict[str, Any]],
    *,
    rubric_failure_dossiers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    projected = []
    for finding in root_cause_findings[:4]:
        evidence_refs = _unique_refs(finding.get("signal_refs", []) + finding.get("action_refs", []))
        projected.append(
            {
                "id": finding["id"],
                "title": finding["title"],
                "summary": finding["headline"],
                "evidence_refs": evidence_refs,
                "lost_points": finding["lost_points_total"],
            }
        )
    if projected:
        return projected
    for dossier in rubric_failure_dossiers:
        evidence_refs = _unique_refs(dossier.get("signal_refs", []) + dossier.get("example_action_refs", []))
        projected.append(
            {
                "id": dossier.get("rubric_line_id") or dossier["id"],
                "title": dossier["title"],
                "summary": dossier["headline"],
                "evidence_refs": evidence_refs,
                "lost_points": dossier["lost_points"],
            }
        )
    return projected[:4]


def _project_improvement_opportunities(
    root_cause_findings: list[dict[str, Any]],
    *,
    rubric_failure_dossiers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    opportunities: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for finding in root_cause_findings:
        title = str(finding.get("title") or "")
        counterfactual = str(finding.get("counterfactual_step") or "")
        if not title or not counterfactual or title in seen_titles:
            continue
        seen_titles.add(title)
        opportunities.append({"title": title, "summary": counterfactual})
    if opportunities:
        return opportunities[:4]
    seen_hints: set[str] = set()
    for dossier in rubric_failure_dossiers:
        hint = str(dossier["deterministic_fix_hint"])
        if hint in seen_hints:
            continue
        seen_hints.add(hint)
        opportunities.append({"title": FIX_HINT_LABELS[hint], "summary": hint})
    return opportunities[:4]


def _is_read_action(row: dict[str, Any]) -> bool:
    return str(row.get("action_type") or "").startswith(READ_ACTION_PREFIX)


def _is_write_action(row: dict[str, Any]) -> bool:
    return str(row.get("action_type") or "") in WRITE_ACTION_TYPES


def _line_text(line: dict[str, Any] | None) -> str:
    if not line:
        return ""
    return " ".join(
        str(line.get(key, ""))
        for key in ("id", "label", "failure_meaning", "measurement_rationale", "deadline_or_window")
    ).lower()


def _keywords_for_text(text: str) -> list[str]:
    tokens = [token for token in re.findall(r"[a-z0-9_]+", text.lower()) if len(token) >= 4]
    stopwords = {
        "that",
        "with",
        "from",
        "this",
        "were",
        "when",
        "would",
        "into",
        "before",
        "after",
        "still",
        "enough",
        "their",
        "there",
        "which",
        "them",
        "while",
        "make",
        "made",
        "time",
        "timing",
    }
    return [token for token in tokens if token not in stopwords][:12]


def _unique_refs(refs: Iterable[str]) -> list[str]:
    ordered: list[str] = []
    for ref in refs:
        if ref and ref not in ordered:
            ordered.append(ref)
    return ordered


def _key_successes(rubric_lines: list[dict[str, Any]], dimension_scores: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    successes = [
        {
            "id": line["id"],
            "title": line["label"],
            "summary": line.get("success_meaning", line["label"]),
            "evidence_refs": line.get("evidence_refs", []),
            "awarded": line["awarded"],
            "weight": line["weight"],
        }
        for line in rubric_lines
        if float(line["awarded"]) > 0
    ]
    successes.sort(key=lambda item: (float(item["awarded"]) / float(item["weight"]), float(item["awarded"])), reverse=True)
    if successes:
        return successes[:5]
    strongest_dimensions = sorted(dimension_scores.values(), key=lambda item: item["score"], reverse=True)
    return [
        {
            "id": item["id"],
            "title": item["label"],
            "summary": f"Relative strength in {item['label'].lower()}.",
            "evidence_refs": _dimension_evidence_refs(item),
            "awarded": item["awarded"],
            "weight": item["weight"],
        }
        for item in strongest_dimensions[:2]
    ]


def _key_failures(rubric_lines: list[dict[str, Any]], diagnostics: dict[str, Any]) -> list[dict[str, Any]]:
    failures = [
        {
            "id": line["id"],
            "title": line["label"],
            "summary": line.get("failure_meaning", line["label"]),
            "evidence_refs": line.get("evidence_refs", []),
            "lost_points": round(float(line["weight"]) - float(line["awarded"]), 2),
        }
        for line in rubric_lines
        if float(line["weight"]) > float(line["awarded"])
    ]
    failures.sort(key=lambda item: item["lost_points"], reverse=True)
    if diagnostics["counts"]["repeated_action_loops"] > 0:
        failures.append(
            {
                "id": "behavior.repeated_loops",
                "title": "Repeated low-leverage coordination loops",
                "summary": "The TPM repeated materially similar asks without enough new evidence or stakeholder state change in between.",
                "evidence_refs": [],
                "lost_points": 0.0,
            }
        )
    return failures[:6]


def _improvement_opportunities(rubric_lines: list[dict[str, Any]], diagnostics: dict[str, Any]) -> list[dict[str, Any]]:
    opportunities: list[dict[str, Any]] = []
    if diagnostics["counts"]["approval_before_preconditions"] > 0:
        opportunities.append(
            {
                "title": "Sequence approval requests after the prerequisite decision is aligned",
                "summary": "Ask for approval after the TPM has established the feasible path and satisfied the visible preconditions, not before.",
            }
        )
    if diagnostics["counts"]["repeated_action_loops"] > 0 or diagnostics["counts"]["unresolved_reply_loops"] > 0:
        opportunities.append(
            {
                "title": "Use new evidence before repeating the same ask",
                "summary": "Read the latest stakeholder response, update the plan, and only then follow up; repetition without new information burns turns without changing state.",
            }
        )
    if diagnostics["counts"]["artifact_churn"] >= 3:
        opportunities.append(
            {
                "title": "Reduce shared-document churn and spend those turns on coordination",
                "summary": "In this benchmark, shared docs are support artifacts. They rarely substitute for getting the right owner, approver, or sponsor aligned.",
            }
        )
    if not opportunities:
        biggest_gap = max(
            rubric_lines,
            key=lambda line: float(line["weight"]) - float(line["awarded"]),
            default=None,
        )
        if biggest_gap is not None:
            opportunities.append(
                {
                    "title": f"Improve {biggest_gap['label'].lower()}",
                    "summary": biggest_gap.get("failure_meaning", biggest_gap["label"]),
                }
            )
    return opportunities[:4]


def _select_visible_trace(agent_trace: list[dict[str, Any]], omniscient_trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered_omniscient = [
        row
        for row in omniscient_trace
        if row.get("event_type") in SIGNAL_EVENT_TYPES
        or row.get("visibility") == "agent"
        or row.get("actor_id") == "tpm"
        or (
            isinstance(row.get("payload"), dict)
            and str(row.get("payload", {}).get("observer_id") or "") == "tpm"
        )
    ]
    if not agent_trace:
        return filtered_omniscient
    return _unique_event_rows(agent_trace + filtered_omniscient)


def _normalize_excerpt(text: str | None, *, limit: int = 200) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip() + "..."


def _row_target_actor_id(row: dict[str, Any]) -> str | None:
    metadata = row.get("metadata", {}) if isinstance(row.get("metadata"), dict) else {}
    slots = row.get("slots", {}) if isinstance(row.get("slots"), dict) else {}
    for candidate in (
        row.get("target_actor_id"),
        metadata.get("target_actor_id"),
        row.get("target"),
        slots.get("thread_id"),
        metadata.get("thread_id"),
    ):
        if candidate:
            return str(candidate)
    return None


def _action_intent_family(row: dict[str, Any]) -> str:
    action_type = str(row.get("action_type") or "")
    act_id = str(row.get("act_id") or "")
    task_id = str(row.get("task_id") or row.get("slots", {}).get("task_id") or "")
    target_actor_id = _row_target_actor_id(row)
    if action_type.startswith("wait."):
        return "wait"
    if _is_read_action(row):
        return "read_only"
    if action_type == "chat.send":
        if act_id in {"request.scope_tradeoff", "negotiate.scope"}:
            return "scope_tradeoff"
        if act_id in {"inform.decision", "inform.risk"}:
            return "decision_alignment"
        if act_id in {"request.review", "request.approval"}:
            return "approval_request"
        if act_id == "request.feasibility":
            if task_id == "runbook_readiness" or target_actor_id == "mia":
                return "runbook_cleanup"
            return "feasibility_alignment"
        if "eta" in act_id:
            return "eta_request"
        if task_id == "runbook_readiness":
            return "runbook_cleanup"
        if act_id == "request.clarification":
            return "clarification_loop"
        if act_id.startswith("inform.") or act_id.startswith("status."):
            return "status_only"
    if action_type == "docs.write" and ("runbook" in task_id or "runbook" in _normalize_excerpt(row.get("body")).lower()):
        return "runbook_cleanup"
    return "other"


def _action_summary(row: dict[str, Any]) -> str:
    intent_family = _action_intent_family(row)
    target = _row_target_actor_id(row)
    action_type = str(row.get("action_type") or "")
    act_id = str(row.get("act_id") or "")
    parts = [part for part in [action_type or None, act_id or None] if part]
    summary = "/".join(parts) if parts else "action"
    if target:
        summary += f" -> {target}"
    if intent_family not in {"other", "read_only", "wait"}:
        summary += f" [{intent_family}]"
    body = _normalize_excerpt(row.get("body"))
    if body:
        summary += f": {body}"
    return summary


def _signal_id_from_event(row: dict[str, Any]) -> str | None:
    payload = row.get("payload", {})
    if not isinstance(payload, dict):
        return None
    value = payload.get("fact_id") or payload.get("signal_id")
    return str(value) if value else None


def _signal_kind(fact: dict[str, Any] | None, event_type: str | None) -> str:
    metadata = fact.get("metadata", {}) if isinstance(fact, dict) else {}
    if isinstance(metadata, dict) and (
        metadata.get("fact_kind") == "actor_private_driver"
        or metadata.get("coordination_implication")
        or metadata.get("driver_type")
    ):
        return "driver"
    if event_type == "agenda_signal.observed":
        return "agenda"
    return "fact"


def _signal_criticality(signal_id: str, fact: dict[str, Any] | None) -> str:
    metadata = fact.get("metadata", {}) if isinstance(fact, dict) else {}
    text = " ".join(
        [
            signal_id,
            str(fact.get("label", "") if isinstance(fact, dict) else ""),
            str(fact.get("description", "") if isinstance(fact, dict) else ""),
            str(metadata.get("coordination_implication", "") if isinstance(metadata, dict) else ""),
        ]
    ).lower()
    if any(token in text for token in ("runbook", "checklist", "ops support")) and not any(
        token in text for token in ("approval", "scope", "rollout", "launch", "eta")
    ):
        return "supporting"
    if isinstance(metadata, dict) and (
        metadata.get("coordination_implication")
        or metadata.get("fact_kind") == "actor_private_driver"
        or metadata.get("owner_actor_id")
    ):
        return "critical"
    if any(token in text for token in ("approval", "scope", "rollout", "launch", "review", "eta", "credible")):
        return "critical"
    return "supporting"


def _deadline_from_label_or_task(
    scenario: dict[str, Any],
    *,
    deadline_label: str | None = None,
    task_id: str | None = None,
) -> str | None:
    if deadline_label:
        window = _scenario_window_by_id(scenario, deadline_label)
        if window is not None and window.get("end_at"):
            return str(window["end_at"])
        milestone = _scenario_milestone_by_id(scenario, deadline_label)
        if milestone is not None and milestone.get("due_at"):
            return str(milestone["due_at"])
    if task_id:
        for task in scenario.get("world", {}).get("tasks", []):
            if isinstance(task, dict) and task.get("id") == task_id and task.get("due_at"):
                return str(task["due_at"])
    return None


def _driver_signal_expectations(metadata: dict[str, Any], text: str, scenario: dict[str, Any]) -> dict[str, Any]:
    owner_actor_id = str(metadata.get("owner_actor_id") or "")
    expected_actors = [owner_actor_id] if owner_actor_id else []
    action_families: list[str] = []
    deadline_label = None
    if any(token in text for token in ("descop", "tradeoff", "credible plan", "narrower credible plan", "engaged early")):
        action_families.extend(["scope_tradeoff", "decision_alignment", "approval_request"])
        deadline_label = "scope_alignment_cutoff"
    if any(token in text for token in ("fake eta", "fake date", "fake dates", "honest feasibility", "blocker", "not credible", "overloaded")):
        action_families.extend(["feasibility_alignment", "scope_tradeoff"])
        deadline_label = deadline_label or "scope_alignment_cutoff"
    if any(token in text for token in ("security review", "review slot", "review queue", "useful slot")):
        action_families.extend(["approval_request"])
        deadline_label = deadline_label or "security_cutoff"
    if any(token in text for token in ("customer plan", "customer confidence", "external story", "wednesday noon", "stable plan early")):
        action_families.extend(["decision_alignment"])
        deadline_label = "customer_plan_cutoff"
    if not action_families:
        action_families.append("decision_alignment")
    return {
        "expected_action_families": _unique_refs(action_families),
        "expected_actors": expected_actors,
        "deadline_label": deadline_label,
        "deadline_at": _deadline_from_label_or_task(scenario, deadline_label=deadline_label) if deadline_label else None,
    }


def _signal_expectations(signal_id: str, fact: dict[str, Any] | None, scenario: dict[str, Any]) -> dict[str, Any]:
    metadata = fact.get("metadata", {}) if isinstance(fact, dict) else {}
    text = " ".join(
        [
            signal_id,
            str(fact.get("label", "") if isinstance(fact, dict) else ""),
            str(fact.get("description", "") if isinstance(fact, dict) else ""),
            str(metadata.get("coordination_implication", "") if isinstance(metadata, dict) else ""),
        ]
    ).lower()
    if signal_id == "approval_required" or ("approval" in text and "review" in text):
        return {
            "expected_action_families": ["approval_request"],
            "expected_actors": ["ivy"],
            "deadline_label": "approval_cutoff",
            "deadline_at": _deadline_from_label_or_task(scenario, deadline_label="approval_cutoff"),
        }
    if signal_id == "ops_checklist_available" or any(token in text for token in ("ops", "checklist", "runbook")):
        return {
            "expected_action_families": ["runbook_cleanup"],
            "expected_actors": ["mia"],
            "deadline_label": "runbook_readiness",
            "deadline_at": _deadline_from_label_or_task(scenario, task_id="runbook_readiness"),
        }
    if signal_id in {"dana_accepts_staged_if_early", "full_rollout_infeasible", "leo_rejects_fake_rollout_dates"}:
        return {
            "expected_action_families": ["scope_tradeoff", "decision_alignment"],
            "expected_actors": ["dana", "leo"],
            "deadline_label": "scope_alignment_cutoff",
            "deadline_at": _deadline_from_label_or_task(scenario, deadline_label="scope_alignment_cutoff"),
        }
    if isinstance(metadata, dict) and metadata.get("fact_kind") == "actor_private_driver":
        return _driver_signal_expectations(metadata, text, scenario)
    expected_actors = [str(metadata.get("owner_actor_id"))] if isinstance(metadata, dict) and metadata.get("owner_actor_id") else []
    return {
        "expected_action_families": [],
        "expected_actors": expected_actors,
        "deadline_label": None,
        "deadline_at": None,
    }


def _find_matching_actions(
    merged_action_rows: list[dict[str, Any]],
    *,
    after_at: str | None,
    deadline_at: str | None,
    expected_action_families: list[str],
    expected_actors: list[str],
    max_actions_after: int | None = None,
    max_refs: int = 3,
) -> list[str]:
    refs: list[str] = []
    actions_after = 0
    for row in merged_action_rows:
        if not row.get("step_succeeded") or not row.get("action_ref"):
            continue
        when = str(row.get("time") or "")
        if after_at and when <= after_at:
            continue
        if deadline_at and when > deadline_at:
            continue
        actions_after += 1
        if max_actions_after is not None and actions_after > max_actions_after:
            break
        intent_family = _action_intent_family(row)
        if expected_action_families and intent_family not in expected_action_families:
            continue
        target_actor_id = _row_target_actor_id(row)
        if expected_actors and target_actor_id and target_actor_id not in expected_actors:
            continue
        if expected_actors and target_actor_id is None and row.get("action_type") == "chat.send":
            continue
        refs.append(str(row["action_ref"]))
        if len(refs) >= max_refs:
            break
    return refs


def _build_signal_coverage(
    scenario: dict[str, Any],
    visible_trace: list[dict[str, Any]],
    merged_action_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    facts = {
        str(fact["id"]): fact
        for fact in scenario.get("world", {}).get("facts", [])
        if isinstance(fact, dict) and fact.get("id")
    }
    surfaced_by_signal: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in visible_trace:
        event_type = str(row.get("event_type"))
        if event_type not in {"fact_signal", "agenda_signal.observed"}:
            continue
        signal_id = _signal_id_from_event(row)
        if signal_id:
            surfaced_by_signal[signal_id].append(row)
    candidate_signal_ids: set[str] = set(surfaced_by_signal)
    for signal_id, fact in facts.items():
        metadata = fact.get("metadata", {}) if isinstance(fact, dict) else {}
        text = " ".join([signal_id, str(fact.get("label", "")), str(fact.get("description", ""))]).lower()
        if surfaced_by_signal.get(signal_id) or (
            isinstance(metadata, dict)
            and (metadata.get("coordination_implication") or metadata.get("fact_kind") == "actor_private_driver")
        ) or any(token in text for token in ("approval", "scope", "rollout", "launch", "eta", "checklist", "runbook")):
            candidate_signal_ids.add(signal_id)
    rows: list[dict[str, Any]] = []
    for signal_id in sorted(candidate_signal_ids):
        fact = facts.get(signal_id)
        events = sorted(surfaced_by_signal.get(signal_id, []), key=lambda row: (str(row.get("at")), int(row.get("id") or 0)))
        first_event = events[0] if events else None
        kind = _signal_kind(fact, str(first_event.get("event_type")) if first_event else None)
        criticality = _signal_criticality(signal_id, fact)
        expectations = _signal_expectations(signal_id, fact, scenario)
        conversion_refs = _find_matching_actions(
            merged_action_rows,
            after_at=str(first_event.get("at")) if first_event else None,
            deadline_at=expectations.get("deadline_at"),
            expected_action_families=list(expectations.get("expected_action_families") or []),
            expected_actors=list(expectations.get("expected_actors") or []),
            max_actions_after=3,
        ) if first_event else []
        rows.append(
            {
                "signal_id": signal_id,
                "label": str(fact.get("label", signal_id)) if isinstance(fact, dict) else signal_id,
                "kind": kind,
                "criticality": criticality,
                "surfaced": bool(first_event),
                "first_surfaced_at": str(first_event.get("at")) if first_event else None,
                "surface_event_ref": f"event:{first_event['id']}" if first_event and first_event.get("id") is not None else None,
                "surface_source_ref": (
                    str(first_event.get("payload", {}).get("source_ref"))
                    if first_event and isinstance(first_event.get("payload"), dict) and first_event.get("payload", {}).get("source_ref")
                    else None
                ),
                "converted_to_plan_change": bool(conversion_refs),
                "conversion_action_refs": conversion_refs,
                "expected_action_families": list(expectations.get("expected_action_families") or []),
                "expected_actors": list(expectations.get("expected_actors") or []),
                "deadline_label": expectations.get("deadline_label"),
                "deadline_at": expectations.get("deadline_at"),
            }
        )
    rows.sort(key=lambda row: (row["criticality"] != "critical", row["signal_id"]))
    critical_rows = [row for row in rows if row["criticality"] == "critical"]
    critical_observed = [row for row in critical_rows if row["surfaced"]]
    critical_converted = [row for row in critical_observed if row["converted_to_plan_change"]]
    critical_not_observed = [row for row in critical_rows if not row["surfaced"]]
    critical_observed_not_converted = [row for row in critical_observed if not row["converted_to_plan_change"]]
    driver_rows = [row for row in rows if row["kind"] == "driver"]
    driver_observed = [row for row in driver_rows if row["surfaced"]]
    driver_converted = [row for row in driver_observed if row["converted_to_plan_change"]]
    driver_not_observed = [row for row in driver_rows if not row["surfaced"]]
    driver_observed_not_converted = [row for row in driver_observed if not row["converted_to_plan_change"]]
    return {
        "signals": rows,
        "summary_metrics": {
            "critical_surfaced": len(critical_observed),
            "critical_observed": len(critical_observed),
            "critical_unsurfaced": [row["signal_id"] for row in critical_not_observed],
            "critical_not_observed": [row["signal_id"] for row in critical_not_observed],
            "critical_converted": len(critical_converted),
            "critical_observed_not_converted": [row["signal_id"] for row in critical_observed_not_converted],
            "driver_total": len(driver_rows),
            "driver_surfaced": len(driver_observed),
            "driver_not_observed": [row["signal_id"] for row in driver_not_observed],
            "driver_converted": len(driver_converted),
            "driver_observed_not_converted": [row["signal_id"] for row in driver_observed_not_converted],
        },
    }


def _actor_decision_rights(actor: dict[str, Any]) -> list[str]:
    profile = actor.get("authority_profile", {}) if isinstance(actor, dict) else {}
    if not isinstance(profile, dict):
        return []
    return sorted(key for key, value in profile.items() if value)


def _is_critical_actor(actor: dict[str, Any]) -> bool:
    if not isinstance(actor, dict):
        return False
    if actor.get("id") == "tpm":
        return False
    if actor.get("coordination_template") in {"sponsor", "critical_path_owner", "cross_functional_dependency_owner"}:
        return True
    rights = set(_actor_decision_rights(actor))
    return bool({"can_approve_scope", "can_commit_eta", "can_grant_review"} & rights)


def _relevant_deadlines_for_actor(actor: dict[str, Any], scenario: dict[str, Any]) -> list[dict[str, str]]:
    actor_id = str(actor.get("id") or "")
    output: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for task in scenario.get("world", {}).get("tasks", []):
        if not isinstance(task, dict) or task.get("owner_id") != actor_id or not task.get("due_at"):
            continue
        critical = bool((task.get("metadata") or {}).get("critical")) if isinstance(task.get("metadata"), dict) else False
        if not critical:
            continue
        entry_id = f"task:{task['id']}"
        if entry_id in seen_ids:
            continue
        seen_ids.add(entry_id)
        output.append({"id": entry_id, "label": str(task.get("title") or task["id"]), "at": str(task["due_at"])})
    rights = set(_actor_decision_rights(actor))
    mappings = [
        ("approval_cutoff", {"can_grant_review"}),
        ("scope_alignment_cutoff", {"can_approve_scope", "can_commit_eta"}),
        ("rollout_ready", {"can_commit_eta"}),
    ]
    for deadline_label, required_rights in mappings:
        if not rights & required_rights:
            continue
        deadline_at = _deadline_from_label_or_task(scenario, deadline_label=deadline_label)
        if not deadline_at:
            continue
        if deadline_label in seen_ids:
            continue
        seen_ids.add(deadline_label)
        output.append({"id": deadline_label, "label": deadline_label, "at": deadline_at})
    output.sort(key=lambda row: row["at"])
    return output


def _message_is_direct_question(message_row: dict[str, Any]) -> bool:
    act_id = str(message_row.get("act_id") or "")
    body = str(message_row.get("body") or "")
    return act_id.startswith("request.") or "?" in body


def _has_outbound_after(actor_id: str, at: str, merged_action_rows: list[dict[str, Any]]) -> bool:
    for row in merged_action_rows:
        if not row.get("step_succeeded") or row.get("action_type") != "chat.send":
            continue
        if _row_target_actor_id(row) == actor_id and str(row.get("time") or "") > at:
            return True
    return False


def _build_stakeholder_engagement(
    scenario: dict[str, Any],
    message_rows: list[dict[str, Any]],
    merged_action_rows: list[dict[str, Any]],
    visible_trace: list[dict[str, Any]],
) -> dict[str, Any]:
    actors = [
        actor
        for actor in scenario.get("world", {}).get("actors", [])
        if isinstance(actor, dict) and actor.get("id") and actor.get("id") != "tpm"
    ]
    rows: list[dict[str, Any]] = []
    for actor in actors:
        actor_id = str(actor["id"])
        inbound = [row for row in message_rows if str(row.get("sender_id")) == actor_id]
        outbound = [
            row
            for row in merged_action_rows
            if row.get("step_succeeded") and row.get("action_type") == "chat.send" and _row_target_actor_id(row) == actor_id
        ]
        reads = [
            row
            for row in merged_action_rows
            if row.get("step_succeeded") and row.get("action_type") == "read.thread" and _row_target_actor_id(row) == actor_id
        ]
        cue_times = [str(row.get("created_at")) for row in inbound if row.get("created_at")]
        for event in visible_trace:
            payload = event.get("payload", {}) if isinstance(event.get("payload"), dict) else {}
            if str(payload.get("owner_actor_id") or "") == actor_id or str(event.get("actor_id") or "") == actor_id:
                if event.get("event_type") in SIGNAL_EVENT_TYPES and event.get("at"):
                    cue_times.append(str(event["at"]))
        direct_question_refs = [
            f"message:{row['id']}"
            for row in inbound
            if _message_is_direct_question(row) and not _has_outbound_after(actor_id, str(row.get("created_at") or ""), merged_action_rows)
        ]
        relevant_deadlines = _relevant_deadlines_for_actor(actor, scenario)
        earliest_deadline = relevant_deadlines[0]["at"] if relevant_deadlines else None
        first_outbound_at = min((str(row.get("time")) for row in outbound if row.get("time")), default=None)
        engaged_before = bool(first_outbound_at and (earliest_deadline is None or first_outbound_at <= earliest_deadline))
        criticality = "critical" if _is_critical_actor(actor) else "supporting"
        notes: list[str] = []
        if criticality == "critical" and not outbound:
            notes.append("Critical stakeholder was never contacted.")
        if direct_question_refs:
            notes.append("Left at least one direct stakeholder question unanswered.")
        rows.append(
            {
                "actor_id": actor_id,
                "name": actor.get("name"),
                "role": actor.get("org_role"),
                "decision_rights": _actor_decision_rights(actor),
                "relevant_deadlines": relevant_deadlines,
                "first_cue_at": min(cue_times) if cue_times else None,
                "first_read_at": min((str(row.get("time")) for row in reads if row.get("time")), default=None),
                "first_outbound_at": first_outbound_at,
                "outbound_count": len(outbound),
                "inbound_count": len(inbound),
                "unanswered_direct_questions": direct_question_refs,
                "engaged_before_relevant_deadline": engaged_before,
                "criticality_to_outcome": criticality,
                "notes": notes,
            }
        )
    rows.sort(key=lambda row: (row["criticality_to_outcome"] != "critical", row["actor_id"]))
    total_outbound = sum(int(row["outbound_count"]) for row in rows)
    top_contacted = max(rows, key=lambda row: int(row["outbound_count"]), default=None)
    top_contacted_share = round((int(top_contacted["outbound_count"]) / total_outbound), 3) if top_contacted and total_outbound else 0.0
    summary_metrics = {
        "top_contacted_actor_id": top_contacted["actor_id"] if top_contacted else None,
        "top_contacted_actor_share": top_contacted_share,
        "critical_actors_never_contacted": [
            row["actor_id"] for row in rows if row["criticality_to_outcome"] == "critical" and int(row["outbound_count"]) == 0
        ],
        "critical_actors_contacted_after_deadline": [
            row["actor_id"]
            for row in rows
            if row["criticality_to_outcome"] == "critical"
            and row["first_outbound_at"]
            and row["relevant_deadlines"]
            and row["first_outbound_at"] > row["relevant_deadlines"][0]["at"]
        ],
        "direct_questions_left_unanswered": _unique_refs(
            ref for row in rows for ref in row.get("unanswered_direct_questions", [])
        ),
    }
    return {"actors": rows, "summary_metrics": summary_metrics}


def _line_recoverability_key(line: dict[str, Any]) -> str | None:
    return _line_success_milestone_id(line) or _line_success_task_id(line)


def _build_window_scorecards(
    report: dict[str, Any],
    scenario: dict[str, Any],
    rubric_lines: list[dict[str, Any]],
    merged_action_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    scorecards: list[dict[str, Any]] = []
    outcome_lines = [line for line in rubric_lines if _is_critical_window_line(line)]
    for line in sorted(outcome_lines, key=lambda item: (_line_deadline_at(item, scenario) or "", item["id"])):
        deadline_at = _line_deadline_at(line, scenario)
        window = _scenario_window_by_id(scenario, str(line.get("deadline_or_window") or ""))
        start_at = str(window.get("start_at")) if isinstance(window, dict) and window.get("start_at") else scenario.get("start_at")
        relevant_rows = [
            row for row in merged_action_rows if row.get("step_succeeded") and deadline_at and str(row.get("time")) <= deadline_at
        ] if deadline_at else [row for row in merged_action_rows if row.get("step_succeeded")]
        outbound_by_actor = Counter(
            _row_target_actor_id(row)
            for row in relevant_rows
            if row.get("action_type") == "chat.send" and _row_target_actor_id(row)
        )
        top_action_families = Counter(_action_intent_family(row) for row in relevant_rows)
        recoverability_key = _line_recoverability_key(line)
        recoverability_status = str(report.get("recoverability", {}).get(recoverability_key, "unknown"))
        achieved = float(line["awarded"]) >= float(line["weight"])
        scorecards.append(
            {
                "window_id": str(line.get("deadline_or_window") or line["id"]),
                "title": str(window.get("title") if isinstance(window, dict) else line["label"]),
                "start_at": start_at,
                "end_at": deadline_at,
                "goal": line["label"],
                "required_state_change": line.get("success_meaning", line.get("measurement_rationale", line["label"])),
                "recoverability_status": recoverability_status,
                "actions_taken": len(relevant_rows),
                "reads": sum(1 for row in relevant_rows if _is_read_action(row)),
                "writes": sum(1 for row in relevant_rows if _is_write_action(row)),
                "waits": sum(1 for row in relevant_rows if str(row.get("action_type") or "").startswith("wait.")),
                "outbound_by_actor": dict(sorted((actor_id, count) for actor_id, count in outbound_by_actor.items() if actor_id)),
                "top_action_families": [
                    {"intent_family": family, "count": count}
                    for family, count in sorted(
                        top_action_families.items(),
                        key=lambda item: (item[1], -INTENT_FAMILY_PRIORITIES.get(item[0], 99)),
                        reverse=True,
                    )[:5]
                ],
                "actor_coverage_before_deadline": sorted(outbound_by_actor),
                "state_before_deadline": {
                    "achieved": achieved,
                    "awarded": float(line["awarded"]),
                    "weight": float(line["weight"]),
                },
                "state_achieved": {
                    "achieved": achieved,
                    "rubric_line_id": line["id"],
                },
                "miss_reason": None if achieved else line.get("failure_meaning", line["label"]),
            }
        )
    return scorecards


def _step_summaries_after(merged_action_rows: list[dict[str, Any]], at: str, *, limit: int = 3) -> list[dict[str, Any]]:
    rows = [
        row
        for row in merged_action_rows
        if row.get("step_succeeded") and row.get("time") and str(row.get("time")) > at
    ]
    output = []
    for row in rows[:limit]:
        output.append(
            {
                "action_ref": row.get("action_ref"),
                "at": row.get("time"),
                "summary": _action_summary(row),
                "target_actor_id": _row_target_actor_id(row),
                "intent_family": _action_intent_family(row),
            }
        )
    return output


def _build_missed_opportunities(
    scenario: dict[str, Any],
    visible_trace: list[dict[str, Any]],
    message_rows: list[dict[str, Any]],
    merged_action_rows: list[dict[str, Any]],
    stakeholder_engagement: dict[str, Any],
    signal_coverage: dict[str, Any],
    window_scorecards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    stakeholders_by_id = {row["actor_id"]: row for row in stakeholder_engagement.get("actors", [])}
    missed: list[dict[str, Any]] = []
    seen_cues: set[str] = set()
    for message_row in message_rows:
        actor_id = str(message_row.get("sender_id") or "")
        stakeholder = stakeholders_by_id.get(actor_id)
        if not stakeholder or stakeholder.get("criticality_to_outcome") != "critical":
            continue
        if not _message_is_direct_question(message_row):
            continue
        cue_ref = f"message:{message_row['id']}"
        if cue_ref in seen_cues or _has_outbound_after(actor_id, str(message_row.get("created_at") or ""), merged_action_rows):
            continue
        seen_cues.add(cue_ref)
        observed_next_actions = _step_summaries_after(merged_action_rows, str(message_row.get("created_at") or ""))
        missed.append(
            {
                "cue_ref": cue_ref,
                "cue_summary": _normalize_excerpt(message_row.get("body") or f"{actor_id} asked for clarification"),
                "expected_action_families": ["decision_alignment" if actor_id == "dana" else "scope_tradeoff"],
                "expected_actors": [actor_id],
                "deadline_context": stakeholder.get("relevant_deadlines", [])[:1],
                "observed_next_actions": observed_next_actions,
                "why_missed": f"The TPM never responded directly to {actor_id}'s question before continuing other coordination.",
                "counterfactual_step": f"Reply directly to {actor_id} with the credible plan and the next required decision.",
            }
        )
    for message_row in message_rows:
        actor_id = str(message_row.get("sender_id") or "")
        stakeholder = stakeholders_by_id.get(actor_id)
        if not stakeholder:
            continue
        rights = set(stakeholder.get("decision_rights", []))
        if "can_grant_review" not in rights:
            continue
        body = str(message_row.get("body") or "").lower()
        cue_ref = f"message:{message_row['id']}"
        if cue_ref in seen_cues:
            continue
        if not any(token in body for token in ("complete", "completeness", "queue", "cutoff", "approvable", "partial")):
            continue
        observed_next_actions = _step_summaries_after(merged_action_rows, str(message_row.get("created_at") or ""))
        qualifies = any(
            action["intent_family"] in {"scope_tradeoff", "decision_alignment"} and action["target_actor_id"] in {"dana", "leo"}
            for action in observed_next_actions
        )
        if qualifies:
            continue
        seen_cues.add(cue_ref)
        missed.append(
            {
                "cue_ref": cue_ref,
                "cue_summary": _normalize_excerpt(message_row.get("body")),
                "expected_action_families": ["scope_tradeoff", "decision_alignment"],
                "expected_actors": ["dana", "leo"],
                "deadline_context": stakeholder.get("relevant_deadlines", [])[:1],
                "observed_next_actions": observed_next_actions,
                "why_missed": "The approver signaled that completeness was the blocker, but the TPM did not pivot to the upstream decision owners or completion path.",
                "counterfactual_step": "Stop re-asking the approver and align the staged scope with Dana and Leo before returning with a complete intake.",
            }
        )
    for signal in signal_coverage.get("signals", []):
        cue_ref = signal.get("surface_event_ref")
        if not cue_ref or cue_ref in seen_cues or not signal.get("surfaced"):
            continue
        signal_id = str(signal.get("signal_id") or "")
        if signal_id == "ops_checklist_available":
            scope_window = next((row for row in window_scorecards if row["window_id"] == "scope_alignment_cutoff"), None)
            if not scope_window or not scope_window.get("state_achieved", {}).get("achieved"):
                continue
        if signal.get("expected_action_families") and signal.get("converted_to_plan_change"):
            continue
        if signal_id not in {"dana_accepts_staged_if_early", "ops_checklist_available"}:
            continue
        seen_cues.add(cue_ref)
        observed_next_actions = _step_summaries_after(merged_action_rows, str(signal.get("first_surfaced_at") or ""))
        missed.append(
            {
                "cue_ref": cue_ref,
                "cue_summary": f"{signal.get('label')} surfaced",
                "expected_action_families": list(signal.get("expected_action_families") or []),
                "expected_actors": list(signal.get("expected_actors") or []),
                "deadline_context": [{"id": signal.get("deadline_label"), "at": signal.get("deadline_at")}]
                if signal.get("deadline_label") or signal.get("deadline_at")
                else [],
                "observed_next_actions": observed_next_actions,
                "why_missed": "A high-value coordination cue surfaced, but the next actions did not change the plan in the direction the signal implied.",
                "counterfactual_step": (
                    "Use the sponsor cue immediately to align the staged tradeoff."
                    if signal_id == "dana_accepts_staged_if_early"
                    else "Close the ops side-path cheaply once scope is settled."
                ),
            }
        )
    missed.sort(key=lambda row: (str(row.get("deadline_context", [{}])[0].get("at") if row.get("deadline_context") else ""), row["cue_ref"]))
    return missed[:6]


def _normalized_step_signature(
    *,
    action_type: str,
    act_id: str,
    target_actor_id: str | None,
    task_id: str | None,
    body: str | None = None,
) -> str:
    intent_family = _action_intent_family(
        {
            "action_type": action_type,
            "act_id": act_id,
            "target_actor_id": target_actor_id,
            "task_id": task_id,
            "body": body or "",
        }
    )
    surface = action_type.split(".", 1)[0] if action_type else "action"
    target = target_actor_id or "-"
    task = task_id or "-"
    return f"{surface}|{target}|{intent_family}|{task}"


def _reference_script_path(scenario_id: str) -> Path | None:
    repo_root = Path(__file__).resolve().parents[1]
    candidates = [
        repo_root / "examples" / scenario_id / "smoke.tpm",
        repo_root / "examples" / scenario_id / "golden.tpm",
        repo_root / "authoring" / "fixtures" / scenario_id / "trajectories" / "smoke.tpm",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _parse_reference_steps(path: Path) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for line_no, raw_line in enumerate(path.read_text().splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            parsed = parse_script_command(stripped)
        except Exception:
            continue
        action = parsed.action
        if action is None:
            continue
        arguments = action.arguments
        target_actor_id = str(arguments.get("target")) if arguments.get("target") else None
        task_id = str(arguments.get("task_id") or arguments.get("slots", {}).get("task_id") or "") or None
        act_id = str(arguments.get("act_id") or "")
        body = str(arguments.get("body") or "")
        steps.append(
            {
                "reference_ref": f"reference:{path.name}:{line_no}",
                "line_no": line_no,
                "raw": stripped,
                "action_type": action.action_type,
                "act_id": act_id,
                "target_actor_id": target_actor_id,
                "task_id": task_id,
                "signature": _normalized_step_signature(
                    action_type=action.action_type,
                    act_id=act_id,
                    target_actor_id=target_actor_id,
                    task_id=task_id,
                    body=body,
                ),
            }
        )
    return steps


def _build_reference_path_diff(
    scenario_id: str,
    scenario: dict[str, Any],
    merged_action_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    path = _reference_script_path(scenario_id)
    if path is None:
        return None
    reference_steps = _parse_reference_steps(path)
    actual_steps = [
        {
            "action_ref": row.get("action_ref"),
            "at": row.get("time"),
            "summary": _action_summary(row),
            "signature": _normalized_step_signature(
                action_type=str(row.get("action_type") or ""),
                act_id=str(row.get("act_id") or ""),
                target_actor_id=_row_target_actor_id(row),
                task_id=str(row.get("task_id") or row.get("slots", {}).get("task_id") or "") or None,
                body=str(row.get("body") or ""),
            ),
        }
        for row in merged_action_rows
        if row.get("step_succeeded")
    ]
    completed: list[str] = []
    divergence_index = None
    for index, ref_step in enumerate(reference_steps):
        if index >= len(actual_steps):
            divergence_index = index
            break
        if ref_step["signature"] != actual_steps[index]["signature"]:
            divergence_index = index
            break
        completed.append(ref_step["raw"])
    if divergence_index is None:
        divergence_index = min(len(reference_steps), len(actual_steps))
        if len(reference_steps) == len(actual_steps):
            return {
                "reference_id": path.name,
                "first_divergence_at": None,
                "first_divergence_action_ref": None,
                "expected_step": None,
                "actual_step": None,
                "missed_expected_steps_before_deadline": [],
                "reference_steps_completed": completed[:5],
                "reference_steps_missed": [],
                "summary": "The run matched the authored reference path.",
            }
    expected_step = reference_steps[divergence_index]["raw"] if divergence_index < len(reference_steps) else None
    actual_step = actual_steps[divergence_index]["summary"] if divergence_index < len(actual_steps) else None
    first_divergence_at = actual_steps[divergence_index]["at"] if divergence_index < len(actual_steps) else None
    first_divergence_action_ref = actual_steps[divergence_index]["action_ref"] if divergence_index < len(actual_steps) else None
    missed_steps = [step["raw"] for step in reference_steps[divergence_index : divergence_index + 5]]
    return {
        "reference_id": path.name,
        "first_divergence_at": first_divergence_at,
        "first_divergence_action_ref": first_divergence_action_ref,
        "expected_step": expected_step,
        "actual_step": actual_step,
        "missed_expected_steps_before_deadline": missed_steps[:3],
        "reference_steps_completed": completed[:5],
        "reference_steps_missed": missed_steps,
        "summary": (
            f"First diverged from {path.name} at step {divergence_index + 1}: expected `{expected_step}` but saw `{actual_step}`."
            if expected_step or actual_step
            else f"Matched {len(completed)} opening steps of {path.name}."
        ),
    }


def _severity_from_lost_points(lost_points_total: float) -> str:
    if lost_points_total >= 40:
        return "critical"
    if lost_points_total >= 20:
        return "high"
    return "medium"


def _impacted_rubric_rows(rubric_lines: list[dict[str, Any]], ids: list[str]) -> list[dict[str, Any]]:
    by_id = {str(line["id"]): line for line in rubric_lines}
    rows = []
    for line_id in ids:
        line = by_id.get(line_id)
        if not line:
            continue
        rows.append(
            {
                "id": line_id,
                "label": line["label"],
                "lost_points": round(float(line["weight"]) - float(line["awarded"]), 2),
            }
        )
    return rows


def _impacted_milestones_for_lines(rubric_lines: list[dict[str, Any]], ids: list[str]) -> list[str]:
    by_id = {str(line["id"]): line for line in rubric_lines}
    milestones = []
    for line_id in ids:
        line = by_id.get(line_id)
        if not line:
            continue
        milestone_id = _line_success_milestone_id(line)
        if milestone_id and milestone_id not in milestones:
            milestones.append(milestone_id)
    return milestones


def _format_actor_list(actor_ids: Iterable[str], *, fallback: str) -> str:
    ordered = _unique_refs(str(actor_id) for actor_id in actor_ids if actor_id)
    if not ordered:
        return fallback
    if len(ordered) == 1:
        return ordered[0]
    if len(ordered) == 2:
        return f"{ordered[0]} and {ordered[1]}"
    return ", ".join(ordered[:-1]) + f", and {ordered[-1]}"


def _driver_owner_actor_ids(signal_coverage: dict[str, Any], *, limit: int | None = None) -> list[str]:
    actor_ids: list[str] = []
    for row in signal_coverage.get("signals", []):
        if row.get("kind") != "driver" or row.get("criticality") != "critical":
            continue
        actor_ids.extend(str(actor_id) for actor_id in row.get("expected_actors") or [] if actor_id)
    unique = _unique_refs(actor_ids)
    return unique[:limit] if limit is not None else unique


def _review_owner_actor_ids(stakeholder_engagement: dict[str, Any], *, limit: int | None = None) -> list[str]:
    actor_ids = [
        str(row["actor_id"])
        for row in stakeholder_engagement.get("actors", [])
        if "can_grant_review" in row.get("decision_rights", [])
    ]
    unique = _unique_refs(actor_ids)
    return unique[:limit] if limit is not None else unique


def _build_root_cause_findings(
    rubric_lines: list[dict[str, Any]],
    *,
    rubric_failure_dossiers: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    signal_coverage: dict[str, Any],
    stakeholder_engagement: dict[str, Any],
    window_scorecards: list[dict[str, Any]],
    missed_opportunities: list[dict[str, Any]],
    reference_path_diff: dict[str, Any] | None,
    merged_action_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    missed_lines = [line for line in rubric_lines if float(line["weight"]) > float(line["awarded"])]
    critical_missed_ids = [str(line["id"]) for line in missed_lines if _is_critical_window_line(line)]
    missing_signal_ids = list(signal_coverage.get("summary_metrics", {}).get("critical_unsurfaced", []))
    stakeholder_metrics = stakeholder_engagement.get("summary_metrics", {})
    decision_owner_phrase = _format_actor_list(
        _driver_owner_actor_ids(signal_coverage, limit=2),
        fallback="the critical decision owners",
    )
    review_owner_phrase = _format_actor_list(
        _review_owner_actor_ids(stakeholder_engagement, limit=2),
        fallback="the downstream reviewers",
    )
    unengaged_critical_actor_phrase = _format_actor_list(
        stakeholder_metrics.get("critical_actors_never_contacted", [])[:3],
        fallback=decision_owner_phrase,
    )
    findings: list[dict[str, Any]] = []
    if diagnostics["counts"]["approval_before_preconditions"] > 0:
        impacted_ids = _unique_refs(critical_missed_ids + [line_id for line_id in ("commitment_quality",) if any(line.get("id") == line_id for line in rubric_lines)])
        lost_points_total = round(sum(item["lost_points"] for item in _impacted_rubric_rows(rubric_lines, impacted_ids)), 2)
        signal_refs = _unique_refs(
            row.get("surface_event_ref")
            for row in signal_coverage.get("signals", [])
            if row.get("signal_id") == "approval_required" and row.get("surface_event_ref")
        )
        action_refs = _sample_action_refs(
            merged_action_rows,
            deadline_at=None,
            predicate=lambda row: row.get("act_id") == "request.approval",
        )
        findings.append(
            {
                "id": "wrong_precondition_sequence",
                "title": "Wrong Precondition Sequence",
                "severity": _severity_from_lost_points(lost_points_total),
                "headline": "The TPM asked for approval before scope and intake were ready, turning the approval path into churn.",
                "what_happened": "The run repeatedly requested approval before the staged path and complete intake were in place.",
                "why_it_mattered": "This consumed the approval window without creating an approvable request, which kept every downstream milestone unstable.",
                "impacted_rubric_lines": _impacted_rubric_rows(rubric_lines, impacted_ids),
                "impacted_milestones": _impacted_milestones_for_lines(rubric_lines, impacted_ids),
                "lost_points_total": lost_points_total,
                "supporting_metrics": {
                    "premature_approval_asks": int(diagnostics["counts"]["approval_before_preconditions"]),
                    "approval_loop_action_refs": action_refs,
                },
                "signal_refs": signal_refs,
                "action_refs": action_refs,
                "counterfactual_step": (
                    f"Align the real scope with {decision_owner_phrase}, then bring {review_owner_phrase} a complete review request before the cutoff."
                ),
                "counterfactual_refs": _unique_refs(signal_refs[:1] + ([f"action:{merged_action_rows[0]['action_id']}"] if merged_action_rows and merged_action_rows[0].get("action_id") else [])),
            }
        )
    top_contacted_actor_id = stakeholder_metrics.get("top_contacted_actor_id")
    top_contacted_actor_share = float(stakeholder_metrics.get("top_contacted_actor_share") or 0.0)
    if top_contacted_actor_id and top_contacted_actor_share >= 0.6:
        impacted_ids = _unique_refs(critical_missed_ids)
        lost_points_total = round(sum(item["lost_points"] for item in _impacted_rubric_rows(rubric_lines, impacted_ids)), 2)
        action_refs = _sample_action_refs(
            merged_action_rows,
            deadline_at=None,
            predicate=lambda row: row.get("action_type") == "chat.send" and _row_target_actor_id(row) == top_contacted_actor_id,
        )
        findings.append(
            {
                "id": "single_threaded_approver_loop",
                "title": "Single-Threaded Coordination Loop",
                "severity": _severity_from_lost_points(lost_points_total),
                "headline": f"The TPM concentrated {round(top_contacted_actor_share * 100)}% of outbound coordination on {top_contacted_actor_id}, instead of prosecuting the full critical path.",
                "what_happened": "Most outbound coordination stayed on one actor while critical decision-makers remained untouched or under-engaged.",
                "why_it_mattered": "This scenario needed parallel coordination across the critical path; concentrating on one actor turned the run into a low-leverage loop.",
                "impacted_rubric_lines": _impacted_rubric_rows(rubric_lines, impacted_ids),
                "impacted_milestones": _impacted_milestones_for_lines(rubric_lines, impacted_ids),
                "lost_points_total": lost_points_total,
                "supporting_metrics": {
                    "top_contacted_actor_id": top_contacted_actor_id,
                    "top_contacted_actor_share": top_contacted_actor_share,
                    "critical_actors_never_contacted": stakeholder_metrics.get("critical_actors_never_contacted", []),
                },
                "signal_refs": _unique_refs(
                    item["cue_ref"]
                    for item in missed_opportunities
                    if item.get("cue_ref", "").startswith("message:") and top_contacted_actor_id in " ".join(item.get("expected_actors", []))
                ),
                "action_refs": action_refs,
                "counterfactual_step": (
                    f"Redistribute the next coordination cycle across {unengaged_critical_actor_phrase} before returning to {top_contacted_actor_id}."
                ),
                "counterfactual_refs": [],
            }
        )
    if stakeholder_metrics.get("critical_actors_never_contacted") or stakeholder_metrics.get("direct_questions_left_unanswered"):
        impacted_ids = _unique_refs(
            critical_missed_ids
            + [
                str(line["id"])
                for line in missed_lines
                if "stakeholder_alignment_communication" in line.get("competency_tags", [])
                or "decision_tradeoff_management" in line.get("competency_tags", [])
            ]
        )
        lost_points_total = round(sum(item["lost_points"] for item in _impacted_rubric_rows(rubric_lines, impacted_ids)), 2)
        signal_refs = _unique_refs(
            list(stakeholder_metrics.get("direct_questions_left_unanswered", []))
            + [
                row.get("surface_event_ref")
                for row in signal_coverage.get("signals", [])
                if row.get("kind") == "driver" and row.get("surface_event_ref")
            ]
        )
        action_refs = _unique_refs(
            action["action_ref"]
            for item in missed_opportunities
            for action in item.get("observed_next_actions", [])
            if action.get("action_ref")
        )[:3]
        findings.append(
            {
                "id": "critical_decision_owner_omission",
                "title": "Critical Decision Owners Omitted",
                "severity": _severity_from_lost_points(lost_points_total),
                "headline": "The TPM failed to engage the key decision owners strongly enough to land the real decision path.",
                "what_happened": "Critical actors either were never contacted or had direct questions left unanswered while the run stayed on lower-leverage follow-ups.",
                "why_it_mattered": "The scenario required the real decision owners to be aligned before downstream approval and commitment work could become credible.",
                "impacted_rubric_lines": _impacted_rubric_rows(rubric_lines, impacted_ids),
                "impacted_milestones": _impacted_milestones_for_lines(rubric_lines, impacted_ids),
                "lost_points_total": lost_points_total,
                "supporting_metrics": {
                    "critical_actors_never_contacted": stakeholder_metrics.get("critical_actors_never_contacted", []),
                    "direct_questions_left_unanswered": stakeholder_metrics.get("direct_questions_left_unanswered", []),
                },
                "signal_refs": signal_refs,
                "action_refs": action_refs,
                "counterfactual_step": (
                    f"Answer the direct stakeholder question and engage {decision_owner_phrase} on the real tradeoff before continuing low-leverage follow-ups."
                ),
                "counterfactual_refs": signal_refs[:2],
            }
        )
    if missed_opportunities:
        impacted_ids = _unique_refs(critical_missed_ids)
        lost_points_total = round(sum(item["lost_points"] for item in _impacted_rubric_rows(rubric_lines, impacted_ids)), 2)
        findings.append(
            {
                "id": "cue_not_converted_to_plan_change",
                "title": "Cue Not Converted To Plan Change",
                "severity": _severity_from_lost_points(lost_points_total),
                "headline": "The TPM saw high-value cues but the following actions did not change the plan in the direction those cues implied.",
                "what_happened": "Signals such as sponsor support, approver completeness blockers, or direct stakeholder questions did not trigger the next best move.",
                "why_it_mattered": "The run kept learning without converting that learning into scope, decision, or dependency movement.",
                "impacted_rubric_lines": _impacted_rubric_rows(rubric_lines, impacted_ids),
                "impacted_milestones": _impacted_milestones_for_lines(rubric_lines, impacted_ids),
                "lost_points_total": lost_points_total,
                "supporting_metrics": {"missed_opportunity_count": len(missed_opportunities)},
                "signal_refs": _unique_refs(item["cue_ref"] for item in missed_opportunities),
                "action_refs": _unique_refs(
                    action["action_ref"]
                    for item in missed_opportunities
                    for action in item.get("observed_next_actions", [])
                    if action.get("action_ref")
                )[:3],
                "counterfactual_step": "Turn the next high-value cue into an explicit decision or dependency move instead of another low-leverage follow-up.",
                "counterfactual_refs": [],
            }
        )
    if missing_signal_ids:
        impacted_ids = _unique_refs(
            [
                str(line["id"])
                for line in missed_lines
                if "discovery_situation_awareness" in line.get("competency_tags", []) or _is_critical_window_line(line)
            ]
        )
        lost_points_total = round(sum(item["lost_points"] for item in _impacted_rubric_rows(rubric_lines, impacted_ids)), 2)
        findings.append(
            {
                "id": "critical_signal_not_surfaced",
                "title": "Critical Signal Not Surfaced",
                "severity": _severity_from_lost_points(lost_points_total),
                "headline": "The TPM never surfaced some of the scenario's most important feasibility signals.",
                "what_happened": f"Critical signals remained unsurfaced: {', '.join(missing_signal_ids)}.",
                "why_it_mattered": "Without surfacing the real feasibility gap, the run could not pivot early enough onto the credible path.",
                "impacted_rubric_lines": _impacted_rubric_rows(rubric_lines, impacted_ids),
                "impacted_milestones": _impacted_milestones_for_lines(rubric_lines, impacted_ids),
                "lost_points_total": lost_points_total,
                "supporting_metrics": {"critical_unsurfaced_signals": missing_signal_ids},
                "signal_refs": [],
                "action_refs": [],
                "counterfactual_step": "Read and engage the artifacts and owners that surface the real feasibility gap before spending the key windows on coordination loops.",
                "counterfactual_refs": [reference_path_diff["first_divergence_action_ref"]] if reference_path_diff and reference_path_diff.get("first_divergence_action_ref") else [],
            }
        )
    findings.sort(
        key=lambda item: (
            float(item["lost_points_total"]),
            len(item.get("impacted_milestones", [])),
            len(item.get("signal_refs", [])) + len(item.get("action_refs", [])),
        ),
        reverse=True,
    )
    deduped: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for finding in findings:
        if finding["id"] in seen_ids:
            continue
        seen_ids.add(finding["id"])
        deduped.append(finding)
    return deduped[:4]


def _build_capability_assessment(
    report: dict[str, Any],
    *,
    outcome_verdict: dict[str, Any],
    critical_path: dict[str, Any],
    root_cause_findings: list[dict[str, Any]],
    stakeholder_engagement: dict[str, Any],
    signal_coverage: dict[str, Any],
    window_scorecards: list[dict[str, Any]],
) -> dict[str, Any]:
    score = float(report.get("total_score", 0.0))
    if critical_path.get("status") == "critical_path_moved" and score >= 75:
        rating = "strong"
    elif score >= 45:
        rating = "mixed"
    else:
        rating = "poor"
    top_root_causes = [item["title"] for item in root_cause_findings[:3]]
    missed_windows = [row["window_id"] for row in window_scorecards if not row.get("state_achieved", {}).get("achieved")]
    top_contacted_actor_id = stakeholder_engagement.get("summary_metrics", {}).get("top_contacted_actor_id")
    top_contacted_actor_share = float(stakeholder_engagement.get("summary_metrics", {}).get("top_contacted_actor_share") or 0.0)
    critical_unsurfaced = signal_coverage.get("summary_metrics", {}).get("critical_unsurfaced", [])
    if rating == "strong":
        headline = "Strong TPM performance for this scenario."
        direct_answer = "This model performed strongly as a TPM in this scenario: it moved the critical path and converted signals into coordinated execution."
    elif rating == "mixed":
        headline = "Mixed TPM performance for this scenario."
        direct_answer = "This model showed some TPM instincts here, but it did not consistently turn signals into the right decisions and commitments."
    else:
        headline = "Poor TPM performance for this scenario."
        direct_answer = (
            "This model performed poorly as a TPM in this scenario: it surfaced some cues, but it did not drive the real scope/approval path and over-focused on low-leverage approver coordination."
        )
    return {
        "rating": rating,
        "headline": headline,
        "direct_answer": direct_answer,
        "confidence_scope": "single_run_directional",
        "primary_root_causes": top_root_causes,
        "key_supporting_metrics": {
            "score": score,
            "critical_path_status": critical_path.get("status"),
            "missed_windows": missed_windows,
            "top_contacted_actor_id": top_contacted_actor_id,
            "top_contacted_actor_share": top_contacted_actor_share,
            "critical_actors_never_contacted": stakeholder_engagement.get("summary_metrics", {}).get("critical_actors_never_contacted", []),
            "critical_unsurfaced_signals": critical_unsurfaced,
        },
    }


def _collect_evidence_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        for item in value.values():
            refs |= _collect_evidence_refs(item)
    elif isinstance(value, list):
        for item in value:
            refs |= _collect_evidence_refs(item)
    elif isinstance(value, str) and re.match(r"^(event|action|message|doc):", value):
        refs.add(value)
    return refs


def _build_evidence_catalog(
    summary: dict[str, Any],
    *,
    visible_trace: list[dict[str, Any]],
    omniscient_trace: list[dict[str, Any]],
    merged_action_rows: list[dict[str, Any]],
    message_rows: list[dict[str, Any]],
    document_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    refs = sorted(_collect_evidence_refs(summary))
    event_index = {
        f"event:{row['id']}": row
        for row in _unique_event_rows(visible_trace + omniscient_trace)
        if row.get("id") is not None
    }
    action_index = {str(row["action_ref"]): row for row in merged_action_rows if row.get("action_ref")}
    message_index = {f"message:{row['id']}": row for row in message_rows if row.get("id") is not None}
    document_index = {f"doc:{row['id']}": row for row in document_rows if row.get("id")}
    catalog: list[dict[str, Any]] = []
    for ref in refs:
        if ref in event_index:
            row = event_index[ref]
            catalog.append(
                {
                    "evidence_ref": ref,
                    "kind": "event",
                    "at": row.get("at"),
                    "actor_id": row.get("actor_id"),
                    "target_actor_id": None,
                    "summary": row.get("summary"),
                    "excerpt": _normalize_excerpt(json.dumps(row.get("payload") or {}, sort_keys=True)),
                }
            )
            continue
        if ref in action_index:
            row = action_index[ref]
            catalog.append(
                {
                    "evidence_ref": ref,
                    "kind": "action",
                    "at": row.get("time"),
                    "actor_id": "tpm",
                    "target_actor_id": _row_target_actor_id(row),
                    "summary": _action_summary(row),
                    "excerpt": _normalize_excerpt(row.get("body")),
                }
            )
            continue
        if ref in message_index:
            row = message_index[ref]
            catalog.append(
                {
                    "evidence_ref": ref,
                    "kind": "message",
                    "at": row.get("created_at"),
                    "actor_id": row.get("sender_id"),
                    "target_actor_id": "tpm" if row.get("sender_id") != "tpm" else row.get("thread_id"),
                    "summary": f"{row.get('sender_id')} [{row.get('act_id') or 'message'}]",
                    "excerpt": _normalize_excerpt(row.get("body")),
                }
            )
            continue
        if ref in document_index:
            row = document_index[ref]
            catalog.append(
                {
                    "evidence_ref": ref,
                    "kind": "doc_excerpt",
                    "at": row.get("updated_at"),
                    "actor_id": row.get("author_id"),
                    "target_actor_id": None,
                    "summary": row.get("title"),
                    "excerpt": _normalize_excerpt(row.get("content")),
                }
            )
    catalog.sort(key=lambda row: (str(row.get("at") or ""), row["evidence_ref"]))
    return catalog


def _unique_event_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        ref = f"event:{row['id']}" if row.get("id") is not None else None
        if not ref or ref in seen:
            continue
        seen.add(ref)
        deduped.append(row)
    return deduped


def _decisive_timeline(report: dict[str, Any], trace_rows: list[dict[str, Any]], rubric_lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_event_id = {f"event:{row['id']}": row for row in trace_rows if row.get("id") is not None}
    timeline: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    high_signal = sorted(
        rubric_lines,
        key=lambda line: (float(line["weight"]) - float(line["awarded"]), float(line["weight"])),
        reverse=True,
    )
    for line in high_signal:
        for evidence_ref in line.get("evidence_refs", []):
            row = by_event_id.get(evidence_ref)
            if not row:
                continue
            key = (row["at"], row["summary"])
            if key in seen:
                continue
            seen.add(key)
            timeline.append(
                {
                    "at": row["at"],
                    "event_ref": evidence_ref,
                    "event_type": row["event_type"],
                    "summary": row["summary"],
                    "related_rubric_line": line["id"],
                }
            )
            if len(timeline) >= 8:
                return timeline
    for item in report.get("decisive_moments", []):
        key = (item["at"], item["summary"])
        if key in seen:
            continue
        seen.add(key)
        timeline.append(
            {
                "at": item["at"],
                "event_ref": f"event:{item.get('id')}" if item.get("id") is not None else None,
                "event_type": item.get("event_type"),
                "summary": item["summary"],
                "related_rubric_line": None,
            }
        )
        if len(timeline) >= 8:
            break
    return timeline


def _outcome_verdict(critical_path: dict[str, Any], dimension_scores: dict[str, dict[str, Any]], run_health: dict[str, Any]) -> dict[str, Any]:
    discovery = dimension_scores["discovery_situation_awareness"]["score"]
    outcome = dimension_scores["outcome_attainment"]["score"]
    timing = dimension_scores["timing_optionality_preservation"]["score"]
    decision = dimension_scores["decision_tradeoff_management"]["score"]
    commitment = dimension_scores["commitment_dependency_management"]["score"]
    if run_health.get("overall_status") == "protocol_failure":
        status = "interrupted"
        headline = "Run was interrupted before the TPM converted visible state into enough progress."
    elif run_health.get("termination_reason") == "max_turns_reached":
        status = "turn_budget_exhausted"
        if discovery >= 60 and outcome < 40:
            headline = "Exhausted the turn budget after discovering important risks, but before converting them into coordinated execution."
        else:
            headline = "Exhausted the turn budget before moving the critical path enough."
    elif critical_path["status"] == "critical_path_moved":
        status = "moved_critical_path"
        headline = "Moved the critical path and landed the scenario’s key outcomes."
    elif discovery >= 60 and outcome < 40:
        status = "discovered_but_did_not_execute"
        headline = "Discovered important risks, but failed to convert them into coordinated execution."
    elif decision < 40 or commitment < 40:
        status = "weak_decision_commitment_loop"
        headline = "Failed to turn partial information into the decisions and commitments needed to move the project."
    elif timing < 40:
        status = "missed_key_windows"
        headline = "Acted too late or in the wrong order, so key windows and optionality were lost."
    else:
        status = "partial_progress"
        headline = "Made some progress, but did not move the project far enough or fast enough."
    return {
        "status": status,
        "headline": headline,
        "score_context": {
            "discovery": discovery,
            "outcome": outcome,
            "timing": timing,
            "decision": decision,
            "commitment": commitment,
        },
    }


def _render_termination_reason(reason: str | None) -> str:
    labels = {
        "protocol_failure": "protocol failure",
        "success_criteria_met": "success criteria met",
        "max_turns_reached": "turn budget exhausted",
        "scenario_horizon_reached": "scenario horizon reached",
        "completed": "completed",
    }
    return labels.get(reason or "completed", reason or "completed")


def _normalized_termination_reason(run_record: dict[str, Any]) -> str:
    explicit = run_record.get("termination_reason")
    if explicit:
        return explicit
    if run_record.get("protocol_failure"):
        return "protocol_failure"
    turns_taken = run_record.get("turns_taken")
    max_turns = run_record.get("max_turns")
    if turns_taken is not None and max_turns is not None and int(turns_taken) >= int(max_turns):
        return "max_turns_reached"
    return "completed"


def _deterministic_narrative(
    capability_assessment: dict[str, Any],
    root_cause_findings: list[dict[str, Any]],
    stakeholder_engagement: dict[str, Any],
    signal_coverage: dict[str, Any],
    window_scorecards: list[dict[str, Any]],
    reference_path_diff: dict[str, Any] | None,
    key_successes: list[dict[str, Any]],
) -> dict[str, Any]:
    supporting_data = []
    stakeholder_metrics = stakeholder_engagement.get("summary_metrics", {})
    if stakeholder_metrics.get("critical_actors_never_contacted"):
        supporting_data.append(
            {
                "title": "Critical actors were omitted",
                "explanation": f"Critical actors never contacted: {', '.join(stakeholder_metrics['critical_actors_never_contacted'])}.",
                "evidence_refs": [],
            }
        )
    if stakeholder_metrics.get("direct_questions_left_unanswered"):
        supporting_data.append(
            {
                "title": "Direct stakeholder questions were left unanswered",
                "explanation": "At least one critical stakeholder asked a direct question that never received a TPM response.",
                "evidence_refs": stakeholder_metrics["direct_questions_left_unanswered"][:3],
            }
        )
    critical_unsurfaced = signal_coverage.get("summary_metrics", {}).get("critical_unsurfaced", [])
    if critical_unsurfaced:
        supporting_data.append(
            {
                "title": "Critical signals remained unsurfaced",
                "explanation": f"Critical unsurfaced signals: {', '.join(critical_unsurfaced)}.",
                "evidence_refs": [],
            }
        )
    missed_windows = [row["window_id"] for row in window_scorecards if not row.get("state_achieved", {}).get("achieved")]
    if missed_windows:
        supporting_data.append(
            {
                "title": "Critical windows were missed",
                "explanation": f"Missed windows: {', '.join(missed_windows)}.",
                "evidence_refs": [],
            }
        )
    counterfactual_path = [
        {
            "title": finding["title"],
            "explanation": finding["counterfactual_step"],
            "evidence_refs": finding.get("counterfactual_refs", []),
        }
        for finding in root_cause_findings[:3]
        if finding.get("counterfactual_step")
    ]
    if reference_path_diff and reference_path_diff.get("missed_expected_steps_before_deadline"):
        for step in reference_path_diff["missed_expected_steps_before_deadline"][:3]:
            counterfactual_path.append(
                {
                    "title": "Reference path",
                    "explanation": step,
                    "evidence_refs": [],
                }
            )
    return {
        "source": "deterministic_template",
        "direct_answer": capability_assessment["direct_answer"],
        "executive_summary": capability_assessment["headline"],
        "top_findings": [
            {
                "title": item["title"],
                "explanation": item["headline"],
                "evidence_refs": _unique_refs(item.get("signal_refs", []) + item.get("action_refs", [])),
            }
            for item in root_cause_findings[:4]
        ],
        "counterfactual_path": counterfactual_path[:4],
        "supporting_data": supporting_data[:4]
        + [
            {"title": item["title"], "explanation": item["summary"], "evidence_refs": item.get("evidence_refs", [])}
            for item in key_successes[:1]
        ],
        "limitations": [
            {
                "title": "Single-seed directional readout",
                "explanation": "This run-level diagnosis is deterministic and auditable, but it is still only a single-seed directional readout.",
                "evidence_refs": [],
            }
        ],
    }


def _build_judge_input_bundle(
    summary: dict[str, Any],
    diagnostics: dict[str, Any],
    decisive_timeline: list[dict[str, Any]],
    omniscient_trace: list[dict[str, Any]],
    agent_trace: list[dict[str, Any]],
    merged_action_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    allowed_evidence_refs = sorted({item["evidence_ref"] for item in summary.get("evidence_catalog", [])})
    trace_excerpt = []
    evidence_by_ref = {item["evidence_ref"]: item for item in summary.get("evidence_catalog", [])}
    for ref in allowed_evidence_refs[:20]:
        item = evidence_by_ref.get(ref)
        if not item:
            continue
        trace_excerpt.append(
            {
                "evidence_ref": ref,
                "at": item.get("at"),
                "actor_id": item.get("actor_id"),
                "event_type": item.get("kind"),
                "summary": item.get("summary"),
                "excerpt": item.get("excerpt"),
            }
        )
    return {
        "scenario_context": summary["scenario_context"],
        "competency_definitions": DIMENSION_DEFINITIONS,
        "run_header": summary["run_header"],
        "score_breakdown": summary.get("score_breakdown", {}),
        "capability_assessment": summary["capability_assessment"],
        "outcome_verdict": summary["outcome_verdict"],
        "critical_path_result": summary["critical_path_result"],
        "root_cause_findings": summary.get("root_cause_findings", []),
        "stakeholder_engagement": summary.get("stakeholder_engagement", {}),
        "signal_coverage": summary.get("signal_coverage", {}),
        "window_scorecards": summary.get("window_scorecards", []),
        "missed_opportunities": summary.get("missed_opportunities", []),
        "reference_path_diff": summary.get("reference_path_diff"),
        "evidence_catalog": summary.get("evidence_catalog", []),
        "rubric_failure_appendix": summary.get("rubric_failure_appendix", []),
        "tpm_competency_profile": summary["tpm_competency_profile"],
        "outcome_profile": summary["outcome_profile"],
        "key_successes": summary["key_successes"],
        "key_failures": summary["key_failures"],
        "improvement_opportunities": summary["improvement_opportunities"],
        "run_health": summary["run_health"],
        "behavior_diagnostics": {
            key: value
            for key, value in diagnostics.items()
            if key != "private_note_audit_rows"
        },
        "decisive_timeline": decisive_timeline,
        "allowed_evidence_refs": allowed_evidence_refs,
        "trace_excerpt": trace_excerpt,
        "agent_visible_excerpt": [
            {
                "at": row["at"],
                "actor_id": row["actor_id"],
                "event_type": row["event_type"],
                "summary": row["summary"],
            }
            for row in agent_trace[:20]
        ],
    }


def _maybe_apply_judge(summary: dict[str, Any], *, judge_client: Any | None, judge_model: str | None) -> dict[str, Any]:
    if judge_client is None and not judge_model:
        return summary["narrative"]
    try:
        from tpm_sim.judge import summarize_with_judge
    except Exception:
        return summary["narrative"]
    try:
        return summarize_with_judge(
            summary["judge_input_bundle"],
            fallback=summary["narrative"],
            judge_client=judge_client,
            judge_model=judge_model,
        )
    except Exception:
        return summary["narrative"]


def _band_from_score(score: float) -> str:
    if score >= 75:
        return "strong"
    if score >= 45:
        return "mixed"
    return "weak"


def _dimension_evidence_refs(item: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for line in item.get("contributing_rubric_lines", []):
        refs.extend(line.get("evidence_refs", []))
    deduped = []
    for ref in refs:
        if ref not in deduped:
            deduped.append(ref)
    return deduped


def _load_trace_rows(trace_path: str | None) -> list[dict[str, Any]]:
    if not trace_path:
        return []
    path = Path(trace_path)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _count_repeated_pairs(items: Iterable[tuple[Any, Any]]) -> int:
    counts = Counter(items)
    return sum(max(0, count - 1) for count in counts.values())


def _find_repeated_action_loops(action_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    loops: list[dict[str, Any]] = []
    index = 0
    while index < len(action_rows):
        row = action_rows[index]
        signature = (row["action_type"], row["target"], row["act_id"], row["task_id"], row["doc_id"])
        end = index + 1
        while end < len(action_rows):
            candidate = action_rows[end]
            candidate_signature = (
                candidate["action_type"],
                candidate["target"],
                candidate["act_id"],
                candidate["task_id"],
                candidate["doc_id"],
            )
            if candidate_signature != signature:
                break
            end += 1
        if end - index >= 3:
            loop_rows = action_rows[index:end]
            loops.append(
                {
                    "signature": {
                        "action_type": row["action_type"],
                        "target": row["target"],
                        "act_id": row["act_id"],
                        "task_id": row["task_id"],
                        "doc_id": row["doc_id"],
                    },
                    "turn_range": [row["turn"], action_rows[end - 1]["turn"]],
                    "count": end - index,
                    "action_refs": [item["action_ref"] for item in loop_rows if item.get("action_ref")][:3],
                    "action_times": [item["time"] for item in loop_rows if item.get("time")],
                }
            )
        index = end
    return loops


def _count_approval_before_scope(action_rows: list[dict[str, Any]], trace_rows: list[dict[str, Any]], scenario: dict[str, Any]) -> int:
    scope_milestone_ids = [
        row["id"]
        for row in scenario["world"].get("milestones", [])
        if "scope" in row["id"] or "scope" in row.get("title", "").lower() or "align" in row.get("title", "").lower()
    ]
    if not scope_milestone_ids:
        return 0
    done_at: dict[str, str] = {}
    for row in trace_rows:
        if row.get("event_type") != "milestone.updated":
            continue
        payload = row.get("payload", {})
        milestone_id = payload.get("milestone_id")
        if milestone_id in scope_milestone_ids and payload.get("new_status") == "done" and milestone_id not in done_at:
            done_at[milestone_id] = row["at"]
    count = 0
    for row in action_rows:
        if row["act_id"] != "request.approval":
            continue
        action_time = row["time"]
        for milestone_id in scope_milestone_ids:
            if milestone_id not in done_at or action_time < done_at[milestone_id]:
                count += 1
                break
    return count


def _count_unresolved_reply_loops(action_rows: list[dict[str, Any]], trace_rows: list[dict[str, Any]]) -> int:
    inbound_times: dict[str, list[str]] = defaultdict(list)
    for row in trace_rows:
        if row.get("event_type") == "npc.message_sent":
            inbound_times[str(row.get("actor_id"))].append(str(row.get("at")))
    count = 0
    last_outbound: dict[tuple[str, str], str] = {}
    for row in action_rows:
        if row["action_type"] != "chat.send" or not row["target"] or not row["act_id"]:
            continue
        key = (str(row["target"]), str(row["act_id"]))
        target = str(row["target"])
        previous = last_outbound.get(key)
        if previous is not None:
            has_inbound = any(previous < stamp < row["time"] for stamp in inbound_times.get(target, []))
            if not has_inbound:
                count += 1
        last_outbound[key] = str(row["time"])
    return count
