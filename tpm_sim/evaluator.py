from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from tpm_sim.common import from_iso


class Evaluator:
    def __init__(self, engine):
        self.engine = engine
        self.store = engine.store
        self.scenario = engine.scenario
        self.config = engine.evaluation

    def evaluate(self) -> dict[str, Any]:
        rubric_results = [self._score_rubric_line(line) for line in self.config.get("rubric_lines", [])]
        total_score = round(sum(item["awarded"] for item in rubric_results), 2)
        failure_breakdown = self._failure_breakdown(rubric_results)
        decisive_moments = self._decisive_moments()
        recoverability = self._recoverability_summary()
        return {
            "scenario_id": self.scenario["id"],
            "scenario_digest": self.engine.scenario_digest(),
            "compiled_coverage_digest": self.store.get_meta("compiled_coverage_digest", self.engine.scenario_digest()),
            "closure_status": self.engine.deserialize(self.store.get_meta("closure_status_json"), {"status": "unknown", "passed": False}),
            "time": self.store.get_meta("current_time"),
            "total_score": total_score,
            "rubric": rubric_results,
            "failure_breakdown": failure_breakdown,
            "decisive_moments": decisive_moments,
            "recoverability": recoverability,
            "coverage_miss": bool(self.engine.project_state().get("coverage_miss")),
        }

    def export_report(self, output_prefix: str | None = None) -> dict[str, Any]:
        report = self.evaluate()
        prefix = Path(output_prefix or self.store.path)
        report_path = prefix.with_suffix(".report.json")
        agent_trace_path = prefix.with_suffix(".agent_trace.jsonl")
        omniscient_trace_path = prefix.with_suffix(".omniscient_trace.jsonl")

        agent_trace = [self._row_to_trace_dict(row) for row in self.store.event_log("agent")]
        omniscient_trace = [self._row_to_trace_dict(row) for row in self.store.event_log()]

        report["trace_paths"] = {
            "agent_trace": str(agent_trace_path),
            "omniscient_trace": str(omniscient_trace_path),
        }

        report_path.write_text(json.dumps(report, indent=2, sort_keys=True))
        with agent_trace_path.open("w", encoding="utf-8") as handle:
            for row in agent_trace:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
        with omniscient_trace_path.open("w", encoding="utf-8") as handle:
            for row in omniscient_trace:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
        return {
            "report_path": str(report_path),
            "agent_trace_path": str(agent_trace_path),
            "omniscient_trace_path": str(omniscient_trace_path),
            "summary": self.render_human_summary(report),
            "report": report,
        }

    def render_human_summary(self, report: dict[str, Any]) -> str:
        lines = [
            f"Scenario: {report['scenario_id']}",
            f"Digest: {report['scenario_digest']}",
            f"Total score: {report['total_score']} / 100",
            "",
            "Failure breakdown:",
        ]
        for key, value in report["failure_breakdown"].items():
            lines.append(f"- {key}: {value}")
        if report["decisive_moments"]:
            lines.extend(["", "Decisive moments:"])
            for item in report["decisive_moments"][:5]:
                lines.append(f"- {item['at']}: {item['summary']}")
        return "\n".join(lines)

    def _score_rubric_line(self, line: dict[str, Any]) -> dict[str, Any]:
        scoring_type = line["scoring_type"]
        evidence_refs: list[str] = []
        matched_predicates: list[str] = []
        awarded = 0.0

        if scoring_type == "binary":
            result = self.engine.predicate.evaluate(line.get("success_predicate"), now=self.engine.now())
            if result.matched and self._evidence_valid(line, result.evidence_refs):
                awarded = float(line["weight"])
                evidence_refs = result.evidence_refs
                matched_predicates = result.matched_predicates
        elif scoring_type == "count_fraction":
            partials = line.get("partial_credit_predicates", [])
            matched_results = [
                self.engine.predicate.evaluate(predicate, now=self.engine.now())
                for predicate in partials
            ]
            matched = [result for result in matched_results if result.matched]
            if partials and self._evidence_valid(line, [ref for result in matched for ref in result.evidence_refs]):
                awarded = float(line["weight"]) * (len(matched) / len(partials))
                evidence_refs = [ref for result in matched for ref in result.evidence_refs]
                matched_predicates = [name for result in matched for name in result.matched_predicates]
        elif scoring_type == "thresholded":
            best = 0.0
            best_result = None
            for candidate in line.get("partial_credit_predicates", []):
                result = self.engine.predicate.evaluate(candidate["predicate"], now=self.engine.now())
                if result.matched and self._evidence_valid(line, result.evidence_refs) and float(candidate["score"]) >= best:
                    best = float(candidate["score"])
                    best_result = result
            awarded = min(float(line["weight"]), best)
            if best_result is not None:
                evidence_refs = best_result.evidence_refs
                matched_predicates = best_result.matched_predicates
        elif scoring_type == "bounded_penalty":
            awarded = float(line["weight"])
            for candidate in line.get("failure_predicates", []):
                result = self.engine.predicate.evaluate(candidate["predicate"], now=self.engine.now())
                if result.matched:
                    awarded -= float(candidate["penalty"])
                    evidence_refs.extend(result.evidence_refs)
                    matched_predicates.extend(result.matched_predicates)
            awarded = max(0.0, min(float(line["weight"]), awarded))
            if awarded == float(line["weight"]):
                evidence_refs = ["state:clean"]
        else:
            raise ValueError(f"Unsupported scoring type '{scoring_type}'.")

        awarded = round(awarded, 2)
        return {
            "id": line["id"],
            "label": line["label"],
            "weight": float(line["weight"]),
            "awarded": awarded,
            "failure_class": line["failure_class"],
            "competency_tags": line.get("competency_tags", []),
            "measurement_rationale": line.get("measurement_rationale", ""),
            "success_meaning": line.get("success_meaning", ""),
            "failure_meaning": line.get("failure_meaning", ""),
            "evidence_refs": sorted(set(evidence_refs)),
            "matched_predicates": matched_predicates,
            "deadline_or_window": line.get("deadline_or_window"),
            "explanation": line.get("explanation", ""),
        }

    def _evidence_valid(self, line: dict[str, Any], evidence_refs: list[str]) -> bool:
        requirements = line.get("evidence_requirements", {})
        if not evidence_refs:
            return False
        if requirements.get("min_refs", 0) > len(set(evidence_refs)):
            return False
        if requirements.get("require_event_ref") and not any(ref.startswith("event:") for ref in evidence_refs):
            return False
        if requirements.get("require_state_transition_ref") and not any(ref.startswith("event:") or ref.startswith("state:") for ref in evidence_refs):
            return False
        return True

    def _failure_breakdown(self, rubric_results: list[dict[str, Any]]) -> dict[str, float]:
        failures: dict[str, float] = {}
        for item in rubric_results:
            failures.setdefault(item["failure_class"], 0.0)
            failures[item["failure_class"]] += round(float(item["weight"]) - float(item["awarded"]), 2)
        return failures

    def _decisive_moments(self) -> list[dict[str, Any]]:
        rows = []
        for row in self.store.event_log():
            if row["event_type"] in {
                "agenda_signal.observed",
                "coverage.miss",
                "milestone.updated",
                "commitment.updated",
                "meeting.completed",
                "npc.message_sent",
                "task.transitioned",
            }:
                rows.append(
                    {
                        "id": row["id"],
                        "at": row["at"],
                        "event_type": row["event_type"],
                        "summary": row["summary"],
                        "payload": self.engine.deserialize(row["payload_json"], {}),
                    }
                )
        return rows[-20:]

    def _recoverability_summary(self) -> dict[str, str]:
        output: dict[str, str] = {}
        for row in self.store.milestones():
            state = self.engine.deserialize(row["state_json"], {})
            output[row["id"]] = state.get("recoverability", "unknown")
        return output

    def _row_to_trace_dict(self, row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "at": row["at"],
            "phase": row["phase"],
            "event_type": row["event_type"],
            "actor_id": row["actor_id"],
            "visibility": row["visibility"],
            "summary": row["summary"],
            "payload": self.engine.deserialize(row["payload_json"], {}),
        }


def summarize_score_band(scores: list[float]) -> dict[str, float]:
    if not scores:
        return {"mean": 0.0, "stdev": 0.0, "worst": 0.0, "best": 0.0}
    return {
        "mean": round(mean(scores), 2),
        "stdev": round(pstdev(scores), 3) if len(scores) > 1 else 0.0,
        "worst": round(min(scores), 2),
        "best": round(max(scores), 2),
    }
