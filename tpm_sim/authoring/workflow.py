from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from tpm_sim.authoring.briefs import AuthoringBrief, load_brief
from tpm_sim.authoring.prompts import (
    build_coverage_prompt,
    build_gap_fill_prompt,
    build_trajectories_prompt,
    build_world_prompt,
)
from tpm_sim.environment import EnvironmentSession
from tpm_sim.model_client import build_model_client
from tpm_sim.scenario import load_bundle_from_paths, seed_store
from tpm_sim.storage import open_store


def init_proposal(brief_path: str, proposal_dir: str) -> dict[str, Any]:
    brief = load_brief(brief_path)
    paths = _proposal_paths(proposal_dir)
    for path in paths.values():
        if isinstance(path, Path):
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
    prompt = build_world_prompt(brief.to_dict())
    prompt["scenario_id"] = brief.scenario_id
    prompt["artifact"] = "scenario.json"
    client = build_model_client(adapter, fixtures_root=fixtures_root, api_key=api_key, base_url=base_url)
    response = client.generate_text(prompt, {"model": model})
    scenario_candidate = json.loads(response.text)
    paths = _proposal_paths(proposal_dir)
    paths["scenario"].write_text(json.dumps(scenario_candidate, indent=2, sort_keys=True))
    _update_manifest(proposal_dir, status="world_synthesized", generated_artifacts={"scenario": str(paths["scenario"])})
    return {"scenario_path": str(paths["scenario"]), "latency_ms": response.latency_ms}


def synthesize_coverage(
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
    prompt = build_coverage_prompt(brief.to_dict(), scenario_candidate)
    prompt["scenario_id"] = brief.scenario_id
    prompt["artifact"] = "npc_coverage.json"
    client = build_model_client(adapter, fixtures_root=fixtures_root, api_key=api_key, base_url=base_url)
    response = client.generate_text(prompt, {"model": model})
    coverage_candidate = json.loads(response.text)
    paths["coverage"].write_text(json.dumps(coverage_candidate, indent=2, sort_keys=True))
    _update_manifest(proposal_dir, status="coverage_synthesized", generated_artifacts={"coverage": str(paths["coverage"])})
    return {"coverage_path": str(paths["coverage"]), "latency_ms": response.latency_ms}


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
    prompt = build_trajectories_prompt(brief.to_dict(), scenario_candidate)
    prompt["scenario_id"] = brief.scenario_id
    prompt["artifact"] = "trajectories.json"
    client = build_model_client(adapter, fixtures_root=fixtures_root, api_key=api_key, base_url=base_url)
    response = client.generate_text(prompt, {"model": model})
    payload = json.loads(response.text)
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
    if not paths["scenario"].exists():
        errors.append("candidate scenario.json missing")
    if not paths["coverage"].exists():
        errors.append("candidate npc_coverage.json missing")
    if errors:
        report = {"valid": False, "errors": errors}
        paths["validation"].write_text(json.dumps(report, indent=2, sort_keys=True))
        return report

    bundle = load_bundle_from_paths(paths["scenario"], paths["coverage"])
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

    if coverage_report["coverage"] < 0.97:
        errors.append("overall authored coverage is below 97%")

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
        "coverage_report": coverage_report,
        "smoke_results": smoke_results,
        "errors": errors,
    }
    paths["validation"].write_text(json.dumps(report, indent=2, sort_keys=True))
    paths["review_summary"].write_text(_render_validation_summary(report))
    _update_manifest(proposal_dir, status="validated" if report["valid"] else "validation_failed")
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
    candidate_coverage = json.loads(paths["coverage"].read_text()) if paths["coverage"].exists() else {}
    if not current_dir.exists():
        summary = {
            "scenario_exists": False,
            "summary": f"No accepted scenario exists for {brief.scenario_id}. Proposal is net-new.",
        }
        paths["diff"].write_text(json.dumps(summary, indent=2, sort_keys=True))
        return summary

    current_scenario = json.loads((current_dir / "scenario.json").read_text())
    current_coverage = json.loads((current_dir / "npc_coverage.json").read_text())
    diff = {
        "scenario_exists": True,
        "scenario_changes": _json_semantic_diff(current_scenario, candidate_scenario),
        "coverage_changes": _json_semantic_diff(current_coverage, candidate_coverage),
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
    coverage_candidate = json.loads(paths["coverage"].read_text())
    gaps = [json.loads(line) for line in Path(gaps_path).read_text().splitlines() if line.strip()]
    prompt = build_gap_fill_prompt(brief.to_dict(), scenario_candidate, coverage_candidate, gaps)
    prompt["scenario_id"] = brief.scenario_id
    prompt["artifact"] = "npc_coverage.json"
    client = build_model_client(adapter, fixtures_root=fixtures_root, api_key=api_key, base_url=base_url)
    response = client.generate_text(prompt, {"model": model})
    updated = json.loads(response.text)
    paths["coverage"].write_text(json.dumps(updated, indent=2, sort_keys=True))
    _update_manifest(proposal_dir, status="gap_filled")
    return {"coverage_path": str(paths["coverage"]), "latency_ms": response.latency_ms, "gap_count": len(gaps)}


def accept_proposal(
    proposal_dir: str,
    *,
    scenarios_root: str,
    examples_root: str | None = None,
) -> dict[str, Any]:
    paths = _proposal_paths(proposal_dir)
    validation = json.loads(paths["validation"].read_text()) if paths["validation"].exists() else None
    if not validation or not validation.get("valid"):
        raise RuntimeError("Proposal cannot be accepted until it has a successful validation report.")
    brief = _load_proposal_brief(proposal_dir)
    scenario_dir = Path(scenarios_root) / brief.scenario_id
    scenario_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(paths["scenario"], scenario_dir / "scenario.json")
    shutil.copyfile(paths["coverage"], scenario_dir / "npc_coverage.json")
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
        "manifest": root / "manifest.json",
        "scenario": candidate / "scenario.json",
        "coverage": candidate / "npc_coverage.json",
        "trajectories": trajectories,
        "validation": reports / "validation.json",
        "diff": reports / "diff.json",
        "review_summary": reports / "review_summary.md",
    }


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
    return preferred + extras


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
    for key in ("actors", "tasks", "milestones", "facts", "windows", "threads", "rubric_lines", "families"):
        current_ids = _id_set(current.get(key, []))
        candidate_ids = _id_set(candidate.get(key, []))
        if current_ids or candidate_ids:
            summary[key] = {
                "added": sorted(candidate_ids - current_ids),
                "removed": sorted(current_ids - candidate_ids),
            }
    return summary


def _id_set(items: list[dict[str, Any]]) -> set[str]:
    output = set()
    for item in items:
        if "id" in item:
            output.add(str(item["id"]))
        elif "label" in item:
            output.add(str(item["label"]))
    return output
