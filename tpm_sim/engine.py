from __future__ import annotations

import json
import re
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

from tpm_sim.common import (
    advance_by_minutes,
    as_json,
    bool_text,
    bucket_value,
    clamp,
    csv_ids,
    format_dt,
    from_iso,
    from_json,
    parse_slot_map,
    stable_digest,
    stable_int,
    summarize_lines,
    to_iso,
    weighted_choice,
)
from tpm_sim.predicate import PredicateEvaluator
from tpm_sim.specs import RENDERER_VERSION, require_known_act
from tpm_sim.storage import StateStore, copy_database


PHASE_PRIORITY = {
    "release": 0,
    "progress": 1,
    "interaction_start": 2,
    "informational": 3,
}


class CoverageMissError(RuntimeError):
    pass


@dataclass
class ContextMatch:
    family: dict[str, Any]
    specificity: int


class SimulationEngine:
    def __init__(self, store: StateStore, bundle: dict[str, Any]):
        self.store = store
        self.bundle = bundle
        self.scenario = bundle["scenario"]
        self.coverage = bundle["coverage"]
        self.world = self.scenario["world"]
        self.policy = self.scenario["policy"]
        self.evaluation = self.scenario["evaluation"]
        self.seed = int(self.store.get_meta("seed", "0"))
        self.coverage_enforcement = self.store.get_meta("coverage_enforcement", "strict")
        self.action_costs = self.policy.get("action_costs", {})
        self.predicate = PredicateEvaluator(self)
        self._compiled_families = self._compile_context_families()

    def now(self) -> datetime:
        return self.store.current_time()

    def deserialize(self, raw: Optional[str], default: Any = None) -> Any:
        return from_json(raw, default)

    def scenario_digest(self) -> str:
        return self.store.get_meta("scenario_digest", "")

    def project_row(self):
        return self.store.get_project_state()

    def project_state(self) -> dict[str, Any]:
        return self.deserialize(self.project_row()["state_json"], {})

    def project_metadata(self) -> dict[str, Any]:
        return self.deserialize(self.project_row()["metadata_json"], {})

    def actor_row(self, actor_id: str):
        return self.store.get_actor(actor_id)

    def actor_state(self, actor_id: str) -> dict[str, Any]:
        return self.deserialize(self.actor_row(actor_id)["state_json"], {})

    def actor_traits(self, actor_id: str) -> dict[str, Any]:
        return self.deserialize(self.actor_row(actor_id)["traits_json"], {})

    def relationship_state(self, actor_id: str, target_actor_id: str) -> dict[str, Any]:
        return self.deserialize(self.store.get_relationship(actor_id, target_actor_id)["state_json"], {})

    def task_row(self, task_id: str):
        return self.store.get_task(task_id)

    def task_true_state(self, task_id: str) -> dict[str, Any]:
        return self.deserialize(self.task_row(task_id)["true_state_json"], {})

    def task_tracker_state(self, task_id: str) -> dict[str, Any]:
        return self.deserialize(self.task_row(task_id)["tracker_state_json"], {})

    def task_metadata(self, task_id: str) -> dict[str, Any]:
        return self.deserialize(self.task_row(task_id)["metadata_json"], {})

    def milestone_row(self, milestone_id: str):
        return self.store.get_milestone(milestone_id)

    def milestone_state(self, milestone_id: str) -> dict[str, Any]:
        return self.deserialize(self.milestone_row(milestone_id)["state_json"], {})

    def milestone_metadata(self, milestone_id: str) -> dict[str, Any]:
        return self.deserialize(self.milestone_row(milestone_id)["metadata_json"], {})

    def fact_row(self, fact_id: str):
        return self.store.get_fact(fact_id)

    def fact_state(self, fact_id: str) -> dict[str, Any]:
        return self.deserialize(self.fact_row(fact_id)["state_json"], {})

    def fact_metadata(self, fact_id: str) -> dict[str, Any]:
        return self.deserialize(self.fact_row(fact_id)["metadata_json"], {})

    def commitment_row(self, commitment_id: str):
        return self.store.get_commitment(commitment_id)

    def commitment_state(self, commitment_id: str) -> dict[str, Any]:
        row = self.commitment_row(commitment_id)
        return {
            "id": row["id"],
            "owner_id": row["owner_id"],
            "audience_ids": self.deserialize(row["audience_ids_json"], []),
            "subject": row["subject"],
            "scope": self.deserialize(row["scope_json"], {}),
            "status": row["status"],
            "confidence": row["confidence"],
            "due_at": row["due_at"],
            "ground_truth_feasibility": row["ground_truth_feasibility"],
            "perceived_feasibility": row["perceived_feasibility"],
            "preconditions": self.deserialize(row["preconditions_json"], []),
            "source_ref": row["source_ref"],
            "last_updated_at": row["last_updated_at"],
            "metadata": self.deserialize(row["metadata_json"], {}),
            "last_transition_ref": self.deserialize(row["metadata_json"], {}).get("last_transition_ref"),
        }

    def latest_belief(self, actor_id: str, belief_key: str):
        return self.store.latest_belief(actor_id, belief_key)

    def meeting_state(self, meeting_id: str):
        return self.store.get_meeting(meeting_id)

    def meeting_metadata(self, meeting_id: str) -> dict[str, Any]:
        return self.deserialize(self.meeting_state(meeting_id)["metadata_json"], {})

    def window(self, window_id: str):
        return self.store.get_window(window_id)

    def actor_name(self, actor_id: str) -> str:
        return self.actor_row(actor_id)["name"]

    def observe(self) -> dict[str, Any]:
        unread = self.store.messages(unread_only=True, limit=50)
        active_meetings = [row for row in self.store.meetings() if row["status"] == "active"]
        return {
            "time": to_iso(self.now()),
            "project_state": self.project_state(),
            "unread_threads": self._unread_thread_summary(unread),
            "upcoming_meetings": [self._meeting_summary(row) for row in self.store.meetings() if row["status"] == "scheduled"][:5],
            "active_meetings": [self._meeting_summary(row) for row in active_meetings],
            "tasks": [self._task_tracker_summary(row) for row in self.store.tasks()],
            "documents": [self._doc_summary(row) for row in self.store.documents()],
        }

    def render_status(self) -> str:
        obs = self.observe()
        project = self.project_row()
        lines = [
            f"Time: {format_dt(self.now())}",
            f"Project: {project['name']}",
            f"Customer confidence: {obs['project_state'].get('customer_confidence', 'fragile')}",
            f"Launch scope: {obs['project_state'].get('launch_scope', 'undecided')}",
            f"Coverage miss seen: {bool_text(bool(obs['project_state'].get('coverage_miss')))}",
            "",
            "Milestones:",
        ]
        for row in self.store.milestones():
            state = self.deserialize(row["state_json"], {})
            lines.append(
                f"- {row['id']}: {state.get('status', 'pending')} | recoverability={state.get('recoverability', 'unknown')} | due {format_dt(from_iso(row['due_at']))}"
            )
        lines.append("")
        lines.append("Unread threads:")
        unread_lines = self._unread_thread_summary(self.store.messages(unread_only=True, limit=50))
        if unread_lines:
            lines.extend(f"- {entry['display']}" for entry in unread_lines)
        else:
            lines.append("- none")
        if obs["active_meetings"]:
            lines.extend(["", "Active meetings:"])
            for meeting in obs["active_meetings"]:
                lines.append(f"- {meeting}")
        return "\n".join(lines)

    def render_people(self) -> str:
        lines = ["Actors:"]
        for row in self.store.actors():
            if row["id"] == "tpm":
                continue
            state = self.deserialize(row["state_json"], {})
            lines.append(
                f"- {row['id']}: {row['name']} ({row['org_role']} / {row['coordination_template']}) | pressure={bucket_value(state.get('priority_pressure', 0.5))} | trust={state.get('trust_in_tpm', 0.5):.2f}"
            )
        return "\n".join(lines)

    def render_inbox(self) -> str:
        unread = self.store.messages(unread_only=True, limit=50)
        if not unread:
            return "Inbox is clear."
        lines = ["Unread messages:"]
        seen_threads: set[str] = set()
        for message in unread:
            if message["thread_id"] in seen_threads:
                continue
            seen_threads.add(message["thread_id"])
            thread = self.store.get_thread(message["thread_id"])
            lines.append(
                f"- {thread['id']} [{thread['surface']}] from {self.actor_name(message['sender_id'])} at {format_dt(from_iso(message['created_at']))}"
            )
        return "\n".join(lines)

    def render_threads(self, surface: str = "chat") -> str:
        lines = [f"{surface.title()} threads:"]
        for thread in self.store.threads(surface=surface):
            participants = ", ".join(self.actor_name(actor_id) for actor_id in self.deserialize(thread["participant_ids_json"], []))
            lines.append(f"- {thread['id']} [{thread['kind']}] {thread['title']} | {participants}")
        return "\n".join(lines)

    def open_thread(self, thread_or_actor_id: str) -> str:
        thread = self._resolve_thread("chat", thread_or_actor_id)
        messages = self.store.thread_messages(thread["id"])
        self.store.mark_thread_read(thread["id"])
        new_beliefs: list[str] = []
        new_facts: list[str] = []
        for message in messages:
            if message["sender_id"] != "tpm" and message["unread_for_tpm"]:
                beliefs, facts = self._apply_observation_signals(
                    observer_id="tpm",
                    source_ref=f"message:{message['id']}",
                    metadata=self.deserialize(message["metadata_json"], {}),
                )
                new_beliefs.extend(beliefs)
                new_facts.extend(facts)
        duration = self.action_costs.get("read_thread", 2)
        action_id = self.store.log_action(
            to_iso(self.now()),
            "tpm",
            "chat",
            "read.thread",
            {"thread_id": thread["id"]},
            "",
            duration,
            {"thread_id": thread["id"]},
        )
        self._advance_internal(duration, reason="read.thread", log_action=False)
        self.store.log_event(
            to_iso(self.now()),
            phase="informational",
            event_type="thread.opened",
            actor_id="tpm",
            visibility="agent",
            summary=f"Opened thread {thread['id']}",
            payload={"thread_id": thread["id"], "action_ref": f"action:{action_id}"},
        )
        lines = [f"Thread {thread['id']}:"]
        for message in messages:
            sender = self.actor_name(message["sender_id"])
            act = f" [{message['act_id']}]" if message["act_id"] else ""
            lines.append(f"[{format_dt(from_iso(message['created_at']))}] {sender}{act}")
            if message["body"]:
                lines.append(message["body"])
            slots = self.deserialize(message["slots_json"], {})
            if slots:
                lines.append(f"slots: {json.dumps(slots, sort_keys=True)}")
            lines.append("")
        if new_beliefs:
            lines.append("Beliefs updated:")
            lines.extend(f"- {entry}" for entry in new_beliefs)
        if new_facts:
            lines.append("Facts surfaced:")
            lines.extend(f"- {entry}" for entry in new_facts)
        return "\n".join(lines).strip()

    def render_docs(self) -> str:
        lines = ["Documents:"]
        for row in self.store.documents():
            lines.append(f"- {row['id']} [{row['type']}] {row['title']} (updated {format_dt(from_iso(row['updated_at']))})")
        return "\n".join(lines)

    def open_doc(self, doc_id: str) -> str:
        row = self.store.get_document(doc_id)
        metadata = self.deserialize(row["metadata_json"], {})
        beliefs, facts = self._apply_observation_signals("tpm", f"doc:{doc_id}", metadata)
        duration = int(metadata.get("read_cost_minutes", self.action_costs.get("read_doc", 5)))
        action_id = self.store.log_action(
            to_iso(self.now()),
            "tpm",
            "docs",
            "read.doc",
            {"doc_id": doc_id},
            "",
            duration,
            {"doc_id": doc_id},
        )
        self._advance_internal(duration, reason="read.doc", log_action=False)
        self.store.log_event(
            to_iso(self.now()),
            phase="informational",
            event_type="doc.opened",
            actor_id="tpm",
            visibility="agent",
            summary=f"Opened document {doc_id}",
            payload={"doc_id": doc_id, "action_ref": f"action:{action_id}"},
        )
        lines = [
            f"{row['id']} [{row['type']}] {row['title']}",
            f"Updated: {format_dt(from_iso(row['updated_at']))}",
            "",
            row["content"],
        ]
        if beliefs:
            lines.extend(["", "Beliefs updated:"])
            lines.extend(f"- {entry}" for entry in beliefs)
        if facts:
            lines.extend(["", "Facts surfaced:"])
            lines.extend(f"- {entry}" for entry in facts)
        return "\n".join(lines)

    def render_tasks(self) -> str:
        lines = ["Task tracker:"]
        for row in self.store.tasks():
            tracker = self.deserialize(row["tracker_state_json"], {})
            lines.append(
                f"- {row['id']} [{tracker.get('status', 'unknown')}] {row['title']} | owner={self.actor_name(tracker.get('owner_id', row['owner_id']))} | due={format_dt(from_iso(row['due_at']))}"
            )
            if tracker.get("blocker"):
                lines.append(f"  blocker: {tracker['blocker']}")
            if tracker.get("notes"):
                lines.append(f"  notes: {tracker['notes']}")
        duration = self.action_costs.get("read_task_board", 1)
        self.store.log_action(to_iso(self.now()), "tpm", "tasks", "read.tasks", {}, "", duration, {})
        self._advance_internal(duration, reason="read.tasks", log_action=False)
        self._sync_tracker_beliefs_for_actor("tpm")
        return "\n".join(lines)

    def render_calendar(self) -> str:
        lines = ["Calendar:"]
        for row in self.store.meetings():
            attendees = ", ".join(self.actor_name(actor_id) for actor_id in self.deserialize(row["attendee_ids_json"], []))
            lines.append(
                f"- {row['id']} [{row['status']}] {format_dt(from_iso(row['start_at']))} - {format_dt(from_iso(row['end_at']))} | {row['title']} | {attendees}"
            )
        duration = self.action_costs.get("read_calendar", 1)
        self.store.log_action(to_iso(self.now()), "tpm", "calendar", "read.calendar", {}, "", duration, {})
        self._advance_internal(duration, reason="read.calendar", log_action=False)
        return "\n".join(lines)

    def render_score_snapshot(self, evaluator) -> str:
        report = evaluator.evaluate()
        lines = [f"Total score: {report['total_score']} / 100"]
        for item in report["rubric"]:
            lines.append(f"- {item['id']}: {item['awarded']} / {item['weight']}")
        lines.append("")
        lines.append("Failure breakdown:")
        for name, value in report["failure_breakdown"].items():
            lines.append(f"- {name}: {value}")
        return "\n".join(lines)

    def render_action_log(self) -> str:
        lines = ["Action log:"]
        for row in self.store.actions():
            slots = self.deserialize(row["slots_json"], {})
            suffix = f" {json.dumps(slots, sort_keys=True)}" if slots else ""
            lines.append(f"- {format_dt(from_iso(row['at']))} {row['act_id']}{suffix}")
        return "\n".join(lines)

    def send_chat(self, target: str, act_id: str, slots: dict[str, Any], body: str) -> str:
        thread = self._resolve_thread("chat", target)
        act = require_known_act(act_id)
        if "chat" not in act.valid_surfaces:
            raise ValueError(f"{act_id} is not valid on chat.")
        action_time = to_iso(self.now())
        target_actor_id = self._thread_primary_target(thread, explicit_target=target)
        logged_slots = {**slots}
        if target_actor_id and "target_actor_id" not in logged_slots:
            logged_slots["target_actor_id"] = target_actor_id
        self.store.add_message(
            {
                "thread_id": thread["id"],
                "surface": "chat",
                "sender_id": "tpm",
                "act_id": act_id,
                "slots": logged_slots,
                "body": body,
                "created_at": action_time,
                "unread_for_tpm": False,
                "metadata": {"sender": "tpm"},
            }
        )
        action_id = self.store.log_action(
            action_time,
            "tpm",
            "chat",
            act_id,
            logged_slots,
            body,
            self.action_costs.get("send_chat", 1),
            {"thread_id": thread["id"], "target_actor_id": target_actor_id},
        )
        self.store.log_event(
            action_time,
            phase="interaction_start",
            event_type="tpm.message_sent",
            actor_id="tpm",
            visibility="agent",
            summary=f"Sent {act_id} to {thread['id']}",
            payload={"thread_id": thread["id"], "act_id": act_id, "slots": logged_slots, "action_ref": f"action:{action_id}"},
        )
        self._apply_outgoing_message_side_effects(thread, act_id, logged_slots, body)
        self._schedule_npc_response(thread, act_id, logged_slots, body)
        notifications = self._spend_time(self.action_costs.get("send_chat", 1), "send_chat")
        lines = [f"Sent {act_id} in {thread['id']}."]
        if notifications:
            lines.extend(["", *notifications])
        return "\n".join(lines)

    def write_doc(self, doc_type: str, title: str, body: str) -> str:
        doc_id = self._next_document_id(doc_type)
        now_iso = to_iso(self.now())
        metadata: dict[str, Any] = {"created_by_tpm": True}
        if doc_type == "runbook":
            metadata["signals"] = [
                {
                    "belief_key": "artifact.runbook_exists",
                    "belief_value": True,
                    "confidence": 0.8,
                    "freshness_window_min": 1440,
                }
            ]
        self.store.add_document(
            {
                "id": doc_id,
                "type": doc_type,
                "title": title,
                "author_id": "tpm",
                "created_at": now_iso,
                "updated_at": now_iso,
                "visibility": "company",
                "content": body,
                "metadata": metadata,
            }
        )
        action_id = self.store.log_action(
            now_iso,
            "tpm",
            "docs",
            "docs.write",
            {"doc_id": doc_id, "doc_type": doc_type},
            body,
            self.action_costs.get("write_doc", 15),
            {"title": title},
        )
        self.store.log_event(
            now_iso,
            phase="interaction_start",
            event_type="doc.created",
            actor_id="tpm",
            visibility="both",
            summary=f"Created document {doc_id}",
            payload={"doc_id": doc_id, "action_ref": f"action:{action_id}"},
        )
        if doc_type == "runbook" and self._task_exists("runbook_readiness"):
            self._schedule_direct_task_completion("runbook_readiness", 45, 90, note="Runbook drafted by TPM.")
        notifications = self._spend_time(self.action_costs.get("write_doc", 15), "write_doc")
        lines = [f"Created {doc_id}."]
        if notifications:
            lines.extend(["", *notifications])
        return "\n".join(lines)

    def add_task_note(self, task_id: str, note: str) -> str:
        row = self.task_row(task_id)
        tracker = self.deserialize(row["tracker_state_json"], {})
        existing = tracker.get("notes", "")
        tracker["notes"] = note if not existing else f"{existing}\n{format_dt(self.now())}: {note}"
        tracker["last_updated_at"] = to_iso(self.now())
        self.store.update_task(task_id, tracker_state=tracker)
        action_id = self.store.log_action(
            to_iso(self.now()),
            "tpm",
            "tasks",
            "task.note",
            {"task_id": task_id},
            note,
            self.action_costs.get("task_note", 3),
            {},
        )
        event_id = self.store.log_event(
            to_iso(self.now()),
            phase="interaction_start",
            event_type="task.tracker_updated",
            actor_id="tpm",
            visibility="both",
            summary=f"Updated tracker note for {task_id}",
            payload={"task_id": task_id, "action_ref": f"action:{action_id}"},
        )
        tracker["last_update_ref"] = f"event:{event_id}"
        self.store.update_task(task_id, tracker_state=tracker)
        self._spend_time(self.action_costs.get("task_note", 3), "task.note")
        return f"Updated tracker note for {task_id}."

    def update_task_owner(self, task_id: str, owner_id: str) -> str:
        row = self.task_row(task_id)
        tracker = self.deserialize(row["tracker_state_json"], {})
        tracker["owner_id"] = owner_id
        tracker["last_updated_at"] = to_iso(self.now())
        self.store.update_task(task_id, tracker_state=tracker)
        self.store.log_action(
            to_iso(self.now()),
            "tpm",
            "tasks",
            "task.set_owner",
            {"task_id": task_id, "owner_id": owner_id},
            "",
            self.action_costs.get("task_edit", 3),
            {},
        )
        self._spend_time(self.action_costs.get("task_edit", 3), "task.set_owner")
        return f"Set visible owner for {task_id} to {owner_id}."

    def update_task_target_date(self, task_id: str, target_at: str) -> str:
        row = self.task_row(task_id)
        tracker = self.deserialize(row["tracker_state_json"], {})
        tracker["target_at"] = target_at
        tracker["last_updated_at"] = to_iso(self.now())
        self.store.update_task(task_id, tracker_state=tracker)
        self.store.log_action(
            to_iso(self.now()),
            "tpm",
            "tasks",
            "task.set_target",
            {"task_id": task_id, "target_at": target_at},
            "",
            self.action_costs.get("task_edit", 3),
            {},
        )
        self._spend_time(self.action_costs.get("task_edit", 3), "task.set_target")
        return f"Set visible target date for {task_id}."

    def write_private_note(self, title: str, body: str) -> str:
        note_id = self._next_document_id("note")
        now_iso = to_iso(self.now())
        self.store.add_document(
            {
                "id": note_id,
                "type": "private_note",
                "title": title,
                "author_id": "tpm",
                "created_at": now_iso,
                "updated_at": now_iso,
                "visibility": "private",
                "content": body,
                "metadata": {},
            }
        )
        self.store.log_action(
            now_iso,
            "tpm",
            "notes",
            "note.write",
            {"doc_id": note_id},
            body,
            self.action_costs.get("write_note", 1),
            {},
        )
        self._spend_time(self.action_costs.get("write_note", 1), "note.write")
        return f"Wrote private note {note_id}."

    def schedule_meeting(self, duration_minutes: int, attendees: Iterable[str], title: str, slots: dict[str, Any], agenda: str) -> str:
        meeting_id = slots.get("meeting_id") or self._next_meeting_id()
        goal = slots.get("goal", "alignment")
        people = [person for person in dict.fromkeys(attendees) if person and person != "tpm"]
        if not people:
            raise ValueError("Meeting requires at least one attendee besides the TPM.")
        start_at = self._find_meeting_slot(duration_minutes, people)
        end_at = start_at + timedelta(minutes=duration_minutes)
        self.store.add_meeting(
            {
                "id": meeting_id,
                "title": title,
                "organizer_id": "tpm",
                "start_at": to_iso(start_at),
                "end_at": to_iso(end_at),
                "status": "scheduled",
                "attendee_ids": ["tpm", *people],
                "agenda": agenda,
                "transcript_doc_id": None,
                "metadata": {
                    "goal": goal,
                    "responses": {"tpm": "accepted"},
                    "tpm_preparation": slots,
                    "tpm_acts": [],
                    "productive_outcome_ids": [],
                },
            }
        )
        action_id = self.store.log_action(
            to_iso(self.now()),
            "tpm",
            "calendar",
            "meeting.propose",
            {"meeting_id": meeting_id, "goal": goal, "attendees": people, "duration_minutes": duration_minutes},
            agenda,
            self.action_costs.get("schedule_meeting", 2),
            {},
        )
        self.store.log_event(
            to_iso(self.now()),
            phase="interaction_start",
            event_type="meeting.scheduled",
            actor_id="tpm",
            visibility="both",
            summary=f"Scheduled meeting {meeting_id}",
            payload={"meeting_id": meeting_id, "action_ref": f"action:{action_id}"},
        )
        for attendee in people:
            due = self._response_due_time(attendee, "calendar", "meeting.propose", {"meeting_id": meeting_id})
            self.store.queue_event(to_iso(due), PHASE_PRIORITY["interaction_start"], "npc.respond_invite", attendee, {"meeting_id": meeting_id, "incoming_act_id": "meeting.propose"})
        self.store.queue_event(to_iso(start_at), PHASE_PRIORITY["interaction_start"], "meeting.start", "system", {"meeting_id": meeting_id})
        self.store.queue_event(to_iso(end_at), PHASE_PRIORITY["release"], "meeting.end", "system", {"meeting_id": meeting_id})
        notifications = self._spend_time(self.action_costs.get("schedule_meeting", 2), "meeting.propose")
        lines = [f"Scheduled meeting {meeting_id} for {format_dt(start_at)}."]
        if notifications:
            lines.extend(["", *notifications])
        return "\n".join(lines)

    def meeting_act(self, meeting_id: str, act_id: str, slots: dict[str, Any], body: str) -> str:
        act = require_known_act(act_id)
        if "meeting" not in act.valid_surfaces:
            raise ValueError(f"{act_id} is not valid inside meetings.")
        row = self.store.get_meeting(meeting_id)
        if row["status"] != "active":
            raise ValueError(f"Meeting {meeting_id} is not active.")
        metadata = self.deserialize(row["metadata_json"], {})
        tpm_acts = metadata.get("tpm_acts", [])
        max_acts = int(self.policy.get("meeting_defaults", {}).get("tpm_max_acts", 2))
        if len(tpm_acts) >= max_acts:
            raise ValueError(f"Meeting {meeting_id} already has the maximum of {max_acts} TPM acts.")
        tpm_acts.append({"act_id": act_id, "slots": slots, "body": body, "at": to_iso(self.now())})
        metadata["tpm_acts"] = tpm_acts
        self.store.update_meeting(meeting_id, metadata_json=as_json(metadata))
        action_id = self.store.log_action(
            to_iso(self.now()),
            "tpm",
            "meeting",
            act_id,
            {**slots, "meeting_id": meeting_id},
            body,
            self.action_costs.get("meeting_act", 1),
            {"meeting_id": meeting_id},
        )
        self.store.log_event(
            to_iso(self.now()),
            phase="interaction_start",
            event_type="meeting.tpm_act",
            actor_id="tpm",
            visibility="both",
            summary=f"TPM used {act_id} in {meeting_id}",
            payload={"meeting_id": meeting_id, "act_id": act_id, "slots": slots, "action_ref": f"action:{action_id}"},
        )
        self._spend_time(self.action_costs.get("meeting_act", 1), "meeting.act")
        return f"Recorded {act_id} in meeting {meeting_id}."

    def wait_minutes(self, minutes: int) -> str:
        notes = self._advance_internal(minutes, reason="wait.duration", log_wait_action=True)
        lines = [f"Time is now {format_dt(self.now())}."]
        if notes:
            lines.extend(["", *notes])
        return "\n".join(lines)

    def wait_until_next_event(self, max_minutes: int) -> str:
        pending = self.store.pending_events()
        if not pending:
            return self.wait_minutes(max_minutes)
        next_due = from_iso(pending[0]["due_at"])
        delta_minutes = int(max(0, (next_due - self.now()).total_seconds() // 60))
        minutes = min(delta_minutes, max_minutes)
        return self.wait_minutes(minutes)

    def checkpoint(self, label: str) -> str:
        checkpoint_dir = Path(f"{self.store.path}.checkpoints")
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        target = checkpoint_dir / f"{label}.sqlite"
        self.store.backup_to(str(target))
        return str(target)

    def fork(self, checkpoint_label: str, out_path: str, seed_override: Optional[int] = None) -> str:
        source = Path(f"{self.store.path}.checkpoints") / f"{checkpoint_label}.sqlite"
        if not source.exists():
            raise FileNotFoundError(f"Unknown checkpoint '{checkpoint_label}'.")
        copy_database(str(source), out_path)
        if seed_override is not None:
            fork_store = StateStore(out_path)
            try:
                with fork_store.transaction():
                    fork_store.set_meta("seed", str(seed_override))
            finally:
                fork_store.close()
        return out_path

    def coverage_report(self) -> dict[str, Any]:
        cells = self.coverage.get("reachable_cells", [])
        uncovered: list[dict[str, Any]] = []
        covered = 0
        critical_uncovered = 0
        for cell in cells:
            if not cell.get("reachable", True):
                continue
            selector = cell.get("selector", {})
            guard = cell.get("guard")
            if any(
                self._selector_covers_cell(family.get("selector", {}), selector)
                and self._guard_matches_cell(family.get("guard"), guard)
                for family in self._compiled_families
            ):
                covered += 1
            else:
                uncovered.append(cell)
                if cell.get("criticality") == "critical":
                    critical_uncovered += 1
        total = sum(1 for cell in cells if cell.get("reachable", True))
        return {
            "total_reachable_cells": total,
            "covered_reachable_cells": covered,
            "coverage": 0.0 if total == 0 else covered / total,
            "critical_uncovered": critical_uncovered,
            "uncovered": uncovered,
        }

    def _resolve_thread(self, surface: str, thread_or_actor_id: str):
        candidate = str(thread_or_actor_id).strip()
        if not candidate:
            raise KeyError("Thread target cannot be empty.")
        normalized = candidate.split(" (", 1)[0].strip()
        fallback_prefix = normalized.split(".", 1)[0].strip() if "." in normalized else ""
        email_local = normalized.split("@", 1)[0].strip() if "@" in normalized else ""
        stripped_suffix = normalized.removesuffix("_thread").removesuffix(".thread").strip()
        token_prefix_match = re.match(r"[A-Za-z0-9]+", normalized)
        token_prefix = token_prefix_match.group(0).strip() if token_prefix_match else ""
        keys = []
        for key in [candidate, normalized, fallback_prefix, email_local, stripped_suffix, token_prefix]:
            if key and key not in keys:
                keys.append(key)
        for key in keys:
            if not key:
                continue
            try:
                row = self.store.get_thread(key)
            except KeyError:
                continue
            if row["surface"] == surface:
                return row
        lowered = normalized.lower()
        for row in self.store.threads(surface=surface):
            if row["id"].lower() == lowered or row["title"].lower() == lowered:
                return row
        actor_alias_matches = []
        for row in self.store.threads(surface=surface):
            if row["kind"] != "dm":
                continue
            participants = self.deserialize(row["participant_ids_json"], [])
            recipients = [actor_id for actor_id in participants if actor_id != "tpm"]
            if len(recipients) != 1:
                continue
            actor = self.actor_row(recipients[0])
            aliases: set[str] = set()
            for raw_value in [
                actor["id"],
                actor["name"],
                actor["org_role"],
                actor["coordination_template"],
            ]:
                text = str(raw_value).strip().lower()
                if not text:
                    continue
                aliases.add(text)
                aliases.update(part for part in re.split(r"[^a-z0-9]+", text) if part)
            if actor["coordination_template"] == "sponsor":
                aliases.update({"manager", "sponsor", "director", "leadership"})
            if actor["org_role"] == "security_engineer":
                aliases.update({"security", "approver"})
            if actor["org_role"] == "engineer":
                aliases.update({"engineer", "owner"})
            if actor["org_role"] == "operations":
                aliases.update({"ops", "operations"})
            if lowered in aliases:
                actor_alias_matches.append(row)
        if len(actor_alias_matches) == 1:
            return actor_alias_matches[0]
        raise KeyError(f"Unknown {surface} thread '{thread_or_actor_id}'.")

    def _thread_primary_target(self, thread, *, explicit_target: Optional[str] = None) -> Optional[str]:
        participants = self.deserialize(thread["participant_ids_json"], [])
        recipients = [actor_id for actor_id in participants if actor_id != "tpm"]
        if thread["kind"] == "dm" and len(recipients) == 1:
            return recipients[0]
        return explicit_target

    def _would_create_invalid_external_commitment(self, actor_id: str, act_id: str, slots: dict[str, Any]) -> bool:
        actor = self.actor_row(actor_id)
        if actor["coordination_template"] != "customer_facing_bridge":
            return False
        if act_id not in {"inform.decision", "commit.propose", "commit.confirm", "commit.revise"}:
            return False
        required_milestones = self.policy.get("external_commitment_requirements", ["scope_aligned", "security_slot_secured"])
        for milestone_id in required_milestones:
            if self.milestone_state(milestone_id).get("status") != "done":
                return True
        commitment_id = slots.get("commitment_id")
        if commitment_id and self._commitment_exists(commitment_id):
            return self.commitment_state(commitment_id)["ground_truth_feasibility"] < 0.5
        return False

    def _response_due_time(self, actor_id: str, surface: str, act_id: str, slots: dict[str, Any]) -> datetime:
        traits = self.actor_traits(actor_id)
        min_key = f"{surface}_reply_delay_min"
        max_key = f"{surface}_reply_delay_max"
        lower = int(traits.get(min_key, 10))
        upper = int(traits.get(max_key, max(lower, 30)))
        delay = stable_int(self.seed, lower, upper, actor_id, surface, act_id, as_json(slots), to_iso(self.now()))
        desired = self.now() + timedelta(minutes=delay)
        return self._next_actor_available_time(actor_id, desired)

    def _next_actor_available_time(self, actor_id: str, desired: datetime) -> datetime:
        traits = self.actor_traits(actor_id)
        state = self.actor_state(actor_id)
        current = desired
        available_after_raw = state.get("available_after")
        if available_after_raw:
            available_after = from_iso(available_after_raw)
            if current < available_after:
                current = available_after
        start_hour, start_minute = self._parse_clock(traits.get("workday_start", "09:00"))
        end_hour, end_minute = self._parse_clock(traits.get("workday_end", "17:00"))
        while True:
            workday_start = current.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
            workday_end = current.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
            if current.weekday() >= 5:
                current = (current + timedelta(days=(7 - current.weekday()))).replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
                continue
            if current < workday_start:
                current = workday_start
            if current > workday_end:
                current = (current + timedelta(days=1)).replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
                continue
            return current

    def _parse_clock(self, raw: str) -> tuple[int, int]:
        hour, minute = raw.split(":")
        return int(hour), int(minute)

    def _find_meeting_slot(self, duration_minutes: int, attendees: list[str]) -> datetime:
        current = self.now().replace(second=0, microsecond=0) + timedelta(minutes=15)
        remainder = current.minute % 15
        if remainder:
            current += timedelta(minutes=(15 - remainder))
        current = current.replace(second=0, microsecond=0)
        horizon = from_iso(self.store.get_meta("simulation_end"))
        while current < horizon:
            end_at = current + timedelta(minutes=duration_minutes)
            if self._actor_available_for_window("tpm", current, end_at):
                if all(self._actor_available_for_window(actor_id, current, end_at) for actor_id in attendees):
                    return current
            current += timedelta(minutes=15)
        raise RuntimeError("Could not find a meeting slot inside the simulation horizon.")

    def _actor_available_for_window(self, actor_id: str, start_at: datetime, end_at: datetime) -> bool:
        traits = self.actor_traits(actor_id)
        state = self.actor_state(actor_id)
        workday_start = start_at.replace(hour=self._parse_clock(traits.get("workday_start", "09:00"))[0], minute=self._parse_clock(traits.get("workday_start", "09:00"))[1], second=0, microsecond=0)
        workday_end = start_at.replace(hour=self._parse_clock(traits.get("workday_end", "17:00"))[0], minute=self._parse_clock(traits.get("workday_end", "17:00"))[1], second=0, microsecond=0)
        if start_at.weekday() >= 5 or start_at < workday_start or end_at > workday_end:
            return False
        available_after_raw = state.get("available_after")
        if available_after_raw and start_at < from_iso(available_after_raw):
            return False
        for meeting in self.store.meetings():
            if actor_id not in self.deserialize(meeting["attendee_ids_json"], []):
                continue
            if meeting["status"] not in {"scheduled", "active"}:
                continue
            meeting_start = from_iso(meeting["start_at"])
            meeting_end = from_iso(meeting["end_at"])
            if start_at < meeting_end and end_at > meeting_start:
                return False
        return True

    def _apply_observation_signals(self, observer_id: str, source_ref: str, metadata: dict[str, Any]) -> tuple[list[str], list[str]]:
        beliefs: list[str] = []
        facts: list[str] = []
        for signal in [*metadata.get("belief_signals", []), *metadata.get("signals", [])]:
            belief_id = self.store.add_belief(
                {
                    "actor_id": observer_id,
                    "belief_key": signal["belief_key"],
                    "belief_value": signal.get("belief_value"),
                    "confidence": signal.get("confidence", 0.7),
                    "freshness_window_min": signal.get("freshness_window_min", 240),
                    "updated_at": to_iso(self.now()),
                    "source_ref": source_ref,
                    "metadata": signal.get("metadata", {}),
                }
            )
            beliefs.append(f"{signal['belief_key']} (confidence {signal.get('confidence', 0.7):.2f})")
            self.store.log_event(
                to_iso(self.now()),
                phase="informational",
                event_type="belief.updated",
                actor_id=observer_id,
                visibility="omniscient",
                summary=f"Belief updated for {observer_id}: {signal['belief_key']}",
                payload={"belief_ref": f"belief:{belief_id}", "belief_key": signal["belief_key"], "source_ref": source_ref},
            )
        for fact_id in metadata.get("surface_facts", []):
            event_id = self.store.log_event(
                to_iso(self.now()),
                phase="informational",
                event_type="fact_signal",
                actor_id=observer_id,
                visibility="omniscient",
                summary=f"Fact signal for {fact_id}",
                payload={"fact_id": fact_id, "observer_id": observer_id, "source_ref": source_ref},
            )
            surfaced = self._try_surface_fact(fact_id, observer_id, f"event:{event_id}")
            if surfaced:
                facts.append(self.fact_row(fact_id)["label"])
        self._refresh_derived_state()
        return beliefs, facts

    def _sync_tracker_beliefs_for_actor(self, actor_id: str) -> None:
        for row in self.store.tasks():
            tracker = self.deserialize(row["tracker_state_json"], {})
            self.store.add_belief(
                {
                    "actor_id": actor_id,
                    "belief_key": f"task.{row['id']}.tracker_status",
                    "belief_value": tracker.get("status"),
                    "confidence": 0.75,
                    "freshness_window_min": 240,
                    "updated_at": to_iso(self.now()),
                    "source_ref": tracker.get("last_update_ref", "tracker:board"),
                    "metadata": {"task_id": row["id"]},
                }
            )

    def _apply_outgoing_message_side_effects(self, thread, act_id: str, slots: dict[str, Any], body: str) -> None:
        participants = self.deserialize(thread["participant_ids_json"], [])
        recipients = [actor_id for actor_id in participants if actor_id != "tpm"]
        for actor_id in recipients:
            self._apply_recipient_belief_updates(actor_id, act_id, slots)
            self._apply_relationship_effect_for_outgoing(actor_id, act_id)
            if act_id.startswith("commit."):
                self._apply_commitment_act_from_tpm(actor_id, act_id, slots)
            if self._would_create_invalid_external_commitment(actor_id, act_id, slots):
                project_state = self.project_state()
                current = int(project_state.get("invalid_external_commitments", 0))
                self.store.update_project_state({"invalid_external_commitments": current + 1})

    def _apply_recipient_belief_updates(self, actor_id: str, act_id: str, slots: dict[str, Any]) -> None:
        if act_id == "inform.decision" and "decision_key" in slots:
            self.store.add_belief(
                {
                    "actor_id": actor_id,
                    "belief_key": f"project.{slots['decision_key']}",
                    "belief_value": slots.get("decision_value"),
                    "confidence": 0.8,
                    "freshness_window_min": 240,
                    "updated_at": to_iso(self.now()),
                    "source_ref": f"action:{self.store.actions()[-1]['id']}" if self.store.actions() else "action",
                    "metadata": {},
                }
            )
        if act_id.startswith("inform.") and "task_id" in slots and "status" in slots:
            self.store.add_belief(
                {
                    "actor_id": actor_id,
                    "belief_key": f"task.{slots['task_id']}.reported_status",
                    "belief_value": slots["status"],
                    "confidence": 0.65,
                    "freshness_window_min": 240,
                    "updated_at": to_iso(self.now()),
                    "source_ref": "tpm_message",
                    "metadata": {},
                }
            )

    def _apply_relationship_effect_for_outgoing(self, actor_id: str, act_id: str) -> None:
        rel = self.relationship_state(actor_id, "tpm")
        trust = float(rel.get("trust", 0.5))
        if act_id == "request.eta":
            trust = clamp(trust - 0.04, 0.0, 1.0)
        elif act_id in {"request.feasibility", "request.scope_tradeoff", "request.approval"}:
            trust = clamp(trust + 0.03, 0.0, 1.0)
        elif act_id.startswith("escalate."):
            trust = clamp(trust - 0.08, 0.0, 1.0)
        self.store.update_relationship(actor_id, "tpm", {"trust": round(trust, 2)})

    def _apply_commitment_act_from_tpm(self, actor_id: str, act_id: str, slots: dict[str, Any]) -> None:
        commitment_id = slots.get("commitment_id")
        if not commitment_id:
            return
        preconditions = slots.get("preconditions", [])
        if act_id == "commit.propose":
            next_status = "proposed"
        elif act_id == "commit.confirm":
            next_status = "tentative" if preconditions else "committed"
        elif act_id == "commit.revise":
            next_status = "tentative"
        elif act_id == "commit.retract":
            next_status = "superseded"
        else:
            next_status = "proposed"
        if not self._commitment_exists(commitment_id):
            metadata = {"created_by": "tpm", "last_transition_ref": f"action:{self.store.actions()[-1]['id']}"} if self.store.actions() else {"created_by": "tpm"}
            self.store.add_commitment(
                {
                    "id": commitment_id,
                    "owner_id": "tpm",
                    "audience_ids": [actor_id],
                    "subject": slots.get("subject", commitment_id),
                    "scope": {"from_slots": slots},
                    "status": next_status,
                    "confidence": float(slots.get("confidence", 0.6)),
                    "due_at": slots.get("due_at"),
                    "ground_truth_feasibility": 0.0,
                    "perceived_feasibility": 0.0,
                    "preconditions": preconditions,
                    "source_ref": "tpm",
                    "last_updated_at": to_iso(self.now()),
                    "metadata": metadata,
                }
            )
        else:
            row = self.commitment_state(commitment_id)
            metadata = row["metadata"]
            metadata["last_transition_ref"] = f"action:{self.store.actions()[-1]['id']}" if self.store.actions() else "action:tpm"
            update_fields = {
                "status": next_status,
                "scope_json": as_json({"from_slots": slots}),
                "confidence": float(slots.get("confidence", row["confidence"])),
                "due_at": slots.get("due_at", row["due_at"]),
                "metadata_json": as_json(metadata),
                "last_updated_at": to_iso(self.now()),
            }
            self.store.update_commitment(commitment_id, **update_fields)

    def _schedule_npc_response(self, thread, act_id: str, slots: dict[str, Any], body: str) -> None:
        participants = self.deserialize(thread["participant_ids_json"], [])
        if thread["kind"] != "dm":
            return
        actor_id = [actor for actor in participants if actor != "tpm"][0]
        due = self._response_due_time(actor_id, thread["surface"], act_id, slots)
        self.store.queue_event(
            to_iso(due),
            PHASE_PRIORITY["interaction_start"],
            "npc.respond_message",
            actor_id,
            {"thread_id": thread["id"], "incoming_act_id": act_id, "incoming_slots": slots, "incoming_body": body},
        )

    def _advance_internal(self, minutes: int, *, reason: str, log_wait_action: bool = False, log_action: bool = True) -> list[str]:
        if minutes < 0:
            raise ValueError("Cannot advance time by a negative duration.")
        current = self.now()
        end_time = min(current + timedelta(minutes=minutes), from_iso(self.store.get_meta("simulation_end")))
        notes: list[str] = []
        if log_wait_action:
            self.store.log_action(to_iso(current), "tpm", "system", "wait", {"minutes": minutes}, "", minutes, {"reason": reason})
        while True:
            due = self.store.due_events(to_iso(end_time))
            if not due:
                break
            event = due[0]
            event_time = from_iso(event["due_at"])
            if event_time > end_time:
                break
            self.store.set_current_time(event_time)
            notes.extend(self._dispatch_event(event))
            self.store.mark_event_done(event["id"])
            self._refresh_derived_state()
        self.store.set_current_time(end_time)
        self._refresh_derived_state()
        return notes

    def _spend_time(self, minutes: int, reason: str) -> list[str]:
        return self._advance_internal(minutes, reason=reason, log_action=False)

    def _dispatch_event(self, event) -> list[str]:
        payload = self.deserialize(event["payload_json"], {})
        event_type = event["type"]
        if event_type == "npc.respond_message":
            return self._event_npc_respond_message(event, payload)
        if event_type == "npc.respond_invite":
            return self._event_npc_respond_invite(event, payload)
        if event_type == "meeting.start":
            return self._event_meeting_start(event, payload)
        if event_type == "meeting.end":
            return self._event_meeting_end(event, payload)
        if event_type == "task.transition":
            return self._event_task_transition(event, payload)
        if event_type == "guarded_message":
            return self._event_guarded_message(event, payload)
        if event_type == "state.patch":
            return self._event_state_patch(event, payload)
        return [f"Unhandled event type: {event_type}"]

    def _event_npc_respond_message(self, event, payload: dict[str, Any]) -> list[str]:
        actor_id = event["actor_id"]
        thread = self.store.get_thread(payload["thread_id"])
        context = self._canonical_context(actor_id, thread["surface"], payload["incoming_act_id"], payload.get("incoming_slots", {}))
        match = self._select_context_family(context)
        envelope = self._select_response_envelope(match["family"], actor_id, payload["incoming_act_id"], payload.get("incoming_slots", {}), event["id"])
        body = self._render_text(envelope["renderer_id"], actor_id, envelope, context)
        message_id = self.store.add_message(
            {
                "thread_id": thread["id"],
                "surface": thread["surface"],
                "sender_id": actor_id,
                "act_id": envelope["outgoing_act_id"],
                "slots": envelope.get("outgoing_slots", {}),
                "body": body,
                "created_at": to_iso(self.now()),
                "unread_for_tpm": True,
                "metadata": {
                    "belief_signals": envelope.get("belief_signals", []),
                    "surface_facts": envelope.get("surface_facts", []),
                    "envelope_id": envelope["id"],
                },
            }
        )
        self.store.log_event(
            to_iso(self.now()),
            phase="interaction_start",
            event_type="npc.message_sent",
            actor_id=actor_id,
            visibility="both",
            summary=f"{actor_id} sent {envelope['outgoing_act_id']}",
            payload={"message_id": message_id, "thread_id": thread["id"], "envelope_id": envelope["id"]},
        )
        self._apply_effects(envelope.get("effects", []), source_ref=f"message:{message_id}")
        return [f"New message from {self.actor_name(actor_id)} in {thread['id']}."]

    def _event_npc_respond_invite(self, event, payload: dict[str, Any]) -> list[str]:
        actor_id = event["actor_id"]
        meeting = self.store.get_meeting(payload["meeting_id"])
        if meeting["status"] != "scheduled":
            return []
        available = self._actor_available_for_window(actor_id, from_iso(meeting["start_at"]), from_iso(meeting["end_at"]))
        context = self._canonical_context(
            actor_id,
            "calendar",
            payload["incoming_act_id"],
            {"meeting_id": payload["meeting_id"], "available_for_meeting": available},
        )
        match = self._select_context_family(context)
        envelope = self._select_response_envelope(match["family"], actor_id, payload["incoming_act_id"], {"meeting_id": payload["meeting_id"]}, event["id"])
        metadata = self.deserialize(meeting["metadata_json"], {})
        responses = metadata.get("responses", {})
        responses[actor_id] = envelope["outgoing_act_id"].split(".")[1]
        metadata["responses"] = responses
        self.store.update_meeting(meeting["id"], metadata_json=as_json(metadata))
        thread = self._resolve_thread("chat", actor_id)
        message_id = self.store.add_message(
            {
                "thread_id": thread["id"],
                "surface": "chat",
                "sender_id": actor_id,
                "act_id": envelope["outgoing_act_id"],
                "slots": {"meeting_id": meeting["id"]},
                "body": self._render_text(envelope["renderer_id"], actor_id, envelope, context),
                "created_at": to_iso(self.now()),
                "unread_for_tpm": True,
                "metadata": {},
            }
        )
        self.store.log_event(
            to_iso(self.now()),
            phase="interaction_start",
            event_type="meeting.response_recorded",
            actor_id=actor_id,
            visibility="both",
            summary=f"{actor_id} responded to meeting {meeting['id']}",
            payload={"message_id": message_id, "meeting_id": meeting["id"], "response": responses[actor_id]},
        )
        return [f"{self.actor_name(actor_id)} {responses[actor_id]} {meeting['id']}."]

    def _event_meeting_start(self, event, payload: dict[str, Any]) -> list[str]:
        meeting = self.store.get_meeting(payload["meeting_id"])
        if meeting["status"] != "scheduled":
            return []
        metadata = self.deserialize(meeting["metadata_json"], {})
        metadata["started_at"] = to_iso(self.now())
        self.store.update_meeting(meeting["id"], status="active", metadata_json=as_json(metadata))
        self.store.log_event(
            to_iso(self.now()),
            phase="interaction_start",
            event_type="meeting.started",
            actor_id="system",
            visibility="agent",
            summary=f"Meeting {meeting['id']} started",
            payload={"meeting_id": meeting["id"]},
        )
        return [f"Meeting started: {meeting['id']} ({meeting['title']})."]

    def _event_meeting_end(self, event, payload: dict[str, Any]) -> list[str]:
        meeting = self.store.get_meeting(payload["meeting_id"])
        if meeting["status"] not in {"scheduled", "active"}:
            return []
        metadata = self.deserialize(meeting["metadata_json"], {})
        attendee_ids = self.deserialize(meeting["attendee_ids_json"], [])
        responses = metadata.get("responses", {})
        actual_attendees = ["tpm"] + [actor_id for actor_id in attendee_ids if actor_id != "tpm" and responses.get(actor_id, "accepted") in {"accept", "accepted"}]
        tpm_acts = metadata.get("tpm_acts", [])
        productive_outcomes = self._resolve_meeting_outcomes(meeting, actual_attendees, metadata)
        transcript_doc_id = self._next_document_id("mtg")
        transcript_lines = [
            f"Meeting: {meeting['title']}",
            f"When: {meeting['start_at']} - {meeting['end_at']}",
            f"Attendees: {', '.join(self.actor_name(actor_id) for actor_id in actual_attendees)}",
            f"Goal: {metadata.get('goal', 'alignment')}",
            "",
            "TPM acts:",
        ]
        if tpm_acts:
            for item in tpm_acts:
                transcript_lines.append(f"- {item['act_id']} {json.dumps(item.get('slots', {}), sort_keys=True)}")
        else:
            transcript_lines.append("- passive attendance")
        transcript_lines.extend(["", "Outcomes:"])
        if productive_outcomes:
            for outcome in productive_outcomes:
                transcript_lines.append(f"- {outcome['id']}")
        else:
            transcript_lines.append("- no material outcome")
        self.store.add_document(
            {
                "id": transcript_doc_id,
                "type": "meeting_transcript",
                "title": f"Transcript: {meeting['title']}",
                "author_id": "system",
                "created_at": to_iso(self.now()),
                "updated_at": to_iso(self.now()),
                "visibility": "company",
                "content": "\n".join(transcript_lines),
                "metadata": {"meeting_id": meeting["id"], "signals": [{"belief_key": f"meeting.{meeting['id']}.completed", "belief_value": True, "confidence": 1.0, "freshness_window_min": 1440}]},
            }
        )
        metadata["completed_at"] = to_iso(self.now())
        metadata["actual_attendees"] = actual_attendees
        metadata["productive_outcome_ids"] = [item["id"] for item in productive_outcomes]
        self.store.update_meeting(meeting["id"], status="completed", transcript_doc_id=transcript_doc_id, metadata_json=as_json(metadata))
        self.store.log_event(
            to_iso(self.now()),
            phase="release",
            event_type="meeting.completed",
            actor_id="system",
            visibility="both",
            summary=f"Meeting {meeting['id']} completed",
            payload={"meeting_id": meeting["id"], "transcript_doc_id": transcript_doc_id, "outcome_ids": metadata["productive_outcome_ids"]},
        )
        for outcome in productive_outcomes:
            self._apply_effects(outcome.get("effects", []), source_ref=f"meeting:{meeting['id']}")
        return [f"Meeting completed: {meeting['id']}. Transcript saved to {transcript_doc_id}."]

    def _event_task_transition(self, event, payload: dict[str, Any]) -> list[str]:
        task_id = payload["task_id"]
        rule = self._task_transition_rule(payload["rule_id"])
        true_state = self.task_true_state(task_id)
        if true_state.get("checkpoint") != rule["from_checkpoint"]:
            true_state.pop("scheduled_transition_id", None)
            true_state.pop("scheduled_transition_at", None)
            self.store.update_task(task_id, true_state=true_state)
            return []
        if not self.predicate.evaluate(rule.get("guard"), now=self.now()).matched:
            true_state.pop("scheduled_transition_id", None)
            true_state.pop("scheduled_transition_at", None)
            self.store.update_task(task_id, true_state=true_state)
            return []
        true_state["checkpoint"] = rule["to_checkpoint"]
        true_state.pop("scheduled_transition_id", None)
        true_state.pop("scheduled_transition_at", None)
        remaining_work = self.task_metadata(task_id).get("remaining_work_by_checkpoint", {}).get(rule["to_checkpoint"])
        if remaining_work is not None:
            true_state["remaining_work"] = remaining_work
        tracker = self.task_tracker_state(task_id)
        tracker_patch = deepcopy(rule.get("tracker_patch", {}))
        tracker.update(tracker_patch)
        tracker["last_updated_at"] = to_iso(self.now())
        event_id = self.store.log_event(
            to_iso(self.now()),
            phase="progress",
            event_type="task.transitioned",
            actor_id="system",
            visibility="omniscient",
            summary=f"{task_id} transitioned to {rule['to_checkpoint']}",
            payload={"task_id": task_id, "rule_id": rule["id"]},
        )
        true_state["last_transition_ref"] = f"event:{event_id}"
        tracker["last_update_ref"] = f"event:{event_id}"
        if rule["to_checkpoint"] == "done":
            tracker["status"] = "done"
            tracker["blocker"] = ""
        self.store.update_task(task_id, true_state=true_state, tracker_state=tracker)
        self._apply_effects(rule.get("effects", []), source_ref=f"event:{event_id}")
        return [f"Task progressed: {task_id} -> {rule['to_checkpoint']}."]

    def _event_guarded_message(self, event, payload: dict[str, Any]) -> list[str]:
        guard = payload.get("guard")
        if guard and not self.predicate.evaluate(guard, now=self.now()).matched:
            return []
        thread = self._resolve_thread(payload["surface"], payload["thread_id"])
        message_id = self.store.add_message(
            {
                "thread_id": thread["id"],
                "surface": payload["surface"],
                "sender_id": payload["sender_id"],
                "act_id": payload.get("act_id"),
                "slots": payload.get("slots", {}),
                "body": payload.get("body", ""),
                "created_at": to_iso(self.now()),
                "unread_for_tpm": True,
                "metadata": payload.get("metadata", {}),
            }
        )
        self.store.log_event(
            to_iso(self.now()),
            phase="interaction_start",
            event_type="npc.message_sent",
            actor_id=payload["sender_id"],
            visibility="both",
            summary=f"{payload['sender_id']} proactively messaged TPM",
            payload={"message_id": message_id, "thread_id": thread["id"]},
        )
        for effect in payload.get("effects", []):
            self._apply_effects([effect], source_ref=f"message:{message_id}")
        return [f"New message from {self.actor_name(payload['sender_id'])} in {thread['id']}."]

    def _event_state_patch(self, event, payload: dict[str, Any]) -> list[str]:
        target = payload["target"]
        if target["kind"] == "actor_state":
            self.store.update_actor_state(target["actor_id"], target["patch"])
        elif target["kind"] == "project_state":
            self.store.update_project_state(target["patch"])
        else:
            raise ValueError(f"Unsupported state patch target: {target['kind']}")
        self.store.log_event(
            to_iso(self.now()),
            phase="progress",
            event_type="state.patched",
            actor_id="system",
            visibility="omniscient",
            summary=f"Patched {target['kind']}",
            payload={"target": target},
        )
        return []

    def _refresh_derived_state(self) -> None:
        self._refresh_fact_surfacing()
        self._refresh_commitments()
        self._refresh_milestones()
        self._refresh_project_state()
        self._maybe_schedule_task_transitions()

    def _refresh_fact_surfacing(self) -> None:
        for row in self.store.facts():
            state = self.deserialize(row["state_json"], {})
            if state.get("surfaced_at"):
                continue
            predicate = self.fact_metadata(row["id"]).get("surface_predicate")
            if not predicate:
                continue
            result = self.predicate.evaluate(predicate, now=self.now())
            if result.matched:
                state["surfaced_at"] = to_iso(self.now())
                state["surfaced_by"] = "predicate"
                state["surface_event_ref"] = result.evidence_refs[0] if result.evidence_refs else None
                self.store.update_fact(row["id"], state)

    def _try_surface_fact(self, fact_id: str, observer_id: str, event_ref: str) -> bool:
        state = self.fact_state(fact_id)
        if state.get("surfaced_at"):
            return False
        state["surfaced_at"] = to_iso(self.now())
        state["surfaced_by"] = observer_id
        state["surface_event_ref"] = event_ref
        self.store.update_fact(fact_id, state)
        return True

    def _refresh_commitments(self) -> None:
        for row in self.store.commitments():
            metadata = self.deserialize(row["metadata_json"], {})
            new_ground = round(self._compute_feasibility(row, perceived=False), 3)
            new_perceived = round(self._compute_feasibility(row, perceived=True), 3)
            new_status = row["status"]
            due_at = row["due_at"]
            if new_status == "superseded":
                pass
            elif metadata.get("fulfillment_predicate") and self.predicate.evaluate(metadata["fulfillment_predicate"], now=self.now()).matched:
                new_status = "fulfilled"
            elif due_at:
                due_time = from_iso(due_at)
                if self.now() >= due_time and new_status not in {"fulfilled", "superseded"}:
                    new_status = "broken"
                elif new_status in {"committed", "tentative"} and (due_time - self.now()).total_seconds() / 60.0 <= 240 and self._outstanding_blocker_mass(row) > 0:
                    new_status = "at_risk"
            if (
                abs(float(row["ground_truth_feasibility"]) - new_ground) > 1e-6
                or abs(float(row["perceived_feasibility"]) - new_perceived) > 1e-6
                or row["status"] != new_status
            ):
                event_id = self.store.log_event(
                    to_iso(self.now()),
                    phase="progress",
                    event_type="commitment.updated",
                    actor_id="system",
                    visibility="omniscient",
                    summary=f"Commitment {row['id']} updated",
                    payload={
                        "commitment_id": row["id"],
                        "old_status": row["status"],
                        "new_status": new_status,
                        "ground_truth_feasibility": new_ground,
                        "perceived_feasibility": new_perceived,
                    },
                )
                metadata["last_transition_ref"] = f"event:{event_id}"
                self.store.update_commitment(
                    row["id"],
                    status=new_status,
                    ground_truth_feasibility=new_ground,
                    perceived_feasibility=new_perceived,
                    last_updated_at=to_iso(self.now()),
                    metadata_json=as_json(metadata),
                )

    def _compute_feasibility(self, row, *, perceived: bool) -> float:
        metadata = self.deserialize(row["metadata_json"], {})
        task_ids = metadata.get("task_ids", [])
        milestone_ids = metadata.get("milestone_ids", [])
        outstanding = 0.0
        for task_id in task_ids:
            state = self.task_tracker_state(task_id) if perceived else self.task_true_state(task_id)
            remaining = float(state.get("remaining_work", self.task_metadata(task_id).get("remaining_work_by_checkpoint", {}).get(state.get("checkpoint", ""), 4)))
            if (state.get("status") or state.get("checkpoint")) != "done":
                outstanding += remaining
        recoverability = 1.0
        for milestone_id in milestone_ids:
            state = self.milestone_state(milestone_id)
            recoverability = min(recoverability, {"high": 1.0, "medium": 0.7, "low": 0.4, "none": 0.05}.get(state.get("recoverability", "medium"), 0.7))
        due_at = row["due_at"]
        time_factor = 0.5
        if due_at:
            remaining_minutes = max(1.0, (from_iso(due_at) - self.now()).total_seconds() / 60.0)
            time_factor = clamp(remaining_minutes / max(60.0, outstanding * 90.0), 0.0, 1.0)
        owner_pressure = 0.2
        owner_state = self.actor_state(row["owner_id"])
        owner_pressure = 1.0 - owner_state.get("priority_pressure", 0.5)
        blocker_penalty = min(0.6, self._outstanding_blocker_mass(row) * 0.2)
        raw = 0.15 + 0.35 * time_factor + 0.25 * recoverability + 0.25 * owner_pressure - blocker_penalty
        return clamp(raw, 0.0, 1.0)

    def _outstanding_blocker_mass(self, row) -> int:
        metadata = self.deserialize(row["metadata_json"], {})
        count = 0
        for task_id in metadata.get("task_ids", []):
            state = self.task_true_state(task_id)
            if state.get("checkpoint") != "done":
                count += 1
        for precondition in self.deserialize(row["preconditions_json"], []):
            if not self.predicate.evaluate(precondition, now=self.now()).matched:
                count += 1
        return count

    def _refresh_milestones(self) -> None:
        for row in self.store.milestones():
            state = self.deserialize(row["state_json"], {})
            metadata = self.deserialize(row["metadata_json"], {})
            achieved = self.predicate.evaluate(metadata.get("achieved_predicate"), now=self.now()).matched
            new_status = "done" if achieved else "pending"
            new_achieved_at = state.get("achieved_at")
            if achieved and not new_achieved_at:
                new_achieved_at = to_iso(self.now())
            new_recoverability = state.get("recoverability", "high")
            for rule in metadata.get("recoverability_rules", []):
                if self.predicate.evaluate(rule["predicate"], now=self.now()).matched:
                    new_recoverability = rule["recoverability"]
                    break
            if state.get("status") != new_status or state.get("recoverability") != new_recoverability or state.get("achieved_at") != new_achieved_at:
                event_id = self.store.log_event(
                    to_iso(self.now()),
                    phase="progress",
                    event_type="milestone.updated",
                    actor_id="system",
                    visibility="omniscient",
                    summary=f"Milestone {row['id']} updated",
                    payload={
                        "milestone_id": row["id"],
                        "old_status": state.get("status"),
                        "new_status": new_status,
                        "recoverability": new_recoverability,
                    },
                )
                state["status"] = new_status
                state["recoverability"] = new_recoverability
                state["achieved_at"] = new_achieved_at
                state["last_transition_ref"] = f"event:{event_id}"
                self.store.update_milestone(row["id"], state=state)

    def _refresh_project_state(self) -> None:
        state = self.project_state()
        for field_rule in self.policy.get("project_state_rules", []):
            for case in field_rule.get("cases", []):
                if self.predicate.evaluate(case["predicate"], now=self.now()).matched:
                    state[field_rule["field"]] = case["value"]
                    break
        state["coverage_miss"] = bool(state.get("coverage_miss"))
        self.store.update_project_state(state)

    def _maybe_schedule_task_transitions(self) -> None:
        for rule in self.policy.get("task_transitions", []):
            row = self.task_row(rule["task_id"])
            true_state = self.deserialize(row["true_state_json"], {})
            if true_state.get("checkpoint") != rule["from_checkpoint"]:
                continue
            if true_state.get("scheduled_transition_id") == rule["id"]:
                continue
            if not self.predicate.evaluate(rule.get("guard"), now=self.now()).matched:
                continue
            low, high = rule.get("delay_minutes", [30, 60])
            due = self.now() + timedelta(minutes=stable_int(self.seed, int(low), int(high), rule["id"], row["id"], to_iso(self.now())))
            true_state["scheduled_transition_id"] = rule["id"]
            true_state["scheduled_transition_at"] = to_iso(due)
            self.store.update_task(row["id"], true_state=true_state)
            self.store.queue_event(to_iso(due), PHASE_PRIORITY["progress"], "task.transition", "system", {"task_id": row["id"], "rule_id": rule["id"]})

    def _task_transition_rule(self, rule_id: str) -> dict[str, Any]:
        for rule in self.policy.get("task_transitions", []):
            if rule["id"] == rule_id:
                return rule
        raise KeyError(f"Unknown task transition rule '{rule_id}'.")

    def _schedule_direct_task_completion(self, task_id: str, low: int, high: int, *, note: str) -> None:
        row = self.task_row(task_id)
        true_state = self.deserialize(row["true_state_json"], {})
        if true_state.get("checkpoint") == "done":
            return
        due = self.now() + timedelta(minutes=stable_int(self.seed, low, high, task_id, "direct_complete", to_iso(self.now())))
        self.store.queue_event(
            to_iso(due),
            PHASE_PRIORITY["progress"],
            "task.transition",
            "system",
            {"task_id": task_id, "rule_id": f"__direct_done__:{task_id}:{stable_digest(note, due)}", "direct_to_done": True, "note": note},
        )
        self.policy.setdefault("task_transitions", []).append(
            {
                "id": f"__direct_done__:{task_id}:{stable_digest(note, due)}",
                "task_id": task_id,
                "from_checkpoint": true_state.get("checkpoint", "todo"),
                "to_checkpoint": "done",
                "guard": {},
                "delay_minutes": [0, 0],
                "tracker_patch": {"status": "done", "blocker": "", "notes": note},
                "effects": [],
            }
        )

    def _canonical_context(self, actor_id: str, surface: str, incoming_act_id: str, slots: dict[str, Any]) -> dict[str, Any]:
        actor = self.actor_row(actor_id)
        state = self.actor_state(actor_id)
        rel = self.relationship_state(actor_id, "tpm")
        return {
            "actor_id": actor_id,
            "coordination_template": actor["coordination_template"],
            "org_role": actor["org_role"],
            "surface": surface,
            "incoming_act_id": incoming_act_id,
            "trust_band": bucket_value(rel.get("trust", state.get("trust_in_tpm", 0.5))),
            "pressure_band": bucket_value(state.get("priority_pressure", 0.5)),
            "alignment_band": bucket_value(state.get("goal_alignment", 0.5)),
            "timing_band": self._timing_band(),
            "dependency_band": self._dependency_band(slots),
            "commitment_band": self._commitment_band(slots),
            "available_for_meeting": slots.get("available_for_meeting", True),
            "launch_scope": self.project_state().get("launch_scope", "undecided"),
        }

    def _timing_band(self) -> str:
        timing_bands = self.policy.get("timing_bands")
        if timing_bands:
            for rule in timing_bands:
                if "window_id" in rule and self.now() <= from_iso(self.window(rule["window_id"])["end_at"]):
                    return rule["band"]
                if "predicate" in rule and self.predicate.evaluate(rule["predicate"], now=self.now()).matched:
                    return rule["band"]
            return self.policy.get("default_timing_band", "late")
        if self.now() <= from_iso(self.window("security_cutoff")["end_at"]):
            return "early"
        if self.now() <= from_iso(self.window("customer_plan_cutoff")["end_at"]):
            return "mid"
        return "late"

    def _dependency_band(self, slots: dict[str, Any]) -> str:
        task_id = slots.get("task_id")
        if not task_id:
            return "unknown"
        tracker = self.task_tracker_state(task_id)
        return "blocked" if tracker.get("blocker") else "clear"

    def _commitment_band(self, slots: dict[str, Any]) -> str:
        commitment_id = slots.get("commitment_id")
        if not commitment_id or not self._commitment_exists(commitment_id):
            return "none"
        return self.commitment_state(commitment_id)["status"]

    def _compile_context_families(self) -> list[dict[str, Any]]:
        raw_families = {family["id"]: deepcopy(family) for family in self.coverage.get("families", [])}
        compiled: dict[str, dict[str, Any]] = {}

        def resolve(family_id: str) -> dict[str, Any]:
            if family_id in compiled:
                return compiled[family_id]
            family = deepcopy(raw_families[family_id])
            inline_selector = {}
            for key in (
                "actor_selector",
                "surface_selector",
                "incoming_act_selector",
                "trust_band",
                "pressure_band",
                "alignment_band",
                "timing_band",
                "dependency_band",
                "commitment_band",
            ):
                if key in family:
                    normalized = key.replace("_selector", "")
                    inline_selector[normalized] = family[key]
            if inline_selector:
                family["selector"] = {**family.get("selector", {}), **inline_selector}
            parent_id = family.get("extends")
            if parent_id:
                parent = resolve(parent_id)
                merged = deepcopy(parent)
                merged["id"] = family["id"]
                merged["priority"] = family.get("priority", parent.get("priority", 0))
                merged["criticality"] = family.get("criticality", parent.get("criticality", "important"))
                merged["selector"] = {**parent.get("selector", {}), **family.get("selector", {})}
                merged["guard"] = family.get("guard", parent.get("guard"))
                merged["response_envelopes"] = family.get("response_envelopes", parent.get("response_envelopes", []))
                family = merged
            compiled[family_id] = family
            return family

        for family_id in raw_families:
            resolve(family_id)
        return [compiled[family_id] for family_id in sorted(compiled)]

    def _matching_families(self, context: dict[str, Any]) -> list[ContextMatch]:
        matches: list[ContextMatch] = []
        for family in self._compiled_families:
            selector = family.get("selector", {})
            if not all(context.get(key) == value for key, value in selector.items()):
                continue
            predicate_guard = family.get("guard")
            if predicate_guard and not self.predicate.evaluate(predicate_guard, now=self.now(), context=context).matched:
                continue
            specificity = len(selector) + (1 if predicate_guard else 0)
            matches.append(ContextMatch(family=family, specificity=specificity))
        matches.sort(key=lambda item: (-item.specificity, -int(item.family.get("priority", 0)), item.family["id"]))
        return matches

    def _select_context_family(self, context: dict[str, Any]) -> dict[str, Any]:
        matches = self._matching_families(context)
        if matches:
            return {"family": matches[0].family, "context": context}
        self._record_coverage_gap(context)
        fallback = {
            "id": "fallback.defer",
            "priority": -999,
            "response_envelopes": [
                {
                    "id": "fallback.defer",
                    "weight": 1.0,
                    "outgoing_act_id": "ack.deferred",
                    "outgoing_slots": {"reason": "unhandled_context"},
                    "belief_signals": [],
                    "surface_facts": [],
                    "effects": [],
                    "renderer_id": "fallback.defer",
                }
            ],
        }
        return {"family": fallback, "context": context}

    def _record_coverage_gap(self, context: dict[str, Any]) -> None:
        project_state = self.project_state()
        project_state["coverage_miss"] = True
        self.store.update_project_state(project_state)
        self.store.log_event(
            to_iso(self.now()),
            phase="informational",
            event_type="coverage.miss",
            actor_id="system",
            visibility="omniscient",
            summary="Coverage miss encountered",
            payload={"context": context},
        )
        gap_path = Path(f"{self.store.path}.coverage_gaps.jsonl")
        gap_path.parent.mkdir(parents=True, exist_ok=True)
        with gap_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"time": to_iso(self.now()), "context": context}, sort_keys=True) + "\n")
        if self.coverage_enforcement == "strict":
            raise CoverageMissError(f"Coverage miss for context: {json.dumps(context, sort_keys=True)}")

    def _select_response_envelope(
        self,
        family: dict[str, Any],
        actor_id: str,
        incoming_act_id: str,
        incoming_slots: dict[str, Any],
        event_id: int,
    ) -> dict[str, Any]:
        envelopes = family.get("response_envelopes", [])
        if not envelopes:
            raise RuntimeError(f"Context family {family['id']} has no response envelopes.")
        return weighted_choice(self.seed, envelopes, family["id"], actor_id, incoming_act_id, as_json(incoming_slots), event_id)

    def _render_text(self, renderer_id: str, actor_id: str, envelope: dict[str, Any], context: dict[str, Any]) -> str:
        variants = self.coverage.get("renderers", {}).get(renderer_id, [renderer_id])
        index = stable_int(self.seed, 0, len(variants) - 1, renderer_id, actor_id, envelope["id"], to_iso(self.now()))
        template = variants[index]
        slots = {
            **context,
            **envelope.get("outgoing_slots", {}),
            "actor_name": self.actor_name(actor_id),
            "time": format_dt(self.now()),
        }
        try:
            return template.format(**slots)
        except Exception:
            return template

    def _selector_covers_cell(self, family_selector: dict[str, Any], cell_selector: dict[str, Any]) -> bool:
        for key, value in family_selector.items():
            if cell_selector.get(key) != value:
                return False
        return True

    def _guard_matches_cell(self, family_guard: Optional[dict[str, Any]], cell_guard: Optional[dict[str, Any]]) -> bool:
        if family_guard is None and cell_guard is None:
            return True
        if family_guard is None:
            return True
        if cell_guard is None:
            return False
        return stable_digest(as_json(family_guard)) == stable_digest(as_json(cell_guard))

    def _apply_effects(self, effects: list[dict[str, Any]], *, source_ref: str) -> None:
        for effect in effects:
            kind = effect["type"]
            if kind == "relationship_delta":
                rel = self.relationship_state(effect["actor_id"], effect["target_actor_id"])
                key = effect.get("field", "trust")
                value = clamp(float(rel.get(key, 0.5)) + float(effect.get("delta", 0.0)), 0.0, 1.0)
                self.store.update_relationship(effect["actor_id"], effect["target_actor_id"], {key: round(value, 2)})
            elif kind == "project_state_patch":
                self.store.update_project_state(effect["patch"])
            elif kind == "actor_state_patch":
                self.store.update_actor_state(effect["actor_id"], effect["patch"])
            elif kind == "belief_signal":
                self.store.add_belief(
                    {
                        "actor_id": effect["actor_id"],
                        "belief_key": effect["belief_key"],
                        "belief_value": effect.get("belief_value"),
                        "confidence": effect.get("confidence", 0.7),
                        "freshness_window_min": effect.get("freshness_window_min", 240),
                        "updated_at": to_iso(self.now()),
                        "source_ref": source_ref,
                        "metadata": {},
                    }
                )
            elif kind == "fact_signal":
                event_id = self.store.log_event(
                    to_iso(self.now()),
                    phase="informational",
                    event_type="fact_signal",
                    actor_id="system",
                    visibility="omniscient",
                    summary=f"Fact signal for {effect['fact_id']}",
                    payload={"fact_id": effect["fact_id"], "observer_id": effect.get("observer_id", "tpm"), "source_ref": source_ref},
                )
                self._try_surface_fact(effect["fact_id"], effect.get("observer_id", "tpm"), f"event:{event_id}")
            elif kind == "create_or_update_commitment":
                commitment_id = effect["id"]
                if self._commitment_exists(commitment_id):
                    row = self.commitment_state(commitment_id)
                    metadata = row["metadata"]
                    metadata["last_transition_ref"] = source_ref
                    self.store.update_commitment(
                        commitment_id,
                        status=effect.get("status", row["status"]),
                        confidence=float(effect.get("confidence", row["confidence"])),
                        due_at=effect.get("due_at", row["due_at"]),
                        scope_json=as_json(effect.get("scope", row["scope"])),
                        source_ref=source_ref,
                        last_updated_at=to_iso(self.now()),
                        metadata_json=as_json(metadata),
                    )
                else:
                    self.store.add_commitment(
                        {
                            "id": commitment_id,
                            "owner_id": effect["owner_id"],
                            "audience_ids": effect.get("audience_ids", []),
                            "subject": effect["subject"],
                            "scope": effect.get("scope", {}),
                            "status": effect.get("status", "proposed"),
                            "confidence": effect.get("confidence", 0.6),
                            "due_at": effect.get("due_at"),
                            "ground_truth_feasibility": effect.get("ground_truth_feasibility", 0.0),
                            "perceived_feasibility": effect.get("perceived_feasibility", 0.0),
                            "preconditions": effect.get("preconditions", []),
                            "source_ref": source_ref,
                            "last_updated_at": to_iso(self.now()),
                            "metadata": {**effect.get("metadata", {}), "last_transition_ref": source_ref},
                        }
                    )
            elif kind == "task_state_patch":
                task_id = effect["task_id"]
                true_state = self.task_true_state(task_id)
                tracker = self.task_tracker_state(task_id)
                true_patch = deepcopy(effect.get("true_patch", {}))
                tracker_patch = deepcopy(effect.get("tracker_patch", {}))
                if true_patch:
                    true_state.update(true_patch)
                    true_state["last_transition_ref"] = source_ref
                    checkpoint = true_state.get("checkpoint")
                    remaining = self.task_metadata(task_id).get("remaining_work_by_checkpoint", {}).get(checkpoint)
                    if remaining is not None:
                        true_state["remaining_work"] = remaining
                if tracker_patch:
                    tracker.update(tracker_patch)
                    tracker["last_update_ref"] = source_ref
                    tracker["last_updated_at"] = to_iso(self.now())
                self.store.update_task(task_id, true_state=true_state, tracker_state=tracker)
            elif kind == "meeting_schedule_hint":
                pass
            else:
                raise ValueError(f"Unsupported effect type: {kind}")

    def _resolve_meeting_outcomes(self, meeting, actual_attendees: list[str], metadata: dict[str, Any]) -> list[dict[str, Any]]:
        outcomes: list[dict[str, Any]] = []
        for rule in self.policy.get("meeting_outcomes", []):
            if rule["meeting_id"] != meeting["id"]:
                continue
            if not all(actor_id in actual_attendees for actor_id in rule.get("required_attendees", [])):
                continue
            required_tpm_acts = set(rule.get("required_tpm_acts", []))
            observed_tpm_acts = {item["act_id"] for item in metadata.get("tpm_acts", [])}
            if not required_tpm_acts.issubset(observed_tpm_acts):
                continue
            if not self.predicate.evaluate(rule.get("guard"), now=self.now()).matched:
                continue
            outcomes.append(rule)
        return outcomes

    def _commitment_exists(self, commitment_id: str) -> bool:
        try:
            self.store.get_commitment(commitment_id)
            return True
        except KeyError:
            return False

    def _task_exists(self, task_id: str) -> bool:
        try:
            self.store.get_task(task_id)
            return True
        except KeyError:
            return False

    def _next_document_id(self, prefix: str) -> str:
        existing = self.store.documents()
        return f"DOC-{prefix.upper()}-{len(existing) + 1:03d}"

    def _next_meeting_id(self) -> str:
        return f"meeting_{len(self.store.meetings()) + 1:03d}"

    def _meeting_summary(self, row) -> str:
        return f"{row['id']} [{row['status']}] {format_dt(from_iso(row['start_at']))} {row['title']}"

    def _task_tracker_summary(self, row) -> dict[str, Any]:
        tracker = self.deserialize(row["tracker_state_json"], {})
        return {
            "task_id": row["id"],
            "title": row["title"],
            "status": tracker.get("status"),
            "owner_id": tracker.get("owner_id", row["owner_id"]),
            "blocker": tracker.get("blocker"),
        }

    def _doc_summary(self, row) -> dict[str, Any]:
        return {"doc_id": row["id"], "type": row["type"], "title": row["title"]}

    def _unread_thread_summary(self, unread_rows: list[Any]) -> list[dict[str, Any]]:
        threads: dict[str, list[Any]] = defaultdict(list)
        for row in unread_rows:
            threads[row["thread_id"]].append(row)
        output: list[dict[str, Any]] = []
        for thread_id, messages in sorted(threads.items()):
            latest = max(messages, key=lambda item: (item["created_at"], item["id"]))
            output.append(
                {
                    "thread_id": thread_id,
                    "unread_count": len(messages),
                    "latest_sender_id": latest["sender_id"],
                    "latest_sender_name": self.actor_name(latest["sender_id"]),
                    "display": f"{thread_id} ({len(messages)} unread, latest from {self.actor_name(latest['sender_id'])})",
                }
            )
        return output
