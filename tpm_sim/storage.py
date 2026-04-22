from __future__ import annotations

import os
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from tpm_sim.common import as_json, from_iso, from_json, to_iso


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  state_json TEXT NOT NULL,
  metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS actors (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  org_role TEXT NOT NULL,
  coordination_template TEXT NOT NULL,
  policy_type TEXT NOT NULL,
  authority_json TEXT NOT NULL,
  traits_json TEXT NOT NULL,
  state_json TEXT NOT NULL,
  metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relationships (
  actor_id TEXT NOT NULL,
  target_actor_id TEXT NOT NULL,
  state_json TEXT NOT NULL,
  PRIMARY KEY (actor_id, target_actor_id)
);

CREATE TABLE IF NOT EXISTS windows (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  start_at TEXT NOT NULL,
  end_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS threads (
  id TEXT PRIMARY KEY,
  surface TEXT NOT NULL,
  title TEXT NOT NULL,
  kind TEXT NOT NULL,
  participant_ids_json TEXT NOT NULL,
  metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  thread_id TEXT NOT NULL,
  surface TEXT NOT NULL,
  sender_id TEXT NOT NULL,
  act_id TEXT,
  slots_json TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  unread_for_tpm INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  title TEXT NOT NULL,
  author_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  visibility TEXT NOT NULL,
  content TEXT NOT NULL,
  metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  priority TEXT NOT NULL,
  due_at TEXT NOT NULL,
  description TEXT NOT NULL,
  true_state_json TEXT NOT NULL,
  tracker_state_json TEXT NOT NULL,
  metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS milestones (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  due_at TEXT NOT NULL,
  state_json TEXT NOT NULL,
  metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dependencies (
  id TEXT PRIMARY KEY,
  src_kind TEXT NOT NULL,
  src_id TEXT NOT NULL,
  dst_kind TEXT NOT NULL,
  dst_id TEXT NOT NULL,
  dep_type TEXT NOT NULL,
  metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  description TEXT NOT NULL,
  state_json TEXT NOT NULL,
  metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS beliefs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_id TEXT NOT NULL,
  belief_key TEXT NOT NULL,
  belief_value_json TEXT NOT NULL,
  confidence REAL NOT NULL,
  freshness_window_min INTEGER NOT NULL,
  updated_at TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commitments (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  audience_ids_json TEXT NOT NULL,
  subject TEXT NOT NULL,
  scope_json TEXT NOT NULL,
  status TEXT NOT NULL,
  confidence REAL NOT NULL,
  due_at TEXT,
  ground_truth_feasibility REAL NOT NULL,
  perceived_feasibility REAL NOT NULL,
  preconditions_json TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  last_updated_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meetings (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  organizer_id TEXT NOT NULL,
  start_at TEXT NOT NULL,
  end_at TEXT NOT NULL,
  status TEXT NOT NULL,
  attendee_ids_json TEXT NOT NULL,
  agenda TEXT NOT NULL,
  transcript_doc_id TEXT,
  metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  due_at TEXT NOT NULL,
  phase_priority INTEGER NOT NULL,
  type TEXT NOT NULL,
  actor_id TEXT,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  surface TEXT NOT NULL,
  act_id TEXT NOT NULL,
  slots_json TEXT NOT NULL,
  body TEXT NOT NULL,
  duration_minutes INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  phase TEXT NOT NULL,
  event_type TEXT NOT NULL,
  actor_id TEXT,
  visibility TEXT NOT NULL,
  summary TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
"""


class StateStore:
    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator["StateStore"]:
        self.conn.execute("BEGIN")
        try:
            yield self
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def execute(self, query: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(query, tuple(params))

    def fetchone(self, query: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        return self.execute(query, params).fetchone()

    def fetchall(self, query: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self.execute(query, params).fetchall()

    def setup_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def reset(self) -> None:
        self.conn.executescript(
            """
            DROP TABLE IF EXISTS meta;
            DROP TABLE IF EXISTS project_state;
            DROP TABLE IF EXISTS actors;
            DROP TABLE IF EXISTS relationships;
            DROP TABLE IF EXISTS windows;
            DROP TABLE IF EXISTS threads;
            DROP TABLE IF EXISTS messages;
            DROP TABLE IF EXISTS documents;
            DROP TABLE IF EXISTS tasks;
            DROP TABLE IF EXISTS milestones;
            DROP TABLE IF EXISTS dependencies;
            DROP TABLE IF EXISTS facts;
            DROP TABLE IF EXISTS beliefs;
            DROP TABLE IF EXISTS commitments;
            DROP TABLE IF EXISTS meetings;
            DROP TABLE IF EXISTS pending_events;
            DROP TABLE IF EXISTS actions;
            DROP TABLE IF EXISTS event_log;
            """
        )
        self.setup_schema()

    def backup_to(self, target_path: str) -> None:
        self.conn.commit()
        target = sqlite3.connect(target_path)
        try:
            self.conn.backup(target)
            target.commit()
        finally:
            target.close()

    def set_meta(self, key: str, value: str) -> None:
        self.execute(
            """
            INSERT INTO meta (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.fetchone("SELECT value FROM meta WHERE key = ?", (key,))
        return default if row is None else row["value"]

    def current_time(self):
        value = self.get_meta("current_time")
        if value is None:
            raise RuntimeError("Simulation clock has not been initialized.")
        return from_iso(value)

    def set_current_time(self, dt) -> None:
        self.set_meta("current_time", to_iso(dt))

    def add_project_state(self, row: dict[str, Any]) -> None:
        self.execute(
            """
            INSERT INTO project_state (id, name, description, state_json, metadata_json)
            VALUES (1, ?, ?, ?, ?)
            """,
            (
                row["name"],
                row["description"],
                as_json(row.get("state", {})),
                as_json(row.get("metadata", {})),
            ),
        )

    def get_project_state(self) -> sqlite3.Row:
        row = self.fetchone("SELECT * FROM project_state WHERE id = 1")
        if row is None:
            raise RuntimeError("Project state not initialized.")
        return row

    def update_project_state(self, updates: dict[str, Any]) -> None:
        row = self.get_project_state()
        state = from_json(row["state_json"], {})
        state.update(updates)
        self.execute("UPDATE project_state SET state_json = ? WHERE id = 1", (as_json(state),))

    def update_project_metadata(self, updates: dict[str, Any]) -> None:
        row = self.get_project_state()
        metadata = from_json(row["metadata_json"], {})
        metadata.update(updates)
        self.execute("UPDATE project_state SET metadata_json = ? WHERE id = 1", (as_json(metadata),))

    def add_actor(self, row: dict[str, Any]) -> None:
        self.execute(
            """
            INSERT INTO actors (
              id, name, org_role, coordination_template, policy_type,
              authority_json, traits_json, state_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["name"],
                row["org_role"],
                row["coordination_template"],
                row.get("policy_type", "bounded_actor"),
                as_json(row.get("authority_profile", {})),
                as_json(row.get("traits", {})),
                as_json(row.get("state", {})),
                as_json(row.get("metadata", {})),
            ),
        )

    def actors(self) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM actors ORDER BY id ASC")

    def get_actor(self, actor_id: str) -> sqlite3.Row:
        row = self.fetchone("SELECT * FROM actors WHERE id = ?", (actor_id,))
        if row is None:
            raise KeyError(f"Unknown actor '{actor_id}'.")
        return row

    def update_actor_state(self, actor_id: str, updates: dict[str, Any]) -> None:
        row = self.get_actor(actor_id)
        state = from_json(row["state_json"], {})
        state.update(updates)
        self.execute("UPDATE actors SET state_json = ? WHERE id = ?", (as_json(state), actor_id))

    def add_relationship(self, actor_id: str, target_actor_id: str, state: dict[str, Any]) -> None:
        self.execute(
            """
            INSERT INTO relationships (actor_id, target_actor_id, state_json)
            VALUES (?, ?, ?)
            """,
            (actor_id, target_actor_id, as_json(state)),
        )

    def get_relationship(self, actor_id: str, target_actor_id: str) -> sqlite3.Row:
        row = self.fetchone(
            "SELECT * FROM relationships WHERE actor_id = ? AND target_actor_id = ?",
            (actor_id, target_actor_id),
        )
        if row is None:
            raise KeyError(f"Unknown relationship {actor_id}->{target_actor_id}.")
        return row

    def relationships(self) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM relationships ORDER BY actor_id ASC, target_actor_id ASC")

    def update_relationship(self, actor_id: str, target_actor_id: str, updates: dict[str, Any]) -> None:
        row = self.get_relationship(actor_id, target_actor_id)
        state = from_json(row["state_json"], {})
        state.update(updates)
        self.execute(
            "UPDATE relationships SET state_json = ? WHERE actor_id = ? AND target_actor_id = ?",
            (as_json(state), actor_id, target_actor_id),
        )

    def add_window(self, row: dict[str, Any]) -> None:
        self.execute(
            """
            INSERT INTO windows (id, title, start_at, end_at, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (row["id"], row["title"], row["start_at"], row["end_at"], as_json(row.get("metadata", {}))),
        )

    def windows(self) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM windows ORDER BY start_at ASC, id ASC")

    def get_window(self, window_id: str) -> sqlite3.Row:
        row = self.fetchone("SELECT * FROM windows WHERE id = ?", (window_id,))
        if row is None:
            raise KeyError(f"Unknown window '{window_id}'.")
        return row

    def add_thread(self, row: dict[str, Any]) -> None:
        self.execute(
            """
            INSERT INTO threads (id, surface, title, kind, participant_ids_json, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["surface"],
                row["title"],
                row["kind"],
                as_json(row.get("participants", [])),
                as_json(row.get("metadata", {})),
            ),
        )

    def threads(self, surface: Optional[str] = None) -> list[sqlite3.Row]:
        if surface:
            return self.fetchall("SELECT * FROM threads WHERE surface = ? ORDER BY id ASC", (surface,))
        return self.fetchall("SELECT * FROM threads ORDER BY surface ASC, id ASC")

    def get_thread(self, thread_id: str) -> sqlite3.Row:
        row = self.fetchone("SELECT * FROM threads WHERE id = ?", (thread_id,))
        if row is None:
            raise KeyError(f"Unknown thread '{thread_id}'.")
        return row

    def add_message(self, row: dict[str, Any]) -> int:
        cursor = self.execute(
            """
            INSERT INTO messages (
              thread_id, surface, sender_id, act_id, slots_json, body,
              created_at, unread_for_tpm, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["thread_id"],
                row["surface"],
                row["sender_id"],
                row.get("act_id"),
                as_json(row.get("slots", {})),
                row.get("body", ""),
                row["created_at"],
                int(row.get("unread_for_tpm", False)),
                as_json(row.get("metadata", {})),
            ),
        )
        return int(cursor.lastrowid)

    def messages(self, thread_id: Optional[str] = None, unread_only: bool = False, limit: int = 100) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[Any] = []
        if thread_id:
            clauses.append("thread_id = ?")
            params.append(thread_id)
        if unread_only:
            clauses.append("unread_for_tpm = 1")
        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)
        params.append(limit)
        return self.fetchall(
            f"""
            SELECT * FROM messages
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            params,
        )

    def thread_messages(self, thread_id: str, limit: int = 200) -> list[sqlite3.Row]:
        return self.fetchall(
            """
            SELECT * FROM messages
            WHERE thread_id = ?
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (thread_id, limit),
        )

    def mark_thread_read(self, thread_id: str) -> None:
        self.execute(
            "UPDATE messages SET unread_for_tpm = 0 WHERE thread_id = ? AND unread_for_tpm = 1",
            (thread_id,),
        )

    def add_document(self, row: dict[str, Any]) -> None:
        self.execute(
            """
            INSERT INTO documents (
              id, type, title, author_id, created_at, updated_at, visibility, content, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["type"],
                row["title"],
                row["author_id"],
                row["created_at"],
                row["updated_at"],
                row.get("visibility", "company"),
                row.get("content", ""),
                as_json(row.get("metadata", {})),
            ),
        )

    def update_document(self, doc_id: str, *, content: str, updated_at: str, metadata: Optional[dict[str, Any]] = None) -> None:
        if metadata is None:
            self.execute("UPDATE documents SET content = ?, updated_at = ? WHERE id = ?", (content, updated_at, doc_id))
        else:
            self.execute(
                "UPDATE documents SET content = ?, updated_at = ?, metadata_json = ? WHERE id = ?",
                (content, updated_at, as_json(metadata), doc_id),
            )

    def documents(self) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM documents ORDER BY updated_at DESC, id ASC")

    def get_document(self, doc_id: str) -> sqlite3.Row:
        row = self.fetchone("SELECT * FROM documents WHERE id = ?", (doc_id,))
        if row is None:
            raise KeyError(f"Unknown document '{doc_id}'.")
        return row

    def add_task(self, row: dict[str, Any]) -> None:
        self.execute(
            """
            INSERT INTO tasks (
              id, title, owner_id, priority, due_at, description,
              true_state_json, tracker_state_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["title"],
                row["owner_id"],
                row["priority"],
                row["due_at"],
                row["description"],
                as_json(row.get("true_state", {})),
                as_json(row.get("tracker_state", {})),
                as_json(row.get("metadata", {})),
            ),
        )

    def tasks(self) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM tasks ORDER BY due_at ASC, id ASC")

    def get_task(self, task_id: str) -> sqlite3.Row:
        row = self.fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if row is None:
            raise KeyError(f"Unknown task '{task_id}'.")
        return row

    def update_task(self, task_id: str, *, true_state: Optional[dict[str, Any]] = None, tracker_state: Optional[dict[str, Any]] = None, metadata: Optional[dict[str, Any]] = None) -> None:
        row = self.get_task(task_id)
        updates: list[str] = []
        params: list[Any] = []
        if true_state is not None:
            updates.append("true_state_json = ?")
            params.append(as_json(true_state))
        if tracker_state is not None:
            updates.append("tracker_state_json = ?")
            params.append(as_json(tracker_state))
        if metadata is not None:
            updates.append("metadata_json = ?")
            params.append(as_json(metadata))
        if not updates:
            return
        params.append(task_id)
        self.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?", params)

    def add_milestone(self, row: dict[str, Any]) -> None:
        self.execute(
            """
            INSERT INTO milestones (id, title, description, due_at, state_json, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["title"],
                row["description"],
                row["due_at"],
                as_json(row.get("state", {})),
                as_json(row.get("metadata", {})),
            ),
        )

    def milestones(self) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM milestones ORDER BY due_at ASC, id ASC")

    def get_milestone(self, milestone_id: str) -> sqlite3.Row:
        row = self.fetchone("SELECT * FROM milestones WHERE id = ?", (milestone_id,))
        if row is None:
            raise KeyError(f"Unknown milestone '{milestone_id}'.")
        return row

    def update_milestone(self, milestone_id: str, *, state: Optional[dict[str, Any]] = None, metadata: Optional[dict[str, Any]] = None) -> None:
        updates: list[str] = []
        params: list[Any] = []
        if state is not None:
            updates.append("state_json = ?")
            params.append(as_json(state))
        if metadata is not None:
            updates.append("metadata_json = ?")
            params.append(as_json(metadata))
        if not updates:
            return
        params.append(milestone_id)
        self.execute(f"UPDATE milestones SET {', '.join(updates)} WHERE id = ?", params)

    def add_dependency(self, row: dict[str, Any]) -> None:
        self.execute(
            """
            INSERT INTO dependencies (id, src_kind, src_id, dst_kind, dst_id, dep_type, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["src_kind"],
                row["src_id"],
                row["dst_kind"],
                row["dst_id"],
                row["dep_type"],
                as_json(row.get("metadata", {})),
            ),
        )

    def dependencies(self) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM dependencies ORDER BY id ASC")

    def add_fact(self, row: dict[str, Any]) -> None:
        self.execute(
            """
            INSERT INTO facts (id, label, description, state_json, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["label"],
                row["description"],
                as_json(row.get("state", {})),
                as_json(row.get("metadata", {})),
            ),
        )

    def facts(self) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM facts ORDER BY id ASC")

    def get_fact(self, fact_id: str) -> sqlite3.Row:
        row = self.fetchone("SELECT * FROM facts WHERE id = ?", (fact_id,))
        if row is None:
            raise KeyError(f"Unknown fact '{fact_id}'.")
        return row

    def update_fact(self, fact_id: str, state: dict[str, Any]) -> None:
        self.execute("UPDATE facts SET state_json = ? WHERE id = ?", (as_json(state), fact_id))

    def add_belief(self, row: dict[str, Any]) -> int:
        cursor = self.execute(
            """
            INSERT INTO beliefs (
              actor_id, belief_key, belief_value_json, confidence,
              freshness_window_min, updated_at, source_ref, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["actor_id"],
                row["belief_key"],
                as_json(row.get("belief_value")),
                float(row.get("confidence", 0.0)),
                int(row.get("freshness_window_min", 240)),
                row["updated_at"],
                row["source_ref"],
                as_json(row.get("metadata", {})),
            ),
        )
        return int(cursor.lastrowid)

    def latest_belief(self, actor_id: str, belief_key: str) -> Optional[sqlite3.Row]:
        return self.fetchone(
            """
            SELECT * FROM beliefs
            WHERE actor_id = ? AND belief_key = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (actor_id, belief_key),
        )

    def beliefs_for_actor(self, actor_id: str) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM beliefs WHERE actor_id = ? ORDER BY updated_at DESC, id DESC",
            (actor_id,),
        )

    def get_belief_by_id(self, belief_id: int) -> sqlite3.Row:
        row = self.fetchone("SELECT * FROM beliefs WHERE id = ?", (belief_id,))
        if row is None:
            raise KeyError(f"Unknown belief '{belief_id}'.")
        return row

    def add_commitment(self, row: dict[str, Any]) -> None:
        self.execute(
            """
            INSERT INTO commitments (
              id, owner_id, audience_ids_json, subject, scope_json, status, confidence,
              due_at, ground_truth_feasibility, perceived_feasibility, preconditions_json,
              source_ref, last_updated_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["owner_id"],
                as_json(row.get("audience_ids", [])),
                row["subject"],
                as_json(row.get("scope", {})),
                row["status"],
                float(row.get("confidence", 0.0)),
                row.get("due_at"),
                float(row.get("ground_truth_feasibility", 0.0)),
                float(row.get("perceived_feasibility", 0.0)),
                as_json(row.get("preconditions", [])),
                row["source_ref"],
                row["last_updated_at"],
                as_json(row.get("metadata", {})),
            ),
        )

    def commitments(self) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM commitments ORDER BY id ASC")

    def get_commitment(self, commitment_id: str) -> sqlite3.Row:
        row = self.fetchone("SELECT * FROM commitments WHERE id = ?", (commitment_id,))
        if row is None:
            raise KeyError(f"Unknown commitment '{commitment_id}'.")
        return row

    def update_commitment(self, commitment_id: str, **fields: Any) -> None:
        assignments = ", ".join(f"{column} = ?" for column in fields)
        params = list(fields.values()) + [commitment_id]
        self.execute(f"UPDATE commitments SET {assignments} WHERE id = ?", params)

    def add_meeting(self, row: dict[str, Any]) -> None:
        self.execute(
            """
            INSERT INTO meetings (
              id, title, organizer_id, start_at, end_at, status,
              attendee_ids_json, agenda, transcript_doc_id, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["title"],
                row["organizer_id"],
                row["start_at"],
                row["end_at"],
                row["status"],
                as_json(row.get("attendee_ids", [])),
                row.get("agenda", ""),
                row.get("transcript_doc_id"),
                as_json(row.get("metadata", {})),
            ),
        )

    def meetings(self) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM meetings ORDER BY start_at ASC, id ASC")

    def get_meeting(self, meeting_id: str) -> sqlite3.Row:
        row = self.fetchone("SELECT * FROM meetings WHERE id = ?", (meeting_id,))
        if row is None:
            raise KeyError(f"Unknown meeting '{meeting_id}'.")
        return row

    def update_meeting(self, meeting_id: str, **fields: Any) -> None:
        assignments = ", ".join(f"{column} = ?" for column in fields)
        params = list(fields.values()) + [meeting_id]
        self.execute(f"UPDATE meetings SET {assignments} WHERE id = ?", params)

    def queue_event(self, due_at: str, phase_priority: int, event_type: str, actor_id: Optional[str], payload: Optional[dict[str, Any]] = None) -> int:
        cursor = self.execute(
            """
            INSERT INTO pending_events (due_at, phase_priority, type, actor_id, payload_json, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
            """,
            (due_at, phase_priority, event_type, actor_id, as_json(payload or {})),
        )
        return int(cursor.lastrowid)

    def update_pending_event(self, event_id: int, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{column} = ?" for column in fields)
        params = list(fields.values()) + [event_id]
        self.execute(f"UPDATE pending_events SET {assignments} WHERE id = ?", params)

    def due_events(self, target_time: str) -> list[sqlite3.Row]:
        return self.fetchall(
            """
            SELECT * FROM pending_events
            WHERE status = 'pending'
              AND due_at <= ?
            ORDER BY due_at ASC, phase_priority ASC, id ASC
            """,
            (target_time,),
        )

    def mark_event_done(self, event_id: int) -> None:
        self.execute("UPDATE pending_events SET status = 'done' WHERE id = ?", (event_id,))

    def pending_events(self) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM pending_events WHERE status = 'pending' ORDER BY due_at ASC, phase_priority ASC, id ASC"
        )

    def log_action(
        self,
        at: str,
        actor_id: str,
        surface: str,
        act_id: str,
        slots: Optional[dict[str, Any]] = None,
        body: str = "",
        duration_minutes: int = 0,
        metadata: Optional[dict[str, Any]] = None,
    ) -> int:
        cursor = self.execute(
            """
            INSERT INTO actions (at, actor_id, surface, act_id, slots_json, body, duration_minutes, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (at, actor_id, surface, act_id, as_json(slots or {}), body, duration_minutes, as_json(metadata or {})),
        )
        return int(cursor.lastrowid)

    def actions(self) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM actions ORDER BY at ASC, id ASC")

    def log_event(
        self,
        at: str,
        phase: str,
        event_type: str,
        actor_id: Optional[str],
        visibility: str,
        summary: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> int:
        cursor = self.execute(
            """
            INSERT INTO event_log (at, phase, event_type, actor_id, visibility, summary, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (at, phase, event_type, actor_id, visibility, summary, as_json(payload or {})),
        )
        return int(cursor.lastrowid)

    def event_log(self, visibility: Optional[str] = None) -> list[sqlite3.Row]:
        if visibility is None:
            return self.fetchall("SELECT * FROM event_log ORDER BY at ASC, id ASC")
        return self.fetchall(
            """
            SELECT * FROM event_log
            WHERE visibility = ? OR visibility = 'both'
            ORDER BY at ASC, id ASC
            """,
            (visibility,),
        )

    def last_event(self, event_type: str, visibility: Optional[str] = None) -> Optional[sqlite3.Row]:
        if visibility is None:
            return self.fetchone(
                "SELECT * FROM event_log WHERE event_type = ? ORDER BY at DESC, id DESC LIMIT 1",
                (event_type,),
            )
        return self.fetchone(
            """
            SELECT * FROM event_log
            WHERE event_type = ?
              AND (visibility = ? OR visibility = 'both')
            ORDER BY at DESC, id DESC
            LIMIT 1
            """,
            (event_type, visibility),
        )


def open_store(path: str) -> StateStore:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    store = StateStore(path)
    store.setup_schema()
    return store


def copy_database(source_path: str, target_path: str) -> None:
    Path(target_path).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, target_path)
