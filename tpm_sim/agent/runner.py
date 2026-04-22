from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from tpm_sim.agent.base import AgentAdapter, AgentRunRecord
from tpm_sim.common import stable_digest
from tpm_sim.engine import CoverageMissError
from tpm_sim.environment import ActionValidationError, EnvironmentSession, coerce_action


class AgentRunner:
    def __init__(self, adapter: AgentAdapter, *, max_turns: int = 80):
        self.adapter = adapter
        self.max_turns = max_turns

    def run(
        self,
        session: EnvironmentSession,
        *,
        seed: int,
        output_dir: str,
        model_name: str,
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
        turns = 0
        end_at = session.engine.store.get_meta("simulation_end")
        while turns < self.max_turns and session.engine.now() < datetime.strptime(end_at, "%Y-%m-%dT%H:%M:%S"):
            observation = session.observe()
            repair_feedback = None
            decision_payload = None
            errors: list[str] = []
            step_result = None
            for attempt in range(2):
                decision = self.adapter.decide(agent_session, observation, repair_feedback=repair_feedback)
                decision_payload = decision
                try:
                    action = coerce_action(decision.action)
                    step_result = session.step(action)
                    errors = []
                    break
                except ActionValidationError as exc:
                    errors = [str(exc)]
                    repair_feedback = str(exc)
                except CoverageMissError as exc:
                    errors = [f"Benchmark coverage miss: {exc}"]
                    repair_feedback = None
                    break
                except (KeyError, ValueError) as exc:
                    errors = [f"Action execution failed: {exc}"]
                    repair_feedback = errors[0]
            else:
                protocol_failure = True
                protocol_failure_reason = errors[0] if errors else "invalid_action"
                decisions.append(
                    {
                        "turn": turns + 1,
                        "observation_time": observation["time"],
                        "decision": decision_payload.to_dict() if decision_payload else {},
                        "step_result": None,
                        "validation_errors": errors,
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
                        "step_result": None,
                        "validation_errors": errors,
                    }
                )
                break

            decisions.append(
                {
                    "turn": turns + 1,
                    "observation_time": observation["time"],
                    "decision": decision_payload.to_dict(),
                    "step_result": step_result.to_dict(),
                    "validation_errors": errors,
                }
            )
            turns += 1

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
