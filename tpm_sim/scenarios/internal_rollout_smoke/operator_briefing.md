Scenario Briefing: Internal Rollout Smoke (internal_rollout_smoke)
Project: Internal Config Rollout
Premise: A lightweight internal rollout scenario focused on approval timing, staged-scope alignment, and a small non-gating runbook distraction.
Project Detail: A lightweight internal rollout focused on approval timing and staged-scope alignment.
Window: Tue 2026-05-05 09:00 -> Wed 2026-05-06 17:00

How To Win / How To Fail:
- Win: Move the critical path early: config_rollout, approval_review.
- Win: Protect the milestone windows, starting with approval_secured by Tue 2026-05-05 12:00 and ending with rollout_ready by Wed 2026-05-06 15:00.
- Win: Scoring leans on milestones (70), commitment quality (10), discovery (10), relationship (5).
- Win: Good TPM behavior here means moving before windows close; finding the real blockers and stakeholder drivers early; making grounded commitments instead of fake green.
- Fail: This scenario punishes timing, discovery, commitment failures.
- Fail: Key hidden pressure: Full rollout is not credible in the current window.; The rollout needs approval before the cutoff.
- Fail: Actor traps: Dana will back the staged rollout if the TPM brings the real tradeoff early instead of defending fake full scope.; Leo is skeptical of rollout-date pressure until scope and approval are real.

Cast:
- Dana Brooks (dana) — director product / sponsor
  Visible goal: Wants a stable internal rollout plan and will support staged rollout if told early.
  Hidden driver: Dana will back the staged rollout if the TPM brings the real tradeoff early instead of defending fake full scope.
  Decision rights: approve scope
- Leo Park (leo) — engineer / critical path owner
  Visible goal: Overloaded engineer who knows full rollout is not credible.
  Hidden driver: Leo is skeptical of rollout-date pressure until scope and approval are real.
  Decision rights: commit ETA, update tracker
- Ivy Shah (ivy) — security engineer / cross functional dependency owner
  Visible goal: Approval owner with a hard queue cutoff.
  Decision rights: grant review, update tracker
- Mia Torres (mia) — operations / ally
  Visible goal: Operations ally who can help tidy the runbook quickly.

Hidden Landscape:
- Full rollout is not credible in the current window.
- The rollout needs approval before the cutoff.

Critical Path And Deadlines:
- Critical path: config_rollout, approval_review
- approval_secured by Tue 2026-05-05 12:00 — Approval is secured before the queue cutoff.
- scope_aligned by Tue 2026-05-05 15:00 — A staged rollout plan is aligned.
- rollout_ready by Wed 2026-05-06 15:00 — The staged rollout is ready.