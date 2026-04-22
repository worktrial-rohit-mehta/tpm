from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REQUIRED_PRIVATE_DRIVER_FIELDS = {
    "id",
    "driver_type",
    "summary",
    "coordination_implication",
    "cue_examples",
}


@dataclass
class AuthoringBrief:
    scenario_id: str
    title: str
    summary: str
    timezone: str
    start_at: str
    end_at: str
    cast: list[dict[str, Any]]
    critical_path: list[str]
    milestones: list[dict[str, Any]]
    hidden_facts: list[dict[str, Any]]
    failure_classes: list[str]
    scoring_emphasis: dict[str, Any]
    realism_notes: list[str]
    non_goals: list[str]
    reference_scenario_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


REQUIRED_BRIEF_FIELDS = {
    "scenario_id",
    "title",
    "summary",
    "timezone",
    "start_at",
    "end_at",
    "cast",
    "critical_path",
    "milestones",
    "hidden_facts",
    "failure_classes",
    "scoring_emphasis",
    "realism_notes",
    "non_goals",
}


def load_brief(path: str | Path) -> AuthoringBrief:
    payload = json.loads(Path(path).read_text())
    validate_brief(payload)
    return AuthoringBrief(**payload)


def validate_brief(payload: dict[str, Any]) -> None:
    missing = sorted(REQUIRED_BRIEF_FIELDS - set(payload))
    if missing:
        raise ValueError(f"Authoring brief is missing required fields: {', '.join(missing)}")
    if not isinstance(payload["cast"], list) or not payload["cast"]:
        raise ValueError("Authoring brief cast must be a non-empty list.")
    for member in payload["cast"]:
        if not isinstance(member, dict):
            raise ValueError("Each cast member must be an object.")
        if "private_drivers" not in member:
            raise ValueError("Each cast member must include private_drivers (use an empty list when none apply).")
        if not isinstance(member["private_drivers"], list):
            raise ValueError("cast[].private_drivers must be a list.")
        for driver in member["private_drivers"]:
            if not isinstance(driver, dict):
                raise ValueError("Each private driver must be an object.")
            missing_driver_fields = sorted(REQUIRED_PRIVATE_DRIVER_FIELDS - set(driver))
            if missing_driver_fields:
                raise ValueError(
                    "private_drivers entries are missing required fields: "
                    + ", ".join(missing_driver_fields)
                )
            if not isinstance(driver["cue_examples"], list) or not driver["cue_examples"]:
                raise ValueError("private_drivers[].cue_examples must be a non-empty list.")
    if not isinstance(payload["milestones"], list) or not payload["milestones"]:
        raise ValueError("Authoring brief milestones must be a non-empty list.")
    if not isinstance(payload["failure_classes"], list) or not payload["failure_classes"]:
        raise ValueError("Authoring brief failure_classes must be a non-empty list.")
