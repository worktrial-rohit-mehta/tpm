from __future__ import annotations

import json
from typing import Any


AUTHORING_PROMPT_VERSION = "authoring_prompt_v5"


def build_world_prompt(brief: dict[str, Any], accepted_reference: dict[str, Any] | None = None) -> dict[str, Any]:
    user_payload: dict[str, Any] = {
        "brief": brief,
        "artifact_contract": {
            "required_top_level_keys": ["id", "name", "timezone", "start_at", "end_at", "world", "policy", "evaluation"],
            "required_world_keys": [
                "project",
                "actors",
                "relationships",
                "windows",
                "threads",
                "documents",
                "tasks",
                "milestones",
                "dependencies",
                "facts",
                "beliefs",
                "commitments",
                "meetings",
                "messages",
                "pending_events",
            ],
            "required_policy_keys": [
                "timing_bands",
                "default_timing_band",
                "action_costs",
                "project_state_rules",
                "task_transitions",
                "meeting_defaults",
                "meeting_outcomes",
                "external_commitment_requirements",
            ],
            "required_evaluation_keys": ["official_seeds", "primary_failure_classes", "rubric_lines"],
            "forbidden_top_level_keys": ["brief", "notes", "analysis", "explanation"],
            "requirements": [
                "Produce the final runtime artifact, not a summary of the brief.",
                "Use explicit deterministic predicates and scoring rules.",
                "Keep the scenario bounded and internally consistent.",
                "Encode hidden stakeholder motives as world facts with metadata fact_kind=actor_private_driver, owner_actor_id, driver_type, and coordination_implication.",
            ],
        },
    }
    if accepted_reference is not None:
        user_payload["accepted_reference"] = accepted_reference
    return {
        "system": (
            "You are assisting with benchmark authoring. Convert the structured authoring brief into a complete "
            "runtime scenario.json artifact for a deterministic TPM simulation harness. Return a single JSON object "
            "only, with no prose, no markdown, and no wrapper fields. Do not echo the brief. The output must have "
            "top-level keys exactly: id, name, timezone, start_at, end_at, world, policy, evaluation. Inside world, "
            "include exactly these sections: project, actors, relationships, windows, threads, documents, tasks, "
            "milestones, dependencies, facts, beliefs, commitments, meetings, messages, pending_events. Inside "
            "policy, include: timing_bands, default_timing_band, action_costs, project_state_rules, task_transitions, "
            "meeting_defaults, meeting_outcomes, external_commitment_requirements. Inside evaluation, include: "
            "official_seeds, primary_failure_classes, rubric_lines. Every rubric line must include competency_tags, "
            "measurement_rationale, success_meaning, and failure_meaning. Keep runtime semantics explicit, frozen, "
            "and deterministic. Do not model hidden agendas in actor metadata; express them as actor private-driver "
            "facts that the TPM can only infer from visible cues. If an accepted_reference artifact is provided, treat "
            "it as the required runtime shape and preserve that level of completeness."
        ),
        "user": json.dumps(user_payload, indent=2, sort_keys=True),
        "metadata": {"prompt_version": AUTHORING_PROMPT_VERSION, "artifact": "scenario.json"},
    }


def build_semantics_prompt(
    brief: dict[str, Any],
    scenario_candidate: dict[str, Any],
    coverage_contract: dict[str, Any],
    accepted_reference: dict[str, Any] | None = None,
) -> dict[str, Any]:
    user_payload: dict[str, Any] = {
        "brief": brief,
        "scenario": scenario_candidate,
        "coverage_contract": coverage_contract,
        "artifact_contract": {
            "required_top_level_keys": ["version", "cells"],
            "forbidden_top_level_keys": ["brief", "notes", "analysis", "explanation"],
            "requirements": [
                "Preserve every cell_id from the coverage_contract exactly.",
                "Do not add, remove, or rename cells.",
                "Do not change selectors, guards, reachability, or criticality.",
                "Only fill semantic response details for the declared cells.",
                "Each response_envelope must use the exact fields: id, weight, outgoing_act_id, outgoing_slots, surface_facts, belief_signals, effects, renderer_variants.",
                "Use a single outgoing_act_id string, never an array like outgoing_acts.",
                "Use outgoing_slots, never slots.",
                "renderer_variants must be a non-empty list of deterministic text variants.",
                "Leak actor private drivers through low-confidence cue belief_signals with accumulate=true instead of immediate surface_facts whenever the hidden truth is a stakeholder motive or sensitivity.",
            ],
        },
    }
    if accepted_reference is not None:
        user_payload["accepted_reference"] = accepted_reference
    return {
        "system": (
            "You are assisting with benchmark authoring. Generate coverage_semantics.json for the provided deterministic "
            "TPM scenario and deterministic coverage contract. Return a single JSON object only, with no prose and no "
            "markdown. The output must have top-level keys exactly: version, cells. Each cell entry must preserve the "
            "provided cell_id and only fill response semantics for that declared interaction situation. Do not add or "
            "remove cells. Do not change selectors, guards, or reachability. For each cell, provide realistic but "
            "bounded response_envelopes using the exact schema fields id, weight, outgoing_act_id, outgoing_slots, "
            "surface_facts, belief_signals, effects, and renderer_variants. Use one outgoing_act_id string per "
            "envelope, not an array. renderer_variants must never be empty. "
            "When a response is hinting at an actor private driver, use shared belief_key/value pairs plus "
            "metadata.private_driver_fact_id and accumulate=true so repeated cues can deterministically cross the "
            "surfacing threshold. If an accepted_reference artifact is provided, treat it as the required semantic "
            "richness and review standard."
        ),
        "user": json.dumps(user_payload, indent=2, sort_keys=True),
        "metadata": {"prompt_version": AUTHORING_PROMPT_VERSION, "artifact": "coverage_semantics.json"},
    }


def build_trajectories_prompt(
    brief: dict[str, Any],
    scenario_candidate: dict[str, Any],
    accepted_reference: dict[str, str] | None = None,
) -> dict[str, Any]:
    user_payload: dict[str, Any] = {"brief": brief, "scenario": scenario_candidate}
    if accepted_reference is not None:
        user_payload["accepted_reference"] = accepted_reference
    return {
        "system": (
            "You are assisting with benchmark authoring. Generate a JSON object that maps TPM trajectory filenames to "
            "their script contents. Return a single JSON object only, with no prose and no markdown. Keys must be "
            "filenames ending in .tpm and values must be the full script contents. Include at least smoke.tpm and one "
            "anti-pattern trajectory when appropriate. If accepted_reference trajectories are provided, treat them as "
            "the required shell command style and fidelity."
        ),
        "user": json.dumps(user_payload, indent=2, sort_keys=True),
        "metadata": {"prompt_version": AUTHORING_PROMPT_VERSION, "artifact": "trajectories.json"},
    }


def build_gap_fill_semantics_prompt(
    brief: dict[str, Any],
    scenario_candidate: dict[str, Any],
    coverage_contract: dict[str, Any],
    coverage_semantics: dict[str, Any],
    gaps: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "system": (
            "You are assisting with benchmark authoring. The deterministic coverage contract has already been expanded "
            "to include the listed gap cells. Generate an updated full coverage_semantics.json that fills realistic "
            "semantics for those cells while preserving existing cell ids and semantics for unchanged cells. Do not "
            "change or remove contract cells. Return a single JSON object only, with no prose and no markdown."
        ),
        "user": json.dumps(
            {
                "brief": brief,
                "scenario": scenario_candidate,
                "coverage_contract": coverage_contract,
                "coverage_semantics": coverage_semantics,
                "gaps": gaps,
            },
            indent=2,
            sort_keys=True,
        ),
        "metadata": {"prompt_version": AUTHORING_PROMPT_VERSION, "artifact": "coverage_semantics.json"},
    }


def build_coverage_prompt(
    brief: dict[str, Any],
    scenario_candidate: dict[str, Any],
    accepted_reference: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_semantics_prompt(
        brief,
        scenario_candidate,
        coverage_contract={"version": "legacy_alias", "cells": []},
        accepted_reference=accepted_reference,
    )


def build_gap_fill_prompt(
    brief: dict[str, Any],
    scenario_candidate: dict[str, Any],
    coverage_candidate: dict[str, Any],
    gaps: list[dict[str, Any]],
) -> dict[str, Any]:
    return build_gap_fill_semantics_prompt(
        brief,
        scenario_candidate,
        coverage_contract={"version": "legacy_alias", "cells": []},
        coverage_semantics=coverage_candidate,
        gaps=gaps,
    )
