from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from tpm_sim.common import stable_digest
from tpm_sim.coverage_artifacts import build_source_digest, compile_coverage
from tpm_sim.specs import (
    ACT_TAXONOMY_VERSION,
    CONTEXT_FAMILY_SCHEMA_VERSION,
    EVAL_DSL_VERSION,
    PREDICATE_DSL_VERSION,
    RENDERER_VERSION,
)
from tpm_sim.storage import StateStore


SPEC_FILES = [
    Path(__file__).resolve().parents[1] / "docs" / "specs" / "ACT_TAXONOMY_v1.md",
    Path(__file__).resolve().parents[1] / "docs" / "specs" / "PREDICATE_DSL_v1.md",
    Path(__file__).resolve().parents[1] / "docs" / "specs" / "CONTEXT_FAMILY_SCHEMA_v1.json",
    Path(__file__).resolve().parents[1] / "docs" / "specs" / "EVAL_DSL_v1.md",
]


def available_scenarios() -> list[str]:
    package = resources.files("tpm_sim.scenarios")
    return sorted(path.name for path in package.iterdir() if path.is_dir())


def _scenario_dir(scenario_id: str):
    root = resources.files("tpm_sim.scenarios").joinpath(scenario_id)
    if not root.is_dir():
        names = ", ".join(available_scenarios())
        raise FileNotFoundError(f"Unknown scenario '{scenario_id}'. Available scenarios: {names}")
    return root


def load_scenario_bundle(scenario_id: str) -> dict[str, Any]:
    root = _scenario_dir(scenario_id)
    return load_bundle_from_paths(
        root.joinpath("scenario.json"),
        root.joinpath("npc_coverage.json"),
        contract_path=root.joinpath("coverage_contract.json"),
        semantics_path=root.joinpath("coverage_semantics.json"),
        closure_report_path=root.joinpath("closure_report.json"),
    )


def load_bundle_from_paths(
    scenario_path: str | Path,
    coverage_path: str | Path | None = None,
    *,
    contract_path: str | Path | None = None,
    semantics_path: str | Path | None = None,
    closure_report_path: str | Path | None = None,
) -> dict[str, Any]:
    scenario_bytes = Path(scenario_path).read_bytes()
    scenario = json.loads(scenario_bytes)
    contract_file = Path(contract_path) if contract_path else Path(scenario_path).with_name("coverage_contract.json")
    semantics_file = Path(semantics_path) if semantics_path else Path(scenario_path).with_name("coverage_semantics.json")
    spec_parts = [path.read_bytes() for path in SPEC_FILES]

    if contract_file.exists() and semantics_file.exists():
        contract_bytes = contract_file.read_bytes()
        semantics_bytes = semantics_file.read_bytes()
        contract = json.loads(contract_bytes)
        semantics = json.loads(semantics_bytes)
        digest = build_source_digest(
            scenario_bytes,
            contract_bytes,
            semantics_bytes,
            spec_parts=spec_parts,
            renderer_version=RENDERER_VERSION,
        )
        if coverage_path:
            coverage_bytes = Path(coverage_path).read_bytes()
            coverage = json.loads(coverage_bytes)
        else:
            coverage, _ = compile_coverage(contract, semantics, compiled_from_digest=digest)
            coverage_bytes = json.dumps(coverage, sort_keys=True).encode("utf-8")
        compiled_from = coverage.get("compiled_from_digest")
        if compiled_from != digest:
            raise RuntimeError(
                f"Compiled coverage is stale for {scenario_path}. "
                f"Expected compiled_from_digest={digest}, found {compiled_from!r}."
            )
        return {
            "scenario": scenario,
            "coverage": coverage,
            "coverage_contract": contract,
            "coverage_semantics": semantics,
            "scenario_digest": digest,
            "compiled_coverage_digest": compiled_from,
            "closure_status": _load_closure_status(closure_report_path or Path(scenario_path).with_name("closure_report.json")),
            "scenario_bytes": scenario_bytes,
            "contract_bytes": contract_bytes,
            "semantics_bytes": semantics_bytes,
            "coverage_bytes": coverage_bytes,
        }

    if coverage_path is None:
        raise FileNotFoundError(f"{scenario_path} is missing coverage_contract.json / coverage_semantics.json and no npc_coverage.json was provided.")
    coverage_bytes = Path(coverage_path).read_bytes()
    coverage = json.loads(coverage_bytes)
    digest = stable_digest(
        scenario_bytes,
        coverage_bytes,
        *spec_parts,
        RENDERER_VERSION,
    )
    return {
        "scenario": scenario,
        "coverage": coverage,
        "scenario_digest": digest,
        "compiled_coverage_digest": coverage.get("compiled_from_digest", digest),
        "closure_status": {"status": "legacy_untracked", "passed": False},
        "scenario_bytes": scenario_bytes,
        "coverage_bytes": coverage_bytes,
    }


def load_bundle_from_store(store: StateStore) -> dict[str, Any]:
    scenario_json = store.get_meta("scenario_json")
    coverage_json = store.get_meta("npc_coverage_json")
    if scenario_json is None or coverage_json is None:
        raise RuntimeError("Run database does not contain a frozen scenario snapshot.")
    contract_json = store.get_meta("coverage_contract_json")
    semantics_json = store.get_meta("coverage_semantics_json")
    closure_status_json = store.get_meta("closure_status_json")
    return {
        "scenario": json.loads(scenario_json),
        "coverage": json.loads(coverage_json),
        "scenario_digest": store.get_meta("scenario_digest", ""),
        "compiled_coverage_digest": store.get_meta("compiled_coverage_digest", store.get_meta("scenario_digest", "")),
        "coverage_contract": json.loads(contract_json) if contract_json else None,
        "coverage_semantics": json.loads(semantics_json) if semantics_json else None,
        "closure_status": json.loads(closure_status_json) if closure_status_json else {"status": "unknown", "passed": False},
    }


def seed_store(store: StateStore, bundle: dict[str, Any], seed: int, coverage_enforcement: str = "strict") -> None:
    scenario = bundle["scenario"]
    coverage = bundle["coverage"]
    contract = bundle.get("coverage_contract")
    semantics = bundle.get("coverage_semantics")
    closure_status = bundle.get("closure_status", {"status": "unknown", "passed": False})
    world = scenario["world"]

    store.reset()
    with store.transaction():
        store.set_meta("scenario_id", scenario["id"])
        store.set_meta("scenario_name", scenario["name"])
        store.set_meta("scenario_digest", bundle["scenario_digest"])
        store.set_meta("compiled_coverage_digest", bundle.get("compiled_coverage_digest", bundle["scenario_digest"]))
        store.set_meta("scenario_json", json.dumps(scenario, sort_keys=True))
        if contract is not None:
            store.set_meta("coverage_contract_json", json.dumps(contract, sort_keys=True))
        if semantics is not None:
            store.set_meta("coverage_semantics_json", json.dumps(semantics, sort_keys=True))
        store.set_meta("npc_coverage_json", json.dumps(coverage, sort_keys=True))
        store.set_meta("closure_status_json", json.dumps(closure_status, sort_keys=True))
        store.set_meta("timezone", scenario.get("timezone", "America/Los_Angeles"))
        store.set_meta("seed", str(seed))
        store.set_meta("coverage_enforcement", coverage_enforcement)
        store.set_meta("act_taxonomy_version", ACT_TAXONOMY_VERSION)
        store.set_meta("predicate_dsl_version", PREDICATE_DSL_VERSION)
        store.set_meta("context_family_schema_version", CONTEXT_FAMILY_SCHEMA_VERSION)
        store.set_meta("eval_dsl_version", EVAL_DSL_VERSION)
        store.set_meta("renderer_version", RENDERER_VERSION)
        store.set_meta("current_time", scenario["start_at"])
        store.set_meta("simulation_end", scenario["end_at"])

        store.add_project_state(world["project"])

        for actor in world.get("actors", []):
            store.add_actor(actor)

        for relationship in world.get("relationships", []):
            store.add_relationship(
                relationship["actor_id"],
                relationship["target_actor_id"],
                relationship.get("state", {}),
            )

        for window in world.get("windows", []):
            store.add_window(window)

        for thread in world.get("threads", []):
            store.add_thread(thread)

        for document in world.get("documents", []):
            store.add_document(document)

        for task in world.get("tasks", []):
            store.add_task(task)

        for milestone in world.get("milestones", []):
            store.add_milestone(milestone)

        for dependency in world.get("dependencies", []):
            store.add_dependency(dependency)

        for fact in world.get("facts", []):
            store.add_fact(fact)

        for belief in world.get("beliefs", []):
            store.add_belief(belief)

        for commitment in world.get("commitments", []):
            store.add_commitment(commitment)

        for meeting in world.get("meetings", []):
            store.add_meeting(meeting)

        for message in world.get("messages", []):
            store.add_message(message)

        for pending_event in world.get("pending_events", []):
            store.queue_event(
                due_at=pending_event["due_at"],
                phase_priority=int(pending_event["phase_priority"]),
                event_type=pending_event["type"],
                actor_id=pending_event.get("actor_id"),
                payload=pending_event.get("payload", {}),
            )

        store.log_event(
            scenario["start_at"],
            phase="setup",
            event_type="run.initialized",
            actor_id="system",
            visibility="omniscient",
            summary=f"Run initialized for {scenario['id']}",
            payload={
                "scenario_id": scenario["id"],
                "scenario_digest": bundle["scenario_digest"],
                "compiled_coverage_digest": bundle.get("compiled_coverage_digest", bundle["scenario_digest"]),
                "seed": seed,
                "coverage_enforcement": coverage_enforcement,
                "closure_status": closure_status,
            },
        )


def _load_closure_status(path: str | Path) -> dict[str, Any]:
    closure_path = Path(path)
    if not closure_path.exists():
        return {"status": "not_certified", "passed": False}
    payload = json.loads(closure_path.read_text())
    return {
        "status": payload.get("status", "unknown"),
        "passed": bool(payload.get("passed", False)),
        "path": str(closure_path),
    }
