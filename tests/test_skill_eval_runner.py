from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "ai-project-copilot"
RUNNER = SKILL / "scripts" / "run_skill_evals.py"
PYTHON = sys.executable


def valid_static_evals(command_cases: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "skill_name": "fixture-skill",
        "version": "1.0.0",
        "evals": [
            {
                "id": "eval-1",
                "prompt": "Inspect the repository.",
                "expected_output": "An evidence-based report.",
                "expectations": ["Uses repository evidence"],
            }
        ],
    }
    if command_cases is not None:
        data["command_cases"] = command_cases
    return data


def command_case(
    case_id: str,
    script: str,
    *,
    timeout: float = 2,
    expect: dict[str, Any] | None = None,
    cwd: str = ".",
) -> dict[str, Any]:
    return {
        "id": case_id,
        "argv": ["{python}", script],
        "cwd": cwd,
        "timeout_seconds": timeout,
        "expect": expect if expect is not None else {"exit_code": 0},
    }


class FixtureRepo:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.write_json("static.json", valid_static_evals())
        self.write_triggers()
        self.write_json("commands.json", {"schema_version": 1, "cases": []})
        (root / "scripts").mkdir(exist_ok=True)

    def write_json(self, name: str, value: Any) -> None:
        (self.root / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def write_triggers(self, rows: list[dict[str, str]] | None = None) -> None:
        rows = rows or [
            {
                "id": "trigger-1",
                "split": "validation",
                "should_trigger": "true",
                "prompt": "Improve this AI repository.",
                "expected_scope": "repository workflow",
            },
            {
                "id": "negative-1",
                "split": "validation",
                "should_trigger": "false",
                "prompt": "Fix one typo.",
                "expected_scope": "isolated edit",
            },
        ]
        with (self.root / "triggers.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=("id", "split", "should_trigger", "prompt", "expected_scope"),
            )
            writer.writeheader()
            writer.writerows(rows)

    def write_script(self, name: str, source: str) -> str:
        path = self.root / "scripts" / name
        path.write_text(source, encoding="utf-8")
        return path.relative_to(self.root).as_posix()


def run_fixture(repo: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            PYTHON,
            str(RUNNER),
            "--repo",
            str(repo),
            "--evals",
            "static.json",
            "--triggers",
            "triggers.csv",
            "--commands",
            "commands.json",
            *extra,
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class SkillEvalRunnerTests(unittest.TestCase):
    def test_checked_in_eval_suite_passes_without_semantic_grading(self) -> None:
        result = subprocess.run(
            [PYTHON, str(RUNNER), "--format", "json"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        report = json.loads(result.stdout)
        self.assertTrue(report["passed"])
        self.assertFalse(report["semantic_grading_performed"])
        self.assertEqual("structural-and-deterministic-only", report["scope"])
        self.assertEqual(27, report["summary"]["static_evals"])
        self.assertEqual(20, report["summary"]["trigger_cases"])
        self.assertEqual(4, report["summary"]["command_passed"])

    def test_markdown_output_is_stable_and_discloses_limit(self) -> None:
        args = [PYTHON, str(RUNNER), "--format", "markdown"]
        first = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=False)
        second = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(0, first.returncode, first.stderr + first.stdout)
        self.assertEqual(first.stdout, second.stdout)
        self.assertIn("did not generate model output or perform semantic grading", first.stdout)
        self.assertIn("# Skill eval report — PASS", first.stdout)

    def test_malformed_json_fails_with_machine_readable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = FixtureRepo(Path(temp))
            (fixture.root / "static.json").write_text('{"evals": [', encoding="utf-8")
            result = run_fixture(fixture.root)
            self.assertEqual(1, result.returncode)
            report = json.loads(result.stdout)
            self.assertFalse(report["passed"])
            self.assertIn("invalid_json", {item["code"] for item in report["errors"]})

    def test_non_utf8_json_fails_without_a_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = FixtureRepo(Path(temp))
            (fixture.root / "static.json").write_bytes(b"\xff\xfe")
            result = run_fixture(fixture.root)
            self.assertEqual(1, result.returncode)
            self.assertNotIn("Traceback", result.stderr)
            report = json.loads(result.stdout)
            self.assertFalse(report["passed"])
            self.assertIn("invalid_encoding", {item["code"] for item in report["errors"]})

    def test_deeply_nested_json_fails_without_a_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = FixtureRepo(Path(temp))
            (fixture.root / "static.json").write_text(
                "[" * 100_000 + "0" + "]" * 100_000,
                encoding="utf-8",
            )
            result = run_fixture(fixture.root)
            self.assertEqual(1, result.returncode)
            self.assertNotIn("Traceback", result.stderr)
            report = json.loads(result.stdout)
            self.assertFalse(report["passed"])
            self.assertIn("invalid_json", {item["code"] for item in report["errors"]})

    def test_copied_standalone_skill_bundle_runs_its_default_evals(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            copied_skill = Path(temp) / "ai-project-copilot"
            shutil.copytree(
                SKILL,
                copied_skill,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            copied_runner = copied_skill / "scripts" / "run_skill_evals.py"
            result = subprocess.run(
                [PYTHON, str(copied_runner), "--format", "json"],
                cwd=Path(temp),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr + result.stdout)
            report = json.loads(result.stdout)
            self.assertTrue(report["passed"])
            self.assertEqual(27, report["summary"]["static_evals"])
            self.assertEqual(20, report["summary"]["trigger_cases"])
            self.assertEqual(4, report["summary"]["command_passed"])

    def test_duplicate_eval_ids_and_empty_expectations_fail_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = FixtureRepo(Path(temp))
            data = valid_static_evals()
            data["evals"].append(
                {
                    "id": "eval-1",
                    "prompt": "Another prompt",
                    "expected_output": "Another output",
                    "expectations": [],
                }
            )
            fixture.write_json("static.json", data)
            result = run_fixture(fixture.root)
            report = json.loads(result.stdout)
            self.assertEqual(1, result.returncode)
            codes = {item["code"] for item in report["errors"]}
            self.assertIn("duplicate_id", codes)
            self.assertIn("invalid_expectations", codes)

    def test_invalid_trigger_label_and_duplicate_id_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = FixtureRepo(Path(temp))
            fixture.write_triggers(
                [
                    {
                        "id": "same",
                        "split": "train",
                        "should_trigger": "sometimes",
                        "prompt": "Prompt one",
                        "expected_scope": "scope",
                    },
                    {
                        "id": "same",
                        "split": "validation",
                        "should_trigger": "false",
                        "prompt": "Prompt two",
                        "expected_scope": "scope",
                    },
                ]
            )
            result = run_fixture(fixture.root)
            report = json.loads(result.stdout)
            self.assertEqual(1, result.returncode)
            codes = {item["code"] for item in report["errors"]}
            self.assertIn("invalid_label", codes)
            self.assertIn("duplicate_id", codes)

    def test_command_failure_returns_nonzero_and_preserves_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = FixtureRepo(Path(temp))
            script = fixture.write_script(
                "fail.py", "import sys\nprint('failure evidence')\nraise SystemExit(3)\n"
            )
            fixture.write_json(
                "commands.json",
                {"schema_version": 1, "cases": [command_case("fails", script)]},
            )
            result = run_fixture(fixture.root)
            report = json.loads(result.stdout)
            self.assertEqual(1, result.returncode)
            case = report["command_results"][0]
            self.assertEqual("fail", case["status"])
            self.assertEqual(3, case["exit_code"])
            self.assertIn("failure evidence", case["stdout"])
            self.assertIn("exit code expected 0, got 3", case["failures"])

    def test_timeout_is_a_deterministic_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = FixtureRepo(Path(temp))
            script = fixture.write_script("slow.py", "import time\ntime.sleep(2)\n")
            fixture.write_json(
                "commands.json",
                {
                    "schema_version": 1,
                    "cases": [command_case("times-out", script, timeout=0.05)],
                },
            )
            result = run_fixture(fixture.root)
            report = json.loads(result.stdout)
            self.assertEqual(1, result.returncode)
            case = report["command_results"][0]
            self.assertTrue(case["timed_out"])
            self.assertEqual(["timed out after 0.05 seconds"], case["failures"])

    def test_command_script_cannot_escape_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            fixture = FixtureRepo(Path(temp))
            marker = Path(outside) / "marker.txt"
            outside_script = Path(outside) / "outside.py"
            outside_script.write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
                encoding="utf-8",
            )
            relative_escape = Path("..") / Path(outside).name / "outside.py"
            fixture.write_json(
                "commands.json",
                {
                    "schema_version": 1,
                    "cases": [command_case("escape", relative_escape.as_posix())],
                },
            )
            result = run_fixture(fixture.root)
            report = json.loads(result.stdout)
            self.assertEqual(1, result.returncode)
            self.assertIn("unsafe command path", report["command_results"][0]["failures"][0])
            self.assertFalse(marker.exists())

    def test_command_dataset_path_cannot_escape_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            fixture = FixtureRepo(Path(temp))
            (Path(outside) / "commands.json").write_text(
                '{"schema_version": 1, "cases": []}\n', encoding="utf-8"
            )
            result = run_fixture(
                fixture.root,
                "--commands",
                f"../{Path(outside).name}/commands.json",
            )
            report = json.loads(result.stdout)
            self.assertEqual(1, result.returncode)
            self.assertTrue(
                any(
                    item["dataset"] == "command_cases" and item["code"] == "unsafe_path"
                    for item in report["errors"]
                )
            )

    def test_json_subset_and_embedded_command_extension_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = FixtureRepo(Path(temp))
            script = fixture.write_script(
                "json_output.py",
                "import json\nprint(json.dumps({'result': {'ok': True, 'extra': 7}}))\n",
            )
            embedded = command_case(
                "embedded",
                script,
                expect={
                    "exit_code": 0,
                    "stderr_exact": "",
                    "stdout_json_subset": {"result": {"ok": True}},
                },
            )
            fixture.write_json("static.json", valid_static_evals([embedded]))
            result = run_fixture(fixture.root)
            report = json.loads(result.stdout)
            self.assertEqual(0, result.returncode, result.stdout)
            self.assertEqual("pass", report["command_results"][0]["status"])

    def test_json_report_is_stable_and_command_ids_are_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = FixtureRepo(Path(temp))
            script = fixture.write_script("pass.py", "print('ok')\n")
            cases = [
                command_case("z-last", script, expect={"exit_code": 0, "stdout_exact": "ok\n"}),
                command_case("a-first", script, expect={"exit_code": 0, "stdout_exact": "ok\n"}),
            ]
            fixture.write_json("commands.json", {"schema_version": 1, "cases": cases})
            first = run_fixture(fixture.root)
            second = run_fixture(fixture.root)
            self.assertEqual(0, first.returncode, first.stdout)
            self.assertEqual(first.stdout, second.stdout)
            report = json.loads(first.stdout)
            self.assertEqual(
                ["a-first", "z-last"],
                [item["id"] for item in report["command_results"]],
            )

    def test_shell_commands_are_rejected_by_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = FixtureRepo(Path(temp))
            unsafe = command_case("shell", "scripts/pass.py")
            unsafe["argv"] = ["sh", "-c", "echo unsafe"]
            fixture.write_json(
                "commands.json", {"schema_version": 1, "cases": [unsafe]}
            )
            result = run_fixture(fixture.root)
            report = json.loads(result.stdout)
            self.assertEqual(1, result.returncode)
            self.assertIn("unsafe_executable", {item["code"] for item in report["errors"]})


if __name__ == "__main__":
    unittest.main()
