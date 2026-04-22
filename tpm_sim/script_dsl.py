from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tpm_sim.common import csv_ids, parse_duration, parse_slot_map, split_pipe_args
from tpm_sim.environment import ALLOWED_ACT_IDS, NOTE_REF_KINDS, StructuredAction, validate_structured_action


SCRIPT_COMMAND_TEMPLATES = (
    "status",
    "people",
    "inbox",
    "observe",
    "tasks",
    "calendar",
    "docs list",
    "docs open DOC-ID",
    "docs write TYPE | TITLE | BODY",
    "notes write TITLE | BODY",
    "notes write TITLE | REF1,REF2 | BODY",
    "chat list",
    "chat open THREAD_OR_ACTOR",
    "chat send TARGET | ACT_ID | key=value,... | BODY",
    "calendar schedule 30m | maya,andrew | TITLE | key=value,... | AGENDA",
    "meeting act MEETING_ID | ACT_ID | key=value,... | BODY",
    "task note TASK-ID | NOTE",
    "task owner TASK-ID | OWNER-ID",
    "task target TASK-ID | YYYY-MM-DDTHH:MM:SS",
    "wait 60m",
    "wait next 120m",
    "coverage",
    "score",
    "log",
    "checkpoint LABEL",
    "fork LABEL | OUT_DB_PATH | [SEED]",
    "quit",
)

SCRIPT_DSL_RULES = (
    "Use only the supported lowercase shell-style commands listed below.",
    "Write exactly one command per line. Lines starting with # are comments.",
    "Commands with structured arguments use pipe-delimited fields exactly as shown.",
    "Slot maps use comma-delimited key=value fragments or - when there are no slots.",
    "Private notes support either TITLE | BODY or TITLE | ref1,ref2 | BODY. Use - for an explicit empty ref field.",
    "Do not invent uppercase verbs like READ_DOC, SEND_CHAT, CREATE_COMMITMENT, HOLD_MEETING, UPDATE_DOCUMENT, or SCHEDULE_MEETING.",
    "There is no direct CREATE_COMMITMENT command in the .tpm DSL; commitments come from valid chat.send or meeting.act actions plus scenario semantics.",
)

SCRIPT_UNSUPPORTED_ALIASES = {
    "CREATE_COMMITMENT": "There is no direct CREATE_COMMITMENT command. Use `chat send` or `meeting act` with a valid act_id and slots instead.",
    "HOLD_MEETING": "Use `calendar schedule 30m | attendees | TITLE | key=value,... | AGENDA`, then `meeting act MEETING_ID | ACT_ID | key=value,... | BODY` after the meeting exists.",
    "READ_DOC": "Use `docs open DOC-ID`.",
    "READ_DOCUMENT": "Use `docs open DOC-ID`.",
    "READ_THREAD": "Use `chat open THREAD_OR_ACTOR`.",
    "SCHEDULE_MEETING": "Use `calendar schedule 30m | attendees | TITLE | key=value,... | AGENDA`.",
    "SEND_CHAT": "Use `chat send TARGET | ACT_ID | key=value,... | BODY`.",
    "UPDATE_DOCUMENT": "Use `docs write TYPE | TITLE | BODY`.",
}

HELP_TEXT = "Commands:\n  " + "\n  ".join(SCRIPT_COMMAND_TEMPLATES)


@dataclass(frozen=True)
class ParsedScriptCommand:
    kind: str
    name: str | None = None
    action: StructuredAction | None = None
    args: dict[str, Any] = field(default_factory=dict)


def trajectory_prompt_contract() -> dict[str, Any]:
    return {
        "valid_command_templates": list(SCRIPT_COMMAND_TEMPLATES),
        "supported_act_ids": sorted(ALLOWED_ACT_IDS),
        "unsupported_alias_hints": dict(SCRIPT_UNSUPPORTED_ALIASES),
        "validation_rule": "Any trajectory line that falls outside this DSL will be rejected by static validation before benchmark validation runs.",
        "rules": list(SCRIPT_DSL_RULES),
    }


def parse_script_command(raw_line: str) -> ParsedScriptCommand:
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return ParsedScriptCommand("noop")

    if line in {"quit", "exit"}:
        return ParsedScriptCommand("exit")
    if line in {"help", "?"}:
        return ParsedScriptCommand("builtin", name="help")

    if line in {"status", "people", "inbox", "observe", "docs list", "chat list", "score", "coverage", "log"}:
        return ParsedScriptCommand("builtin", name=line)
    if line == "tasks":
        return _structured("read.tasks", {})
    if line == "calendar":
        return _structured("read.calendar", {})

    if line.startswith("wait next "):
        return _structured("wait.until_next_event", {"max_minutes": parse_duration(line.removeprefix("wait next ").strip())})
    if line.startswith("wait "):
        return _structured("wait.duration", {"minutes": parse_duration(line.removeprefix("wait ").strip())})
    if line.startswith("docs open "):
        return _structured("read.doc", {"doc_id": line.removeprefix("docs open ").strip()})
    if line.startswith("docs write "):
        doc_type, title, body = split_pipe_args(line.removeprefix("docs write "), expected=3)
        return _structured("docs.write", {"doc_type": doc_type, "title": title, "body": body})
    if line.startswith("notes write "):
        parts = split_pipe_args(line.removeprefix("notes write "))
        if len(parts) == 2:
            title, body = parts
            return _structured("notes.write", {"title": title, "body": body})
        if len(parts) == 3:
            title, raw_refs, body = parts
            return _structured("notes.write", {"title": title, "refs": _parse_note_refs(raw_refs), "body": body})
        raise ValueError("notes write expects TITLE | BODY or TITLE | ref1,ref2 | BODY")
    if line.startswith("chat open "):
        return _structured("read.thread", {"target": line.removeprefix("chat open ").strip()})
    if line.startswith("chat send "):
        target, act_id, raw_slots, body = split_pipe_args(line.removeprefix("chat send "), expected=4)
        return _structured("chat.send", {"target": target, "act_id": act_id, "slots": parse_slot_map(raw_slots), "body": body})
    if line.startswith("calendar schedule "):
        duration, attendees, title, raw_slots, agenda = split_pipe_args(line.removeprefix("calendar schedule "), expected=5)
        return _structured(
            "meeting.propose",
            {
                "duration_minutes": parse_duration(duration),
                "attendees": csv_ids(attendees),
                "title": title,
                "slots": parse_slot_map(raw_slots),
                "agenda": agenda,
            },
        )
    if line.startswith("meeting act "):
        meeting_id, act_id, raw_slots, body = split_pipe_args(line.removeprefix("meeting act "), expected=4)
        return _structured("meeting.act", {"meeting_id": meeting_id, "act_id": act_id, "slots": parse_slot_map(raw_slots), "body": body})
    if line.startswith("task note "):
        task_id, note = split_pipe_args(line.removeprefix("task note "), expected=2)
        return _structured("task.note", {"task_id": task_id, "note": note})
    if line.startswith("task owner "):
        task_id, owner_id = split_pipe_args(line.removeprefix("task owner "), expected=2)
        return _structured("task.set_owner", {"task_id": task_id, "owner_id": owner_id})
    if line.startswith("task target "):
        task_id, target_at = split_pipe_args(line.removeprefix("task target "), expected=2)
        return _structured("task.set_target", {"task_id": task_id, "target_at": target_at})
    if line.startswith("checkpoint "):
        label = line.removeprefix("checkpoint ").strip()
        if not label:
            raise ValueError("checkpoint expects a non-empty label.")
        return ParsedScriptCommand("checkpoint", args={"label": label})
    if line.startswith("fork "):
        parts = split_pipe_args(line.removeprefix("fork "))
        if len(parts) not in {2, 3}:
            raise ValueError("fork expects LABEL | OUT_DB_PATH | [SEED]")
        seed_override = None
        if len(parts) == 3:
            seed_override = int(parts[2])
        return ParsedScriptCommand("fork", args={"label": parts[0], "out_db": parts[1], "seed_override": seed_override})

    raise ValueError(_unknown_command_message(line))


def validate_trajectory_payload(
    payload: dict[str, Any],
    *,
    scenario: dict[str, Any] | None = None,
    require_smoke_like: bool = True,
) -> list[str]:
    errors: list[str] = []
    has_smoke_like = False
    for name, content in payload.items():
        if not isinstance(name, str) or not name.endswith(".tpm"):
            errors.append(f"{name}: trajectory filenames must end in .tpm")
            continue
        if not isinstance(content, str):
            errors.append(f"{name}: trajectory contents must be strings")
            continue
        if name in {"smoke.tpm", "golden.tpm"}:
            has_smoke_like = True
        errors.extend(validate_trajectory_script_text(content, script_name=name, scenario=scenario))
    if require_smoke_like and payload and not has_smoke_like:
        errors.append("trajectory bundle must include smoke.tpm or golden.tpm")
    return errors


def validate_trajectory_script_text(
    text: str,
    *,
    script_name: str = "<inline>",
    scenario: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            parsed = parse_script_command(stripped)
        except Exception as exc:
            errors.append(f"{script_name}:{line_no}: {exc}")
            continue
        errors.extend(f"{script_name}:{line_no}: {message}" for message in _scenario_reference_errors(parsed, scenario))
    return errors


def _structured(action_type: str, arguments: dict[str, Any]) -> ParsedScriptCommand:
    action = StructuredAction(action_type, arguments)
    validate_structured_action(action)
    return ParsedScriptCommand("structured", action=action)


def _scenario_reference_errors(parsed: ParsedScriptCommand, scenario: dict[str, Any] | None) -> list[str]:
    if scenario is None or parsed.action is None:
        return []

    action = parsed.action
    actor_ids = {
        actor["id"]
        for actor in scenario.get("world", {}).get("actors", [])
        if isinstance(actor, dict) and isinstance(actor.get("id"), str)
    }
    doc_ids = {
        doc["id"]
        for doc in scenario.get("world", {}).get("documents", [])
        if isinstance(doc, dict) and isinstance(doc.get("id"), str)
    }
    task_ids = {
        task["id"]
        for task in scenario.get("world", {}).get("tasks", [])
        if isinstance(task, dict) and isinstance(task.get("id"), str)
    }
    thread_ids = {
        thread["id"]
        for thread in scenario.get("world", {}).get("threads", [])
        if isinstance(thread, dict) and isinstance(thread.get("id"), str)
    }
    meeting_ids = {
        meeting["id"]
        for meeting in scenario.get("world", {}).get("meetings", [])
        if isinstance(meeting, dict) and isinstance(meeting.get("id"), str)
    }

    errors: list[str] = []
    if action.action_type == "read.doc":
        doc_id = str(action.arguments.get("doc_id", ""))
        if doc_id and doc_ids and doc_id not in doc_ids:
            errors.append(f"unknown doc_id '{doc_id}'")
    if action.action_type == "notes.write":
        for raw_ref in action.arguments.get("refs", []) or []:
            kind, ref_id = _split_note_ref(raw_ref)
            if kind == "actor" and actor_ids and ref_id not in actor_ids:
                errors.append(f"unknown actor ref '{raw_ref}'")
            if kind == "thread" and thread_ids and ref_id not in thread_ids:
                errors.append(f"unknown thread ref '{raw_ref}'")
            if kind == "task" and task_ids and ref_id not in task_ids:
                errors.append(f"unknown task ref '{raw_ref}'")
            if kind == "doc" and doc_ids and ref_id not in doc_ids:
                errors.append(f"unknown doc ref '{raw_ref}'")
            if kind == "meeting" and meeting_ids and ref_id not in meeting_ids:
                errors.append(f"unknown meeting ref '{raw_ref}'")
    if action.action_type in {"task.note", "task.set_owner", "task.set_target"}:
        task_id = str(action.arguments.get("task_id", ""))
        if task_id and task_ids and task_id not in task_ids:
            errors.append(f"unknown task_id '{task_id}'")
        owner_id = action.arguments.get("owner_id")
        if owner_id is not None and actor_ids and str(owner_id) not in actor_ids:
            errors.append(f"unknown owner_id '{owner_id}'")
    if action.action_type == "meeting.propose":
        attendees = action.arguments.get("attendees", [])
        unknown = [actor_id for actor_id in attendees if actor_ids and actor_id not in actor_ids]
        if unknown:
            errors.append(f"unknown attendee ids: {', '.join(unknown)}")
    if action.action_type == "chat.send":
        task_id = action.arguments.get("slots", {}).get("task_id")
        if task_id is not None and task_ids and str(task_id) not in task_ids:
            errors.append(f"unknown task_id '{task_id}'")
    return errors


def _unknown_command_message(line: str) -> str:
    token = line.split(maxsplit=1)[0]
    hint = SCRIPT_UNSUPPORTED_ALIASES.get(token.upper())
    if hint:
        return f"Unknown command: {line}. {hint}"
    return f"Unknown command: {line}"


def _parse_note_refs(raw: str) -> list[str]:
    text = raw.strip()
    if not text or text == "-":
        return []
    refs = [item.strip() for item in text.split(",") if item.strip()]
    if not refs:
        return []
    return refs


def _split_note_ref(raw_ref: str) -> tuple[str, str]:
    kind, separator, ref_id = raw_ref.partition(":")
    if not separator or not kind or not ref_id:
        raise ValueError(f"invalid note ref '{raw_ref}'")
    if kind not in NOTE_REF_KINDS:
        raise ValueError(f"invalid note ref kind '{kind}'")
    return kind, ref_id
