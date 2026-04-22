# ACT_TAXONOMY_v1

This document freezes the structured communication acts used by the TPM harness runtime.

## Principles

- All authoritative runtime semantics come from `act_id` and structured `slots`.
- Free-text bodies are stored and rendered for realism only.
- Acts are valid only on the surfaces explicitly listed below.
- Commitment, belief, and relationship effects are driven by the kernel and authored scenario artifacts, never by keyword matching.

## Common Fields

Every structured act has:

- `act_id`
- `surface`
- `slots`
- `body`

`body` is optional for runtime semantics and mandatory only for human-facing rendering.

## Acts

### inform.status_update

- Semantics: Share the current visible status of a task, milestone, or plan.
- Required slots: one of `task_id`, `milestone_id`, `topic`
- Optional slots: `status`, `confidence`, `next_step`, `owner_id`
- Example: `inform.status_update { milestone_id: "customer_plan_locked", status: "at_risk" }`
- Typical emitters: TPM, sponsor, customer-facing bridge
- Commitment effects: none directly
- Belief effects: yes, if the referenced entity is visible to the recipient
- Relationship effects: neutral by default
- Valid surfaces: `chat`, `meeting`

### inform.risk

- Semantics: Surface a concrete delivery or coordination risk.
- Required slots: `topic`
- Optional slots: `task_id`, `milestone_id`, `fact_id`, `severity`, `owner_id`, `window_id`
- Example: `inform.risk { fact_id: "backend_infeasible_for_friday", severity: "high" }`
- Typical emitters: TPM, critical-path owner, sponsor
- Commitment effects: may downgrade related commitments indirectly
- Belief effects: yes
- Relationship effects: usually positive with sponsors, neutral elsewhere
- Valid surfaces: `chat`, `meeting`

### inform.blocker

- Semantics: State a specific blocker, constraint, or unmet prerequisite.
- Required slots: one of `task_id`, `milestone_id`, `fact_id`
- Optional slots: `blocking_actor_id`, `dependency_id`, `reason`, `next_needed_act`
- Example: `inform.blocker { task_id: "backend_api", fact_id: "security_review_required" }`
- Typical emitters: TPM, critical-path owner, dependency owner
- Commitment effects: may move commitments to `at_risk`
- Belief effects: yes
- Relationship effects: neutral
- Valid surfaces: `chat`, `meeting`

### inform.decision

- Semantics: Announce a scope, timing, ownership, or launch decision.
- Required slots: `decision_key`
- Optional slots: `decision_value`, `milestone_id`, `owner_id`, `scope_variant`, `effective_at`
- Example: `inform.decision { decision_key: "launch_scope", decision_value: "descoped_pilot" }`
- Typical emitters: TPM, sponsor
- Commitment effects: may confirm or supersede commitments
- Belief effects: yes
- Relationship effects: positive when aligned, negative if premature
- Valid surfaces: `chat`, `meeting`

### inform.availability

- Semantics: State current or future availability constraints.
- Required slots: `available_state`
- Optional slots: `available_from`, `available_until`, `reason`
- Example: `inform.availability { available_state: "busy", available_from: "2026-05-04T15:00:00" }`
- Typical emitters: any actor
- Commitment effects: may revise perceived feasibility
- Belief effects: yes
- Relationship effects: neutral
- Valid surfaces: `chat`, `meeting`

### request.eta

- Semantics: Ask for a delivery date or time estimate.
- Required slots: one of `task_id`, `milestone_id`, `topic`
- Optional slots: `target_time`, `reason`
- Example: `request.eta { task_id: "backend_api", target_time: "2026-05-07T12:00:00" }`
- Typical emitters: TPM, sponsor
- Commitment effects: may trigger commitment proposal or revision
- Belief effects: no direct effect
- Relationship effects: often negative when overused under pressure
- Valid surfaces: `chat`, `meeting`

### request.feasibility

- Semantics: Ask for the honest feasible path rather than a nominal date.
- Required slots: one of `task_id`, `milestone_id`, `topic`
- Optional slots: `scope_variant`, `deadline`
- Example: `request.feasibility { task_id: "backend_api", deadline: "2026-05-08T15:00:00" }`
- Typical emitters: TPM
- Commitment effects: may trigger commitment proposal or blocker disclosure
- Belief effects: no direct effect
- Relationship effects: often positive with skeptical owners
- Valid surfaces: `chat`, `meeting`

### request.scope_tradeoff

- Semantics: Ask what scope reduction or tradeoff makes the plan feasible.
- Required slots: `topic`
- Optional slots: `task_id`, `milestone_id`, `deadline`
- Example: `request.scope_tradeoff { milestone_id: "pilot_ready" }`
- Typical emitters: TPM, sponsor
- Commitment effects: may create scope decision proposals
- Belief effects: no direct effect
- Relationship effects: positive when timely
- Valid surfaces: `chat`, `meeting`

### request.review

- Semantics: Ask for a review slot or review work to start.
- Required slots: `task_id`
- Optional slots: `window_id`, `scope_variant`, `artifact_id`
- Example: `request.review { task_id: "security_review", window_id: "security_cutoff" }`
- Typical emitters: TPM
- Commitment effects: may create tentative review commitments
- Belief effects: no direct effect
- Relationship effects: neutral
- Valid surfaces: `chat`, `meeting`

### request.approval

- Semantics: Ask an actor with decision rights to approve scope, design, or readiness.
- Required slots: one of `task_id`, `milestone_id`, `decision_key`
- Optional slots: `scope_variant`, `artifact_id`
- Example: `request.approval { task_id: "design_signoff", scope_variant: "descoped_pilot" }`
- Typical emitters: TPM
- Commitment effects: may produce `approve.*` and confirm commitments
- Belief effects: no direct effect
- Relationship effects: neutral
- Valid surfaces: `chat`, `meeting`

### request.clarification

- Semantics: Ask for missing context, assumptions, or interpretation.
- Required slots: `topic`
- Optional slots: `task_id`, `doc_id`, `thread_id`
- Example: `request.clarification { topic: "customer pain points" }`
- Typical emitters: TPM, any actor
- Commitment effects: none directly
- Belief effects: none directly
- Relationship effects: neutral
- Valid surfaces: `chat`, `meeting`

### request.ownership

- Semantics: Ask who can own or unblock a work item.
- Required slots: one of `task_id`, `milestone_id`, `topic`
- Optional slots: `candidate_actor_id`
- Example: `request.ownership { task_id: "frontend_pilot" }`
- Typical emitters: TPM, sponsor
- Commitment effects: may create owner commitments
- Belief effects: none directly
- Relationship effects: neutral
- Valid surfaces: `chat`, `meeting`

### negotiate.scope

- Semantics: Propose or counter a scope variant.
- Required slots: `scope_variant`
- Optional slots: `milestone_id`, `reason`
- Example: `negotiate.scope { scope_variant: "descoped_pilot", milestone_id: "pilot_ready" }`
- Typical emitters: TPM, sponsor, critical-path owner
- Commitment effects: may create or revise scope commitments
- Belief effects: yes when accepted
- Relationship effects: positive when realistic
- Valid surfaces: `chat`, `meeting`

### negotiate.timeline

- Semantics: Propose or counter a timeline given current constraints.
- Required slots: one of `task_id`, `milestone_id`
- Optional slots: `proposed_due_at`, `confidence`
- Example: `negotiate.timeline { task_id: "backend_api", proposed_due_at: "2026-05-07T12:00:00" }`
- Typical emitters: TPM, critical-path owner, sponsor
- Commitment effects: may create or revise timing commitments
- Belief effects: yes when accepted
- Relationship effects: neutral
- Valid surfaces: `chat`, `meeting`

### negotiate.ownership

- Semantics: Propose or counter who should own a task or subpath.
- Required slots: one of `task_id`, `milestone_id`
- Optional slots: `owner_id`, `scope_variant`
- Example: `negotiate.ownership { task_id: "frontend_pilot", owner_id: "andrew" }`
- Typical emitters: TPM, sponsor, ally
- Commitment effects: may create owner commitments
- Belief effects: yes when accepted
- Relationship effects: neutral
- Valid surfaces: `chat`, `meeting`

### commit.propose

- Semantics: Offer a tentative promise or decision under current assumptions.
- Required slots: `commitment_id`
- Optional slots: `due_at`, `scope_variant`, `confidence`, `preconditions`
- Example: `commit.propose { commitment_id: "backend_descoped_eta", due_at: "2026-05-07T12:00:00" }`
- Typical emitters: TPM, owner, sponsor
- Commitment effects: create or revise commitment to `proposed`
- Belief effects: yes
- Relationship effects: neutral
- Valid surfaces: `chat`, `meeting`

### commit.confirm

- Semantics: Convert a proposal into a stronger shared commitment.
- Required slots: `commitment_id`
- Optional slots: `due_at`, `confidence`
- Example: `commit.confirm { commitment_id: "pilot_scope" }`
- Typical emitters: actor who owns or authorizes the commitment
- Commitment effects: `proposed -> tentative/committed`
- Belief effects: yes
- Relationship effects: positive when credible
- Valid surfaces: `chat`, `meeting`

### commit.revise

- Semantics: Change the scope, date, or confidence of an existing commitment.
- Required slots: `commitment_id`
- Optional slots: `due_at`, `scope_variant`, `confidence`, `reason`
- Example: `commit.revise { commitment_id: "backend_descoped_eta", due_at: "2026-05-08T11:00:00" }`
- Typical emitters: commitment owner, TPM
- Commitment effects: revise and possibly downgrade status
- Belief effects: yes
- Relationship effects: neutral or negative depending on lateness
- Valid surfaces: `chat`, `meeting`

### commit.retract

- Semantics: Retract or supersede an existing commitment.
- Required slots: `commitment_id`
- Optional slots: `reason`, `superseded_by`
- Example: `commit.retract { commitment_id: "customer_full_scope_friday", reason: "not credible" }`
- Typical emitters: commitment owner, TPM, sponsor
- Commitment effects: move to `superseded`
- Belief effects: yes
- Relationship effects: often negative short-term, positive long-term if honest
- Valid surfaces: `chat`, `meeting`

### approve.grant

- Semantics: Grant approval or authorize a requested decision.
- Required slots: one of `decision_key`, `task_id`, `milestone_id`
- Optional slots: `scope_variant`, `commitment_id`
- Example: `approve.grant { decision_key: "launch_scope", scope_variant: "descoped_pilot" }`
- Typical emitters: sponsor, dependency owner, approver
- Commitment effects: may confirm commitments
- Belief effects: yes
- Relationship effects: positive
- Valid surfaces: `chat`, `meeting`

### approve.deny

- Semantics: Deny approval or decline to authorize.
- Required slots: one of `decision_key`, `task_id`, `milestone_id`
- Optional slots: `reason`
- Example: `approve.deny { task_id: "design_signoff", reason: "missing support context" }`
- Typical emitters: approvers
- Commitment effects: may block or retract commitments
- Belief effects: yes
- Relationship effects: neutral
- Valid surfaces: `chat`, `meeting`

### approve.defer

- Semantics: Delay approval pending more information or time.
- Required slots: one of `decision_key`, `task_id`, `milestone_id`
- Optional slots: `reason`, `available_after`
- Example: `approve.defer { task_id: "security_review", available_after: "2026-05-06T10:00:00" }`
- Typical emitters: approvers
- Commitment effects: may keep related commitments tentative
- Belief effects: yes
- Relationship effects: neutral
- Valid surfaces: `chat`, `meeting`

### escalate.to_sponsor

- Semantics: Escalate a blocker or decision to the sponsor.
- Required slots: `topic`
- Optional slots: `task_id`, `milestone_id`, `reason`
- Example: `escalate.to_sponsor { topic: "scope decision", milestone_id: "scope_aligned" }`
- Typical emitters: TPM
- Commitment effects: none directly
- Belief effects: yes for sponsor
- Relationship effects: may reduce trust with the escalated actor
- Valid surfaces: `chat`, `meeting`

### escalate.to_manager

- Semantics: Escalate a blocker or ownership issue to a line manager.
- Required slots: `topic`
- Optional slots: `task_id`, `actor_id`, `reason`
- Example: `escalate.to_manager { actor_id: "maya", topic: "backend ownership" }`
- Typical emitters: TPM
- Commitment effects: none directly
- Belief effects: yes for manager
- Relationship effects: may reduce trust with the escalated actor
- Valid surfaces: `chat`, `meeting`

### ack.received

- Semantics: Acknowledge receipt without changing the underlying plan.
- Required slots: none
- Optional slots: `topic`
- Example: `ack.received {}`
- Typical emitters: any actor
- Commitment effects: none
- Belief effects: weak
- Relationship effects: mildly positive
- Valid surfaces: `chat`, `meeting`

### ack.deferred

- Semantics: Acknowledge the ask but defer a substantive response.
- Required slots: `reason`
- Optional slots: `available_after`
- Example: `ack.deferred { reason: "oncall", available_after: "2026-05-04T15:00:00" }`
- Typical emitters: any actor
- Commitment effects: none
- Belief effects: weak
- Relationship effects: neutral
- Valid surfaces: `chat`, `meeting`

### meeting.propose

- Semantics: Propose a meeting for a specific goal and attendee set.
- Required slots: `meeting_id`
- Optional slots: `goal`, `duration_minutes`, `artifact_id`
- Example: `meeting.propose { meeting_id: "launch_tradeoff_huddle", goal: "scope_alignment" }`
- Typical emitters: TPM, sponsor
- Commitment effects: none directly
- Belief effects: none directly
- Relationship effects: neutral
- Valid surfaces: `calendar`

### meeting.accept

- Semantics: Accept a proposed meeting.
- Required slots: `meeting_id`
- Optional slots: none
- Example: `meeting.accept { meeting_id: "launch_tradeoff_huddle" }`
- Typical emitters: any actor
- Commitment effects: none
- Belief effects: none
- Relationship effects: neutral
- Valid surfaces: `calendar`

### meeting.decline

- Semantics: Decline a proposed meeting.
- Required slots: `meeting_id`
- Optional slots: `reason`
- Example: `meeting.decline { meeting_id: "launch_tradeoff_huddle", reason: "conflict" }`
- Typical emitters: any actor
- Commitment effects: none
- Belief effects: none
- Relationship effects: neutral or negative
- Valid surfaces: `calendar`

### meeting.reschedule

- Semantics: Decline the current slot and suggest a future one.
- Required slots: `meeting_id`
- Optional slots: `proposed_start_at`, `reason`
- Example: `meeting.reschedule { meeting_id: "launch_tradeoff_huddle", proposed_start_at: "2026-05-05T11:00:00" }`
- Typical emitters: any actor
- Commitment effects: none
- Belief effects: none
- Relationship effects: neutral
- Valid surfaces: `calendar`
