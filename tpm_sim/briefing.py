from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from tpm_sim.authoring.briefs import AuthoringBrief, load_brief
from tpm_sim.common import format_dt, from_iso, summarize_lines


BRIEFING_SCHEMA_VERSION = "operator_briefing_v1"
PROPOSAL_BRIEFING_JSON = "operator_briefing.json"
PROPOSAL_BRIEFING_MARKDOWN = "operator_briefing.md"

AUTHORITY_LABELS = {
    "can_approve_design": "approve design",
    "can_approve_review_slot": "approve review slots",
    "can_approve_scope": "approve scope",
    "can_block_launch": "block launch",
    "can_commit_eta": "commit ETA",
    "can_commit_external_dates": "commit external dates",
    "can_grant_review": "grant review",
    "can_re_scope_product": "re-scope product",
    "can_reassign_people": "reassign people",
    "can_schedule_meeting": "schedule meetings",
    "can_schedule_staffing": "schedule staffing",
    "can_update_customer_context": "update customer context",
    "can_update_tracker": "update tracker",
    "can_veto_launch": "veto launch",
}

FAILURE_CLASS_LABELS = {
    "commitment": "making grounded commitments instead of fake green",
    "discovery": "finding the real blockers and stakeholder drivers early",
    "relationship": "protecting trust with the people who matter",
    "timing": "moving before windows close",
}


def proposal_briefing_paths(proposal_dir: str | Path) -> dict[str, Path]:
    root = Path(proposal_dir)
    reports = root / "reports"
    return {
        "json": reports / PROPOSAL_BRIEFING_JSON,
        "markdown": reports / PROPOSAL_BRIEFING_MARKDOWN,
    }


def scenario_briefing_paths(scenario_id: str) -> dict[str, Path]:
    base = Path(resources.files("tpm_sim.scenarios").joinpath(scenario_id))
    return {
        "json": base / PROPOSAL_BRIEFING_JSON,
        "markdown": base / PROPOSAL_BRIEFING_MARKDOWN,
    }


def build_authoring_briefing(
    brief: AuthoringBrief,
    *,
    scenario: dict[str, Any] | None = None,
    proposal_status: dict[str, Any] | None = None,
    run_context: dict[str, Any] | None = None,
    source_kind: str = "authoring_brief",
) -> dict[str, Any]:
    actor_lookup: dict[str, dict[str, Any]] = {}
    if scenario is not None:
        actor_lookup = {
            actor["id"]: actor
            for actor in scenario.get("world", {}).get("actors", [])
            if actor.get("id") != "tpm"
        }
        _validate_cast_ids_against_scenario(brief, actor_lookup)

    deadlines = _build_deadlines(brief, scenario)
    cast = _build_cast_cards(brief, actor_lookup)
    focus_areas = _build_focus_areas(brief, scenario)
    hidden_landscape = _build_hidden_landscape(brief)
    how_to_win = _build_how_to_win(brief, deadlines)
    how_to_fail = _build_how_to_fail(brief, hidden_landscape)
    project_name = scenario.get("world", {}).get("project", {}).get("name") if scenario else None
    project_summary = scenario.get("world", {}).get("project", {}).get("description") if scenario else brief.summary
    title = scenario.get("name") if scenario else brief.title

    return {
        "schema_version": BRIEFING_SCHEMA_VERSION,
        "source_kind": source_kind,
        "scenario_id": brief.scenario_id,
        "title": title,
        "project_name": project_name,
        "premise": brief.summary,
        "project_summary": project_summary,
        "window": {
            "start_at": brief.start_at,
            "end_at": brief.end_at,
            "start_display": _display_time(brief.start_at),
            "end_display": _display_time(brief.end_at),
        },
        "focus_areas": focus_areas,
        "how_to_win": how_to_win,
        "how_to_fail": how_to_fail,
        "cast": cast,
        "hidden_landscape": hidden_landscape,
        "critical_path": list(brief.critical_path),
        "deadlines": deadlines,
        "proposal_status": proposal_status,
        "run_context": run_context,
    }


def build_scenario_fallback_briefing(
    scenario_id: str,
    scenario: dict[str, Any],
    *,
    run_context: dict[str, Any] | None = None,
    source_kind: str = "scenario_fallback",
) -> dict[str, Any]:
    facts = scenario.get("world", {}).get("facts", [])
    hidden_by_actor: dict[str, list[str]] = {}
    hidden_landscape: list[str] = []
    for fact in facts:
        metadata = fact.get("metadata", {})
        summary = fact.get("label") or fact.get("description") or fact.get("id")
        owner_actor_id = metadata.get("owner_actor_id")
        if metadata.get("fact_kind") == "actor_private_driver" and owner_actor_id:
            hidden_by_actor.setdefault(owner_actor_id, []).append(str(summary))
        elif summary:
            hidden_landscape.append(str(summary))

    cast = []
    for actor in scenario.get("world", {}).get("actors", []):
        actor_id = actor.get("id")
        if actor_id == "tpm":
            continue
        cast.append(
            {
                "id": actor_id,
                "name": actor.get("name", actor_id),
                "org_role": actor.get("org_role", "unknown"),
                "coordination_template": actor.get("coordination_template"),
                "visible_goal": None,
                "hidden_drivers": hidden_by_actor.get(actor_id, []),
                "decision_rights": _summarize_authority_profile(actor.get("authority_profile", {})),
            }
        )

    deadlines = []
    for milestone in scenario.get("world", {}).get("milestones", []):
        due_at = milestone.get("due_at")
        if not due_at:
            continue
        deadlines.append(
            {
                "id": milestone.get("id", "unknown"),
                "summary": milestone.get("description") or milestone.get("title") or milestone.get("id", "unknown"),
                "at": due_at,
                "display_at": _display_time(due_at),
            }
        )

    how_to_win = [
        "Read the scenario premise, actor cards, and deadlines before trusting the run.",
        "Use the milestone windows and actor decision rights to sanity-check whether the plan is credible.",
    ]
    how_to_fail = [
        "If this accepted scenario is missing an operator briefing artifact, some visible goals may be unavailable in preflight.",
    ]

    return {
        "schema_version": BRIEFING_SCHEMA_VERSION,
        "source_kind": source_kind,
        "scenario_id": scenario_id,
        "title": scenario.get("name", scenario_id),
        "project_name": scenario.get("world", {}).get("project", {}).get("name"),
        "premise": scenario.get("world", {}).get("project", {}).get("description", ""),
        "project_summary": scenario.get("world", {}).get("project", {}).get("description", ""),
        "window": {
            "start_at": scenario.get("start_at"),
            "end_at": scenario.get("end_at"),
            "start_display": _display_time(scenario.get("start_at")),
            "end_display": _display_time(scenario.get("end_at")),
        },
        "focus_areas": list(scenario.get("evaluation", {}).get("primary_failure_classes", [])),
        "how_to_win": how_to_win,
        "how_to_fail": how_to_fail,
        "cast": cast,
        "hidden_landscape": hidden_landscape[:6],
        "critical_path": [],
        "deadlines": deadlines,
        "proposal_status": None,
        "run_context": run_context,
    }


def load_scenario_briefing(
    scenario_id: str,
    *,
    run_context: dict[str, Any] | None = None,
    bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paths = scenario_briefing_paths(scenario_id)
    if paths["json"].exists():
        payload = json.loads(paths["json"].read_text())
        if run_context is not None:
            payload = with_run_context(payload, run_context)
        return payload

    brief_path = Path(__file__).resolve().parents[1] / "authoring" / "briefs" / f"{scenario_id}.json"
    if brief_path.exists():
        brief = load_brief(brief_path)
        scenario = None if bundle is None else bundle.get("scenario")
        return build_authoring_briefing(
            brief,
            scenario=scenario,
            run_context=run_context,
            source_kind="authoring_brief_fallback",
        )

    if bundle is None:
        from tpm_sim.scenario import load_scenario_bundle

        bundle = load_scenario_bundle(scenario_id)
    return build_scenario_fallback_briefing(scenario_id, bundle["scenario"], run_context=run_context)


def with_run_context(briefing: dict[str, Any], run_context: dict[str, Any] | None) -> dict[str, Any]:
    payload = json.loads(json.dumps(briefing))
    payload["run_context"] = run_context
    return payload


def write_operator_briefing_artifacts(
    briefing: dict[str, Any],
    *,
    json_path: str | Path,
    markdown_path: str | Path,
) -> dict[str, str]:
    json_target = Path(json_path)
    markdown_target = Path(markdown_path)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    markdown_target.parent.mkdir(parents=True, exist_ok=True)
    json_target.write_text(json.dumps(briefing, indent=2, sort_keys=True))
    markdown_target.write_text(render_operator_briefing(briefing, compact=False))
    return {
        "json_path": str(json_target),
        "markdown_path": str(markdown_target),
    }


def render_operator_briefing(briefing: dict[str, Any], *, compact: bool) -> str:
    lines = [
        f"{'Run Preflight' if briefing.get('run_context') else 'Scenario Briefing'}: {briefing['title']} ({briefing['scenario_id']})",
    ]
    if briefing.get("project_name"):
        lines.append(f"Project: {briefing['project_name']}")
    lines.append(f"Premise: {briefing['premise']}")
    if briefing.get("project_summary") and briefing["project_summary"] != briefing["premise"]:
        lines.append(f"Project Detail: {briefing['project_summary']}")
    window = briefing.get("window", {})
    if window.get("start_display") and window.get("end_display"):
        lines.append(f"Window: {window['start_display']} -> {window['end_display']}")

    lines.extend(["", "How To Win / How To Fail:"])
    for item in briefing.get("how_to_win", []):
        lines.append(f"- Win: {item}")
    for item in briefing.get("how_to_fail", []):
        lines.append(f"- Fail: {item}")

    lines.extend(["", "Cast:"])
    cast_limit = 8 if compact else None
    cast_entries = briefing.get("cast", [])
    if cast_limit is not None:
        cast_entries = cast_entries[:cast_limit]
    for actor in cast_entries:
        role = _humanize(actor.get("org_role") or "unknown")
        template = actor.get("coordination_template")
        header = f"- {actor['name']} ({actor['id']})"
        header += f" — {role}"
        if template:
            header += f" / {_humanize(template)}"
        lines.append(header)
        if actor.get("visible_goal"):
            lines.append(f"  Visible goal: {actor['visible_goal']}")
        if actor.get("hidden_drivers"):
            lines.append(f"  Hidden driver: {_join_sentences(actor['hidden_drivers'])}")
        if actor.get("decision_rights"):
            lines.append(f"  Decision rights: {', '.join(actor['decision_rights'])}")
    if cast_limit is not None and len(briefing.get("cast", [])) > len(cast_entries):
        lines.append(f"- +{len(briefing['cast']) - len(cast_entries)} more actors")

    if briefing.get("hidden_landscape"):
        lines.extend(["", "Hidden Landscape:"])
        hidden_limit = 6 if not compact else 4
        hidden_entries = briefing["hidden_landscape"][:hidden_limit]
        for item in hidden_entries:
            lines.append(f"- {item}")
        if len(briefing["hidden_landscape"]) > len(hidden_entries):
            lines.append(f"- +{len(briefing['hidden_landscape']) - len(hidden_entries)} more hidden pressures")

    lines.extend(["", "Critical Path And Deadlines:"])
    if briefing.get("critical_path"):
        lines.append(f"- Critical path: {', '.join(briefing['critical_path'])}")
    deadline_limit = 6 if not compact else 4
    deadline_entries = briefing.get("deadlines", [])[:deadline_limit]
    for item in deadline_entries:
        lines.append(f"- {item['id']} by {item['display_at']} — {item['summary']}")
    if len(briefing.get("deadlines", [])) > len(deadline_entries):
        lines.append(f"- +{len(briefing['deadlines']) - len(deadline_entries)} more deadlines")

    if briefing.get("proposal_status"):
        lines.extend(["", "Proposal Status:"])
        for item in briefing["proposal_status"].get("items", []):
            lines.append(f"- {item['label']}: {item['value']}")

    if briefing.get("run_context"):
        lines.extend(["", briefing["run_context"].get("title", "Run Context") + ":"])
        for item in briefing["run_context"].get("items", []):
            lines.append(f"- {item['label']}: {item['value']}")

    return summarize_lines(lines)


def build_proposal_status(
    manifest: dict[str, Any] | None,
    *,
    validation: dict[str, Any] | None = None,
    closure: dict[str, Any] | None = None,
    diff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    items = [{"label": "workflow status", "value": (manifest or {}).get("status", "unknown")}]
    if validation is not None:
        items.append({"label": "validation", "value": "passed" if validation.get("valid") else "failed"})
    if closure is not None:
        items.append({"label": "closure", "value": "passed" if closure.get("passed") else "failed"})
    if diff is not None:
        items.append({"label": "diff", "value": _summarize_diff(diff)})
    return {"title": "Proposal Status", "items": items}


def build_run_context(command_name: str, items: list[tuple[str, Any]]) -> dict[str, Any]:
    return {
        "title": "Run Context",
        "items": [{"label": label, "value": str(value)} for label, value in items if value not in {None, ""}],
        "command": command_name,
    }


def _validate_cast_ids_against_scenario(brief: AuthoringBrief, actor_lookup: dict[str, dict[str, Any]]) -> None:
    cast_ids = {member["id"] for member in brief.cast}
    actor_ids = set(actor_lookup)
    missing = sorted(actor_ids - cast_ids)
    unexpected = sorted(cast_ids - actor_ids)
    if missing or unexpected:
        fragments = []
        if missing:
            fragments.append(f"missing cast ids for scenario actors: {', '.join(missing)}")
        if unexpected:
            fragments.append(f"unknown cast ids: {', '.join(unexpected)}")
        raise RuntimeError("Authoring brief cast ids do not match scenario actors; " + "; ".join(fragments))


def _build_cast_cards(brief: AuthoringBrief, actor_lookup: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    cards = []
    for member in brief.cast:
        actor = actor_lookup.get(member["id"], {})
        cards.append(
            {
                "id": member["id"],
                "name": actor.get("name", member.get("name", member["id"])),
                "org_role": actor.get("org_role", member.get("org_role", "unknown")),
                "coordination_template": actor.get("coordination_template", member.get("coordination_template")),
                "visible_goal": member.get("notes"),
                "hidden_drivers": [driver["summary"] for driver in member.get("private_drivers", [])],
                "decision_rights": _summarize_authority_profile(actor.get("authority_profile", {})),
            }
        )
    return cards


def _build_hidden_landscape(brief: AuthoringBrief) -> list[str]:
    actor_driver_ids = {
        driver["id"]
        for member in brief.cast
        for driver in member.get("private_drivers", [])
    }
    entries = [
        item["summary"]
        for item in brief.hidden_facts
        if item.get("summary") and item.get("id") not in actor_driver_ids
    ]
    return entries[:6]


def _build_deadlines(brief: AuthoringBrief, scenario: dict[str, Any] | None) -> list[dict[str, Any]]:
    if scenario is None:
        rows = [
            {
                "id": item["id"],
                "summary": item["summary"],
                "at": item["deadline"],
                "display_at": _display_time(item["deadline"]),
            }
            for item in brief.milestones
        ]
        return sorted(rows, key=lambda item: item["at"])

    summary_lookup = {item["id"]: item["summary"] for item in brief.milestones}
    rows = []
    for item in scenario.get("world", {}).get("milestones", []):
        due_at = item.get("due_at")
        if not due_at:
            continue
        rows.append(
            {
                "id": item.get("id", "unknown"),
                "summary": summary_lookup.get(item.get("id"), item.get("description") or item.get("title") or item.get("id", "unknown")),
                "at": due_at,
                "display_at": _display_time(due_at),
            }
        )
    return sorted(rows, key=lambda item: item["at"])


def _build_focus_areas(brief: AuthoringBrief, scenario: dict[str, Any] | None) -> list[str]:
    ranked = sorted(
        ((key, value) for key, value in brief.scoring_emphasis.items()),
        key=lambda item: (-int(item[1]), item[0]),
    )
    focus = [f"{_humanize(key)} ({value})" for key, value in ranked[:4]]
    if scenario is not None:
        for key in scenario.get("evaluation", {}).get("primary_failure_classes", []):
            label = _humanize(key)
            if label not in focus:
                focus.append(label)
    return focus


def _build_how_to_win(brief: AuthoringBrief, deadlines: list[dict[str, Any]]) -> list[str]:
    lines = []
    if brief.critical_path:
        lines.append(f"Move the critical path early: {', '.join(brief.critical_path)}.")
    if deadlines:
        first_deadline = deadlines[0]
        final_deadline = deadlines[-1]
        lines.append(
            "Protect the milestone windows, starting with "
            f"{first_deadline['id']} by {first_deadline['display_at']} and ending with "
            f"{final_deadline['id']} by {final_deadline['display_at']}."
        )
    if brief.scoring_emphasis:
        ranked = sorted(brief.scoring_emphasis.items(), key=lambda item: (-int(item[1]), item[0]))
        summary = ", ".join(f"{_humanize(key)} ({value})" for key, value in ranked[:4])
        lines.append(f"Scoring leans on {summary}.")
    focus = [
        FAILURE_CLASS_LABELS.get(item, _humanize(item))
        for item in brief.failure_classes
        if item in FAILURE_CLASS_LABELS
    ]
    if focus:
        lines.append("Good TPM behavior here means " + "; ".join(focus) + ".")
    return lines[:4]


def _build_how_to_fail(brief: AuthoringBrief, hidden_landscape: list[str]) -> list[str]:
    lines = []
    if brief.failure_classes:
        labels = [_humanize(item) for item in brief.failure_classes]
        lines.append("This scenario punishes " + ", ".join(labels) + " failures.")
    if hidden_landscape:
        lines.append("Key hidden pressure: " + "; ".join(hidden_landscape[:2]))
    driver_summaries = [
        driver["summary"]
        for member in brief.cast
        for driver in member.get("private_drivers", [])
    ]
    if driver_summaries:
        lines.append("Actor traps: " + "; ".join(driver_summaries[:2]))
    return lines[:4]


def _summarize_authority_profile(authority_profile: dict[str, Any]) -> list[str]:
    rights = []
    for key, value in authority_profile.items():
        if value:
            rights.append(AUTHORITY_LABELS.get(key, _humanize(key.removeprefix("can_"))))
    return rights


def _summarize_diff(diff: dict[str, Any]) -> str:
    if not diff.get("scenario_exists"):
        return "net-new scenario"
    top_level = diff.get("scenario_changes", {}).get("top_level_changed", [])
    changed_count = len(top_level)
    if changed_count == 0:
        return "no top-level scenario drift"
    preview = ", ".join(top_level[:3])
    suffix = "" if changed_count <= 3 else f", +{changed_count - 3} more"
    return f"{changed_count} top-level changes ({preview}{suffix})"


def _join_sentences(values: list[str]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    return "; ".join(values[:2])


def _humanize(value: str) -> str:
    return value.replace("_", " ")


def _display_time(raw: str | None) -> str:
    if not raw:
        return "unknown"
    return format_dt(from_iso(raw))
