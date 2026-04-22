from __future__ import annotations

from datetime import datetime
import tempfile
import unittest
from pathlib import Path

from tpm_sim.cli import execute_command, execute_script
from tpm_sim.engine import SimulationEngine
from tpm_sim.evaluator import Evaluator, summarize_score_band
from tpm_sim.scenario import load_bundle_from_store, load_scenario_bundle, seed_store
from tpm_sim.storage import open_store


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def build_runtime(
    db_path: str,
    scenario_id: str = "northstar_launch_week",
    *,
    seed: int = 11,
    coverage_enforcement: str = "strict",
) -> tuple[SimulationEngine, Evaluator]:
    bundle = load_scenario_bundle(scenario_id)
    store = open_store(db_path)
    seed_store(store, bundle, seed, coverage_enforcement=coverage_enforcement)
    engine = SimulationEngine(store, bundle)
    evaluator = Evaluator(engine)
    return engine, evaluator


def open_existing_runtime(db_path: str) -> tuple[SimulationEngine, Evaluator]:
    store = open_store(db_path)
    bundle = load_bundle_from_store(store)
    engine = SimulationEngine(store, bundle)
    evaluator = Evaluator(engine)
    return engine, evaluator


def run_script(engine: SimulationEngine, evaluator: Evaluator, script_name: str) -> dict[str, object]:
    execute_script(engine, evaluator, EXAMPLES / script_name, echo=False, emit=False)
    return evaluator.evaluate()


class SimulationTests(unittest.TestCase):
    def test_tpm_time_spend_stays_within_work_hours(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, evaluator = build_runtime(
                str(Path(tmpdir) / "workhours.sqlite"),
                "internal_rollout_smoke",
                seed=11,
            )
            try:
                engine.store.set_current_time(datetime.fromisoformat("2026-05-05T16:50:00"))
                engine._spend_time(30, "unit_test")
                self.assertEqual(engine.now().strftime("%Y-%m-%dT%H:%M:%S"), "2026-05-06T09:20:00")
            finally:
                engine.store.close()

    def test_wait_until_next_event_advances_to_first_due_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, evaluator = build_runtime(str(Path(tmpdir) / "event.sqlite"))
            try:
                execute_command(engine, evaluator, "chat send maya | request.feasibility | task_id=backend_api | Need the honest path.")
                execute_command(engine, evaluator, "wait next 600m")
                self.assertEqual(engine.now().strftime("%Y-%m-%dT%H:%M:%S"), "2026-05-04T15:00:00")
                inbox = engine.render_inbox()
                self.assertIn("maya", inbox.lower())
            finally:
                engine.store.close()

    def test_coverage_report_is_complete_for_authored_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, evaluator = build_runtime(str(Path(tmpdir) / "coverage.sqlite"), coverage_enforcement="permissive")
            try:
                coverage = engine.coverage_report()
                self.assertEqual(coverage["critical_uncovered"], 0)
                self.assertEqual(coverage["coverage"], 1.0)
            finally:
                engine.store.close()

    def test_coverage_report_is_complete_for_internal_rollout_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, evaluator = build_runtime(
                str(Path(tmpdir) / "coverage_smoke.sqlite"),
                "internal_rollout_smoke",
                coverage_enforcement="permissive",
            )
            try:
                coverage = engine.coverage_report()
                self.assertEqual(coverage["critical_uncovered"], 0)
                self.assertEqual(coverage["coverage"], 1.0)
            finally:
                engine.store.close()

    def test_same_value_belief_signals_accumulate_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, evaluator = build_runtime(str(Path(tmpdir) / "accumulate.sqlite"))
            try:
                engine._apply_observation_signals(
                    "tpm",
                    "doc:synthetic-a",
                    {
                        "belief_signals": [
                            {
                                "belief_key": "synthetic.driver",
                                "belief_value": True,
                                "confidence": 0.4,
                                "freshness_window_min": 240,
                                "accumulate": True,
                            }
                        ]
                    },
                )
                engine._apply_observation_signals(
                    "tpm",
                    "doc:synthetic-b",
                    {
                        "belief_signals": [
                            {
                                "belief_key": "synthetic.driver",
                                "belief_value": True,
                                "confidence": 0.5,
                                "freshness_window_min": 240,
                                "accumulate": True,
                            }
                        ]
                    },
                )
                belief = engine.latest_belief("tpm", "synthetic.driver")
                self.assertIsNotNone(belief)
                self.assertAlmostEqual(float(belief["confidence"]), 0.7, places=3)
            finally:
                engine.store.close()

    def test_conflicting_belief_signals_do_not_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, evaluator = build_runtime(str(Path(tmpdir) / "conflict.sqlite"))
            try:
                engine._apply_observation_signals(
                    "tpm",
                    "doc:synthetic-a",
                    {
                        "belief_signals": [
                            {
                                "belief_key": "synthetic.driver",
                                "belief_value": True,
                                "confidence": 0.4,
                                "freshness_window_min": 240,
                                "accumulate": True,
                            }
                        ]
                    },
                )
                engine._apply_observation_signals(
                    "tpm",
                    "doc:synthetic-b",
                    {
                        "belief_signals": [
                            {
                                "belief_key": "synthetic.driver",
                                "belief_value": False,
                                "confidence": 0.5,
                                "freshness_window_min": 240,
                                "accumulate": True,
                            }
                        ]
                    },
                )
                belief = engine.latest_belief("tpm", "synthetic.driver")
                self.assertIsNotNone(belief)
                self.assertEqual(engine.deserialize(belief["belief_value_json"]), False)
                self.assertAlmostEqual(float(belief["confidence"]), 0.5, places=3)
                all_rows = [row for row in engine.store.beliefs_for_actor("tpm") if row["belief_key"] == "synthetic.driver"]
                self.assertEqual(len(all_rows), 2)
            finally:
                engine.store.close()

    def test_belief_known_uses_underlying_source_ref_for_private_driver_cues(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, evaluator = build_runtime(str(Path(tmpdir) / "belief_known.sqlite"))
            try:
                engine._apply_observation_signals(
                    "tpm",
                    "doc:synthetic",
                    {
                        "belief_signals": [
                            {
                                "belief_key": "actor.rohit.private_driver.supports_descoping_if_engaged_early",
                                "belief_value": True,
                                "confidence": 0.8,
                                "freshness_window_min": 240,
                                "accumulate": True,
                                "metadata": {
                                    "private_driver_fact_id": "rohit_accepts_descoping_if_early",
                                },
                            }
                        ]
                    },
                )
                result = engine.predicate.evaluate(
                    {
                        "belief_known": {
                            "actor_id": "tpm",
                            "belief_key": "actor.rohit.private_driver.supports_descoping_if_engaged_early",
                            "equals": True,
                            "min_confidence": 0.75,
                        }
                    }
                )
                self.assertTrue(result.matched)
                self.assertGreaterEqual(len(result.evidence_refs), 2)
                self.assertTrue(result.evidence_refs[0].startswith("event:"))
                self.assertTrue(result.evidence_refs[1].startswith("belief:"))
            finally:
                engine.store.close()

    def test_checkpoint_and_fork_preserve_run_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "checkpoint.sqlite")
            engine, evaluator = build_runtime(db_path)
            expected_now = None
            try:
                execute_command(engine, evaluator, "docs open DOC-MAYA-LOAD-001")
                execute_command(engine, evaluator, "chat send maya | request.feasibility | task_id=backend_api | Need the honest path.")
                checkpoint_path = execute_command(engine, evaluator, "checkpoint day1")
                self.assertIn("Checkpoint written", checkpoint_path)
                fork_db = str(Path(tmpdir) / "forked.sqlite")
                execute_command(engine, evaluator, f"fork day1 | {fork_db}")
                expected_now = engine.now()
            finally:
                engine.store.close()

            fork_store = open_store(fork_db)
            try:
                bundle = load_bundle_from_store(fork_store)
                fork_engine = SimulationEngine(fork_store, bundle)
                fork_evaluator = Evaluator(fork_engine)
                self.assertEqual(fork_engine.scenario_digest(), load_scenario_bundle("northstar_launch_week")["scenario_digest"])
                self.assertEqual(fork_engine.now(), expected_now)
                self.assertEqual(fork_evaluator.evaluate()["scenario_digest"], fork_engine.scenario_digest())
            finally:
                fork_store.close()

    def test_deterministic_replay_same_seed_same_script_same_score(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            first_db = str(Path(tmpdir) / "first.sqlite")
            second_db = str(Path(tmpdir) / "second.sqlite")
            first_engine, first_evaluator = build_runtime(first_db, seed=11)
            second_engine, second_evaluator = build_runtime(second_db, seed=11)
            try:
                first_report = run_script(first_engine, first_evaluator, "golden.tpm")
                second_report = run_script(second_engine, second_evaluator, "golden.tpm")
                self.assertEqual(first_report["total_score"], second_report["total_score"])
                self.assertEqual(first_report["scenario_digest"], second_report["scenario_digest"])
                self.assertEqual(
                    [row["event_type"] for row in first_engine.store.event_log()],
                    [row["event_type"] for row in second_engine.store.event_log()],
                )
                self.assertEqual(
                    [row["act_id"] for row in first_engine.store.actions()],
                    [row["act_id"] for row in second_engine.store.actions()],
                )
            finally:
                first_engine.store.close()
                second_engine.store.close()

    def test_replay_persists_across_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "persisted.sqlite")
            engine, evaluator = build_runtime(db_path, seed=11)
            try:
                first_report = run_script(engine, evaluator, "golden.tpm")
                self.assertAlmostEqual(first_report["total_score"], 81.67, places=2)
            finally:
                engine.store.close()

            reopened_engine, reopened_evaluator = open_existing_runtime(db_path)
            try:
                reopened_report = reopened_evaluator.evaluate()
                self.assertAlmostEqual(reopened_report["total_score"], 81.67, places=2)
                self.assertEqual(reopened_report["scenario_digest"], first_report["scenario_digest"])
            finally:
                reopened_engine.store.close()

    def test_golden_path_awards_both_new_discovery_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine, evaluator = build_runtime(str(Path(tmpdir) / "golden_discovery.sqlite"), seed=11)
            try:
                report = run_script(engine, evaluator, "golden.tpm")
                awarded = {line["id"]: float(line["awarded"]) for line in report["rubric"]}
                self.assertEqual(awarded["project_constraint_discovery"], 10.0)
                self.assertAlmostEqual(awarded["stakeholder_driver_discovery"], 6.67, places=2)
            finally:
                engine.store.close()

    def test_reference_trajectories_separate_cleanly(self) -> None:
        trajectories = {
            "golden": "golden.tpm",
            "competent_but_imperfect": "competent_but_imperfect.tpm",
            "busywork": "busywork.tpm",
            "false_green": "false_green.tpm",
            "spray_and_pray": "spray_and_pray.tpm",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            score_bands: dict[str, dict[str, float]] = {}
            for name, script_name in trajectories.items():
                scores: list[float] = []
                for seed in (11, 29, 47):
                    db_path = str(Path(tmpdir) / f"{name}_{seed}.sqlite")
                    engine, evaluator = build_runtime(db_path, seed=seed)
                    try:
                        report = run_script(engine, evaluator, script_name)
                        scores.append(float(report["total_score"]))
                    finally:
                        engine.store.close()
                score_bands[name] = summarize_score_band(scores)

            self.assertGreaterEqual(score_bands["golden"]["mean"], 81)
            self.assertGreaterEqual(score_bands["golden"]["worst"], 81)
            self.assertGreaterEqual(score_bands["competent_but_imperfect"]["mean"], 55)
            self.assertLessEqual(score_bands["competent_but_imperfect"]["mean"], 65)
            self.assertLessEqual(score_bands["busywork"]["mean"], 35)
            self.assertLessEqual(score_bands["false_green"]["mean"], 30)
            self.assertLess(score_bands["spray_and_pray"]["mean"], score_bands["competent_but_imperfect"]["mean"])
            self.assertLess(score_bands["spray_and_pray"]["mean"], score_bands["golden"]["mean"])


if __name__ == "__main__":
    unittest.main()
