from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from tpm_sim.common import format_dt, from_iso, to_iso
from tpm_sim.engine import SimulationEngine
from tpm_sim.evaluator import Evaluator
from tpm_sim.scenario import load_bundle_from_store, load_scenario_bundle, seed_store
from tpm_sim.storage import open_store


ACTION_SCHEMA_VERSION = "tpm_action_v1"

READ_ACTIONS = {"read.thread", "read.doc", "read.tasks", "read.calendar"}
WRITE_ACTIONS = {
    "chat.send",
    "docs.write",
    "notes.write",
    "task.note",
    "task.set_owner",
    "task.set_target",
    "meeting.propose",
    "meeting.act",
}
WAIT_ACTIONS = {"wait.duration", "wait.until_next_event"}
VALID_ACTIONS = READ_ACTIONS | WRITE_ACTIONS | WAIT_ACTIONS
ALLOWED_ACT_IDS = (
    "ack.deferred",
    "ack.received",
    "approve.defer",
    "approve.deny",
    "approve.grant",
    "commit.confirm",
    "commit.propose",
    "commit.retract",
    "commit.revise",
    "escalate.to_manager",
    "escalate.to_sponsor",
    "inform.availability",
    "inform.blocker",
    "inform.decision",
    "inform.risk",
    "inform.status_update",
    "meeting.accept",
    "meeting.decline",
    "meeting.propose",
    "meeting.reschedule",
    "negotiate.ownership",
    "negotiate.scope",
    "negotiate.timeline",
    "request.approval",
    "request.clarification",
    "request.eta",
    "request.feasibility",
    "request.ownership",
    "request.review",
    "request.scope_tradeoff",
)


ACTION_SCHEMA: dict[str, Any] = {
    "version": ACTION_SCHEMA_VERSION,
    "description": "Canonical TPM benchmark action contract. Exactly one action is allowed per turn.",
    "actions": {
        "read.thread": {"required": ["target"], "optional": []},
        "read.doc": {"required": ["doc_id"], "optional": []},
        "read.tasks": {"required": [], "optional": []},
        "read.calendar": {"required": [], "optional": []},
        "chat.send": {"required": ["target", "act_id"], "optional": ["slots", "body"]},
        "docs.write": {"required": ["doc_type", "title", "body"], "optional": []},
        "notes.write": {"required": ["title", "body"], "optional": []},
        "task.note": {"required": ["task_id", "note"], "optional": []},
        "task.set_owner": {"required": ["task_id", "owner_id"], "optional": []},
        "task.set_target": {"required": ["task_id", "target_at"], "optional": []},
        "meeting.propose": {
            "required": ["duration_minutes", "attendees", "title"],
            "optional": ["slots", "agenda"],
        },
        "meeting.act": {"required": ["meeting_id", "act_id"], "optional": ["slots", "body"]},
        "wait.duration": {"required": ["minutes"], "optional": []},
        "wait.until_next_event": {"required": ["max_minutes"], "optional": []},
    },
}


class ActionValidationError(ValueError):
    pass


@dataclass
class StructuredAction:
    action_type: str
    arguments: dict[str, Any]
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"action_type": self.action_type, "arguments": self.arguments, "reason": self.reason}


@dataclass
class StepResult:
    action: dict[str, Any]
    time_before: str
    time_after: str
    message: str
    coverage_miss: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EnvironmentSession:
    def __init__(self, db_path: str, engine: SimulationEngine, evaluator: Evaluator):
        self.db_path = db_path
        self.engine = engine
        self.evaluator = evaluator

    @classmethod
    def create(
        cls,
        db_path: str,
        scenario_id: str,
        seed: int,
        *,
        coverage_enforcement: str = "strict",
        force: bool = False,
    ) -> "EnvironmentSession":
        path = Path(db_path)
        if path.exists() and not force:
            raise RuntimeError(f"{db_path} already exists. Re-run with force=True to overwrite it.")
        if path.exists():
            path.unlink()
        store = open_store(db_path)
        bundle = load_scenario_bundle(scenario_id)
        try:
            seed_store(store, bundle, seed, coverage_enforcement=coverage_enforcement)
            engine = SimulationEngine(store, bundle)
            evaluator = Evaluator(engine)
            return cls(db_path, engine, evaluator)
        except Exception:
            store.close()
            raise

    @classmethod
    def open(cls, db_path: str) -> "EnvironmentSession":
        store = open_store(db_path)
        scenario_id = store.get_meta("scenario_id")
        if not scenario_id:
            store.close()
            raise RuntimeError(f"Database at {db_path} has not been initialized.")
        bundle = load_bundle_from_store(store)
        engine = SimulationEngine(store, bundle)
        evaluator = Evaluator(engine)
        return cls(db_path, engine, evaluator)

    def close(self) -> None:
        self.engine.store.close()

    def reset(self, scenario_id: str, seed: int, coverage_enforcement: str = "strict") -> None:
        bundle = load_scenario_bundle(scenario_id)
        seed_store(self.engine.store, bundle, seed, coverage_enforcement=coverage_enforcement)
        self.engine = SimulationEngine(self.engine.store, bundle)
        self.evaluator = Evaluator(self.engine)

    def observe(self) -> dict[str, Any]:
        return {
            "schema_version": ACTION_SCHEMA_VERSION,
            "scenario_id": self.engine.scenario["id"],
            "scenario_digest": self.engine.scenario_digest(),
            "time": to_iso(self.engine.now()),
            "observation": self.engine.observe(),
            "working_memory": self._working_memory(),
            "recent_history": self._recent_history(),
            "action_schema": ACTION_SCHEMA,
        }

    def score(self) -> dict[str, Any]:
        return self.evaluator.evaluate()

    def export_report(self, output_prefix: Optional[str] = None) -> dict[str, Any]:
        return self.evaluator.export_report(output_prefix)

    def checkpoint(self, label: str) -> str:
        return self.engine.checkpoint(label)

    def fork(self, checkpoint_ref: str, out_path: str, seed_override: Optional[int] = None) -> "EnvironmentSession":
        path = self.engine.fork(checkpoint_ref, out_path, seed_override=seed_override)
        return self.open(path)

    def render_status(self) -> str:
        return self.engine.render_status()

    def render_people(self) -> str:
        return self.engine.render_people()

    def render_inbox(self) -> str:
        return self.engine.render_inbox()

    def render_score_snapshot(self) -> str:
        return self.engine.render_score_snapshot(self.evaluator)

    def render_action_log(self) -> str:
        return self.engine.render_action_log()

    def coverage_report(self) -> dict[str, Any]:
        return self.engine.coverage_report()

    def step(self, action: StructuredAction | dict[str, Any]) -> StepResult:
        structured = coerce_action(action)
        before = to_iso(self.engine.now())
        message = self._dispatch(structured)
        after = to_iso(self.engine.now())
        return StepResult(
            action=structured.to_dict(),
            time_before=before,
            time_after=after,
            message=message,
            coverage_miss=bool(self.engine.project_state().get("coverage_miss")),
        )

    def _dispatch(self, action: StructuredAction) -> str:
        args = action.arguments
        kind = action.action_type
        if kind == "read.thread":
            return self.engine.open_thread(args["target"])
        if kind == "read.doc":
            return self.engine.open_doc(args["doc_id"])
        if kind == "read.tasks":
            return self.engine.render_tasks()
        if kind == "read.calendar":
            return self.engine.render_calendar()
        if kind == "chat.send":
            return self.engine.send_chat(
                args["target"],
                args["act_id"],
                dict(args.get("slots") or {}),
                str(args.get("body", "")),
            )
        if kind == "docs.write":
            return self.engine.write_doc(args["doc_type"], args["title"], args["body"])
        if kind == "notes.write":
            return self.engine.write_private_note(args["title"], args["body"])
        if kind == "task.note":
            return self.engine.add_task_note(args["task_id"], args["note"])
        if kind == "task.set_owner":
            return self.engine.update_task_owner(args["task_id"], args["owner_id"])
        if kind == "task.set_target":
            return self.engine.update_task_target_date(args["task_id"], args["target_at"])
        if kind == "meeting.propose":
            return self.engine.schedule_meeting(
                int(args["duration_minutes"]),
                list(args["attendees"]),
                args["title"],
                dict(args.get("slots") or {}),
                str(args.get("agenda", "")),
            )
        if kind == "meeting.act":
            return self.engine.meeting_act(
                args["meeting_id"],
                args["act_id"],
                dict(args.get("slots") or {}),
                str(args.get("body", "")),
            )
        if kind == "wait.duration":
            return self.engine.wait_minutes(int(args["minutes"]))
        if kind == "wait.until_next_event":
            return self.engine.wait_until_next_event(int(args["max_minutes"]))
        raise ActionValidationError(f"Unsupported action_type '{kind}'.")

    def _recent_history(self, action_limit: int = 6, event_limit: int = 10) -> dict[str, Any]:
        actions = self.engine.store.actions()[-action_limit:]
        events = self.engine.store.event_log("agent")[-event_limit:]
        return {
            "recent_actions": [
                {
                    "at": row["at"],
                    "surface": row["surface"],
                    "act_id": row["act_id"],
                    "slots": self.engine.deserialize(row["slots_json"], {}),
                }
                for row in actions
            ],
            "recent_agent_events": [
                {
                    "at": row["at"],
                    "event_type": row["event_type"],
                    "summary": row["summary"],
                    "payload": self.engine.deserialize(row["payload_json"], {}),
                }
                for row in events
            ],
        }

    def _working_memory(self) -> dict[str, Any]:
        facts = []
        for row in self.engine.store.facts():
            state = self.engine.deserialize(row["state_json"], {})
            if state.get("surfaced") and state.get("surfaced_by") == "tpm":
                facts.append(
                    {
                        "id": row["id"],
                        "label": row["label"],
                        "surfaced_at": state.get("surfaced_at"),
                    }
                )

        commitments = []
        for row in self.engine.store.commitments():
            if row["status"] in {"fulfilled", "broken", "superseded"}:
                continue
            commitments.append(
                {
                    "id": row["id"],
                    "owner_id": row["owner_id"],
                    "subject": row["subject"],
                    "status": row["status"],
                    "due_at": row["due_at"],
                    "confidence": row["confidence"],
                    "perceived_feasibility": row["perceived_feasibility"],
                }
            )

        blockers = []
        task_summaries = []
        for row in self.engine.store.tasks():
            tracker = self.engine.deserialize(row["tracker_state_json"], {})
            task_summaries.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "status": tracker.get("status", "unknown"),
                    "owner_id": tracker.get("owner_id", row["owner_id"]),
                    "due_at": row["due_at"],
                    "blocker": tracker.get("blocker"),
                }
            )
            if tracker.get("blocker"):
                blockers.append(
                    {
                        "task_id": row["id"],
                        "blocker": tracker["blocker"],
                        "status": tracker.get("status", "unknown"),
                    }
                )

        windows = []
        now = self.engine.now()
        for row in self.engine.store.windows():
            end_at = from_iso(row["end_at"])
            if end_at >= now:
                windows.append(
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "start_at": row["start_at"],
                        "end_at": row["end_at"],
                    }
                )

        meetings = []
        for row in self.engine.store.meetings():
            if row["status"] not in {"scheduled", "active"}:
                continue
            meetings.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "status": row["status"],
                    "start_at": row["start_at"],
                    "end_at": row["end_at"],
                }
            )

        milestones = []
        for row in self.engine.store.milestones():
            state = self.engine.deserialize(row["state_json"], {})
            milestones.append(
                {
                    "id": row["id"],
                    "status": state.get("status", "pending"),
                    "recoverability": state.get("recoverability", "unknown"),
                    "due_at": row["due_at"],
                }
            )

        return {
            "surfaced_facts": facts,
            "open_commitments": commitments,
            "unresolved_blockers": blockers,
            "visible_windows": windows,
            "pending_meetings": meetings,
            "milestones": milestones,
            "task_summaries": task_summaries,
        }


def coerce_action(action: StructuredAction | dict[str, Any]) -> StructuredAction:
    if isinstance(action, StructuredAction):
        validate_structured_action(action)
        return action
    if not isinstance(action, dict):
        raise ActionValidationError("Action must be a StructuredAction or dict.")
    structured = StructuredAction(
        action_type=str(action.get("action_type", "")),
        arguments=dict(action.get("arguments", {})),
        reason=str(action.get("reason", "")),
    )
    validate_structured_action(structured)
    return structured


def validate_structured_action(action: StructuredAction) -> None:
    if action.action_type not in VALID_ACTIONS:
        raise ActionValidationError(
            f"Unknown action_type '{action.action_type}'. Valid actions: {', '.join(sorted(VALID_ACTIONS))}"
        )
    spec = ACTION_SCHEMA["actions"][action.action_type]
    missing = [field for field in spec["required"] if field not in action.arguments or action.arguments[field] is None]
    if missing:
        raise ActionValidationError(f"{action.action_type} is missing required fields: {', '.join(missing)}")
    args = action.arguments
    if action.action_type == "meeting.propose" and not isinstance(args.get("attendees"), list):
        raise ActionValidationError("meeting.propose expects attendees to be a list of actor ids.")
    if action.action_type in {"chat.send", "meeting.act"}:
        act_id = args.get("act_id")
        if act_id not in ALLOWED_ACT_IDS:
            raise ActionValidationError(
                f"Unknown act_id '{act_id}'. Known acts: {', '.join(ALLOWED_ACT_IDS)}"
            )
    if action.action_type in {"wait.duration", "wait.until_next_event"}:
        field = "minutes" if action.action_type == "wait.duration" else "max_minutes"
        try:
            value = int(args[field])
        except Exception as exc:
            raise ActionValidationError(f"{action.action_type} expects integer field '{field}'.") from exc
        if value < 0:
            raise ActionValidationError(f"{action.action_type} expects non-negative '{field}'.")


def render_step_result(step: StepResult) -> str:
    lines = [
        f"Action: {step.action['action_type']}",
        f"Time: {format_dt(from_iso(step.time_before))} -> {format_dt(from_iso(step.time_after))}",
        step.message,
    ]
    if step.coverage_miss:
        lines.extend(["", "Coverage miss flagged in run state."])
    return "\n".join(lines)
