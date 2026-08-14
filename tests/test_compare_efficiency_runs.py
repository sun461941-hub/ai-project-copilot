from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "ai-project-copilot" / "scripts" / "compare_efficiency_runs.py"


def load_module():
    spec = importlib.util.spec_from_file_location("aipc_compare_efficiency_runs", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CompareEfficiencyRunsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_module()

    def record(self, task, *, status="success", tokens=100, cost=1000, e2e=1000, ttft=100):
        return self.mod.normalize_record(
            {
                "task_id": task,
                "final_status": status,
                "total_tokens": tokens,
                "total_cost_nano_usd": cost,
                "e2e_ms": e2e,
                "ttft_ms": ttft,
                "attempts": [{"attempt": 1}],
                "request_template_sha256": "c" * 64,
                "quality_policy_sha256": "a" * 64,
                "pricing_policy_sha256": "e" * 64,
            },
            1,
        )

    def test_reports_real_positive_and_negative_percentages(self):
        baseline = {
            "a": self.record("a", tokens=100, cost=1000, e2e=1000, ttft=100),
            "b": self.record("b", tokens=100, cost=1000, e2e=1000, ttft=200),
        }
        candidate = {
            "a": self.record("a", tokens=70, cost=600, e2e=800, ttft=80),
            "b": self.record("b", tokens=90, cost=800, e2e=700, ttft=160),
        }
        report = self.mod.compare_runs(baseline, candidate)
        self.assertTrue(report.safe_to_adopt)
        self.assertEqual(20.0, report.token_savings_percent)
        self.assertEqual(30.0, report.cost_savings_percent)
        self.assertEqual(25.0, report.e2e_latency_reduction_percent)
        self.assertEqual(1.3333, report.e2e_speedup)
        self.assertEqual(20.0, report.ttft_reduction_percent)

    def test_quality_regression_is_never_hidden_by_efficiency(self):
        baseline = {"a": self.record("a")}
        candidate = {
            "a": self.record("a", status="needs-user-review", tokens=1, cost=1, e2e=1)
        }
        report = self.mod.compare_runs(baseline, candidate)
        self.assertFalse(report.safe_to_adopt)
        self.assertEqual(["a"], report.success_regressions)
        self.assertGreater(report.token_savings_percent, 0)

    def test_failures_and_retries_remain_in_aggregate(self):
        baseline = {"a": self.record("a", tokens=100, cost=100, e2e=100)}
        candidate_record = self.record("a", tokens=150, cost=150, e2e=150)
        candidate = {"a": candidate_record}
        report = self.mod.compare_runs(baseline, candidate)
        self.assertEqual(-50.0, report.token_savings_percent)
        self.assertEqual(-50.0, report.cost_savings_percent)
        self.assertEqual(-50.0, report.e2e_latency_reduction_percent)
        self.assertTrue(report.safe_to_adopt)

    def test_task_ids_must_align_and_be_unique(self):
        with self.assertRaisesRegex(ValueError, "not aligned"):
            self.mod.compare_runs({"a": self.record("a")}, {"b": self.record("b")})
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "runs.jsonl"
            row = {
                "task_id": "same",
                "final_status": "success",
                "total_tokens": 1,
                "total_cost_nano_usd": 1,
                "e2e_ms": 1,
                "ttft_ms": None,
                "attempts": [],
                "request_template_sha256": "c" * 64,
                "quality_policy_sha256": "a" * 64,
                "pricing_policy_sha256": "e" * 64,
            }
            path.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate task"):
                self.mod.load_runs(path)

    def test_missing_ttft_stays_unknown(self):
        baseline = {"a": self.record("a", ttft=100)}
        candidate = {"a": self.record("a", ttft=None)}
        report = self.mod.compare_runs(baseline, candidate)
        self.assertIsNone(report.candidate_ttft_median_ms)
        self.assertIsNone(report.ttft_reduction_percent)

    def test_cli_exit_codes_gate_quality_and_optional_improvement(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline = root / "baseline.json"
            candidate = root / "candidate.json"
            base = {
                "task_id": "a",
                "final_status": "success",
                "total_tokens": 100,
                "total_cost_nano_usd": 100,
                "e2e_ms": 100,
                "ttft_ms": 10,
                "attempts": [],
                "request_template_sha256": "c" * 64,
                "quality_policy_sha256": "a" * 64,
                "pricing_policy_sha256": "e" * 64,
            }
            baseline.write_text(json.dumps([base]), encoding="utf-8")
            same = dict(base)
            candidate.write_text(json.dumps([same]), encoding="utf-8")
            with mock.patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(
                    4,
                    self.mod.main(
                        [
                            "--baseline",
                            str(baseline),
                            "--candidate",
                            str(candidate),
                            "--require-improvement",
                        ]
                    ),
                )
            failed = dict(base, final_status="needs-user-review")
            candidate.write_text(json.dumps([failed]), encoding="utf-8")
            with mock.patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(
                    3,
                    self.mod.main(
                        ["--baseline", str(baseline), "--candidate", str(candidate)]
                    ),
                )

    def test_quality_policy_mismatch_blocks_adoption(self):
        baseline = {"a": self.record("a")}
        candidate_record = replace(
            self.record("a"), quality_policy_sha256="b" * 64
        )
        report = self.mod.compare_runs(baseline, {"a": candidate_record})
        self.assertFalse(report.quality_policy_aligned)
        self.assertEqual(["a"], report.quality_policy_mismatches)
        self.assertFalse(report.safe_to_adopt)

    def test_request_template_mismatch_blocks_adoption(self):
        baseline = {"a": self.record("a")}
        candidate_record = replace(
            self.record("a"), request_template_sha256="d" * 64
        )
        report = self.mod.compare_runs(baseline, {"a": candidate_record})
        self.assertFalse(report.request_templates_aligned)
        self.assertEqual(["a"], report.request_template_mismatches)
        self.assertFalse(report.safe_to_adopt)

    def test_pricing_policy_mismatch_blocks_adoption(self):
        baseline = {"a": self.record("a")}
        candidate_record = replace(
            self.record("a"), pricing_policy_sha256="f" * 64
        )
        report = self.mod.compare_runs(baseline, {"a": candidate_record})
        self.assertFalse(report.pricing_policy_aligned)
        self.assertEqual(["a"], report.pricing_policy_mismatches)
        self.assertFalse(report.safe_to_adopt)

    def test_zero_success_pair_fails_closed(self):
        baseline = {"a": self.record("a", status="needs-user-review")}
        candidate = {"a": self.record("a", status="needs-user-review")}
        report = self.mod.compare_runs(baseline, candidate)
        self.assertFalse(report.safe_to_adopt)
        self.assertIn("baseline contains no successful tasks", report.decision_reasons)

    def test_huge_integer_usage_is_compared_without_float_overflow(self):
        huge = 10**400
        baseline = {"a": self.record("a", tokens=huge, cost=huge)}
        candidate = {"a": self.record("a", tokens=huge // 2, cost=huge // 2)}
        report = self.mod.compare_runs(baseline, candidate)
        self.assertEqual(50.0, report.token_savings_percent)
        self.assertEqual(50.0, report.cost_savings_percent)

    def test_e2e_aggregate_overflow_is_clean_cli_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            row = {
                "task_id": "a",
                "final_status": "success",
                "total_tokens": 1,
                "total_cost_nano_usd": 1,
                "e2e_ms": 1e308,
                "ttft_ms": 1,
                "attempts": [],
                "request_template_sha256": "c" * 64,
                "quality_policy_sha256": "a" * 64,
                "pricing_policy_sha256": "e" * 64,
            }
            baseline = root / "baseline.json"
            candidate = root / "candidate.json"
            baseline.write_text(json.dumps([row, dict(row, task_id="b")]), encoding="utf-8")
            candidate.write_text(json.dumps([row, dict(row, task_id="b")]), encoding="utf-8")
            stderr = io.StringIO()
            with mock.patch("sys.stderr", stderr), mock.patch(
                "sys.stdout", new_callable=io.StringIO
            ):
                self.assertEqual(
                    2,
                    self.mod.main(
                        ["--baseline", str(baseline), "--candidate", str(candidate)]
                    ),
                )
            self.assertIn("aggregate exceeds", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_percentage_overflow_is_clean_error_not_infinity(self):
        baseline = {"a": self.record("a", tokens=1, cost=1, e2e=1)}
        candidate = {
            "a": self.record("a", tokens=10**307, cost=10**307, e2e=1e307)
        }
        with self.assertRaisesRegex(ValueError, "saving percentage exceeds"):
            self.mod.compare_runs(baseline, candidate)

    def test_markdown_reports_ttft(self):
        report = self.mod.compare_runs(
            {"a": self.record("a", ttft=100)},
            {"a": self.record("a", ttft=80)},
        )
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            self.mod._emit(report, "markdown")
        self.assertIn("baseline median TTFT", output.getvalue())
        self.assertIn("TTFT reduction", output.getvalue())

        unknown = self.mod.compare_runs(
            {"a": self.record("a", ttft=None)},
            {"a": self.record("a", ttft=None)},
        )
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            self.mod._emit(unknown, "markdown")
        self.assertIn("baseline median TTFT: `n/a`", output.getvalue())
        self.assertNotIn("None", output.getvalue())


if __name__ == "__main__":
    unittest.main()
