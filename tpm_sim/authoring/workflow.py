from __future__ import annotations

from copy import deepcopy
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from tpm_sim.agent import AgentRunner, OpenAIResponsesAgentAdapter
from tpm_sim.authoring.briefs import AuthoringBrief, load_brief
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
    validate_contract,
    validate_semantics,
)
from tpm_sim.environment import EnvironmentSession
from tpm_sim.model_client import build_model_client
from tpm_sim.scenario import SPEC_FILES, load_bundle_from_paths, seed_store
from tpm_sim.specs import require_known_act
from tpm_sim.storage import open_store


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
        if baseline_seeds and not scenario_candidate.get("evaluation", {}).get("official_seeds"):
            scenario_candidate.setdefault("evaluation", {})["official_seeds"] = list(baseline_seeds)
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
    paths = _proposal_paths(proposal_dir)
    paths["scenario"].write_text(json.dumps(scenario_candidate, indent=2, sort_keys=True))
    _update_manifest(proposal_dir, status="world_synthesized", generated_artifacts={"scenario": str(paths["scenario"])})
    return {"scenario_path": str(paths["scenario"]), "latency_ms": response.latency_ms}


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
    return {"coverage_contract_path": str(paths["contract"]), "cell_count": len(contract.get("cells", []))}


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
    errors = validate_semantics(coverage_contract, semantics_candidate)
    if errors:
        raise RuntimeError(f"coverage_semantics.json failed validation: {'; '.join(errors)}")
    paths["semantics"].write_text(json.dumps(semantics_candidate, indent=2, sort_keys=True))
    _update_manifest(
        proposal_dir,
        status="semantics_synthesized",
        generated_artifacts={"coverage_semantics": str(paths["semantics"])},
    )
    return {"coverage_semantics_path": str(paths["semantics"]), "latency_ms": response.latency_ms}


def compile_coverage_artifact(proposal_dir: str) -> dict[str, Any]:
    paths = _proposal_paths(proposal_dir)
    scenario_bytes = paths["scenario"].read_bytes()
    contract_bytes = paths["contract"].read_bytes()
    semantics_bytes = paths["semantics"].read_bytes()
    contract = json.loads(contract_bytes)
    semantics = json.loads(semantics_bytes)
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
    return {"coverage_path": str(paths["coverage"]), "compile_report_path": str(paths["coverage_compile_report"]), "report": report}


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
    if accepted_full:
        merged_payload = dict(accepted_full)
        for name, content in payload.items():
            if not (isinstance(content, str) and name.endswith(".tpm")):
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
    return {"trajectories": written, "latency_ms": response.latency_ms}


def validate_proposal(proposal_dir: str, *, smoke_seed: int = 11) -> dict[str, Any]:
    paths = _proposal_paths(proposal_dir)
    brief = _load_proposal_brief(proposal_dir)
    errors: list[str] = []
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
        return report

    compile_result = compile_coverage_artifact(proposal_dir)
    compile_report = compile_result["report"]
    if compile_report["errors"]:
        errors.extend(compile_report["errors"])

    bundle = load_bundle_from_paths(
        paths["scenario"],
        paths["coverage"],
        contract_path=paths["contract"],
        semantics_path=paths["semantics"],
    )
    coverage_report = None
    smoke_results: list[dict[str, Any]] = []
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
    if compile_report["missing_semantic_cells"]:
        errors.append("contract cells are missing semantics")
    if compile_report["orphan_semantic_cells"]:
        errors.append("orphan semantic cells detected")

    scripts = _smoke_scripts(paths["trajectories"])
    if not scripts:
        errors.append("no smoke trajectories available")
    else:
        for script in scripts[:2]:
            smoke_results.append(_run_candidate_script(bundle, script, smoke_seed))

    if any(item["error"] for item in smoke_results):
        errors.append("smoke trajectory execution failed")

    report = {
        "valid": not errors,
        "scenario_id": brief.scenario_id,
        "bundle_digest": bundle["scenario_digest"],
        "compiled_coverage_digest": bundle.get("compiled_coverage_digest"),
        "coverage_report": coverage_report,
        "compile_report": compile_report,
        "smoke_results": smoke_results,
        "errors": errors,
    }
    paths["validation"].write_text(json.dumps(report, indent=2, sort_keys=True))
    paths["review_summary"].write_text(_render_validation_summary(report))
    _update_manifest(proposal_dir, status="validated" if report["valid"] else "validation_failed")
    return report


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

    bundle = load_bundle_from_paths(
        paths["scenario"],
        paths["coverage"],
        contract_path=paths["contract"],
        semantics_path=paths["semantics"],
    )
    scenario = bundle["scenario"]
    official_seeds = list(scenario.get("evaluation", {}).get("official_seeds", []))
    if not official_seeds:
        report = {
            "scenario_id": scenario["id"],
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
                    session = EnvironmentSession.create(
                        str(Path(tmpdir) / "closure.sqlite"),
                        scenario["id"],
                        seed,
                        coverage_enforcement="strict",
                        force=True,
                    )
                    try:
                        adapter_impl = OpenAIResponsesAgentAdapter(client, model=model, temperature=0, top_p=1)
                        record = AgentRunner(adapter_impl, max_turns=40).run(
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
    return report


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
        return summary

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
    return diff


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
    return {
        "coverage_contract_path": str(paths["contract"]),
        "coverage_semantics_path": str(paths["semantics"]),
        "coverage_path": str(paths["coverage"]),
        "added_cells": added_cells,
        "compile_report": compile_result["report"],
        "latency_ms": response.latency_ms,
        "gap_count": len(gaps),
    }


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
    shutil.copyfile(paths["scenario"], scenario_dir / "scenario.json")
    shutil.copyfile(paths["contract"], scenario_dir / "coverage_contract.json")
    shutil.copyfile(paths["semantics"], scenario_dir / "coverage_semantics.json")
    shutil.copyfile(paths["coverage"], scenario_dir / "npc_coverage.json")
    shutil.copyfile(paths["validation"], scenario_dir / "validation.json")
    shutil.copyfile(paths["closure_report"], scenario_dir / "closure_report.json")
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
        "review_summary": reports / "review_summary.md",
    }


def _reference_scenario_id(brief: AuthoringBrief, filename: str) -> str | None:
    own_path = Path("tpm_sim") / "scenarios" / brief.scenario_id / filename
    if filename == "trajectories":
        own_path = Path("examples") / brief.scenario_id
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
        normalized_entry = {
            "cell_id": cell_id,
            "response_envelopes": _normalize_semantic_envelopes(
                cell_id,
                candidate_entry.get("response_envelopes", []) if isinstance(candidate_entry, dict) else [],
                baseline_entry.get("response_envelopes", []) if isinstance(baseline_entry, dict) else [],
            ),
        }
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
                "effects": deepcopy(candidate_env.get("effects", baseline_env.get("effects", []))),
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


def _load_accepted_full_artifact(scenario_id: str, filename: str) -> dict[str, Any] | None:
    path = Path("tpm_sim") / "scenarios" / scenario_id / filename
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _load_accepted_trajectory_reference(scenario_id: str) -> dict[str, Any]:
    directory = Path("examples") / scenario_id
    if not directory.exists():
        return {}
    items = sorted(directory.glob("*.tpm"))
    return {
        "filenames": [item.name for item in items],
        "line_counts": {item.name: len(item.read_text().splitlines()) for item in items},
        "first_commands": {
            item.name: [line for line in item.read_text().splitlines() if line.strip() and not line.strip().startswith("#")][:3]
            for item in items
        },
    }


def _load_accepted_full_trajectories(scenario_id: str) -> dict[str, str]:
    directory = Path("examples") / scenario_id
    if not directory.exists():
        return {}
    return {item.name: item.read_text() for item in sorted(directory.glob("*.tpm"))}


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
    manifest.update(patch)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))


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
    if report.get("smoke_results"):
        lines.append("Smoke runs:")
        for item in report["smoke_results"]:
            status = f"score={item['score']}" if item["error"] is None else f"error={item['error']}"
            lines.append(f"- {item['script']}: {status}")
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
