from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional, Protocol


@dataclass
class AgentDecision:
    action: dict[str, Any]
    summary: str
    raw_model_output: dict[str, Any]
    usage: dict[str, Any]
    latency_ms: int
    validation_errors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentRunRecord:
    run_id: str
    scenario_id: str
    scenario_digest: str
    seed: int
    adapter: str
    model: str
    prompt_pack_version: str
    max_turns: int
    turns_taken: int
    protocol_failure: bool
    protocol_failure_reason: Optional[str]
    score: float
    output_dir: str
    report_path: str
    agent_log_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgentAdapter(Protocol):
    name: str
    prompt_pack_version: str

    def start(self, run_context: dict[str, Any]) -> Any:
        ...

    def decide(
        self,
        session: Any,
        observation: dict[str, Any],
        *,
        repair_feedback: Optional[str] = None,
    ) -> AgentDecision:
        ...

    def finish(self, session: Any, final_report: dict[str, Any]) -> None:
        ...
