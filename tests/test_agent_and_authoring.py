from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from tpm_sim.agent import AgentDecision, AgentRunner, OpenAIResponsesAgentAdapter
from tpm_sim.agent.prompts import build_agent_prompt
from tpm_sim.authoring import (
    accept_proposal,
    compile_contract,
    compile_coverage_artifact,
    diff_proposal,
    init_proposal,
    run_closure_suite,
    synthesize_coverage,
    synthesize_semantics,
    synthesize_trajectories,
    synthesize_world,
    validate_proposal,
)
from tpm_sim.cli import _resolve_authoring_model, _resolve_summary_judge_model, _run_agent_replay, execute_command, execute_script
from tpm_sim.cli import (
    init_db,
    run_agent,
    run_agent_replay,
    run_summarize_run,
    run_author_compile_contract,
    run_author_init,
    run_author_synthesize,
    run_author_validate,
)
from tpm_sim.coverage_artifacts import build_starter_contract
from tpm_sim.engine import CoverageMissError, SimulationEngine
from tpm_sim.environment import ActionValidationError, EnvironmentSession, StructuredAction, validate_structured_action
from tpm_sim.evaluator import Evaluator
from tpm_sim.briefing import render_operator_briefing
from tpm_sim.model_client import ModelResponse, OpenAIResponsesModelClient, _extract_output_text, _extract_refusal
from tpm_sim.performance import (
    _build_signal_coverage,
    _merge_decision_action_rows,
    build_behavior_diagnostics,
    build_run_summary,
    export_bundle_summary,
    export_run_summary,
    render_bundle_summary,
    render_run_summary,
)
from tpm_sim.runtime_env import autoload_project_dotenv
from tpm_sim.scenario import load_bundle_from_paths, load_scenario_bundle, seed_store
from tpm_sim.script_dsl import parse_script_command, validate_trajectory_script_text
from tpm_sim.storage import open_store


ROOT = Path(__file__).resolve().parents[1]
AUTHORING_BRIEFS = ROOT / "authoring" / "briefs"
AUTHORING_FIXTURES = ROOT / "authoring" / "fixtures"
OFFICIAL_SCENARIOS = ROOT / "tpm_sim" / "scenarios"
EXAMPLES = ROOT / "examples"


def build_runtime(db_path: str, scenario_id: str, *, seed: int = 11, coverage_enforcement: str = "strict") -> tuple[SimulationEngine, Evaluator]:
    bundle = load_scenario_bundle(scenario_id)
    store = open_store(db_path)
    seed_store(store, bundle, seed, coverage_enforcement=coverage_enforcement)
    engine = SimulationEngine(store, bundle)
    evaluator = Evaluator(engine)
    return engine, evaluator


def load_bundle_from_current_sources(scenario_id: str) -> dict[str, object]:
    root = OFFICIAL_SCENARIOS / scenario_id
    return load_bundle_from_paths(
        root / "scenario.json",
        None,
        contract_path=root / "coverage_contract.json",
        semantics_path=root / "coverage_semantics.json",
        closure_report_path=root / "closure_report.json",
    )


def summary_scenario_bundle(scenario_id: str = "unit_test_scenario") -> dict[str, object]:
    return {
        "scenario": {
            "id": scenario_id,
            "start_at": "2026-05-05T09:00:00",
            "world": {
                "project": {
                    "name": "Unit Test Launch",
                    "description": "A compact deterministic scenario used for run-summary tests.",
                },
                "actors": [
                    {
                        "id": "tpm",
                        "name": "You",
                        "org_role": "technical_program_manager",
                        "coordination_template": "external_agent",
                        "authority_profile": {},
                    },
                    {
                        "id": "dana",
                        "name": "Dana Brooks",
                        "org_role": "director_product",
                        "coordination_template": "sponsor",
                        "authority_profile": {"can_approve_scope": True},
                    },
                    {
                        "id": "leo",
                        "name": "Leo Park",
                        "org_role": "engineer",
                        "coordination_template": "critical_path_owner",
                        "authority_profile": {"can_commit_eta": True},
                    },
                    {
                        "id": "ivy",
                        "name": "Ivy Shah",
                        "org_role": "security_engineer",
                        "coordination_template": "cross_functional_dependency_owner",
                        "authority_profile": {"can_grant_review": True},
                    },
                    {
                        "id": "mia",
                        "name": "Mia Torres",
                        "org_role": "operations",
                        "coordination_template": "ally",
                        "authority_profile": {},
                    },
                ],
                "facts": [
                    {
                        "id": "approval_required",
                        "label": "Approval is required for the staged rollout",
                        "description": "Approval is required before the rollout can proceed.",
                        "metadata": {},
                    },
                    {
                        "id": "dana_accepts_staged_if_early",
                        "label": "Dana backs staged rollout if engaged early",
                        "description": "Dana will support staged rollout if the TPM brings the tradeoff early.",
                        "metadata": {
                            "coordination_implication": "Surface the real feasibility gap early and frame the staged plan clearly before the scope window closes.",
                            "fact_kind": "actor_private_driver",
                            "owner_actor_id": "dana",
                        },
                    },
                    {
                        "id": "full_rollout_infeasible",
                        "label": "Full rollout is not credible this week",
                        "description": "The full rollout path is not credible.",
                        "metadata": {},
                    },
                    {
                        "id": "leo_rejects_fake_rollout_dates",
                        "label": "Leo rejects fake rollout dates until the path is real",
                        "description": "Leo will not commit to dates until scope and approval are grounded.",
                        "metadata": {
                            "coordination_implication": "Ask Leo for honest feasibility before ETA.",
                            "fact_kind": "actor_private_driver",
                            "owner_actor_id": "leo",
                        },
                    },
                    {
                        "id": "ops_checklist_available",
                        "label": "Operations checklist already exists",
                        "description": "Ops already has the checklist and rollback notes.",
                        "metadata": {},
                    },
                ],
                "windows": [
                    {"id": "scope_alignment_cutoff", "title": "Scope alignment window", "start_at": "2026-05-05T09:00:00", "end_at": "2026-05-05T10:00:00"},
                    {"id": "approval_cutoff", "title": "Approval queue window", "start_at": "2026-05-05T09:00:00", "end_at": "2026-05-05T10:30:00"},
                ],
                "tasks": [
                    {
                        "id": "config_rollout",
                        "title": "Config rollout",
                        "owner_id": "leo",
                        "due_at": "2026-05-05T12:00:00",
                        "metadata": {"critical": True},
                    },
                    {
                        "id": "approval_review",
                        "title": "Approval review",
                        "owner_id": "ivy",
                        "due_at": "2026-05-05T10:30:00",
                        "metadata": {"critical": True},
                    },
                    {
                        "id": "runbook_readiness",
                        "title": "Runbook readiness",
                        "owner_id": "mia",
                        "due_at": "2026-05-05T11:30:00",
                        "metadata": {"critical": False},
                    },
                ],
                "documents": [
                    {
                        "id": "DOC-BRIEF-100",
                        "title": "Kickoff brief",
                        "content": "Leadership wants the real rollout story early, not fake green.",
                    },
                    {
                        "id": "DOC-OPS-101",
                        "title": "Ops checklist",
                        "content": "The staging checklist already exists.",
                    },
                ],
                "milestones": [
                    {"id": "scope_aligned", "title": "Scope aligned", "due_at": "2026-05-05T10:00:00"},
                    {"id": "approval_secured", "title": "Approval secured", "due_at": "2026-05-05T10:30:00"},
                    {"id": "rollout_ready", "title": "Rollout ready", "due_at": "2026-05-05T12:00:00"},
                ],
            },
            "evaluation": {
                "primary_failure_classes": ["timing", "discovery", "commitment"],
            },
        }
    }


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def build_failure_summary_fixture(tmpdir: str) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    run_dir = Path(tmpdir) / "failure_run"
    run_dir.mkdir(parents=True, exist_ok=True)
    scenario_id = "internal_rollout_smoke"
    store = open_store(str(run_dir / "run.sqlite"))
    try:
        approval_first = store.log_action(
            "2026-05-05T09:00:00",
            "tpm",
            "chat",
            "request.approval",
            body="Need approval for staged rollout.",
            duration_minutes=10,
            metadata={"cost_key": "follow_up_on_commitment"},
        )
        approval_second = store.log_action(
            "2026-05-05T09:20:00",
            "tpm",
            "chat",
            "request.approval",
            body="Following up on approval.",
            duration_minutes=10,
            metadata={"cost_key": "follow_up_on_commitment"},
        )
        approval_third = store.log_action(
            "2026-05-05T09:22:00",
            "tpm",
            "chat",
            "request.approval",
            body="Bumping approval again.",
            duration_minutes=10,
            metadata={"cost_key": "status_push_without_new_information"},
        )
        dana_read = store.log_action(
            "2026-05-05T09:07:00",
            "tpm",
            "chat",
            "read.thread",
            slots={"thread_id": "dana"},
            body="",
            duration_minutes=2,
            metadata={"thread_id": "dana", "target_actor_id": "dana"},
        )
        dana_message = store.add_message(
            {
                "thread_id": "dana",
                "surface": "chat",
                "sender_id": "dana",
                "act_id": "request.clarification",
                "slots": {},
                "body": "What is actually credible by Wednesday?",
                "created_at": "2026-05-05T09:05:00",
                "unread_for_tpm": False,
                "metadata": {},
            }
        )
        ivy_message = store.add_message(
            {
                "thread_id": "ivy",
                "surface": "chat",
                "sender_id": "ivy",
                "act_id": "inform.blocker",
                "slots": {},
                "body": "I need the full staged-rollout request in the queue before the cutoff if you want today's review.",
                "created_at": "2026-05-05T09:06:00",
                "unread_for_tpm": False,
                "metadata": {},
            }
        )
    finally:
        store.close()
    agent_trace_rows = [
        {
            "id": 9,
            "at": "2026-05-05T09:06:00",
            "phase": "runtime",
            "event_type": "fact_signal",
            "actor_id": "system",
            "visibility": "agent",
            "summary": "Fact signal for approval_required",
            "payload": {"fact_id": "approval_required", "source_ref": f"message:{ivy_message}"},
        },
        {
            "id": 15,
            "at": "2026-05-05T09:05:00",
            "phase": "runtime",
            "event_type": "agenda_signal.observed",
            "actor_id": "dana",
            "visibility": "agent",
            "summary": "Agenda cue observed for dana_accepts_staged_if_early",
            "payload": {
                "fact_id": "dana_accepts_staged_if_early",
                "owner_actor_id": "dana",
                "source_ref": f"message:{dana_message}",
            },
        },
        {
            "id": 21,
            "at": "2026-05-05T09:06:00",
            "phase": "runtime",
            "event_type": "npc.message_sent",
            "actor_id": "ivy",
            "visibility": "agent",
            "summary": "ivy sent inform.blocker",
            "payload": {"act_id": "inform.blocker", "message_id": ivy_message, "thread_id": "ivy"},
        },
    ]
    agent_trace_path = run_dir / "agent_trace.jsonl"
    omniscient_trace_path = run_dir / "omniscient_trace.jsonl"
    write_jsonl(agent_trace_path, agent_trace_rows)
    write_jsonl(omniscient_trace_path, agent_trace_rows)
    report = {
        "scenario_id": scenario_id,
        "scenario_digest": "digest",
        "compiled_coverage_digest": "digest",
        "closure_status": {"status": "passed", "passed": True},
        "time": "2026-05-05T09:22:00",
        "total_score": 15.0,
        "rubric": [
            {
                "id": "scope_aligned_on_time",
                "label": "Scope aligned on time",
                "weight": 25.0,
                "awarded": 0.0,
                "failure_class": "timing",
                "competency_tags": [
                    "outcome_attainment",
                    "timing_optionality_preservation",
                    "decision_tradeoff_management",
                ],
                "measurement_rationale": "Drive the staged path decision before the main window closes.",
                "success_meaning": "The TPM got the team onto the staged path in time.",
                "failure_meaning": "The TPM failed to converge the team on the staged path before the window closed.",
                "evidence_refs": [],
                "matched_predicates": [],
                "deadline_or_window": "scope_alignment_cutoff",
                "success_predicate": {
                    "before": {
                        "time": "2026-05-05T10:00:00",
                        "predicate": {"milestone_state": {"field": "status", "equals": "done", "milestone_id": "scope_aligned"}},
                    }
                },
            },
            {
                "id": "approval_secured_on_time",
                "label": "Approval secured before cutoff",
                "weight": 25.0,
                "awarded": 0.0,
                "failure_class": "timing",
                "competency_tags": [
                    "outcome_attainment",
                    "timing_optionality_preservation",
                    "commitment_dependency_management",
                ],
                "measurement_rationale": "Secure the approval dependency before the queue cutoff.",
                "success_meaning": "The TPM secured the key dependency on time.",
                "failure_meaning": "The TPM did not secure approval in time, leaving the path blocked.",
                "evidence_refs": [],
                "matched_predicates": [],
                "deadline_or_window": "approval_cutoff",
                "success_predicate": {
                    "before": {
                        "time": "2026-05-05T10:30:00",
                        "predicate": {"milestone_state": {"field": "status", "equals": "done", "milestone_id": "approval_secured"}},
                    }
                },
            },
            {
                "id": "rollout_ready_on_time",
                "label": "Rollout ready by Wednesday afternoon",
                "weight": 20.0,
                "awarded": 0.0,
                "failure_class": "timing",
                "competency_tags": [
                    "outcome_attainment",
                    "timing_optionality_preservation",
                    "critical_path_prioritization",
                ],
                "measurement_rationale": "Translate the decision and approval into rollout readiness.",
                "success_meaning": "The TPM made the rollout ready on time.",
                "failure_meaning": "The TPM did not convert coordination into rollout readiness quickly enough.",
                "evidence_refs": [],
                "matched_predicates": [],
                "deadline_or_window": "rollout_ready",
                "success_predicate": {
                    "before": {
                        "time": "2026-05-05T12:00:00",
                        "predicate": {"milestone_state": {"field": "status", "equals": "done", "milestone_id": "rollout_ready"}},
                    }
                },
            },
            {
                "id": "project_constraint_discovery",
                "label": "Project constraints surfaced in time",
                "weight": 10.0,
                "awarded": 10.0,
                "failure_class": "discovery",
                "competency_tags": ["discovery_situation_awareness"],
                "measurement_rationale": "Surface the key approval constraint early.",
                "success_meaning": "The TPM surfaced the hard constraint in time.",
                "failure_meaning": "The TPM missed a hard project constraint.",
                "evidence_refs": ["event:9"],
                "matched_predicates": ["surfaced:approval_required"],
                "deadline_or_window": "critical_fact_windows",
            },
            {
                "id": "stakeholder_driver_discovery",
                "label": "Stakeholder drivers surfaced in time",
                "weight": 5.0,
                "awarded": 5.0,
                "failure_class": "discovery",
                "competency_tags": ["discovery_situation_awareness"],
                "measurement_rationale": "Surface the sponsor driver early.",
                "success_meaning": "The TPM surfaced the stakeholder driver in time.",
                "failure_meaning": "The TPM missed a key stakeholder driver.",
                "evidence_refs": ["event:15"],
                "matched_predicates": ["surfaced:dana_accepts_staged_if_early"],
                "deadline_or_window": "stakeholder_driver_windows",
            },
        ],
        "failure_breakdown": {"timing": 70.0},
        "recoverability": {"scope_aligned": "none", "approval_secured": "none", "rollout_ready": "low"},
        "coverage_miss": False,
        "trace_paths": {
            "agent_trace": str(agent_trace_path),
            "omniscient_trace": str(omniscient_trace_path),
        },
        "decisive_moments": [],
    }
    payload = {
        "run": {
            "scenario_id": scenario_id,
            "scenario_digest": "digest",
            "seed": 11,
            "adapter": "scripted",
            "model": "mock-model",
            "prompt_pack_version": "test_prompt_v1",
            "max_turns": 10,
            "turns_taken": 4,
            "termination_reason": "max_turns_reached",
            "simulated_end_time": "2026-05-05T09:22:00",
            "protocol_failure": False,
            "protocol_failure_reason": None,
            "output_dir": str(run_dir),
            "report_path": str(run_dir / "benchmark_run.report.json"),
            "agent_log_path": str(run_dir / "agent_run.json"),
        },
        "decisions": [
            {
                "turn": 1,
                "observation_time": "2026-05-05T09:00:00",
                "decision": {
                    "action": {
                        "action_type": "chat.send",
                        "arguments": {"target": "ivy", "act_id": "request.approval"},
                    }
                },
                "executed_action_ref": f"action:{approval_first}",
                "step_result": {"ok": True},
                "validation_errors": [],
                "repair_attempts": 0,
            },
            {
                "turn": 2,
                "observation_time": "2026-05-05T09:07:00",
                "decision": {
                    "action": {
                        "action_type": "read.thread",
                        "arguments": {"target": "dana"},
                    }
                },
                "executed_action_ref": f"action:{dana_read}",
                "step_result": {"ok": True},
                "validation_errors": [],
                "repair_attempts": 0,
            },
            {
                "turn": 3,
                "observation_time": "2026-05-05T09:20:00",
                "decision": {
                    "action": {
                        "action_type": "chat.send",
                        "arguments": {"target": "ivy", "act_id": "request.approval"},
                    }
                },
                "executed_action_ref": f"action:{approval_second}",
                "step_result": {"ok": True},
                "validation_errors": [],
                "repair_attempts": 0,
            },
            {
                "turn": 4,
                "observation_time": "2026-05-05T09:22:00",
                "decision": {
                    "action": {
                        "action_type": "chat.send",
                        "arguments": {"target": "ivy", "act_id": "request.approval"},
                    }
                },
                "executed_action_ref": f"action:{approval_third}",
                "step_result": {"ok": True},
                "validation_errors": [],
                "repair_attempts": 0,
            },
        ],
    }
    return report, payload, summary_scenario_bundle(scenario_id)


class CaptureStructuredClient:
    def __init__(self, payload: dict[str, object]):
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def generate_text(self, prompt_spec, config):  # pragma: no cover - not used in this test
        raise AssertionError("generate_text should not be called")

    def generate_structured(self, *, schema_name, schema, prompt_spec, config):
        self.calls.append(
            {
                "schema_name": schema_name,
                "schema": schema,
                "prompt_spec": prompt_spec,
                "config": config,
            }
        )
        return ModelResponse(
            text=json.dumps(self.payload),
            raw={"fixture": True},
            usage={},
            latency_ms=0,
            refusal=None,
        )


class TextResponseClient:
    def __init__(self, text: str):
        self.text = text
        self.calls: list[dict[str, object]] = []

    def generate_text(self, prompt_spec, config):
        self.calls.append({"prompt_spec": prompt_spec, "config": config})
        return ModelResponse(
            text=self.text,
            raw={"fixture": True},
            usage={},
            latency_ms=0,
            refusal=None,
        )


class ScriptedAdapter:
    name = "scripted"
    prompt_pack_version = "test_prompt_v1"

    def __init__(self, actions: list[dict[str, object]]):
        self.actions = list(actions)

    def start(self, run_context: dict[str, object]) -> dict[str, object]:
        return {"index": 0, "run_context": run_context}

    def decide(self, session: dict[str, object], observation: dict[str, object], *, repair_feedback: str | None = None) -> AgentDecision:
        index = int(session["index"])
        session["index"] = index + 1
        if index < len(self.actions):
            payload = self.actions[index]
        else:
            payload = {
                "action_type": "wait.duration",
                "arguments": {"minutes": 600},
                "reason": "advance to the next meaningful checkpoint",
            }
        return AgentDecision(
            action=payload,
            summary=str(payload.get("reason", "")),
            raw_model_output={"scripted": True},
            usage={},
            latency_ms=0,
            validation_errors=[],
        )

    def finish(self, session: dict[str, object], final_report: dict[str, object]) -> None:
        session["final_report"] = final_report


class RepairingAdapter:
    name = "repairing"
    prompt_pack_version = "test_prompt_v1"

    def start(self, run_context: dict[str, object]) -> dict[str, object]:
        return {"calls": 0}

    def decide(self, session: dict[str, object], observation: dict[str, object], *, repair_feedback: str | None = None) -> AgentDecision:
        session["calls"] = int(session["calls"]) + 1
        if repair_feedback is None:
            payload = {
                "action_type": "not.a.real.action",
                "arguments": {},
                "reason": "bad first try",
            }
        else:
            payload = {
                "action_type": "wait.duration",
                "arguments": {"minutes": 600},
                "reason": "fixed after repair",
            }
        return AgentDecision(
            action=payload,
            summary=str(payload["reason"]),
            raw_model_output={"repair_feedback": repair_feedback},
            usage={},
            latency_ms=0,
            validation_errors=[],
        )

    def finish(self, session: dict[str, object], final_report: dict[str, object]) -> None:
        session["final_report"] = final_report


class InvalidAdapter:
    name = "invalid"
    prompt_pack_version = "test_prompt_v1"

    def start(self, run_context: dict[str, object]) -> dict[str, object]:
        return {}

    def decide(self, session: dict[str, object], observation: dict[str, object], *, repair_feedback: str | None = None) -> AgentDecision:
        return AgentDecision(
            action={"action_type": "still.invalid", "arguments": {}, "reason": "never recovers"},
            summary="never recovers",
            raw_model_output={"repair_feedback": repair_feedback},
            usage={},
            latency_ms=0,
            validation_errors=[],
        )

    def finish(self, session: dict[str, object], final_report: dict[str, object]) -> None:
        session["final_report"] = final_report


class RuntimeRepairAdapter:
    name = "runtime_repair"
    prompt_pack_version = "test_prompt_v1"

    def start(self, run_context: dict[str, object]) -> dict[str, object]:
        return {"calls": 0}

    def decide(self, session: dict[str, object], observation: dict[str, object], *, repair_feedback: str | None = None) -> AgentDecision:
        session["calls"] = int(session["calls"]) + 1
        if repair_feedback is None:
            payload = {
                "action_type": "read.thread",
                "arguments": {"target": "not-a-real-thread"},
                "reason": "bad thread target",
            }
        else:
            payload = {
                "action_type": "wait.duration",
                "arguments": {"minutes": 60},
                "reason": "fallback after runtime repair",
            }
        return AgentDecision(
            action=payload,
            summary=str(payload["reason"]),
            raw_model_output={"repair_feedback": repair_feedback},
            usage={},
            latency_ms=0,
            validation_errors=[],
        )

    def finish(self, session: dict[str, object], final_report: dict[str, object]) -> None:
        session["final_report"] = final_report


class AgentAndAuthoringTests(unittest.TestCase):
    def test_environment_session_matches_shell_for_equivalent_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            shell_db = str(Path(tmpdir) / "shell.sqlite")
            api_db = str(Path(tmpdir) / "api.sqlite")
            shell_engine, shell_evaluator = build_runtime(shell_db, "northstar_launch_week", seed=11)
            api_engine, api_evaluator = build_runtime(api_db, "northstar_launch_week", seed=11)
            api_session = EnvironmentSession(api_db, api_engine, api_evaluator)
            try:
                execute_command(shell_engine, shell_evaluator, "docs open DOC-BRIEF-001")
                execute_command(shell_engine, shell_evaluator, "chat send maya | request.feasibility | task_id=backend_api | Need the honest path.")
                execute_command(shell_engine, shell_evaluator, "wait next 600m")
                execute_command(shell_engine, shell_evaluator, "chat open maya")

                api_session.step(StructuredAction("read.doc", {"doc_id": "DOC-BRIEF-001"}))
                api_session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "maya",
                            "act_id": "request.feasibility",
                            "slots": {"task_id": "backend_api"},
                            "body": "Need the honest path.",
                        },
                    )
                )
                api_session.step(StructuredAction("wait.until_next_event", {"max_minutes": 600}))
                api_session.step(StructuredAction("read.thread", {"target": "maya"}))

                self.assertEqual(shell_engine.now(), api_engine.now())
                self.assertEqual(shell_evaluator.evaluate()["total_score"], api_evaluator.evaluate()["total_score"])
                self.assertEqual(
                    [row["event_type"] for row in shell_engine.store.event_log()],
                    [row["event_type"] for row in api_engine.store.event_log()],
                )
                self.assertEqual(
                    [row["act_id"] for row in shell_engine.store.actions()],
                    [row["act_id"] for row in api_engine.store.actions()],
                )
            finally:
                shell_engine.store.close()
                api_session.close()

    def test_openai_agent_adapter_uses_fixed_prompt_contract(self) -> None:
        client = CaptureStructuredClient(
            {
                "action_type": "read.tasks",
                "arguments": {},
                "reason": "inspect current execution state",
            }
        )
        adapter = OpenAIResponsesAgentAdapter(client, model="gpt-test")
        decision = adapter.decide({}, {"scenario_id": "northstar_launch_week", "time": "2026-05-04T09:00:00"})
        self.assertEqual(decision.action["action_type"], "read.tasks")
        self.assertEqual(len(client.calls), 1)
        call = client.calls[0]
        self.assertEqual(call["schema_name"], "tpm_next_action")
        self.assertEqual(call["prompt_spec"]["metadata"]["prompt_pack_version"], adapter.prompt_pack_version)
        self.assertEqual(call["config"]["model"], "gpt-test")
        self.assertIn("read.tasks", call["schema"]["properties"]["action_type"]["enum"])
        self.assertIn("request.approval", call["schema"]["properties"]["arguments"]["properties"]["act_id"]["enum"])

    def test_build_agent_prompt_keeps_private_notes_audit_only(self) -> None:
        prompt = build_agent_prompt({"scenario_id": "internal_rollout_smoke", "time": "2026-05-05T09:00:00"})
        system = prompt["system"]
        self.assertIn("notes.write: private scratchpad only. It has no coordination effect.", system)
        self.assertIn("follow through later", system)
        self.assertNotIn("private-note stuffing instead of coordination", system)
        self.assertNotIn("Think like a TPM, not a note-taker", system)

    def test_openai_agent_adapter_applies_gpt5_reasoning_defaults(self) -> None:
        payload = {
            "action_type": "read.tasks",
            "arguments": {},
            "reason": "inspect current execution state",
        }
        client = CaptureStructuredClient(payload)
        nano = OpenAIResponsesAgentAdapter(client, model="gpt-5-nano")
        nano.decide({}, {"scenario_id": "internal_rollout_smoke", "time": "2026-05-05T09:00:00"})
        self.assertEqual(client.calls[-1]["config"]["reasoning_effort"], "minimal")

        mini = OpenAIResponsesAgentAdapter(client, model="gpt-5-mini")
        mini.decide({}, {"scenario_id": "internal_rollout_smoke", "time": "2026-05-05T09:00:00"})
        self.assertEqual(client.calls[-1]["config"]["reasoning_effort"], "low")

        four_o = OpenAIResponsesAgentAdapter(client, model="gpt-4o")
        four_o.decide({}, {"scenario_id": "internal_rollout_smoke", "time": "2026-05-05T09:00:00"})
        self.assertNotIn("reasoning_effort", client.calls[-1]["config"])

    def test_openai_agent_adapter_allows_reasoning_override_from_environment(self) -> None:
        client = CaptureStructuredClient(
            {
                "action_type": "read.tasks",
                "arguments": {},
                "reason": "inspect current execution state",
            }
        )
        with mock.patch.dict(os.environ, {"TPM_AGENT_REASONING_EFFORT": "low"}, clear=False):
            adapter = OpenAIResponsesAgentAdapter(client, model="gpt-5-nano")
            adapter.decide({}, {"scenario_id": "internal_rollout_smoke", "time": "2026-05-05T09:00:00"})
        self.assertEqual(client.calls[-1]["config"]["reasoning_effort"], "low")

    def test_validate_structured_action_rejects_unknown_act_id(self) -> None:
        with self.assertRaises(ActionValidationError):
            validate_structured_action(
                StructuredAction(
                    "chat.send",
                    {"target": "ivy", "act_id": "reminder.send", "slots": {}, "body": "ping"},
                )
            )

    def test_validate_structured_action_rejects_malformed_note_ref(self) -> None:
        with self.assertRaises(ActionValidationError):
            validate_structured_action(
                StructuredAction(
                    "notes.write",
                    {"title": "Follow-up", "body": "Check approval path.", "refs": ["badref"]},
                )
            )

    def test_parse_script_command_accepts_notes_with_refs(self) -> None:
        parsed = parse_script_command("notes write Approval follow-up | task:approval_review,actor:ivy | Send the packet before noon.")
        self.assertIsNotNone(parsed.action)
        self.assertEqual(parsed.action.arguments["refs"], ["task:approval_review", "actor:ivy"])

    def test_validate_trajectory_script_text_accepts_golden_northstar_example(self) -> None:
        scenario = load_scenario_bundle("northstar_launch_week")["scenario"]
        errors = validate_trajectory_script_text((EXAMPLES / "golden.tpm").read_text(), script_name="golden.tpm", scenario=scenario)
        self.assertEqual(errors, [])

    def test_validate_trajectory_script_text_rejects_unsupported_uppercase_dsl(self) -> None:
        scenario = load_scenario_bundle("northstar_launch_week")["scenario"]
        errors = validate_trajectory_script_text("READ_DOC DOC-BRIEF-001\n", script_name="smoke.tpm", scenario=scenario)
        self.assertEqual(len(errors), 1)
        self.assertIn("Use `docs open DOC-ID`.", errors[0])

    def test_validate_trajectory_script_text_rejects_unknown_note_refs(self) -> None:
        scenario = {
            "world": {
                "actors": [{"id": "ivy"}],
                "documents": [{"id": "DOC-BRIEF-100"}],
                "tasks": [{"id": "approval_review"}],
                "threads": [{"id": "ivy"}],
                "meetings": [{"id": "meeting_001"}],
            }
        }
        errors = validate_trajectory_script_text(
            "notes write Approval follow-up | actor:missing,task:approval_review | Need packet.\n",
            script_name="smoke.tpm",
            scenario=scenario,
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("unknown actor ref 'actor:missing'", errors[0])

    def test_agent_runner_repair_path_succeeds_after_one_invalid_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = load_bundle_from_current_sources("internal_rollout_smoke")
            db_path = str(Path(tmpdir) / "repair.sqlite")
            session = EnvironmentSession.create_from_bundle(db_path, bundle, 11, force=True)
            try:
                record = AgentRunner(RepairingAdapter(), max_turns=3).run(
                    session,
                    seed=11,
                    output_dir=str(Path(tmpdir) / "repair_run"),
                    model_name="mock-model",
                )
            finally:
                session.close()
            self.assertFalse(record.protocol_failure)
            payload = json.loads(Path(record.agent_log_path).read_text())
            self.assertEqual(payload["run"]["prompt_pack_version"], "test_prompt_v1")
            self.assertEqual(payload["decisions"][0]["decision"]["action"]["action_type"], "wait.duration")

    def test_agent_runner_records_executed_action_ref_on_successful_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = load_bundle_from_current_sources("internal_rollout_smoke")
            db_path = str(Path(tmpdir) / "success.sqlite")
            session = EnvironmentSession.create_from_bundle(db_path, bundle, 11, force=True)
            try:
                record = AgentRunner(
                    ScriptedAdapter([{"action_type": "wait.duration", "arguments": {"minutes": 5}, "reason": "advance time"}]),
                    max_turns=1,
                ).run(
                    session,
                    seed=11,
                    output_dir=str(Path(tmpdir) / "success_run"),
                    model_name="mock-model",
                )
            finally:
                session.close()
            payload = json.loads(Path(record.agent_log_path).read_text())
            executed_action_ref = payload["decisions"][0]["executed_action_ref"]
            self.assertRegex(executed_action_ref or "", r"^action:\d+$")
            store = open_store(db_path)
            try:
                self.assertEqual(executed_action_ref, f"action:{int(store.actions()[-1]['id'])}")
            finally:
                store.close()

    def test_agent_runner_records_protocol_failure_after_second_invalid_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = load_bundle_from_current_sources("internal_rollout_smoke")
            db_path = str(Path(tmpdir) / "invalid.sqlite")
            session = EnvironmentSession.create_from_bundle(db_path, bundle, 11, force=True)
            try:
                record = AgentRunner(InvalidAdapter(), max_turns=2).run(
                    session,
                    seed=11,
                    output_dir=str(Path(tmpdir) / "invalid_run"),
                    model_name="mock-model",
                )
            finally:
                session.close()
            self.assertTrue(record.protocol_failure)
            self.assertIn("Unknown action_type", record.protocol_failure_reason or "")
            payload = json.loads(Path(record.agent_log_path).read_text())
            self.assertIsNone(payload["decisions"][0]["executed_action_ref"])
            self.assertIsNone(payload["decisions"][0]["step_result"])

    def test_agent_runner_records_coverage_miss_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = load_bundle_from_current_sources("internal_rollout_smoke")
            db_path = str(Path(tmpdir) / "coverage_miss.sqlite")
            session = EnvironmentSession.create_from_bundle(db_path, bundle, 11, force=True)
            try:
                with mock.patch.object(session, "step", side_effect=CoverageMissError("synthetic coverage miss")):
                    record = AgentRunner(ScriptedAdapter([{"action_type": "read.tasks", "arguments": {}, "reason": "probe"}]), max_turns=1).run(
                        session,
                        seed=11,
                        output_dir=str(Path(tmpdir) / "coverage_miss_run"),
                        model_name="mock-model",
                    )
            finally:
                session.close()
            self.assertTrue(record.protocol_failure)
            self.assertIn("Benchmark coverage miss", record.protocol_failure_reason or "")
            payload = json.loads(Path(record.agent_log_path).read_text())
            self.assertIsNone(payload["decisions"][0]["executed_action_ref"])
            self.assertIsNone(payload["decisions"][0]["step_result"])

    def test_agent_runner_records_turn_budget_termination(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = load_bundle_from_current_sources("internal_rollout_smoke")
            db_path = str(Path(tmpdir) / "turn_budget.sqlite")
            session = EnvironmentSession.create_from_bundle(db_path, bundle, 11, force=True)
            try:
                record = AgentRunner(
                    ScriptedAdapter(
                        [
                            {"action_type": "wait.duration", "arguments": {"minutes": 5}, "reason": "advance a little"},
                            {"action_type": "wait.duration", "arguments": {"minutes": 5}, "reason": "advance a little more"},
                        ]
                    ),
                    max_turns=2,
                ).run(
                    session,
                    seed=11,
                    output_dir=str(Path(tmpdir) / "turn_budget_run"),
                    model_name="mock-model",
                )
            finally:
                session.close()
            self.assertFalse(record.protocol_failure)
            self.assertEqual(record.termination_reason, "max_turns_reached")
            self.assertEqual(record.turns_taken, 2)
            self.assertRegex(record.simulated_end_time, r"^2026-05-05T09:10:00$")

    def test_agent_runner_records_success_criteria_met_termination(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = load_bundle_from_current_sources("internal_rollout_smoke")
            db_path = str(Path(tmpdir) / "success_criteria.sqlite")
            session = EnvironmentSession.create_from_bundle(db_path, bundle, 11, force=True)
            try:
                with mock.patch.object(session, "success_criteria_met", side_effect=[False, True]):
                    record = AgentRunner(
                        ScriptedAdapter([{"action_type": "wait.duration", "arguments": {"minutes": 5}, "reason": "advance a little"}]),
                        max_turns=3,
                    ).run(
                        session,
                        seed=11,
                        output_dir=str(Path(tmpdir) / "success_criteria_run"),
                        model_name="mock-model",
                    )
            finally:
                session.close()
            self.assertFalse(record.protocol_failure)
            self.assertEqual(record.termination_reason, "success_criteria_met")
            self.assertEqual(record.turns_taken, 1)

    def test_render_run_summary_exposes_turn_budget_termination(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report, payload, scenario_bundle = build_failure_summary_fixture(tmpdir)
            summary = build_run_summary(report, agent_payload=payload, scenario_bundle=scenario_bundle)
        rendered = render_run_summary(summary)
        self.assertIn("Score: 15 / 85 (17.65%)", rendered)
        self.assertIn("Capability verdict:", rendered)
        self.assertIn("Direct answer:", rendered)
        self.assertIn("Score breakdown:", rendered)
        self.assertIn("This benchmark uses 85 rubric points.", rendered)
        self.assertIn("Top root-cause findings:", rendered)
        self.assertLess(rendered.index("Top root-cause findings:"), rendered.index("Supporting data:"))
        self.assertIn("Explains 70 / 70 unearned points.", rendered)
        self.assertIn("Outbound stakeholder coordination: ivy 3.", rendered)
        self.assertIn("dana was a critical stakeholder and received no proactive TPM outreach.", rendered)
        self.assertIn("Reference-path divergence:", rendered)
        self.assertIn("Observed in the trace: 2 / 4", rendered)
        self.assertIn("Observed but not converted: dana_accepts_staged_if_early", rendered)
        self.assertIn("Not observed at all: full_rollout_infeasible, leo_rejects_fake_rollout_dates", rendered)
        self.assertIn("Stakeholder drivers / hidden motives:", rendered)
        self.assertIn("Dana backs staged rollout if engaged early [dana_accepts_staged_if_early]: surfaced", rendered)
        self.assertIn("TPM private notes:", rendered)
        self.assertIn("No private notes were written.", rendered)
        self.assertIn("Confidence: Low confidence. This is a single-seed directional readout", rendered)
        self.assertIn("Run end: turn budget exhausted; turns=4 / 10;", rendered)
        self.assertIn("rubric failure: Scope aligned on time lost=25.0", rendered)

    def test_render_run_summary_surfaces_private_note_follow_through(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report, payload, scenario_bundle = build_failure_summary_fixture(tmpdir)
            summary = build_run_summary(report, agent_payload=payload, scenario_bundle=scenario_bundle)
        summary["run_health"]["behavior_diagnostics"]["private_note_audit"] = {
            "total_notes_written": 2,
            "structured_notes_written": 2,
            "followed_through": 1,
            "revisited_only": 0,
            "not_followed_through": 1,
            "unscoped_notes": 0,
        }
        summary["raw_scoring_appendix"]["private_note_audit_notes"] = [
            {
                "note_doc_id": "DOC-NOTE-001",
                "note_action_ref": "action:10",
                "created_turn": 3,
                "created_at": "2026-05-05T09:10:00",
                "refs": ["task:approval_review"],
                "status": "followed_through",
                "reread_note": False,
                "reread_note_action_ref": None,
                "reread_note_at": None,
                "first_touch_action_ref": "action:11",
                "first_touch_at": "2026-05-05T09:15:00",
                "first_touch_refs": ["task:approval_review"],
                "first_non_read_touch_action_ref": "action:11",
                "first_non_read_touch_at": "2026-05-05T09:15:00",
                "first_non_read_touch_refs": ["task:approval_review"],
                "touch_action_refs": ["action:11"],
            },
            {
                "note_doc_id": "DOC-NOTE-002",
                "note_action_ref": "action:12",
                "created_turn": 4,
                "created_at": "2026-05-05T09:20:00",
                "refs": ["actor:ivy"],
                "status": "not_followed_through",
                "reread_note": False,
                "reread_note_action_ref": None,
                "reread_note_at": None,
                "first_touch_action_ref": None,
                "first_touch_at": None,
                "first_touch_refs": [],
                "first_non_read_touch_action_ref": None,
                "first_non_read_touch_at": None,
                "first_non_read_touch_refs": [],
                "touch_action_refs": [],
            },
        ]
        rendered = render_run_summary(summary)
        self.assertIn("Written=2; structured=2; followed through=1; revisited only=0; not followed through=1; unscoped=0.", rendered)
        self.assertIn("DOC-NOTE-001: followed through; influenced later work via action:11; refs: task:approval_review.", rendered)
        self.assertIn("DOC-NOTE-002: not followed through; refs: actor:ivy.", rendered)

    def test_scripted_agent_runner_can_complete_internal_rollout_smoke(self) -> None:
        actions = [
            {"action_type": "read.doc", "arguments": {"doc_id": "DOC-BRIEF-100"}, "reason": "read the kickoff brief"},
            {
                "action_type": "chat.send",
                "arguments": {
                    "target": "leo",
                    "act_id": "request.scope_tradeoff",
                    "slots": {"task_id": "config_rollout"},
                    "body": "If full rollout is not credible, what staged path is actually workable this week?"
                },
                "reason": "get the credible staged path from engineering"
            },
            {"action_type": "wait.until_next_event", "arguments": {"max_minutes": 180}, "reason": "wait for Leo's scope response"},
            {"action_type": "read.thread", "arguments": {"target": "leo"}, "reason": "read Leo's scope response"},
            {
                "action_type": "chat.send",
                "arguments": {
                    "target": "leo",
                    "act_id": "negotiate.scope",
                    "slots": {"proposed_scope": "staged_rollout"},
                    "body": "Let's commit to staged rollout as the real path."
                },
                "reason": "lock engineering onto staged scope"
            },
            {
                "action_type": "chat.send",
                "arguments": {
                    "target": "dana",
                    "act_id": "request.scope_tradeoff",
                    "slots": {"task_id": "config_rollout"},
                    "body": "Engineering is saying full rollout is not credible. Will you back staged rollout today?"
                },
                "reason": "get sponsor backing on the real tradeoff"
            },
            {"action_type": "wait.until_next_event", "arguments": {"max_minutes": 240}, "reason": "wait for Dana"},
            {"action_type": "read.thread", "arguments": {"target": "dana"}, "reason": "read Dana's tradeoff response"},
            {
                "action_type": "chat.send",
                "arguments": {
                    "target": "leo",
                    "act_id": "inform.decision",
                    "slots": {"decision_key": "launch_scope", "decision_value": "staged_rollout"},
                    "body": "We are aligning on staged rollout."
                },
                "reason": "make the staged decision explicit to engineering"
            },
            {
                "action_type": "chat.send",
                "arguments": {
                    "target": "dana",
                    "act_id": "inform.decision",
                    "slots": {"decision_key": "launch_scope", "decision_value": "staged_rollout"},
                    "body": "We are aligning on staged rollout and I need your support on that tradeoff."
                },
                "reason": "make the staged decision explicit to the sponsor"
            },
            {
                "action_type": "chat.send",
                "arguments": {
                    "target": "ivy",
                    "act_id": "request.review",
                    "slots": {"task_id": "approval_review"},
                    "body": "Staged rollout is the path. Please review the concrete staged request before noon."
                },
                "reason": "secure approval before the cutoff"
            },
            {"action_type": "wait.until_next_event", "arguments": {"max_minutes": 240}, "reason": "wait for the approval reply"},
            {"action_type": "read.thread", "arguments": {"target": "ivy"}, "reason": "read the approval response"},
            {
                "action_type": "chat.send",
                "arguments": {
                    "target": "mia",
                    "act_id": "request.feasibility",
                    "slots": {"task_id": "runbook_readiness"},
                    "body": "If we go staged, is ops support feasible from your side?"
                },
                "reason": "close the small readiness side path"
            },
            {
                "action_type": "chat.send",
                "arguments": {
                    "target": "leo",
                    "act_id": "request.eta",
                    "slots": {"task_id": "config_rollout"},
                    "body": "With staged scope and approval ready, what is the credible ETA?"
                },
                "reason": "convert the aligned plan into a concrete ETA"
            },
            {"action_type": "wait.duration", "arguments": {"minutes": 600}, "reason": "let the remaining work land"}
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = load_bundle_from_current_sources("internal_rollout_smoke")
            db_path = str(Path(tmpdir) / "scripted.sqlite")
            session = EnvironmentSession.create_from_bundle(db_path, bundle, 11, force=True)
            try:
                record = AgentRunner(ScriptedAdapter(actions), max_turns=25).run(
                    session,
                    seed=11,
                    output_dir=str(Path(tmpdir) / "scripted_run"),
                    model_name="scripted-agent",
                )
            finally:
                session.close()
            self.assertGreaterEqual(record.score, 90.0)
            self.assertFalse(record.protocol_failure)

    def test_export_run_summary_builds_canonical_tpm_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report, payload, scenario_bundle = build_failure_summary_fixture(tmpdir)
            run_dir = Path(payload["run"]["output_dir"])
            Path(payload["run"]["report_path"]).write_text(json.dumps(report, indent=2, sort_keys=True))
            Path(payload["run"]["agent_log_path"]).write_text(json.dumps(payload, indent=2, sort_keys=True))
            with mock.patch("tpm_sim.performance.load_scenario_bundle", return_value=scenario_bundle):
                summary = export_run_summary(run_dir, write_files=True)
            self.assertEqual(summary["schema_version"], "tpm_performance_summary_v3")
            self.assertIn("capability_assessment", summary)
            self.assertIn("score_breakdown", summary)
            self.assertIn("root_cause_findings", summary)
            self.assertIn("stakeholder_engagement", summary)
            self.assertIn("signal_coverage", summary)
            self.assertIn("window_scorecards", summary)
            self.assertIn("missed_opportunities", summary)
            self.assertIn("reference_path_diff", summary)
            self.assertIn("evidence_catalog", summary)
            self.assertIn("tpm_competency_profile", summary)
            self.assertIn("run_health", summary)
            self.assertEqual(summary["narrative"]["source"], "deterministic_template")
            self.assertTrue((run_dir / "tpm_performance_summary.json").exists())
            self.assertTrue((run_dir / "judge_input_bundle.json").exists())

    def test_export_run_summary_resolves_paths_after_run_directory_move(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report, payload, scenario_bundle = build_failure_summary_fixture(tmpdir)
            original_run_dir = Path(payload["run"]["output_dir"])
            Path(payload["run"]["report_path"]).write_text(json.dumps(report, indent=2, sort_keys=True))
            Path(payload["run"]["agent_log_path"]).write_text(json.dumps(payload, indent=2, sort_keys=True))
            moved_run_dir = Path(tmpdir) / "moved_failure_run"
            original_run_dir.rename(moved_run_dir)

            with mock.patch("tpm_sim.performance.load_scenario_bundle", return_value=scenario_bundle):
                summary = export_run_summary(moved_run_dir, write_files=True)

            self.assertEqual(summary["run_header"]["report_path"], str(moved_run_dir / "benchmark_run.report.json"))
            repaired_run = json.loads((moved_run_dir / "agent_run.json").read_text())["run"]
            repaired_report = json.loads((moved_run_dir / "benchmark_run.report.json").read_text())
            self.assertEqual(repaired_run["output_dir"], str(moved_run_dir))
            self.assertEqual(repaired_run["report_path"], str(moved_run_dir / "benchmark_run.report.json"))
            self.assertEqual(repaired_run["agent_log_path"], str(moved_run_dir / "agent_run.json"))
            self.assertEqual(repaired_report["trace_paths"]["agent_trace"], str(moved_run_dir / "agent_trace.jsonl"))
            self.assertEqual(repaired_report["trace_paths"]["omniscient_trace"], str(moved_run_dir / "omniscient_trace.jsonl"))

    def test_merge_decision_action_rows_prefers_executed_action_refs(self) -> None:
        action_rows = [
            {"id": 1, "actor_id": "tpm", "surface": "system", "act_id": "wait", "slots": {"minutes": 5}, "duration_minutes": 5, "metadata": {}},
            {"id": 2, "actor_id": "tpm", "surface": "system", "act_id": "wait", "slots": {"minutes": 10}, "duration_minutes": 10, "metadata": {}},
        ]
        payload = {
            "decisions": [
                {
                    "turn": 1,
                    "observation_time": "2026-05-05T09:00:00",
                    "decision": {"action": {"action_type": "wait.duration", "arguments": {"minutes": 5}}},
                    "executed_action_ref": "action:2",
                    "step_result": {"ok": True},
                    "validation_errors": [],
                    "repair_attempts": 0,
                },
                {
                    "turn": 2,
                    "observation_time": "2026-05-05T09:05:00",
                    "decision": {"action": {"action_type": "wait.duration", "arguments": {"minutes": 10}}},
                    "executed_action_ref": "action:1",
                    "step_result": {"ok": True},
                    "validation_errors": [],
                    "repair_attempts": 0,
                },
            ]
        }
        merged = _merge_decision_action_rows(payload, action_rows)
        self.assertEqual(merged[0]["action_id"], 2)
        self.assertEqual(merged[0]["action_ref"], "action:2")
        self.assertEqual(merged[1]["action_id"], 1)
        self.assertEqual(merged[1]["action_ref"], "action:1")

    def test_merge_decision_action_rows_falls_back_for_legacy_runs(self) -> None:
        action_rows = [
            {"id": 7, "actor_id": "tpm", "surface": "system", "act_id": "wait", "slots": {"minutes": 5}, "duration_minutes": 5, "metadata": {}},
            {"id": 8, "actor_id": "tpm", "surface": "system", "act_id": "wait", "slots": {"minutes": 10}, "duration_minutes": 10, "metadata": {}},
        ]
        payload = {
            "decisions": [
                {
                    "turn": 1,
                    "observation_time": "2026-05-05T09:00:00",
                    "decision": {"action": {"action_type": "wait.duration", "arguments": {"minutes": 5}}},
                    "step_result": {"ok": True},
                    "validation_errors": [],
                    "repair_attempts": 0,
                },
                {
                    "turn": 2,
                    "observation_time": "2026-05-05T09:05:00",
                    "decision": {"action": {"action_type": "wait.duration", "arguments": {"minutes": 10}}},
                    "step_result": {"ok": True},
                    "validation_errors": [],
                    "repair_attempts": 0,
                },
            ]
        }
        merged = _merge_decision_action_rows(payload, action_rows)
        self.assertEqual([row["action_id"] for row in merged], [7, 8])

    def test_notes_write_persists_refs_in_document_and_action_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = load_bundle_from_current_sources("internal_rollout_smoke")
            db_path = str(Path(tmpdir) / "notes.sqlite")
            session = EnvironmentSession.create_from_bundle(db_path, bundle, 11, force=True)
            try:
                session.step(
                    StructuredAction(
                        "notes.write",
                        {
                            "title": "Approval follow-up",
                            "body": "Need Ivy packet before noon.",
                            "refs": ["task:approval_review", "actor:ivy"],
                        },
                    )
                )
                action_row = session.engine.store.actions()[-1]
                action_metadata = session.engine.deserialize(action_row["metadata_json"], {})
                note_id = action_metadata["doc_id"]
                note_row = session.engine.store.get_document(note_id)
                note_metadata = session.engine.deserialize(note_row["metadata_json"], {})
            finally:
                session.close()
        self.assertEqual(action_metadata["refs"], ["task:approval_review", "actor:ivy"])
        self.assertEqual(note_metadata["refs"], ["task:approval_review", "actor:ivy"])

    def test_notes_write_rejects_unknown_runtime_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = load_bundle_from_current_sources("internal_rollout_smoke")
            db_path = str(Path(tmpdir) / "notes_invalid.sqlite")
            session = EnvironmentSession.create_from_bundle(db_path, bundle, 11, force=True)
            try:
                with self.assertRaises(ActionValidationError):
                    session.step(
                        StructuredAction(
                            "notes.write",
                            {"title": "Missing task", "body": "Track this later.", "refs": ["task:not_real"]},
                        )
                    )
            finally:
                session.close()

    def test_build_behavior_diagnostics_tracks_private_note_audit_statuses(self) -> None:
        scenario = summary_scenario_bundle()["scenario"]
        action_rows = [
            {
                "turn": 1,
                "time": "2026-05-05T09:00:00",
                "action_type": "notes.write",
                "act_id": "note.write",
                "target": None,
                "task_id": None,
                "doc_id": "DOC-NOTE-001",
                "meeting_id": None,
                "refs": ["task:approval_review"],
                "validation_errors": [],
                "repair_attempts": 0,
                "step_succeeded": True,
                "action_ref": "action:1",
                "surface": "notes",
                "duration_minutes": 5,
                "slots": {"doc_id": "DOC-NOTE-001"},
                "metadata": {"doc_id": "DOC-NOTE-001", "refs": ["task:approval_review"]},
                "thread_id": None,
                "target_actor_id": None,
            },
            {
                "turn": 2,
                "time": "2026-05-05T09:05:00",
                "action_type": "task.note",
                "act_id": "task.note",
                "target": None,
                "task_id": "approval_review",
                "doc_id": None,
                "meeting_id": None,
                "refs": [],
                "validation_errors": [],
                "repair_attempts": 0,
                "step_succeeded": True,
                "action_ref": "action:2",
                "surface": "tasks",
                "duration_minutes": 3,
                "slots": {"task_id": "approval_review"},
                "metadata": {},
                "thread_id": None,
                "target_actor_id": None,
            },
            {
                "turn": 3,
                "time": "2026-05-05T09:10:00",
                "action_type": "notes.write",
                "act_id": "note.write",
                "target": None,
                "task_id": None,
                "doc_id": "DOC-NOTE-002",
                "meeting_id": None,
                "refs": ["actor:ivy"],
                "validation_errors": [],
                "repair_attempts": 0,
                "step_succeeded": True,
                "action_ref": "action:3",
                "surface": "notes",
                "duration_minutes": 5,
                "slots": {"doc_id": "DOC-NOTE-002"},
                "metadata": {"doc_id": "DOC-NOTE-002", "refs": ["actor:ivy"]},
                "thread_id": None,
                "target_actor_id": None,
            },
            {
                "turn": 4,
                "time": "2026-05-05T09:12:00",
                "action_type": "read.thread",
                "act_id": "read.thread",
                "target": "ivy",
                "task_id": None,
                "doc_id": None,
                "meeting_id": None,
                "refs": [],
                "validation_errors": [],
                "repair_attempts": 0,
                "step_succeeded": True,
                "action_ref": "action:4",
                "surface": "chat",
                "duration_minutes": 2,
                "slots": {"thread_id": "ivy"},
                "metadata": {"thread_id": "ivy", "target_actor_id": "ivy"},
                "thread_id": "ivy",
                "target_actor_id": "ivy",
            },
            {
                "turn": 5,
                "time": "2026-05-05T09:15:00",
                "action_type": "notes.write",
                "act_id": "note.write",
                "target": None,
                "task_id": None,
                "doc_id": "DOC-NOTE-003",
                "meeting_id": None,
                "refs": ["doc:DOC-BRIEF-100"],
                "validation_errors": [],
                "repair_attempts": 0,
                "step_succeeded": True,
                "action_ref": "action:5",
                "surface": "notes",
                "duration_minutes": 5,
                "slots": {"doc_id": "DOC-NOTE-003"},
                "metadata": {"doc_id": "DOC-NOTE-003", "refs": ["doc:DOC-BRIEF-100"]},
                "thread_id": None,
                "target_actor_id": None,
            },
            {
                "turn": 6,
                "time": "2026-05-05T09:17:00",
                "action_type": "read.doc",
                "act_id": "read.doc",
                "target": None,
                "task_id": None,
                "doc_id": "DOC-NOTE-003",
                "meeting_id": None,
                "refs": [],
                "validation_errors": [],
                "repair_attempts": 0,
                "step_succeeded": True,
                "action_ref": "action:6",
                "surface": "docs",
                "duration_minutes": 5,
                "slots": {"doc_id": "DOC-NOTE-003"},
                "metadata": {"doc_id": "DOC-NOTE-003"},
                "thread_id": None,
                "target_actor_id": None,
            },
            {
                "turn": 7,
                "time": "2026-05-05T09:20:00",
                "action_type": "notes.write",
                "act_id": "note.write",
                "target": None,
                "task_id": None,
                "doc_id": "DOC-NOTE-004",
                "meeting_id": None,
                "refs": [],
                "validation_errors": [],
                "repair_attempts": 0,
                "step_succeeded": True,
                "action_ref": "action:7",
                "surface": "notes",
                "duration_minutes": 5,
                "slots": {"doc_id": "DOC-NOTE-004"},
                "metadata": {"doc_id": "DOC-NOTE-004", "refs": []},
                "thread_id": None,
                "target_actor_id": None,
            },
        ]
        diagnostics = build_behavior_diagnostics(action_rows, [], scenario)
        audit = diagnostics["private_note_audit"]
        self.assertEqual(audit["total_notes_written"], 4)
        self.assertEqual(audit["structured_notes_written"], 3)
        self.assertEqual(audit["followed_through"], 1)
        self.assertEqual(audit["revisited_only"], 1)
        self.assertEqual(audit["not_followed_through"], 1)
        self.assertEqual(audit["unscoped_notes"], 1)
        by_note = {item["note_doc_id"]: item for item in diagnostics["private_note_audit_rows"]}
        self.assertEqual(by_note["DOC-NOTE-001"]["status"], "followed_through")
        self.assertEqual(by_note["DOC-NOTE-002"]["status"], "revisited_only")
        self.assertEqual(by_note["DOC-NOTE-003"]["status"], "not_followed_through")
        self.assertTrue(by_note["DOC-NOTE-003"]["reread_note"])
        self.assertEqual(by_note["DOC-NOTE-004"]["status"], "unscoped")

    def test_build_behavior_diagnostics_excludes_private_notes_from_artifact_churn(self) -> None:
        scenario = summary_scenario_bundle()["scenario"]
        action_rows = [
            {
                "turn": 1,
                "time": "2026-05-05T09:00:00",
                "action_type": "notes.write",
                "act_id": "note.write",
                "target": None,
                "task_id": None,
                "doc_id": "DOC-NOTE-010",
                "meeting_id": None,
                "refs": ["task:approval_review"],
                "validation_errors": [],
                "repair_attempts": 0,
                "step_succeeded": True,
                "action_ref": "action:10",
                "surface": "notes",
                "duration_minutes": 5,
                "slots": {"doc_id": "DOC-NOTE-010"},
                "metadata": {"doc_id": "DOC-NOTE-010", "refs": ["task:approval_review"]},
                "thread_id": None,
                "target_actor_id": None,
            },
            {
                "turn": 2,
                "time": "2026-05-05T09:05:00",
                "action_type": "notes.write",
                "act_id": "note.write",
                "target": None,
                "task_id": None,
                "doc_id": "DOC-NOTE-011",
                "meeting_id": None,
                "refs": ["actor:ivy"],
                "validation_errors": [],
                "repair_attempts": 0,
                "step_succeeded": True,
                "action_ref": "action:11",
                "surface": "notes",
                "duration_minutes": 5,
                "slots": {"doc_id": "DOC-NOTE-011"},
                "metadata": {"doc_id": "DOC-NOTE-011", "refs": ["actor:ivy"]},
                "thread_id": None,
                "target_actor_id": None,
            },
            {
                "turn": 3,
                "time": "2026-05-05T09:10:00",
                "action_type": "docs.write",
                "act_id": "docs.write",
                "target": None,
                "task_id": None,
                "doc_id": "DOC-PLAN-001",
                "meeting_id": None,
                "refs": [],
                "validation_errors": [],
                "repair_attempts": 0,
                "step_succeeded": True,
                "action_ref": "action:12",
                "surface": "docs",
                "duration_minutes": 15,
                "slots": {"doc_id": "DOC-PLAN-001"},
                "metadata": {"doc_id": "DOC-PLAN-001"},
                "thread_id": None,
                "target_actor_id": None,
            },
        ]
        diagnostics = build_behavior_diagnostics(action_rows, [], scenario)
        self.assertEqual(diagnostics["counts"]["artifact_churn"], 1)
        self.assertEqual(diagnostics["private_note_audit"]["total_notes_written"], 2)

    def test_run_summary_surfaces_new_driver_discovery_lines_when_relevant(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report, payload, scenario_bundle = build_failure_summary_fixture(tmpdir)
            summary = build_run_summary(report, agent_payload=payload, scenario_bundle=scenario_bundle)
        success_ids = {item["id"] for item in summary["key_successes"]}
        self.assertIn("project_constraint_discovery", success_ids)
        self.assertIn("stakeholder_driver_discovery", success_ids)

    def test_northstar_driver_signal_conversion_tracks_maya_feasibility_follow_up(self) -> None:
        scenario = load_bundle_from_current_sources("northstar_launch_week")["scenario"]
        visible_trace = [
            {
                "id": 1,
                "at": "2026-05-05T09:00:00",
                "event_type": "fact_signal",
                "payload": {"fact_id": "maya_rejects_fake_dates_under_pressure", "observer_id": "tpm"},
            }
        ]
        merged_action_rows = [
            {
                "turn": 2,
                "time": "2026-05-05T09:05:00",
                "action_type": "chat.send",
                "act_id": "request.feasibility",
                "target": "maya",
                "task_id": "backend_api",
                "doc_id": None,
                "meeting_id": None,
                "refs": [],
                "validation_errors": [],
                "repair_attempts": 0,
                "step_succeeded": True,
                "executed_action_ref": "action:1",
                "action_id": 1,
                "action_ref": "action:1",
                "surface": "chat",
                "duration_minutes": 10,
                "body": "Need the honest path and blockers.",
                "slots": {"task_id": "backend_api"},
                "metadata": {"target_actor_id": "maya"},
                "thread_id": "maya",
                "target_actor_id": "maya",
            }
        ]
        coverage = _build_signal_coverage(scenario, visible_trace, merged_action_rows)
        maya_row = next(
            row for row in coverage["signals"] if row["signal_id"] == "maya_rejects_fake_dates_under_pressure"
        )
        self.assertEqual(maya_row["kind"], "driver")
        self.assertEqual(maya_row["expected_actors"], ["maya"])
        self.assertIn("feasibility_alignment", maya_row["expected_action_families"])
        self.assertTrue(maya_row["converted_to_plan_change"])
        self.assertEqual(maya_row["conversion_action_refs"], ["action:1"])

    def test_build_run_summary_surfaces_behavior_centric_findings_with_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report, payload, scenario_bundle = build_failure_summary_fixture(tmpdir)
            summary = build_run_summary(report, agent_payload=payload, scenario_bundle=scenario_bundle)
        self.assertEqual(summary["score_breakdown"]["total_possible"], 85.0)
        self.assertEqual(summary["score_breakdown"]["total_awarded"], 15.0)
        self.assertEqual(summary["score_breakdown"]["total_unearned"], 70.0)
        findings = summary["root_cause_findings"]
        self.assertGreaterEqual(len(findings), 4)
        self.assertEqual(findings[0]["id"], "cue_not_converted_to_plan_change")
        self.assertEqual(findings[0]["lost_points_total"], 70.0)
        self.assertEqual(
            [item["id"] for item in findings[0]["impacted_rubric_lines"]],
            ["scope_aligned_on_time", "approval_secured_on_time", "rollout_ready_on_time"],
        )
        ids = {item["id"] for item in findings}
        self.assertIn("wrong_precondition_sequence", ids)
        self.assertIn("critical_decision_owner_omission", ids)
        self.assertIn("single_threaded_approver_loop", ids)
        omission = next(item for item in findings if item["id"] == "critical_decision_owner_omission")
        self.assertTrue(any(ref.startswith("message:") for ref in omission["signal_refs"]))
        self.assertTrue(any(ref.startswith("action:") for ref in omission["action_refs"]))

        stakeholders = summary["stakeholder_engagement"]
        self.assertEqual(stakeholders["summary_metrics"]["critical_actors_never_contacted"], ["dana", "leo"])
        dana_row = next(item for item in stakeholders["actors"] if item["actor_id"] == "dana")
        self.assertEqual(dana_row["unanswered_direct_questions"], ["message:1"])
        self.assertIsNone(dana_row["first_outbound_at"])

        reference = summary["reference_path_diff"]
        self.assertIsNotNone(reference)
        self.assertEqual(reference["reference_id"], "smoke.tpm")
        self.assertEqual(reference["first_divergence_action_ref"], "action:1")
        self.assertEqual(reference["expected_step"], "docs open DOC-BRIEF-100")
        self.assertTrue(str(reference["actual_step"]).startswith("chat.send/request.approval"))

        evidence_refs = {item["evidence_ref"] for item in summary["evidence_catalog"]}
        self.assertIn("message:1", evidence_refs)
        self.assertIn("message:2", evidence_refs)
        self.assertIn("action:1", evidence_refs)
        self.assertIn("event:15", evidence_refs)

    def test_build_run_summary_merges_missing_signal_events_from_omniscient_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report, payload, scenario_bundle = build_failure_summary_fixture(tmpdir)
            agent_trace_path = Path(report["trace_paths"]["agent_trace"])
            omniscient_rows = [json.loads(line) for line in Path(report["trace_paths"]["omniscient_trace"]).read_text().splitlines() if line.strip()]
            agent_only_rows = [row for row in omniscient_rows if row["event_type"] == "npc.message_sent"]
            write_jsonl(agent_trace_path, agent_only_rows)
            summary = build_run_summary(report, agent_payload=payload, scenario_bundle=scenario_bundle)
        self.assertEqual(summary["signal_coverage"]["summary_metrics"]["critical_observed"], 2)
        self.assertEqual(
            summary["signal_coverage"]["summary_metrics"]["critical_not_observed"],
            ["full_rollout_infeasible", "leo_rejects_fake_rollout_dates"],
        )

    def test_build_run_summary_marks_coverage_interruptions_as_run_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report, payload, scenario_bundle = build_failure_summary_fixture(tmpdir)
            report["coverage_miss"] = True
            payload["decisions"] = []
            payload["run"]["turns_taken"] = 0
            payload["run"]["termination_reason"] = "protocol_failure"
            summary = build_run_summary(report, agent_payload=payload, scenario_bundle=scenario_bundle)
        self.assertEqual(summary["rubric_failure_appendix"][0]["kind"], "run_interruption")
        self.assertEqual(summary["run_health"]["overall_status"], "protocol_failure")
        self.assertEqual(summary["run_health"]["model_status"], "clean")
        self.assertEqual(summary["run_health"]["harness_status"], "attention_needed")
        self.assertIn("coverage miss", summary["rubric_failure_appendix"][0]["why_it_matters"].lower())

    def test_build_run_summary_passes_dossier_refs_to_judge_and_falls_back_on_invalid_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report, payload, scenario_bundle = build_failure_summary_fixture(tmpdir)
            baseline_summary = build_run_summary(report, agent_payload=payload, scenario_bundle=scenario_bundle)
            allowed_refs = baseline_summary["judge_input_bundle"]["allowed_evidence_refs"]
            self.assertTrue(any(ref.startswith("event:") for ref in allowed_refs))
            self.assertTrue(any(ref.startswith("action:") for ref in allowed_refs))
            self.assertTrue(any(ref.startswith("message:") for ref in allowed_refs))
            self.assertIn("capability_assessment", baseline_summary["judge_input_bundle"])
            self.assertIn("root_cause_findings", baseline_summary["judge_input_bundle"])
            self.assertIn("stakeholder_engagement", baseline_summary["judge_input_bundle"])
            self.assertIn("reference_path_diff", baseline_summary["judge_input_bundle"])

            client = CaptureStructuredClient(
                {
                    "direct_answer": "Invalid evidence ref should trigger deterministic fallback.",
                    "executive_summary": "Invalid evidence ref should trigger deterministic fallback.",
                    "top_findings": [
                        {
                            "title": "Approval loop",
                            "explanation": "The model kept asking for approval and missed the real path.",
                            "evidence_refs": ["action:9999"],
                        }
                    ],
                    "counterfactual_path": [],
                    "supporting_data": [],
                    "limitations": [],
                }
            )
            judged_summary = build_run_summary(
                report,
                agent_payload=payload,
                scenario_bundle=scenario_bundle,
                judge_client=client,
                judge_model="judge-model",
            )
        self.assertEqual(len(client.calls), 1)
        prompt_bundle = json.loads(client.calls[0]["prompt_spec"]["user"])
        self.assertIn("score_breakdown", prompt_bundle)
        self.assertIn("capability_assessment", prompt_bundle)
        self.assertIn("missed_opportunities", prompt_bundle)
        self.assertIn("evidence_catalog", prompt_bundle)
        self.assertEqual(judged_summary["narrative"]["source"], "deterministic_template")

    def test_build_run_summary_accepts_valid_v3_judge_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report, payload, scenario_bundle = build_failure_summary_fixture(tmpdir)
            baseline_summary = build_run_summary(report, agent_payload=payload, scenario_bundle=scenario_bundle)
            client = CaptureStructuredClient(
                {
                    "direct_answer": "The model performed poorly as a TPM because it never turned sponsor and approver cues into the right decision path.",
                    "executive_summary": "The run stayed stuck in an approval loop instead of aligning the staged rollout path.",
                    "top_findings": [
                        {
                            "title": "Approval loop",
                            "explanation": "The TPM kept pushing Ivy without answering Dana or engaging Leo.",
                            "evidence_refs": ["message:1", "action:1"],
                        }
                    ],
                    "counterfactual_path": [
                        {
                            "title": "Stage the path first",
                            "explanation": "Answer Dana, align Leo on staged scope, then return to Ivy with a complete intake.",
                            "evidence_refs": ["message:2", "event:15"],
                        }
                    ],
                    "supporting_data": [
                        {
                            "title": "Critical actor omission",
                            "explanation": "Dana and Leo were never contacted even though both were needed to make the request approvable.",
                            "evidence_refs": ["message:1"],
                        }
                    ],
                    "limitations": [
                        {
                            "title": "Single run",
                            "explanation": "This is still a single-seed readout.",
                            "evidence_refs": [],
                        }
                    ],
                }
            )
            judged_summary = build_run_summary(
                report,
                agent_payload=payload,
                scenario_bundle=scenario_bundle,
                judge_client=client,
                judge_model="judge-model",
            )
        self.assertEqual(judged_summary["narrative"]["source"], "llm_judge")
        self.assertEqual(judged_summary["narrative"]["model"], "judge-model")
        self.assertEqual(
            judged_summary["narrative"]["direct_answer"],
            "The model performed poorly as a TPM because it never turned sponsor and approver cues into the right decision path.",
        )
        self.assertEqual(judged_summary["run_header"]["score"], 15.0)
        self.assertEqual(judged_summary["outcome_verdict"]["headline"], baseline_summary["outcome_verdict"]["headline"])

    def test_export_bundle_summary_aggregates_run_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_summaries = [
                {
                    "schema_version": "tpm_performance_summary_v3",
                    "run_header": {"seed": 11, "score": 55, "scenario_id": "internal_rollout_smoke", "summary_path": "a"},
                    "score_breakdown": {"total_possible": 100.0},
                    "outcome_verdict": {"headline": "partial"},
                    "capability_assessment": {"rating": "mixed"},
                    "root_cause_findings": [{"id": "wrong_precondition_sequence", "title": "Wrong Precondition Sequence", "lost_points_total": 50.0}],
                    "stakeholder_engagement": {
                        "actors": [
                            {"actor_id": "dana", "unanswered_direct_questions": ["message:1"]},
                            {"actor_id": "leo", "unanswered_direct_questions": []},
                        ],
                        "summary_metrics": {
                            "critical_actors_never_contacted": ["leo"],
                            "critical_actors_contacted_after_deadline": [],
                            "direct_questions_left_unanswered": ["message:1"],
                        },
                    },
                    "signal_coverage": {
                        "signals": [
                            {
                                "signal_id": "approval_required",
                                "label": "Approval is required for the staged rollout",
                                "kind": "fact",
                                "criticality": "critical",
                                "surfaced": True,
                                "converted_to_plan_change": True,
                            },
                            {
                                "signal_id": "dana_accepts_staged_if_early",
                                "label": "Dana backs staged rollout if engaged early",
                                "kind": "driver",
                                "criticality": "critical",
                                "surfaced": True,
                                "converted_to_plan_change": False,
                            },
                            {
                                "signal_id": "full_rollout_infeasible",
                                "label": "Full rollout is not credible this week",
                                "kind": "fact",
                                "criticality": "critical",
                                "surfaced": False,
                                "converted_to_plan_change": False,
                            },
                        ]
                    },
                    "window_scorecards": [
                        {"window_id": "approval_cutoff", "state_achieved": {"achieved": False}},
                    ],
                    "reference_path_diff": {"expected_step": "docs open DOC-BRIEF-100", "actual_step": "chat.send ivy"},
                    "tpm_competency_profile": [{"id": "discovery_situation_awareness", "label": "Discovery & Situation Awareness", "score": 80}],
                    "outcome_profile": [{"id": "outcome_attainment", "label": "Outcome Attainment", "score": 40}],
                    "run_health": {
                        "protocol_failure": False,
                        "coverage_miss": False,
                        "harness_interface_issues": [],
                        "scenario_authoring_issues": [],
                        "behavior_diagnostics": {
                            "private_note_audit": {
                                "total_notes_written": 2,
                                "structured_notes_written": 2,
                                "followed_through": 1,
                                "revisited_only": 1,
                                "not_followed_through": 0,
                                "unscoped_notes": 0,
                            }
                        },
                    },
                    "key_failures": [{"id": "scope_aligned_on_time"}],
                },
                {
                    "schema_version": "tpm_performance_summary_v3",
                    "run_header": {"seed": 29, "score": 45, "scenario_id": "internal_rollout_smoke", "summary_path": "b"},
                    "score_breakdown": {"total_possible": 100.0},
                    "outcome_verdict": {"headline": "failed"},
                    "capability_assessment": {"rating": "poor"},
                    "root_cause_findings": [{"id": "wrong_precondition_sequence", "title": "Wrong Precondition Sequence", "lost_points_total": 70.0}],
                    "stakeholder_engagement": {
                        "actors": [
                            {"actor_id": "dana", "unanswered_direct_questions": []},
                            {"actor_id": "leo", "unanswered_direct_questions": []},
                        ],
                        "summary_metrics": {
                            "critical_actors_never_contacted": ["leo"],
                            "critical_actors_contacted_after_deadline": [],
                            "direct_questions_left_unanswered": [],
                        },
                    },
                    "signal_coverage": {
                        "signals": [
                            {
                                "signal_id": "approval_required",
                                "label": "Approval is required for the staged rollout",
                                "kind": "fact",
                                "criticality": "critical",
                                "surfaced": True,
                                "converted_to_plan_change": True,
                            },
                            {
                                "signal_id": "dana_accepts_staged_if_early",
                                "label": "Dana backs staged rollout if engaged early",
                                "kind": "driver",
                                "criticality": "critical",
                                "surfaced": False,
                                "converted_to_plan_change": False,
                            },
                            {
                                "signal_id": "full_rollout_infeasible",
                                "label": "Full rollout is not credible this week",
                                "kind": "fact",
                                "criticality": "critical",
                                "surfaced": False,
                                "converted_to_plan_change": False,
                            },
                        ]
                    },
                    "window_scorecards": [
                        {"window_id": "approval_cutoff", "state_achieved": {"achieved": False}},
                    ],
                    "reference_path_diff": {"expected_step": "docs open DOC-BRIEF-100", "actual_step": "chat.send ivy"},
                    "tpm_competency_profile": [{"id": "discovery_situation_awareness", "label": "Discovery & Situation Awareness", "score": 60}],
                    "outcome_profile": [{"id": "outcome_attainment", "label": "Outcome Attainment", "score": 20}],
                    "run_health": {
                        "protocol_failure": False,
                        "coverage_miss": False,
                        "harness_interface_issues": [],
                        "scenario_authoring_issues": [],
                        "behavior_diagnostics": {
                            "private_note_audit": {
                                "total_notes_written": 0,
                                "structured_notes_written": 0,
                                "followed_through": 0,
                                "revisited_only": 0,
                                "not_followed_through": 0,
                                "unscoped_notes": 0,
                            }
                        },
                    },
                    "key_failures": [{"id": "scope_aligned_on_time"}],
                },
            ]
            summary = export_bundle_summary(Path(tmpdir), run_summaries, scenario_id="internal_rollout_smoke", model="mock-model", seed_bundle=[11, 29], write_files=False)
            self.assertEqual(summary["schema_version"], "tpm_bundle_performance_summary_v2")
            self.assertEqual(summary["headline"]["mean_score"], 50.0)
            self.assertEqual(summary["headline"]["score_possible"], 100.0)
            self.assertEqual(summary["aggregate_capability_assessment"]["rating"], "mixed")
            self.assertEqual(summary["confidence_scope"], "multi_seed_supported")
            self.assertEqual(summary["recurring_root_causes"][0]["id"], "wrong_precondition_sequence")
            self.assertEqual(summary["recurring_root_causes"][0]["seeds"], [11, 29])
            self.assertEqual(summary["stakeholder_failure_patterns"][0]["actor_id"], "leo")
            self.assertEqual(summary["signal_coverage_consistency"][0]["signal_id"], "approval_required")
            self.assertEqual(summary["driver_signal_consistency"][0]["signal_id"], "dana_accepts_staged_if_early")
            self.assertEqual(summary["private_note_audit_aggregate"]["runs_with_any_notes"], 1)
            self.assertEqual(summary["private_note_audit_aggregate"]["runs_with_followed_through_notes"], 1)
            self.assertEqual(summary["window_miss_recurrence"][0]["window_id"], "approval_cutoff")
            self.assertEqual(summary["runs"][0]["critical_signals_total"], 3)
            self.assertEqual(summary["runs"][0]["critical_signals_observed"], 2)
            self.assertEqual(summary["runs"][0]["critical_signals_converted"], 1)
            self.assertEqual(summary["runs"][0]["critical_actors_never_contacted"], ["leo"])
            self.assertEqual(summary["dimension_highlights"]["stable_weaknesses"][0]["id"], "outcome_attainment")
            self.assertEqual(summary["narrative"]["source"], "deterministic_template")
            rendered = render_bundle_summary(summary)
            self.assertIn("Per-seed comparison:", rendered)
            self.assertIn("| Seed | Score | Capability | Outcome | Critical signals | Driver clues | Stakeholder handling | Windows | Biggest miss |", rendered)
            self.assertIn("| 11 | 55 / 100 (55%) | mixed | partial | 2/3 seen, 1/3 acted | 1/1 seen, 0/1 acted | missed leo; 1 unanswered | 0/1 hit | Wrong Precondition Sequence |", rendered)
            self.assertIn("Capability profile:", rendered)
            self.assertIn("| Root cause | Runs | Seeds | Mean lost points |", rendered)
            self.assertIn("Critical signal consistency:", rendered)
            self.assertIn("Driver clue consistency:", rendered)
            self.assertIn("| Dana backs staged rollout if engaged early [dana_accepts_staged_if_early] | 1/2 (50%) | 0/2 (0%) | 11 | none |", rendered)
            self.assertIn("Private note audit:", rendered)
            self.assertIn("| Runs with notes | 1/2 |", rendered)

    def test_observation_exposes_unread_threads_with_stable_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "observe.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True)
            try:
                observation = session.observe()
            finally:
                session.close()
            unread = observation["observation"]["unread_threads"]
            self.assertIsInstance(unread, list)
            self.assertTrue(all(isinstance(item, dict) for item in unread))
            self.assertTrue(all("thread_id" in item for item in unread))
            self.assertTrue(all("display" in item for item in unread))

    def test_working_memory_exposes_surfaced_facts_after_doc_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "working_memory.sqlite")
            session = EnvironmentSession.create(db_path, "northstar_launch_week", 11, force=True)
            try:
                session.step(StructuredAction("read.doc", {"doc_id": "DOC-MAYA-LOAD-001"}))
                observation = session.observe()
            finally:
                session.close()
            fact_ids = {item["id"] for item in observation["working_memory"]["surfaced_facts"]}
            self.assertIn("maya_oncall_until_mon_1500", fact_ids)
            self.assertIn("backend_infeasible_for_friday", fact_ids)

    def test_session_observe_exposes_scenario_horizon_and_remaining_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "time_context.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True)
            try:
                before = session.observe()
                session.step(StructuredAction("wait.duration", {"minutes": 5}))
                after = session.observe()
            finally:
                session.close()
            self.assertEqual(before["scenario_end_at"], "2026-05-06T17:00:00")
            self.assertEqual(after["scenario_end_at"], "2026-05-06T17:00:00")
            self.assertIsInstance(before["minutes_remaining"], int)
            self.assertEqual(after["minutes_remaining"], before["minutes_remaining"] - 5)

    def test_northstar_andrew_clarification_path_has_authored_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "andrew_clarification.sqlite")
            session = EnvironmentSession.create(db_path, "northstar_launch_week", 11, force=True)
            try:
                result = session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "andrew",
                            "act_id": "request.clarification",
                            "body": "What specific alignment do you need before the frontend pilot can move?",
                        },
                    )
                )
            finally:
                session.close()
            self.assertFalse(result.coverage_miss)

    def test_northstar_andrew_inform_decision_path_has_authored_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "andrew_decision.sqlite")
            session = EnvironmentSession.create(db_path, "northstar_launch_week", 11, force=True)
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "andrew",
                            "act_id": "inform.decision",
                            "body": "We may need to narrow the pilot and I want to keep the frontend path aligned.",
                        },
                    )
                )
                result = session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
            finally:
                session.close()
            self.assertFalse(result.coverage_miss)

    def test_northstar_rahul_inform_blocker_path_has_authored_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "rahul_blocker.sqlite")
            session = EnvironmentSession.create(db_path, "northstar_launch_week", 11, force=True)
            try:
                session.step(StructuredAction("read.doc", {"doc_id": "DOC-SUPPORT-001"}))
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "rahul",
                            "act_id": "inform.blocker",
                            "body": "I reviewed the support context and design is still blocked until we align scope.",
                        },
                    )
                )
                result = session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
            finally:
                session.close()
            self.assertFalse(result.coverage_miss)

    def test_northstar_rahul_inform_decision_path_has_authored_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "rahul_decision.sqlite")
            session = EnvironmentSession.create(db_path, "northstar_launch_week", 11, force=True)
            try:
                session.step(StructuredAction("read.doc", {"doc_id": "DOC-SUPPORT-001"}))
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "rahul",
                            "act_id": "inform.decision",
                            "body": "I reviewed the support context and want to align on the next design step for the pilot.",
                        },
                    )
                )
                result = session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
            finally:
                session.close()
            self.assertFalse(result.coverage_miss)

    def test_northstar_rahul_clarification_path_has_authored_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "rahul_clarification.sqlite")
            session = EnvironmentSession.create(db_path, "northstar_launch_week", 11, force=True)
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "rahul",
                            "act_id": "request.clarification",
                            "body": "Can you clarify what support context and narrowed flow details you still need before design can move?",
                        },
                    )
                )
                result = session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
            finally:
                session.close()
            self.assertFalse(result.coverage_miss)

    def test_northstar_rahul_review_path_has_authored_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "rahul_review.sqlite")
            session = EnvironmentSession.create(db_path, "northstar_launch_week", 11, force=True)
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "rahul",
                            "act_id": "request.review",
                            "body": "Can you review the narrowed pilot flow once I have the customer context and concrete path packaged?",
                        },
                    )
                )
                result = session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
            finally:
                session.close()
            self.assertFalse(result.coverage_miss)

    def test_northstar_rahul_status_path_has_authored_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "rahul_status.sqlite")
            session = EnvironmentSession.create(db_path, "northstar_launch_week", 11, force=True)
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "rahul",
                            "act_id": "inform.status_update",
                            "body": "Quick status update: we are still narrowing the pilot path and I want to keep design aligned.",
                        },
                    )
                )
                result = session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
            finally:
                session.close()
            self.assertFalse(result.coverage_miss)

    def test_northstar_rahul_approval_after_context_before_scope_has_authored_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "rahul_approval_need_scope.sqlite")
            session = EnvironmentSession.create(db_path, "northstar_launch_week", 11, force=True)
            try:
                session.step(StructuredAction("read.doc", {"doc_id": "DOC-SUPPORT-001"}))
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "rahul",
                            "act_id": "request.approval",
                            "body": "Can you sign off on the design now that I have the support context, even though the sponsor has not yet locked the narrowed scope?",
                        },
                    )
                )
                result = session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
            finally:
                session.close()
            self.assertFalse(result.coverage_miss)

    def test_northstar_sara_clarification_path_has_authored_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "sara_clarification.sqlite")
            session = EnvironmentSession.create(db_path, "northstar_launch_week", 11, force=True)
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "sara",
                            "act_id": "request.clarification",
                            "body": "Can you clarify the customer support context and what needs to stay stable for the descoped pilot story?",
                        },
                    )
                )
                result = session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
            finally:
                session.close()
            self.assertFalse(result.coverage_miss)

    def test_northstar_maya_inform_blocker_path_has_authored_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "maya_blocker.sqlite")
            session = EnvironmentSession.create(db_path, "northstar_launch_week", 11, force=True)
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "maya",
                            "act_id": "inform.blocker",
                            "body": "The backend blocker is keeping us from aligning the pilot plan.",
                        },
                    )
                )
                result = session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
            finally:
                session.close()
            self.assertFalse(result.coverage_miss)

    def test_northstar_rohit_clarification_path_has_authored_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "rohit_clarification.sqlite")
            session = EnvironmentSession.create(db_path, "northstar_launch_week", 11, force=True)
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "rohit",
                            "act_id": "request.clarification",
                            "body": "We need to clarify what is actually launchable this week for the pilot.",
                        },
                    )
                )
                result = session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
            finally:
                session.close()
            self.assertFalse(result.coverage_miss)

    def test_northstar_rohit_inform_decision_path_has_authored_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "rohit_decision.sqlite")
            session = EnvironmentSession.create(db_path, "northstar_launch_week", 11, force=True)
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "rohit",
                            "act_id": "inform.decision",
                            "body": "The feasible scope this week is a descoped pilot with the critical dependencies still being worked through.",
                        },
                    )
                )
                result = session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
            finally:
                session.close()
            self.assertFalse(result.coverage_miss)

    def test_northstar_nina_request_approval_path_has_authored_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "nina_approval.sqlite")
            session = EnvironmentSession.create(db_path, "northstar_launch_week", 11, force=True)
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "nina",
                            "act_id": "request.approval",
                            "body": "Can security approve the pilot path as-is so we can keep moving?",
                        },
                    )
                )
                result = session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
            finally:
                session.close()
            self.assertFalse(result.coverage_miss)

    def test_northstar_nina_request_clarification_path_has_authored_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "nina_clarification.sqlite")
            session = EnvironmentSession.create(db_path, "northstar_launch_week", 11, force=True)
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "nina",
                            "act_id": "request.clarification",
                            "body": "What do you need from us to make the security path real this week?",
                        },
                    )
                )
                result = session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
            finally:
                session.close()
            self.assertFalse(result.coverage_miss)

    def test_chat_send_uses_semantic_cost_buckets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "semantic_costs.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True)
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "leo",
                            "act_id": "request.feasibility",
                            "slots": {"task_id": "config_rollout"},
                            "body": "Need the honest path and blockers.",
                        },
                    )
                )
                feasibility_action = session.engine.store.actions()[-1]
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "ivy",
                            "act_id": "request.approval",
                            "slots": {"task_id": "approval_review"},
                            "body": "Requesting approval for the rollout.",
                        },
                    )
                )
                approval_action = session.engine.store.actions()[-1]
            finally:
                session.close()

            feasibility_metadata = json.loads(feasibility_action["metadata_json"])
            approval_metadata = json.loads(approval_action["metadata_json"])
            self.assertEqual(feasibility_metadata["cost_key"], "ask_discovery_question")
            self.assertEqual(feasibility_action["duration_minutes"], 10)
            self.assertEqual(approval_metadata["cost_key"], "follow_up_on_commitment")
            self.assertEqual(approval_action["duration_minutes"], 10)

    def test_repeated_chat_ping_coalesces_pending_reply_and_uses_status_push_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "coalesced_reply.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True)
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "leo",
                            "act_id": "request.scope_tradeoff",
                            "slots": {"task_id": "config_rollout"},
                            "body": "Can we stage the rollout if full scope is not credible?",
                        },
                    )
                )
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "leo",
                            "act_id": "request.scope_tradeoff",
                            "slots": {"task_id": "config_rollout"},
                            "body": "Following up on the staged tradeoff.",
                        },
                    )
                )
                pending = [
                    row
                    for row in session.engine.store.pending_events()
                    if row["type"] == "npc.respond_message" and row["actor_id"] == "leo"
                ]
                latest_action = session.engine.store.actions()[-1]
            finally:
                session.close()

            self.assertEqual(len(pending), 1)
            payload = json.loads(pending[0]["payload_json"])
            metadata = json.loads(latest_action["metadata_json"])
            self.assertEqual(payload["batched_message_count"], 2)
            self.assertEqual(metadata["cost_key"], "status_push_without_new_information")
            self.assertEqual(latest_action["duration_minutes"], 15)

    def test_working_memory_exposes_thread_state_actor_constraints_and_approval_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "working_memory_state.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True)
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "ivy",
                            "act_id": "request.approval",
                            "slots": {"task_id": "approval_review"},
                            "body": "Requesting approval for the rollout.",
                        },
                    )
                )
                before_reply = session.observe()["working_memory"]
                session.step(StructuredAction("wait.duration", {"minutes": 60}))
                after_reply = session.observe()["working_memory"]
            finally:
                session.close()

            ivy_thread = next(item for item in before_reply["thread_state"] if item["actor_id"] == "ivy")
            self.assertIsNotNone(ivy_thread["pending_reply_due_at"])
            self.assertTrue(ivy_thread["repeated_followup_risk"])
            self.assertTrue(any("Approval" in item["title"] or "approval" in item["title"].lower() for item in before_reply["approval_readiness"]))
            ivy_constraints = next(item for item in after_reply["actor_constraints"] if item["actor_id"] == "ivy")
            joined = " ".join(ivy_constraints["constraints"]).lower()
            self.assertTrue("blocker" in joined or "intake" in joined or "review" in joined)

    def test_pressuring_engineer_vs_honest_feasibility_changes_trust_and_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            eta_db = str(Path(tmpdir) / "eta.sqlite")
            feasibility_db = str(Path(tmpdir) / "feasibility.sqlite")
            eta_session = EnvironmentSession.create(eta_db, "internal_rollout_smoke", 11, force=True)
            feasibility_session = EnvironmentSession.create(feasibility_db, "internal_rollout_smoke", 11, force=True)
            try:
                eta_session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "leo",
                            "act_id": "request.eta",
                            "slots": {"task_id": "config_rollout"},
                            "body": "Need a delivery date right now.",
                        },
                    )
                )
                eta_session.step(StructuredAction("wait.until_next_event", {"max_minutes": 180}))
                eta_session.step(StructuredAction("read.thread", {"target": "leo"}))

                feasibility_session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "leo",
                            "act_id": "request.feasibility",
                            "slots": {"task_id": "config_rollout"},
                            "body": "Need the honest path and blockers.",
                        },
                    )
                )
                feasibility_session.step(StructuredAction("wait.until_next_event", {"max_minutes": 180}))
                feasibility_session.step(StructuredAction("read.thread", {"target": "leo"}))

                eta_trust = eta_session.engine.relationship_state("leo", "tpm")["trust"]
                feasibility_trust = feasibility_session.engine.relationship_state("leo", "tpm")["trust"]
                eta_facts = {item["id"] for item in eta_session.observe()["working_memory"]["surfaced_facts"]}
                feasibility_facts = {
                    item["id"] for item in feasibility_session.observe()["working_memory"]["surfaced_facts"]
                }
            finally:
                eta_session.close()
                feasibility_session.close()
            self.assertLess(eta_trust, feasibility_trust)
            self.assertNotIn("full_rollout_infeasible", eta_facts)
            self.assertIn("full_rollout_infeasible", feasibility_facts)

    def test_read_thread_accepts_envelope_like_target_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "thread_prefix.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True)
            try:
                result = session.step(StructuredAction("read.thread", {"target": "ivy.approval.defer"}))
            finally:
                session.close()
            self.assertIn("Thread ivy:", result.message)

    def test_read_thread_accepts_email_like_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "thread_email.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True)
            try:
                result = session.step(StructuredAction("read.thread", {"target": "leo@company.com"}))
            finally:
                session.close()
            self.assertIn("Thread leo:", result.message)

    def test_read_thread_accepts_alias_with_thread_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "thread_alias.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True)
            try:
                result = session.step(StructuredAction("read.thread", {"target": "ivy_approval_thread"}))
            finally:
                session.close()
            self.assertIn("Thread ivy:", result.message)

    def test_read_thread_accepts_unique_role_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "thread_role_alias.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True)
            try:
                result = session.step(StructuredAction("read.thread", {"target": "manager"}))
            finally:
                session.close()
            self.assertIn("Thread dana:", result.message)

    def test_chat_send_accepts_unique_role_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "chat_role_alias.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True)
            try:
                result = session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "manager",
                            "act_id": "escalate.to_manager",
                            "slots": {},
                            "body": "Escalating the blocked rollout decision.",
                        },
                    )
                )
            finally:
                session.close()
            self.assertIn("Sent escalate.to_manager in dana", result.message)

    def test_agent_runner_repairs_after_runtime_invalid_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "runtime_repair.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True)
            try:
                record = AgentRunner(RuntimeRepairAdapter(), max_turns=2).run(
                    session,
                    seed=11,
                    output_dir=str(Path(tmpdir) / "runtime_repair_run"),
                    model_name="mock-model",
                )
            finally:
                session.close()
            self.assertFalse(record.protocol_failure)
            payload = json.loads(Path(record.agent_log_path).read_text())
            self.assertEqual(payload["decisions"][0]["decision"]["action"]["action_type"], "wait.duration")

    def test_authoring_brief_validation_rejects_missing_fields(self) -> None:
        from tpm_sim.authoring.briefs import validate_brief

        with self.assertRaises(ValueError):
            validate_brief({"scenario_id": "broken"})

    def test_authoring_brief_validation_rejects_cast_id_mismatch_for_existing_scenario(self) -> None:
        from tpm_sim.authoring.briefs import validate_brief

        payload = json.loads((AUTHORING_BRIEFS / "internal_rollout_smoke.json").read_text())
        payload["cast"][0]["id"] = "wrong_actor"
        with self.assertRaises(ValueError):
            validate_brief(payload)

    def test_run_author_init_renders_full_intent_briefing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            proposal_dir = str(Path(tmpdir) / "proposal")
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = run_author_init(str(AUTHORING_BRIEFS / "internal_rollout_smoke.json"), proposal_dir, False)
            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("Scenario Briefing: Internal Rollout Smoke", output)
            self.assertIn("Dana Brooks (dana)", output)
            self.assertIn("Hidden Landscape:", output)
            self.assertIn("approval_secured by", output)

    def test_run_author_synthesize_world_renders_candidate_briefing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            proposal_dir = str(Path(tmpdir) / "proposal")
            init_proposal(str(AUTHORING_BRIEFS / "internal_rollout_smoke.json"), proposal_dir)
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = run_author_synthesize(
                    "world",
                    proposal_dir,
                    "fixture",
                    "fixture",
                    str(AUTHORING_FIXTURES),
                    False,
                )
            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("Synthesized candidate world.", output)
            self.assertIn("Project: Internal Config Rollout", output)
            self.assertIn("Decision rights: approve scope", output)
            self.assertIn("Proposal Status:", output)

    def test_synthesize_trajectories_prompt_includes_legacy_northstar_examples(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            proposal_dir = str(Path(tmpdir) / "proposal")
            init_proposal(str(AUTHORING_BRIEFS / "northstar_launch_week.json"), proposal_dir)
            synthesize_world(proposal_dir, adapter="fixture", model="fixture", fixtures_root=str(AUTHORING_FIXTURES))
            client = TextResponseClient(json.dumps({"smoke.tpm": (EXAMPLES / "golden.tpm").read_text()}))
            with mock.patch("tpm_sim.authoring.workflow.build_model_client", return_value=client):
                synthesize_trajectories(proposal_dir, adapter="openai", model="gpt-test")
            prompt_spec = client.calls[0]["prompt_spec"]
            payload = json.loads(prompt_spec["user"])
            accepted_reference = payload["accepted_reference"]
            self.assertIn("golden.tpm", accepted_reference["filenames"])
            self.assertIn("golden.tpm", accepted_reference["example_scripts"])
            self.assertIn("docs open DOC-BRIEF-001", accepted_reference["example_scripts"]["golden.tpm"])
            self.assertIn("valid_command_templates", payload["trajectory_contract"]["command_reference"])

    def test_synthesize_trajectories_rejects_invalid_dsl_before_validate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            proposal_dir = str(Path(tmpdir) / "proposal")
            init_proposal(str(AUTHORING_BRIEFS / "internal_rollout_smoke.json"), proposal_dir)
            synthesize_world(proposal_dir, adapter="fixture", model="fixture", fixtures_root=str(AUTHORING_FIXTURES))
            client = TextResponseClient(json.dumps({"smoke.tpm": "READ_DOC DOC-BRIEF-100\n"}))
            with mock.patch("tpm_sim.authoring.workflow.build_model_client", return_value=client):
                with self.assertRaises(RuntimeError) as ctx:
                    synthesize_trajectories(proposal_dir, adapter="openai", model="gpt-test")
            self.assertIn("syntax validation", str(ctx.exception))
            self.assertIn("Use `docs open DOC-ID`.", str(ctx.exception))

    def test_synthesize_world_normalizes_external_commitment_requirements_from_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            proposal_dir = str(Path(tmpdir) / "proposal")
            init_proposal(str(AUTHORING_BRIEFS / "northstar_launch_week.json"), proposal_dir)
            scenario = json.loads(json.dumps(load_scenario_bundle("northstar_launch_week")["scenario"]))
            scenario["policy"]["external_commitment_requirements"] = [{"id": "req_no_fake_eta"}]
            client = TextResponseClient(json.dumps(scenario))
            with mock.patch("tpm_sim.authoring.workflow.build_model_client", return_value=client):
                synthesize_world(proposal_dir, adapter="openai", model="gpt-test")
            written = json.loads((Path(proposal_dir) / "candidate" / "scenario.json").read_text())
            self.assertEqual(written["policy"]["external_commitment_requirements"], ["scope_aligned", "security_slot_secured"])

    def test_synthesize_semantics_normalizes_commitment_id_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            proposal_dir = str(Path(tmpdir) / "proposal")
            init_proposal(str(AUTHORING_BRIEFS / "northstar_launch_week.json"), proposal_dir)
            synthesize_world(proposal_dir, adapter="fixture", model="fixture", fixtures_root=str(AUTHORING_FIXTURES))
            compile_contract(proposal_dir)
            semantics = json.loads((OFFICIAL_SCENARIOS / "northstar_launch_week" / "coverage_semantics.json").read_text())
            rewritten = False
            for entry in semantics.get("cells", []):
                for envelope in entry.get("response_envelopes", []):
                    for effect in envelope.get("effects", []):
                        if effect.get("type") == "create_or_update_commitment" and isinstance(effect.get("id"), str):
                            effect["commitment_id"] = effect.pop("id")
                            rewritten = True
                            break
                    if rewritten:
                        break
                if rewritten:
                    break
            self.assertTrue(rewritten)
            client = TextResponseClient(json.dumps(semantics))
            with mock.patch("tpm_sim.authoring.workflow.build_model_client", return_value=client):
                synthesize_semantics(proposal_dir, adapter="openai", model="gpt-test")
            rendered = json.loads((Path(proposal_dir) / "candidate" / "coverage_semantics.json").read_text())
            commitment_effects = [
                effect
                for entry in rendered.get("cells", [])
                for envelope in entry.get("response_envelopes", [])
                for effect in envelope.get("effects", [])
                if effect.get("type") == "create_or_update_commitment"
            ]
            self.assertTrue(commitment_effects)
            self.assertTrue(all("id" in effect for effect in commitment_effects))
            self.assertTrue(all("commitment_id" not in effect for effect in commitment_effects))

    def test_compile_coverage_artifact_normalizes_commitment_id_alias_from_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            proposal_dir = str(Path(tmpdir) / "proposal")
            init_proposal(str(AUTHORING_BRIEFS / "northstar_launch_week.json"), proposal_dir)
            synthesize_world(proposal_dir, adapter="fixture", model="fixture", fixtures_root=str(AUTHORING_FIXTURES))
            compile_contract(proposal_dir)
            synthesize_semantics(proposal_dir, adapter="fixture", model="fixture", fixtures_root=str(AUTHORING_FIXTURES))
            semantics_path = Path(proposal_dir) / "candidate" / "coverage_semantics.json"
            semantics = json.loads(semantics_path.read_text())
            rewritten = False
            for entry in semantics.get("cells", []):
                for envelope in entry.get("response_envelopes", []):
                    for effect in envelope.get("effects", []):
                        if effect.get("type") == "create_or_update_commitment" and isinstance(effect.get("id"), str):
                            effect["commitment_id"] = effect.pop("id")
                            rewritten = True
                            break
                    if rewritten:
                        break
                if rewritten:
                    break
            self.assertTrue(rewritten)
            semantics_path.write_text(json.dumps(semantics, indent=2, sort_keys=True))

            compile_coverage_artifact(proposal_dir)

            rendered = json.loads(semantics_path.read_text())
            commitment_effects = [
                effect
                for entry in rendered.get("cells", [])
                for envelope in entry.get("response_envelopes", [])
                for effect in envelope.get("effects", [])
                if effect.get("type") == "create_or_update_commitment"
            ]
            self.assertTrue(commitment_effects)
            self.assertTrue(all("id" in effect for effect in commitment_effects))
            self.assertTrue(all("commitment_id" not in effect for effect in commitment_effects))

    def test_render_operator_briefing_omits_redundant_project_detail(self) -> None:
        rendered = render_operator_briefing(
            {
                "title": "Northstar Launch Week",
                "scenario_id": "northstar_launch_week",
                "project_name": "Northstar customer pilot",
                "premise": (
                    "A first-week TPM benchmark around rescuing a credible Friday customer pilot under hidden "
                    "dependency and stakeholder pressure."
                ),
                "project_summary": "A first-week TPM benchmark around rescuing a credible Friday pilot for Northstar.",
                "window": {},
                "how_to_win": [],
                "how_to_fail": [],
                "cast": [],
                "hidden_landscape": [],
                "critical_path": [],
                "deadlines": [],
                "proposal_status": None,
                "run_context": None,
            },
            compact=False,
        )
        self.assertIn("Project: Northstar customer pilot", rendered)
        self.assertNotIn("Project Detail:", rendered)

    def test_render_operator_briefing_keeps_distinct_project_detail(self) -> None:
        rendered = render_operator_briefing(
            {
                "title": "Northstar Launch Week",
                "scenario_id": "northstar_launch_week",
                "project_name": "Northstar customer pilot",
                "premise": "A benchmark about rescuing a credible Friday pilot.",
                "project_summary": "Customer-facing admin hub rollout with security, design, and support dependencies.",
                "window": {},
                "how_to_win": [],
                "how_to_fail": [],
                "cast": [],
                "hidden_landscape": [],
                "critical_path": [],
                "deadlines": [],
                "proposal_status": None,
                "run_context": None,
            },
            compact=False,
        )
        self.assertIn("Project Detail: Customer-facing admin hub rollout with security, design, and support dependencies.", rendered)

    def test_fixture_authoring_workflow_round_trips_and_accepts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            proposal_dir = str(Path(tmpdir) / "proposal")
            manifest = init_proposal(str(AUTHORING_BRIEFS / "internal_rollout_smoke.json"), proposal_dir)
            self.assertTrue(Path(manifest["operator_briefing_json_path"]).exists())
            self.assertTrue(Path(manifest["operator_briefing_markdown_path"]).exists())
            synthesize_world(proposal_dir, adapter="fixture", model="fixture", fixtures_root=str(AUTHORING_FIXTURES))
            compile_contract(proposal_dir)
            synthesize_semantics(proposal_dir, adapter="fixture", model="fixture", fixtures_root=str(AUTHORING_FIXTURES))
            compile_coverage_artifact(proposal_dir)
            synthesize_trajectories(proposal_dir, adapter="fixture", model="fixture", fixtures_root=str(AUTHORING_FIXTURES))
            validation = validate_proposal(proposal_dir)
            self.assertTrue(validation["valid"])
            closure = run_closure_suite(proposal_dir, adapter="fixture", model="fixture", fixtures_root=str(AUTHORING_FIXTURES))
            self.assertTrue(closure["passed"])
            diff = diff_proposal(proposal_dir, scenarios_root=str(OFFICIAL_SCENARIOS))
            self.assertTrue(diff["scenario_exists"])
            self.assertEqual(diff["scenario_changes"]["top_level_changed"], [])

            scenarios_root = Path(tmpdir) / "accepted_scenarios"
            examples_root = Path(tmpdir) / "accepted_examples"
            accepted = accept_proposal(
                proposal_dir,
                scenarios_root=str(scenarios_root),
                examples_root=str(examples_root),
            )
            scenario_dir = Path(accepted["scenario_dir"])
            self.assertTrue((scenario_dir / "scenario.json").exists())
            self.assertTrue((scenario_dir / "coverage_contract.json").exists())
            self.assertTrue((scenario_dir / "coverage_semantics.json").exists())
            self.assertTrue((scenario_dir / "npc_coverage.json").exists())
            self.assertTrue((scenario_dir / "closure_report.json").exists())
            self.assertTrue((scenario_dir / "operator_briefing.json").exists())
            self.assertTrue((scenario_dir / "operator_briefing.md").exists())
            self.assertTrue((examples_root / "internal_rollout_smoke" / "smoke.tpm").exists())

    def test_author_command_json_output_keeps_stdout_clean_and_writes_summary_to_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            proposal_dir = str(Path(tmpdir) / "proposal")
            init_proposal(str(AUTHORING_BRIEFS / "internal_rollout_smoke.json"), proposal_dir)
            synthesize_world(proposal_dir, adapter="fixture", model="fixture", fixtures_root=str(AUTHORING_FIXTURES))
            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = run_author_compile_contract(proposal_dir, True)
            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertIn("coverage_contract_path", payload)
            self.assertIn("Compiled coverage contract.", stderr.getvalue())
            self.assertIn("operator briefing:", stderr.getvalue())

    def test_run_author_validate_non_json_is_human_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            proposal_dir = str(Path(tmpdir) / "proposal")
            init_proposal(str(AUTHORING_BRIEFS / "internal_rollout_smoke.json"), proposal_dir)
            synthesize_world(proposal_dir, adapter="fixture", model="fixture", fixtures_root=str(AUTHORING_FIXTURES))
            compile_contract(proposal_dir)
            synthesize_semantics(proposal_dir, adapter="fixture", model="fixture", fixtures_root=str(AUTHORING_FIXTURES))
            compile_coverage_artifact(proposal_dir)
            synthesize_trajectories(proposal_dir, adapter="fixture", model="fixture", fixtures_root=str(AUTHORING_FIXTURES))
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = run_author_validate(proposal_dir, False)
            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("Validated proposal for internal_rollout_smoke.", output)
            self.assertIn("coverage:", output)
            self.assertNotIn('{\n  "', output)

    def test_validate_proposal_reports_trajectory_syntax_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            proposal_dir = str(Path(tmpdir) / "proposal")
            init_proposal(str(AUTHORING_BRIEFS / "internal_rollout_smoke.json"), proposal_dir)
            synthesize_world(proposal_dir, adapter="fixture", model="fixture", fixtures_root=str(AUTHORING_FIXTURES))
            compile_contract(proposal_dir)
            synthesize_semantics(proposal_dir, adapter="fixture", model="fixture", fixtures_root=str(AUTHORING_FIXTURES))
            compile_coverage_artifact(proposal_dir)
            trajectories_dir = Path(proposal_dir) / "trajectories"
            trajectories_dir.mkdir(parents=True, exist_ok=True)
            (trajectories_dir / "smoke.tpm").write_text("READ_DOC DOC-BRIEF-100\n")

            report = validate_proposal(proposal_dir)

            self.assertFalse(report["valid"])
            self.assertIn("trajectory syntax validation failed", report["errors"])
            self.assertEqual(report["smoke_results"], [])
            self.assertIn("Use `docs open DOC-ID`.", " ".join(report["trajectory_syntax_errors"]))

    def test_validate_proposal_reports_invalid_scenario_runtime_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            proposal_dir = str(Path(tmpdir) / "proposal")
            init_proposal(str(AUTHORING_BRIEFS / "internal_rollout_smoke.json"), proposal_dir)
            synthesize_world(proposal_dir, adapter="fixture", model="fixture", fixtures_root=str(AUTHORING_FIXTURES))
            compile_contract(proposal_dir)
            synthesize_semantics(proposal_dir, adapter="fixture", model="fixture", fixtures_root=str(AUTHORING_FIXTURES))
            compile_coverage_artifact(proposal_dir)
            synthesize_trajectories(proposal_dir, adapter="fixture", model="fixture", fixtures_root=str(AUTHORING_FIXTURES))

            scenario_path = Path(proposal_dir) / "candidate" / "scenario.json"
            scenario = json.loads(scenario_path.read_text())
            scenario["policy"]["external_commitment_requirements"] = [42]
            scenario_path.write_text(json.dumps(scenario, indent=2, sort_keys=True))

            report = validate_proposal(proposal_dir)

            self.assertFalse(report["valid"])
            self.assertIn("scenario runtime validation failed", report["errors"])
            self.assertEqual(report["smoke_results"], [])
            self.assertIn("policy.external_commitment_requirements", " ".join(report["scenario_validation_errors"]))

    def test_init_db_emits_human_readable_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = init_db(str(Path(tmpdir) / "demo.sqlite"), "internal_rollout_smoke", 11, "strict", True)
            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("Run Preflight: Internal Rollout Smoke", output)
            self.assertIn("command: init", output)
            self.assertIn("Initialized", output)

    def test_closure_suite_fails_when_official_seeds_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            proposal_dir = str(Path(tmpdir) / "proposal")
            init_proposal(str(AUTHORING_BRIEFS / "internal_rollout_smoke.json"), proposal_dir)
            synthesize_world(proposal_dir, adapter="fixture", model="fixture", fixtures_root=str(AUTHORING_FIXTURES))
            compile_contract(proposal_dir)
            synthesize_semantics(proposal_dir, adapter="fixture", model="fixture", fixtures_root=str(AUTHORING_FIXTURES))
            compile_coverage_artifact(proposal_dir)
            synthesize_trajectories(proposal_dir, adapter="fixture", model="fixture", fixtures_root=str(AUTHORING_FIXTURES))
            validation = validate_proposal(proposal_dir)
            self.assertTrue(validation["valid"])

            scenario_path = Path(proposal_dir) / "candidate" / "scenario.json"
            scenario = json.loads(scenario_path.read_text())
            scenario["evaluation"]["official_seeds"] = []
            scenario_path.write_text(json.dumps(scenario, indent=2, sort_keys=True))
            compile_coverage_artifact(proposal_dir)

            closure = run_closure_suite(proposal_dir, adapter="fixture", model="fixture", fixtures_root=str(AUTHORING_FIXTURES))
            self.assertFalse(closure["passed"])
            self.assertEqual(closure["status"], "failed_no_seeds")
            self.assertEqual(closure["live_agent_suite"]["status"], "skipped_no_seeds")

    def test_build_starter_contract_uses_top_level_coordination_template_affordances(self) -> None:
        scenario = {
            "world": {
                "actors": [
                    {"id": "dana", "coordination_template": "sponsor"},
                    {"id": "mia", "coordination_template": "ally"},
                    {"id": "ivy", "coordination_template": "cross_functional_dependency_owner"},
                ]
            }
        }
        contract = build_starter_contract(scenario)
        selectors = {
            (cell["selector"].get("actor_id"), cell["selector"].get("incoming_act_id"))
            for cell in contract["cells"]
            if cell["selector"].get("surface") == "chat"
        }
        self.assertIn(("dana", "request.approval"), selectors)
        self.assertIn(("dana", "ack.received"), selectors)
        self.assertIn(("dana", "commit.propose"), selectors)
        self.assertIn(("dana", "negotiate.scope"), selectors)
        self.assertIn(("dana", "inform.status_update"), selectors)
        self.assertIn(("mia", "request.feasibility"), selectors)
        self.assertIn(("mia", "request.review"), selectors)
        self.assertIn(("mia", "request.scope_tradeoff"), selectors)
        self.assertIn(("mia", "negotiate.scope"), selectors)
        self.assertIn(("mia", "inform.decision"), selectors)
        self.assertIn(("ivy", "request.feasibility"), selectors)
        self.assertIn(("ivy", "inform.blocker"), selectors)

    def test_compile_contract_merges_starter_floor_into_reference_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            proposal_dir = str(Path(tmpdir) / "proposal")
            init_proposal(str(AUTHORING_BRIEFS / "northstar_launch_week.json"), proposal_dir)
            synthesize_world(proposal_dir, adapter="fixture", model="fixture", fixtures_root=str(AUTHORING_FIXTURES))

            result = compile_contract(proposal_dir)

            contract = json.loads((Path(proposal_dir) / "candidate" / "coverage_contract.json").read_text())
            selectors = {
                (cell["selector"].get("actor_id"), cell["selector"].get("incoming_act_id"))
                for cell in contract["cells"]
                if isinstance(cell.get("selector"), dict) and cell["selector"].get("surface") == "chat"
            }
            ids = {cell["id"] for cell in contract["cells"] if isinstance(cell.get("id"), str)}
            self.assertGreater(result["starter_floor_cells_added"], 0)
            self.assertIn(("sara", "request.clarification"), selectors)
            self.assertIn(("maya", "request.scope_tradeoff"), selectors)
            self.assertIn("rohit_decision_need_context", ids)

    def test_project_env_autoload_reads_repo_root_dotenv_without_overriding_process_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            nested = root / "nested" / "workspace"
            nested.mkdir(parents=True)
            (root / ".env").write_text(
                "\n".join(
                    [
                        "OPENAI_API_KEY=from-dotenv",
                        "TPM_AGENT_MODEL=\"gpt-from-dotenv\"",
                        "TPM_AUTHORING_MODEL=\"gpt-authoring-from-dotenv\"",
                    ]
                )
            )
            with mock.patch.dict(
                os.environ,
                {"OPENAI_API_KEY": "from-process", "TPM_AUTHORING_MODEL": "gpt-authoring-from-process"},
                clear=True,
            ):
                result = autoload_project_dotenv(start_dir=nested, project_root_path=root)
                self.assertEqual(os.environ["OPENAI_API_KEY"], "from-process")
                self.assertEqual(os.environ["TPM_AGENT_MODEL"], "gpt-from-dotenv")
                self.assertEqual(os.environ["TPM_AUTHORING_MODEL"], "gpt-authoring-from-process")
                self.assertEqual([str(Path(path).resolve()) for path in result["loaded_paths"]], [str((root / ".env").resolve())])

    def test_gpt5_model_client_omits_sampling_controls(self) -> None:
        class DummyResponses:
            def __init__(self) -> None:
                self.requests: list[dict[str, object]] = []

            def create(self, **kwargs: object) -> dict[str, object]:
                self.requests.append(kwargs)
                return {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps({"ok": True}),
                                }
                            ],
                        }
                    ],
                    "usage": {},
                }

        responses = DummyResponses()
        client = OpenAIResponsesModelClient.__new__(OpenAIResponsesModelClient)
        client.client = type("DummyClient", (), {"responses": responses})()

        prompt_spec = {"system": "system", "messages": [], "metadata": {"test": True}}
        schema = {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        }

        client.generate_structured(
            schema_name="demo",
            schema=schema,
            prompt_spec=prompt_spec,
            config={"model": "gpt-5-nano", "temperature": 0, "top_p": 1, "reasoning_effort": "minimal"},
        )
        self.assertNotIn("temperature", responses.requests[-1])
        self.assertNotIn("top_p", responses.requests[-1])
        self.assertEqual(responses.requests[-1]["reasoning"], {"effort": "minimal"})

        client.generate_structured(
            schema_name="demo",
            schema=schema,
            prompt_spec=prompt_spec,
            config={"model": "gpt-4o", "temperature": 0, "top_p": 1},
        )
        self.assertEqual(responses.requests[-1]["temperature"], 0)
        self.assertEqual(responses.requests[-1]["top_p"], 1)

    def test_response_extractors_tolerate_null_content_items(self) -> None:
        raw = {
            "output": [
                {"type": "message", "content": None},
                {"type": "message", "content": [{"type": "output_text", "text": "hello"}]},
            ]
        }
        self.assertEqual(_extract_output_text(raw), "hello")
        self.assertIsNone(_extract_refusal(raw))

    def test_cli_uses_tpm_agent_model_from_environment_as_default(self) -> None:
        from tpm_sim.cli import build_parser

        with mock.patch.dict(os.environ, {"TPM_AGENT_MODEL": "gpt-env-default"}, clear=True):
            parser = build_parser()
            args = parser.parse_args(["agent", "run", "--scenario", "internal_rollout_smoke"])
            self.assertEqual(args.model, "gpt-env-default")
            self.assertEqual(args.stream_events, "omniscient")

    def test_resolve_authoring_model_prefers_explicit_then_authoring_env(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"TPM_AGENT_MODEL": "gpt-agent-default", "TPM_AUTHORING_MODEL": "gpt-authoring-default"},
            clear=True,
        ):
            self.assertEqual(_resolve_authoring_model("openai", "gpt-cli-override"), "gpt-cli-override")
            self.assertEqual(_resolve_authoring_model("openai", None), "gpt-authoring-default")

    def test_resolve_authoring_model_falls_back_to_tpm_agent_model(self) -> None:
        with mock.patch.dict(os.environ, {"TPM_AGENT_MODEL": "gpt-agent-default"}, clear=True):
            self.assertEqual(_resolve_authoring_model("openai", None), "gpt-agent-default")

    def test_fixture_authoring_resolution_keeps_fixture_default(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"TPM_AGENT_MODEL": "gpt-agent-default", "TPM_AUTHORING_MODEL": "gpt-authoring-default"},
            clear=True,
        ):
            self.assertEqual(_resolve_authoring_model("fixture", None), "fixture")

    def test_run_agent_streams_live_events_to_stderr_without_corrupting_json_stdout(self) -> None:
        actions = [
            {
                "action_type": "chat.send",
                "arguments": {
                    "target": "leo",
                    "act_id": "request.feasibility",
                    "slots": {"task_id": "config_rollout"},
                    "body": "Need the honest path for the staged rollout.",
                },
                "reason": "get the real feasibility signal",
            },
            {
                "action_type": "wait.until_next_event",
                "arguments": {"max_minutes": 180},
                "reason": "wait for Leo to answer",
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            stdout = StringIO()
            stderr = StringIO()
            summary = {"schema_version": "test_summary_v1", "ok": True}
            with (
                mock.patch("tpm_sim.cli.build_model_client", return_value=object()),
                mock.patch("tpm_sim.cli.OpenAIResponsesAgentAdapter", return_value=ScriptedAdapter(actions)),
                mock.patch("tpm_sim.cli.export_run_summary", return_value=summary) as export_run_summary_mock,
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                exit_code = run_agent(
                    "internal_rollout_smoke",
                    11,
                    "mock-model",
                    str(Path(tmpdir) / "live_run"),
                    2,
                    "strict",
                    "omniscient",
                    True,
                )
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), summary)
        self.assertIn("tpm.message_sent", stderr.getvalue())
        self.assertIn("npc.message_sent", stderr.getvalue())
        self.assertEqual(export_run_summary_mock.call_args.kwargs["judge_model"], "gpt-5.4")

    def test_run_summarize_run_uses_gpt_5_4_judge_model(self) -> None:
        stdout = StringIO()
        summary = {"schema_version": "test_summary_v1", "ok": True}
        with (
            mock.patch("tpm_sim.cli.summarize_existing_run", return_value=summary) as summarize_existing_run_mock,
            redirect_stdout(stdout),
        ):
            exit_code = run_summarize_run("/tmp/mock-run", as_json_output=True)
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), summary)
        self.assertEqual(summarize_existing_run_mock.call_args.kwargs["judge_model"], "gpt-5.4")

    def test_resolve_summary_judge_model_is_pinned_to_gpt_5_4(self) -> None:
        self.assertEqual(_resolve_summary_judge_model(), "gpt-5.4")

    def test_internal_rollout_smoke_script_runs_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "smoke.sqlite")
            engine, evaluator = build_runtime(db_path, "internal_rollout_smoke", seed=11)
            try:
                execute_script(engine, evaluator, EXAMPLES / "internal_rollout_smoke" / "smoke.tpm", echo=False, emit=False)
                report = evaluator.evaluate()
            finally:
                engine.store.close()
            self.assertGreaterEqual(report["total_score"], 90.0)

    def test_internal_rollout_clarification_to_leo_is_covered_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "clarification.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True, coverage_enforcement="strict")
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "leo",
                            "act_id": "request.clarification",
                            "slots": {},
                            "body": "Spell out the blocker and what needs to happen next.",
                        },
                    )
                )
                notices = []
                for _ in range(3):
                    result = session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
                    notices.append(result.message)
                    if "Leo Park" in result.message:
                        break
                thread_view = session.step(StructuredAction("read.thread", {"target": "leo"}))
            finally:
                session.close()
            self.assertTrue(any("Leo Park" in message for message in notices))
            self.assertIn("[inform.status_update]", thread_view.message)
            self.assertIn("actor.leo.prefers_scope_before_eta", thread_view.message)

    def test_northstar_clarification_to_maya_is_covered_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "maya_clarification.sqlite")
            session = EnvironmentSession.create(db_path, "northstar_launch_week", 11, force=True, coverage_enforcement="strict")
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "maya",
                            "act_id": "request.clarification",
                            "slots": {},
                            "body": "Clarify the real blocker and what needs to happen next.",
                        },
                    )
                )
                notices = []
                for _ in range(3):
                    result = session.step(StructuredAction("wait.until_next_event", {"max_minutes": 180}))
                    notices.append(result.message)
                    if "Maya Chen" in result.message:
                        break
                thread_view = session.step(StructuredAction("read.thread", {"target": "maya"}))
            finally:
                session.close()
            self.assertTrue(any("Maya Chen" in message for message in notices))
            self.assertIn("[inform.blocker]", thread_view.message)
            self.assertIn("Full backend scope is not credible for Friday", thread_view.message)
            self.assertIn("Security review is required", thread_view.message)

    def test_northstar_status_update_to_maya_is_covered_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "maya_status.sqlite")
            session = EnvironmentSession.create(db_path, "northstar_launch_week", 11, force=True, coverage_enforcement="strict")
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "maya",
                            "act_id": "inform.status_update",
                            "slots": {},
                            "body": "Status update: we still need the credible backend path and security timing.",
                        },
                    )
                )
                notices = []
                for _ in range(3):
                    result = session.step(StructuredAction("wait.until_next_event", {"max_minutes": 180}))
                    notices.append(result.message)
                    if "Maya Chen" in result.message:
                        break
                thread_view = session.step(StructuredAction("read.thread", {"target": "maya"}))
            finally:
                session.close()
            self.assertTrue(any("Maya Chen" in message for message in notices))
            self.assertIn("[inform.blocker]", thread_view.message)
            self.assertIn("Full backend scope is not credible for Friday", thread_view.message)
            self.assertIn("Security review is required", thread_view.message)

    def test_internal_rollout_request_eta_to_ivy_is_covered_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "ivy_eta.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True, coverage_enforcement="strict")
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "ivy",
                            "act_id": "request.eta",
                            "slots": {},
                            "body": "When can security review this?",
                        },
                    )
                )
                notices = []
                for _ in range(3):
                    result = session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
                    notices.append(result.message)
                    if "Ivy Chen" in result.message:
                        break
                thread_view = session.step(StructuredAction("read.thread", {"target": "ivy"}))
            finally:
                session.close()
            self.assertTrue(any("Ivy Shah" in message for message in notices))
            self.assertIn("[inform.blocker]", thread_view.message)
            self.assertIn("actor.ivy.cannot_commit_engineering_eta", thread_view.message)

    def test_internal_rollout_request_clarification_to_dana_is_covered_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "dana_clarification.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True, coverage_enforcement="strict")
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "dana",
                            "act_id": "request.clarification",
                            "slots": {},
                            "body": "What tradeoff do you need from me to make the staged path real?",
                        },
                    )
                )
                for _ in range(3):
                    session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
                thread_view = session.step(StructuredAction("read.thread", {"target": "dana"}))
            finally:
                session.close()
            self.assertIn("[inform.blocker]", thread_view.message)
            self.assertIn("Dana backs staged rollout if engaged early", thread_view.message)

    def test_internal_rollout_inform_blocker_to_dana_is_covered_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "dana_blocker.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True, coverage_enforcement="strict")
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "dana",
                            "act_id": "inform.blocker",
                            "slots": {"target_actor_id": "dana"},
                            "body": "The approval path is blocked until we settle whether staged rollout is the only credible option.",
                        },
                    )
                )
                for _ in range(3):
                    session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
                thread_view = session.step(StructuredAction("read.thread", {"target": "dana"}))
            finally:
                session.close()
            self.assertIn("[request.clarification]", thread_view.message)
            self.assertIn("actor.dana.needs_actionable_summary", thread_view.message)

    def test_internal_rollout_inform_decision_to_dana_is_covered_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "dana_decision.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True, coverage_enforcement="strict")
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "dana",
                            "act_id": "inform.decision",
                            "slots": {
                                "decision_key": "project.launch_scope",
                                "decision_value": "staged_rollout",
                                "target_actor_id": "dana",
                            },
                            "body": "We are leaning staged, but I need your backing on the tradeoff.",
                        },
                    )
                )
                for _ in range(3):
                    session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
                thread_view = session.step(StructuredAction("read.thread", {"target": "dana"}))
            finally:
                session.close()
            self.assertIn("[request.clarification]", thread_view.message)
            self.assertIn("actor.dana.requires_decision_rationale", thread_view.message)

    def test_internal_rollout_request_approval_to_dana_is_covered_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "dana_request_approval.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True, coverage_enforcement="strict")
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "dana",
                            "act_id": "request.approval",
                            "slots": {"task_id": "approval_review"},
                            "body": "Can you approve this path now so I can keep the launch moving?",
                        },
                    )
                )
                for _ in range(3):
                    session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
                thread_view = session.step(StructuredAction("read.thread", {"target": "dana"}))
            finally:
                session.close()
            self.assertIn("[inform.blocker]", thread_view.message)
            self.assertIn("project.approval_owner", thread_view.message)

    def test_internal_rollout_request_ownership_to_dana_after_staged_rollout_is_covered_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "dana_request_ownership.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True, coverage_enforcement="strict")
            try:
                session.engine.store.update_project_state({"launch_scope": "staged_rollout"})
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "dana",
                            "act_id": "request.ownership",
                            "slots": {"task_id": "config_rollout"},
                            "body": "Now that staged rollout is the path, who owns delivery versus approval versus sponsor decisions?",
                        },
                    )
                )
                for _ in range(3):
                    session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
                thread_view = session.step(StructuredAction("read.thread", {"target": "dana"}))
            finally:
                session.close()
            self.assertIn("[inform.decision]", thread_view.message)
            self.assertIn("project.ownership_split", thread_view.message)

    def test_internal_rollout_negotiate_scope_to_dana_is_covered_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "dana_negotiate_scope.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True, coverage_enforcement="strict")
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "dana",
                            "act_id": "negotiate.scope",
                            "slots": {
                                "approved_scope": "staged_rollout",
                                "rejected_scope": "full_rollout",
                            },
                            "body": "If full scope is not credible, I want to negotiate down to staged rollout now.",
                        },
                    )
                )
                for _ in range(3):
                    session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
                thread_view = session.step(StructuredAction("read.thread", {"target": "dana"}))
            finally:
                session.close()
            self.assertIn("[request.scope_tradeoff]", thread_view.message)
            self.assertIn("actor.dana.wants_real_rollout_story", thread_view.message)

    def test_internal_rollout_inform_status_update_to_dana_is_covered_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "dana_status_update.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True, coverage_enforcement="strict")
            try:
                execute_script(session.engine, session.evaluator, EXAMPLES / "internal_rollout_smoke" / "smoke.tpm", echo=False, emit=False)
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "dana",
                            "act_id": "inform.status_update",
                            "slots": {"task_id": "config_rollout"},
                            "body": "Status update: staged rollout is aligned, but approval and readiness still need follow-through.",
                        },
                    )
                )
                for _ in range(3):
                    session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
                thread_view = session.step(StructuredAction("read.thread", {"target": "dana"}))
            finally:
                session.close()
            self.assertIn("[request.clarification]", thread_view.message)
            self.assertIn("project.sponsor_needs_clarity", thread_view.message)

    def test_internal_rollout_escalate_to_sponsor_unknown_dependency_is_covered_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "dana_escalate_unknown.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True, coverage_enforcement="strict")
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "dana",
                            "act_id": "escalate.to_sponsor",
                            "slots": {"urgency": "now"},
                            "body": "Escalating because approval is at risk and I need sponsor help on the rollout path.",
                        },
                    )
                )
                for _ in range(3):
                    session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
                thread_view = session.step(StructuredAction("read.thread", {"target": "dana"}))
            finally:
                session.close()
            self.assertIn("[request.scope_tradeoff]", thread_view.message)
            self.assertIn("actor.dana.prefers_direct_tradeoff_over_self_escalation", thread_view.message)

    def test_internal_rollout_escalate_to_sponsor_short_timing_is_covered_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "dana_escalate_short.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True, coverage_enforcement="strict")
            try:
                session.step(StructuredAction("wait.duration", {"minutes": 390}))
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "dana",
                            "act_id": "escalate.to_sponsor",
                            "slots": {"urgency": "now"},
                            "body": "Escalating because approval is at risk and I need sponsor help on the rollout path.",
                        },
                    )
                )
                for _ in range(3):
                    session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
                thread_view = session.step(StructuredAction("read.thread", {"target": "dana"}))
            finally:
                session.close()
            self.assertIn("[request.scope_tradeoff]", thread_view.message)
            self.assertIn("actor.dana.prefers_direct_tradeoff_over_self_escalation", thread_view.message)

    def test_internal_rollout_escalate_to_sponsor_short_timing_low_trust_is_covered_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "dana_escalate_short_low_trust.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True, coverage_enforcement="strict")
            try:
                session.step(StructuredAction("wait.duration", {"minutes": 390}))
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "dana",
                            "act_id": "escalate.to_sponsor",
                            "slots": {"urgency": "now"},
                            "body": "Escalating because approval is at risk and I need sponsor help on the rollout path.",
                        },
                    )
                )
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "dana",
                            "act_id": "escalate.to_sponsor",
                            "slots": {"urgency": "now"},
                            "body": "Escalating again because the rollout path still needs sponsor intervention.",
                        },
                    )
                )
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "dana",
                            "act_id": "escalate.to_sponsor",
                            "slots": {"urgency": "now"},
                            "body": "Escalating a third time because we still need the sponsor tradeoff clarified.",
                        },
                    )
                )
                for _ in range(3):
                    session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
                thread_view = session.step(StructuredAction("read.thread", {"target": "dana"}))
            finally:
                session.close()
            self.assertIn("[request.scope_tradeoff]", thread_view.message)
            self.assertIn("actor.dana.prefers_direct_tradeoff_over_self_escalation", thread_view.message)

    def test_internal_rollout_request_ownership_to_leo_is_covered_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "leo_request_ownership.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True, coverage_enforcement="strict")
            try:
                session.step(StructuredAction("wait.duration", {"minutes": 390}))
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "leo",
                            "act_id": "request.ownership",
                            "slots": {"task_id": "config_rollout"},
                            "body": "Can you take explicit ownership of the rollout execution path from engineering?",
                        },
                    )
                )
                for _ in range(3):
                    session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
                thread_view = session.step(StructuredAction("read.thread", {"target": "leo"}))
            finally:
                session.close()
            self.assertIn("[inform.blocker]", thread_view.message)
            self.assertIn("project.engineering_owner", thread_view.message)

    def test_internal_rollout_inform_decision_to_ivy_is_covered_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "ivy_decision.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True, coverage_enforcement="strict")
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "ivy",
                            "act_id": "inform.decision",
                            "slots": {"decision_key": "launch_scope", "decision_value": "staged_rollout"},
                            "body": "We are going with the staged rollout path.",
                        },
                    )
                )
                notices = []
                for _ in range(3):
                    result = session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
                    notices.append(result.message)
                    if "Ivy Chen" in result.message:
                        break
                thread_view = session.step(StructuredAction("read.thread", {"target": "ivy"}))
            finally:
                session.close()
            self.assertTrue(any("Ivy Shah" in message for message in notices))
            self.assertIn("[inform.status_update]", thread_view.message)
            self.assertIn("decision_received", thread_view.message)

    def test_internal_rollout_inform_blocker_to_leo_is_covered_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "leo_blocker.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True, coverage_enforcement="strict")
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "leo",
                            "act_id": "inform.blocker",
                            "slots": {"task_id": "approval_review"},
                            "body": "Security approval is still blocked.",
                        },
                    )
                )
                notices = []
                for _ in range(3):
                    result = session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
                    notices.append(result.message)
                    if "Leo Park" in result.message:
                        break
                thread_view = session.step(StructuredAction("read.thread", {"target": "leo"}))
            finally:
                session.close()
            self.assertTrue(any("Leo Park" in message for message in notices))
            self.assertIn("[inform.status_update]", thread_view.message)
            self.assertIn("actor.leo.shared_blocker_context", thread_view.message)

    def test_internal_rollout_negotiate_scope_to_leo_is_covered_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "leo_negotiate_scope.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True, coverage_enforcement="strict")
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "leo",
                            "act_id": "negotiate.scope",
                            "slots": {"proposed_scope": "staged_rollout"},
                            "body": "If we cut to the staged rollout, is that the credible path this week?",
                        },
                    )
                )
                notices = []
                for _ in range(3):
                    result = session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
                    notices.append(result.message)
                    if "Leo Park" in result.message:
                        break
                thread_view = session.step(StructuredAction("read.thread", {"target": "leo"}))
            finally:
                session.close()
            self.assertTrue(any("Leo Park" in message for message in notices))
            self.assertIn("[inform.decision]", thread_view.message)
            self.assertIn("actor.leo.aligned_on_scope", thread_view.message)

    def test_internal_rollout_request_ownership_to_ivy_is_covered_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "ivy_ownership.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True, coverage_enforcement="strict")
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "ivy",
                            "act_id": "request.ownership",
                            "slots": {"task_id": "approval_review"},
                            "body": "Who owns the approval review path and what still needs to happen?",
                        },
                    )
                )
                notices = []
                for _ in range(3):
                    result = session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
                    notices.append(result.message)
                    if "Ivy Chen" in result.message:
                        break
                thread_view = session.step(StructuredAction("read.thread", {"target": "ivy"}))
            finally:
                session.close()
            self.assertTrue(any("Ivy Shah" in message for message in notices))
            self.assertIn("[inform.decision]", thread_view.message)
            self.assertIn("project.approval_owner", thread_view.message)

    def test_internal_rollout_negotiate_scope_to_ivy_is_covered_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "ivy_negotiate_scope.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True, coverage_enforcement="strict")
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "ivy",
                            "act_id": "negotiate.scope",
                            "slots": {"proposed_scope": "staged_rollout"},
                            "body": "If we narrow to the staged rollout, can your review move on that path?",
                        },
                    )
                )
                notices = []
                for _ in range(3):
                    result = session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
                    notices.append(result.message)
                    if "Ivy Chen" in result.message:
                        break
                thread_view = session.step(StructuredAction("read.thread", {"target": "ivy"}))
            finally:
                session.close()
            self.assertTrue(any("Ivy Shah" in message for message in notices))
            self.assertIn("[inform.decision]", thread_view.message)
            self.assertIn("actor.ivy.scope_requirement", thread_view.message)

    def test_internal_rollout_request_scope_tradeoff_to_ivy_after_scope_is_staged_is_covered_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "ivy_scope_tradeoff_clear.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True, coverage_enforcement="strict")
            try:
                execute_script(session.engine, session.evaluator, EXAMPLES / "internal_rollout_smoke" / "smoke.tpm", echo=False, emit=False)

                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "ivy",
                            "act_id": "request.scope_tradeoff",
                            "slots": {},
                            "body": "Any remaining scope tradeoff concerns from your side now that the staged path is set?",
                        },
                    )
                )
                for _ in range(3):
                    session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
                thread_view = session.step(StructuredAction("read.thread", {"target": "ivy"}))
            finally:
                session.close()
            self.assertIn("Any remaining scope tradeoff concerns from your side now that the staged path is set?", thread_view.message)
            self.assertIn("[inform.decision]", thread_view.message)

    def test_internal_rollout_request_feasibility_to_mia_is_covered_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "mia_feasibility.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True, coverage_enforcement="strict")
            try:
                session.step(
                    StructuredAction(
                        "chat.send",
                        {
                            "target": "mia",
                            "act_id": "request.feasibility",
                            "slots": {"task_id": "runbook_readiness"},
                            "body": "If we go staged, is ops support feasible from your side?",
                        },
                    )
                )
                notices = []
                for _ in range(3):
                    result = session.step(StructuredAction("wait.until_next_event", {"max_minutes": 60}))
                    notices.append(result.message)
                    if "Mia Torres" in result.message:
                        break
                thread_view = session.step(StructuredAction("read.thread", {"target": "mia"}))
            finally:
                session.close()
            self.assertTrue(any("Mia Torres" in message for message in notices))
            self.assertIn("[inform.decision]", thread_view.message)
            self.assertIn("actor.mia.ops_support", thread_view.message)

    def test_agent_replay_prints_timestamps_actor_and_trace_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "run"
            run_dir.mkdir(parents=True, exist_ok=True)
            report_path = run_dir / "benchmark_run.report.json"
            agent_trace = run_dir / "benchmark_run.agent_trace.jsonl"
            omniscient_trace = run_dir / "benchmark_run.omniscient_trace.jsonl"
            agent_trace.write_text(
                json.dumps(
                    {
                        "actor_id": "tpm",
                        "at": "2026-05-05T09:00:00",
                        "event_type": "task.tracker_updated",
                        "phase": "interaction_start",
                        "summary": "Updated tracker note for approval_review",
                    }
                )
                + "\n"
            )
            omniscient_trace.write_text(
                json.dumps(
                    {
                        "actor_id": "dana",
                        "at": "2026-05-05T10:00:00",
                        "event_type": "npc.message_sent",
                        "phase": "interaction_start",
                        "summary": "dana proactively messaged TPM",
                    }
                )
                + "\n"
            )
            report_path.write_text(
                json.dumps(
                    {
                        "trace_paths": {
                            "agent_trace": str(agent_trace),
                            "omniscient_trace": str(omniscient_trace),
                        }
                    }
                )
            )
            (run_dir / "agent_run.json").write_text(
                json.dumps(
                    {
                        "run": {
                            "scenario_id": "internal_rollout_smoke",
                            "seed": 11,
                            "model": "gpt-test",
                            "score": 5.0,
                            "turns_taken": 1,
                            "protocol_failure": False,
                            "report_path": str(report_path),
                        },
                        "decisions": [
                            {
                                "turn": 1,
                                "observation_time": "2026-05-05T09:00:00",
                                "decision": {"action": {"action_type": "task.note"}},
                                "step_result": {
                                    "time_before": "2026-05-05T09:00:00",
                                    "time_after": "2026-05-05T09:03:00",
                                    "message": "Updated tracker note for approval_review.",
                                },
                                "validation_errors": [],
                            }
                        ],
                    }
                )
            )
            output = StringIO()
            with redirect_stdout(output):
                run_agent_replay(str(run_dir))
            rendered = output.getvalue()
            self.assertIn("Turn log (TPM actions):", rendered)
            self.assertIn("TPM task.note", rendered)
            self.assertIn("2026-05-05T09:00:00 -> 2026-05-05T09:03:00", rendered)
            self.assertIn("Full traces:", rendered)

            output = StringIO()
            with redirect_stdout(output):
                _run_agent_replay(str(run_dir), events="omniscient", event_limit=10)
            rendered = output.getvalue()
            self.assertIn("Chronological omniscient events:", rendered)
            self.assertIn("[2026-05-05T10:00:00] dana npc.message_sent", rendered)

    def test_agent_replay_resolves_paths_after_run_directory_move(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            original_run_dir = Path(tmpdir) / "run"
            original_run_dir.mkdir(parents=True, exist_ok=True)
            report_path = original_run_dir / "benchmark_run.report.json"
            agent_trace = original_run_dir / "benchmark_run.agent_trace.jsonl"
            omniscient_trace = original_run_dir / "benchmark_run.omniscient_trace.jsonl"
            agent_trace.write_text(
                json.dumps(
                    {
                        "actor_id": "tpm",
                        "at": "2026-05-05T09:00:00",
                        "event_type": "task.tracker_updated",
                        "phase": "interaction_start",
                        "summary": "Updated tracker note for approval_review",
                    }
                )
                + "\n"
            )
            omniscient_trace.write_text(
                json.dumps(
                    {
                        "actor_id": "dana",
                        "at": "2026-05-05T10:00:00",
                        "event_type": "npc.message_sent",
                        "phase": "interaction_start",
                        "summary": "dana proactively messaged TPM",
                    }
                )
                + "\n"
            )
            report_path.write_text(
                json.dumps(
                    {
                        "trace_paths": {
                            "agent_trace": str(agent_trace),
                            "omniscient_trace": str(omniscient_trace),
                        }
                    }
                )
            )
            (original_run_dir / "agent_run.json").write_text(
                json.dumps(
                    {
                        "run": {
                            "scenario_id": "internal_rollout_smoke",
                            "seed": 11,
                            "model": "gpt-test",
                            "score": 5.0,
                            "turns_taken": 1,
                            "protocol_failure": False,
                            "report_path": str(report_path),
                        },
                        "decisions": [
                            {
                                "turn": 1,
                                "observation_time": "2026-05-05T09:00:00",
                                "decision": {"action": {"action_type": "task.note"}},
                                "step_result": {
                                    "time_before": "2026-05-05T09:00:00",
                                    "time_after": "2026-05-05T09:03:00",
                                    "message": "Updated tracker note for approval_review.",
                                },
                                "validation_errors": [],
                            }
                        ],
                    }
                )
            )
            moved_run_dir = Path(tmpdir) / "moved_run"
            original_run_dir.rename(moved_run_dir)

            output = StringIO()
            with redirect_stdout(output):
                _run_agent_replay(str(moved_run_dir), events="omniscient", event_limit=10)
            rendered = output.getvalue()
            self.assertIn(str(moved_run_dir / "benchmark_run.agent_trace.jsonl"), rendered)
            self.assertIn(str(moved_run_dir / "benchmark_run.omniscient_trace.jsonl"), rendered)
            self.assertIn("[2026-05-05T10:00:00] dana npc.message_sent", rendered)


if __name__ == "__main__":
    unittest.main()
