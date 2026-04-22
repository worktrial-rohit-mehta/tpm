from __future__ import annotations

from copy import deepcopy
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from tpm_sim.agent import AgentRunner, DEFAULT_AGENT_MAX_TURNS, OpenAIResponsesAgentAdapter
from tpm_sim.authoring.briefs import AuthoringBrief, load_brief
from tpm_sim.briefing import (
    build_authoring_briefing,
    build_proposal_status,
    proposal_briefing_paths,
    write_operator_briefing_artifacts,
)
from tpm_sim.authoring.prompts import (
    build_gap_fill_semantics_prompt,
    build_semantics_prompt,
    build_trajectories_prompt,
    build_world_prompt,
)
from tpm_sim.coverage_artifacts import (
    build_source_digest,
    build_starter_contract,
    compile_coverage,
    extract_contract_and_semantics,
    extend_contract_with_gaps,
    merge_contract_with_starter_floor,
    normalize_semantics_artifact,
    validate_contract,
    validate_semantics,
)
from tpm_sim.environment import EnvironmentSession
from tpm_sim.model_client import build_model_client
from tpm_sim.scenario import SPEC_FILES, load_bundle_from_paths, seed_store, validate_runtime_scenario
from tpm_sim.script_dsl import validate_trajectory_payload
from tpm_sim.specs import require_known_act
from tpm_sim.storage import open_store


LEGACY_ROOT_TRAJECTORY_SCENARIOS = {"northstar_launch_week"}


def init_proposal(brief_path: str, proposal_dir: str) -> dict[str, Any]:
    brief = load_brief(brief_path)
    paths = _proposal_paths(proposal_dir)
    for key, path in paths.items():
        if key == "root":
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
    paths["root"].mkdir(parents=True, exist_ok=True)
    brief_target = paths["root"] / "brief.json"
    brief_target.write_text(json.dumps(brief.to_dict(), indent=2, sort_keys=True))
    manifest = {
        "proposal_id": paths["root"].name,
        "scenario_id": brief.scenario_id,
        "brief_path": str(brief_target),
        "status": "initialized",
        "generated_artifacts": {},
    }
    paths["manifest"].write_text(json.dumps(manifest, indent=2, sort_keys=True))
    refreshed = _refresh_operator_briefing(proposal_dir)
    manifest = json.loads(paths["manifest"].read_text())
    manifest["operator_briefing_json_path"] = refreshed["json_path"]
    manifest["operator_briefing_markdown_path"] = refreshed["markdown_path"]
    return manifest


def synthesize_world(
    proposal_dir: str,
    *,
    adapter: str,
    model: str,
    fixtures_root: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    brief = _load_proposal_brief(proposal_dir)
    reference_id = _reference_scenario_id(brief, "scenario.json")
    accepted = _load_accepted_reference(reference_id, "scenario.json") if reference_id else None
    accepted_full = _load_accepted_full_artifact(reference_id, "scenario.json") if reference_id else None
    prompt = build_world_prompt(brief.to_dict(), accepted_reference=accepted)
    prompt["scenario_id"] = brief.scenario_id
    prompt["artifact"] = "scenario.json"
    client = build_model_client(adapter, fixtures_root=fixtures_root, api_key=api_key, base_url=base_url)
    response = client.generate_text(prompt, {"model": model})
    _write_model_response(proposal_dir, "world", response)
    scenario_candidate = _extract_json_payload(
        response.text,
        required_keys=["id", "name", "timezone", "start_at", "end_at", "world", "policy", "evaluation"],
        artifact_name="scenario.json",
    )
    if accepted_full is not None:
        scenario_candidate = _merge_authoring_candidate(scenario_candidate, accepted_full)
        baseline_seeds = accepted_full.get("evaluation", {}).get("official_seeds", [])
        normalized_seeds = _normalize_official_seeds(scenario_candidate.get("evaluation", {}).get("official_seeds", []))
        if baseline_seeds and not normalized_seeds:
            normalized_seeds = list(baseline_seeds)
        scenario_candidate.setdefault("evaluation", {})["official_seeds"] = normalized_seeds
    else:
        scenario_candidate.setdefault("evaluation", {})["official_seeds"] = _normalize_official_seeds(
            scenario_candidate.get("evaluation", {}).get("official_seeds", [])
        )
    scenario_candidate = _normalize_world_candidate(scenario_candidate, accepted_full=accepted_full)
    _require_nested_mapping_keys(
        scenario_candidate,
        "world",
        required_keys=[
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
        artifact_name="scenario.json",
    )
    _require_nested_mapping_keys(
        scenario_candidate,
        "policy",
        required_keys=[
            "timing_bands",
            "default_timing_band",
            "action_costs",
            "project_state_rules",
            "task_transitions",
            "meeting_defaults",
            "meeting_outcomes",
            "external_commitment_requirements",
        ],
        artifact_name="scenario.json",
    )
    _require_nested_mapping_keys(
        scenario_candidate,
        "evaluation",
        required_keys=["official_seeds", "primary_failure_classes", "rubric_lines"],
        artifact_name="scenario.json",
    )
    scenario_errors = validate_runtime_scenario(scenario_candidate)
    if scenario_errors:
        raise RuntimeError(f"scenario.json failed validation: {'; '.join(scenario_errors)}")
    paths = _proposal_paths(proposal_dir)
    paths["scenario"].write_text(json.dumps(scenario_candidate, indent=2, sort_keys=True))
    _update_manifest(proposal_dir, status="world_synthesized", generated_artifacts={"scenario": str(paths["scenario"])})
    return _with_operator_briefing(
        {"scenario_path": str(paths["scenario"]), "latency_ms": response.latency_ms},
        proposal_dir,
    )


def compile_contract(proposal_dir: str) -> dict[str, Any]:
    brief = _load_proposal_brief(proposal_dir)
    paths = _proposal_paths(proposal_dir)
    scenario_candidate = json.loads(paths["scenario"].read_text())
    reference_id = _reference_scenario_id(brief, "coverage_contract.json")
    contract: dict[str, Any]
    extracted_semantics: dict[str, Any] | None = None

    if reference_id:
        accepted_contract = _load_accepted_full_artifact(reference_id, "coverage_contract.json")
        if accepted_contract is not None:
            contract = accepted_contract
        else:
            accepted_coverage = _load_accepted_full_artifact(reference_id, "npc_coverage.json")
            if accepted_coverage is not None:
                contract, extracted_semantics = extract_contract_and_semantics(accepted_coverage)
            else:
                contract = build_starter_contract(scenario_candidate)
    else:
        contract = build_starter_contract(scenario_candidate)
    contract, starter_floor_added = merge_contract_with_starter_floor(contract, scenario_candidate)

    errors = validate_contract(contract)
    if errors:
        raise RuntimeError(f"coverage_contract.json failed validation: {'; '.join(errors)}")
    paths["contract"].write_text(json.dumps(contract, indent=2, sort_keys=True))
    if extracted_semantics is not None and not paths["semantics"].exists():
        paths["semantics"].write_text(json.dumps(extracted_semantics, indent=2, sort_keys=True))
    _update_manifest(
        proposal_dir,
        status="contract_compiled",
        generated_artifacts={"coverage_contract": str(paths["contract"])},
    )
    return _with_operator_briefing(
        {
            "coverage_contract_path": str(paths["contract"]),
            "cell_count": len(contract.get("cells", [])),
            "starter_floor_cells_added": len(starter_floor_added),
        },
        proposal_dir,
    )


def synthesize_semantics(
    proposal_dir: str,
    *,
    adapter: str,
    model: str,
    fixtures_root: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    brief = _load_proposal_brief(proposal_dir)
    paths = _proposal_paths(proposal_dir)
    scenario_candidate = json.loads(paths["scenario"].read_text())
    coverage_contract = json.loads(paths["contract"].read_text())
    reference_id = _reference_scenario_id(brief, "coverage_semantics.json")
    accepted = _load_accepted_reference(reference_id, "coverage_semantics.json") if reference_id else None
    accepted_full = _load_accepted_full_artifact(reference_id, "coverage_semantics.json") if reference_id else None
    if accepted is None and reference_id:
        legacy_coverage = _load_accepted_full_artifact(reference_id, "npc_coverage.json")
        if legacy_coverage is not None:
            _, accepted_full = extract_contract_and_semantics(legacy_coverage)
            accepted = _summarize_semantics(accepted_full)
    prompt = build_semantics_prompt(brief.to_dict(), scenario_candidate, coverage_contract, accepted_reference=accepted)
    prompt["scenario_id"] = brief.scenario_id
    prompt["artifact"] = "coverage_semantics.json"
    client = build_model_client(adapter, fixtures_root=fixtures_root, api_key=api_key, base_url=base_url)
    response = client.generate_text(prompt, {"model": model})
    _write_model_response(proposal_dir, "semantics", response)
    semantics_candidate = _extract_json_payload(
        response.text,
        required_keys=["version", "cells"],
        artifact_name="coverage_semantics.json",
    )
    semantics_candidate = _normalize_semantics_candidate(
        semantics_candidate,
        coverage_contract,
        accepted_full=accepted_full,
    )
    semantics_candidate = normalize_semantics_artifact(semantics_candidate)
    errors = validate_semantics(coverage_contract, semantics_candidate)
    if errors:
        raise RuntimeError(f"coverage_semantics.json failed validation: {'; '.join(errors)}")
    paths["semantics"].write_text(json.dumps(semantics_candidate, indent=2, sort_keys=True))
    _update_manifest(
        proposal_dir,
        status="semantics_synthesized",
        generated_artifacts={"coverage_semantics": str(paths["semantics"])},
    )
    return _with_operator_briefing(
        {
            "coverage_semantics_path": str(paths["semantics"]),
            "latency_ms": response.latency_ms,
            "cell_count": len(semantics_candidate.get("cells", [])),
        },
        proposal_dir,
    )


def compile_coverage_artifact(proposal_dir: str) -> dict[str, Any]:
    paths = _proposal_paths(proposal_dir)
    scenario_bytes = paths["scenario"].read_bytes()
    contract_bytes = paths["contract"].read_bytes()
    semantics_bytes = paths["semantics"].read_bytes()
    contract = json.loads(contract_bytes)
    semantics = normalize_semantics_artifact(json.loads(semantics_bytes))
    normalized_semantics_text = json.dumps(semantics, indent=2, sort_keys=True)
    normalized_semantics_bytes = normalized_semantics_text.encode("utf-8")
    if normalized_semantics_bytes != semantics_bytes:
        paths["semantics"].write_text(normalized_semantics_text)
        semantics_bytes = normalized_semantics_bytes
    source_digest = _source_digest_for_bytes(scenario_bytes, contract_bytes, semantics_bytes)
    compiled, report = compile_coverage(contract, semantics, compiled_from_digest=source_digest)
    paths["coverage"].write_text(json.dumps(compiled, indent=2, sort_keys=True))
    paths["coverage_compile_report"].write_text(json.dumps(report, indent=2, sort_keys=True))
    _update_manifest(
        proposal_dir,
        status="coverage_compiled",
        generated_artifacts={
            "npc_coverage": str(paths["coverage"]),
            "coverage_compile_report": str(paths["coverage_compile_report"]),
        },
    )
    return _with_operator_briefing(
        {
            "coverage_path": str(paths["coverage"]),
            "compile_report_path": str(paths["coverage_compile_report"]),
            "report": report,
        },
        proposal_dir,
    )


def synthesize_coverage(
    proposal_dir: str,
    *,
    adapter: str,
    model: str,
    fixtures_root: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    return synthesize_semantics(
        proposal_dir,
        adapter=adapter,
        model=model,
        fixtures_root=fixtures_root,
        api_key=api_key,
        base_url=base_url,
    )


def synthesize_trajectories(
    proposal_dir: str,
    *,
    adapter: str,
    model: str,
    fixtures_root: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    brief = _load_proposal_brief(proposal_dir)
    paths = _proposal_paths(proposal_dir)
    scenario_candidate = json.loads(paths["scenario"].read_text())
    reference_id = _reference_scenario_id(brief, "trajectories")
    accepted = _load_accepted_trajectory_reference(reference_id) if reference_id else {}
    accepted_full = _load_accepted_full_trajectories(reference_id) if reference_id else {}
    prompt = build_trajectories_prompt(brief.to_dict(), scenario_candidate, accepted_reference=accepted or None)
    prompt["scenario_id"] = brief.scenario_id
    prompt["artifact"] = "trajectories.json"
    client = build_model_client(adapter, fixtures_root=fixtures_root, api_key=api_key, base_url=base_url)
    response = client.generate_text(prompt, {"model": model})
    _write_model_response(proposal_dir, "trajectories", response)
    payload = _extract_json_payload(response.text, artifact_name="trajectories.json")
    trajectory_errors = validate_trajectory_payload(payload, scenario=scenario_candidate)
    if trajectory_errors:
        preview = "; ".join(trajectory_errors[:3])
        suffix = "" if len(trajectory_errors) <= 3 else f"; +{len(trajectory_errors) - 3} more"
        raise RuntimeError(f"Generated trajectories failed syntax validation: {preview}{suffix}")
    if accepted_full:
        merged_payload = dict(accepted_full)
        for name, content in payload.items():
            if not (isinstance(name, str) and isinstance(content, str) and name.endswith(".tpm")):
                merged_payload[name] = content
                continue
            target_name = name if name not in merged_payload else f"generated__{name}"
            merged_payload[target_name] = content
        payload = merged_payload
    paths["trajectories"].mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for name, content in payload.items():
        target = paths["trajectories"] / name
        target.write_text(content)
        written.append(str(target))
    _update_manifest(proposal_dir, status="trajectories_synthesized", generated_artifacts={"trajectories": written})
    return _with_operator_briefing(
        {"trajectories": written, "latency_ms": response.latency_ms},
        proposal_dir,
    )


def validate_proposal(proposal_dir: str, *, smoke_seed: int = 11) -> dict[str, Any]:
    paths = _proposal_paths(proposal_dir)
    brief = _load_proposal_brief(proposal_dir)
    errors: list[str] = []
    scenario_validation_errors: list[str] = []
    for key, label in (
        ("scenario", "candidate scenario.json"),
        ("contract", "candidate coverage_contract.json"),
        ("semantics", "candidate coverage_semantics.json"),
    ):
        if not paths[key].exists():
            errors.append(f"{label} missing")
    if errors:
        report = {"valid": False, "errors": errors}
        paths["validation"].write_text(json.dumps(report, indent=2, sort_keys=True))
        _update_manifest(proposal_dir, status="validation_failed")
        return _with_operator_briefing(report, proposal_dir)

    scenario_candidate = json.loads(paths["scenario"].read_text())
    scenario_validation_errors = validate_runtime_scenario(scenario_candidate)
    if scenario_validation_errors:
        errors.append("scenario runtime validation failed")

    compile_result = compile_coverage_artifact(proposal_dir)
    compile_report = compile_result["report"]
    if compile_report["errors"]:
        errors.extend(compile_report["errors"])
    if compile_report["missing_semantic_cells"]:
        errors.append("contract cells are missing semantics")
    if compile_report["orphan_semantic_cells"]:
        errors.append("orphan semantic cells detected")

    coverage_report = None
    smoke_results: list[dict[str, Any]] = []
    trajectory_syntax_errors: list[str] = []
    bundle = None
    if not scenario_validation_errors:
        try:
            bundle = load_bundle_from_paths(
                paths["scenario"],
                paths["coverage"],
                contract_path=paths["contract"],
                semantics_path=paths["semantics"],
            )
        except Exception as exc:
            errors.append(f"bundle load failed: {exc}")

    if bundle is not None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "proposal.sqlite")
            store = open_store(db_path)
            try:
                seed_store(store, bundle, smoke_seed, coverage_enforcement="strict")
                from tpm_sim.engine import SimulationEngine
                from tpm_sim.evaluator import Evaluator

                engine = SimulationEngine(store, bundle)
                evaluator = Evaluator(engine)
                session = EnvironmentSession(db_path, engine, evaluator)
            except Exception:
                store.close()
                raise
            try:
                coverage_report = session.coverage_report()
            finally:
                session.close()

        if coverage_report["critical_uncovered"] > 0:
            errors.append("critical coverage cells remain uncovered")

        executable_scripts = {
            path.name: path.read_text()
            for path in dict.fromkeys(_smoke_scripts(paths["trajectories"]) + _closure_scripts(paths["trajectories"]))
        }
        trajectory_syntax_errors = validate_trajectory_payload(
            executable_scripts,
            scenario=bundle["scenario"],
            require_smoke_like=False,
        )
        if trajectory_syntax_errors:
            errors.append("trajectory syntax validation failed")

        scripts = _smoke_scripts(paths["trajectories"])
        if trajectory_syntax_errors:
            smoke_results = []
        elif not scripts:
            errors.append("no smoke trajectories available")
        else:
            for script in scripts[:2]:
                smoke_results.append(_run_candidate_script(bundle, script, smoke_seed))

        if any(item["error"] for item in smoke_results):
            errors.append("smoke trajectory execution failed")

    report = {
        "valid": not errors,
        "scenario_id": brief.scenario_id,
        "bundle_digest": bundle["scenario_digest"] if bundle is not None else None,
        "compiled_coverage_digest": bundle.get("compiled_coverage_digest") if bundle is not None else None,
        "coverage_report": coverage_report,
        "compile_report": compile_report,
        "scenario_validation_errors": scenario_validation_errors,
        "smoke_results": smoke_results,
        "trajectory_syntax_errors": trajectory_syntax_errors,
        "errors": errors,
    }
    paths["validation"].write_text(json.dumps(report, indent=2, sort_keys=True))
    paths["review_summary"].write_text(_render_validation_summary(report))
    _update_manifest(proposal_dir, status="validated" if report["valid"] else "validation_failed")
    return _with_operator_briefing(report, proposal_dir)


def run_closure_suite(
    proposal_dir: str,
    *,
    adapter: str | None = None,
    model: str | None = None,
    fixtures_root: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    repeats: int = 1,
) -> dict[str, Any]:
    paths = _proposal_paths(proposal_dir)
    validation = json.loads(paths["validation"].read_text()) if paths["validation"].exists() else None
    if not validation or not validation.get("valid"):
        raise RuntimeError("Closure suite requires a successful validation report.")

    raw_scenario = json.loads(paths["scenario"].read_text())
    official_seeds = list(raw_scenario.get("evaluation", {}).get("official_seeds", []))
    scenario_id = raw_scenario.get("id", paths["root"].name)
    if not official_seeds:
        report = {
            "scenario_id": scenario_id,
            "status": "failed_no_seeds",
            "passed": False,
            "deterministic_scripted_suite": [],
            "live_agent_suite": {
                "status": "skipped_no_seeds",
                "runs": [],
                "model": model,
                "repeats": repeats,
            },
            "pass_criteria": {
                "zero_script_errors": False,
                "zero_live_protocol_failures": False,
                "zero_observed_coverage_misses": False,
            },
            "error": "Scenario evaluation.official_seeds must be a non-empty list before closure certification can run.",
        }
        paths["closure_report"].write_text(json.dumps(report, indent=2, sort_keys=True))
        _update_manifest(proposal_dir, status="closure_failed")
        return report

    bundle = load_bundle_from_paths(
        paths["scenario"],
        paths["coverage"],
        contract_path=paths["contract"],
        semantics_path=paths["semantics"],
    )
    scenario = bundle["scenario"]

    scripted_suite: list[dict[str, Any]] = []
    deterministic_ok = True
    for script in _closure_scripts(paths["trajectories"]):
        for seed in official_seeds:
            result = _run_candidate_script(bundle, script, seed)
            result["seed"] = seed
            scripted_suite.append(result)
            if result["error"] is not None:
                deterministic_ok = False

    live_suite: list[dict[str, Any]] = []
    live_status = "skipped_no_model"
    live_ok = True
    if adapter == "openai" and model:
        live_status = "executed"
        client = build_model_client(adapter, fixtures_root=fixtures_root, api_key=api_key, base_url=base_url)
        for seed in official_seeds:
            for run_index in range(repeats):
                with tempfile.TemporaryDirectory() as tmpdir:
                    session = EnvironmentSession.create_from_bundle(
                        str(Path(tmpdir) / "closure.sqlite"),
                        bundle,
                        seed,
                        coverage_enforcement="strict",
                        force=True,
                    )
                    try:
                        adapter_impl = OpenAIResponsesAgentAdapter(client, model=model, temperature=0, top_p=1)
                        record = AgentRunner(adapter_impl, max_turns=DEFAULT_AGENT_MAX_TURNS).run(
                            session,
                            seed=seed,
                            output_dir=str(Path(tmpdir) / "closure_run"),
                            model_name=model,
                        )
                    finally:
                        session.close()
                outcome = {
                    "seed": seed,
                    "repeat": run_index + 1,
                    "protocol_failure": record.protocol_failure,
                    "protocol_failure_reason": record.protocol_failure_reason,
                    "score": record.score,
                }
                live_suite.append(outcome)
                if record.protocol_failure:
                    live_ok = False
        if not live_suite:
            live_ok = False
            live_status = "failed_no_runs"
    elif adapter == "fixture":
        live_status = "fixture_skipped"

    report = {
        "scenario_id": scenario["id"],
        "status": "passed" if deterministic_ok and live_ok else "failed",
        "passed": deterministic_ok and live_ok,
        "deterministic_scripted_suite": scripted_suite,
        "live_agent_suite": {
            "status": live_status,
            "runs": live_suite,
            "model": model,
            "repeats": repeats,
        },
        "pass_criteria": {
            "zero_script_errors": deterministic_ok,
            "zero_live_protocol_failures": live_ok,
            "zero_observed_coverage_misses": all(
                "coverage miss" not in str(item.get("protocol_failure_reason", "")).lower()
                for item in live_suite
            ),
        },
    }
    paths["closure_report"].write_text(json.dumps(report, indent=2, sort_keys=True))
    _update_manifest(proposal_dir, status="closure_passed" if report["passed"] else "closure_failed")
    return _with_operator_briefing(report, proposal_dir)


def diff_proposal(
    proposal_dir: str,
    *,
    scenarios_root: str,
) -> dict[str, Any]:
    paths = _proposal_paths(proposal_dir)
    brief = _load_proposal_brief(proposal_dir)
    current_dir = Path(scenarios_root) / brief.scenario_id
    candidate_scenario = json.loads(paths["scenario"].read_text()) if paths["scenario"].exists() else {}
    candidate_contract = json.loads(paths["contract"].read_text()) if paths["contract"].exists() else {}
    candidate_semantics = json.loads(paths["semantics"].read_text()) if paths["semantics"].exists() else {}
    if not current_dir.exists():
        summary = {
            "scenario_exists": False,
            "summary": f"No accepted scenario exists for {brief.scenario_id}. Proposal is net-new.",
        }
        paths["diff"].write_text(json.dumps(summary, indent=2, sort_keys=True))
        return _with_operator_briefing(summary, proposal_dir)

    current_scenario = json.loads((current_dir / "scenario.json").read_text())
    current_contract = json.loads((current_dir / "coverage_contract.json").read_text())
    current_semantics = json.loads((current_dir / "coverage_semantics.json").read_text())
    diff = {
        "scenario_exists": True,
        "scenario_changes": _json_semantic_diff(current_scenario, candidate_scenario),
        "coverage_contract_changes": _contract_diff(current_contract, candidate_contract),
        "coverage_semantics_changes": _semantics_diff(current_semantics, candidate_semantics),
    }
    paths["diff"].write_text(json.dumps(diff, indent=2, sort_keys=True))
    return _with_operator_briefing(diff, proposal_dir)


def gap_fill_proposal(
    proposal_dir: str,
    *,
    gaps_path: str,
    adapter: str,
    model: str,
    fixtures_root: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    brief = _load_proposal_brief(proposal_dir)
    paths = _proposal_paths(proposal_dir)
    scenario_candidate = json.loads(paths["scenario"].read_text())
    contract = json.loads(paths["contract"].read_text())
    semantics = json.loads(paths["semantics"].read_text())
    gaps = [json.loads(line) for line in Path(gaps_path).read_text().splitlines() if line.strip()]
    updated_contract, added_cells = extend_contract_with_gaps(contract, gaps)
    paths["contract"].write_text(json.dumps(updated_contract, indent=2, sort_keys=True))
    prompt = build_gap_fill_semantics_prompt(brief.to_dict(), scenario_candidate, updated_contract, semantics, gaps)
    prompt["scenario_id"] = brief.scenario_id
    prompt["artifact"] = "coverage_semantics.json"
    client = build_model_client(adapter, fixtures_root=fixtures_root, api_key=api_key, base_url=base_url)
    response = client.generate_text(prompt, {"model": model})
    _write_model_response(proposal_dir, "gap_fill", response)
    updated_semantics = _extract_json_payload(
        response.text,
        required_keys=["version", "cells"],
        artifact_name="coverage_semantics.json",
    )
    errors = validate_semantics(updated_contract, updated_semantics)
    if errors:
        raise RuntimeError(f"Gap-filled coverage_semantics.json failed validation: {'; '.join(errors)}")
    paths["semantics"].write_text(json.dumps(updated_semantics, indent=2, sort_keys=True))
    compile_result = compile_coverage_artifact(proposal_dir)
    _update_manifest(proposal_dir, status="gap_filled")
    return _with_operator_briefing(
        {
            "coverage_contract_path": str(paths["contract"]),
            "coverage_semantics_path": str(paths["semantics"]),
            "coverage_path": str(paths["coverage"]),
            "added_cells": added_cells,
            "compile_report": compile_result["report"],
            "latency_ms": response.latency_ms,
            "gap_count": len(gaps),
        },
        proposal_dir,
    )


def accept_proposal(
    proposal_dir: str,
    *,
    scenarios_root: str,
    examples_root: str | None = None,
) -> dict[str, Any]:
    paths = _proposal_paths(proposal_dir)
    validation = json.loads(paths["validation"].read_text()) if paths["validation"].exists() else None
    closure = json.loads(paths["closure_report"].read_text()) if paths["closure_report"].exists() else None
    if not validation or not validation.get("valid"):
        raise RuntimeError("Proposal cannot be accepted until it has a successful validation report.")
    if not closure or not closure.get("passed"):
        raise RuntimeError("Proposal cannot be accepted until it has a passing closure report.")
    brief = _load_proposal_brief(proposal_dir)
    scenario_dir = Path(scenarios_root) / brief.scenario_id
    scenario_dir.mkdir(parents=True, exist_ok=True)
    refreshed = _refresh_operator_briefing(proposal_dir)
    accepted_briefing = build_authoring_briefing(
        brief,
        scenario=json.loads(paths["scenario"].read_text()),
        source_kind="accepted_scenario",
    )
    shutil.copyfile(paths["scenario"], scenario_dir / "scenario.json")
    shutil.copyfile(paths["contract"], scenario_dir / "coverage_contract.json")
    shutil.copyfile(paths["semantics"], scenario_dir / "coverage_semantics.json")
    shutil.copyfile(paths["coverage"], scenario_dir / "npc_coverage.json")
    shutil.copyfile(paths["validation"], scenario_dir / "validation.json")
    shutil.copyfile(paths["closure_report"], scenario_dir / "closure_report.json")
    accepted_briefing_paths = write_operator_briefing_artifacts(
        accepted_briefing,
        json_path=scenario_dir / "operator_briefing.json",
        markdown_path=scenario_dir / "operator_briefing.md",
    )
    copied_examples: list[str] = []
    if examples_root:
        target_dir = Path(examples_root) / brief.scenario_id
        target_dir.mkdir(parents=True, exist_ok=True)
        for script in sorted(paths["trajectories"].glob("*.tpm")):
            target = target_dir / script.name
            shutil.copyfile(script, target)
            copied_examples.append(str(target))
    _update_manifest(proposal_dir, status="accepted", accepted_scenario_dir=str(scenario_dir))
    return {
        "scenario_dir": str(scenario_dir),
        "copied_examples": copied_examples,
        "proposal_operator_briefing_json_path": refreshed["json_path"],
        "proposal_operator_briefing_markdown_path": refreshed["markdown_path"],
        "operator_briefing_json_path": accepted_briefing_paths["json_path"],
        "operator_briefing_markdown_path": accepted_briefing_paths["markdown_path"],
    }


def _proposal_paths(proposal_dir: str) -> dict[str, Path]:
    root = Path(proposal_dir)
    candidate = root / "candidate"
    reports = root / "reports"
    trajectories = root / "trajectories"
    return {
        "root": root,
        "reports": reports,
        "manifest": root / "manifest.json",
        "scenario": candidate / "scenario.json",
        "contract": candidate / "coverage_contract.json",
        "semantics": candidate / "coverage_semantics.json",
        "coverage": candidate / "npc_coverage.json",
        "trajectories": trajectories,
        "validation": reports / "validation.json",
        "closure_report": reports / "closure_report.json",
        "coverage_compile_report": reports / "coverage_compile_report.json",
        "diff": reports / "diff.json",
        "operator_briefing_json": proposal_briefing_paths(root)["json"],
        "operator_briefing_markdown": proposal_briefing_paths(root)["markdown"],
        "review_summary": reports / "review_summary.md",
    }


def _reference_scenario_id(brief: AuthoringBrief, filename: str) -> str | None:
    own_path = Path("tpm_sim") / "scenarios" / brief.scenario_id / filename
    if filename == "trajectories":
        own_path = _trajectory_reference_directory(brief.scenario_id) or Path("examples") / brief.scenario_id
    if own_path.exists():
        return brief.scenario_id
    return brief.reference_scenario_id


def _load_accepted_reference(scenario_id: str, filename: str) -> dict[str, Any] | None:
    path = Path("tpm_sim") / "scenarios" / scenario_id / filename
    if not path.exists():
        return None
    payload = json.loads(path.read_text())
    if filename == "scenario.json":
        return {
            "top_level_keys": list(payload.keys()),
            "world_keys": list(payload.get("world", {}).keys()),
            "policy_keys": list(payload.get("policy", {}).keys()),
            "evaluation_keys": list(payload.get("evaluation", {}).keys()),
            "actor_count": len(payload.get("world", {}).get("actors", [])),
            "task_count": len(payload.get("world", {}).get("tasks", [])),
            "milestone_count": len(payload.get("world", {}).get("milestones", [])),
            "rubric_line_ids": [item.get("id") for item in payload.get("evaluation", {}).get("rubric_lines", [])[:12]],
        }
    if filename == "coverage_contract.json":
        cells = payload.get("cells", [])
        return {
            "version": payload.get("version"),
            "cell_count": len(cells),
            "sample_cell_ids": [item.get("id") for item in cells[:8]],
            "sample_fields": sorted({key for item in cells[:5] for key in item.keys()}),
        }
    if filename == "coverage_semantics.json":
        return _summarize_semantics(payload)
    if filename == "npc_coverage.json":
        families = payload.get("families", [])
        sample = families[:5]
        return {
            "top_level_keys": list(payload.keys()),
            "reachable_cell_count": len(payload.get("reachable_cells", [])),
            "family_count": len(families),
            "renderer_count": len(payload.get("renderers", [])),
            "sample_family_ids": [item.get("id") for item in sample],
            "sample_family_fields": sorted({key for item in sample for key in item.keys()}),
        }
    return payload


def _summarize_semantics(payload: dict[str, Any]) -> dict[str, Any]:
    cells = payload.get("cells", [])
    sample = cells[:5]
    return {
        "version": payload.get("version"),
        "cell_count": len(cells),
        "sample_cell_ids": [item.get("cell_id") for item in sample],
        "sample_outgoing_acts": sorted(
            {
                envelope.get("outgoing_act_id")
                for item in sample
                for envelope in item.get("response_envelopes", [])
                if envelope.get("outgoing_act_id")
            }
        ),
    }


def _normalize_world_candidate(candidate: dict[str, Any], *, accepted_full: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized = deepcopy(candidate)
    policy = normalized.setdefault("policy", {})
    baseline_policy = (accepted_full or {}).get("policy", {})
    normalized_requirements = _normalize_external_commitment_requirements(
        policy.get("external_commitment_requirements"),
        baseline_policy.get("external_commitment_requirements"),
    )
    if normalized_requirements:
        policy["external_commitment_requirements"] = normalized_requirements
    world = normalized.setdefault("world", {})
    beliefs = world.get("beliefs", [])
    if isinstance(beliefs, list):
        world["beliefs"] = _normalize_initial_beliefs(beliefs, normalized.get("start_at"))
    commitments = world.get("commitments", [])
    if isinstance(commitments, list):
        world["commitments"] = _normalize_initial_commitments(commitments, normalized.get("start_at"))
    meetings = world.get("meetings", [])
    if isinstance(meetings, list):
        world["meetings"] = _normalize_initial_meetings(meetings)
    messages = world.get("messages", [])
    if isinstance(messages, list):
        world["messages"] = _normalize_initial_messages(messages, world.get("threads", []))
    return normalized


def _normalize_external_commitment_requirements(candidate: Any, baseline: Any) -> list[str]:
    normalized = _extract_external_commitment_requirement_ids(candidate)
    if normalized:
        return normalized
    return _extract_external_commitment_requirement_ids(baseline)


def _extract_external_commitment_requirement_ids(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    normalized: list[str] = []
    for item in raw:
        if isinstance(item, str) and item:
            normalized.append(item)
            continue
        if isinstance(item, dict):
            milestone_id = item.get("milestone_id")
            if isinstance(milestone_id, str) and milestone_id:
                normalized.append(milestone_id)
            milestone_ids = item.get("milestone_ids")
            if isinstance(milestone_ids, list):
                normalized.extend(value for value in milestone_ids if isinstance(value, str) and value)
    return normalized


def _normalize_official_seeds(raw_seeds: Any) -> list[int]:
    normalized: list[int] = []
    if not isinstance(raw_seeds, list):
        return normalized
    for item in raw_seeds:
        if isinstance(item, int):
            normalized.append(item)
            continue
        if isinstance(item, str):
            try:
                normalized.append(int(item))
            except ValueError:
                continue
            continue
        if isinstance(item, dict):
            value = item.get("seed") or item.get("value") or item.get("id")
            if isinstance(value, int):
                normalized.append(value)
                continue
            if isinstance(value, str):
                try:
                    normalized.append(int(value))
                except ValueError:
                    continue
    return normalized


def _normalize_initial_beliefs(raw_beliefs: list[Any], default_updated_at: str | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for entry in raw_beliefs:
        if not isinstance(entry, dict):
            continue
        if "belief_key" in entry:
            row = dict(entry)
            row.setdefault("updated_at", default_updated_at)
            row.setdefault("source_ref", row.get("id", "authoring.initial_belief"))
            normalized.append(row)
            continue

        actor_id = entry.get("actor_id")
        bundle_id = entry.get("id", f"authoring.initial_belief.{len(normalized)}")
        nested = entry.get("beliefs", [])
        if not actor_id or not isinstance(nested, list):
            continue
        for index, belief in enumerate(nested, 1):
            if not isinstance(belief, dict):
                continue
            belief_key = belief.get("belief_key") or belief.get("topic")
            if not belief_key:
                continue
            normalized.append(
                {
                    "actor_id": actor_id,
                    "belief_key": belief_key,
                    "belief_value": belief.get("belief_value", belief.get("value")),
                    "confidence": belief.get("confidence", 0.5),
                    "freshness_window_min": belief.get("freshness_window_min", 240),
                    "updated_at": belief.get("updated_at", entry.get("updated_at", default_updated_at)),
                    "source_ref": belief.get("source_ref", f"{bundle_id}#{index}"),
                    "metadata": {
                        "generated_from_bundle_id": bundle_id,
                        **belief.get("metadata", {}),
                    },
                }
            )
    return normalized


def _normalize_initial_commitments(raw_commitments: list[Any], default_updated_at: str | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for entry in raw_commitments:
        if not isinstance(entry, dict):
            continue
        if "owner_id" in entry and "subject" in entry:
            row = dict(entry)
            row.setdefault("last_updated_at", default_updated_at)
            row.setdefault("source_ref", row.get("id", "authoring.initial_commitment"))
            normalized.append(row)
            continue

        owner_id = entry.get("owner_id") or entry.get("owner_actor_id")
        if not owner_id:
            continue
        audience_id = entry.get("counterparty_actor_id") or entry.get("audience_actor_id")
        original_state = entry.get("state", "available_if_requested")
        original_type = entry.get("type", "informal_support")
        effect = entry.get("effect_if_met")
        conditions = entry.get("conditions", [])
        if not isinstance(conditions, list):
            conditions = [conditions]
        status_map = {
            "available_if_requested": "tentative",
            "offered": "tentative",
            "conditional": "tentative",
            "committed": "committed",
        }
        normalized.append(
            {
                "id": entry.get("id", f"authoring.initial_commitment.{len(normalized)}"),
                "owner_id": owner_id,
                "audience_ids": [audience_id] if audience_id else [],
                "subject": original_type,
                "scope": {
                    "effect_if_met": effect,
                    "original_state": original_state,
                },
                "status": status_map.get(original_state, "tentative"),
                "confidence": float(entry.get("confidence", 0.5)),
                "due_at": entry.get("due_at"),
                "ground_truth_feasibility": float(entry.get("ground_truth_feasibility", 0.5)),
                "perceived_feasibility": float(entry.get("perceived_feasibility", 0.5)),
                "preconditions": conditions,
                "source_ref": entry.get("source_ref", entry.get("id", "authoring.initial_commitment")),
                "last_updated_at": entry.get("last_updated_at", entry.get("created_at", default_updated_at)),
                "metadata": {
                    "generated_from_authoring": True,
                    "effect_if_met": effect,
                    **entry.get("metadata", {}),
                },
            }
        )
    return normalized


def _normalize_initial_meetings(raw_meetings: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for entry in raw_meetings:
        if not isinstance(entry, dict):
            continue
        if "organizer_id" in entry and "attendee_ids" in entry:
            normalized.append(dict(entry))
            continue
        participants = entry.get("attendee_ids", entry.get("participant_actor_ids", []))
        if not isinstance(participants, list):
            participants = []
        organizer_id = entry.get("organizer_id")
        if not organizer_id:
            organizer_id = "tpm" if "tpm" in participants else (participants[0] if participants else "tpm")
        normalized.append(
            {
                "id": entry["id"],
                "title": entry["title"],
                "organizer_id": organizer_id,
                "start_at": entry["start_at"],
                "end_at": entry["end_at"],
                "status": entry.get("status", "scheduled"),
                "attendee_ids": participants,
                "agenda": entry.get("agenda", entry.get("objective", "")),
                "transcript_doc_id": entry.get("transcript_doc_id"),
                "metadata": deepcopy(entry.get("metadata", {})),
            }
        )
    return normalized


def _normalize_initial_messages(raw_messages: list[Any], threads: list[Any]) -> list[dict[str, Any]]:
    thread_surface = {
        item.get("id"): item.get("surface", "chat")
        for item in threads
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    normalized: list[dict[str, Any]] = []
    for entry in raw_messages:
        if not isinstance(entry, dict):
            continue
        if entry.get("sender_id") and entry.get("created_at") and entry.get("thread_id"):
            normalized.append(dict(entry))
            continue
        thread_id = entry.get("thread_id")
        surface = entry.get("surface") or thread_surface.get(thread_id, "chat")
        sender_id = entry.get("sender_id", entry.get("sender_actor_id"))
        created_at = entry.get("created_at", entry.get("sent_at"))
        if not thread_id or not sender_id or not created_at:
            continue
        normalized.append(
            {
                "thread_id": thread_id,
                "surface": surface,
                "sender_id": sender_id,
                "act_id": entry.get("act_id"),
                "slots": deepcopy(entry.get("slots", {})),
                "body": entry.get("body", ""),
                "created_at": created_at,
                "unread_for_tpm": bool(entry.get("unread_for_tpm", entry.get("sender_actor_id") != "tpm")),
                "metadata": {
                    "tags": deepcopy(entry.get("tags", [])),
                    **deepcopy(entry.get("metadata", {})),
                },
            }
        )
    return normalized


def _normalize_semantics_candidate(
    candidate: dict[str, Any],
    contract: dict[str, Any],
    *,
    accepted_full: dict[str, Any] | None = None,
) -> dict[str, Any]:
    baseline_cells = {
        item.get("cell_id"): item
        for item in (accepted_full or {}).get("cells", [])
        if isinstance(item, dict) and isinstance(item.get("cell_id"), str)
    }
    candidate_cells = {
        item.get("cell_id"): item
        for item in candidate.get("cells", [])
        if isinstance(item, dict) and isinstance(item.get("cell_id"), str)
    }
    normalized_cells: list[dict[str, Any]] = []
    for contract_cell in contract.get("cells", []):
        cell_id = contract_cell["id"]
        baseline_entry = baseline_cells.get(cell_id)
        candidate_entry = candidate_cells.get(cell_id)
        if candidate_entry is None and baseline_entry is not None:
            normalized_cells.append(deepcopy(baseline_entry))
            continue
        if candidate_entry is None and baseline_entry is None:
            normalized_cells.append(
                {
                    "cell_id": cell_id,
                    "response_envelopes": _fallback_semantic_envelopes(contract_cell),
                }
            )
            continue
        normalized_entry = {
            "cell_id": cell_id,
            "response_envelopes": _normalize_semantic_envelopes(
                cell_id,
                candidate_entry.get("response_envelopes", []) if isinstance(candidate_entry, dict) else [],
                baseline_entry.get("response_envelopes", []) if isinstance(baseline_entry, dict) else [],
            ),
        }
        if not normalized_entry["response_envelopes"]:
            normalized_entry["response_envelopes"] = _fallback_semantic_envelopes(contract_cell)
        normalized_cells.append(normalized_entry)
    return {
        "version": candidate.get("version", (accepted_full or {}).get("version", "coverage_semantics_v1")),
        "cells": normalized_cells,
    }


def _normalize_semantic_envelopes(
    cell_id: str,
    candidate_envelopes: list[dict[str, Any]],
    baseline_envelopes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not candidate_envelopes and baseline_envelopes:
        return deepcopy(baseline_envelopes)
    envelope_count = max(len(candidate_envelopes), len(baseline_envelopes))
    normalized: list[dict[str, Any]] = []
    for index in range(envelope_count):
        candidate_env = candidate_envelopes[index] if index < len(candidate_envelopes) and isinstance(candidate_envelopes[index], dict) else {}
        baseline_env = baseline_envelopes[index] if index < len(baseline_envelopes) and isinstance(baseline_envelopes[index], dict) else {}
        outgoing_act_id = _normalize_outgoing_act_id(candidate_env, baseline_env)
        renderer_variants = deepcopy(candidate_env.get("renderer_variants", []))
        if not renderer_variants:
            renderer_variants = deepcopy(baseline_env.get("renderer_variants", []))
        if not renderer_variants and outgoing_act_id:
            renderer_variants = [_default_renderer_variant(outgoing_act_id)]
        normalized.append(
            {
                "id": candidate_env.get("id") or baseline_env.get("id") or f"{cell_id}.{index + 1}",
                "weight": float(candidate_env.get("weight", baseline_env.get("weight", 1.0))),
                "outgoing_act_id": outgoing_act_id,
                "outgoing_slots": deepcopy(
                    candidate_env.get(
                        "outgoing_slots",
                        candidate_env.get("slots", baseline_env.get("outgoing_slots", {})),
                    )
                ),
                "surface_facts": deepcopy(candidate_env.get("surface_facts", baseline_env.get("surface_facts", []))),
                "belief_signals": deepcopy(candidate_env.get("belief_signals", baseline_env.get("belief_signals", []))),
                "effects": _normalize_effects(
                    candidate_env.get("effects", baseline_env.get("effects", [])),
                    baseline_env.get("effects", []),
                ),
                "renderer_id": candidate_env.get("renderer_id") or baseline_env.get("renderer_id") or candidate_env.get("id") or baseline_env.get("id") or f"{cell_id}.{index + 1}",
                "renderer_variants": renderer_variants,
            }
        )
    return normalized


def _normalize_outgoing_act_id(candidate_env: dict[str, Any], baseline_env: dict[str, Any]) -> str | None:
    outgoing_act = candidate_env.get("outgoing_act_id")
    if not outgoing_act:
        outgoing_act = candidate_env.get("outgoing_act")
    if not outgoing_act:
        outgoing_acts = candidate_env.get("outgoing_acts")
        if isinstance(outgoing_acts, list) and outgoing_acts:
            outgoing_act = outgoing_acts[0]
    if isinstance(outgoing_act, str):
        try:
            require_known_act(outgoing_act)
            return outgoing_act
        except ValueError:
            pass
    baseline_outgoing = baseline_env.get("outgoing_act_id")
    if isinstance(baseline_outgoing, str):
        return baseline_outgoing
    return outgoing_act if isinstance(outgoing_act, str) else None


def _default_renderer_variant(outgoing_act_id: str) -> str:
    defaults = {
        "meeting.accept": "I can make that time.",
        "meeting.decline": "I can't make that slot.",
        "ack.received": "Acknowledged.",
        "ack.deferred": "I need a little more before I can move on this.",
        "inform.blocker": "There is still a blocker here.",
        "inform.risk": "This path is riskier than it looks.",
        "inform.decision": "Here is the decision I am operating from.",
        "inform.status_update": "Here is the latest status.",
        "inform.availability": "Here is my current availability.",
        "request.clarification": "I need the concrete tradeoff spelled out.",
        "request.review": "Send me the exact review package.",
        "request.feasibility": "I need the honest feasibility story first.",
        "request.ownership": "Who is actually owning this?",
        "request.eta": "What is the credible ETA here?",
        "request.approval": "I still need the formal approval path.",
        "approve.defer": "I am not ready to approve this yet.",
        "approve.grant": "Approved.",
        "negotiate.scope": "We should narrow this to the credible staged path.",
        "negotiate.timeline": "We need to revise the timeline to match the real work.",
        "negotiate.ownership": "We need to realign ownership before this moves.",
        "escalate.to_manager": "This needs manager attention.",
        "escalate.to_sponsor": "This needs sponsor attention.",
        "commit.confirm": "I can confirm that commitment.",
        "commit.propose": "Here is the commitment I can credibly make.",
        "commit.revise": "I need to revise that commitment.",
        "commit.retract": "I cannot stand behind that commitment as stated.",
    }
    return defaults.get(outgoing_act_id, "Acknowledged.")


def _fallback_semantic_envelopes(contract_cell: dict[str, Any]) -> list[dict[str, Any]]:
    selector = contract_cell.get("selector", {}) if isinstance(contract_cell, dict) else {}
    cell_id = str(contract_cell.get("id", "fallback.cell"))
    incoming_act_id = str(selector.get("incoming_act_id", "request.clarification"))
    surface = str(selector.get("surface", "chat"))
    if surface == "calendar" and incoming_act_id == "meeting.propose":
        available = _calendar_cell_accepts(contract_cell.get("guard"))
        outgoing_act_id = "meeting.accept" if available else "meeting.decline"
        outgoing_slots = {"status": "accepted"} if available else {"status": "declined", "reason": "unavailable"}
    else:
        outgoing_act_id, outgoing_slots = _fallback_response_plan(incoming_act_id)
        if surface == "chat":
            outgoing_slots.setdefault("target_actor_id", "tpm")
    return [
        {
            "id": f"{cell_id}.fallback",
            "weight": 1.0,
            "outgoing_act_id": outgoing_act_id,
            "outgoing_slots": outgoing_slots,
            "surface_facts": [],
            "belief_signals": [],
            "effects": [],
            "renderer_id": f"{cell_id}.fallback",
            "renderer_variants": _fallback_renderer_variants(outgoing_act_id, outgoing_slots),
        }
    ]


def _calendar_cell_accepts(guard: Any) -> bool:
    if not isinstance(guard, dict):
        return True
    context_field = guard.get("context_field")
    if isinstance(context_field, dict) and context_field.get("field") == "available_for_meeting":
        return context_field.get("equals") is True
    return True


def _fallback_response_plan(incoming_act_id: str) -> tuple[str, dict[str, Any]]:
    plans: dict[str, tuple[str, dict[str, Any]]] = {
        "ack.received": ("inform.status_update", {"status": "acknowledged"}),
        "ack.deferred": ("inform.status_update", {"status": "deferred_noted"}),
        "request.feasibility": ("inform.blocker", {"blocker": "needs_scope_or_dependency_alignment"}),
        "request.eta": ("inform.blocker", {"blocker": "credible_timeline_not_ready"}),
        "request.scope_tradeoff": (
            "negotiate.scope",
            {"proposed_scope": "narrower_credible_slice", "dropped_scope": "full_scope"},
        ),
        "request.clarification": ("request.clarification", {"question": "scope_and_dependency_details"}),
        "request.review": ("request.clarification", {"question": "review_material_and_risk_context"}),
        "request.approval": ("request.clarification", {"question": "decision_scope_risks_and_owner"}),
        "request.ownership": ("request.clarification", {"question": "owner_boundary_and_expected_outcome"}),
        "negotiate.scope": (
            "negotiate.scope",
            {"proposed_scope": "narrower_credible_slice", "dropped_scope": "full_scope"},
        ),
        "negotiate.timeline": ("inform.blocker", {"blocker": "timeline_not_credible_without_tradeoff"}),
        "negotiate.ownership": ("request.clarification", {"question": "owner_boundary_and_handoff"}),
        "commit.propose": ("request.clarification", {"question": "commitment_preconditions_and_scope"}),
        "commit.confirm": ("inform.status_update", {"status": "commitment_acknowledged"}),
        "inform.status_update": ("request.clarification", {"question": "impact_on_scope_timing_or_dependencies"}),
        "inform.decision": ("inform.status_update", {"status": "decision_acknowledged"}),
        "inform.blocker": ("request.clarification", {"question": "blocker_impact_and_help_needed"}),
        "inform.risk": ("request.clarification", {"question": "risk_likelihood_impact_and_mitigation"}),
        "inform.availability": ("inform.status_update", {"status": "availability_acknowledged"}),
        "escalate.to_sponsor": ("inform.decision", {"decision": "escalation_acknowledged"}),
    }
    outgoing_act_id, outgoing_slots = plans.get(
        incoming_act_id,
        ("request.clarification", {"question": "next_step_and_decision_context"}),
    )
    return outgoing_act_id, deepcopy(outgoing_slots)


def _fallback_renderer_variants(outgoing_act_id: str, outgoing_slots: dict[str, Any]) -> list[str]:
    variants: dict[str, list[str]] = {
        "meeting.accept": [
            "I can make that time.",
            "That slot works for me.",
        ],
        "meeting.decline": [
            "I can't make that slot.",
            "That time doesn't work for me.",
        ],
        "inform.blocker": [
            "I can't give you a credible answer yet. The blocker is {blocker}.",
            "The honest constraint here is {blocker}, so I don't want to fake certainty.",
        ],
        "negotiate.scope": [
            "The credible path is {proposed_scope}, not {dropped_scope}.",
            "If we want a believable plan, we should move to {proposed_scope} and stop carrying {dropped_scope}.",
        ],
        "request.clarification": [
            "I need more clarity on {question} before I can respond credibly.",
            "Can you clarify {question} so I can give you a useful answer?",
        ],
        "inform.status_update": [
            "Understood. I'm noting {status} and planning around it.",
            "Got it. I'll treat this as {status} unless something changes.",
        ],
        "inform.decision": [
            "Acknowledged. I'm operating from {decision} for now.",
            "Understood. I'll treat {decision} as the working decision.",
        ],
    }
    if outgoing_act_id in variants:
        return variants[outgoing_act_id]
    return [_default_renderer_variant(outgoing_act_id)]


def _normalize_effects(candidate_effects: Any, baseline_effects: Any) -> list[dict[str, Any]]:
    if not isinstance(candidate_effects, list):
        candidate_effects = baseline_effects if isinstance(baseline_effects, list) else []
    normalized: list[dict[str, Any]] = []
    for effect in candidate_effects:
        if not isinstance(effect, dict):
            continue
        kind = effect.get("type")
        if kind == "relationship_patch":
            patch = effect.get("patch", {})
            if isinstance(patch, dict) and len(patch) == 1:
                field, delta = next(iter(patch.items()))
                if isinstance(delta, (int, float)):
                    normalized.append(
                        {
                            "type": "relationship_delta",
                            "actor_id": effect.get("actor_id"),
                            "target_actor_id": effect.get("target_actor_id"),
                            "field": field,
                            "delta": float(delta),
                        }
                    )
                    continue
        if kind in {
            "relationship_delta",
            "project_state_patch",
            "actor_state_patch",
            "belief_signal",
            "fact_signal",
            "create_or_update_commitment",
            "task_state_patch",
            "meeting_schedule_hint",
        }:
            normalized.append(deepcopy(effect))
    if not normalized and isinstance(baseline_effects, list):
        return deepcopy(baseline_effects)
    return normalized


def _load_accepted_full_artifact(scenario_id: str, filename: str) -> dict[str, Any] | None:
    path = Path("tpm_sim") / "scenarios" / scenario_id / filename
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _load_accepted_trajectory_reference(scenario_id: str) -> dict[str, Any]:
    directory = _trajectory_reference_directory(scenario_id)
    if directory is None:
        return {}
    items = sorted(directory.glob("*.tpm"))
    example_items = _reference_trajectory_examples(directory)
    return {
        "directory": str(directory),
        "filenames": [item.name for item in items],
        "line_counts": {item.name: len(item.read_text().splitlines()) for item in items},
        "first_commands": {
            item.name: [line for line in item.read_text().splitlines() if line.strip() and not line.strip().startswith("#")][:3]
            for item in items
        },
        "example_scripts": {item.name: item.read_text() for item in example_items},
    }


def _load_accepted_full_trajectories(scenario_id: str) -> dict[str, str]:
    directory = _trajectory_reference_directory(scenario_id)
    if directory is None:
        return {}
    return {item.name: item.read_text() for item in sorted(directory.glob("*.tpm"))}


def _trajectory_reference_directory(scenario_id: str | None) -> Path | None:
    if not scenario_id:
        return None
    nested = Path("examples") / scenario_id
    if nested.exists():
        return nested
    root = Path("examples")
    if scenario_id in LEGACY_ROOT_TRAJECTORY_SCENARIOS and any(root.glob("*.tpm")):
        return root
    return None


def _reference_trajectory_examples(directory: Path) -> list[Path]:
    ordered: list[Path] = []
    for name in ("golden.tpm", "smoke.tpm", "false_green.tpm", "anti_pattern_scenario.tpm"):
        path = directory / name
        if path.exists():
            ordered.append(path)
    for path in sorted(directory.glob("*.tpm")):
        if path not in ordered:
            ordered.append(path)
    return ordered[:3]


def _merge_authoring_candidate(candidate: Any, baseline: Any) -> Any:
    if isinstance(candidate, dict) and isinstance(baseline, dict):
        merged = dict(baseline)
        for key, value in candidate.items():
            if key in baseline:
                merged[key] = _merge_authoring_candidate(value, baseline[key])
            else:
                merged[key] = value
        return merged
    if isinstance(candidate, list) and isinstance(baseline, list):
        if baseline and all(isinstance(item, dict) for item in baseline):
            baseline_keys = set(baseline[0].keys())
            if not candidate or not all(isinstance(item, dict) for item in candidate):
                return baseline
            if baseline_keys and not all(baseline_keys.issubset(set(item.keys())) for item in candidate):
                return baseline
        return candidate
    if type(candidate) is type(baseline):
        return candidate
    return baseline


def _write_model_response(proposal_dir: str, stage: str, response: Any) -> None:
    paths = _proposal_paths(proposal_dir)
    target = paths["reports"] / f"{stage}_model_response.json"
    target.write_text(json.dumps(response.to_dict(), indent=2, sort_keys=True))


def _extract_json_payload(text: str, *, required_keys: list[str] | None = None, artifact_name: str = "artifact") -> Any:
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
        if candidate.lower().startswith("json"):
            candidate = candidate[4:].lstrip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        payload = json.loads(_extract_balanced_json(candidate))
    if required_keys:
        missing = [key for key in required_keys if key not in payload]
        if missing:
            raise RuntimeError(f"{artifact_name} response missing required keys: {', '.join(missing)}")
    return payload


def _require_nested_mapping_keys(payload: Any, section: str, *, required_keys: list[str], artifact_name: str) -> None:
    if not isinstance(payload, dict):
        raise RuntimeError(f"{artifact_name} response was not a JSON object.")
    value = payload.get(section)
    if not isinstance(value, dict):
        raise RuntimeError(f"{artifact_name} response missing object section: {section}")
    missing = [key for key in required_keys if key not in value]
    if missing:
        raise RuntimeError(f"{artifact_name} section '{section}' missing required keys: {', '.join(missing)}")


def _extract_balanced_json(text: str) -> str:
    start = None
    opener = ""
    for idx, char in enumerate(text):
        if char in "{[":
            start = idx
            opener = char
            break
    if start is None:
        raise RuntimeError("Model response did not contain a JSON object or array.")
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        char = text[idx]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
    raise RuntimeError("Model response contained unterminated JSON payload.")


def _load_proposal_brief(proposal_dir: str) -> AuthoringBrief:
    return load_brief(_proposal_paths(proposal_dir)["root"] / "brief.json")


def _update_manifest(proposal_dir: str, **patch: Any) -> None:
    manifest_path = _proposal_paths(proposal_dir)["manifest"]
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    if "generated_artifacts" in patch and isinstance(manifest.get("generated_artifacts"), dict) and isinstance(patch["generated_artifacts"], dict):
        merged_artifacts = dict(manifest["generated_artifacts"])
        merged_artifacts.update(patch["generated_artifacts"])
        patch = {**patch, "generated_artifacts": merged_artifacts}
    manifest.update(patch)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))


def _with_operator_briefing(result: dict[str, Any], proposal_dir: str) -> dict[str, Any]:
    refreshed = _refresh_operator_briefing(proposal_dir)
    payload = dict(result)
    payload["operator_briefing_json_path"] = refreshed["json_path"]
    payload["operator_briefing_markdown_path"] = refreshed["markdown_path"]
    return payload


def _refresh_operator_briefing(proposal_dir: str) -> dict[str, str]:
    paths = _proposal_paths(proposal_dir)
    brief = _load_proposal_brief(proposal_dir)
    scenario = json.loads(paths["scenario"].read_text()) if paths["scenario"].exists() else None
    manifest = json.loads(paths["manifest"].read_text()) if paths["manifest"].exists() else {}
    validation = json.loads(paths["validation"].read_text()) if paths["validation"].exists() else None
    closure = json.loads(paths["closure_report"].read_text()) if paths["closure_report"].exists() else None
    diff = json.loads(paths["diff"].read_text()) if paths["diff"].exists() else None
    briefing = build_authoring_briefing(
        brief,
        scenario=scenario,
        proposal_status=build_proposal_status(manifest, validation=validation, closure=closure, diff=diff),
        source_kind="proposal_candidate" if scenario is not None else "authoring_brief",
    )
    written = write_operator_briefing_artifacts(
        briefing,
        json_path=paths["operator_briefing_json"],
        markdown_path=paths["operator_briefing_markdown"],
    )
    _update_manifest(
        proposal_dir,
        generated_artifacts={
            "operator_briefing_json": written["json_path"],
            "operator_briefing_markdown": written["markdown_path"],
        },
    )
    return written


def _smoke_scripts(trajectory_dir: Path) -> list[Path]:
    preferred = []
    for name in ("smoke.tpm", "golden.tpm"):
        path = trajectory_dir / name
        if path.exists():
            preferred.append(path)
    extras = sorted(path for path in trajectory_dir.glob("*.tpm") if path not in preferred)
    return preferred[:2] if preferred else extras[:2]


def _closure_scripts(trajectory_dir: Path) -> list[Path]:
    ordered: list[Path] = []
    for name in ("golden.tpm", "smoke.tpm", "busywork.tpm", "false_green.tpm", "spray_and_pray.tpm"):
        path = trajectory_dir / name
        if path.exists():
            ordered.append(path)
    if ordered:
        return ordered
    extras = sorted(path for path in trajectory_dir.glob("*.tpm"))
    return extras[:3]


def _run_candidate_script(bundle: dict[str, Any], script_path: Path, seed: int) -> dict[str, Any]:
    from tpm_sim.cli import execute_script
    from tpm_sim.engine import SimulationEngine
    from tpm_sim.evaluator import Evaluator

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "candidate.sqlite")
        store = open_store(db_path)
        try:
            seed_store(store, bundle, seed, coverage_enforcement="strict")
            engine = SimulationEngine(store, bundle)
            evaluator = Evaluator(engine)
            execute_script(engine, evaluator, script_path, echo=False, emit=False)
            report = evaluator.evaluate()
            return {
                "script": script_path.name,
                "score": report["total_score"],
                "error": None,
            }
        except Exception as exc:
            return {"script": script_path.name, "score": None, "error": str(exc)}
        finally:
            store.close()


def _render_validation_summary(report: dict[str, Any]) -> str:
    lines = [
        f"Scenario: {report.get('scenario_id', 'unknown')}",
        f"Valid: {report['valid']}",
    ]
    if report.get("bundle_digest"):
        lines.append(f"Digest: {report['bundle_digest']}")
    if report.get("coverage_report"):
        coverage = report["coverage_report"]
        lines.append(
            f"Coverage: {coverage['covered_reachable_cells']} / {coverage['total_reachable_cells']} "
            f"({coverage['coverage']:.2%}), critical_uncovered={coverage['critical_uncovered']}"
        )
    if report.get("compile_report"):
        compile_report = report["compile_report"]
        lines.append(
            "Compile: "
            f"cells={compile_report['contract_cell_count']} semantics={compile_report['semantic_entry_count']} "
            f"families={compile_report['compiled_family_count']} renderers={compile_report['renderer_count']}"
        )
    if report.get("scenario_validation_errors"):
        lines.append("Scenario validation:")
        lines.extend(f"- {item}" for item in report["scenario_validation_errors"])
    if report.get("smoke_results"):
        lines.append("Smoke runs:")
        for item in report["smoke_results"]:
            status = f"score={item['score']}" if item["error"] is None else f"error={item['error']}"
            lines.append(f"- {item['script']}: {status}")
    if report.get("trajectory_syntax_errors"):
        lines.append("Trajectory syntax:")
        lines.extend(f"- {item}" for item in report["trajectory_syntax_errors"])
    if report.get("errors"):
        lines.append("Errors:")
        lines.extend(f"- {item}" for item in report["errors"])
    return "\n".join(lines)


def _json_semantic_diff(current: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "top_level_changed": sorted(
            key
            for key in set(current) | set(candidate)
            if current.get(key) != candidate.get(key)
        )
    }
    for key in ("actors", "tasks", "milestones", "facts", "windows", "threads", "rubric_lines"):
        current_ids = _id_set(current.get(key, []))
        candidate_ids = _id_set(candidate.get(key, []))
        if current_ids or candidate_ids:
            summary[key] = {
                "added": sorted(candidate_ids - current_ids),
                "removed": sorted(current_ids - candidate_ids),
            }
    return summary


def _contract_diff(current: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    current_cells = {item["id"]: item for item in current.get("cells", [])}
    candidate_cells = {item["id"]: item for item in candidate.get("cells", [])}
    changed: dict[str, Any] = {}
    for cell_id in sorted(set(current_cells) & set(candidate_cells)):
        left = current_cells[cell_id]
        right = candidate_cells[cell_id]
        delta: dict[str, Any] = {}
        for key in ("selector", "guard", "criticality", "priority"):
            if left.get(key) != right.get(key):
                delta[key] = {"current": left.get(key), "candidate": right.get(key)}
        if delta:
            changed[cell_id] = delta
    return {
        "added": sorted(set(candidate_cells) - set(current_cells)),
        "removed": sorted(set(current_cells) - set(candidate_cells)),
        "changed": changed,
    }


def _semantics_diff(current: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    current_cells = {item["cell_id"]: item for item in current.get("cells", [])}
    candidate_cells = {item["cell_id"]: item for item in candidate.get("cells", [])}
    changed: dict[str, Any] = {}
    for cell_id in sorted(set(current_cells) & set(candidate_cells)):
        left = current_cells[cell_id]
        right = candidate_cells[cell_id]
        if left != right:
            changed[cell_id] = {
                "current_outgoing_acts": [item.get("outgoing_act_id") for item in left.get("response_envelopes", [])],
                "candidate_outgoing_acts": [item.get("outgoing_act_id") for item in right.get("response_envelopes", [])],
            }
    return {
        "added": sorted(set(candidate_cells) - set(current_cells)),
        "removed": sorted(set(current_cells) - set(candidate_cells)),
        "changed": changed,
    }


def _id_set(items: list[dict[str, Any]]) -> set[str]:
    output = set()
    for item in items:
        if "id" in item:
            output.add(str(item["id"]))
        elif "label" in item:
            output.add(str(item["label"]))
    return output


def _source_digest_for_bytes(scenario_bytes: bytes, contract_bytes: bytes, semantics_bytes: bytes) -> str:
    return build_source_digest(
        scenario_bytes,
        contract_bytes,
        semantics_bytes,
        spec_parts=[path.read_bytes() for path in SPEC_FILES],
    )
