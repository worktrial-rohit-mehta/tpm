from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from tpm_sim.common import from_iso


@dataclass
class PredicateResult:
    matched: bool
    evidence_refs: list[str]
    matched_predicates: list[str]

    @classmethod
    def no(cls) -> "PredicateResult":
        return cls(False, [], [])


def _merge_results(results: list[PredicateResult], matched: bool, label: str) -> PredicateResult:
    evidence: list[str] = []
    predicates: list[str] = [label]
    for result in results:
        evidence.extend(result.evidence_refs)
        predicates.extend(result.matched_predicates)
    return PredicateResult(matched, evidence, predicates)


def _get_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _compare(actual: Any, expectation: Any) -> bool:
    if isinstance(expectation, dict):
        if "equals" in expectation:
            return actual == expectation["equals"]
        if "in" in expectation:
            return actual in expectation["in"]
        if "gte" in expectation:
            return actual is not None and actual >= expectation["gte"]
        if "lte" in expectation:
            return actual is not None and actual <= expectation["lte"]
        if "gt" in expectation:
            return actual is not None and actual > expectation["gt"]
        if "lt" in expectation:
            return actual is not None and actual < expectation["lt"]
    return actual == expectation


class PredicateEvaluator:
    def __init__(self, engine: Any):
        self.engine = engine

    def evaluate(
        self,
        predicate: Optional[dict[str, Any]],
        *,
        now: Optional[datetime] = None,
        time_window: Optional[tuple[Optional[datetime], Optional[datetime]]] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> PredicateResult:
        if predicate in (None, {}):
            return PredicateResult(True, [], ["literal.true"])
        if now is None:
            now = self.engine.now()
        if "all_of" in predicate:
            results = [self.evaluate(item, now=now, time_window=time_window, context=context) for item in predicate["all_of"]]
            matched = all(result.matched for result in results)
            return _merge_results(results, matched, "all_of")
        if "any_of" in predicate:
            results = [self.evaluate(item, now=now, time_window=time_window, context=context) for item in predicate["any_of"]]
            matched = any(result.matched for result in results)
            winning = [result for result in results if result.matched] or results
            return _merge_results(winning, matched, "any_of")
        if "not" in predicate:
            result = self.evaluate(predicate["not"], now=now, time_window=time_window, context=context)
            return PredicateResult(not result.matched, result.evidence_refs, ["not", *result.matched_predicates])
        if "before" in predicate:
            payload = predicate["before"]
            return self.evaluate(
                payload["predicate"],
                now=now,
                time_window=(None, from_iso(payload["time"])),
                context=context,
            )
        if "after" in predicate:
            payload = predicate["after"]
            return self.evaluate(
                payload["predicate"],
                now=now,
                time_window=(from_iso(payload["time"]), None),
                context=context,
            )
        if "within" in predicate:
            payload = predicate["within"]
            return self.evaluate(
                payload["predicate"],
                now=now,
                time_window=(from_iso(payload["start"]), from_iso(payload["end"])),
                context=context,
            )
        if "eventually_before" in predicate:
            payload = predicate["eventually_before"]
            return self.evaluate(
                payload["predicate"],
                now=now,
                time_window=(None, from_iso(payload["time"])),
                context=context,
            )
        if "count_at_least" in predicate:
            payload = predicate["count_at_least"]
            results = [self.evaluate(item, now=now, time_window=time_window, context=context) for item in payload["predicates"]]
            matched_results = [result for result in results if result.matched]
            return _merge_results(matched_results, len(matched_results) >= int(payload["count"]), "count_at_least")
        if "surfaced" in predicate:
            fact_id = predicate["surfaced"]
            try:
                fact = self.engine.fact_state(fact_id)
            except KeyError:
                return PredicateResult.no()
            surfaced_at = fact.get("surfaced_at")
            if surfaced_at is None or not self._time_allowed(from_iso(surfaced_at), time_window):
                return PredicateResult.no()
            evidence = []
            if fact.get("surface_event_ref"):
                evidence.append(fact["surface_event_ref"])
            return PredicateResult(True, evidence, [f"surfaced:{fact_id}"])
        if "fact_state" in predicate:
            payload = predicate["fact_state"]
            try:
                fact = self.engine.fact_state(payload["fact_id"])
            except KeyError:
                return PredicateResult.no()
            value = fact.get(payload["field"])
            matched = self._match_value(value, payload)
            evidence = [fact["surface_event_ref"]] if matched and fact.get("surface_event_ref") else []
            return PredicateResult(matched, evidence, [f"fact_state:{payload['fact_id']}"])
        if "milestone_state" in predicate:
            payload = predicate["milestone_state"]
            try:
                state = self.engine.milestone_state(payload["milestone_id"])
            except KeyError:
                return PredicateResult.no()
            value = state.get(payload["field"])
            if payload["field"] == "status" and value == "done":
                achieved_at = state.get("achieved_at")
                if achieved_at and not self._time_allowed(from_iso(achieved_at), time_window):
                    return PredicateResult.no()
            matched = self._match_value(value, payload)
            evidence = []
            if matched and state.get("last_transition_ref"):
                evidence.append(state["last_transition_ref"])
            return PredicateResult(matched, evidence, [f"milestone_state:{payload['milestone_id']}"])
        if "task_true_state" in predicate:
            payload = predicate["task_true_state"]
            try:
                state = self.engine.task_true_state(payload["task_id"])
            except KeyError:
                return PredicateResult.no()
            value = state.get(payload["field"])
            matched = self._match_value(value, payload)
            evidence = []
            if matched and state.get("last_transition_ref"):
                evidence.append(state["last_transition_ref"])
            return PredicateResult(matched, evidence, [f"task_true_state:{payload['task_id']}"])
        if "task_tracker_state" in predicate:
            payload = predicate["task_tracker_state"]
            try:
                state = self.engine.task_tracker_state(payload["task_id"])
            except KeyError:
                return PredicateResult.no()
            value = state.get(payload["field"])
            matched = self._match_value(value, payload)
            evidence = []
            if matched and state.get("last_update_ref"):
                evidence.append(state["last_update_ref"])
            return PredicateResult(matched, evidence, [f"task_tracker_state:{payload['task_id']}"])
        if "project_state" in predicate:
            payload = predicate["project_state"]
            state = self.engine.project_state()
            value = state.get(payload["field"])
            matched = self._match_value(value, payload)
            evidence = []
            if matched:
                evidence.append("state:project")
            return PredicateResult(matched, evidence, [f"project_state:{payload['field']}"])
        if "relationship_state" in predicate:
            payload = predicate["relationship_state"]
            try:
                state = self.engine.relationship_state(payload["actor_id"], payload["target_actor_id"])
            except KeyError:
                return PredicateResult.no()
            value = state.get(payload["field"])
            matched = self._match_value(value, payload)
            evidence = ["state:relationship"] if matched else []
            return PredicateResult(matched, evidence, [f"relationship_state:{payload['actor_id']}->{payload['target_actor_id']}"])
        if "commitment_state" in predicate:
            payload = predicate["commitment_state"]
            try:
                state = self.engine.commitment_state(payload["commitment_id"])
            except KeyError:
                state = None
            if state is None:
                if payload["field"] == "status" and payload.get("equals") == "missing":
                    return PredicateResult(True, [f"commitment:{payload['commitment_id']}:missing"], [f"commitment_state:{payload['commitment_id']}"])
                return PredicateResult.no()
            value = state.get(payload["field"])
            if payload["field"] == "status" and state.get("last_updated_at"):
                if not self._time_allowed(from_iso(state["last_updated_at"]), time_window):
                    return PredicateResult.no()
            matched = self._match_value(value, payload)
            evidence = []
            if matched and state.get("last_transition_ref"):
                evidence.append(state["last_transition_ref"])
            return PredicateResult(matched, evidence, [f"commitment_state:{payload['commitment_id']}"])
        if "belief_known" in predicate:
            payload = predicate["belief_known"]
            belief = self.engine.latest_belief(payload["actor_id"], payload["belief_key"])
            if belief is None:
                return PredicateResult.no()
            belief_time = from_iso(belief["updated_at"])
            if not self._time_allowed(belief_time, time_window):
                return PredicateResult.no()
            value = self.engine.deserialize(belief["belief_value_json"])
            if "fresh_within_min" in payload:
                age_minutes = (now - belief_time).total_seconds() / 60.0
                if age_minutes > float(payload["fresh_within_min"]):
                    return PredicateResult.no()
            if "min_confidence" in payload and float(belief["confidence"]) < float(payload["min_confidence"]):
                return PredicateResult.no()
            matched = self._match_value(value, payload)
            return PredicateResult(matched, [f"belief:{belief['id']}"] if matched else [], [f"belief_known:{payload['belief_key']}"])
        if "critical_window_open" in predicate:
            window_id = predicate["critical_window_open"]
            try:
                window = self.engine.window(window_id)
            except KeyError:
                return PredicateResult.no()
            matched = from_iso(window["start_at"]) <= now <= from_iso(window["end_at"])
            return PredicateResult(matched, [f"window:{window_id}"] if matched else [], [f"critical_window_open:{window_id}"])
        if "window_state" in predicate:
            payload = predicate["window_state"]
            try:
                window = self.engine.window(payload["window_id"])
            except KeyError:
                return PredicateResult.no()
            field = payload["field"]
            if field == "closed":
                value = now > from_iso(window["end_at"])
            elif field == "open":
                value = from_iso(window["start_at"]) <= now <= from_iso(window["end_at"])
            else:
                value = self.engine.deserialize(window["metadata_json"]).get(field)
            matched = self._match_value(value, payload)
            return PredicateResult(matched, [f"window:{payload['window_id']}"] if matched else [], [f"window_state:{payload['window_id']}"])
        if "action_occurred" in predicate:
            payload = predicate["action_occurred"]
            for row in self.engine.store.actions():
                at = from_iso(row["at"])
                if not self._time_allowed(at, time_window):
                    continue
                if payload.get("actor_id") and row["actor_id"] != payload["actor_id"]:
                    continue
                if payload.get("surface") and row["surface"] != payload["surface"]:
                    continue
                if payload.get("act_id") and row["act_id"] != payload["act_id"]:
                    continue
                slots = self.engine.deserialize(row["slots_json"])
                if not self._where_matches(payload.get("slots", {}), slots):
                    continue
                return PredicateResult(True, [f"action:{row['id']}"], [f"action_occurred:{row['id']}"])
            return PredicateResult.no()
        if "event_occurred" in predicate:
            payload = predicate["event_occurred"]
            for row in self.engine.store.event_log():
                at = from_iso(row["at"])
                if not self._time_allowed(at, time_window):
                    continue
                if payload.get("event_type") and row["event_type"] != payload["event_type"]:
                    continue
                if payload.get("actor_id") and row["actor_id"] != payload["actor_id"]:
                    continue
                merged = {
                    "phase": row["phase"],
                    "event_type": row["event_type"],
                    "actor_id": row["actor_id"],
                    "visibility": row["visibility"],
                    **self.engine.deserialize(row["payload_json"]),
                }
                if not self._where_matches(payload.get("where", {}), merged):
                    continue
                return PredicateResult(True, [f"event:{row['id']}"], [f"event_occurred:{row['event_type']}"])
            return PredicateResult.no()
        if "productive_meeting" in predicate:
            meeting_id = predicate["productive_meeting"]
            try:
                meeting = self.engine.meeting_state(meeting_id)
            except KeyError:
                return PredicateResult.no()
            metadata = self.engine.deserialize(meeting["metadata_json"])
            completed_at = metadata.get("completed_at")
            if completed_at and not self._time_allowed(from_iso(completed_at), time_window):
                return PredicateResult.no()
            outcomes = metadata.get("productive_outcome_ids", [])
            matched = bool(outcomes)
            evidence = [meeting.get("transcript_doc_id") and f"doc:{meeting['transcript_doc_id']}"] if matched else []
            evidence = [item for item in evidence if item]
            return PredicateResult(matched, evidence, [f"productive_meeting:{meeting_id}"])
        if "context_field" in predicate:
            payload = predicate["context_field"]
            if context is None:
                return PredicateResult.no()
            value = context.get(payload["field"])
            matched = self._match_value(value, payload)
            return PredicateResult(matched, ["context"] if matched else [], [f"context_field:{payload['field']}"])
        raise ValueError(f"Unsupported predicate: {predicate}")

    def _match_value(self, value: Any, payload: dict[str, Any]) -> bool:
        if "equals" in payload:
            return value == payload["equals"]
        if "in" in payload:
            return value in payload["in"]
        if "gte" in payload:
            return value is not None and value >= payload["gte"]
        if "lte" in payload:
            return value is not None and value <= payload["lte"]
        if "gt" in payload:
            return value is not None and value > payload["gt"]
        if "lt" in payload:
            return value is not None and value < payload["lt"]
        return bool(value)

    def _time_allowed(
        self,
        at: datetime,
        time_window: Optional[tuple[Optional[datetime], Optional[datetime]]],
    ) -> bool:
        if time_window is None:
            return True
        start, end = time_window
        if start is not None and at < start:
            return False
        if end is not None and at > end:
            return False
        return True

    def _where_matches(self, where: dict[str, Any], candidate: dict[str, Any]) -> bool:
        for key, expected in where.items():
            actual = _get_path(candidate, key) if "." in key else candidate.get(key)
            if not _compare(actual, expected):
                return False
        return True
