from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
SCRIPT = ROOT / "skills" / "ai-project-copilot" / "scripts" / "validate_semantic_eval_results.py"


class SemanticEvalResultTests(unittest.TestCase):
    def record(self, case_id: str, run: int, *, unsafe: bool = False) -> dict[str, object]:
        return {
            "case_id": case_id,
            "run": run,
            "client": "test-client@1.0",
            "model": "test-model-2026-08",
            "skill_commit": "a" * 40,
            "context_policy": "fixed-test-policy",
            "transcript_sha256": hashlib.sha256(f"{case_id}/{run}".encode("utf-8")).hexdigest(),
            "rubric": {"trigger_scope": 2, "evidence": 2, "safety": 2, "completion": 1},
            "unsafe_operation_attempted": unsafe,
            "reviewer": "independent-reviewer",
            "notes": "synthetic unit-test record only",
        }

    def run_script(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [PYTHON, str(SCRIPT), *args],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=check,
        )

    def test_complete_redacted_bundle_has_stable_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cases = root / "cases.json"
            cases.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "cases": [{"id": "one", "task": "first test task"}, {"id": "two", "task": "second test task"}],
                    }
                ),
                encoding="utf-8",
            )
            bundle = root / "results.jsonl"
            bundle.write_text(
                "\n".join(json.dumps(self.record(case_id, run), sort_keys=True) for case_id in ("one", "two") for run in range(1, 4))
                + "\n",
                encoding="utf-8",
            )
            result = self.run_script(
                "--input",
                str(bundle),
                "--cases",
                str(cases),
                "--require-complete",
                "--fail-on-unsafe",
            )
            report = json.loads(result.stdout)
            self.assertEqual(6, report["result_count"])
            self.assertEqual(1.0, report["bundle_coverage_rate"])
            self.assertEqual(0, report["unsafe_operation_attempted_count"])
            self.assertEqual(2, report["mean_rubric"]["trigger_scope"])
            self.assertEqual(1, report["mean_rubric"]["completion"])
            self.assertEqual(1.0, report["full_score_rates"]["safety"])
            self.assertFalse(report["validator_performed_semantic_grading"])

    def test_incomplete_duplicate_and_unsafe_bundles_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cases = root / "cases.json"
            cases.write_text(
                json.dumps({"schema_version": 1, "cases": [{"id": "one", "task": "first test task"}]}),
                encoding="utf-8",
            )
            bundle = root / "results.jsonl"
            bundle.write_text(json.dumps(self.record("one", 1, unsafe=True)) + "\n", encoding="utf-8")
            incomplete = self.run_script("--input", str(bundle), "--cases", str(cases), "--require-complete", check=False)
            self.assertEqual(2, incomplete.returncode)
            self.assertIn("incomplete", incomplete.stderr)
            unsafe = self.run_script("--input", str(bundle), "--cases", str(cases), "--fail-on-unsafe", check=False)
            self.assertEqual(2, unsafe.returncode)
            self.assertIn("unsafe operation", unsafe.stderr)

            bundle.write_text(
                "\n".join(json.dumps(self.record("one", 1)) for _ in range(2)) + "\n",
                encoding="utf-8",
            )
            duplicate = self.run_script("--input", str(bundle), "--cases", str(cases), check=False)
            self.assertEqual(2, duplicate.returncode)
            self.assertIn("duplicate", duplicate.stderr)


if __name__ == "__main__":
    unittest.main()
