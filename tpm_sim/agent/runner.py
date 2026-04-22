from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from tpm_sim.agent.base import AgentAdapter, AgentRunRecord
from tpm_sim.common import stable_digest
from tpm_sim.engine import CoverageMissError
from tpm_sim.environment import ActionValidationError, EnvironmentSession, coerce_action


DEFAULT_AGENT_MAX_TURNS = 200


class AgentRunner:
    def __init__(self, adapter: AgentAdapter, *, max_turns: int = DEFAULT_AGENT_MAX_TURNS):
        self.adapter = adapter
        self.max_turns = max_turns

    def run(
        self,
        session: EnvironmentSession,
        *,
        seed: int,
        output_dir: str,
        model_name: str,
        event_stream: str = "none",
        on_event: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> AgentRunRecord:
        run_id = stable_digest(
            session.engine.scenario["id"],
            session.engine.scenario_digest(),
            seed,
            self.adapter.name,
            model_name,
            datetime.utcnow().isoformat(),
        )[:12]
        outdir = Path(output_dir)
        outdir.mkdir(parents=True, exist_ok=True)
        decisions: list[dict[str, Any]] = []
        protocol_failure = False
        protocol_failure_reason: Optional[str] = None
        agent_session = self.adapter.start(
            {
                "scenario_id": session.engine.scenario["id"],
                "scenario_digest": session.engine.scenario_digest(),
                "seed": seed,
                "max_turns": self.max_turns,
            }
        )
        last_event_id = 0
        if event_stream != "none" and on_event is not None:
            relevant_rows = self._stream_rows(session, event_stream)
            if relevant_rows:
                last_event_id = int(relevant_rows[-1]["id"])
        turns = 0
        end_at = session.engine.store.get_meta("simulation_end")
        end_dt = datetime.strptime(end_at, "%Y-%m-%dT%H:%M:%S")
        while turns < self.max_turns and session.engine.now() < end_dt:
            observation = session.observe()
            repair_feedback = None
            decision_payload = None
            errors: list[str] = []
            step_result = None
            executed_action_ref: str | None = None
            repair_attempts = 0
            for attempt in range(2):
                decision = self.adapter.decide(agent_session, observation, repair_feedback=repair_feedback)
                decision_payload = decision
                try:
                    action = coerce_action(decision.action)
                    step_result = session.step(action)
                    executed_action_ref = self._latest_tpm_action_ref(session)
                    if event_stream != "none" and on_event is not None:
                        last_event_id = self._emit_new_events(
                            session,
                            event_stream=event_stream,
                            last_event_id=last_event_id,
                            on_event=on_event,
                        )
                    errors = []
                    break
                except ActionValidationError as exc:
                    errors = [str(exc)]
                    repair_feedback = str(exc)
                    repair_attempts += 1
                except CoverageMissError as exc:
                    errors = [f"Benchmark coverage miss: {exc}"]
                    repair_feedback = None
                    break
                except (KeyError, ValueError) as exc:
                    errors = [f"Action execution failed: {exc}"]
                    repair_feedback = errors[0]
                    repair_attempts += 1
            else:
                protocol_failure = True
                protocol_failure_reason = errors[0] if errors else "invalid_action"
                decisions.append(
                    {
                        "turn": turns + 1,
                        "observation_time": observation["time"],
                        "decision": decision_payload.to_dict() if decision_payload else {},
                        "executed_action_ref": None,
                        "step_result": None,
                        "validation_errors": errors,
                        "repair_attempts": repair_attempts,
                    }
                )
                break

            if step_result is None:
                protocol_failure = True
                protocol_failure_reason = errors[0] if errors else "step_failed"
                decisions.append(
                    {
                        "turn": turns + 1,
                        "observation_time": observation["time"],
                        "decision": decision_payload.to_dict() if decision_payload else {},
                        "executed_action_ref": None,
                        "step_result": None,
                        "validation_errors": errors,
                        "repair_attempts": repair_attempts,
                    }
                )
                break

            decisions.append(
                {
                    "turn": turns + 1,
                    "observation_time": observation["time"],
                    "decision": decision_payload.to_dict(),
                    "executed_action_ref": executed_action_ref,
                    "step_result": step_result.to_dict(),
                    "validation_errors": errors,
                    "repair_attempts": repair_attempts,
                }
            )
            turns += 1

        termination_reason = "completed"
        if protocol_failure:
            termination_reason = "protocol_failure"
        elif turns >= self.max_turns and session.engine.now() < end_dt:
            termination_reason = "max_turns_reached"
        elif session.engine.now() >= end_dt:
            termination_reason = "scenario_horizon_reached"

        simulated_end_time = session.engine.now().strftime("%Y-%m-%dT%H:%M:%S")
        export = session.export_report(str(outdir / "benchmark_run"))
        final_report = export["report"]
        self.adapter.finish(agent_session, final_report)
        agent_log_path = outdir / "agent_run.json"
        run_record = AgentRunRecord(
            run_id=run_id,
            scenario_id=session.engine.scenario["id"],
            scenario_digest=session.engine.scenario_digest(),
            seed=seed,
            adapter=self.adapter.name,
            model=model_name,
            prompt_pack_version=self.adapter.prompt_pack_version,
            max_turns=self.max_turns,
            turns_taken=turns,
            termination_reason=termination_reason,
            simulated_end_time=simulated_end_time,
            protocol_failure=protocol_failure,
            protocol_failure_reason=protocol_failure_reason,
            score=float(final_report["total_score"]),
            output_dir=str(outdir),
            report_path=export["report_path"],
            agent_log_path=str(agent_log_path),
        )
        payload = {
            "run": run_record.to_dict(),
            "decisions": decisions,
        }
        agent_log_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
        return run_record

    def _emit_new_events(
        self,
        session: EnvironmentSession,
        *,
        event_stream: str,
        last_event_id: int,
        on_event: Callable[[dict[str, Any]], None],
    ) -> int:
        new_rows = [row for row in self._stream_rows(session, event_stream) if int(row["id"]) > last_event_id]
        for row in new_rows:
            on_event(
                {
                    "id": int(row["id"]),
                    "at": row["at"],
                    "phase": row["phase"],
                    "event_type": row["event_type"],
                    "actor_id": row["actor_id"],
                    "visibility": row["visibility"],
                    "summary": row["summary"],
                    "payload": session.engine.deserialize(row["payload_json"], {}),
                }
            )
        if new_rows:
            return int(new_rows[-1]["id"])
        return last_event_id

    def _stream_rows(self, session: EnvironmentSession, event_stream: str) -> list[Any]:
        if event_stream == "agent":
            return session.engine.store.event_log("agent")
        return session.engine.store.event_log()

    def _latest_tpm_action_ref(self, session: EnvironmentSession) -> str | None:
        for row in reversed(session.engine.store.actions()):
            if row["actor_id"] == "tpm":
                return f"action:{int(row['id'])}"
        return None
