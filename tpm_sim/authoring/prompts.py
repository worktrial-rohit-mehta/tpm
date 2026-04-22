from __future__ import annotations

import json
from typing import Any


AUTHORING_PROMPT_VERSION = "authoring_prompt_v1"


def build_world_prompt(brief: dict[str, Any]) -> dict[str, Any]:
    return {
        "system": (
            "You are assisting with benchmark authoring. Generate a complete scenario.json for a deterministic TPM "
            "simulation harness. Return valid JSON only. Keep runtime semantics explicit and frozen."
        ),
        "user": json.dumps({"brief": brief}, indent=2, sort_keys=True),
        "metadata": {"prompt_version": AUTHORING_PROMPT_VERSION, "artifact": "scenario.json"},
    }


def build_coverage_prompt(brief: dict[str, Any], scenario_candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "system": (
            "You are assisting with benchmark authoring. Generate npc_coverage.json for the provided deterministic "
            "TPM scenario. Return valid JSON only. Use bounded context families and deterministic renderers."
        ),
        "user": json.dumps({"brief": brief, "scenario": scenario_candidate}, indent=2, sort_keys=True),
        "metadata": {"prompt_version": AUTHORING_PROMPT_VERSION, "artifact": "npc_coverage.json"},
    }


def build_trajectories_prompt(brief: dict[str, Any], scenario_candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "system": (
            "You are assisting with benchmark authoring. Generate a JSON object mapping trajectory filenames to TPM "
            "shell script contents. Include at least smoke.tpm and one anti-pattern trajectory when appropriate."
        ),
        "user": json.dumps({"brief": brief, "scenario": scenario_candidate}, indent=2, sort_keys=True),
        "metadata": {"prompt_version": AUTHORING_PROMPT_VERSION, "artifact": "trajectories.json"},
    }


def build_gap_fill_prompt(
    brief: dict[str, Any],
    scenario_candidate: dict[str, Any],
    coverage_candidate: dict[str, Any],
    gaps: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "system": (
            "You are assisting with benchmark authoring. Update the provided npc_coverage.json to close the listed "
            "coverage gaps while preserving deterministic semantics. Return valid JSON only."
        ),
        "user": json.dumps(
            {"brief": brief, "scenario": scenario_candidate, "coverage": coverage_candidate, "gaps": gaps},
            indent=2,
            sort_keys=True,
        ),
        "metadata": {"prompt_version": AUTHORING_PROMPT_VERSION, "artifact": "npc_coverage.json"},
    }
