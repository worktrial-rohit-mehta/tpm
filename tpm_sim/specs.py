from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


ACT_TAXONOMY_VERSION = "ACT_TAXONOMY_v1"
PREDICATE_DSL_VERSION = "PREDICATE_DSL_v1"
CONTEXT_FAMILY_SCHEMA_VERSION = "CONTEXT_FAMILY_SCHEMA_v1"
EVAL_DSL_VERSION = "EVAL_DSL_v1"
RENDERER_VERSION = "renderer_v1"


@dataclass(frozen=True)
class ActDefinition:
    act_id: str
    valid_surfaces: tuple[str, ...]
    commitment_effect: str
    belief_effect: str
    relationship_effect: str
    meeting_valid: bool


ACT_DEFINITIONS = {
    "inform.status_update": ActDefinition("inform.status_update", ("chat", "meeting"), "none", "yes", "neutral", True),
    "inform.risk": ActDefinition("inform.risk", ("chat", "meeting"), "indirect", "yes", "neutral", True),
    "inform.blocker": ActDefinition("inform.blocker", ("chat", "meeting"), "indirect", "yes", "neutral", True),
    "inform.decision": ActDefinition("inform.decision", ("chat", "meeting"), "indirect", "yes", "contextual", True),
    "inform.availability": ActDefinition("inform.availability", ("chat", "meeting"), "indirect", "yes", "neutral", True),
    "request.eta": ActDefinition("request.eta", ("chat", "meeting"), "proposal", "none", "contextual", True),
    "request.feasibility": ActDefinition("request.feasibility", ("chat", "meeting"), "proposal", "none", "contextual", True),
    "request.scope_tradeoff": ActDefinition("request.scope_tradeoff", ("chat", "meeting"), "proposal", "none", "positive", True),
    "request.review": ActDefinition("request.review", ("chat", "meeting"), "proposal", "none", "neutral", True),
    "request.approval": ActDefinition("request.approval", ("chat", "meeting"), "proposal", "none", "neutral", True),
    "request.clarification": ActDefinition("request.clarification", ("chat", "meeting"), "none", "none", "neutral", True),
    "request.ownership": ActDefinition("request.ownership", ("chat", "meeting"), "proposal", "none", "neutral", True),
    "negotiate.scope": ActDefinition("negotiate.scope", ("chat", "meeting"), "proposal", "yes", "positive", True),
    "negotiate.timeline": ActDefinition("negotiate.timeline", ("chat", "meeting"), "proposal", "yes", "neutral", True),
    "negotiate.ownership": ActDefinition("negotiate.ownership", ("chat", "meeting"), "proposal", "yes", "neutral", True),
    "commit.propose": ActDefinition("commit.propose", ("chat", "meeting"), "create", "yes", "neutral", True),
    "commit.confirm": ActDefinition("commit.confirm", ("chat", "meeting"), "confirm", "yes", "positive", True),
    "commit.revise": ActDefinition("commit.revise", ("chat", "meeting"), "revise", "yes", "neutral", True),
    "commit.retract": ActDefinition("commit.retract", ("chat", "meeting"), "retract", "yes", "contextual", True),
    "approve.grant": ActDefinition("approve.grant", ("chat", "meeting"), "confirm", "yes", "positive", True),
    "approve.deny": ActDefinition("approve.deny", ("chat", "meeting"), "block", "yes", "neutral", True),
    "approve.defer": ActDefinition("approve.defer", ("chat", "meeting"), "tentative", "yes", "neutral", True),
    "escalate.to_sponsor": ActDefinition("escalate.to_sponsor", ("chat", "meeting"), "none", "yes", "negative", True),
    "escalate.to_manager": ActDefinition("escalate.to_manager", ("chat", "meeting"), "none", "yes", "negative", True),
    "ack.received": ActDefinition("ack.received", ("chat", "meeting"), "none", "weak", "positive", True),
    "ack.deferred": ActDefinition("ack.deferred", ("chat", "meeting"), "none", "weak", "neutral", True),
    "meeting.propose": ActDefinition("meeting.propose", ("calendar",), "none", "none", "neutral", False),
    "meeting.accept": ActDefinition("meeting.accept", ("calendar",), "none", "none", "neutral", False),
    "meeting.decline": ActDefinition("meeting.decline", ("calendar",), "none", "none", "neutral", False),
    "meeting.reschedule": ActDefinition("meeting.reschedule", ("calendar",), "none", "none", "neutral", False),
}


def require_known_act(act_id: str) -> ActDefinition:
    try:
        return ACT_DEFINITIONS[act_id]
    except KeyError as exc:
        known = ", ".join(sorted(ACT_DEFINITIONS))
        raise ValueError(f"Unknown act_id '{act_id}'. Known acts: {known}") from exc


def act_ids() -> Iterable[str]:
    return sorted(ACT_DEFINITIONS)
