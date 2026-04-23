from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

from tpm_sim.authoring import init_proposal, run_closure_suite, validate_proposal
from tpm_sim.cli import main, run_readiness
from tpm_sim.scenario import load_bundle_from_paths, load_scenario_bundle


ROOT = Path(__file__).resolve().parents[1]
AUTHORING_BRIEFS = ROOT / "authoring" / "briefs"
AUTHORING_FIXTURES = ROOT / "authoring" / "fixtures"
EXAMPLES = ROOT / "examples"
SCENARIOS = ROOT / "tpm_sim" / "scenarios"


class CalibrationHygieneTests(unittest.TestCase):
    def test_official_scenarios_ship_fresh_validation_and_closure_artifacts(self) -> None:
        for scenario_id in ("northstar_launch_week", "internal_rollout_smoke"):
            bundle = load_scenario_bundle(scenario_id)
            self.assertTrue(bundle["validation_status"]["fresh"], scenario_id)
            self.assertTrue(bundle["validation_status"]["passed"], scenario_id)
            self.assertTrue(bundle["closure_status"]["fresh"], scenario_id)
            self.assertTrue(bundle["closure_status"]["passed"], scenario_id)

    def test_loader_marks_stale_validation_and_closure_artifacts_untrusted(self) -> None:
        source_dir = SCENARIOS / "northstar_launch_week"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for name in ("scenario.json", "coverage_contract.json", "coverage_semantics.json"):
                shutil.copyfile(source_dir / name, root / name)

            validation = json.loads((source_dir / "validation.json").read_text())
            validation["bundle_digest"] = "stale-validation-digest"
            validation["compiled_coverage_digest"] = "stale-validation-coverage-digest"
            (root / "validation.json").write_text(json.dumps(validation, indent=2, sort_keys=True))

            closure = json.loads((source_dir / "closure_report.json").read_text())
            closure["bundle_digest"] = "stale-closure-digest"
            closure["compiled_coverage_digest"] = "stale-closure-coverage-digest"
            (root / "closure_report.json").write_text(json.dumps(closure, indent=2, sort_keys=True))

            bundle = load_bundle_from_paths(
                root / "scenario.json",
                None,
                contract_path=root / "coverage_contract.json",
                semantics_path=root / "coverage_semantics.json",
                validation_report_path=root / "validation.json",
                closure_report_path=root / "closure_report.json",
            )

            self.assertEqual(bundle["validation_status"]["status"], "stale")
            self.assertFalse(bundle["validation_status"]["fresh"])
            self.assertFalse(bundle["validation_status"]["passed"])
            self.assertEqual(bundle["closure_status"]["status"], "stale")
            self.assertFalse(bundle["closure_status"]["fresh"])
            self.assertFalse(bundle["closure_status"]["passed"])

    def test_readiness_rejects_smoke_only_scenarios_cleanly(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            run_readiness("internal_rollout_smoke", str(EXAMPLES), False)
        self.assertIn("not readiness-calibrated", str(ctx.exception))
        self.assertIn("benchmark --scenario internal_rollout_smoke", str(ctx.exception))

        stderr = StringIO()
        with redirect_stderr(stderr):
            exit_code = main(["readiness", "--scenario", "internal_rollout_smoke"])
        self.assertEqual(exit_code, 1)
        self.assertIn("not readiness-calibrated", stderr.getvalue())

    def test_closure_suite_emits_bundle_and_compiled_coverage_digests(self) -> None:
        scenario_dir = SCENARIOS / "internal_rollout_smoke"
        smoke_script = EXAMPLES / "internal_rollout_smoke" / "smoke.tpm"

        with tempfile.TemporaryDirectory() as tmpdir:
            proposal_dir = Path(tmpdir) / "proposal"
            init_proposal(str(AUTHORING_BRIEFS / "internal_rollout_smoke.json"), str(proposal_dir))

            candidate_dir = proposal_dir / "candidate"
            candidate_dir.mkdir(parents=True, exist_ok=True)
            for name in ("scenario.json", "coverage_contract.json", "coverage_semantics.json", "npc_coverage.json"):
                shutil.copyfile(scenario_dir / name, candidate_dir / name)

            trajectories_dir = proposal_dir / "trajectories"
            trajectories_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(smoke_script, trajectories_dir / "smoke.tpm")

            validation = validate_proposal(str(proposal_dir))
            closure = run_closure_suite(
                str(proposal_dir),
                adapter="fixture",
                model="fixture",
                fixtures_root=str(AUTHORING_FIXTURES),
            )

            self.assertEqual(closure["bundle_digest"], validation["bundle_digest"])
            self.assertEqual(closure["compiled_coverage_digest"], validation["compiled_coverage_digest"])

            stored = json.loads((proposal_dir / "reports" / "closure_report.json").read_text())
            self.assertEqual(stored["bundle_digest"], validation["bundle_digest"])
            self.assertEqual(stored["compiled_coverage_digest"], validation["compiled_coverage_digest"])


if __name__ == "__main__":
    unittest.main()
