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
from tpm_sim.cli import _resolve_authoring_model, _run_agent_replay, execute_command, execute_script
from tpm_sim.cli import (
    init_db,
    run_agent,
    run_agent_replay,
    run_author_compile_contract,
    run_author_init,
    run_author_synthesize,
    run_author_validate,
)
from tpm_sim.coverage_artifacts import build_starter_contract
from tpm_sim.engine import CoverageMissError, SimulationEngine
from tpm_sim.environment import ActionValidationError, EnvironmentSession, StructuredAction, validate_structured_action
from tpm_sim.evaluator import Evaluator
from tpm_sim.model_client import ModelResponse
from tpm_sim.performance import build_run_summary, export_bundle_summary, export_run_summary
from tpm_sim.runtime_env import autoload_project_dotenv
from tpm_sim.scenario import load_scenario_bundle, seed_store
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

    def test_validate_structured_action_rejects_unknown_act_id(self) -> None:
        with self.assertRaises(ActionValidationError):
            validate_structured_action(
                StructuredAction(
                    "chat.send",
                    {"target": "ivy", "act_id": "reminder.send", "slots": {}, "body": "ping"},
                )
            )

    def test_agent_runner_repair_path_succeeds_after_one_invalid_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "repair.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True)
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

    def test_agent_runner_records_protocol_failure_after_second_invalid_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "invalid.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True)
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
            self.assertIsNone(payload["decisions"][0]["step_result"])

    def test_agent_runner_records_coverage_miss_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "coverage_miss.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True)
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
            self.assertIsNone(payload["decisions"][0]["step_result"])

    def test_scripted_agent_runner_can_complete_internal_rollout_smoke(self) -> None:
        actions = [
            {"action_type": "read.doc", "arguments": {"doc_id": "DOC-BRIEF-100"}, "reason": "read the kickoff brief"},
            {
                "action_type": "chat.send",
                "arguments": {
                    "target": "leo",
                    "act_id": "request.feasibility",
                    "slots": {"task_id": "config_rollout"},
                    "body": "Need the honest path for Wednesday."
                },
                "reason": "get the real engineering feasibility"
            },
            {"action_type": "wait.until_next_event", "arguments": {"max_minutes": 180}, "reason": "wait for Leo's reply"},
            {"action_type": "read.thread", "arguments": {"target": "leo"}, "reason": "read Leo's reply"},
            {
                "action_type": "chat.send",
                "arguments": {
                    "target": "dana",
                    "act_id": "request.scope_tradeoff",
                    "slots": {"task_id": "config_rollout"},
                    "body": "If full rollout is not credible, will you back the staged path?"
                },
                "reason": "drive the scope decision"
            },
            {"action_type": "wait.until_next_event", "arguments": {"max_minutes": 180}, "reason": "wait for Dana"},
            {"action_type": "read.thread", "arguments": {"target": "dana"}, "reason": "read Dana's decision"},
            {
                "action_type": "chat.send",
                "arguments": {
                    "target": "ivy",
                    "act_id": "request.approval",
                    "slots": {"task_id": "approval_review"},
                    "body": "Requesting approval for the staged rollout path."
                },
                "reason": "secure approval before the cutoff"
            },
            {"action_type": "wait.until_next_event", "arguments": {"max_minutes": 240}, "reason": "wait for approval reply"},
            {"action_type": "read.thread", "arguments": {"target": "ivy"}, "reason": "read the approval response"},
            {"action_type": "wait.until_next_event", "arguments": {"max_minutes": 240}, "reason": "wait for Leo to cool down"},
            {
                "action_type": "chat.send",
                "arguments": {
                    "target": "leo",
                    "act_id": "request.eta",
                    "slots": {"task_id": "config_rollout"},
                    "body": "What is the credible ETA for the staged rollout path?"
                },
                "reason": "convert the aligned plan into a concrete ETA"
            },
            {"action_type": "wait.until_next_event", "arguments": {"max_minutes": 240}, "reason": "wait for the ETA response"},
            {"action_type": "read.thread", "arguments": {"target": "leo"}, "reason": "read the ETA response"},
            {
                "action_type": "docs.write",
                "arguments": {
                    "doc_type": "runbook",
                    "title": "Rollout runbook",
                    "body": "Drafted staged rollout checklist, rollback plan, and owner notes."
                },
                "reason": "close the small readiness side path"
            },
            {"action_type": "wait.duration", "arguments": {"minutes": 600}, "reason": "let the remaining work land"}
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "scripted.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True)
            try:
                record = AgentRunner(ScriptedAdapter(actions), max_turns=25).run(
                    session,
                    seed=11,
                    output_dir=str(Path(tmpdir) / "scripted_run"),
                    model_name="scripted-agent",
                )
            finally:
                session.close()
            self.assertGreaterEqual(record.score, 95.0)
            self.assertFalse(record.protocol_failure)

    def test_export_run_summary_builds_canonical_tpm_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "summary.sqlite")
            session = EnvironmentSession.create(db_path, "internal_rollout_smoke", 11, force=True)
            try:
                record = AgentRunner(
                    ScriptedAdapter(
                        [
                            {"action_type": "read.doc", "arguments": {"doc_id": "DOC-BRIEF-100"}, "reason": "read the kickoff brief"},
                            {"action_type": "wait.duration", "arguments": {"minutes": 30}, "reason": "advance"},
                        ]
                    ),
                    max_turns=2,
                ).run(
                    session,
                    seed=11,
                    output_dir=str(Path(tmpdir) / "summary_run"),
                    model_name="mock-model",
                )
            finally:
                session.close()
            summary = export_run_summary(Path(record.output_dir), write_files=True)
            self.assertEqual(summary["schema_version"], "tpm_performance_summary_v1")
            self.assertIn("tpm_competency_profile", summary)
            self.assertIn("run_health", summary)
            self.assertEqual(summary["narrative"]["source"], "deterministic_template")
            self.assertTrue((Path(record.output_dir) / "tpm_performance_summary.json").exists())

    def test_run_summary_surfaces_new_driver_discovery_lines_when_relevant(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "summary_success.sqlite")
            engine, evaluator = build_runtime(db_path, "northstar_launch_week", seed=11)
            try:
                execute_script(engine, evaluator, EXAMPLES / "golden.tpm", echo=False, emit=False)
                success_summary = build_run_summary(
                    evaluator.evaluate(),
                    scenario_bundle=load_scenario_bundle("northstar_launch_week"),
                )
            finally:
                engine.store.close()

            db_path = str(Path(tmpdir) / "summary_failure.sqlite")
            engine, evaluator = build_runtime(db_path, "northstar_launch_week", seed=11)
            try:
                execute_script(engine, evaluator, EXAMPLES / "false_green.tpm", echo=False, emit=False)
                failure_summary = build_run_summary(
                    evaluator.evaluate(),
                    scenario_bundle=load_scenario_bundle("northstar_launch_week"),
                )
            finally:
                engine.store.close()

        self.assertIn("stakeholder_driver_discovery", [item["id"] for item in success_summary["key_successes"]])
        self.assertIn("stakeholder_driver_discovery", [item["id"] for item in failure_summary["key_failures"]])

    def test_export_bundle_summary_aggregates_run_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_summaries = [
                {
                    "run_header": {"seed": 11, "score": 55, "scenario_id": "internal_rollout_smoke", "summary_path": "a"},
                    "outcome_verdict": {"headline": "partial"},
                    "tpm_competency_profile": [{"id": "discovery_situation_awareness", "label": "Discovery & Situation Awareness", "score": 80}],
                    "outcome_profile": [{"id": "outcome_attainment", "label": "Outcome Attainment", "score": 40}],
                    "run_health": {"protocol_failure": False, "coverage_miss": False, "harness_interface_issues": [], "scenario_authoring_issues": []},
                    "key_failures": [{"id": "scope_aligned_on_time"}],
                },
                {
                    "run_header": {"seed": 29, "score": 35, "scenario_id": "internal_rollout_smoke", "summary_path": "b"},
                    "outcome_verdict": {"headline": "failed"},
                    "tpm_competency_profile": [{"id": "discovery_situation_awareness", "label": "Discovery & Situation Awareness", "score": 60}],
                    "outcome_profile": [{"id": "outcome_attainment", "label": "Outcome Attainment", "score": 20}],
                    "run_health": {"protocol_failure": False, "coverage_miss": False, "harness_interface_issues": [], "scenario_authoring_issues": []},
                    "key_failures": [{"id": "scope_aligned_on_time"}],
                },
            ]
            summary = export_bundle_summary(Path(tmpdir), run_summaries, scenario_id="internal_rollout_smoke", model="mock-model", seed_bundle=[11, 29], write_files=False)
            self.assertEqual(summary["schema_version"], "tpm_bundle_performance_summary_v1")
            self.assertEqual(summary["headline"]["mean_score"], 45.0)
            self.assertEqual(summary["top_recurring_failure_themes"][0]["id"], "scope_aligned_on_time")

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
            self.assertIn("approval_required", feasibility_facts)

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
            self.assertIn("Project: Internal Rollout Project", output)
            self.assertIn("Decision rights: approve scope", output)
            self.assertIn("Proposal Status:", output)

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
        self.assertIn(("mia", "request.feasibility"), selectors)

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
                mock.patch("tpm_sim.cli.export_run_summary", return_value=summary),
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

    def test_internal_rollout_smoke_script_runs_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "smoke.sqlite")
            engine, evaluator = build_runtime(db_path, "internal_rollout_smoke", seed=11)
            try:
                execute_script(engine, evaluator, EXAMPLES / "internal_rollout_smoke" / "smoke.tpm", echo=False, emit=False)
                report = evaluator.evaluate()
            finally:
                engine.store.close()
            self.assertGreaterEqual(report["total_score"], 95.0)

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
            self.assertIn("Full rollout is not credible", thread_view.message)

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
            self.assertIn("do not give the delivery ETA", thread_view.message)

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
            self.assertIn("recommend the staged path clearly", thread_view.message)

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
            self.assertIn("bring me the staged tradeoff", thread_view.message.lower())

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
            self.assertIn("need the concrete tradeoff", thread_view.message.lower())

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
            self.assertIn("not approving a vague plan", thread_view.message.lower())

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
            self.assertIn("Send the concrete staged request", thread_view.message)

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
            self.assertIn("should stay on the staged path", thread_view.message)

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
            self.assertIn("scope move is staged rollout", thread_view.message)

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
            self.assertIn("I own the approval review", thread_view.message)

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
            self.assertIn("I do not set product scope", thread_view.message)

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
            self.assertIn("staged rollout is feasible", thread_view.message.lower())

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


if __name__ == "__main__":
    unittest.main()
