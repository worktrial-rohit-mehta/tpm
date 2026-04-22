from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Iterable, Optional

from tpm_sim.scenario import load_scenario_bundle


PERFORMANCE_SUMMARY_VERSION = "tpm_performance_summary_v1"
BUNDLE_PERFORMANCE_SUMMARY_VERSION = "tpm_bundle_performance_summary_v1"
COMPETENCY_MODEL_VERSION = "tpm_competency_model_v1"

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
    report = json.loads(Path(run_record["report_path"]).read_text())
    scenario_bundle = load_scenario_bundle(run_record["scenario_id"])
    summary = build_run_summary(
        report,
        agent_payload=payload,
        scenario_bundle=scenario_bundle,
        judge_client=judge_client,
        judge_model=judge_model,
    )
    if write_files:
        summary_json = run_path / "tpm_performance_summary.json"
        summary_md = run_path / "tpm_performance_summary.md"
        judge_input_json = run_path / "judge_input_bundle.json"
        judge_output_json = run_path / "judge_output.json"
        summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True))
        summary_md.write_text(render_run_summary(summary))
        judge_input_json.write_text(json.dumps(summary["judge_input_bundle"], indent=2, sort_keys=True))
        if summary["narrative"].get("source") == "llm_judge":
            judge_output_json.write_text(json.dumps(summary["narrative"], indent=2, sort_keys=True))
        run_record["summary_path"] = str(summary_json)
        run_record["summary_markdown_path"] = str(summary_md)
        run_record["judge_input_path"] = str(judge_input_json)
        if summary["narrative"].get("source") == "llm_judge":
            run_record["judge_output_path"] = str(judge_output_json)
        payload["run"] = run_record
        (run_path / "agent_run.json").write_text(json.dumps(payload, indent=2, sort_keys=True))
    return summary


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
    omniscient_trace = _load_trace_rows(report.get("trace_paths", {}).get("omniscient_trace"))
    agent_trace = _load_trace_rows(report.get("trace_paths", {}).get("agent_trace"))
    diagnostics = build_behavior_diagnostics(agent_payload, omniscient_trace, scenario)
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
    key_successes = _key_successes(report["rubric"], dimension_scores)
    key_failures = _key_failures(report["rubric"], diagnostics)
    improvements = _improvement_opportunities(report["rubric"], diagnostics)
    decisive_timeline = _decisive_timeline(report, omniscient_trace, report["rubric"])
    outcome_verdict = _outcome_verdict(critical_path, dimension_scores, run_health)
    summary = {
        "schema_version": PERFORMANCE_SUMMARY_VERSION,
        "run_header": {
            "scenario_id": report["scenario_id"],
            "scenario_digest": report["scenario_digest"],
            "compiled_coverage_digest": report.get("compiled_coverage_digest", report["scenario_digest"]),
            "closure_status": report.get("closure_status", {"status": "unknown", "passed": False}),
            "seed": run_record.get("seed"),
            "adapter": run_record.get("adapter"),
            "model": run_record.get("model"),
            "prompt_pack_version": run_record.get("prompt_pack_version"),
            "time": report["time"],
            "score": report["total_score"],
            "turns_taken": run_record.get("turns_taken"),
            "max_turns": run_record.get("max_turns"),
            "report_path": run_record.get("report_path"),
            "agent_log_path": run_record.get("agent_log_path"),
        },
        "scenario_context": {
            "title": scenario["world"]["project"]["name"],
            "summary": scenario["world"]["project"]["description"],
            "primary_failure_classes": scenario["evaluation"].get("primary_failure_classes", []),
            "competency_model_version": COMPETENCY_MODEL_VERSION,
        },
        "outcome_verdict": outcome_verdict,
        "critical_path_result": critical_path,
        "tpm_competency_profile": competency_profile,
        "outcome_profile": outcome_profile,
        "decisive_timeline": decisive_timeline,
        "key_successes": key_successes,
        "key_failures": key_failures,
        "improvement_opportunities": improvements,
        "run_health": run_health,
        "narrative": _deterministic_narrative(outcome_verdict, key_successes, key_failures, improvements),
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
        },
    }
    summary["judge_input_bundle"] = _build_judge_input_bundle(summary, diagnostics, decisive_timeline, omniscient_trace, agent_trace)
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
                "stdev": round(pstdev([float(item["score"]) for item in rows]), 3) if len(rows) > 1 else 0.0,
                "band": _band_from_score(mean([float(item["score"]) for item in rows])),
            }
        )
    recurring_failures = Counter()
    recurring_health = Counter()
    for run in run_summaries:
        for item in run.get("key_failures", []):
            recurring_failures[item["id"]] += 1
        for item in run.get("run_health", {}).get("harness_interface_issues", []):
            recurring_health[item] += 1
        for item in run.get("run_health", {}).get("scenario_authoring_issues", []):
            recurring_health[item] += 1
    aggregate = {
        "schema_version": BUNDLE_PERFORMANCE_SUMMARY_VERSION,
        "bundle_header": {
            "scenario_id": scenario_id,
            "model": model,
            "seed_bundle": seed_bundle,
        },
        "headline": {
            "mean_score": round(mean(scores), 2) if scores else 0.0,
            "worst_score": round(min(scores), 2) if scores else 0.0,
            "best_score": round(max(scores), 2) if scores else 0.0,
            "stdev": round(pstdev(scores), 3) if len(scores) > 1 else 0.0,
        },
        "aggregate_competency_profile": competency_profile,
        "seed_consistency": {
            "protocol_failures": sum(1 for run in run_summaries if run.get("run_health", {}).get("protocol_failure")),
            "coverage_misses": sum(1 for run in run_summaries if run.get("run_health", {}).get("coverage_miss")),
            "score_variance_ok": (round(pstdev(scores), 3) if len(scores) > 1 else 0.0) < 15,
        },
        "top_recurring_failure_themes": [
            {"id": key, "count": count}
            for key, count in recurring_failures.most_common(5)
        ],
        "harness_health": {
            "status": "clean" if not recurring_health else "attention_needed",
            "issues": [{"issue": key, "count": count} for key, count in recurring_health.most_common(5)],
        },
        "runs": [
            {
                "seed": run["run_header"]["seed"],
                "score": run["run_header"]["score"],
                "outcome_verdict": run["outcome_verdict"]["headline"],
                "summary_path": run["run_header"].get("summary_path"),
            }
            for run in run_summaries
        ],
    }
    return aggregate


def render_run_summary(summary: dict[str, Any]) -> str:
    strengths = summary.get("key_successes", [])[:2]
    failures = summary.get("key_failures", [])[:3]
    competencies = summary.get("tpm_competency_profile", [])
    lines = [
        f"Scenario: {summary['run_header']['scenario_id']}",
        f"Seed: {summary['run_header'].get('seed')}",
        f"Model: {summary['run_header'].get('model')}",
        f"Score: {summary['run_header']['score']}",
        "",
        f"Outcome verdict: {summary['outcome_verdict']['headline']}",
        f"Critical path: {summary['critical_path_result']['status']}",
        "",
        "Competency profile:",
    ]
    for item in competencies:
        lines.append(f"- {item['label']}: {item['score']} ({item['band']})")
    lines.extend(["", "Top strengths:"])
    if strengths:
        for item in strengths:
            lines.append(f"- {item['title']}: {item['summary']}")
    else:
        lines.append("- none")
    lines.extend(["", "Top failures:"])
    if failures:
        for item in failures:
            lines.append(f"- {item['title']}: {item['summary']}")
    else:
        lines.append("- none")
    lines.extend(["", "Run health:"])
    lines.append(f"- status: {summary['run_health']['status']}")
    if summary["run_health"].get("model_behavior_issues"):
        lines.append(f"- model issues: {', '.join(summary['run_health']['model_behavior_issues'])}")
    if summary["run_health"].get("harness_interface_issues"):
        lines.append(f"- harness issues: {', '.join(summary['run_health']['harness_interface_issues'])}")
    if summary["run_health"].get("scenario_authoring_issues"):
        lines.append(f"- scenario issues: {', '.join(summary['run_health']['scenario_authoring_issues'])}")
    lines.extend(["", "Narrative:", f"- {summary['narrative']['executive_summary']}"])
    return "\n".join(lines)


def render_bundle_summary(summary: dict[str, Any]) -> str:
    lines = [
        f"Scenario: {summary['bundle_header']['scenario_id']}",
        f"Model: {summary['bundle_header']['model']}",
        f"Mean score: {summary['headline']['mean_score']}",
        f"Worst score: {summary['headline']['worst_score']}",
        f"Stdev: {summary['headline']['stdev']}",
        "",
        "Aggregate competency profile:",
    ]
    for item in summary.get("aggregate_competency_profile", [])[:8]:
        lines.append(f"- {item['label']}: mean={item['mean_score']} worst={item['worst_score']} stdev={item['stdev']}")
    if summary.get("top_recurring_failure_themes"):
        lines.extend(["", "Recurring failure themes:"])
        for item in summary["top_recurring_failure_themes"]:
            lines.append(f"- {item['id']}: {item['count']}")
    if summary.get("harness_health", {}).get("issues"):
        lines.extend(["", "Harness health:"])
        for item in summary["harness_health"]["issues"]:
            lines.append(f"- {item['issue']}: {item['count']}")
    return "\n".join(lines)


def build_behavior_diagnostics(agent_payload: dict[str, Any] | None, omniscient_trace: list[dict[str, Any]], scenario: dict[str, Any]) -> dict[str, Any]:
    decisions = (agent_payload or {}).get("decisions", [])
    action_rows = []
    for turn in decisions:
        decision = turn.get("decision", {})
        action = decision.get("action", {})
        args = action.get("arguments", {})
        action_rows.append(
            {
                "turn": turn.get("turn"),
                "time": turn.get("observation_time"),
                "action_type": action.get("action_type"),
                "target": args.get("target"),
                "act_id": args.get("act_id"),
                "doc_id": args.get("doc_id"),
                "task_id": args.get("task_id"),
                "validation_errors": turn.get("validation_errors") or [],
                "repair_attempts": turn.get("repair_attempts", 0),
            }
        )
    read_count = sum(1 for row in action_rows if str(row["action_type"]).startswith("read."))
    write_count = sum(1 for row in action_rows if row["action_type"] in {"chat.send", "docs.write", "notes.write", "task.note", "task.set_owner", "task.set_target", "meeting.propose", "meeting.act"})
    wait_count = sum(1 for row in action_rows if str(row["action_type"]).startswith("wait."))
    artifact_churn = sum(1 for row in action_rows if row["action_type"] in {"docs.write", "notes.write"})
    tracker_churn = sum(1 for row in action_rows if row["action_type"] in {"task.note", "task.set_owner", "task.set_target"})
    escalation_actions = [row for row in action_rows if row["act_id"] in {"escalate.to_manager", "escalate.to_sponsor"}]
    escalation_repetition = _count_repeated_pairs((row["target"], row["act_id"]) for row in escalation_actions)
    repeated_loops = _find_repeated_action_loops(action_rows)
    approval_before_preconditions = _count_approval_before_scope(action_rows, omniscient_trace, scenario)
    reply_loops = _count_unresolved_reply_loops(action_rows, omniscient_trace)
    alias_normalizations = sum(1 for row in omniscient_trace if row.get("event_type") == "thread.normalized")
    coverage_misses = sum(1 for row in omniscient_trace if row.get("event_type") == "coverage.miss")
    protocol_repairs = sum(int(row.get("repair_attempts", 0)) for row in action_rows)
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
        },
        "repeated_action_loops": repeated_loops,
    }
    return diagnostics


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
        model_issues.append("artifact churn")
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

    if run_record.get("protocol_failure"):
        status = "protocol_failure"
    elif scenario_issues:
        status = "scenario_authoring_attention_needed"
    elif harness_issues:
        status = "minor_harness_friction"
    else:
        status = "clean"

    return {
        "status": status,
        "protocol_failure": bool(run_record.get("protocol_failure")),
        "protocol_failure_reason": run_record.get("protocol_failure_reason"),
        "coverage_miss": bool(report.get("coverage_miss")),
        "model_behavior_issues": model_issues,
        "harness_interface_issues": harness_issues,
        "scenario_authoring_issues": scenario_issues,
        "behavior_diagnostics": diagnostics,
    }


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
                "title": "Reduce document churn and spend those turns on coordination",
                "summary": "In this benchmark, docs are support artifacts. They rarely substitute for getting the right owner, approver, or sponsor aligned.",
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
    if run_health["protocol_failure"]:
        status = "interrupted"
        headline = "Run was interrupted before the TPM converted visible state into enough progress."
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


def _deterministic_narrative(
    outcome_verdict: dict[str, Any],
    key_successes: list[dict[str, Any]],
    key_failures: list[dict[str, Any]],
    improvements: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "source": "deterministic_template",
        "executive_summary": outcome_verdict["headline"],
        "top_strengths": [
            {"title": item["title"], "explanation": item["summary"], "evidence_refs": item.get("evidence_refs", [])}
            for item in key_successes[:2]
        ],
        "top_failures": [
            {"title": item["title"], "explanation": item["summary"], "evidence_refs": item.get("evidence_refs", [])}
            for item in key_failures[:3]
        ],
        "improvement_opportunities": [
            {"title": item["title"], "explanation": item["summary"], "evidence_refs": []}
            for item in improvements[:3]
        ],
    }


def _build_judge_input_bundle(
    summary: dict[str, Any],
    diagnostics: dict[str, Any],
    decisive_timeline: list[dict[str, Any]],
    omniscient_trace: list[dict[str, Any]],
    agent_trace: list[dict[str, Any]],
) -> dict[str, Any]:
    allowed_evidence_refs = sorted(
        {
            ref
            for line in summary["evidence_appendix"]["rubric_lines"]
            for ref in line.get("evidence_refs", [])
        }
    )
    trace_excerpt = []
    event_index = {f"event:{row['id']}": row for row in omniscient_trace if row.get("id") is not None}
    for ref in allowed_evidence_refs[:15]:
        row = event_index.get(ref)
        if row:
            trace_excerpt.append(
                {
                    "evidence_ref": ref,
                    "at": row["at"],
                    "actor_id": row["actor_id"],
                    "event_type": row["event_type"],
                    "summary": row["summary"],
                }
            )
    return {
        "scenario_context": summary["scenario_context"],
        "competency_definitions": DIMENSION_DEFINITIONS,
        "run_header": summary["run_header"],
        "outcome_verdict": summary["outcome_verdict"],
        "critical_path_result": summary["critical_path_result"],
        "tpm_competency_profile": summary["tpm_competency_profile"],
        "outcome_profile": summary["outcome_profile"],
        "key_successes": summary["key_successes"],
        "key_failures": summary["key_failures"],
        "improvement_opportunities": summary["improvement_opportunities"],
        "run_health": summary["run_health"],
        "behavior_diagnostics": diagnostics,
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
