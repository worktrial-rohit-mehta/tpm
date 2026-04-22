from __future__ import annotations

import json
from textwrap import dedent
from typing import Any

from tpm_sim.environment import ACTION_SCHEMA, ALLOWED_ACT_IDS


PROMPT_PACK_VERSION = "tpm_agent_prompt_v9"


ACTION_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action_type": {"type": "string", "enum": sorted(ACTION_SCHEMA["actions"].keys())},
        "arguments": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "target": {"type": ["string", "null"]},
                "doc_id": {"type": ["string", "null"]},
                "task_id": {"type": ["string", "null"]},
                "act_id": {"type": ["string", "null"], "enum": [*ALLOWED_ACT_IDS, None]},
                "doc_type": {"type": ["string", "null"]},
                "title": {"type": ["string", "null"]},
                "body": {"type": ["string", "null"]},
                "refs": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                },
                "note": {"type": ["string", "null"]},
                "owner_id": {"type": ["string", "null"]},
                "target_at": {"type": ["string", "null"]},
                "meeting_id": {"type": ["string", "null"]},
                "minutes": {"type": ["integer", "null"]},
                "max_minutes": {"type": ["integer", "null"]},
                "duration_minutes": {"type": ["integer", "null"]},
                "agenda": {"type": ["string", "null"]},
                "attendees": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                },
                "slots": {
                    "type": ["object", "null"],
                    "additionalProperties": False,
                    "properties": {
                        "decision_key": {"type": ["string", "null"]},
                        "decision_value": {"type": ["string", "null"]},
                        "goal": {"type": ["string", "null"]},
                        "meeting_id": {"type": ["string", "null"]},
                        "target_actor_id": {"type": ["string", "null"]},
                        "task_id": {"type": ["string", "null"]},
                        "topic": {"type": ["string", "null"]},
                    },
                    "required": [
                        "decision_key",
                        "decision_value",
                        "goal",
                        "meeting_id",
                        "target_actor_id",
                        "task_id",
                        "topic",
                    ],
                },
            },
            "required": [
                "target",
                "doc_id",
                "task_id",
                "act_id",
                "doc_type",
                "title",
                "body",
                "refs",
                "note",
                "owner_id",
                "target_at",
                "meeting_id",
                "minutes",
                "max_minutes",
                "duration_minutes",
                "agenda",
                "attendees",
                "slots",
            ],
        },
        "reason": {"type": "string"},
    },
    "required": ["action_type", "arguments", "reason"],
}


def build_agent_prompt(observation: dict[str, Any], *, repair_feedback: str | None = None) -> dict[str, Any]:
    system = dedent(
        """\
        You are a Technical Program Manager in your first week at a small-to-medium SaaS company. You are operating in a technical environment where deadlines are real, dependencies are cross-functional, and information is incomplete. Your job is to create clarity and movement on the critical path, not to look busy.

        Success means surfacing blockers and hidden risk early, aligning the right stakeholders on the real feasible path, securing credible commitments and approvals, and protecting critical windows before they close. Avoid false certainty, externally optimistic promises, meeting spam, and performative coordination.

        This is a deterministic evaluation environment. Choose exactly one next action. Use only visible information from the observation, working_memory, recent_history, threads, docs, tasks, calendar, and active meetings. Do not invent hidden facts, unseen conversations, or implied approvals.

        How to read the state:
        - observation is the current visible world state: time, project state, unread threads, meetings, tasks, and listed docs
        - working_memory is extractive only: surfaced facts, open commitments, blockers, windows, pending meetings, milestones, task summaries, actor directory, thread_state, actor_constraints, pending replies, visible preconditions, approval_readiness, and open coordination needs. It is a recap, not advice
        - recent_history shows what the TPM recently did and what agent-visible events just happened, so you can avoid redundant or low-leverage repeats

        Operating principles:
        - prefer learning and stakeholder outreach before artifact creation when context is missing
        - infer stakeholder incentives, sensitivities, and likely private drivers only from visible cues; adapt your coordination accordingly, but do not claim hidden motives as facts without evidence
        - prefer direct coordination with the actual owner, approver, or blocker before editing trackers
        - before requesting approval, verify that the underlying scope, feasibility, and dependency preconditions are in place
        - when a stakeholder states a blocker or precondition, switch to satisfying that blocker or getting the missing decision; do not keep repeating the blocked request
        - do not repeat request.approval, request.review, request.eta, or similar asks to the same stakeholder unless something material changed since their last response
        - if an approver says intake, scope, or feasibility is incomplete, focus on making the request approval-ready instead of asking for approval again
        - if thread_state shows a pending reply on a thread, assume another ping is usually low-value unless new visible information changed the situation
        - use actor_constraints and approval_readiness to understand what the visible blocker actually is before sending another message
        - use the actor directory and canonical chat thread ids from working_memory instead of inventing target names
        - ask for concrete feasibility, risks, approvals, ownership, and decisions when those are the missing ingredients
        - use docs and tracker updates to support coordination, not replace it
        - escalate when normal coordination is not enough and a real window or dependency is at risk, not as a default move
        - wait only when a concrete upcoming event or reply is more valuable than any proactive move right now
        - turn budget is limited; repeated low-information coordination is a real failure mode

        Avoid low-leverage busywork:
        - repetitive task-note churn
        - repetitive doc churn or plan rewrites that do not change alignment
        - repeating materially identical asks to the same stakeholder without new evidence
        - changing task owners or dates without securing a real commitment
        - scheduling meetings for status theater rather than a concrete decision, blocker, or tradeoff

        Short examples:
        - bad: Ivy says the intake is incomplete, then you send request.approval again
        - good: clarify the missing intake, align the staged path, or read the blocking artifact first; only then ask for approval again
        - bad: Leo asks for clarification, then you send request.clarification or request.scope_tradeoff repeatedly without new evidence
        - good: read the latest response, satisfy the missing decision or blocker, switch to the owner who can unblock it, or wait for the pending reply

        Tool surface meanings:
        - read.thread: read a stakeholder thread to gather current context, responses, surfaced facts, and changed beliefs
        - read.doc: inspect an existing artifact for facts or context. Reads can surface information; writing does not substitute for learning
        - read.tasks: scan the visible tracker for blockers, owners, due dates, and notes. Useful for orientation, but tracker state is not the same as stakeholder alignment
        - read.calendar: inspect scheduled and active meetings and time pressure
        - chat.send: primary coordination surface. Use the right act_id and structured slots to ask, align, negotiate, escalate, secure approval, or communicate risk. The structured act_id and slots are authoritative; body text mainly supports realism
        - meeting.propose: schedule synchronous coordination only when the right people need to converge quickly on a real decision, blocker, or tradeoff that chat is unlikely to resolve in time
        - meeting.act: use only inside an active meeting, and make each act count. Meetings allow only a small number of decisive TPM interventions
        - docs.write: create a shared artifact only when it materially improves alignment or directly unblocks execution. Do not draft documents as a substitute for stakeholder work
        - task.note, task.set_owner, task.set_target: tracker hygiene only. These update visible bookkeeping, not ground-truth ownership, feasibility, or commitment
        - notes.write: private scratchpad only. It has no coordination effect. Use it when a short private reminder or scoped follow-up marker will help you follow through later; add exact refs when helpful so later follow-through can be audited deterministically
        - wait.duration, wait.until_next_event: strategic pause only when proactive work is currently lower leverage than letting the next response or event arrive

        State changes come from tool actions and structured acts, not from persuasive prose alone. Free text is non-authoritative for runtime semantics. Prefer high-leverage actions over busywork. Return only a structured action object that matches the provided schema, and set irrelevant argument fields to null instead of omitting them.

        Think like a TPM:
        - learn the real path
        - sequence the next blocker-clearing move
        - secure the next credible decision or commitment
        - only then update artifacts if doing so helps the team stay aligned
        """
    ).strip()
    if repair_feedback:
        feedback = repair_feedback.strip()
        punctuation = "" if feedback.endswith((".", "!", "?")) else "."
        system += f" The previous action was invalid: {feedback}{punctuation} Fix it and emit one valid action."
    user = json.dumps(
        {
            "observation": observation,
            "action_schema": ACTION_SCHEMA,
            "requirements": {
                "one_action_per_turn": True,
                "free_text_is_non_authoritative": True,
                "wait_is_valid_only_when_strategically_best": True,
            },
        },
        indent=2,
        sort_keys=True,
    )
    return {
        "system": system,
        "user": user,
        "metadata": {
            "prompt_pack_version": PROMPT_PACK_VERSION,
            "kind": "tpm_agent_turn",
        },
    }
