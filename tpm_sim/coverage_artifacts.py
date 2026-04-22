from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from tpm_sim.common import as_json, stable_digest
from tpm_sim.specs import (
    CONTEXT_FAMILY_SCHEMA_VERSION,
    COVERAGE_CONTRACT_VERSION,
    COVERAGE_SEMANTICS_VERSION,
    RENDERER_VERSION,
    require_known_act,
)

ALLOWED_SELECTOR_FIELDS = {
    "actor_id",
    "coordination_template",
    "org_role",
    "surface",
    "incoming_act_id",
    "trust_band",
    "pressure_band",
    "alignment_band",
    "timing_band",
    "dependency_band",
    "commitment_band",
    "available_for_meeting",
    "launch_scope",
}

ALLOWED_EFFECT_TYPES = {
    "relationship_delta",
    "project_state_patch",
    "actor_state_patch",
    "belief_signal",
    "fact_signal",
    "create_or_update_commitment",
    "task_state_patch",
    "meeting_schedule_hint",
}


ACT_AFFORDANCE_LIBRARY_V1: dict[str, dict[str, list[str]]] = {
    "critical_path_owner": {
        "chat": [
            "request.feasibility",
            "request.eta",
            "request.scope_tradeoff",
            "request.clarification",
            "negotiate.scope",
            "negotiate.timeline",
            "commit.propose",
            "commit.confirm",
            "inform.blocker",
            "inform.risk",
        ]
    },
    "cross_functional_dependency_owner": {
        "chat": [
            "request.review",
            "request.approval",
            "request.clarification",
            "request.scope_tradeoff",
            "request.ownership",
            "negotiate.scope",
            "inform.status_update",
            "inform.decision",
        ]
    },
    "sponsor": {
        "chat": [
            "request.approval",
            "request.scope_tradeoff",
            "request.clarification",
            "inform.blocker",
            "inform.risk",
            "inform.decision",
            "escalate.to_sponsor",
        ]
    },
    "ally": {
        "chat": [
            "request.feasibility",
            "request.eta",
            "request.ownership",
            "request.clarification",
            "inform.status_update",
        ]
    },
    "ally_accelerator": {
        "chat": [
            "request.feasibility",
            "request.eta",
            "request.ownership",
            "request.clarification",
            "inform.status_update",
        ]
    },
    "customer_facing_bridge": {
        "chat": [
            "inform.status_update",
            "inform.decision",
            "request.clarification",
            "request.review",
        ]
    },
}

DEFAULT_CHAT_AFFORDANCES = [
    "request.clarification",
    "inform.status_update",
]

HIGH_VALUE_ACTS = {
    "request.feasibility",
    "request.scope_tradeoff",
    "request.approval",
    "request.review",
    "commit.confirm",
    "inform.blocker",
    "inform.risk",
    "escalate.to_sponsor",
}


def build_source_digest(
    scenario_bytes: bytes,
    contract_bytes: bytes,
    semantics_bytes: bytes,
    *,
    spec_parts: list[bytes],
    renderer_version: str = RENDERER_VERSION,
) -> str:
    return stable_digest(
        scenario_bytes,
        contract_bytes,
        semantics_bytes,
        *spec_parts,
        renderer_version,
    )


def build_starter_contract(scenario: dict[str, Any]) -> dict[str, Any]:
    cells: list[dict[str, Any]] = [
        {
            "id": "calendar.accept.available",
            "criticality": "important",
            "priority": 10,
            "selector": {
                "surface": "calendar",
                "incoming_act_id": "meeting.propose",
            },
            "guard": {"context_field": {"field": "available_for_meeting", "equals": True}},
            "why_reachable": "Meeting proposals are a universal TPM interaction surface.",
            "rationale": "Any scenario with coworkers should define how calendars respond to meeting proposals when actors are available.",
        },
        {
            "id": "calendar.decline.unavailable",
            "criticality": "important",
            "priority": 10,
            "selector": {
                "surface": "calendar",
                "incoming_act_id": "meeting.propose",
            },
            "guard": {"context_field": {"field": "available_for_meeting", "equals": False}},
            "why_reachable": "Meeting proposals can also land when actors are unavailable.",
            "rationale": "The contract should explicitly handle the unavailable meeting path instead of letting it emerge as an implicit gap.",
        },
    ]
    seen_ids = {cell["id"] for cell in cells}
    for actor in scenario.get("world", {}).get("actors", []):
        actor_id = actor["id"]
        template = (
            actor.get("coordination_template")
            or actor.get("traits", {}).get("coordination_template")
            or actor.get("traits", {}).get("coordination_template_id")
        )
        chat_acts = ACT_AFFORDANCE_LIBRARY_V1.get(template or "", {}).get("chat", DEFAULT_CHAT_AFFORDANCES)
        for act_id in chat_acts:
            cell_id = f"{actor_id}.{act_id.replace('.', '_')}"
            if cell_id in seen_ids:
                continue
            seen_ids.add(cell_id)
            cells.append(
                {
                    "id": cell_id,
                    "criticality": "critical" if act_id in HIGH_VALUE_ACTS else "important",
                    "priority": 20 if act_id in HIGH_VALUE_ACTS else 10,
                    "selector": {
                        "actor_id": actor_id,
                        "surface": "chat",
                        "incoming_act_id": act_id,
                    },
                    "why_reachable": f"{actor_id} is interactive in this scenario and can receive {act_id} over chat.",
                    "rationale": f"Starter contract compiled from the actor coordination template '{template or 'default'}'.",
                }
            )
    return {"version": COVERAGE_CONTRACT_VERSION, "cells": cells}


def validate_contract(contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    cells = contract.get("cells")
    if not isinstance(cells, list) or not cells:
        errors.append("coverage contract must define a non-empty cells list")
        return errors
    seen_ids: set[str] = set()
    seen_signatures: dict[str, list[str]] = {}
    for cell in cells:
        cell_id = cell.get("id")
        if not cell_id or not isinstance(cell_id, str):
            errors.append("coverage contract cell missing string id")
            continue
        if cell_id in seen_ids:
            errors.append(f"duplicate contract cell id '{cell_id}'")
        seen_ids.add(cell_id)
        selector = cell.get("selector")
        if not isinstance(selector, dict) or not selector:
            errors.append(f"contract cell '{cell_id}' missing selector")
            continue
        unknown_fields = sorted(set(selector) - ALLOWED_SELECTOR_FIELDS)
        if unknown_fields:
            errors.append(f"contract cell '{cell_id}' uses unknown selector fields: {', '.join(unknown_fields)}")
        incoming_act = selector.get("incoming_act_id")
        if incoming_act is not None:
            try:
                require_known_act(str(incoming_act))
            except ValueError as exc:
                errors.append(f"contract cell '{cell_id}' has invalid incoming_act_id: {exc}")
        signature = as_json({"selector": selector, "guard": cell.get("guard")})
        seen_signatures.setdefault(signature, []).append(cell_id)
    for ids in seen_signatures.values():
        if len(ids) > 1:
            errors.append(f"duplicate selector/guard collision across cells: {', '.join(sorted(ids))}")
    return errors


def validate_semantics(contract: dict[str, Any], semantics: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    contract_ids = {cell["id"] for cell in contract.get("cells", []) if isinstance(cell, dict) and cell.get("id")}
    entries = semantics.get("cells")
    if not isinstance(entries, list) or not entries:
        errors.append("coverage semantics must define a non-empty cells list")
        return errors
    seen: set[str] = set()
    for entry in entries:
        cell_id = entry.get("cell_id")
        if not cell_id or not isinstance(cell_id, str):
            errors.append("coverage semantics entry missing cell_id")
            continue
        if cell_id in seen:
            errors.append(f"duplicate semantics entry for '{cell_id}'")
        seen.add(cell_id)
        if cell_id not in contract_ids:
            errors.append(f"orphan semantics entry '{cell_id}' not present in coverage contract")
        envelopes = entry.get("response_envelopes")
        if not isinstance(envelopes, list) or not envelopes:
            errors.append(f"semantics entry '{cell_id}' missing response_envelopes")
            continue
        for envelope in envelopes:
            outgoing_act = envelope.get("outgoing_act_id")
            if outgoing_act is None:
                errors.append(f"semantics entry '{cell_id}' missing outgoing_act_id")
                continue
            try:
                require_known_act(str(outgoing_act))
            except ValueError as exc:
                errors.append(f"semantics entry '{cell_id}' has invalid outgoing_act_id: {exc}")
            variants = envelope.get("renderer_variants")
            if not isinstance(variants, list) or not variants:
                errors.append(f"semantics entry '{cell_id}' envelope '{envelope.get('id', '?')}' missing renderer_variants")
            effects = envelope.get("effects", [])
            if not isinstance(effects, list):
                errors.append(f"semantics entry '{cell_id}' envelope '{envelope.get('id', '?')}' has non-list effects")
                continue
            for effect in effects:
                if not isinstance(effect, dict):
                    errors.append(f"semantics entry '{cell_id}' envelope '{envelope.get('id', '?')}' has non-object effect")
                    continue
                effect_type = effect.get("type")
                if effect_type not in ALLOWED_EFFECT_TYPES:
                    errors.append(
                        f"semantics entry '{cell_id}' envelope '{envelope.get('id', '?')}' has unsupported effect type: {effect_type}"
                    )
    missing = sorted(contract_ids - seen)
    for cell_id in missing:
        errors.append(f"coverage contract cell '{cell_id}' missing semantics")
    return errors


def compile_coverage(
    contract: dict[str, Any],
    semantics: dict[str, Any],
    *,
    compiled_from_digest: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    contract_errors = validate_contract(contract)
    semantics_errors = validate_semantics(contract, semantics)
    errors = contract_errors + semantics_errors
    semantics_by_cell = {
        entry["cell_id"]: entry
        for entry in semantics.get("cells", [])
        if isinstance(entry, dict) and isinstance(entry.get("cell_id"), str)
    }
    reachable_cells: list[dict[str, Any]] = []
    families: list[dict[str, Any]] = []
    renderers_by_id: dict[str, list[str]] = {}
    collisions: list[list[str]] = []
    signature_index: dict[str, list[str]] = {}
    for cell in contract.get("cells", []):
        cell_id = cell["id"]
        reachable_id = cell.get("reachable_cell_id", cell_id)
        family_id = cell.get("compiled_family_id", cell_id)
        selector = deepcopy(cell["selector"])
        guard = deepcopy(cell.get("guard"))
        reachable = {
            "id": reachable_id,
            "criticality": cell.get("criticality", "important"),
            "reachable": True,
            "selector": selector,
        }
        if guard is not None:
            reachable["guard"] = guard
        reachable_cells.append(reachable)
        signature = as_json({"selector": selector, "guard": guard})
        signature_index.setdefault(signature, []).append(str(family_id))
        entry = semantics_by_cell.get(cell_id)
        compiled_envelopes: list[dict[str, Any]] = []
        if entry is not None:
            for envelope in entry.get("response_envelopes", []):
                variants = deepcopy(envelope.get("renderer_variants", []))
                renderer_id = envelope.get("renderer_id") or None
                if variants:
                    renderer_id = renderer_id or str(envelope.get("id", cell_id))
                    renderers_by_id[str(renderer_id)] = [str(item) for item in variants]
                compiled = {
                    "id": envelope.get("id", cell_id),
                    "weight": float(envelope.get("weight", 1.0)),
                    "outgoing_act_id": envelope.get("outgoing_act_id"),
                    "outgoing_slots": deepcopy(envelope.get("outgoing_slots", {})),
                    "surface_facts": deepcopy(envelope.get("surface_facts", [])),
                    "belief_signals": deepcopy(envelope.get("belief_signals", [])),
                    "effects": deepcopy(envelope.get("effects", [])),
                    "renderer_id": renderer_id,
                }
                compiled_envelopes.append(compiled)
        family = {
            "id": family_id,
            "priority": int(cell.get("priority", 10)),
            "criticality": cell.get("criticality", "important"),
            "selector": selector,
            "response_envelopes": compiled_envelopes,
        }
        if guard is not None:
            family["guard"] = guard
        families.append(family)
    for ids in signature_index.values():
        if len(ids) > 1:
            collisions.append(sorted(ids))
    report = {
        "contract_cell_count": len(contract.get("cells", [])),
        "semantic_entry_count": len(semantics.get("cells", [])),
        "compiled_family_count": len(families),
        "missing_semantic_cells": sorted(
            cell["id"]
            for cell in contract.get("cells", [])
            if cell["id"] not in semantics_by_cell
        ),
        "orphan_semantic_cells": sorted(
            entry.get("cell_id")
            for entry in semantics.get("cells", [])
            if entry.get("cell_id") not in {cell["id"] for cell in contract.get("cells", [])}
        ),
        "duplicate_selector_guard_collisions": collisions,
        "renderer_count": len(renderers_by_id),
        "errors": errors,
        "compiled_from_digest": compiled_from_digest,
    }
    compiled = {
        "version": CONTEXT_FAMILY_SCHEMA_VERSION,
        "compiled_from_digest": compiled_from_digest,
        "reachable_cells": reachable_cells,
        "families": families,
        "renderers": {key: renderers_by_id[key] for key in sorted(renderers_by_id)},
    }
    return compiled, report


def extract_contract_and_semantics(compiled_coverage: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    cells_by_id = {cell["id"]: cell for cell in compiled_coverage.get("reachable_cells", []) if isinstance(cell, dict) and cell.get("id")}
    families_by_id = {family["id"]: family for family in compiled_coverage.get("families", []) if isinstance(family, dict) and family.get("id")}
    all_families = [family for family in compiled_coverage.get("families", []) if isinstance(family, dict) and family.get("id")]
    renderers_by_id = {
        str(renderer_id): list(variants)
        for renderer_id, variants in compiled_coverage.get("renderers", {}).items()
        if isinstance(variants, list)
    }
    ordered_ids: list[str] = []
    for item in compiled_coverage.get("reachable_cells", []):
        cell_id = item.get("id")
        if cell_id and cell_id not in ordered_ids:
            ordered_ids.append(cell_id)
    matched_family_ids: set[str] = set()

    contract_cells: list[dict[str, Any]] = []
    semantic_cells: list[dict[str, Any]] = []
    for cell_id in ordered_ids:
        cell = deepcopy(cells_by_id.get(cell_id, {}))
        family = deepcopy(families_by_id.get(cell_id, {}))
        if not family:
            matched = _best_family_for_reachable_cell(cell, all_families)
            if matched is not None:
                family = deepcopy(matched)
        if family.get("id"):
            matched_family_ids.add(str(family["id"]))
        selector = deepcopy(family.get("selector", cell.get("selector", {})))
        guard = deepcopy(family.get("guard", cell.get("guard")))
        contract_cell = {
            "id": cell_id,
            "criticality": family.get("criticality", cell.get("criticality", "important")),
            "priority": int(family.get("priority", 10)),
            "selector": selector,
            "why_reachable": cell.get("why_reachable", "Extracted from legacy compiled coverage."),
            "rationale": cell.get("rationale", "Migrated from existing npc_coverage.json so compiled runtime behavior stays stable."),
        }
        if cell_id != cell.get("id", cell_id):
            contract_cell["reachable_cell_id"] = cell.get("id")
        if family.get("id") and family["id"] != cell_id:
            contract_cell["compiled_family_id"] = family["id"]
        if guard is not None:
            contract_cell["guard"] = guard
        contract_cells.append(contract_cell)

        response_envelopes: list[dict[str, Any]] = []
        for envelope in family.get("response_envelopes", []):
            variants: list[str] = []
            renderer_id = envelope.get("renderer_id")
            if renderer_id and renderer_id in renderers_by_id:
                variants = deepcopy(renderers_by_id[renderer_id])
            response_envelopes.append(
                {
                    "id": envelope.get("id", cell_id),
                    "weight": float(envelope.get("weight", 1.0)),
                    "outgoing_act_id": envelope.get("outgoing_act_id"),
                    "outgoing_slots": deepcopy(envelope.get("outgoing_slots", {})),
                    "surface_facts": deepcopy(envelope.get("surface_facts", [])),
                    "belief_signals": deepcopy(envelope.get("belief_signals", [])),
                    "effects": deepcopy(envelope.get("effects", [])),
                    "renderer_id": renderer_id,
                    "renderer_variants": variants,
                }
            )
        if not response_envelopes:
            response_envelopes = _fallback_response_envelopes(cell_id, selector)
        semantic_cells.append({"cell_id": cell_id, "response_envelopes": response_envelopes})
    for family_id, family in families_by_id.items():
        if family_id in matched_family_ids:
            continue
        selector = deepcopy(family.get("selector", {}))
        guard = deepcopy(family.get("guard"))
        contract_cells.append(
            {
                "id": family_id,
                "criticality": family.get("criticality", "important"),
                "priority": int(family.get("priority", 10)),
                "selector": selector,
                "why_reachable": "Extracted from legacy compiled coverage family with no explicit reachable cell entry.",
                "rationale": "Migrated from existing npc_coverage.json so compiled runtime behavior stays stable.",
                "compiled_family_id": family_id,
                **({"guard": guard} if guard is not None else {}),
            }
        )
        response_envelopes: list[dict[str, Any]] = []
        for envelope in family.get("response_envelopes", []):
            renderer_id = envelope.get("renderer_id")
            variants: list[str] = deepcopy(renderers_by_id.get(str(renderer_id), [])) if renderer_id else []
            response_envelopes.append(
                {
                    "id": envelope.get("id", family_id),
                    "weight": float(envelope.get("weight", 1.0)),
                    "outgoing_act_id": envelope.get("outgoing_act_id"),
                    "outgoing_slots": deepcopy(envelope.get("outgoing_slots", {})),
                    "surface_facts": deepcopy(envelope.get("surface_facts", [])),
                    "belief_signals": deepcopy(envelope.get("belief_signals", [])),
                    "effects": deepcopy(envelope.get("effects", [])),
                    "renderer_id": renderer_id,
                    "renderer_variants": variants,
                }
            )
        semantic_cells.append({"cell_id": family_id, "response_envelopes": response_envelopes})
    return (
        {"version": COVERAGE_CONTRACT_VERSION, "cells": contract_cells},
        {"version": COVERAGE_SEMANTICS_VERSION, "cells": semantic_cells},
    )


def extend_contract_with_gaps(contract: dict[str, Any], gaps: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    existing_ids = {cell["id"] for cell in contract.get("cells", []) if isinstance(cell, dict) and cell.get("id")}
    updated = deepcopy(contract)
    added: list[dict[str, Any]] = []
    for index, gap in enumerate(gaps, start=1):
        context = gap.get("context", gap)
        selector = {
            key: context[key]
            for key in ALLOWED_SELECTOR_FIELDS
            if key in context
        }
        if not selector:
            continue
        base_id = _gap_cell_id(context, index)
        cell_id = base_id
        suffix = 1
        while cell_id in existing_ids:
            suffix += 1
            cell_id = f"{base_id}_{suffix}"
        cell = {
            "id": cell_id,
            "criticality": "critical" if context.get("incoming_act_id") in HIGH_VALUE_ACTS else "important",
            "priority": 20 if context.get("incoming_act_id") in HIGH_VALUE_ACTS else 10,
            "selector": selector,
            "why_reachable": "Added from an observed coverage gap during closure or live-agent execution.",
            "rationale": "Deterministically promoted from a concrete canonical context that the benchmark reached.",
        }
        updated.setdefault("cells", []).append(cell)
        existing_ids.add(cell_id)
        added.append(cell)
    return updated, added


def _gap_cell_id(context: dict[str, Any], index: int) -> str:
    actor = str(context.get("actor_id", "actor")).replace(".", "_")
    act = str(context.get("incoming_act_id", "act")).replace(".", "_")
    timing = str(context.get("timing_band", "timing")).replace(".", "_")
    return f"{actor}.{act}.{timing}.gap{index}"


def _fallback_response_envelopes(cell_id: str, selector: dict[str, Any]) -> list[dict[str, Any]]:
    incoming_act = str(selector.get("incoming_act_id", "ack.deferred"))
    renderer_id = f"{cell_id}.fallback"
    if incoming_act == "request.feasibility":
        return [
            {
                "id": f"{cell_id}.fallback",
                "weight": 1.0,
                "outgoing_act_id": "inform.decision",
                "outgoing_slots": {},
                "surface_facts": [],
                "belief_signals": [],
                "effects": [],
                "renderer_id": renderer_id,
                "renderer_variants": ["The descoped path is the only feasible option from here."],
            }
        ]
    if incoming_act == "request.eta":
        return [
            {
                "id": f"{cell_id}.fallback",
                "weight": 1.0,
                "outgoing_act_id": "commit.revise",
                "outgoing_slots": {},
                "surface_facts": [],
                "belief_signals": [],
                "effects": [],
                "renderer_id": renderer_id,
                "renderer_variants": ["I can offer a revised ETA once we hold the narrowed path steady."],
            }
        ]
    if incoming_act in {"request.approval", "request.review"}:
        return [
            {
                "id": f"{cell_id}.fallback",
                "weight": 1.0,
                "outgoing_act_id": "approve.defer",
                "outgoing_slots": {},
                "surface_facts": [],
                "belief_signals": [],
                "effects": [],
                "renderer_id": renderer_id,
                "renderer_variants": ["I need the concrete narrowed plan before I can approve this."],
            }
        ]
    if incoming_act in {"inform.blocker", "inform.risk", "inform.status_update", "inform.decision"}:
        return [
            {
                "id": f"{cell_id}.fallback",
                "weight": 1.0,
                "outgoing_act_id": "ack.received",
                "outgoing_slots": {},
                "surface_facts": [],
                "belief_signals": [],
                "effects": [],
                "renderer_id": renderer_id,
                "renderer_variants": ["Understood. Keep me posted on the next concrete move."],
            }
        ]
    return [
        {
            "id": f"{cell_id}.fallback",
            "weight": 1.0,
            "outgoing_act_id": "ack.deferred",
            "outgoing_slots": {},
            "surface_facts": [],
            "belief_signals": [],
            "effects": [],
            "renderer_id": renderer_id,
            "renderer_variants": ["I need a clearer next step before I can respond decisively."],
        }
    ]


def _best_family_for_reachable_cell(cell: dict[str, Any], families: list[dict[str, Any]]) -> dict[str, Any] | None:
    selector = cell.get("selector", {})
    guard = cell.get("guard")
    matches: list[tuple[int, int, str, dict[str, Any]]] = []
    for family in families:
        family_selector = family.get("selector", {})
        if not all(selector.get(key) == value for key, value in family_selector.items()):
            continue
        if family.get("guard") != guard:
            continue
        specificity = len(family_selector) + (1 if family.get("guard") else 0)
        matches.append((specificity, int(family.get("priority", 0)), str(family["id"]), family))
    if not matches:
        return None
    matches.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return matches[0][3]
