from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from tpm_sim.agent import AgentRunner, OpenAIResponsesAgentAdapter
from tpm_sim.authoring import (
    accept_proposal,
    compile_contract,
    compile_coverage_artifact,
    diff_proposal,
    gap_fill_proposal,
    init_proposal,
    run_closure_suite,
    synthesize_coverage,
    synthesize_semantics,
    synthesize_trajectories,
    synthesize_world,
    validate_proposal,
)
from tpm_sim.common import csv_ids, parse_duration, parse_slot_map, split_pipe_args
from tpm_sim.engine import CoverageMissError, SimulationEngine
from tpm_sim.environment import EnvironmentSession, StructuredAction, render_step_result
from tpm_sim.evaluator import Evaluator, summarize_score_band
from tpm_sim.model_client import build_model_client
from tpm_sim.performance import (
    export_bundle_summary,
    export_run_summary,
    render_bundle_summary,
    render_run_summary,
    summarize_existing_bundle,
    summarize_existing_run,
)
from tpm_sim.scenario import (
    available_scenarios,
    load_bundle_from_store,
    load_scenario_bundle,
    seed_store,
)
from tpm_sim.storage import open_store


HELP_TEXT = """Commands:
  status
  people
  inbox
  observe
  tasks
  calendar
  docs list
  docs open DOC-ID
  docs write TYPE | TITLE | BODY
  notes write TITLE | BODY
  chat list
  chat open THREAD_OR_ACTOR
  chat send TARGET | ACT_ID | key=value,... | BODY
  calendar schedule 30m | maya,andrew | TITLE | key=value,... | AGENDA
  meeting act MEETING_ID | ACT_ID | key=value,... | BODY
  task note TASK-ID | NOTE
  task owner TASK-ID | OWNER-ID
  task target TASK-ID | YYYY-MM-DDTHH:MM:SS
  wait 60m
  wait next 120m
  coverage
  score
  log
  checkpoint LABEL
  fork LABEL | OUT_DB_PATH | [SEED]
  quit
"""


class ShellExit(Exception):
    pass


def build_runtime(db_path: str) -> tuple[SimulationEngine, Evaluator]:
    store = open_store(db_path)
    scenario_id = store.get_meta("scenario_id")
    if not scenario_id:
        raise RuntimeError(f"Database at {db_path} has not been initialized. Run `python3 -m tpm_sim init` first.")
    bundle = load_bundle_from_store(store)
    engine = SimulationEngine(store, bundle)
    evaluator = Evaluator(engine)
    return engine, evaluator


def execute_command(engine: SimulationEngine, evaluator: Evaluator, raw_line: str) -> Optional[str]:
    session = EnvironmentSession(engine.store.path, engine, evaluator)
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return ""
    if line in {"quit", "exit"}:
        raise ShellExit()
    if line in {"help", "?"}:
        return HELP_TEXT
    if line == "status":
        return engine.render_status()
    if line == "people":
        return engine.render_people()
    if line == "inbox":
        return engine.render_inbox()
    if line == "observe":
        return json.dumps(engine.observe(), indent=2, sort_keys=True)
    if line == "tasks":
        result = session.step(StructuredAction("read.tasks", {}))
        return render_step_result(result)
    if line == "calendar":
        result = session.step(StructuredAction("read.calendar", {}))
        return render_step_result(result)
    if line == "docs list":
        return engine.render_docs()
    if line == "chat list":
        return engine.render_threads("chat")
    if line == "score":
        return engine.render_score_snapshot(evaluator)
    if line == "coverage":
        return json.dumps(engine.coverage_report(), indent=2, sort_keys=True)
    if line == "log":
        return engine.render_action_log()

    if line.startswith("wait next "):
        result = session.step(
            StructuredAction("wait.until_next_event", {"max_minutes": parse_duration(line.removeprefix("wait next ").strip())})
        )
        return render_step_result(result)
    if line.startswith("wait "):
        result = session.step(StructuredAction("wait.duration", {"minutes": parse_duration(line.removeprefix("wait ").strip())}))
        return render_step_result(result)
    if line.startswith("docs open "):
        result = session.step(StructuredAction("read.doc", {"doc_id": line.removeprefix("docs open ").strip()}))
        return render_step_result(result)
    if line.startswith("docs write "):
        doc_type, title, body = split_pipe_args(line.removeprefix("docs write "), expected=3)
        result = session.step(StructuredAction("docs.write", {"doc_type": doc_type, "title": title, "body": body}))
        return render_step_result(result)
    if line.startswith("notes write "):
        title, body = split_pipe_args(line.removeprefix("notes write "), expected=2)
        result = session.step(StructuredAction("notes.write", {"title": title, "body": body}))
        return render_step_result(result)
    if line.startswith("chat open "):
        result = session.step(StructuredAction("read.thread", {"target": line.removeprefix("chat open ").strip()}))
        return render_step_result(result)
    if line.startswith("chat send "):
        target, act_id, raw_slots, body = split_pipe_args(line.removeprefix("chat send "), expected=4)
        result = session.step(
            StructuredAction(
                "chat.send",
                {"target": target, "act_id": act_id, "slots": parse_slot_map(raw_slots), "body": body},
            )
        )
        return render_step_result(result)
    if line.startswith("calendar schedule "):
        duration, attendees, title, raw_slots, agenda = split_pipe_args(line.removeprefix("calendar schedule "), expected=5)
        result = session.step(
            StructuredAction(
                "meeting.propose",
                {
                    "duration_minutes": parse_duration(duration),
                    "attendees": csv_ids(attendees),
                    "title": title,
                    "slots": parse_slot_map(raw_slots),
                    "agenda": agenda,
                },
            )
        )
        return render_step_result(result)
    if line.startswith("meeting act "):
        meeting_id, act_id, raw_slots, body = split_pipe_args(line.removeprefix("meeting act "), expected=4)
        result = session.step(
            StructuredAction(
                "meeting.act",
                {"meeting_id": meeting_id, "act_id": act_id, "slots": parse_slot_map(raw_slots), "body": body},
            )
        )
        return render_step_result(result)
    if line.startswith("task note "):
        task_id, note = split_pipe_args(line.removeprefix("task note "), expected=2)
        result = session.step(StructuredAction("task.note", {"task_id": task_id, "note": note}))
        return render_step_result(result)
    if line.startswith("task owner "):
        task_id, owner_id = split_pipe_args(line.removeprefix("task owner "), expected=2)
        result = session.step(StructuredAction("task.set_owner", {"task_id": task_id, "owner_id": owner_id}))
        return render_step_result(result)
    if line.startswith("task target "):
        task_id, target_at = split_pipe_args(line.removeprefix("task target "), expected=2)
        result = session.step(StructuredAction("task.set_target", {"task_id": task_id, "target_at": target_at}))
        return render_step_result(result)
    if line.startswith("checkpoint "):
        path = engine.checkpoint(line.removeprefix("checkpoint ").strip())
        return f"Checkpoint written to {path}."
    if line.startswith("fork "):
        parts = split_pipe_args(line.removeprefix("fork "))
        if len(parts) not in {2, 3}:
            raise ValueError("fork expects LABEL | OUT_DB_PATH | [SEED]")
        label, out_db = parts[:2]
        seed_override = int(parts[2]) if len(parts) == 3 else None
        path = engine.fork(label, out_db, seed_override=seed_override)
        return f"Forked checkpoint to {path}."

    raise ValueError(f"Unknown command: {line}")


def execute_script(
    engine: SimulationEngine,
    evaluator: Evaluator,
    script_path: Path,
    *,
    echo: bool = False,
    emit: bool = True,
) -> None:
    for raw_line in script_path.read_text().splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if echo:
            print(f"> {stripped}")
        try:
            result = execute_command(engine, evaluator, stripped)
        except ShellExit:
            break
        if emit and result:
            print(result)
            if echo:
                print()


def init_db(db_path: str, scenario_id: str, seed: int, coverage_enforcement: str, force: bool) -> int:
    path = Path(db_path)
    if path.exists() and not force:
        raise RuntimeError(f"{db_path} already exists. Re-run with --force to overwrite it.")
    if path.exists():
        path.unlink()
    store = open_store(db_path)
    bundle = load_scenario_bundle(scenario_id)
    try:
        seed_store(store, bundle, seed, coverage_enforcement=coverage_enforcement)
    finally:
        store.close()
    print(f"Initialized {db_path} with scenario {scenario_id} (seed={seed}, coverage={coverage_enforcement}).")
    return 0


def run_shell(db_path: str) -> int:
    engine, evaluator = build_runtime(db_path)
    print(engine.render_status())
    try:
        while True:
            prompt = f"[{engine.now().strftime('%a %H:%M')}] tpm> "
            try:
                raw = input(prompt)
            except EOFError:
                print()
                break
            if not raw.strip():
                continue
            try:
                result = execute_command(engine, evaluator, raw)
            except ShellExit:
                break
            except Exception as exc:
                print(f"Error: {exc}")
                continue
            if result:
                print(result)
    finally:
        engine.store.close()
    return 0


def run_replay(db_path: str, script_path: str, echo: bool) -> int:
    engine, evaluator = build_runtime(db_path)
    try:
        execute_script(engine, evaluator, Path(script_path), echo=echo, emit=True)
    finally:
        engine.store.close()
    return 0


def run_eval(db_path: str, as_json_output: bool, export_prefix: Optional[str]) -> int:
    engine, evaluator = build_runtime(db_path)
    try:
        result = evaluator.export_report(export_prefix) if export_prefix else {"report": evaluator.evaluate()}
    finally:
        engine.store.close()
    report = result["report"]
    if as_json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(EvaluatorDisplay.render(report))
        if export_prefix:
            print("")
            print(f"Report: {result['report_path']}")
            print(f"Agent trace: {result['agent_trace_path']}")
            print(f"Omniscient trace: {result['omniscient_trace_path']}")
    return 0


def _run_scripted_seed(scenario_id: str, seed: int, script_path: Path, out_dir: Optional[Path] = None, coverage_enforcement: str = "strict") -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / f"{scenario_id}_{seed}.sqlite"
        store = open_store(str(db_path))
        bundle = load_scenario_bundle(scenario_id)
        seed_store(store, bundle, seed, coverage_enforcement=coverage_enforcement)
        engine = SimulationEngine(store, bundle)
        evaluator = Evaluator(engine)
        try:
            execute_script(engine, evaluator, script_path, echo=False, emit=False)
            exported = None
            if out_dir is not None:
                out_dir.mkdir(parents=True, exist_ok=True)
                exported = evaluator.export_report(str(out_dir / f"{scenario_id}_seed{seed}"))
            report = evaluator.evaluate() if exported is None else exported["report"]
            return {
                "seed": seed,
                "score": report["total_score"],
                "report": report,
                "paths": None if exported is None else {
                    "report": exported["report_path"],
                    "agent_trace": exported["agent_trace_path"],
                    "omniscient_trace": exported["omniscient_trace_path"],
                },
            }
        finally:
            store.close()


def run_benchmark(scenario_id: str, script_path: str, out_dir: Optional[str], seeds: Optional[list[int]], as_json_output: bool) -> int:
    bundle = load_scenario_bundle(scenario_id)
    seed_bundle = seeds or list(bundle["scenario"]["evaluation"].get("official_seeds", [11, 29, 47]))
    out_path = Path(out_dir) if out_dir else None
    results = [_run_scripted_seed(scenario_id, seed, Path(script_path), out_path, coverage_enforcement="strict") for seed in seed_bundle]
    scores = [float(item["score"]) for item in results]
    aggregate = {
        "scenario_id": scenario_id,
        "script": str(Path(script_path)),
        "seed_bundle": seed_bundle,
        "headline": summarize_score_band(scores),
        "runs": results,
    }
    if as_json_output:
        print(json.dumps(aggregate, indent=2, sort_keys=True))
    else:
        print(f"Scenario: {scenario_id}")
        print(f"Script: {script_path}")
        print(f"Seeds: {', '.join(str(seed) for seed in seed_bundle)}")
        print(f"Mean score: {aggregate['headline']['mean']}")
        print(f"Worst seed: {aggregate['headline']['worst']}")
        print(f"Stdev: {aggregate['headline']['stdev']}")
        print("")
        for item in results:
            print(f"- seed {item['seed']}: {item['score']}")
    return 0


def run_coverage_report(scenario_id: str, as_json_output: bool) -> int:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / f"{scenario_id}.sqlite"
        store = open_store(str(db_path))
        bundle = load_scenario_bundle(scenario_id)
        seed_store(store, bundle, 11, coverage_enforcement="permissive")
        engine = SimulationEngine(store, bundle)
        try:
            report = engine.coverage_report()
        finally:
            store.close()
    if as_json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Coverage: {report['covered_reachable_cells']} / {report['total_reachable_cells']} ({report['coverage']:.3f})")
        print(f"Critical uncovered: {report['critical_uncovered']}")
        if report["uncovered"]:
            print("")
            print("Uncovered cells:")
            for item in report["uncovered"]:
                print(f"- {item['id']}")
    return 0


def run_readiness(scenario_id: str, examples_dir: str, as_json_output: bool) -> int:
    scripts = {
        "golden": Path(examples_dir) / "golden.tpm",
        "competent_but_imperfect": Path(examples_dir) / "competent_but_imperfect.tpm",
        "busywork": Path(examples_dir) / "busywork.tpm",
        "false_green": Path(examples_dir) / "false_green.tpm",
        "spray_and_pray": Path(examples_dir) / "spray_and_pray.tpm",
    }
    missing = [name for name, path in scripts.items() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing readiness scripts: {', '.join(missing)}")

    bundle = load_scenario_bundle(scenario_id)
    official_seeds = list(bundle["scenario"]["evaluation"].get("official_seeds", [11, 29, 47]))
    readiness_runs: dict[str, dict[str, object]] = {}
    for name, path in scripts.items():
        results = [_run_scripted_seed(scenario_id, seed, path, None, coverage_enforcement="strict") for seed in official_seeds]
        readiness_runs[name] = {
            "scores": [float(item["score"]) for item in results],
            "band": summarize_score_band([float(item["score"]) for item in results]),
        }

    variance_seeds = list(range(11, 31))
    golden_variance = [_run_scripted_seed(scenario_id, seed, scripts["golden"], None, coverage_enforcement="strict")["score"] for seed in variance_seeds]
    competent_variance = [
        _run_scripted_seed(scenario_id, seed, scripts["competent_but_imperfect"], None, coverage_enforcement="strict")["score"]
        for seed in variance_seeds
    ]

    readiness = {
        "scenario_id": scenario_id,
        "official_seeds": official_seeds,
        "trajectories": readiness_runs,
        "variance": {
            "golden": summarize_score_band([float(score) for score in golden_variance]),
            "competent_but_imperfect": summarize_score_band([float(score) for score in competent_variance]),
            "seed_count": len(variance_seeds),
        },
        "gates": {
            "golden_mean_gte_85": readiness_runs["golden"]["band"]["mean"] >= 85,
            "golden_worst_gte_75": readiness_runs["golden"]["band"]["worst"] >= 75,
            "competent_mean_between_55_65": 55 <= readiness_runs["competent_but_imperfect"]["band"]["mean"] <= 65,
            "busywork_mean_lte_35": readiness_runs["busywork"]["band"]["mean"] <= 35,
            "false_green_mean_lte_30": readiness_runs["false_green"]["band"]["mean"] <= 30,
            "spray_and_pray_below_competent": readiness_runs["spray_and_pray"]["band"]["mean"] < readiness_runs["competent_but_imperfect"]["band"]["mean"],
        },
    }

    readiness["gates"]["golden_variance_bounded"] = readiness["variance"]["golden"]["stdev"] < 8
    readiness["gates"]["competent_variance_bounded"] = readiness["variance"]["competent_but_imperfect"]["stdev"] < 8

    if as_json_output:
        print(json.dumps(readiness, indent=2, sort_keys=True))
    else:
        print(f"Scenario: {scenario_id}")
        print("Trajectory bands:")
        for name, payload in readiness_runs.items():
            band = payload["band"]
            print(f"- {name}: mean={band['mean']} worst={band['worst']} stdev={band['stdev']}")
        print("")
        print("Variance characterization:")
        print(f"- golden: mean={readiness['variance']['golden']['mean']} stdev={readiness['variance']['golden']['stdev']} over {len(variance_seeds)} seeds")
        print(f"- competent_but_imperfect: mean={readiness['variance']['competent_but_imperfect']['mean']} stdev={readiness['variance']['competent_but_imperfect']['stdev']} over {len(variance_seeds)} seeds")
        print("")
        print("Readiness gates:")
        for gate, passed in readiness["gates"].items():
            print(f"- {gate}: {'PASS' if passed else 'FAIL'}")
    return 0


def _default_agent_output_dir(scenario_id: str, seed: int, model: str) -> Path:
    slug = model.replace("/", "_").replace(":", "_")
    return Path(".artifacts") / "agent_runs" / f"{scenario_id}_seed{seed}_{slug}"


def run_agent(
    scenario_id: str,
    seed: int,
    model: Optional[str],
    output_dir: Optional[str],
    max_turns: int,
    coverage_enforcement: str,
    stream_events: str,
    as_json_output: bool,
) -> int:
    resolved_model = _resolve_model_name(model)
    client = build_model_client("openai")
    adapter = OpenAIResponsesAgentAdapter(client, model=resolved_model, temperature=0, top_p=1)
    outdir = Path(output_dir) if output_dir else _default_agent_output_dir(scenario_id, seed, resolved_model)
    session = EnvironmentSession.create(str(outdir / "run.sqlite"), scenario_id, seed, coverage_enforcement=coverage_enforcement, force=True)
    try:
        runner = AgentRunner(adapter, max_turns=max_turns)
        record = runner.run(
            session,
            seed=seed,
            output_dir=str(outdir),
            model_name=resolved_model,
            event_stream=stream_events,
            on_event=_emit_live_event if stream_events != "none" else None,
        )
    finally:
        session.close()
    if stream_events != "none":
        print("Run complete. Generating summary artifacts...", file=sys.stderr, flush=True)
    summary = export_run_summary(
        outdir,
        judge_client=client,
        judge_model=os.getenv("TPM_JUDGE_MODEL") or resolved_model,
    )
    if as_json_output:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(render_run_summary(summary))
        print("")
        print(f"Summary JSON: {outdir / 'tpm_performance_summary.json'}")
        print(f"Summary Markdown: {outdir / 'tpm_performance_summary.md'}")
        print(f"Raw report: {record.report_path}")
        print(f"Agent log: {record.agent_log_path}")
    return 0


def run_agent_bundle_eval(
    scenario_id: str,
    model: Optional[str],
    output_dir: Optional[str],
    max_turns: int,
    as_json_output: bool,
) -> int:
    resolved_model = _resolve_model_name(model)
    bundle = load_scenario_bundle(scenario_id)
    seed_bundle = list(bundle["scenario"]["evaluation"].get("official_seeds", [11, 29, 47]))
    base_dir = Path(output_dir) if output_dir else Path(".artifacts") / "agent_bundle_eval" / scenario_id / resolved_model.replace("/", "_")
    run_summaries = []
    for seed in seed_bundle:
        seed_dir = base_dir / f"seed{seed}"
        client = build_model_client("openai")
        adapter = OpenAIResponsesAgentAdapter(client, model=resolved_model, temperature=0, top_p=1)
        session = EnvironmentSession.create(str(seed_dir / "run.sqlite"), scenario_id, seed, coverage_enforcement="strict", force=True)
        try:
            runner = AgentRunner(adapter, max_turns=max_turns)
            runner.run(session, seed=seed, output_dir=str(seed_dir), model_name=resolved_model)
        finally:
            session.close()
        run_summaries.append(
            export_run_summary(
                seed_dir,
                judge_client=client,
                judge_model=os.getenv("TPM_JUDGE_MODEL") or resolved_model,
            )
        )
    aggregate = export_bundle_summary(base_dir, run_summaries, scenario_id=scenario_id, model=resolved_model, seed_bundle=seed_bundle)
    if as_json_output:
        print(json.dumps(aggregate, indent=2, sort_keys=True))
    else:
        print(render_bundle_summary(aggregate))
        print("")
        print(f"Bundle summary JSON: {base_dir / 'bundle_performance_summary.json'}")
        print(f"Bundle summary Markdown: {base_dir / 'bundle_performance_summary.md'}")
    return 0


def run_agent_replay(run_dir: str) -> int:
    return _run_agent_replay(run_dir, events="none", event_limit=None)


def run_summarize_run(run_dir: str, as_json_output: bool) -> int:
    summary = summarize_existing_run(run_dir, judge_model=os.getenv("TPM_JUDGE_MODEL") or os.getenv("TPM_AGENT_MODEL"))
    if as_json_output:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(render_run_summary(summary))
        print("")
        print(f"Summary JSON: {Path(run_dir) / 'tpm_performance_summary.json'}")
    return 0


def run_summarize_bundle(bundle_dir: str, as_json_output: bool) -> int:
    summary = summarize_existing_bundle(bundle_dir)
    if as_json_output:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(render_bundle_summary(summary))
        print("")
        print(f"Bundle summary JSON: {Path(bundle_dir) / 'bundle_performance_summary.json'}")
    return 0


def _run_agent_replay(run_dir: str, *, events: str, event_limit: Optional[int]) -> int:
    payload = json.loads((Path(run_dir) / "agent_run.json").read_text())
    run = payload["run"]
    report_path = Path(run["report_path"])
    trace_paths: dict[str, str] = {}
    if report_path.exists():
        try:
            report_payload = json.loads(report_path.read_text())
            trace_paths = report_payload.get("trace_paths", {})
        except Exception:
            trace_paths = {}
    print(f"Scenario: {run['scenario_id']}")
    print(f"Seed: {run['seed']}")
    print(f"Model: {run['model']}")
    print(f"Score: {run['score']}")
    print(f"Turns: {run['turns_taken']}")
    print(f"Protocol failure: {'yes' if run['protocol_failure'] else 'no'}")
    print("")
    print("Turn log (TPM actions):")
    for turn in payload["decisions"]:
        action = turn["decision"].get("action", {})
        result = turn.get("step_result")
        summary = result["message"].splitlines()[0] if result else "; ".join(turn.get("validation_errors", []))
        if result:
            time_range = f"{result['time_before']} -> {result['time_after']}"
        else:
            observed_at = turn.get("observation_time")
            time_range = f"{observed_at} -> {observed_at}" if observed_at else "unknown"
        print(f"- turn {turn['turn']} [{time_range}] TPM {action.get('action_type', 'invalid')} -> {summary}")
    if trace_paths:
        print("")
        print("Full traces:")
        if trace_paths.get("agent_trace"):
            print(f"- Agent-perspective events: {trace_paths['agent_trace']}")
        if trace_paths.get("omniscient_trace"):
            print(f"- Omniscient events: {trace_paths['omniscient_trace']}")
    if events != "none" and trace_paths:
        trace_key = "agent_trace" if events == "agent" else "omniscient_trace"
        trace_path = trace_paths.get(trace_key)
        if trace_path:
            print("")
            print(f"Chronological {events} events:")
            for line in _render_trace_events(trace_path, limit=event_limit):
                print(line)
    return 0


def _emit_live_event(event: dict[str, Any]) -> None:
    print(_render_trace_row(event), file=sys.stderr, flush=True)


def _render_trace_row(row: dict[str, Any]) -> str:
    actor = row.get("actor_id") or "unknown"
    when = row.get("at") or "unknown"
    event_type = row.get("event_type") or "unknown"
    phase = row.get("phase") or "unknown"
    summary = row.get("summary") or ""
    return f"- [{when}] {actor} {event_type} ({phase}) -> {summary}"


def _render_trace_events(trace_path: str, *, limit: Optional[int]) -> list[str]:
    rows = []
    for raw_line in Path(trace_path).read_text().splitlines():
        if not raw_line.strip():
            continue
        rows.append(json.loads(raw_line))
    if limit is not None and limit >= 0:
        rows = rows[:limit]
    return [_render_trace_row(row) for row in rows]


def run_author_init(brief_path: str, proposal_dir: str, as_json_output: bool) -> int:
    manifest = init_proposal(brief_path, proposal_dir)
    if as_json_output:
        print(json.dumps(manifest, indent=2, sort_keys=True))
    else:
        print(f"Initialized proposal at {proposal_dir} for scenario {manifest['scenario_id']}.")
    return 0


def run_author_synthesize(
    kind: str,
    proposal_dir: str,
    adapter: str,
    model: Optional[str],
    fixtures_root: Optional[str],
    as_json_output: bool,
) -> int:
    resolved_model = _resolve_authoring_model(adapter, model)
    kwargs = {
        "proposal_dir": proposal_dir,
        "adapter": adapter,
        "model": resolved_model,
        "fixtures_root": fixtures_root,
    }
    if kind == "world":
        result = synthesize_world(**kwargs)
    elif kind == "semantics":
        result = synthesize_semantics(**kwargs)
    elif kind == "coverage":
        result = synthesize_coverage(**kwargs)
    elif kind == "trajectories":
        result = synthesize_trajectories(**kwargs)
    else:
        raise RuntimeError(f"Unknown synthesis kind '{kind}'.")
    if as_json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def run_author_compile_contract(proposal_dir: str, as_json_output: bool) -> int:
    result = compile_contract(proposal_dir)
    if as_json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def run_author_compile_coverage(proposal_dir: str, as_json_output: bool) -> int:
    result = compile_coverage_artifact(proposal_dir)
    if as_json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def run_author_validate(proposal_dir: str, as_json_output: bool) -> int:
    result = validate_proposal(proposal_dir)
    if as_json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def run_author_diff(proposal_dir: str, scenarios_root: str, as_json_output: bool) -> int:
    result = diff_proposal(proposal_dir, scenarios_root=scenarios_root)
    if as_json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def run_author_gap_fill(
    proposal_dir: str,
    gaps_path: str,
    adapter: str,
    model: Optional[str],
    fixtures_root: Optional[str],
    as_json_output: bool,
) -> int:
    result = gap_fill_proposal(
        proposal_dir,
        gaps_path=gaps_path,
        adapter=adapter,
        model=_resolve_authoring_model(adapter, model),
        fixtures_root=fixtures_root,
    )
    if as_json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def run_author_closure_suite(
    proposal_dir: str,
    adapter: str,
    model: Optional[str],
    fixtures_root: Optional[str],
    repeats: int,
    as_json_output: bool,
) -> int:
    resolved_model = None if adapter == "fixture" else _resolve_authoring_model(adapter, model)
    result = run_closure_suite(
        proposal_dir,
        adapter=adapter,
        model=resolved_model,
        fixtures_root=fixtures_root,
        repeats=repeats,
    )
    if as_json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def run_author_accept(
    proposal_dir: str,
    scenarios_root: str,
    examples_root: Optional[str],
    as_json_output: bool,
) -> int:
    result = accept_proposal(proposal_dir, scenarios_root=scenarios_root, examples_root=examples_root)
    if as_json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


class EvaluatorDisplay:
    @staticmethod
    def render(report: dict[str, object]) -> str:
        lines = [f"Scenario: {report['scenario_id']}", f"Digest: {report['scenario_digest']}", f"Total score: {report['total_score']} / 100", ""]
        lines.append("Rubric:")
        for item in report["rubric"]:
            lines.append(f"- {item['id']}: {item['awarded']} / {item['weight']}")
        lines.extend(["", "Failure breakdown:"])
        for name, value in report["failure_breakdown"].items():
            lines.append(f"- {name}: {value}")
        if report["decisive_moments"]:
            lines.extend(["", "Decisive moments:"])
            for item in report["decisive_moments"][:5]:
                lines.append(f"- {item['at']}: {item['summary']}")
        return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TPM first-week evaluation harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize a run database")
    init_parser.add_argument("--db", required=True, help="Path to the sqlite database file")
    init_parser.add_argument("--scenario", default="northstar_launch_week", choices=available_scenarios())
    init_parser.add_argument("--seed", type=int, default=11)
    init_parser.add_argument("--coverage-enforcement", choices=["strict", "permissive"], default="strict")
    init_parser.add_argument("--force", action="store_true")

    shell_parser = subparsers.add_parser("shell", help="Open an interactive shell")
    shell_parser.add_argument("--db", required=True)

    replay_parser = subparsers.add_parser("replay", help="Replay a script against a run database")
    replay_parser.add_argument("--db", required=True)
    replay_parser.add_argument("--script", required=True)
    replay_parser.add_argument("--echo", action="store_true")

    eval_parser = subparsers.add_parser("eval", help="Evaluate a run database")
    eval_parser.add_argument("--db", required=True)
    eval_parser.add_argument("--json", action="store_true")
    eval_parser.add_argument("--export-prefix")

    benchmark_parser = subparsers.add_parser("benchmark", help="Run a scripted trajectory across the official seed bundle")
    benchmark_parser.add_argument("--scenario", default="northstar_launch_week", choices=available_scenarios())
    benchmark_parser.add_argument("--script", required=True)
    benchmark_parser.add_argument("--outdir")
    benchmark_parser.add_argument("--seeds", help="Comma-separated override seed bundle")
    benchmark_parser.add_argument("--json", action="store_true")

    coverage_parser = subparsers.add_parser("coverage-report", help="Compute authored NPC coverage coverage")
    coverage_parser.add_argument("--scenario", default="northstar_launch_week", choices=available_scenarios())
    coverage_parser.add_argument("--json", action="store_true")

    readiness_parser = subparsers.add_parser("readiness", help="Run the authored readiness gate")
    readiness_parser.add_argument("--scenario", default="northstar_launch_week", choices=available_scenarios())
    readiness_parser.add_argument("--examples-dir", default=str(Path(__file__).resolve().parents[1] / "examples"))
    readiness_parser.add_argument("--json", action="store_true")

    summarize_run_parser = subparsers.add_parser("summarize-run", help="Generate or refresh the canonical TPM run summary for an agent run directory")
    summarize_run_parser.add_argument("--run-dir", required=True)
    summarize_run_parser.add_argument("--json", action="store_true")

    summarize_bundle_parser = subparsers.add_parser("summarize-bundle", help="Generate or refresh the canonical TPM bundle summary for a bundle directory")
    summarize_bundle_parser.add_argument("--bundle-dir", required=True)
    summarize_bundle_parser.add_argument("--json", action="store_true")

    agent_parser = subparsers.add_parser("agent", help="Run a live TPM agent or inspect prior runs")
    agent_subparsers = agent_parser.add_subparsers(dest="agent_command", required=True)

    agent_run = agent_subparsers.add_parser("run", help="Run one live TPM agent episode")
    agent_run.add_argument("--scenario", default="northstar_launch_week", choices=available_scenarios())
    agent_run.add_argument("--seed", type=int, default=11)
    agent_run.add_argument("--model", default=os.getenv("TPM_AGENT_MODEL"))
    agent_run.add_argument("--outdir")
    agent_run.add_argument("--max-turns", type=int, default=80)
    agent_run.add_argument("--coverage-enforcement", choices=["strict", "permissive"], default="strict")
    agent_run.add_argument("--stream-events", choices=["none", "agent", "omniscient"], default="omniscient")
    agent_run.add_argument("--json", action="store_true")

    agent_bundle = agent_subparsers.add_parser("bundle-eval", help="Run the live TPM agent across the official seed bundle")
    agent_bundle.add_argument("--scenario", default="northstar_launch_week", choices=available_scenarios())
    agent_bundle.add_argument("--model", default=os.getenv("TPM_AGENT_MODEL"))
    agent_bundle.add_argument("--outdir")
    agent_bundle.add_argument("--max-turns", type=int, default=80)
    agent_bundle.add_argument("--json", action="store_true")

    agent_replay = agent_subparsers.add_parser("replay", help="Replay a prior agent run directory")
    agent_replay.add_argument("--run-dir", required=True)
    agent_replay.add_argument("--events", choices=["none", "agent", "omniscient"], default="none")
    agent_replay.add_argument("--event-limit", type=int)

    author_parser = subparsers.add_parser("author", help="Offline authoring and curation workflow")
    author_subparsers = author_parser.add_subparsers(dest="author_command", required=True)

    author_init = author_subparsers.add_parser("init", help="Initialize a proposal directory from a structured brief")
    author_init.add_argument("--brief", required=True)
    author_init.add_argument("--proposal-dir", required=True)
    author_init.add_argument("--json", action="store_true")

    for subcommand in ("synthesize-world", "synthesize-semantics", "synthesize-coverage", "synthesize-trajectories"):
        synth = author_subparsers.add_parser(subcommand, help=f"{subcommand.replace('-', ' ').title()}")
        synth.add_argument("--proposal-dir", required=True)
        synth.add_argument("--adapter", choices=["openai", "fixture"], default="fixture")
        synth.add_argument("--model")
        synth.add_argument("--fixtures-root", default=str(Path(__file__).resolve().parents[1] / "authoring" / "fixtures"))
        synth.add_argument("--json", action="store_true")

    author_compile_contract = author_subparsers.add_parser("compile-contract", help="Compile a deterministic coverage contract from the scenario")
    author_compile_contract.add_argument("--proposal-dir", required=True)
    author_compile_contract.add_argument("--json", action="store_true")

    author_compile_coverage = author_subparsers.add_parser("compile-coverage", help="Compile npc_coverage.json from contract + semantics")
    author_compile_coverage.add_argument("--proposal-dir", required=True)
    author_compile_coverage.add_argument("--json", action="store_true")

    author_validate = author_subparsers.add_parser("validate", help="Validate a proposal bundle")
    author_validate.add_argument("--proposal-dir", required=True)
    author_validate.add_argument("--json", action="store_true")

    author_closure = author_subparsers.add_parser("closure-suite", help="Run deterministic and optional live closure checks for a proposal")
    author_closure.add_argument("--proposal-dir", required=True)
    author_closure.add_argument("--adapter", choices=["openai", "fixture"], default="fixture")
    author_closure.add_argument("--model")
    author_closure.add_argument("--fixtures-root", default=str(Path(__file__).resolve().parents[1] / "authoring" / "fixtures"))
    author_closure.add_argument("--repeats", type=int, default=1)
    author_closure.add_argument("--json", action="store_true")

    author_diff = author_subparsers.add_parser("diff", help="Diff a proposal against the accepted scenario")
    author_diff.add_argument("--proposal-dir", required=True)
    author_diff.add_argument("--scenarios-root", default=str(Path(__file__).resolve().parent / "scenarios"))
    author_diff.add_argument("--json", action="store_true")

    author_gap = author_subparsers.add_parser("gap-fill", help="Generate a coverage update from observed gaps")
    author_gap.add_argument("--proposal-dir", required=True)
    author_gap.add_argument("--gaps-path", required=True)
    author_gap.add_argument("--adapter", choices=["openai", "fixture"], default="fixture")
    author_gap.add_argument("--model")
    author_gap.add_argument("--fixtures-root", default=str(Path(__file__).resolve().parents[1] / "authoring" / "fixtures"))
    author_gap.add_argument("--json", action="store_true")

    author_accept = author_subparsers.add_parser("accept", help="Promote a validated proposal into accepted scenario artifacts")
    author_accept.add_argument("--proposal-dir", required=True)
    author_accept.add_argument("--scenarios-root", default=str(Path(__file__).resolve().parent / "scenarios"))
    author_accept.add_argument("--examples-root")
    author_accept.add_argument("--json", action="store_true")

    subparsers.add_parser("list-scenarios", help="List bundled scenarios")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        return init_db(args.db, args.scenario, args.seed, args.coverage_enforcement, args.force)
    if args.command == "shell":
        return run_shell(args.db)
    if args.command == "replay":
        return run_replay(args.db, args.script, args.echo)
    if args.command == "eval":
        return run_eval(args.db, args.json, args.export_prefix)
    if args.command == "benchmark":
        seeds = [int(item) for item in csv_ids(args.seeds)] if args.seeds else None
        return run_benchmark(args.scenario, args.script, args.outdir, seeds, args.json)
    if args.command == "coverage-report":
        return run_coverage_report(args.scenario, args.json)
    if args.command == "readiness":
        return run_readiness(args.scenario, args.examples_dir, args.json)
    if args.command == "summarize-run":
        return run_summarize_run(args.run_dir, args.json)
    if args.command == "summarize-bundle":
        return run_summarize_bundle(args.bundle_dir, args.json)
    if args.command == "agent":
        if args.agent_command == "run":
            return run_agent(args.scenario, args.seed, args.model, args.outdir, args.max_turns, args.coverage_enforcement, args.stream_events, args.json)
        if args.agent_command == "bundle-eval":
            return run_agent_bundle_eval(args.scenario, args.model, args.outdir, args.max_turns, args.json)
        if args.agent_command == "replay":
            return _run_agent_replay(args.run_dir, events=args.events, event_limit=args.event_limit)
    if args.command == "author":
        if args.author_command == "init":
            return run_author_init(args.brief, args.proposal_dir, args.json)
        if args.author_command == "synthesize-world":
            return run_author_synthesize("world", args.proposal_dir, args.adapter, args.model, args.fixtures_root, args.json)
        if args.author_command == "compile-contract":
            return run_author_compile_contract(args.proposal_dir, args.json)
        if args.author_command == "synthesize-semantics":
            return run_author_synthesize("semantics", args.proposal_dir, args.adapter, args.model, args.fixtures_root, args.json)
        if args.author_command == "synthesize-coverage":
            return run_author_synthesize("coverage", args.proposal_dir, args.adapter, args.model, args.fixtures_root, args.json)
        if args.author_command == "compile-coverage":
            return run_author_compile_coverage(args.proposal_dir, args.json)
        if args.author_command == "synthesize-trajectories":
            return run_author_synthesize("trajectories", args.proposal_dir, args.adapter, args.model, args.fixtures_root, args.json)
        if args.author_command == "validate":
            return run_author_validate(args.proposal_dir, args.json)
        if args.author_command == "closure-suite":
            return run_author_closure_suite(args.proposal_dir, args.adapter, args.model, args.fixtures_root, args.repeats, args.json)
        if args.author_command == "diff":
            return run_author_diff(args.proposal_dir, args.scenarios_root, args.json)
        if args.author_command == "gap-fill":
            return run_author_gap_fill(args.proposal_dir, args.gaps_path, args.adapter, args.model, args.fixtures_root, args.json)
        if args.author_command == "accept":
            return run_author_accept(args.proposal_dir, args.scenarios_root, args.examples_root, args.json)
    if args.command == "list-scenarios":
        for scenario_id in available_scenarios():
            print(scenario_id)
        return 0

    parser.error(f"Unhandled command: {args.command}")
    return 2


def _resolve_model_name(model: Optional[str]) -> str:
    resolved = model or os.getenv("TPM_AGENT_MODEL")
    if resolved:
        return resolved
    raise RuntimeError("A model is required. Pass --model or set TPM_AGENT_MODEL in your environment or .env file.")


def _default_authoring_model_name() -> Optional[str]:
    return os.getenv("TPM_AUTHORING_MODEL") or os.getenv("TPM_AGENT_MODEL")


def _resolve_authoring_model(adapter: str, model: Optional[str]) -> str:
    if adapter == "fixture":
        return model or "fixture"
    resolved = model or _default_authoring_model_name()
    if resolved:
        return resolved
    raise RuntimeError(
        "A model is required for OpenAI authoring. Pass --model or set TPM_AUTHORING_MODEL "
        "(or TPM_AGENT_MODEL as a fallback) in your environment or .env file."
    )


if __name__ == "__main__":
    raise SystemExit(main())
