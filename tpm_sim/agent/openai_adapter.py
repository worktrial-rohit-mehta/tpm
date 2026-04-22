from __future__ import annotations

import json
from typing import Any, Optional

from tpm_sim.agent.base import AgentDecision
from tpm_sim.agent.prompts import ACTION_DECISION_SCHEMA, PROMPT_PACK_VERSION, build_agent_prompt
from tpm_sim.model_client import ModelClient


class OpenAIResponsesAgentAdapter:
    name = "openai"
    prompt_pack_version = PROMPT_PACK_VERSION

    def __init__(self, model_client: ModelClient, model: str, *, temperature: float = 0.0, top_p: float = 1.0):
        self.model_client = model_client
        self.model = model
        self.temperature = temperature
        self.top_p = top_p

    def start(self, run_context: dict[str, Any]) -> dict[str, Any]:
        return {"run_context": run_context}

    def decide(
        self,
        session: dict[str, Any],
        observation: dict[str, Any],
        *,
        repair_feedback: Optional[str] = None,
    ) -> AgentDecision:
        prompt_spec = build_agent_prompt(observation, repair_feedback=repair_feedback)
        response = self.model_client.generate_structured(
            schema_name="tpm_next_action",
            schema=ACTION_DECISION_SCHEMA,
            prompt_spec=prompt_spec,
            config={"model": self.model, "temperature": self.temperature, "top_p": self.top_p},
        )
        parsed = json.loads(response.text)
        return AgentDecision(
            action=parsed,
            summary=parsed.get("reason", ""),
            raw_model_output=response.raw,
            usage=response.usage,
            latency_ms=response.latency_ms,
            validation_errors=[],
        )

    def finish(self, session: dict[str, Any], final_report: dict[str, Any]) -> None:
        session["final_report"] = final_report
