#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCENARIO_META: dict[str, dict[str, Any]] = {
    "northstar_launch_week": {
        "title": "Northstar Launch Week",
        "deck": "Flagship scenario",
        "summary": "A full launch-week TPM gauntlet where discovery, alignment, approvals, and timing all interact.",
        "accent": "#f26430",
        "story_beats": [
            "Deep scenario with official seed bundle and strong cross-model separation.",
            "Great for showing the difference between discovery, conversion, and real critical-path movement.",
        ],
    },
    "internal_rollout_smoke": {
        "title": "Internal Rollout Smoke",
        "deck": "Smoke scenario",
        "summary": "A lighter scenario used to stress-test generalization and operational stability, not headline claims.",
        "accent": "#0e9384",
        "story_beats": [
            "Useful as the follow-up: does the harness stay sharp on a smaller scenario?",
            "Good for showing that the evaluator does not hand out accidental wins.",
        ],
    },
}


MODEL_META: dict[str, dict[str, str]] = {
    "gpt-4o": {
        "label": "GPT-4o",
        "color": "#f26430",
        "shadow": "rgba(242, 100, 48, 0.28)",
    },
    "gpt-5-mini": {
        "label": "GPT-5 mini",
        "color": "#0e9384",
        "shadow": "rgba(14, 147, 132, 0.25)",
    },
    "gpt-5-nano": {
        "label": "GPT-5 nano",
        "color": "#d99904",
        "shadow": "rgba(217, 153, 4, 0.25)",
    },
    "gpt-5.4": {
        "label": "GPT-5.4",
        "color": "#1f6feb",
        "shadow": "rgba(31, 111, 235, 0.25)",
    },
}

MODEL_VARIANT_PATTERN = re.compile(r"^(?P<base>.+)-v(?P<version>\d+)$")
BUNDLE_VARIANT_PREFERENCES: dict[tuple[str, str], int] = {
    ("northstar_launch_week", "gpt-4o"): 2,
    ("northstar_launch_week", "gpt-5.4"): 2,
    ("northstar_launch_week", "gpt-5-mini"): 2,
}


PRESENTATION_CONTENT: dict[str, Any] = {
    "hero_badges": [
        "Benchmark, not roleplay",
        "SQLite-backed discrete-event runtime",
        "Policy coworkers, not runtime improv",
        "Deterministic scoring with evidence refs",
    ],
    "control_surface": [
        {
            "title": "Hold The Org Constant",
            "body": (
                "Simulated time, hidden truth, coworker behavior, tool surfaces, and scoring semantics stay fixed. "
                "The only thing that changes is the TPM model."
            ),
        },
        {
            "title": "Measure Actual TPM Work",
            "body": (
                "The question is not whether the model sounds organized. It is whether it discovers blockers, aligns "
                "stakeholders, secures credible commitments, and changes real outcomes."
            ),
        },
        {
            "title": "Benchmark Integrity First",
            "body": (
                "Models can help author the test offline, but they do not get to redefine the world while the "
                "benchmark is executing."
            ),
        },
        {
            "title": "Auditability Over Theater",
            "body": (
                "Every meaningful change is state or an event in the runtime. There is no secret truth hiding only "
                "inside a prompt."
            ),
        },
    ],
    "event_types": [
        {
            "label": "Seeded Events",
            "example": "Maya becomes less available Monday at 3 PM.",
            "body": "Scheduled into the world ahead of time so the week has authored pressure, availability shifts, and deadlines.",
        },
        {
            "label": "TPM-Triggered Events",
            "example": "A chat creates a reply event shaped by work hours, delay traits, and actor policy.",
            "body": "The TPM can change the queue, but the engine still decides when and how those consequences land.",
        },
        {
            "label": "State-Triggered Events",
            "example": "A handoff is scheduled when a task reaches the right checkpoint.",
            "body": "The world can advance because state crossed a threshold, not because the prompt improvised a scene.",
        },
    ],
    "state_layers": [
        {
            "label": "True Execution State",
            "body": "What is actually happening in the project: task truth, commitments, feasibility, milestones, blockers.",
        },
        {
            "label": "Shared Artifact State",
            "body": "What the tools say: messages, meetings, docs, tracker entries, and visible status surfaces.",
        },
        {
            "label": "Per-Actor Belief State",
            "body": "What each stakeholder believes, how confidently, and whether that belief is stale or aligned.",
        },
    ],
    "coworker_points": [
        "Each coworker has role, authority, work hours, reply-delay traits, trust, pressure, and private motivations.",
        "Interactions are matched against a compiled context model instead of asking a runtime LLM to improvise.",
        "That preserves realism where it matters for TPM work: conflicting incentives, delayed replies, partial information, and belief mismatch.",
    ],
    "authoring_steps": [
        "Structured brief",
        "Candidate world draft",
        "Reachable interaction compile",
        "Offline semantic fill",
        "Runtime coworker-policy compile",
        "Validation gate",
        "Closure suite",
        "Accept into official benchmark",
    ],
    "calibration_probes": [
        {
            "label": "golden",
            "body": "Proves the scenario has a strong path to success.",
        },
        {
            "label": "busywork",
            "body": "Proves artifact churn without unblocking does not score.",
        },
        {
            "label": "false_green",
            "body": "Proves confidence theater before discovery and alignment does not score.",
        },
        {
            "label": "spray_and_pray",
            "body": "Proves noisy, unsequenced coordination does not score.",
        },
    ],
    "evaluation_counts": [
        "Critical-path movement",
        "Blocker discovery",
        "Scope aligned on time",
        "Decision quality and credible planning",
        "Commitment capture",
        "Approval timing",
        "Stakeholder belief alignment",
    ],
    "evaluation_ignores": [
        "Artifact churn with no state change",
        "Docs that do not alter commitments or beliefs",
        "Meetings as a proxy for progress",
        "Confident prose unsupported by evidence",
    ],
}


DIAGRAM_CANDIDATES = [
    (
        "What I Built",
        "High-level benchmark framing.",
        Path("docs/diagrams/presentation_01_what_i_built.png"),
    ),
    (
        "Runtime And Realism",
        "Discrete-event semantics, time, and realism controls.",
        Path("docs/diagrams/presentation_02_runtime_and_realism.png"),
    ),
    (
        "Evaluation",
        "How deterministic scoring and TPM competency summaries fit together.",
        Path("docs/diagrams/presentation_03_evaluation.png"),
    ),
    (
        "Authoring And Scale",
        "How structured authoring scales without collapsing into prompt spaghetti.",
        Path("docs/diagrams/presentation_04_authoring_and_scale.png"),
    ),
    (
        "Rich Authoring, Frozen Runtime",
        "The authoring firewall between test creation and runtime execution.",
        Path("docs/diagrams/presentation_rich_authoring_frozen_runtime.png"),
    ),
]


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


ROOT = repo_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def slug_title(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").title()


def scenario_meta(scenario_id: str) -> dict[str, Any]:
    meta = SCENARIO_META.get(scenario_id, {})
    return {
        "id": scenario_id,
        "title": meta.get("title", slug_title(scenario_id)),
        "deck": meta.get("deck", "Scenario"),
        "summary": meta.get("summary", "Scenario summary unavailable."),
        "accent": meta.get("accent", "#1f6feb"),
        "story_beats": meta.get("story_beats", []),
    }


def model_meta(model: str) -> dict[str, str]:
    meta = MODEL_META.get(model, {})
    return {
        "id": model,
        "label": meta.get("label", model),
        "color": meta.get("color", "#1f6feb"),
        "shadow": meta.get("shadow", "rgba(31, 111, 235, 0.25)"),
    }


def relative_href(base_dir: Path, target: Path) -> str:
    return os.path.relpath(target, start=base_dir).replace(os.sep, "/")


def score_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def average(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def summarize_signal_rates(bundle: dict[str, Any]) -> dict[str, float | None]:
    rows = [row for row in bundle.get("signal_coverage_consistency", []) if row.get("criticality") == "critical"]
    surfaced = [float(row.get("surfaced_rate", 0.0)) * 100.0 for row in rows if row.get("surfaced_rate") is not None]
    converted = [float(row.get("converted_rate", 0.0)) * 100.0 for row in rows if row.get("converted_rate") is not None]
    return {
        "critical_signal_count": len(rows),
        "critical_surfaced_rate": average(surfaced),
        "critical_converted_rate": average(converted),
    }


def summarize_window_rates(runs: list[dict[str, Any]]) -> dict[str, float | None]:
    fractions: list[float] = []
    for run in runs:
        window_summary = run.get("window_summary", {})
        hit = window_summary.get("hit")
        total = window_summary.get("total")
        if total:
            fractions.append((float(hit or 0.0) / float(total)) * 100.0)
    return {
        "window_hit_rate": average(fractions),
    }


def first_dimension_score(profile: list[dict[str, Any]], dimension_id: str) -> float | None:
    for item in profile:
        if item.get("id") == dimension_id:
            return score_or_none(item.get("mean_score", item.get("score")))
    return None


def split_model_variant(value: str) -> tuple[str, int | None]:
    match = MODEL_VARIANT_PATTERN.match(value)
    if not match:
        return value, None
    return match.group("base"), int(match.group("version"))


def trim_bundle(
    bundle: dict[str, Any],
    path: Path,
    output_dir: Path,
    *,
    source_dir: Path | None = None,
) -> dict[str, Any]:
    header = bundle.get("bundle_header", {})
    bundle_dir = source_dir or (path.parent if path.is_file() else path)
    source_path = path
    dir_name = bundle_dir.name
    model_base, variant_num = split_model_variant(dir_name)
    model = header.get("model") or model_base
    existing_seed_bundle = header.get("seed_bundle", [])
    available_seed_count = len(existing_seed_bundle)
    source_note = None
    if path.parent != bundle_dir:
        source_note = (
            f"Bundle synthesized live from {available_seed_count} completed seed "
            f"{'summary' if available_seed_count == 1 else 'summaries'} in {dir_name}."
        )
    return {
        "path": source_path.as_posix(),
        "href": relative_href(output_dir, source_path),
        "bundle_dir_path": bundle_dir.as_posix(),
        "scenario_id": header.get("scenario_id"),
        "model": model,
        "seed_bundle": existing_seed_bundle,
        "headline": bundle.get("headline", {}),
        "aggregate_capability_assessment": bundle.get("aggregate_capability_assessment", {}),
        "aggregate_competency_profile": bundle.get("aggregate_competency_profile", []),
        "confidence_scope": bundle.get("confidence_scope"),
        "recurring_root_causes": bundle.get("recurring_root_causes", []),
        "signal_coverage_consistency": bundle.get("signal_coverage_consistency", []),
        "driver_signal_consistency": bundle.get("driver_signal_consistency", []),
        "stakeholder_failure_patterns": bundle.get("stakeholder_failure_patterns", []),
        "window_miss_recurrence": bundle.get("window_miss_recurrence", []),
        "reference_divergence_patterns": bundle.get("reference_divergence_patterns", []),
        "top_recurring_failure_themes": bundle.get("top_recurring_failure_themes", []),
        "private_note_audit_aggregate": bundle.get("private_note_audit_aggregate", {}),
        "harness_health": bundle.get("harness_health", {}),
        "runs": bundle.get("runs", []),
        "source_note": source_note,
        "source_dir_name": dir_name,
        "variant_num": variant_num,
        "available_seed_count": available_seed_count,
    }


def trim_run(run: dict[str, Any], path: Path, output_dir: Path) -> dict[str, Any]:
    header = run.get("run_header", {})
    window_cards = run.get("window_scorecards", [])
    windows_hit = sum(1 for item in window_cards if item.get("state_achieved", {}).get("achieved"))
    return {
        "_mtime": path.stat().st_mtime,
        "path": path.as_posix(),
        "href": relative_href(output_dir, path),
        "scenario_id": header.get("scenario_id"),
        "model": header.get("model"),
        "seed": header.get("seed"),
        "score": score_or_none(header.get("score")),
        "score_percent": score_or_none(header.get("score_percent")),
        "score_possible": score_or_none(header.get("score_possible")),
        "turns_taken": header.get("turns_taken"),
        "max_turns": header.get("max_turns"),
        "simulated_end_time": header.get("simulated_end_time"),
        "termination_reason": header.get("termination_reason"),
        "outcome_headline": run.get("outcome_verdict", {}).get("headline"),
        "capability_rating": run.get("capability_assessment", {}).get("rating"),
        "capability_direct_answer": run.get("capability_assessment", {}).get("direct_answer"),
        "critical_path_status": run.get("critical_path_result", {}).get("status"),
        "overall_status": run.get("run_health", {}).get("overall_status"),
        "model_status": run.get("run_health", {}).get("model_status"),
        "harness_status": run.get("run_health", {}).get("harness_status"),
        "critical_signal_summary": run.get("signal_coverage", {}).get("summary_metrics", {}),
        "stakeholder_summary": run.get("stakeholder_engagement", {}).get("summary_metrics", {}),
        "window_summary": {
            "hit": windows_hit,
            "total": len(window_cards),
        },
        "top_failure_title": next(
            (item.get("title") for item in run.get("key_failures", []) if isinstance(item, dict) and item.get("title")),
            None,
        ),
        "top_root_cause_title": next(
            (
                item.get("title")
                for item in run.get("root_cause_findings", [])
                if isinstance(item, dict) and item.get("title")
            ),
            None,
        ),
        "failure_dossiers": run.get("failure_dossiers", []),
        "root_cause_findings": run.get("root_cause_findings", []),
        "signal_coverage": run.get("signal_coverage", {}),
        "stakeholder_engagement": run.get("stakeholder_engagement", {}),
        "window_scorecards": window_cards,
        "decisive_timeline": run.get("decisive_timeline", []),
        "tpm_competency_profile": run.get("tpm_competency_profile", []),
        "score_breakdown": run.get("score_breakdown", {}),
        "bundle_stub": False,
    }


def bundle_row_to_stub(bundle: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    critical_observed = row.get("critical_signals_observed")
    critical_converted = row.get("critical_signals_converted")
    return {
        "path": bundle["path"],
        "href": bundle["href"],
        "scenario_id": bundle.get("scenario_id"),
        "model": bundle.get("model"),
        "seed": row.get("seed"),
        "score": score_or_none(row.get("score")),
        "score_percent": score_or_none(row.get("score_percent")),
        "score_possible": score_or_none(row.get("score_possible")),
        "turns_taken": None,
        "max_turns": None,
        "simulated_end_time": None,
        "termination_reason": None,
        "outcome_headline": row.get("outcome_verdict"),
        "capability_rating": row.get("capability_rating"),
        "capability_direct_answer": None,
        "critical_path_status": row.get("critical_path_status"),
        "overall_status": row.get("overall_status"),
        "model_status": row.get("model_status"),
        "harness_status": row.get("harness_status"),
        "critical_signal_summary": {
            "critical_observed": critical_observed,
            "critical_converted": critical_converted,
            "critical_total": row.get("critical_signals_total"),
            "critical_observed_not_converted": row.get("critical_signals_observed_not_converted", []),
            "critical_unsurfaced": row.get("critical_signals_unsurfaced", []),
        },
        "stakeholder_summary": {
            "critical_actors_never_contacted": row.get("critical_actors_never_contacted", []),
            "critical_actors_contacted_after_deadline": row.get("critical_actors_contacted_after_deadline", []),
            "direct_questions_left_unanswered": [None] * int(row.get("unanswered_direct_questions") or 0),
        },
        "window_summary": {
            "hit": row.get("windows_hit"),
            "total": row.get("windows_total"),
        },
        "top_failure_title": (row.get("top_failure_theme") or {}).get("title"),
        "top_root_cause_title": (row.get("top_root_cause") or {}).get("title"),
        "failure_dossiers": [],
        "root_cause_findings": [],
        "signal_coverage": {},
        "stakeholder_engagement": {},
        "window_scorecards": [],
        "decisive_timeline": [],
        "tpm_competency_profile": [],
        "score_breakdown": {},
        "bundle_stub": True,
    }


def run_priority(path_text: str) -> tuple[int, int, str]:
    if "/agent_runs/" in path_text:
        bucket = 0
    elif "/agent_bundle_eval/" in path_text:
        bucket = 1
    else:
        bucket = 2
    return (bucket, len(path_text), path_text)


def load_bundle_from_dir(bundle_dir: Path) -> tuple[dict[str, Any], Path] | None:
    summary_path = bundle_dir / "bundle_performance_summary.json"
    if summary_path.exists():
        return load_json(summary_path), summary_path
    run_jsons = sorted(bundle_dir.glob("seed*/tpm_performance_summary.json"))
    if not run_jsons:
        return None
    run_summaries = [load_json(path) for path in run_jsons]
    header = run_summaries[0].get("run_header", {})
    scenario_id = header.get("scenario_id")
    model = header.get("model")
    if not scenario_id or not model:
        return None
    seed_bundle = []
    for summary in run_summaries:
        seed = summary.get("run_header", {}).get("seed")
        try:
            seed_bundle.append(int(seed))
        except (TypeError, ValueError):
            continue
    from tpm_sim.performance import build_bundle_summary

    return build_bundle_summary(
        run_summaries,
        scenario_id=str(scenario_id),
        model=str(model),
        seed_bundle=seed_bundle,
    ), run_jsons[0]


def preferred_bundle_choice(
    scenario_id: str,
    model: str,
    bundles: list[dict[str, Any]],
) -> dict[str, Any]:
    desired_variant = BUNDLE_VARIANT_PREFERENCES.get((scenario_id, model))
    if desired_variant is not None:
        preferred = [bundle for bundle in bundles if bundle.get("variant_num") == desired_variant]
        if preferred:
            bundles = preferred
    return sorted(
        bundles,
        key=lambda bundle: (
            -int(bundle.get("available_seed_count") or 0),
            0 if bundle.get("source_note") is None else 1,
            -(bundle.get("variant_num") or 0),
            bundle.get("path", ""),
        ),
    )[0]


def discover_bundle_dirs(bundle_root: Path) -> list[Path]:
    discovered: list[Path] = []
    for candidate in sorted(path for path in bundle_root.rglob("*") if path.is_dir()):
        if candidate.name.startswith("seed"):
            continue
        has_bundle_summary = (candidate / "bundle_performance_summary.json").exists()
        has_seed_summaries = any(candidate.glob("seed*/tpm_performance_summary.json"))
        if has_bundle_summary or has_seed_summaries:
            discovered.append(candidate)
    return discovered


def collect_bundles(artifact_root: Path, output_dir: Path) -> list[dict[str, Any]]:
    bundle_root = artifact_root / "agent_bundle_eval"
    if not bundle_root.exists():
        return []
    candidates: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for bundle_dir in discover_bundle_dirs(bundle_root):
        loaded = load_bundle_from_dir(bundle_dir)
        if not loaded:
            continue
        raw_bundle, source_path = loaded
        bundle = trim_bundle(raw_bundle, source_path, output_dir, source_dir=bundle_dir)
        scenario_id = bundle.get("scenario_id")
        model = bundle.get("model")
        if scenario_id and model:
            candidates[(str(scenario_id), str(model))].append(bundle)
    return [
        preferred_bundle_choice(scenario_id, model, rows)
        for (scenario_id, model), rows in sorted(candidates.items())
    ]


def collect_run_candidates(artifact_root: Path, output_dir: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for path in sorted(artifact_root.rglob("tpm_performance_summary.json")):
        run = trim_run(load_json(path), path, output_dir)
        scenario_id = run.get("scenario_id")
        model = run.get("model")
        seed = run.get("seed")
        if scenario_id is None or model is None or seed is None:
            continue
        candidates.append(run)
    return candidates


def run_candidate_priority(path_text: str, bundle_dir: str | None, mtime: float | None) -> tuple[int, float, int, str]:
    if bundle_dir and path_text.startswith(bundle_dir.rstrip("/") + "/"):
        bucket = 0
    elif bundle_dir and path_text == bundle_dir:
        bucket = 0
    elif "/agent_runs/" in path_text:
        bucket = 1
    elif "/agent_bundle_eval/" in path_text:
        bucket = 2
    else:
        bucket = 3
    return (bucket, -(mtime or 0.0), len(path_text), path_text)


def build_scenarios(bundles: list[dict[str, Any]], run_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_scenario: dict[str, dict[str, Any]] = {}
    runs_by_scenario_model: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for run in run_candidates:
        runs_by_scenario_model[(str(run["scenario_id"]), str(run["model"]))].append(run)

    for bundle in bundles:
        scenario_id = str(bundle["scenario_id"])
        by_scenario.setdefault(
            scenario_id,
            {
                "id": scenario_id,
                "meta": scenario_meta(scenario_id),
                "models": [],
            },
        )

    for scenario_id, payload in by_scenario.items():
        scenario_bundles = [bundle for bundle in bundles if bundle["scenario_id"] == scenario_id]
        model_rows: list[dict[str, Any]] = []
        for bundle in scenario_bundles:
            bundle_dir = str(bundle.get("bundle_dir_path") or Path(bundle["path"]).parent)
            grouped_runs: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for run in runs_by_scenario_model.get((scenario_id, str(bundle["model"])), []):
                if run.get("seed") is None:
                    continue
                if bundle_dir and not str(run.get("path", "")).startswith(bundle_dir.rstrip("/") + "/"):
                    continue
                grouped_runs[int(run["seed"])].append(run)
            scenario_runs: list[dict[str, Any]] = []
            for seed, run_group in sorted(grouped_runs.items()):
                chosen = sorted(
                    run_group,
                    key=lambda item: run_candidate_priority(item["path"], bundle_dir, item.get("_mtime")),
                )[0]
                scenario_runs.append(chosen)
            seen_seeds = {int(item.get("seed") or 0) for item in scenario_runs if item.get("seed") is not None}
            for row in bundle.get("runs", []):
                seed = row.get("seed")
                if seed is None:
                    continue
                try:
                    seed_int = int(seed)
                except (TypeError, ValueError):
                    continue
                if seed_int not in seen_seeds:
                    scenario_runs.append(bundle_row_to_stub(bundle, row))
                    seen_seeds.add(seed_int)
            scenario_runs.sort(key=lambda item: int(item.get("seed") or 0))

            official_seeds = [int(seed) for seed in bundle.get("seed_bundle", []) if isinstance(seed, int)]
            featured_run = None
            for seed in official_seeds:
                featured_run = next((run for run in scenario_runs if int(run.get("seed") or -1) == seed), None)
                if featured_run:
                    break
            if not featured_run and scenario_runs:
                featured_run = scenario_runs[0]

            profile = bundle.get("aggregate_competency_profile", [])
            signal_summary = summarize_signal_rates(bundle)
            window_summary = summarize_window_rates(scenario_runs)
            headline = bundle.get("headline", {})
            model_rows.append(
                {
                    "id": bundle["model"],
                    "meta": model_meta(str(bundle["model"])),
                    "bundle": bundle,
                    "runs": scenario_runs,
                    "featured_run": featured_run,
                    "metrics": {
                        "mean_score": score_or_none(headline.get("mean_score")),
                        "best_score": score_or_none(headline.get("best_score")),
                        "worst_score": score_or_none(headline.get("worst_score")),
                        "stdev": score_or_none(headline.get("stdev")),
                        "critical_signal_count": signal_summary["critical_signal_count"],
                        "critical_surfaced_rate": signal_summary["critical_surfaced_rate"],
                        "critical_converted_rate": signal_summary["critical_converted_rate"],
                        "window_hit_rate": window_summary["window_hit_rate"],
                        "discovery_score": first_dimension_score(profile, "discovery_situation_awareness"),
                        "decision_score": first_dimension_score(profile, "decision_tradeoff_management"),
                        "commitment_score": first_dimension_score(profile, "commitment_dependency_management"),
                        "alignment_score": first_dimension_score(profile, "stakeholder_alignment_communication"),
                        "top_root_cause": (
                            bundle.get("recurring_root_causes", [{}])[0].get("title")
                            if bundle.get("recurring_root_causes")
                            else None
                        ),
                    },
                }
            )
        model_rows.sort(key=lambda row: score_or_none(row["metrics"].get("mean_score")) or -1.0, reverse=True)
        payload["models"] = model_rows
        if model_rows:
            payload["champion_model"] = model_rows[0]["id"]
            payload["laggard_model"] = model_rows[-1]["id"]
            champion_mean = score_or_none(model_rows[0]["metrics"].get("mean_score")) or 0.0
            laggard_mean = score_or_none(model_rows[-1]["metrics"].get("mean_score")) or 0.0
            payload["score_gap"] = round(champion_mean - laggard_mean, 2)
        else:
            payload["champion_model"] = None
            payload["laggard_model"] = None
            payload["score_gap"] = None

    scenario_rows = list(by_scenario.values())
    scenario_rows.sort(
        key=lambda row: (
            0 if row["id"] == "northstar_launch_week" else 1,
            -len(row.get("models", [])),
            row["meta"]["title"],
        )
    )
    return scenario_rows


def collect_diagrams(root: Path, output_dir: Path) -> list[dict[str, str]]:
    diagrams: list[dict[str, str]] = []
    for label, description, rel_path in DIAGRAM_CANDIDATES:
        path = root / rel_path
        if path.exists():
            diagrams.append(
                {
                    "label": label,
                    "description": description,
                    "href": relative_href(output_dir, path),
                }
            )
    return diagrams


def build_payload(root: Path, artifact_root: Path, output_path: Path) -> dict[str, Any]:
    output_dir = output_path.parent
    bundles = collect_bundles(artifact_root, output_dir)
    run_candidates = collect_run_candidates(artifact_root, output_dir)
    scenarios = build_scenarios(bundles, run_candidates)
    model_ids = sorted({bundle["model"] for bundle in bundles})
    displayed_run_count = sum(len(model.get("runs", [])) for scenario in scenarios for model in scenario.get("models", []))
    featured_scenario_id = next((row["id"] for row in scenarios if row["id"] == "northstar_launch_week"), None)
    if not featured_scenario_id and scenarios:
        featured_scenario_id = scenarios[0]["id"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifact_root": artifact_root.as_posix(),
        "overview": {
            "scenario_count": len(scenarios),
            "bundle_count": len(bundles),
            "run_count": displayed_run_count,
            "model_count": len(model_ids),
            "seed_eval_count": sum(len(bundle.get("seed_bundle", [])) for bundle in bundles),
        },
        "presentation": PRESENTATION_CONTENT,
        "scenarios": scenarios,
        "diagrams": collect_diagrams(root, output_dir),
        "featured_scenario_id": featured_scenario_id,
    }


HTML_TEMPLATE = textwrap.dedent(
    """\
    <!DOCTYPE html>
    <html lang="en">
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>TPM Benchmark Signal Room</title>
        <style>
          :root {
            --paper: #f4efe4;
            --paper-strong: #fffaf0;
            --ink: #162133;
            --ink-soft: #5f6876;
            --panel: rgba(255, 251, 244, 0.82);
            --panel-strong: rgba(255, 255, 255, 0.92);
            --line: rgba(22, 33, 51, 0.12);
            --navy: #162133;
            --teal: #0e9384;
            --copper: #f26430;
            --gold: #d99904;
            --sky: #1f6feb;
            --shadow: 0 22px 60px rgba(22, 33, 51, 0.10);
            --radius-xl: 34px;
            --radius-lg: 24px;
            --radius-md: 18px;
            --radius-sm: 12px;
            --content-width: 1280px;
          }

          * {
            box-sizing: border-box;
          }

          html {
            scroll-behavior: smooth;
          }

          body {
            margin: 0;
            min-height: 100vh;
            color: var(--ink);
            background:
              radial-gradient(circle at top left, rgba(14, 147, 132, 0.18), transparent 36%),
              radial-gradient(circle at top right, rgba(242, 100, 48, 0.18), transparent 32%),
              linear-gradient(180deg, #faf5eb 0%, #f4efe4 54%, #efe6d6 100%);
            font-family: "Avenir Next", "Segoe UI", "Helvetica Neue", sans-serif;
            line-height: 1.45;
          }

          body::before {
            content: "";
            position: fixed;
            inset: 0;
            pointer-events: none;
            opacity: 0.42;
            background-image:
              linear-gradient(rgba(22, 33, 51, 0.03) 1px, transparent 1px),
              linear-gradient(90deg, rgba(22, 33, 51, 0.03) 1px, transparent 1px),
              radial-gradient(circle at 20% 20%, rgba(255, 255, 255, 0.55) 0.7px, transparent 0.8px);
            background-size: 28px 28px, 28px 28px, 18px 18px;
            mix-blend-mode: multiply;
          }

          a {
            color: inherit;
          }

          .ambient {
            position: fixed;
            inset: 0;
            pointer-events: none;
            z-index: 0;
          }

          .orb {
            position: absolute;
            border-radius: 999px;
            filter: blur(18px);
            opacity: 0.42;
            animation: drift 20s ease-in-out infinite;
          }

          .orb-a {
            width: 280px;
            height: 280px;
            top: 6%;
            right: 10%;
            background: radial-gradient(circle, rgba(242, 100, 48, 0.38), transparent 72%);
          }

          .orb-b {
            width: 240px;
            height: 240px;
            top: 46%;
            left: -4%;
            background: radial-gradient(circle, rgba(14, 147, 132, 0.34), transparent 70%);
            animation-delay: -6s;
          }

          .orb-c {
            width: 340px;
            height: 340px;
            bottom: -4%;
            right: 26%;
            background: radial-gradient(circle, rgba(31, 111, 235, 0.18), transparent 72%);
            animation-delay: -11s;
          }

          @keyframes drift {
            0%, 100% { transform: translate3d(0, 0, 0) scale(1); }
            50% { transform: translate3d(0, -18px, 0) scale(1.04); }
          }

          .topbar {
            position: sticky;
            top: 0;
            z-index: 20;
            backdrop-filter: blur(16px);
            background: rgba(250, 245, 235, 0.72);
            border-bottom: 1px solid rgba(22, 33, 51, 0.08);
          }

          .topbar-inner {
            max-width: var(--content-width);
            margin: 0 auto;
            padding: 14px 28px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 20px;
          }

          .brand {
            display: flex;
            align-items: center;
            gap: 14px;
            font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", serif;
            font-weight: 700;
            letter-spacing: 0.03em;
            font-size: 1.05rem;
          }

          .brand-mark {
            width: 40px;
            height: 40px;
            border-radius: 14px;
            background:
              linear-gradient(140deg, rgba(242, 100, 48, 0.95), rgba(14, 147, 132, 0.9));
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.45), 0 10px 28px rgba(22, 33, 51, 0.18);
            position: relative;
            overflow: hidden;
          }

          .brand-mark::after {
            content: "";
            position: absolute;
            inset: 7px;
            border: 1px solid rgba(255, 255, 255, 0.58);
            border-radius: 11px;
          }

          .nav-links {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
          }

          .nav-links a {
            text-decoration: none;
            padding: 10px 14px;
            border-radius: 999px;
            color: var(--ink-soft);
            background: rgba(255, 255, 255, 0.52);
            border: 1px solid rgba(22, 33, 51, 0.06);
            transition: transform 180ms ease, color 180ms ease, background 180ms ease;
          }

          .nav-links a:hover {
            transform: translateY(-1px);
            color: var(--ink);
            background: rgba(255, 255, 255, 0.9);
          }

          .page {
            position: relative;
            z-index: 1;
          }

          .hero {
            max-width: var(--content-width);
            margin: 0 auto;
            padding: 52px 28px 18px;
            display: grid;
            grid-template-columns: minmax(0, 1.12fr) minmax(360px, 0.88fr);
            gap: 28px;
            align-items: stretch;
          }

          .hero-copy,
          .hero-pulse {
            border-radius: var(--radius-xl);
            background: linear-gradient(180deg, rgba(255, 255, 255, 0.78), rgba(255, 250, 240, 0.72));
            border: 1px solid rgba(22, 33, 51, 0.09);
            box-shadow: var(--shadow);
            padding: 34px;
            position: relative;
            overflow: hidden;
          }

          .hero-copy::after,
          .hero-pulse::after,
          .panel::after,
          .story-card::after,
          .event-card::after,
          .state-card::after,
          .probe-card::after {
            content: "";
            position: absolute;
            inset: 0;
            border-radius: inherit;
            padding: 1px;
            background: linear-gradient(135deg, rgba(255, 255, 255, 0.75), transparent 42%, rgba(22, 33, 51, 0.08));
            -webkit-mask:
              linear-gradient(#fff 0 0) content-box,
              linear-gradient(#fff 0 0);
            -webkit-mask-composite: xor;
            mask-composite: exclude;
            pointer-events: none;
          }

          .eyebrow {
            display: inline-flex;
            align-items: center;
            gap: 10px;
            font-size: 0.78rem;
            text-transform: uppercase;
            letter-spacing: 0.22em;
            color: rgba(22, 33, 51, 0.72);
            font-weight: 700;
            margin-bottom: 16px;
          }

          .eyebrow::before {
            content: "";
            width: 34px;
            height: 2px;
            border-radius: 999px;
            background: linear-gradient(90deg, var(--copper), var(--teal));
          }

          .hero h1 {
            margin: 0 0 16px;
            font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", serif;
            font-weight: 700;
            line-height: 0.98;
            letter-spacing: -0.03em;
            font-size: clamp(2.8rem, 7vw, 5.4rem);
            max-width: 14ch;
          }

          .hero p {
            margin: 0;
            color: var(--ink-soft);
            font-size: 1.05rem;
            max-width: 64ch;
          }

          .hero-badges {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin-top: 24px;
          }

          .hero-badge {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 10px 14px;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.72);
            border: 1px solid rgba(22, 33, 51, 0.08);
            color: rgba(22, 33, 51, 0.84);
            font-size: 0.92rem;
          }

          .hero-badge::before {
            content: "";
            width: 8px;
            height: 8px;
            border-radius: 999px;
            background: linear-gradient(135deg, var(--copper), var(--teal));
          }

          .hero-pulse {
            display: grid;
            gap: 18px;
            background:
              radial-gradient(circle at top right, rgba(242, 100, 48, 0.15), transparent 44%),
              radial-gradient(circle at bottom left, rgba(14, 147, 132, 0.16), transparent 44%),
              linear-gradient(180deg, rgba(22, 33, 51, 0.96), rgba(17, 27, 44, 0.92));
            color: #fff8ef;
          }

          .hero-pulse h2 {
            margin: 0;
            font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", serif;
            font-size: 1.6rem;
          }

          .hero-pulse p {
            color: rgba(255, 248, 239, 0.78);
            margin: 0;
          }

          .hero-stats-grid,
          .hero-showdown-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 14px;
          }

          .hero-stat,
          .hero-showdown-card {
            border-radius: var(--radius-lg);
            border: 1px solid rgba(255, 248, 239, 0.10);
            background: rgba(255, 255, 255, 0.08);
            padding: 18px;
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.05);
          }

          .hero-stat-label,
          .hero-showdown-label {
            font-size: 0.74rem;
            letter-spacing: 0.15em;
            text-transform: uppercase;
            color: rgba(255, 248, 239, 0.58);
            margin-bottom: 8px;
          }

          .hero-stat-value {
            font-size: 2rem;
            font-weight: 700;
          }

          .hero-showdown-card strong {
            display: block;
            font-size: 2.4rem;
            line-height: 1;
            margin-bottom: 8px;
          }

          .hero-showdown-card span {
            display: block;
            color: rgba(255, 248, 239, 0.74);
            font-size: 0.92rem;
          }

          .hero-callout {
            border-radius: 22px;
            padding: 18px 20px;
            background: linear-gradient(135deg, rgba(255, 255, 255, 0.10), rgba(255, 255, 255, 0.05));
            border: 1px solid rgba(255, 248, 239, 0.10);
            color: rgba(255, 248, 239, 0.92);
          }

          .hero-callout strong {
            display: block;
            font-size: 1rem;
            margin-bottom: 8px;
          }

          .section {
            max-width: var(--content-width);
            margin: 0 auto;
            padding: 34px 28px 20px;
          }

          .section-ink {
            padding-top: 42px;
            padding-bottom: 28px;
          }

          .section-shell {
            border-radius: var(--radius-xl);
            padding: 28px;
            background: var(--panel);
            border: 1px solid rgba(22, 33, 51, 0.09);
            box-shadow: var(--shadow);
          }

          .section-ink .section-shell {
            background:
              radial-gradient(circle at top left, rgba(14, 147, 132, 0.16), transparent 38%),
              radial-gradient(circle at bottom right, rgba(242, 100, 48, 0.12), transparent 34%),
              linear-gradient(180deg, rgba(22, 33, 51, 0.96), rgba(17, 27, 44, 0.94));
            color: #fff8ef;
            border-color: rgba(255, 255, 255, 0.08);
          }

          .section-heading {
            display: flex;
            flex-wrap: wrap;
            align-items: flex-end;
            justify-content: space-between;
            gap: 16px;
            margin-bottom: 26px;
          }

          .section-heading h2 {
            margin: 0;
            font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", serif;
            font-size: clamp(2rem, 4vw, 3.2rem);
            line-height: 1;
            letter-spacing: -0.03em;
          }

          .section-heading p {
            max-width: 62ch;
            margin: 0;
            color: inherit;
            opacity: 0.78;
          }

          .grid-4 {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 16px;
          }

          .grid-3 {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 16px;
          }

          .story-card,
          .event-card,
          .state-card,
          .probe-card,
          .panel {
            position: relative;
            overflow: hidden;
            border-radius: var(--radius-lg);
            background: var(--panel-strong);
            border: 1px solid rgba(22, 33, 51, 0.08);
            padding: 22px;
            box-shadow: 0 16px 38px rgba(22, 33, 51, 0.08);
          }

          .section-ink .story-card,
          .section-ink .event-card,
          .section-ink .state-card,
          .section-ink .probe-card,
          .section-ink .panel {
            background: rgba(255, 255, 255, 0.08);
            border-color: rgba(255, 248, 239, 0.10);
            box-shadow: none;
          }

          .story-card h3,
          .event-card h3,
          .state-card h3,
          .probe-card h3,
          .panel h3 {
            margin: 0 0 10px;
            font-size: 1.05rem;
            letter-spacing: -0.01em;
          }

          .story-card p,
          .event-card p,
          .state-card p,
          .probe-card p,
          .panel p,
          .panel li {
            margin: 0;
            color: inherit;
            opacity: 0.8;
          }

          .story-index,
          .probe-label {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 40px;
            height: 40px;
            border-radius: 14px;
            margin-bottom: 14px;
            font-weight: 700;
            background: rgba(242, 100, 48, 0.12);
            color: var(--copper);
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.6);
          }

          .section-ink .story-index,
          .section-ink .probe-label {
            background: rgba(255, 248, 239, 0.10);
            color: #fff8ef;
            box-shadow: none;
          }

          .event-stack,
          .state-stack,
          .probe-grid {
            display: grid;
            gap: 16px;
          }

          .event-card strong,
          .state-card strong,
          .probe-card strong {
            display: block;
            margin-bottom: 10px;
            font-size: 0.92rem;
            color: inherit;
          }

          .state-card {
            padding-top: 28px;
          }

          .state-card::before {
            content: "";
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 6px;
            background: linear-gradient(90deg, var(--copper), var(--teal), var(--sky));
          }

          .coworker-box {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 18px;
            margin-top: 18px;
          }

          .bullet-list {
            display: grid;
            gap: 12px;
            padding: 0;
            margin: 0;
            list-style: none;
          }

          .bullet-list li {
            position: relative;
            padding-left: 18px;
          }

          .bullet-list li::before {
            content: "";
            position: absolute;
            left: 0;
            top: 0.62em;
            width: 8px;
            height: 8px;
            border-radius: 999px;
            background: linear-gradient(135deg, var(--copper), var(--teal));
          }

          .timeline-pipeline {
            display: grid;
            grid-template-columns: repeat(8, minmax(0, 1fr));
            gap: 10px;
            margin-top: 18px;
          }

          .pipeline-step {
            position: relative;
            border-radius: 18px;
            padding: 18px 14px;
            background: rgba(255, 255, 255, 0.68);
            border: 1px solid rgba(22, 33, 51, 0.08);
            min-height: 112px;
          }

          .pipeline-step strong {
            display: block;
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.13em;
            color: rgba(22, 33, 51, 0.52);
            margin-bottom: 12px;
          }

          .pipeline-step span {
            font-size: 0.96rem;
            display: block;
          }

          .pipeline-step:not(:last-child)::after {
            content: "";
            position: absolute;
            top: 50%;
            right: -12px;
            width: 24px;
            height: 2px;
            background: linear-gradient(90deg, rgba(22, 33, 51, 0.16), rgba(22, 33, 51, 0.03));
          }

          .reward-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 18px;
          }

          .reward-panel h3 {
            margin-bottom: 14px;
          }

          .reward-panel ul {
            margin: 0;
            padding-left: 18px;
            display: grid;
            gap: 10px;
          }

          .rubric-shell {
            display: grid;
            gap: 18px;
            margin-top: 18px;
          }

          .rubric-reference {
            margin: 2px 0 0;
            color: rgba(255, 248, 239, 0.78);
            max-width: 72ch;
          }

          .rubric-reference strong {
            color: var(--paper);
          }

          .rubric-meta {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 14px;
            margin: 20px 0 16px;
          }

          .rubric-stat {
            padding: 16px 18px;
            border-radius: 20px;
            background: rgba(255, 255, 255, 0.06);
            border: 1px solid rgba(255, 255, 255, 0.08);
          }

          .rubric-stat-label {
            font-size: 0.78rem;
            text-transform: uppercase;
            letter-spacing: 0.14em;
            color: rgba(255, 248, 239, 0.62);
            margin-bottom: 8px;
          }

          .rubric-stat-value {
            font-size: clamp(1.4rem, 2vw, 1.9rem);
            font-weight: 700;
            color: var(--paper);
          }

          .rubric-note {
            margin: 0 0 18px;
            color: rgba(255, 248, 239, 0.82);
            max-width: 80ch;
          }

          .rubric-group-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 14px;
          }

          .rubric-group-card {
            border-radius: 22px;
            padding: 18px;
            background: rgba(255, 255, 255, 0.06);
            border: 1px solid rgba(255, 255, 255, 0.08);
            display: grid;
            gap: 12px;
          }

          .rubric-group-card h3 {
            margin: 0;
            font-size: 1.02rem;
            color: var(--paper);
          }

          .rubric-group-weight {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 10px;
          }

          .rubric-group-weight strong {
            font-size: 1.45rem;
            color: var(--paper);
          }

          .rubric-group-copy {
            color: rgba(255, 248, 239, 0.72);
            font-size: 0.92rem;
            line-height: 1.5;
          }

          .rubric-lines-grid {
            display: grid;
            gap: 12px;
          }

          .rubric-line-card {
            border-radius: 20px;
            padding: 18px;
            background: rgba(255, 255, 255, 0.84);
            border: 1px solid rgba(22, 33, 51, 0.08);
            box-shadow: 0 14px 30px rgba(4, 12, 24, 0.06);
          }

          .rubric-line-head {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 16px;
            margin-bottom: 10px;
          }

          .rubric-line-title {
            margin: 0;
            font-size: 1rem;
            line-height: 1.35;
            color: var(--ink);
          }

          .rubric-pill {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            white-space: nowrap;
            border-radius: 999px;
            padding: 7px 11px;
            font-size: 0.82rem;
            font-weight: 700;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            background: rgba(31, 111, 235, 0.12);
            color: #124b9c;
          }

          .rubric-line-copy {
            margin: 0;
            color: rgba(22, 33, 51, 0.78);
            line-height: 1.58;
          }

          .rubric-line-footer {
            margin-top: 12px;
          }

          .rubric-dimension-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 16px;
          }

          .rubric-dimension-card {
            display: grid;
            gap: 14px;
          }

          .rubric-dimension-copy {
            margin: 0;
            color: rgba(255, 248, 239, 0.82);
            line-height: 1.58;
          }

          .rubric-dual-copy {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 14px;
          }

          .rubric-dual-copy > div {
            border-radius: 18px;
            padding: 14px;
            background: rgba(255, 255, 255, 0.06);
            border: 1px solid rgba(255, 255, 255, 0.08);
          }

          .rubric-dual-copy strong {
            display: block;
            margin-bottom: 8px;
            font-size: 0.76rem;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            color: rgba(255, 248, 239, 0.64);
          }

          .rubric-dual-copy p {
            margin: 0;
            color: rgba(255, 248, 239, 0.84);
            line-height: 1.55;
          }

          .results-topline {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 12px;
            margin-bottom: 18px;
          }

          .scenario-switcher,
          .model-switcher {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin-bottom: 18px;
          }

          .scenario-button,
          .model-button {
            border: 0;
            cursor: pointer;
            border-radius: 999px;
            padding: 11px 16px;
            background: rgba(255, 255, 255, 0.72);
            border: 1px solid rgba(22, 33, 51, 0.09);
            color: var(--ink-soft);
            font: inherit;
            transition: transform 180ms ease, background 180ms ease, color 180ms ease, box-shadow 180ms ease;
          }

          .scenario-button:hover,
          .model-button:hover {
            transform: translateY(-1px);
            background: rgba(255, 255, 255, 0.96);
            color: var(--ink);
          }

          .scenario-button.is-active,
          .model-button.is-active {
            color: white;
            background: linear-gradient(135deg, var(--copper), var(--teal));
            box-shadow: 0 16px 30px rgba(22, 33, 51, 0.16);
          }

          .results-summary {
            display: grid;
            gap: 16px;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            margin-bottom: 20px;
          }

          .model-capsule {
            position: relative;
            overflow: hidden;
            border-radius: var(--radius-lg);
            background: rgba(255, 255, 255, 0.86);
            border: 1px solid rgba(22, 33, 51, 0.08);
            box-shadow: 0 16px 38px rgba(22, 33, 51, 0.08);
            padding: 20px;
          }

          .model-capsule::before {
            content: "";
            position: absolute;
            left: 0;
            top: 0;
            bottom: 0;
            width: 6px;
            background: var(--model-color, var(--sky));
          }

          .capsule-head {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 12px;
          }

          .capsule-title {
            font-weight: 700;
            font-size: 1.1rem;
          }

          .capsule-subtitle {
            color: var(--ink-soft);
            font-size: 0.88rem;
          }

          .capsule-score {
            font-size: 2.5rem;
            font-weight: 700;
            line-height: 1;
            color: var(--model-color, var(--sky));
          }

          .capsule-source {
            margin-top: 12px;
            font-size: 0.8rem;
            color: var(--ink-soft);
            line-height: 1.45;
            overflow-wrap: anywhere;
          }

          .capsule-source a {
            text-decoration: underline;
            text-decoration-thickness: 1px;
            text-underline-offset: 0.18em;
          }

          .section-ink .capsule-source {
            color: rgba(255, 248, 239, 0.72);
          }

          .metric-stack {
            display: grid;
            gap: 10px;
            margin-top: 16px;
          }

          .metric-row {
            display: grid;
            gap: 6px;
          }

          .metric-label-line {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            font-size: 0.88rem;
          }

          .meter {
            position: relative;
            height: 10px;
            border-radius: 999px;
            overflow: hidden;
            background: rgba(22, 33, 51, 0.08);
          }

          .meter > span {
            display: block;
            height: 100%;
            border-radius: inherit;
            background: linear-gradient(90deg, var(--model-color, var(--sky)), rgba(255, 255, 255, 0.82));
          }

          .capsule-foot {
            margin-top: 14px;
            display: flex;
            justify-content: space-between;
            gap: 12px;
            font-size: 0.84rem;
            color: var(--ink-soft);
          }

          .panel-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 18px;
            margin-top: 18px;
          }

          .panel {
            background: rgba(255, 255, 255, 0.88);
          }

          .section-ink .panel {
            background: rgba(255, 255, 255, 0.08);
          }

          .panel-head {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 16px;
          }

          .panel-head strong,
          .panel-head span {
            display: inline-block;
          }

          .panel-head strong {
            font-size: 0.78rem;
            letter-spacing: 0.16em;
            text-transform: uppercase;
            opacity: 0.6;
          }

          .skyline {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 14px;
            align-items: end;
            min-height: 320px;
          }

          .skyline-card {
            border-radius: 22px;
            padding: 16px;
            background: rgba(22, 33, 51, 0.04);
            border: 1px solid rgba(22, 33, 51, 0.07);
            display: grid;
            gap: 12px;
            min-height: 320px;
          }

          .section-ink .skyline-card {
            background: rgba(255, 255, 255, 0.05);
            border-color: rgba(255, 248, 239, 0.10);
          }

          .skyline-chart {
            position: relative;
            min-height: 220px;
            display: flex;
            align-items: end;
            justify-content: center;
            padding: 12px 0 10px;
          }

          .skyline-chart::before,
          .skyline-chart::after {
            content: "";
            position: absolute;
            left: 0;
            right: 0;
            border-top: 1px dashed rgba(22, 33, 51, 0.10);
          }

          .section-ink .skyline-chart::before,
          .section-ink .skyline-chart::after {
            border-color: rgba(255, 248, 239, 0.10);
          }

          .skyline-chart::before {
            bottom: 25%;
          }

          .skyline-chart::after {
            bottom: 50%;
          }

          .skyline-range {
            position: absolute;
            width: 18px;
            border-radius: 999px;
            background: rgba(22, 33, 51, 0.14);
            left: 50%;
            transform: translateX(-50%);
          }

          .section-ink .skyline-range {
            background: rgba(255, 248, 239, 0.16);
          }

          .skyline-bar {
            width: 72px;
            border-radius: 24px 24px 18px 18px;
            background: linear-gradient(180deg, color-mix(in srgb, var(--model-color, var(--sky)) 85%, white), color-mix(in srgb, var(--model-color, var(--sky)) 68%, black 6%));
            box-shadow: 0 18px 40px color-mix(in srgb, var(--model-color, var(--sky)) 26%, transparent);
            position: relative;
          }

          .skyline-bar::after {
            content: "";
            position: absolute;
            inset: 10px 10px auto 10px;
            height: 24px;
            border-radius: 18px;
            background: rgba(255, 255, 255, 0.28);
          }

          .skyline-label strong {
            display: block;
            font-size: 1.05rem;
          }

          .skyline-label span {
            color: inherit;
            opacity: 0.7;
            font-size: 0.88rem;
          }

          .skyline-meta {
            display: grid;
            gap: 6px;
            font-size: 0.82rem;
            opacity: 0.84;
          }

          .radar-shell {
            display: grid;
            grid-template-columns: minmax(0, 1fr) minmax(230px, 280px);
            gap: 12px;
            align-items: center;
          }

          .radar-legend {
            display: grid;
            gap: 10px;
            align-content: start;
          }

          .legend-item {
            display: grid;
            gap: 4px;
            border-radius: 18px;
            padding: 12px 14px;
            background: rgba(22, 33, 51, 0.04);
            border: 1px solid rgba(22, 33, 51, 0.07);
          }

          .section-ink .legend-item {
            background: rgba(255, 255, 255, 0.05);
            border-color: rgba(255, 248, 239, 0.10);
          }

          .legend-title {
            display: flex;
            align-items: center;
            gap: 10px;
            font-weight: 700;
          }

          .legend-dot {
            width: 12px;
            height: 12px;
            border-radius: 999px;
            background: var(--model-color, var(--sky));
          }

          .legend-copy {
            font-size: 0.84rem;
            opacity: 0.8;
          }

          .signal-lattice {
            display: grid;
            gap: 12px;
          }

          .signal-row {
            display: grid;
            grid-template-columns: minmax(240px, 1.3fr) repeat(var(--signal-cols, 1), minmax(0, 1fr));
            gap: 12px;
            align-items: stretch;
          }

          .signal-heading {
            font-size: 0.78rem;
            letter-spacing: 0.14em;
            text-transform: uppercase;
            opacity: 0.6;
            margin-bottom: 6px;
          }

          .signal-label-card,
          .signal-cell {
            border-radius: 18px;
            padding: 14px;
            background: rgba(22, 33, 51, 0.04);
            border: 1px solid rgba(22, 33, 51, 0.07);
          }

          .section-ink .signal-label-card,
          .section-ink .signal-cell {
            background: rgba(255, 255, 255, 0.05);
            border-color: rgba(255, 248, 239, 0.10);
          }

          .signal-kind {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            font-size: 0.74rem;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            opacity: 0.66;
            margin-top: 8px;
          }

          .signal-kind::before {
            content: "";
            width: 8px;
            height: 8px;
            border-radius: 999px;
            background: linear-gradient(135deg, var(--copper), var(--gold));
          }

          .signal-cell h4 {
            margin: 0 0 10px;
            font-size: 0.96rem;
          }

          .signal-meter-wrap {
            display: grid;
            gap: 8px;
          }

          .signal-meter-label {
            display: flex;
            justify-content: space-between;
            gap: 8px;
            font-size: 0.82rem;
          }

          .fault-grid {
            display: grid;
            gap: 14px;
          }

          .fault-card {
            border-radius: 20px;
            padding: 16px;
            border: 1px solid rgba(22, 33, 51, 0.08);
            background: rgba(22, 33, 51, 0.04);
          }

          .section-ink .fault-card {
            background: rgba(255, 255, 255, 0.05);
            border-color: rgba(255, 248, 239, 0.10);
          }

          .fault-card + .fault-card {
            margin-top: 4px;
          }

          .fault-head {
            display: flex;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 12px;
          }

          .fault-head strong {
            font-size: 1rem;
          }

          .fault-badge {
            padding: 6px 10px;
            border-radius: 999px;
            font-size: 0.74rem;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            color: white;
            background: var(--model-color, var(--sky));
          }

          .fault-list {
            display: grid;
            gap: 10px;
          }

          .fault-item {
            display: grid;
            gap: 6px;
          }

          .fault-item-title {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            font-size: 0.92rem;
          }

          .fault-item-meta {
            font-size: 0.82rem;
            opacity: 0.76;
          }

          .seed-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9rem;
          }

          .seed-table th,
          .seed-table td {
            text-align: left;
            padding: 12px 10px;
            border-bottom: 1px solid rgba(22, 33, 51, 0.08);
            vertical-align: top;
          }

          .section-ink .seed-table th,
          .section-ink .seed-table td {
            border-color: rgba(255, 248, 239, 0.10);
          }

          .seed-table th {
            font-size: 0.76rem;
            letter-spacing: 0.14em;
            text-transform: uppercase;
            opacity: 0.62;
          }

          .mono {
            font-family: "SFMono-Regular", "IBM Plex Mono", "Menlo", monospace;
          }

          .autopsy-shell {
            display: grid;
            gap: 18px;
          }

          .autopsy-header {
            display: grid;
            gap: 16px;
            grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.85fr);
          }

          .run-scorecard {
            border-radius: 28px;
            padding: 22px;
            background: rgba(255, 255, 255, 0.10);
            border: 1px solid rgba(255, 248, 239, 0.10);
          }

          .run-scorecard h3 {
            margin: 0 0 10px;
            font-size: 1.4rem;
            font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", serif;
          }

          .run-score {
            display: flex;
            align-items: baseline;
            gap: 12px;
            margin-bottom: 12px;
          }

          .run-score strong {
            font-size: 3.2rem;
            line-height: 0.95;
          }

          .run-score span {
            color: rgba(255, 248, 239, 0.72);
          }

          .run-metrics {
            display: grid;
            grid-template-columns: minmax(240px, 1.45fr) repeat(3, minmax(132px, 1fr));
            gap: 12px;
            margin-top: 14px;
          }

          .run-metric {
            border-radius: 18px;
            padding: 12px;
            background: rgba(255, 255, 255, 0.06);
            border: 1px solid rgba(255, 248, 239, 0.08);
          }

          .run-metric-label {
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.14em;
            opacity: 0.6;
            margin-bottom: 8px;
          }

          .run-metric strong {
            display: block;
            font-size: 1.3rem;
            line-height: 1.12;
          }

          .run-metric-value {
            display: block;
            font-size: 1.34rem;
            font-weight: 700;
            letter-spacing: -0.02em;
          }

          .run-metric-value-text {
            font-size: 1.02rem;
            line-height: 1.2;
            overflow-wrap: anywhere;
          }

          .run-metric-sub {
            margin-top: 8px;
            font-size: 0.8rem;
            color: rgba(255, 248, 239, 0.66);
            line-height: 1.35;
          }

          .run-fingerprint {
            border-radius: 28px;
            padding: 22px;
            background: rgba(255, 255, 255, 0.10);
            border: 1px solid rgba(255, 248, 239, 0.10);
          }

          .run-fingerprint-grid {
            display: grid;
            gap: 10px;
          }

          .autopsy-grid {
            display: grid;
            grid-template-columns: 1.1fr 0.9fr;
            gap: 18px;
          }

          .dossier-grid,
          .window-grid,
          .actor-grid,
          .timeline-grid {
            display: grid;
            gap: 14px;
          }

          .dossier-card,
          .window-card,
          .actor-card,
          .timeline-card {
            border-radius: 22px;
            padding: 18px;
            border: 1px solid rgba(255, 248, 239, 0.10);
            background: rgba(255, 255, 255, 0.08);
          }

          .dossier-head,
          .window-head,
          .actor-head {
            display: flex;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 10px;
          }

          .severity-chip,
          .status-chip {
            padding: 6px 10px;
            border-radius: 999px;
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            color: white;
          }

          .severity-critical {
            background: #c03822;
          }

          .severity-high {
            background: #d99904;
          }

          .severity-medium {
            background: #0e9384;
          }

          .status-hit {
            background: #0e9384;
          }

          .status-miss {
            background: #c03822;
          }

          .chip-row {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 12px;
          }

          .chip {
            padding: 7px 10px;
            border-radius: 999px;
            font-size: 0.78rem;
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 248, 239, 0.10);
          }

          .actor-stat-line {
            display: flex;
            justify-content: space-between;
            gap: 12px;
            font-size: 0.84rem;
            margin: 10px 0 6px;
          }

          .timeline-grid {
            position: relative;
          }

          .timeline-grid::before {
            content: "";
            position: absolute;
            left: 15px;
            top: 8px;
            bottom: 8px;
            width: 2px;
            background: rgba(255, 248, 239, 0.14);
          }

          .timeline-card {
            position: relative;
            padding-left: 38px;
          }

          .timeline-card::before {
            content: "";
            position: absolute;
            left: 8px;
            top: 18px;
            width: 16px;
            height: 16px;
            border-radius: 999px;
            background: linear-gradient(135deg, var(--copper), var(--teal));
            box-shadow: 0 0 0 5px rgba(255, 248, 239, 0.05);
          }

          .timeline-time {
            font-size: 0.74rem;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            opacity: 0.64;
            margin-bottom: 6px;
          }

          .gallery-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 16px;
          }

          .gallery-card {
            border-radius: 22px;
            overflow: hidden;
            border: 1px solid rgba(22, 33, 51, 0.08);
            background: rgba(255, 255, 255, 0.88);
            box-shadow: 0 16px 38px rgba(22, 33, 51, 0.08);
            cursor: zoom-in;
          }

          .gallery-card img {
            display: block;
            width: 100%;
            aspect-ratio: 16 / 10;
            object-fit: cover;
            background: linear-gradient(135deg, rgba(22, 33, 51, 0.08), rgba(22, 33, 51, 0.02));
          }

          .gallery-card-copy {
            padding: 16px;
          }

          .gallery-card-copy strong {
            display: block;
            margin-bottom: 8px;
          }

          .footer {
            max-width: var(--content-width);
            margin: 0 auto;
            padding: 24px 28px 48px;
            color: var(--ink-soft);
            font-size: 0.88rem;
          }

          .footer code {
            padding: 3px 7px;
            border-radius: 999px;
            background: rgba(22, 33, 51, 0.06);
          }

          .lightbox {
            position: fixed;
            inset: 0;
            display: none;
            place-items: center;
            padding: 28px;
            background: rgba(7, 10, 16, 0.82);
            backdrop-filter: blur(10px);
            z-index: 60;
          }

          .lightbox.is-open {
            display: grid;
          }

          .lightbox-frame {
            width: min(1200px, 100%);
            max-height: calc(100vh - 56px);
            border-radius: 28px;
            overflow: hidden;
            background: #101626;
            box-shadow: 0 30px 90px rgba(0, 0, 0, 0.34);
            display: grid;
            grid-template-rows: auto minmax(0, 1fr);
          }

          .lightbox-head {
            padding: 16px 18px;
            color: white;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            background: rgba(255, 255, 255, 0.06);
          }

          .lightbox-head button {
            border: 0;
            border-radius: 999px;
            padding: 10px 14px;
            font: inherit;
            cursor: pointer;
            background: rgba(255, 255, 255, 0.10);
            color: white;
          }

          .lightbox-body {
            min-height: 0;
            overflow: auto;
            display: grid;
            place-items: center;
            padding: 20px;
          }

          .lightbox-body img {
            display: block;
            width: min(100%, 1100px);
            height: auto;
            border-radius: 20px;
          }

          .reveal {
            opacity: 0;
            transform: translateY(28px);
            transition: opacity 540ms ease, transform 540ms ease;
          }

          .reveal.is-visible {
            opacity: 1;
            transform: translateY(0);
          }

          @media (max-width: 1120px) {
            .hero,
            .autopsy-header,
            .autopsy-grid,
            .panel-grid,
            .grid-4,
            .grid-3,
            .reward-grid,
            .rubric-meta,
            .rubric-group-grid,
            .rubric-dimension-grid,
            .coworker-box,
            .radar-shell {
              grid-template-columns: 1fr;
            }

            .timeline-pipeline {
              grid-template-columns: repeat(2, minmax(0, 1fr));
            }
          }

          @media (max-width: 760px) {
            .topbar-inner,
            .hero,
            .section,
            .footer {
              padding-left: 18px;
              padding-right: 18px;
            }

            .hero-copy,
            .hero-pulse,
            .section-shell {
              padding: 22px;
            }

            .hero h1 {
              font-size: clamp(2.4rem, 13vw, 4rem);
            }

            .hero-stats-grid,
            .hero-showdown-grid,
            .run-metrics {
              grid-template-columns: 1fr 1fr;
            }

            .timeline-pipeline {
              grid-template-columns: 1fr;
            }

            .signal-row {
              grid-template-columns: 1fr;
            }

            .rubric-dual-copy {
              grid-template-columns: 1fr;
            }

            .seed-table {
              display: block;
              overflow-x: auto;
              white-space: nowrap;
            }
          }
        </style>
      </head>
      <body>
        <div class="ambient" aria-hidden="true">
          <div class="orb orb-a"></div>
          <div class="orb orb-b"></div>
          <div class="orb orb-c"></div>
        </div>
        <div class="page">
          <header class="topbar">
            <div class="topbar-inner">
              <div class="brand">
                <div class="brand-mark" aria-hidden="true"></div>
                <div>TPM Benchmark Signal Room</div>
              </div>
              <nav class="nav-links" aria-label="Section navigation">
                <a href="#control-surface">Thesis</a>
                <a href="#world-mechanics">Runtime</a>
                <a href="#authoring-firewall">Authoring</a>
                <a href="#evidence-engine">Evaluation</a>
                <a href="#results-lab">Results</a>
                <a href="#autopsy">Autopsy</a>
                <a href="#diagram-deck">Diagrams</a>
              </nav>
            </div>
          </header>

          <section class="hero">
            <article class="hero-copy reveal">
              <div class="eyebrow">Deterministic TPM Benchmark Harness</div>
              <h1>Hold the org constant. Vary the TPM.</h1>
              <p>
                This dashboard turns your harness into a review-session control room: benchmark thesis up front,
                runtime mechanics in the middle, and bundle-level fingerprints plus a run autopsy at the end.
              </p>
              <div class="hero-badges" id="hero-badges"></div>
            </article>
            <aside class="hero-pulse reveal" aria-live="polite">
              <div>
                <div class="eyebrow" style="color: rgba(255, 248, 239, 0.72);">Live Artifact Readout</div>
                <h2 id="hero-title">Loading benchmark artifacts…</h2>
                <p id="hero-description"></p>
              </div>
              <div class="hero-stats-grid" id="hero-stats"></div>
              <div class="hero-showdown-grid" id="hero-showdown"></div>
              <div class="hero-callout" id="hero-callout"></div>
            </aside>
          </section>

          <section id="control-surface" class="section">
            <div class="section-shell reveal">
              <div class="section-heading">
                <div>
                  <div class="eyebrow">Control Surface</div>
                  <h2>Benchmark-first framing</h2>
                </div>
                <p>
                  The dashboard is designed to reinforce your thesis before you ever show a score:
                  this is a controlled TPM benchmark, not a roleplay demo.
                </p>
              </div>
              <div class="grid-4" id="control-surface-grid"></div>
            </div>
          </section>

          <section id="world-mechanics" class="section section-ink">
            <div class="section-shell reveal">
              <div class="section-heading">
                <div>
                  <div class="eyebrow">World Mechanics</div>
                  <h2>Discrete-event week, concrete observability</h2>
                </div>
                <p>
                  The runtime advances when the TPM acts and when scheduled events fire. The hard part of TPM work is
                  not just what is true, but the gap between truth, artifacts, and belief.
                </p>
              </div>

              <div class="grid-3" id="event-type-grid"></div>

              <div class="coworker-box">
                <article class="panel">
                  <div class="panel-head">
                    <div>
                      <strong>Policy Coworkers</strong>
                      <h3>Authored actors instead of runtime improv</h3>
                    </div>
                  </div>
                  <ul class="bullet-list" id="coworker-points"></ul>
                </article>
                <article class="panel">
                  <div class="panel-head">
                    <div>
                      <strong>State Triad</strong>
                      <h3>Partial observability is first-class</h3>
                    </div>
                  </div>
                  <div class="state-stack" id="state-layer-grid"></div>
                </article>
              </div>
            </div>
          </section>

          <section id="authoring-firewall" class="section">
            <div class="section-shell reveal">
              <div class="section-heading">
                <div>
                  <div class="eyebrow">Authoring Firewall</div>
                  <h2>Model-assisted creation, frozen runtime execution</h2>
                </div>
                <p>
                  Models can help draft and fill a candidate world offline, but the authoritative gates stay deterministic:
                  validation, closure, and scoring.
                </p>
              </div>

              <div class="timeline-pipeline" id="authoring-pipeline"></div>

              <div class="section-heading" style="margin-top: 28px;">
                <div>
                  <div class="eyebrow">Calibration Probes</div>
                  <h2 style="font-size: clamp(1.8rem, 3vw, 2.6rem);">Closure suite as benchmark certification</h2>
                </div>
                <p>
                  The point of the closure suite is not to flatter the environment. It is to prove that strong, weak,
                  noisy, and theater-heavy behaviors separate in deterministic ways.
                </p>
              </div>

              <div class="grid-4" id="probe-grid"></div>
            </div>
          </section>

          <section id="evidence-engine" class="section section-ink">
            <div class="section-shell reveal">
              <div class="section-heading">
                <div>
                  <div class="eyebrow">Evidence Engine</div>
                  <h2>State-derived scoring that resists reward hacking</h2>
                </div>
                <p>
                  Rubric lines attach to predicates over state and evidence. A doc, note, or meeting only matters if it
                  changes belief, commitment, readiness, or milestone truth.
                </p>
              </div>

              <div class="reward-grid">
                <article class="panel reward-panel">
                  <div class="panel-head">
                    <div>
                      <strong>Counts</strong>
                      <h3>High-signal TPM behavior</h3>
                    </div>
                  </div>
                  <ul id="evaluation-counts"></ul>
                </article>
                <article class="panel reward-panel">
                  <div class="panel-head">
                    <div>
                      <strong>Does Not Count</strong>
                      <h3>Activity without leverage</h3>
                    </div>
                  </div>
                  <ul id="evaluation-ignores"></ul>
                </article>
              </div>

              <div class="rubric-shell">
                <article class="panel">
                  <div class="panel-head">
                    <div>
                      <strong>Rubric Decoder</strong>
                      <h3>Actual score groups, fixed weights, and scenario line items</h3>
                    </div>
                  </div>
                  <p class="rubric-reference" id="rubric-reference"></p>
                  <div class="rubric-meta" id="rubric-meta"></div>
                  <p class="rubric-note" id="rubric-note"></p>
                  <div class="rubric-group-grid" id="rubric-group-grid"></div>
                </article>

                <article class="panel">
                  <div class="panel-head">
                    <div>
                      <strong>Line Item Ledger</strong>
                      <h3>What the evaluator explicitly checks</h3>
                    </div>
                  </div>
                  <div class="rubric-lines-grid" id="rubric-lines-grid"></div>
                </article>

                <div class="rubric-dimension-grid" id="rubric-dimension-grid"></div>
              </div>
            </div>
          </section>

          <section id="results-lab" class="section">
            <div class="section-shell reveal">
              <div class="section-heading">
                <div>
                  <div class="eyebrow">Results Lab</div>
                  <h2>Bundle fingerprints over raw logs</h2>
                </div>
                <p>
                  This is the showpiece section for review: switch scenarios, compare models, and surface the patterns that
                  matter before dropping into any single trace.
                </p>
              </div>

              <div class="scenario-switcher" id="scenario-switcher" aria-label="Scenario selector"></div>
              <div class="results-topline" id="results-topline"></div>
              <div class="results-summary" id="results-summary"></div>

              <div class="panel-grid">
                <article class="panel">
                  <div class="panel-head">
                    <div>
                      <strong>Score Skyline</strong>
                      <h3>Mean, range, and stability by model</h3>
                    </div>
                  </div>
                  <div id="score-skyline"></div>
                </article>
                <article class="panel">
                  <div class="panel-head">
                    <div>
                      <strong>TPM Fingerprint</strong>
                      <h3>Competency profile across the bundle</h3>
                    </div>
                  </div>
                  <div id="competency-radar"></div>
                </article>
              </div>

              <article class="panel" style="margin-top: 18px;">
                <div class="panel-head">
                  <div>
                    <strong>Clue Conversion Lattice</strong>
                    <h3>Surfacing vs turning cues into plan changes</h3>
                  </div>
                </div>
                <div id="signal-lattice"></div>
              </article>

              <div class="panel-grid">
                <article class="panel">
                  <div class="panel-head">
                    <div>
                      <strong>Failure Fault Lines</strong>
                      <h3>Recurring bundle-level root causes</h3>
                    </div>
                  </div>
                  <div id="root-cause-forge"></div>
                </article>
                <article class="panel">
                  <div class="panel-head">
                    <div>
                      <strong>Bundle Seed Tableau</strong>
                      <h3>Seed-by-seed run shape</h3>
                    </div>
                  </div>
                  <div id="bundle-run-table"></div>
                </article>
              </div>
            </div>
          </section>

          <section id="autopsy" class="section section-ink">
            <div class="section-shell reveal">
              <div class="section-heading">
                <div>
                  <div class="eyebrow">Run Autopsy</div>
                  <h2>One representative episode, opened up</h2>
                </div>
                <p>
                  Use this section right after the bundle view. It makes the summary tangible without drowning the room in raw logs.
                </p>
              </div>

              <div class="model-switcher" id="autopsy-model-switcher" aria-label="Model selector"></div>

              <div class="autopsy-shell">
                <div class="autopsy-header">
                  <article class="run-scorecard" id="autopsy-scorecard"></article>
                  <article class="run-fingerprint" id="autopsy-fingerprint"></article>
                </div>

                <div class="autopsy-grid">
                  <div class="dossier-grid" id="autopsy-dossiers"></div>
                  <div class="actor-grid" id="autopsy-actors"></div>
                </div>

                <div class="panel-grid">
                  <article class="panel" id="autopsy-windows"></article>
                  <article class="panel" id="autopsy-timeline"></article>
                </div>
              </div>
            </div>
          </section>

          <section id="diagram-deck" class="section">
            <div class="section-shell reveal">
              <div class="section-heading">
                <div>
                  <div class="eyebrow">Diagram Deck</div>
                  <h2>Pull your supporting visuals into the same room</h2>
                </div>
                <p>
                  These are wired in from the repo so the dashboard complements your Excalidraw deck instead of replacing it.
                </p>
              </div>
              <div class="gallery-grid" id="diagram-gallery"></div>
            </div>
          </section>

          <footer class="footer">
            Generated from local benchmark artifacts.
            Refresh with <code>python3 scripts/build_review_dashboard.py</code>.
          </footer>
        </div>

        <div class="lightbox" id="lightbox" aria-hidden="true">
          <div class="lightbox-frame">
            <div class="lightbox-head">
              <div>
                <strong id="lightbox-title">Diagram</strong>
              </div>
              <button type="button" id="lightbox-close">Close</button>
            </div>
            <div class="lightbox-body">
              <img id="lightbox-image" alt="" />
            </div>
          </div>
        </div>

        <script id="dashboard-data" type="application/json">__DATA__</script>
        <script>
          const payload = JSON.parse(document.getElementById("dashboard-data").textContent);
          const scenarios = payload.scenarios || [];
          const scenarioMap = new Map(scenarios.map((scenario) => [scenario.id, scenario]));
          const state = {
            scenarioId: payload.featured_scenario_id || (scenarios[0] ? scenarios[0].id : null),
            autopsyModelId: null,
          };

          const $ = (id) => document.getElementById(id);

          function escapeHtml(value) {
            return String(value ?? "")
              .replace(/&/g, "&amp;")
              .replace(/</g, "&lt;")
              .replace(/>/g, "&gt;")
              .replace(/"/g, "&quot;")
              .replace(/'/g, "&#39;");
          }

          function clamp(value, min, max) {
            return Math.max(min, Math.min(max, value));
          }

          function asNumber(value, fallback = null) {
            const number = Number(value);
            return Number.isFinite(number) ? number : fallback;
          }

          function formatNumber(value, digits = 0) {
            const number = asNumber(value);
            if (number === null) return "—";
            return number.toFixed(digits).replace(/\\.0+$/, "");
          }

          function formatPercent(value, digits = 0) {
            const number = asNumber(value);
            if (number === null) return "—";
            return `${number.toFixed(digits).replace(/\\.0+$/, "")}%`;
          }

          function formatDate(value) {
            if (!value) return "Unknown";
            const date = new Date(value);
            if (Number.isNaN(date.getTime())) return value;
            return new Intl.DateTimeFormat("en-US", {
              weekday: "short",
              month: "short",
              day: "numeric",
              hour: "numeric",
              minute: "2-digit",
            }).format(date);
          }

          function humanizeToken(value) {
            if (!value) return "—";
            const normalized = String(value).replace(/[_-]+/g, " ").trim();
            if (!normalized) return "—";
            return normalized.charAt(0).toUpperCase() + normalized.slice(1);
          }

          function signalSummaryText(summary) {
            const observed = asNumber(summary?.critical_observed, null);
            const converted = asNumber(summary?.critical_converted, null);
            if (observed === null && converted === null) return { headline: "—", detail: "No signal summary available" };
            if (observed === null) return { headline: `${formatNumber(converted, 0)} acted`, detail: "Converted to plan changes" };
            if (converted === null) return { headline: `${formatNumber(observed, 0)} seen`, detail: "Critical cues surfaced" };
            return {
              headline: `${formatNumber(observed, 0)} seen / ${formatNumber(converted, 0)} acted`,
              detail: "Critical cues converted into plan changes",
            };
          }

          function windowSummaryText(summary) {
            const hit = asNumber(summary?.hit, null);
            const total = asNumber(summary?.total, null);
            if (hit === null || total === null || total <= 0) {
              return { headline: "—", detail: "No deadline windows recorded" };
            }
            return {
              headline: `${formatNumber(hit, 0)}/${formatNumber(total, 0)}`,
              detail: `${formatPercent((hit / total) * 100)} of authored windows hit`,
            };
          }

          function getScenario() {
            return state.scenarioId ? scenarioMap.get(state.scenarioId) : null;
          }

          function sortedModels(scenario) {
            return [...(scenario?.models || [])].sort((left, right) => {
              const leftScore = asNumber(left?.metrics?.mean_score, -Infinity);
              const rightScore = asNumber(right?.metrics?.mean_score, -Infinity);
              return rightScore - leftScore;
            });
          }

          function findDimension(profile, dimensionId) {
            return (profile || []).find((item) => item.id === dimensionId) || null;
          }

          function shortCapability(bundle) {
            return bundle?.aggregate_capability_assessment?.direct_answer || "Capability verdict unavailable.";
          }

          function topRootCause(bundle) {
            return bundle?.recurring_root_causes?.[0] || null;
          }

          function scenarioInsight(scenario) {
            const models = sortedModels(scenario);
            if (!models.length) {
              return "No bundle summaries found for this scenario.";
            }
            if (models.length === 1) {
              const only = models[0];
              return `${only.meta.label} is the only available model bundle for ${scenario.meta.title}, so the emphasis here is pattern diagnosis rather than head-to-head comparison.`;
            }
            const champion = models[0];
            const laggard = models[models.length - 1];
            const gap = (asNumber(champion.metrics.mean_score, 0) - asNumber(laggard.metrics.mean_score, 0)).toFixed(1).replace(/\\.0$/, "");
            const championSurfaced = formatPercent(champion.metrics.critical_surfaced_rate);
            const championConverted = formatPercent(champion.metrics.critical_converted_rate);
            return `${champion.meta.label} leads ${laggard.meta.label} by ${gap} points on ${scenario.meta.title}. The interesting part is not just the score gap: the leader surfaces about ${championSurfaced} of critical cues and converts about ${championConverted} of them into action.`;
          }

          function currentAutopsyModel(scenario) {
            const models = sortedModels(scenario);
            if (!models.length) return null;
            let match = models.find((model) => model.id === state.autopsyModelId);
            if (!match) {
              match = models[0];
              state.autopsyModelId = match.id;
            }
            return match;
          }

          function rubricContext(scenario) {
            const preferredModel = currentAutopsyModel(scenario);
            const candidates = [];
            const seen = new Set();

            function enqueue(model, run) {
              if (!model || !run) return;
              const key = `${model.id}:${run.seed ?? "na"}:${run.path || ""}`;
              if (seen.has(key)) return;
              seen.add(key);
              candidates.push({ model, run });
            }

            enqueue(preferredModel, preferredModel?.featured_run);
            sortedModels(scenario).forEach((model) => {
              enqueue(model, model.featured_run);
              (model.runs || []).forEach((run) => enqueue(model, run));
            });

            return (
              candidates.find(
                ({ run }) =>
                  (run?.score_breakdown?.groups || []).length > 0 || (run?.tpm_competency_profile || []).length > 0
              ) ||
              candidates[0] || { model: null, run: null }
            );
          }

          function buildRubricLineIndex(profile) {
            const index = new Map();
            (profile || []).forEach((dimension) => {
              (dimension.contributing_rubric_lines || []).forEach((line) => {
                const key = line.id || line.label;
                if (!key) return;
                if (!index.has(key)) {
                  index.set(key, {
                    id: line.id || key,
                    label: line.label || line.id || key,
                    measurement_rationale: line.measurement_rationale || "",
                    competencies: [],
                  });
                }
                const row = index.get(key);
                if (line.label && !row.label) row.label = line.label;
                if (line.measurement_rationale && !row.measurement_rationale) {
                  row.measurement_rationale = line.measurement_rationale;
                }
                if (dimension.label && !row.competencies.includes(dimension.label)) {
                  row.competencies.push(dimension.label);
                }
              });
            });
            return index;
          }

          function flattenRubricLines(groups, lineIndex) {
            const lineMap = new Map();

            (groups || []).forEach((group) => {
              (group.lines || []).forEach((line) => {
                const key = line.id || line.label || `${group.id || group.label || "group"}:${lineMap.size + 1}`;
                if (!lineMap.has(key)) {
                  lineMap.set(key, {
                    id: line.id || key,
                    label: line.label || line.id || key,
                    weight: asNumber(line.weight, 0),
                    awarded: asNumber(line.awarded, null),
                    lost_points: asNumber(line.lost_points, null),
                    groups: [],
                    competencies: [],
                    measurement_rationale: "",
                  });
                }

                const row = lineMap.get(key);
                if (line.label && !row.label) row.label = line.label;
                if (!row.weight) row.weight = asNumber(line.weight, 0);
                if (row.awarded === null) row.awarded = asNumber(line.awarded, null);
                if (row.lost_points === null) row.lost_points = asNumber(line.lost_points, null);

                const groupLabel = group.label || humanizeToken(group.id || "group");
                if (groupLabel && !row.groups.includes(groupLabel)) {
                  row.groups.push(groupLabel);
                }

                const meta = lineIndex.get(line.id || key) || lineIndex.get(line.label || key);
                if (meta) {
                  if (meta.label && !row.label) row.label = meta.label;
                  if (meta.measurement_rationale && !row.measurement_rationale) {
                    row.measurement_rationale = meta.measurement_rationale;
                  }
                  (meta.competencies || []).forEach((label) => {
                    if (label && !row.competencies.includes(label)) {
                      row.competencies.push(label);
                    }
                  });
                }
              });
            });

            return [...lineMap.values()].sort((left, right) => {
              const weightDelta = asNumber(right.weight, 0) - asNumber(left.weight, 0);
              if (weightDelta !== 0) return weightDelta;
              return String(left.label || "").localeCompare(String(right.label || ""));
            });
          }

          function renderHero() {
            const overview = payload.overview || {};
            const scenario = getScenario();
            const models = sortedModels(scenario);
            const champion = models[0];
            const laggard = models[models.length - 1];

            $("hero-badges").innerHTML = (payload.presentation.hero_badges || [])
              .map((item) => `<span class="hero-badge">${escapeHtml(item)}</span>`)
              .join("");

            $("hero-title").textContent = scenario ? `${scenario.meta.title} loaded into the room.` : "Benchmark artifacts loaded.";
            $("hero-description").textContent = scenario ? scenario.meta.summary : "No featured scenario found.";

            $("hero-stats").innerHTML = [
              { label: "Scenarios", value: formatNumber(overview.scenario_count) },
              { label: "Bundle Reports", value: formatNumber(overview.bundle_count) },
              { label: "Run Summaries", value: formatNumber(overview.run_count) },
              { label: "Seed Evals", value: formatNumber(overview.seed_eval_count) },
            ]
              .map(
                (item) => `
                  <div class="hero-stat">
                    <div class="hero-stat-label">${escapeHtml(item.label)}</div>
                    <div class="hero-stat-value">${escapeHtml(item.value)}</div>
                  </div>
                `
              )
              .join("");

            if (champion && laggard) {
              $("hero-showdown").innerHTML = `
                <div class="hero-showdown-card">
                  <div class="hero-showdown-label">Champion</div>
                  <strong style="color:${escapeHtml(champion.meta.color)};">${escapeHtml(formatNumber(champion.metrics.mean_score, 1))}</strong>
                  <span>${escapeHtml(champion.meta.label)} on ${escapeHtml(scenario.meta.title)}</span>
                </div>
                <div class="hero-showdown-card">
                  <div class="hero-showdown-label">Spread</div>
                  <strong>${escapeHtml(formatNumber((asNumber(champion.metrics.mean_score, 0) - asNumber(laggard.metrics.mean_score, 0)), 1))}</strong>
                  <span>Mean score gap to ${escapeHtml(laggard.meta.label)}</span>
                </div>
              `;
            } else {
              $("hero-showdown").innerHTML = "";
            }

            $("hero-callout").innerHTML = `
              <strong>Why this room works in review</strong>
              <span>${escapeHtml(scenarioInsight(scenario))}</span>
            `;
          }

          function renderStaticSections() {
            $("control-surface-grid").innerHTML = (payload.presentation.control_surface || [])
              .map(
                (item, index) => `
                  <article class="story-card">
                    <div class="story-index">${index + 1}</div>
                    <h3>${escapeHtml(item.title)}</h3>
                    <p>${escapeHtml(item.body)}</p>
                  </article>
                `
              )
              .join("");

            $("event-type-grid").innerHTML = (payload.presentation.event_types || [])
              .map(
                (item) => `
                  <article class="event-card">
                    <h3>${escapeHtml(item.label)}</h3>
                    <strong>${escapeHtml(item.example)}</strong>
                    <p>${escapeHtml(item.body)}</p>
                  </article>
                `
              )
              .join("");

            $("coworker-points").innerHTML = (payload.presentation.coworker_points || [])
              .map((item) => `<li>${escapeHtml(item)}</li>`)
              .join("");

            $("state-layer-grid").innerHTML = (payload.presentation.state_layers || [])
              .map(
                (item) => `
                  <article class="state-card">
                    <h3>${escapeHtml(item.label)}</h3>
                    <p>${escapeHtml(item.body)}</p>
                  </article>
                `
              )
              .join("");

            $("authoring-pipeline").innerHTML = (payload.presentation.authoring_steps || [])
              .map(
                (item, index) => `
                  <div class="pipeline-step">
                    <strong>Step ${index + 1}</strong>
                    <span>${escapeHtml(item)}</span>
                  </div>
                `
              )
              .join("");

            $("probe-grid").innerHTML = (payload.presentation.calibration_probes || [])
              .map(
                (item) => `
                  <article class="probe-card">
                    <div class="probe-label mono">${escapeHtml(item.label)}</div>
                    <h3>${escapeHtml(item.label)}</h3>
                    <p>${escapeHtml(item.body)}</p>
                  </article>
                `
              )
              .join("");

            $("evaluation-counts").innerHTML = (payload.presentation.evaluation_counts || [])
              .map((item) => `<li>${escapeHtml(item)}</li>`)
              .join("");
            $("evaluation-ignores").innerHTML = (payload.presentation.evaluation_ignores || [])
              .map((item) => `<li>${escapeHtml(item)}</li>`)
              .join("");
          }

          function renderEvaluationRubric(scenario) {
            const context = rubricContext(scenario);
            const model = context.model;
            const run = context.run;
            const scoreBreakdown = run?.score_breakdown || {};
            const groups = scoreBreakdown.groups || [];
            const profile = run?.tpm_competency_profile || [];

            if (!groups.length && !profile.length) {
              $("rubric-reference").textContent = "Detailed rubric data was not included in the available run summaries.";
              $("rubric-meta").innerHTML = "";
              $("rubric-note").textContent = "";
              $("rubric-group-grid").innerHTML = "<p>No score-group data available for this scenario.</p>";
              $("rubric-lines-grid").innerHTML = "<p>No rubric line items available for this scenario.</p>";
              $("rubric-dimension-grid").innerHTML = '<article class="panel"><p>No competency rubric details available for this scenario.</p></article>';
              return;
            }

            const lineIndex = buildRubricLineIndex(profile);
            const lines = flattenRubricLines(groups, lineIndex);
            const totalPossible =
              asNumber(scoreBreakdown.total_possible, null) ??
              groups.reduce((sum, group) => sum + asNumber(group.weight, 0), 0);
            const totalAwarded = asNumber(scoreBreakdown.total_awarded, null);
            const lineCount = lines.length || groups.reduce((sum, group) => sum + (group.lines || []).length, 0);
            const referenceLabel =
              model && run
                ? `${model.meta.label} seed ${run.seed}`
                : "the selected scenario";

            $("rubric-reference").innerHTML = `
              Same scoring contract for every model on <strong>${escapeHtml(scenario.meta.title)}</strong>.
              Reference readout: ${escapeHtml(referenceLabel)}.
            `;

            $("rubric-meta").innerHTML = [
              { label: "Total Contract", value: `${formatNumber(totalPossible, 0)} pts` },
              { label: "Score Groups", value: formatNumber(groups.length, 0) },
              { label: "Rubric Lines", value: formatNumber(lineCount, 0) },
              {
                label: "Reference Score",
                value:
                  totalAwarded === null || totalPossible === null
                    ? "Frozen"
                    : `${formatNumber(totalAwarded, 1)} / ${formatNumber(totalPossible, 0)}`,
              },
            ]
              .map(
                (item) => `
                  <div class="rubric-stat">
                    <div class="rubric-stat-label">${escapeHtml(item.label)}</div>
                    <div class="rubric-stat-value">${escapeHtml(item.value)}</div>
                  </div>
                `
              )
              .join("");

            $("rubric-note").textContent =
              totalAwarded === null
                ? "Weights, line definitions, and evidence expectations are frozen at scenario compile-time. The structure below is the real scoring contract used by the evaluator."
                : `Weights, line definitions, and evidence expectations are frozen at scenario compile-time. The award chips below simply show how ${referenceLabel} performed against that fixed contract.`;

            $("rubric-group-grid").innerHTML = groups.length
              ? groups
                  .map((group) => {
                    const groupLabel = group.label || humanizeToken(group.id || "group");
                    const groupWeight = asNumber(group.weight, 0);
                    const groupAwarded = asNumber(group.awarded, null);
                    const groupLineCount = (group.lines || []).length;
                    const groupRatio =
                      groupAwarded === null || !groupWeight
                        ? 0
                        : clamp((groupAwarded / groupWeight) * 100, 0, 100);
                    return `
                      <article class="rubric-group-card">
                        <div class="rubric-group-weight">
                          <strong>${escapeHtml(formatNumber(groupWeight, 0))} pts</strong>
                          ${
                            groupAwarded === null
                              ? ""
                              : `<span class="chip">Ref ${escapeHtml(formatNumber(groupAwarded, 1))}/${escapeHtml(formatNumber(groupWeight, 0))}</span>`
                          }
                        </div>
                        <h3>${escapeHtml(groupLabel)}</h3>
                        <div class="meter"><span style="width:${groupRatio}%;"></span></div>
                        <div class="rubric-group-copy">
                          ${escapeHtml(formatNumber(groupLineCount, 0))} line ${groupLineCount === 1 ? "item" : "items"} in this bucket.
                        </div>
                      </article>
                    `;
                  })
                  .join("")
              : "<p>No score-group data available for this scenario.</p>";

            $("rubric-lines-grid").innerHTML = lines.length
              ? lines
                  .map((line) => {
                    const awarded = asNumber(line.awarded, null);
                    const lost = asNumber(line.lost_points, null);
                    return `
                      <article class="rubric-line-card">
                        <div class="rubric-line-head">
                          <div>
                            <h4 class="rubric-line-title">${escapeHtml(line.label || line.id)}</h4>
                            <div class="chip-row">
                              ${(line.groups || []).map((group) => `<span class="chip">${escapeHtml(group)}</span>`).join("")}
                              ${(line.competencies || [])
                                .map((label) => `<span class="chip mono">${escapeHtml(label)}</span>`)
                                .join("")}
                            </div>
                          </div>
                          <span class="rubric-pill">${escapeHtml(formatNumber(line.weight, 0))} pts</span>
                        </div>
                        <p class="rubric-line-copy">${escapeHtml(line.measurement_rationale || "No measurement rationale captured in this summary.")}</p>
                        <div class="chip-row rubric-line-footer">
                          ${
                            awarded === null
                              ? ""
                              : `<span class="chip">Reference awarded ${escapeHtml(formatNumber(awarded, 1))}</span>`
                          }
                          ${
                            lost === null
                              ? ""
                              : `<span class="chip">Reference lost ${escapeHtml(formatNumber(lost, 1))}</span>`
                          }
                        </div>
                      </article>
                    `;
                  })
                  .join("")
              : "<p>No rubric line items available for this scenario.</p>";

            $("rubric-dimension-grid").innerHTML = profile.length
              ? profile
                  .map((item) => {
                    const score = asNumber(item.score, null);
                    const weight = asNumber(item.weight, null);
                    return `
                      <article class="panel rubric-dimension-card">
                        <div class="panel-head">
                          <div>
                            <strong>${escapeHtml(weight === null ? "Competency Lens" : `${formatNumber(weight, 0)} pts`)}</strong>
                            <h3>${escapeHtml(item.label || item.id)}</h3>
                          </div>
                        </div>
                        <p class="rubric-dimension-copy">${escapeHtml(item.description || "No competency description available.")}</p>
                        <div class="chip-row">
                          ${
                            score === null
                              ? ""
                              : `<span class="chip">Reference run ${escapeHtml(formatPercent(score))}</span>`
                          }
                          ${item.band ? `<span class="chip">${escapeHtml(humanizeToken(item.band))}</span>` : ""}
                        </div>
                        <div class="rubric-dual-copy">
                          <div>
                            <strong>Counts</strong>
                            <p>${escapeHtml(item.counts || "No positive criteria captured.")}</p>
                          </div>
                          <div>
                            <strong>Does Not Count</strong>
                            <p>${escapeHtml(item.does_not_count || "No exclusion criteria captured.")}</p>
                          </div>
                        </div>
                        <div class="chip-row">
                          ${(item.contributing_rubric_lines || [])
                            .map((line) => `<span class="chip mono">${escapeHtml(line.label || line.id)}</span>`)
                            .join("")}
                        </div>
                      </article>
                    `;
                  })
                  .join("")
              : '<article class="panel"><p>No competency rubric details available for this scenario.</p></article>';
          }

          function renderScenarioButtons() {
            $("scenario-switcher").innerHTML = scenarios
              .map(
                (scenario) => `
                  <button
                    class="scenario-button ${scenario.id === state.scenarioId ? "is-active" : ""}"
                    type="button"
                    data-scenario-id="${escapeHtml(scenario.id)}"
                  >
                    ${escapeHtml(scenario.meta.title)}
                  </button>
                `
              )
              .join("");

            $("scenario-switcher")
              .querySelectorAll("[data-scenario-id]")
              .forEach((button) => {
                button.addEventListener("click", () => {
                  state.scenarioId = button.getAttribute("data-scenario-id");
                  state.autopsyModelId = null;
                  renderDynamicSections();
                });
              });
          }

          function renderTopline(scenario) {
            const beats = scenario?.meta?.story_beats || [];
            $("results-topline").innerHTML = [
              `<span class="hero-badge">${escapeHtml(scenario.meta.deck)}</span>`,
              ...beats.map((beat) => `<span class="hero-badge">${escapeHtml(beat)}</span>`),
            ].join("");
          }

          function renderModelCapsules(scenario) {
            const models = sortedModels(scenario);
            $("results-summary").innerHTML = models
              .map((model) => {
                const metrics = model.metrics || {};
                const bundle = model.bundle || {};
                const rootCause = metrics.top_root_cause || topRootCause(bundle)?.title || "No recurring root cause captured";
                return `
                  <article class="model-capsule" style="--model-color:${escapeHtml(model.meta.color)};">
                    <div class="capsule-head">
                      <div>
                        <div class="capsule-title">${escapeHtml(model.meta.label)}</div>
                        <div class="capsule-subtitle">${escapeHtml(shortCapability(bundle))}</div>
                      </div>
                      <div class="capsule-score">${escapeHtml(formatNumber(metrics.mean_score, 1))}</div>
                    </div>
                    <div class="metric-stack">
                      <div class="metric-row">
                        <div class="metric-label-line">
                          <span>Critical cues surfaced</span>
                          <strong>${escapeHtml(formatPercent(metrics.critical_surfaced_rate))}</strong>
                        </div>
                        <div class="meter"><span style="width:${clamp(asNumber(metrics.critical_surfaced_rate, 0), 0, 100)}%;"></span></div>
                      </div>
                      <div class="metric-row">
                        <div class="metric-label-line">
                          <span>Critical cues converted</span>
                          <strong>${escapeHtml(formatPercent(metrics.critical_converted_rate))}</strong>
                        </div>
                        <div class="meter"><span style="width:${clamp(asNumber(metrics.critical_converted_rate, 0), 0, 100)}%;"></span></div>
                      </div>
                      <div class="metric-row">
                        <div class="metric-label-line">
                          <span>Window hit rate</span>
                          <strong>${escapeHtml(formatPercent(metrics.window_hit_rate))}</strong>
                        </div>
                        <div class="meter"><span style="width:${clamp(asNumber(metrics.window_hit_rate, 0), 0, 100)}%;"></span></div>
                      </div>
                    </div>
                    <div class="capsule-foot">
                      <span>Stdev ${escapeHtml(formatNumber(metrics.stdev, 1))}</span>
                      <span>${escapeHtml(rootCause)}</span>
                    </div>
                    <div class="capsule-source">
                      ${
                        bundle.source_note
                          ? `${escapeHtml(bundle.source_note)} `
                          : ""
                      }
                      Source: <a href="${escapeHtml(bundle.href)}" target="_blank" rel="noreferrer">${escapeHtml(bundle.path)}</a>
                    </div>
                  </article>
                `;
              })
              .join("");
          }

          function renderScoreSkyline(scenario) {
            const models = sortedModels(scenario);
            $("score-skyline").innerHTML = `
              <div class="skyline">
                ${models
                  .map((model) => {
                    const mean = clamp(asNumber(model.metrics.mean_score, 0), 0, 100);
                    const best = clamp(asNumber(model.metrics.best_score, mean), 0, 100);
                    const worst = clamp(asNumber(model.metrics.worst_score, mean), 0, 100);
                    const rangeBottom = Math.min(best, worst);
                    const rangeHeight = Math.max(2, Math.abs(best - worst));
                    return `
                      <div class="skyline-card" style="--model-color:${escapeHtml(model.meta.color)};">
                        <div class="skyline-chart">
                          <div class="skyline-range" style="bottom:${rangeBottom}%; height:${rangeHeight}%;"></div>
                          <div class="skyline-bar" style="height:${mean}%;"></div>
                        </div>
                        <div class="skyline-label">
                          <strong>${escapeHtml(model.meta.label)}</strong>
                          <span>${escapeHtml(shortCapability(model.bundle))}</span>
                        </div>
                        <div class="skyline-meta">
                          <span>Mean ${escapeHtml(formatNumber(model.metrics.mean_score, 1))} / 100</span>
                          <span>Best ${escapeHtml(formatNumber(model.metrics.best_score, 1))} · Worst ${escapeHtml(formatNumber(model.metrics.worst_score, 1))}</span>
                          <span>Seed bundle: ${(model.bundle.seed_bundle || []).map((seed) => escapeHtml(seed)).join(", ") || "—"}</span>
                        </div>
                      </div>
                    `;
                  })
                  .join("")}
              </div>
            `;
          }

          function renderCompetencyRadar(scenario) {
            const models = sortedModels(scenario);
            const profileSource = models[0]?.bundle?.aggregate_competency_profile || [];
            const dimensions = profileSource.length
              ? profileSource.map((item) => ({ id: item.id, label: item.label || item.id }))
              : [];

            if (!dimensions.length || !models.length) {
              $("competency-radar").innerHTML = `<p>No aggregate competency profile available for this scenario.</p>`;
              return;
            }

            const size = 420;
            const center = size / 2;
            const radius = 150;
            const rings = [25, 50, 75, 100];

            const ringPolygons = rings
              .map((ring) => {
                const points = dimensions
                  .map((_, index) => {
                    const angle = -Math.PI / 2 + (Math.PI * 2 * index) / dimensions.length;
                    const x = center + Math.cos(angle) * radius * (ring / 100);
                    const y = center + Math.sin(angle) * radius * (ring / 100);
                    return `${x.toFixed(1)},${y.toFixed(1)}`;
                  })
                  .join(" ");
                return `<polygon points="${points}" fill="none" stroke="rgba(22,33,51,0.10)" stroke-dasharray="4 6"></polygon>`;
              })
              .join("");

            const axes = dimensions
              .map((dimension, index) => {
                const angle = -Math.PI / 2 + (Math.PI * 2 * index) / dimensions.length;
                const x = center + Math.cos(angle) * radius;
                const y = center + Math.sin(angle) * radius;
                const lx = center + Math.cos(angle) * (radius + 28);
                const ly = center + Math.sin(angle) * (radius + 28);
                return `
                  <line x1="${center}" y1="${center}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="rgba(22,33,51,0.12)"></line>
                  <text x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="middle" font-size="11" fill="currentColor" opacity="0.72">${escapeHtml(
                    dimension.label.length > 24 ? `${dimension.label.slice(0, 22)}…` : dimension.label
                  )}</text>
                `;
              })
              .join("");

            const shapes = models
              .map((model) => {
                const points = dimensions
                  .map((dimension, index) => {
                    const row = findDimension(model.bundle.aggregate_competency_profile || [], dimension.id);
                    const value = clamp(asNumber(row?.mean_score, 0), 0, 100);
                    const angle = -Math.PI / 2 + (Math.PI * 2 * index) / dimensions.length;
                    const x = center + Math.cos(angle) * radius * (value / 100);
                    const y = center + Math.sin(angle) * radius * (value / 100);
                    return `${x.toFixed(1)},${y.toFixed(1)}`;
                  })
                  .join(" ");
                return `
                  <polygon points="${points}" fill="${escapeHtml(model.meta.color)}22" stroke="${escapeHtml(model.meta.color)}" stroke-width="3"></polygon>
                `;
              })
              .join("");

            $("competency-radar").innerHTML = `
              <div class="radar-shell">
                <svg viewBox="0 0 ${size} ${size}" width="100%" height="100%" aria-label="Competency radar chart">
                  ${ringPolygons}
                  ${axes}
                  ${shapes}
                </svg>
                <div class="radar-legend">
                  ${models
                    .map((model) => `
                      <div class="legend-item" style="--model-color:${escapeHtml(model.meta.color)};">
                        <div class="legend-title">
                          <span class="legend-dot"></span>
                          <span>${escapeHtml(model.meta.label)}</span>
                        </div>
                        <div class="legend-copy">
                          Discovery ${escapeHtml(formatPercent(model.metrics.discovery_score))} ·
                          Decision ${escapeHtml(formatPercent(model.metrics.decision_score))} ·
                          Commitment ${escapeHtml(formatPercent(model.metrics.commitment_score))}
                        </div>
                      </div>
                    `)
                    .join("")}
                </div>
              </div>
            `;
          }

          function renderSignalLattice(scenario) {
            const models = sortedModels(scenario);
            const signalMap = new Map();

            models.forEach((model) => {
              (model.bundle.signal_coverage_consistency || [])
                .filter((row) => row.criticality === "critical")
                .forEach((row) => {
                  if (!signalMap.has(row.signal_id)) {
                    signalMap.set(row.signal_id, {
                      signal_id: row.signal_id,
                      label: row.label || row.signal_id,
                      kind: row.kind || "signal",
                    });
                  }
                });
            });

            const signals = [...signalMap.values()].sort((left, right) => left.label.localeCompare(right.label));
            if (!signals.length) {
              $("signal-lattice").innerHTML = `<p>No critical signal consistency data found for this scenario.</p>`;
              return;
            }

            const header = `
              <div class="signal-row" style="--signal-cols:${models.length};">
                <div class="signal-heading">Critical signal</div>
                ${models.map((model) => `<div class="signal-heading">${escapeHtml(model.meta.label)}</div>`).join("")}
              </div>
            `;

            const rows = signals
              .map((signal) => {
                const cells = models
                  .map((model) => {
                    const row = (model.bundle.signal_coverage_consistency || []).find((item) => item.signal_id === signal.signal_id) || {};
                    const surfaced = clamp(asNumber(row.surfaced_rate, 0) * 100, 0, 100);
                    const converted = clamp(asNumber(row.converted_rate, 0) * 100, 0, 100);
                    return `
                      <div class="signal-cell" style="--model-color:${escapeHtml(model.meta.color)};">
                        <h4>${escapeHtml(model.meta.label)}</h4>
                        <div class="signal-meter-wrap">
                          <div class="signal-meter-label">
                            <span>Surfaced</span>
                            <strong>${escapeHtml(formatPercent(surfaced))}</strong>
                          </div>
                          <div class="meter"><span style="width:${surfaced}%;"></span></div>
                          <div class="signal-meter-label">
                            <span>Converted</span>
                            <strong>${escapeHtml(formatPercent(converted))}</strong>
                          </div>
                          <div class="meter"><span style="width:${converted}%;"></span></div>
                        </div>
                      </div>
                    `;
                  })
                  .join("");

                return `
                  <div class="signal-row" style="--signal-cols:${models.length};">
                    <div class="signal-label-card">
                      <strong>${escapeHtml(signal.label)}</strong>
                      <div class="signal-kind">${escapeHtml(signal.kind)}</div>
                    </div>
                    ${cells}
                  </div>
                `;
              })
              .join("");

            $("signal-lattice").innerHTML = `<div class="signal-lattice">${header}${rows}</div>`;
          }

          function renderRootCauseForge(scenario) {
            const models = sortedModels(scenario);
            $("root-cause-forge").innerHTML = `
              <div class="fault-grid">
                ${models
                  .map((model) => {
                    const causes = (model.bundle.recurring_root_causes || []).slice(0, 4);
                    return `
                      <div class="fault-card" style="--model-color:${escapeHtml(model.meta.color)};">
                        <div class="fault-head">
                          <div>
                            <strong>${escapeHtml(model.meta.label)}</strong>
                            <div class="fault-item-meta">${escapeHtml(shortCapability(model.bundle))}</div>
                          </div>
                          <div class="fault-badge">${escapeHtml(formatNumber(model.metrics.mean_score, 1))}</div>
                        </div>
                        <div class="fault-list">
                          ${
                            causes.length
                              ? causes
                                  .map(
                                    (cause) => `
                                      <div class="fault-item">
                                        <div class="fault-item-title">
                                          <span>${escapeHtml(cause.title || cause.id || "Root cause")}</span>
                                          <strong>${escapeHtml(formatNumber(cause.mean_lost_points, 1))}</strong>
                                        </div>
                                        <div class="fault-item-meta">
                                          ${escapeHtml(formatNumber(cause.count, 0))} seeded appearances ·
                                          lost points when present
                                        </div>
                                      </div>
                                    `
                                  )
                                  .join("")
                              : `<div class="fault-item-meta">No recurring root cause summary found.</div>`
                          }
                        </div>
                      </div>
                    `;
                  })
                  .join("")}
              </div>
            `;
          }

          function renderBundleRunTable(scenario) {
            const models = sortedModels(scenario);
            const rows = [];
            models.forEach((model) => {
              (model.runs || []).forEach((run) => rows.push({ model, run }));
            });
            rows.sort((left, right) => {
              const leftScore = asNumber(left.run.score, 0);
              const rightScore = asNumber(right.run.score, 0);
              if (left.model.id === right.model.id) {
                return asNumber(left.run.seed, 0) - asNumber(right.run.seed, 0);
              }
              return rightScore - leftScore;
            });

            $("bundle-run-table").innerHTML = `
              <table class="seed-table">
                <thead>
                  <tr>
                    <th>Model</th>
                    <th>Seed</th>
                    <th>Score</th>
                    <th>Critical Signals</th>
                    <th>Windows</th>
                    <th>Top Failure</th>
                  </tr>
                </thead>
                <tbody>
                  ${rows
                    .map(({ model, run }) => {
                      const summary = run.critical_signal_summary || {};
                      const observed = asNumber(summary.critical_observed, null);
                      const converted = asNumber(summary.critical_converted, null);
                      const hit = asNumber(run.window_summary?.hit, null);
                      const total = asNumber(run.window_summary?.total, null);
                      return `
                        <tr>
                          <td><strong style="color:${escapeHtml(model.meta.color)};">${escapeHtml(model.meta.label)}</strong></td>
                          <td class="mono">${escapeHtml(run.seed)}</td>
                          <td>${escapeHtml(formatNumber(run.score, 1))}</td>
                          <td>${observed === null ? "—" : `${formatNumber(observed)}/${formatNumber(converted, 0)} converted`}</td>
                          <td>${total ? `${formatNumber(hit, 0)}/${formatNumber(total, 0)}` : "—"}</td>
                          <td>${escapeHtml(run.top_failure_title || run.top_root_cause_title || run.outcome_headline || "—")}</td>
                        </tr>
                      `;
                    })
                    .join("")}
                </tbody>
              </table>
            `;
          }

          function renderAutopsySwitcher(scenario) {
            const models = sortedModels(scenario);
            const active = currentAutopsyModel(scenario);
            $("autopsy-model-switcher").innerHTML = models
              .map(
                (model) => `
                  <button
                    type="button"
                    class="model-button ${active && model.id === active.id ? "is-active" : ""}"
                    data-model-id="${escapeHtml(model.id)}"
                  >
                    ${escapeHtml(model.meta.label)}
                  </button>
                `
              )
              .join("");

            $("autopsy-model-switcher")
              .querySelectorAll("[data-model-id]")
              .forEach((button) => {
                button.addEventListener("click", () => {
                  state.autopsyModelId = button.getAttribute("data-model-id");
                  renderEvaluationRubric(scenario);
                  renderAutopsySection(scenario);
                });
              });
          }

          function severityClass(severity) {
            const value = String(severity || "medium").toLowerCase();
            if (value.includes("critical")) return "severity-critical";
            if (value.includes("high")) return "severity-high";
            return "severity-medium";
          }

          function renderAutopsySection(scenario) {
            const model = currentAutopsyModel(scenario);
            const run = model?.featured_run;

            if (!model || !run) {
              $("autopsy-scorecard").innerHTML = "<p>No run summary available for this model.</p>";
              $("autopsy-fingerprint").innerHTML = "";
              $("autopsy-dossiers").innerHTML = "";
              $("autopsy-actors").innerHTML = "";
              $("autopsy-windows").innerHTML = "";
              $("autopsy-timeline").innerHTML = "";
              return;
            }

            const summary = run.critical_signal_summary || {};
            const stakeholders = run.stakeholder_summary || {};
            const windows = run.window_summary || {};
            const profile = run.tpm_competency_profile || [];
            const scoreBreakdown = run.score_breakdown || {};
            const signalCopy = signalSummaryText(summary);
            const windowCopy = windowSummaryText(windows);
            const unansweredCount = (stakeholders.direct_questions_left_unanswered || []).length;

            $("autopsy-scorecard").innerHTML = `
              <h3>${escapeHtml(model.meta.label)} · Seed ${escapeHtml(run.seed)}</h3>
              <div class="run-score">
                <strong style="color:${escapeHtml(model.meta.color)};">${escapeHtml(formatNumber(run.score, 1))}</strong>
                <span>/ ${escapeHtml(formatNumber(run.score_possible, 0))}</span>
              </div>
              <p>${escapeHtml(run.outcome_headline || run.capability_direct_answer || "Run outcome unavailable.")}</p>
              <div class="run-metrics">
                <div class="run-metric">
                  <div class="run-metric-label">Critical Path</div>
                  <strong class="run-metric-value run-metric-value-text">${escapeHtml(humanizeToken(run.critical_path_status || "—"))}</strong>
                  <div class="run-metric-sub">${escapeHtml(humanizeToken(run.capability_rating || run.overall_status || "run state unavailable"))}</div>
                </div>
                <div class="run-metric">
                  <div class="run-metric-label">Signals</div>
                  <strong class="run-metric-value">${escapeHtml(signalCopy.headline)}</strong>
                  <div class="run-metric-sub">${escapeHtml(signalCopy.detail)}</div>
                </div>
                <div class="run-metric">
                  <div class="run-metric-label">Windows</div>
                  <strong class="run-metric-value">${escapeHtml(windowCopy.headline)}</strong>
                  <div class="run-metric-sub">${escapeHtml(windowCopy.detail)}</div>
                </div>
                <div class="run-metric">
                  <div class="run-metric-label">Turns</div>
                  <strong class="run-metric-value">${escapeHtml(formatNumber(run.turns_taken, 0))}</strong>
                  <div class="run-metric-sub">Max ${escapeHtml(formatNumber(run.max_turns, 0))} · simulated stop ${escapeHtml(formatDate(run.simulated_end_time))}</div>
                </div>
              </div>
              <div class="chip-row">
                <span class="chip">${escapeHtml(humanizeToken(run.termination_reason || "termination_unknown"))}</span>
                <span class="chip">${escapeHtml(humanizeToken(run.overall_status || "run status unavailable"))}</span>
                <span class="chip">Unanswered ${escapeHtml(formatNumber(unansweredCount, 0))}</span>
              </div>
              <div class="capsule-source" style="margin-top:14px;">
                Source run: <a href="${escapeHtml(run.href)}" target="_blank" rel="noreferrer">${escapeHtml(run.path)}</a>
              </div>
            `;

            $("autopsy-fingerprint").innerHTML = `
              <div class="panel-head">
                <div>
                  <strong>Episode Fingerprint</strong>
                  <h3>How this run scored by TPM dimension</h3>
                </div>
              </div>
              <div class="run-fingerprint-grid">
                ${profile
                  .map((item) => {
                    const value = clamp(asNumber(item.score, 0), 0, 100);
                    return `
                      <div class="metric-row">
                        <div class="metric-label-line">
                          <span>${escapeHtml(item.label || item.id)}</span>
                          <strong>${escapeHtml(formatPercent(value))}</strong>
                        </div>
                        <div class="meter" style="--model-color:${escapeHtml(model.meta.color)};">
                          <span style="width:${value}%;"></span>
                        </div>
                      </div>
                    `;
                  })
                  .join("")}
              </div>
              <div class="chip-row">
                <span class="chip">Milestone outcomes ${escapeHtml(formatNumber(scoreBreakdown.milestone_score, 1))}</span>
                <span class="chip">Discovery ${escapeHtml(formatNumber(scoreBreakdown.discovery_score, 1))}</span>
                <span class="chip">Commitments ${escapeHtml(formatNumber(scoreBreakdown.commitment_score, 1))}</span>
              </div>
            `;

            const dossiers = (run.failure_dossiers || []).slice(0, 3);
            const fallbacks = (run.root_cause_findings || []).slice(0, 3);
            const dossierRows = dossiers.length ? dossiers : fallbacks;
            $("autopsy-dossiers").innerHTML = `
              <article class="panel">
                <div class="panel-head">
                  <div>
                    <strong>Failure Dossiers</strong>
                    <h3>Deterministic explanations of what actually broke</h3>
                  </div>
                </div>
                <div class="dossier-grid">
                  ${dossierRows
                    .map((dossier) => {
                      const chips = dossier.contributing_patterns || [];
                      return `
                        <div class="dossier-card">
                          <div class="dossier-head">
                            <div>
                              <strong>${escapeHtml(dossier.title || dossier.id || "Finding")}</strong>
                            </div>
                            <span class="severity-chip ${severityClass(dossier.severity)}">${escapeHtml(dossier.severity || "medium")}</span>
                          </div>
                          <p>${escapeHtml(dossier.headline || dossier.what_happened || "No narrative available.")}</p>
                          <div class="chip-row">
                            ${
                              dossier.lost_points
                                ? `<span class="chip">Lost ${escapeHtml(formatNumber(dossier.lost_points, 1))} pts</span>`
                                : ""
                            }
                            ${
                              dossier.deadline_label
                                ? `<span class="chip mono">${escapeHtml(dossier.deadline_label)}</span>`
                                : ""
                            }
                            ${
                              dossier.deterministic_fix_hint
                                ? `<span class="chip">${escapeHtml(dossier.deterministic_fix_hint)}</span>`
                                : ""
                            }
                          </div>
                          ${
                            chips.length
                              ? `
                                <div class="chip-row">
                                  ${chips
                                    .slice(0, 4)
                                    .map((item) => `<span class="chip mono">${escapeHtml(item.kind)} x${escapeHtml(formatNumber(item.count, 0))}</span>`)
                                    .join("")}
                                </div>
                              `
                              : ""
                          }
                        </div>
                      `;
                    })
                    .join("")}
                </div>
              </article>
            `;

            const actors = [...(run.stakeholder_engagement?.actors || [])].sort((left, right) => {
              return asNumber(right.outbound_count, 0) - asNumber(left.outbound_count, 0);
            });
            const maxOutbound = Math.max(1, ...actors.map((actor) => asNumber(actor.outbound_count, 0)));
            $("autopsy-actors").innerHTML = `
              <article class="panel">
                <div class="panel-head">
                  <div>
                    <strong>Stakeholder Field Map</strong>
                    <h3>Who absorbed the run's attention</h3>
                  </div>
                </div>
                <div class="actor-grid">
                  ${actors
                    .map((actor) => {
                      const outbound = asNumber(actor.outbound_count, 0);
                      const inbound = asNumber(actor.inbound_count, 0);
                      const engaged = actor.engaged_before_relevant_deadline ? "Engaged in time" : "Late / missed";
                      return `
                        <div class="actor-card" style="--model-color:${escapeHtml(model.meta.color)};">
                          <div class="actor-head">
                            <div>
                              <strong>${escapeHtml(actor.name || actor.actor_id)}</strong>
                              <div class="fault-item-meta">${escapeHtml(actor.role || actor.actor_id)}</div>
                            </div>
                            <span class="status-chip ${actor.engaged_before_relevant_deadline ? "status-hit" : "status-miss"}">${escapeHtml(engaged)}</span>
                          </div>
                          <div class="actor-stat-line">
                            <span>Outbound ${escapeHtml(formatNumber(outbound, 0))}</span>
                            <span>Inbound ${escapeHtml(formatNumber(inbound, 0))}</span>
                          </div>
                          <div class="meter" style="--model-color:${escapeHtml(model.meta.color)};"><span style="width:${(outbound / maxOutbound) * 100}%;"></span></div>
                          <div class="chip-row">
                            ${
                              actor.first_cue_at
                                ? `<span class="chip">Cue ${escapeHtml(formatDate(actor.first_cue_at))}</span>`
                                : `<span class="chip">Cue never surfaced</span>`
                            }
                            ${
                              actor.first_outbound_at
                                ? `<span class="chip">First outbound ${escapeHtml(formatDate(actor.first_outbound_at))}</span>`
                                : `<span class="chip">Never contacted</span>`
                            }
                            ${(actor.notes || [])
                              .slice(0, 2)
                              .map((note) => `<span class="chip">${escapeHtml(note)}</span>`)
                              .join("")}
                          </div>
                        </div>
                      `;
                    })
                    .join("")}
                </div>
              </article>
            `;

            $("autopsy-windows").innerHTML = `
              <div class="panel-head">
                <div>
                  <strong>Window Tape</strong>
                  <h3>Deadline windows and what the run did inside them</h3>
                </div>
              </div>
              <div class="window-grid">
                ${(run.window_scorecards || [])
                  .map((windowCard) => {
                    const hit = !!windowCard.state_achieved?.achieved;
                    return `
                      <div class="window-card">
                        <div class="window-head">
                          <div>
                            <strong>${escapeHtml(windowCard.title || windowCard.window_id)}</strong>
                            <div class="fault-item-meta">${escapeHtml(windowCard.required_state_change || "")}</div>
                          </div>
                          <span class="status-chip ${hit ? "status-hit" : "status-miss"}">${hit ? "hit" : "miss"}</span>
                        </div>
                        <p>${escapeHtml(windowCard.miss_reason || "State achieved before the deadline.")}</p>
                        <div class="chip-row">
                          <span class="chip">Actions ${escapeHtml(formatNumber(windowCard.actions_taken, 0))}</span>
                          <span class="chip">Reads ${escapeHtml(formatNumber(windowCard.reads, 0))}</span>
                          <span class="chip">Writes ${escapeHtml(formatNumber(windowCard.writes, 0))}</span>
                          ${(windowCard.top_action_families || [])
                            .slice(0, 2)
                            .map((item) => `<span class="chip mono">${escapeHtml(item.intent_family)} x${escapeHtml(formatNumber(item.count, 0))}</span>`)
                            .join("")}
                        </div>
                      </div>
                    `;
                  })
                  .join("")}
              </div>
            `;

            $("autopsy-timeline").innerHTML = `
              <div class="panel-head">
                <div>
                  <strong>Decisive Timeline</strong>
                  <h3>Moments the evaluator decided mattered</h3>
                </div>
              </div>
              <div class="timeline-grid">
                ${(run.decisive_timeline || [])
                  .slice(0, 8)
                  .map(
                    (item) => `
                      <div class="timeline-card">
                        <div class="timeline-time">${escapeHtml(formatDate(item.at))}</div>
                        <strong>${escapeHtml(item.event_type || "event")}</strong>
                        <p>${escapeHtml(item.summary || "No summary available.")}</p>
                        ${
                          item.related_rubric_line
                            ? `<div class="chip-row"><span class="chip mono">${escapeHtml(item.related_rubric_line)}</span></div>`
                            : ""
                        }
                      </div>
                    `
                  )
                  .join("")}
              </div>
            `;
          }

          function renderGallery() {
            const gallery = $("diagram-gallery");
            const diagrams = payload.diagrams || [];
            if (!diagrams.length) {
              gallery.innerHTML = "<p>No diagram PNGs were found in docs/diagrams.</p>";
              return;
            }
            gallery.innerHTML = diagrams
              .map(
                (diagram) => `
                  <article class="gallery-card" data-lightbox-src="${escapeHtml(diagram.href)}" data-lightbox-title="${escapeHtml(diagram.label)}">
                    <img src="${escapeHtml(diagram.href)}" alt="${escapeHtml(diagram.label)}" loading="lazy" />
                    <div class="gallery-card-copy">
                      <strong>${escapeHtml(diagram.label)}</strong>
                      <p>${escapeHtml(diagram.description)}</p>
                    </div>
                  </article>
                `
              )
              .join("");

            gallery.querySelectorAll("[data-lightbox-src]").forEach((card) => {
              card.addEventListener("click", () => openLightbox(card.dataset.lightboxSrc, card.dataset.lightboxTitle));
            });
          }

          function openLightbox(src, title) {
            $("lightbox-image").src = src || "";
            $("lightbox-image").alt = title || "Diagram";
            $("lightbox-title").textContent = title || "Diagram";
            $("lightbox").classList.add("is-open");
            $("lightbox").setAttribute("aria-hidden", "false");
          }

          function closeLightbox() {
            $("lightbox").classList.remove("is-open");
            $("lightbox").setAttribute("aria-hidden", "true");
            $("lightbox-image").src = "";
          }

          function renderDynamicSections() {
            renderHero();
            renderScenarioButtons();
            const scenario = getScenario();
            if (!scenario) return;
            renderEvaluationRubric(scenario);
            renderTopline(scenario);
            renderModelCapsules(scenario);
            renderScoreSkyline(scenario);
            renderCompetencyRadar(scenario);
            renderSignalLattice(scenario);
            renderRootCauseForge(scenario);
            renderBundleRunTable(scenario);
            renderAutopsySwitcher(scenario);
            renderAutopsySection(scenario);
          }

          function wireReveals() {
            const observer = new IntersectionObserver(
              (entries) => {
                entries.forEach((entry) => {
                  if (entry.isIntersecting) {
                    entry.target.classList.add("is-visible");
                  }
                });
              },
              { threshold: 0.12 }
            );
            document.querySelectorAll(".reveal").forEach((node) => observer.observe(node));
          }

          function init() {
            renderStaticSections();
            renderDynamicSections();
            renderGallery();
            wireReveals();
            $("lightbox-close").addEventListener("click", closeLightbox);
            $("lightbox").addEventListener("click", (event) => {
              if (event.target === $("lightbox")) closeLightbox();
            });
            window.addEventListener("keydown", (event) => {
              if (event.key === "Escape") closeLightbox();
            });
          }

          init();
        </script>
      </body>
    </html>
    """
)


def build_html(payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return HTML_TEMPLATE.replace("__DATA__", data)


def parse_args() -> argparse.Namespace:
    root = repo_root()
    parser = argparse.ArgumentParser(description="Build a presentation-ready TPM benchmark dashboard.")
    parser.add_argument(
        "--artifact-root",
        default=str(root / ".artifacts"),
        help="Root directory to scan for bundle and run summary artifacts.",
    )
    parser.add_argument(
        "--output",
        default=str(root / "docs" / "review_session_dashboard.html"),
        help="Output HTML file path.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = repo_root()
    artifact_root = Path(args.artifact_root).resolve()
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_payload(root, artifact_root, output_path)
    output_path.write_text(build_html(payload), encoding="utf-8")
    print(f"Wrote dashboard to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
