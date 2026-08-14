#!/usr/bin/env python3
"""Validate Skill eval datasets and run trusted deterministic command cases.

This runner does not invoke a model and does not grade prompt semantics.  Static
prompt expectations are schema-checked only; executable cases must declare
deterministic, bundled Skill-local Python commands and machine-checkable outcomes.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPORT_SCHEMA_VERSION = 1
COMMAND_SCHEMA_VERSION = 1
MAX_CAPTURE_CHARS = 65_536
REQUIRED_TRIGGER_COLUMNS = {
    "id",
    "split",
    "should_trigger",
    "prompt",
    "expected_scope",
}
EXPECTATION_KEYS = {
    "exit_code",
    "stdout_exact",
    "stderr_exact",
    "stdout_contains",
    "stderr_contains",
    "stdout_json_subset",
}


@dataclass(frozen=True)
class Issue:
    dataset: str
    code: str
    location: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "dataset": self.dataset,
            "code": self.code,
            "location": self.location,
            "message": self.message,
        }


@dataclass(frozen=True)
class CommandCase:
    case_id: str
    argv: tuple[str, ...]
    cwd: str
    timeout_seconds: float
    expect: dict[str, Any]


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _safe_repo_path(repo: Path, raw: str, *, must_be: str) -> Path:
    """Resolve a repository-relative path without permitting an escape."""
    candidate_path = Path(raw)
    if candidate_path.is_absolute():
        raise ValueError("path must be repository-relative")
    if any(part == ".." for part in candidate_path.parts):
        raise ValueError("path must not contain '..'")
    try:
        candidate = (repo / candidate_path).resolve(strict=True)
    except OSError as exc:
        raise ValueError("path does not exist") from exc
    try:
        candidate.relative_to(repo)
    except ValueError as exc:
        raise ValueError("path resolves outside the repository") from exc
    if must_be == "file" and not candidate.is_file():
        raise ValueError("path must identify a file")
    if must_be == "directory" and not candidate.is_dir():
        raise ValueError("path must identify a directory")
    return candidate


def _load_json(path: Path, dataset: str, label: str, issues: list[Issue]) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        issues.append(Issue(dataset, "read_error", label, "could not read file"))
    except UnicodeError:
        issues.append(Issue(dataset, "invalid_encoding", label, "file must be valid UTF-8"))
    except json.JSONDecodeError as exc:
        issues.append(
            Issue(
                dataset,
                "invalid_json",
                label,
                f"invalid JSON at line {exc.lineno}, column {exc.colno}",
            )
        )
    except RecursionError:
        issues.append(
            Issue(dataset, "invalid_json", label, "JSON nesting exceeds the safe parser depth")
        )
    return None


def _validate_static_evals(data: Any, issues: list[Issue]) -> tuple[int, list[Any]]:
    dataset = "skill_evals"
    if not isinstance(data, dict):
        issues.append(Issue(dataset, "invalid_root", "$", "root must be an object"))
        return 0, []
    for field in ("skill_name", "version"):
        if not _nonempty_string(data.get(field)):
            issues.append(
                Issue(dataset, "invalid_field", f"$.{field}", "must be a non-empty string")
            )
    embedded = data.get("command_cases", [])
    if embedded is None:
        embedded = []
    elif not isinstance(embedded, list):
        issues.append(
            Issue(dataset, "invalid_command_cases", "$.command_cases", "optional extension must be an array")
        )
        embedded = []
    evals = data.get("evals")
    if not isinstance(evals, list) or not evals:
        issues.append(Issue(dataset, "invalid_evals", "$.evals", "must be a non-empty array"))
        return 0, embedded

    seen: set[str] = set()
    for index, item in enumerate(evals):
        location = f"$.evals[{index}]"
        if not isinstance(item, dict):
            issues.append(Issue(dataset, "invalid_case", location, "must be an object"))
            continue
        raw_id = item.get("id")
        if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)) or not str(raw_id).strip():
            issues.append(Issue(dataset, "invalid_id", f"{location}.id", "must be a string or integer"))
        else:
            normalized = str(raw_id).strip()
            if normalized in seen:
                issues.append(Issue(dataset, "duplicate_id", f"{location}.id", f"duplicate id: {normalized}"))
            seen.add(normalized)
        for field in ("prompt", "expected_output"):
            if not _nonempty_string(item.get(field)):
                issues.append(
                    Issue(dataset, "invalid_field", f"{location}.{field}", "must be a non-empty string")
                )
        expectations = item.get("expectations")
        if not isinstance(expectations, list) or not expectations:
            issues.append(
                Issue(dataset, "invalid_expectations", f"{location}.expectations", "must be a non-empty string array")
            )
        elif any(not _nonempty_string(value) for value in expectations):
            issues.append(
                Issue(dataset, "invalid_expectations", f"{location}.expectations", "must contain only non-empty strings")
            )
    return len(evals), embedded


def _validate_triggers(path: Path, label: str, issues: list[Issue]) -> tuple[int, int, int]:
    dataset = "trigger_dataset"
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            fields = reader.fieldnames or []
            if len(fields) != len(set(fields)):
                issues.append(Issue(dataset, "duplicate_columns", label, "CSV header names must be unique"))
            missing = sorted(REQUIRED_TRIGGER_COLUMNS - set(fields))
            if missing:
                issues.append(
                    Issue(dataset, "missing_columns", label, "missing columns: " + ", ".join(missing))
                )
                return 0, 0, 0
            rows = list(reader)
    except (OSError, csv.Error):
        issues.append(Issue(dataset, "read_error", label, "could not read CSV dataset"))
        return 0, 0, 0

    if not rows:
        issues.append(Issue(dataset, "empty_dataset", label, "must contain at least one row"))
    seen: set[str] = set()
    positives = 0
    negatives = 0
    for index, row in enumerate(rows, start=2):
        location = f"row {index}"
        if None in row:
            issues.append(Issue(dataset, "extra_columns", location, "row has more values than the header"))
        for field in REQUIRED_TRIGGER_COLUMNS - {"should_trigger"}:
            if not _nonempty_string(row.get(field)):
                issues.append(Issue(dataset, "invalid_field", f"{location}.{field}", "must be non-empty"))
        split = (row.get("split") or "").strip().casefold()
        if split and split not in {"train", "validation"}:
            issues.append(
                Issue(dataset, "invalid_split", f"{location}.split", "must be train or validation")
            )
        case_id = (row.get("id") or "").strip()
        if case_id:
            if case_id in seen:
                issues.append(Issue(dataset, "duplicate_id", f"{location}.id", f"duplicate id: {case_id}"))
            seen.add(case_id)
        label_value = (row.get("should_trigger") or "").strip().casefold()
        if label_value == "true":
            positives += 1
        elif label_value == "false":
            negatives += 1
        else:
            issues.append(
                Issue(dataset, "invalid_label", f"{location}.should_trigger", "must be true or false")
            )
    return len(rows), positives, negatives


def _validate_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _parse_command_cases(raw_cases: list[Any], issues: list[Issue]) -> list[CommandCase]:
    dataset = "command_cases"
    parsed: list[CommandCase] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_cases):
        location = f"$.cases[{index}]"
        if not isinstance(item, dict):
            issues.append(Issue(dataset, "invalid_case", location, "must be an object"))
            continue
        case_id = item.get("id")
        valid = True
        if not _nonempty_string(case_id):
            issues.append(Issue(dataset, "invalid_id", f"{location}.id", "must be a non-empty string"))
            valid = False
            normalized_id = f"invalid-{index}"
        else:
            normalized_id = case_id.strip()
            if normalized_id in seen:
                issues.append(
                    Issue(dataset, "duplicate_id", f"{location}.id", f"duplicate id: {normalized_id}")
                )
                valid = False
            seen.add(normalized_id)

        argv = item.get("argv")
        if (
            not isinstance(argv, list)
            or len(argv) < 2
            or any(not _nonempty_string(value) for value in argv)
        ):
            issues.append(
                Issue(dataset, "invalid_argv", f"{location}.argv", "must contain {python}, a script path, and optional arguments")
            )
            valid = False
            argv = []
        elif argv[0] != "{python}":
            issues.append(
                Issue(dataset, "unsafe_executable", f"{location}.argv[0]", "only the {python} executable token is allowed")
            )
            valid = False

        cwd = item.get("cwd", ".")
        if not _nonempty_string(cwd):
            issues.append(Issue(dataset, "invalid_cwd", f"{location}.cwd", "must be a relative directory"))
            valid = False
            cwd = "."

        timeout = item.get("timeout_seconds", 10)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0.05 <= float(timeout) <= 300:
            issues.append(
                Issue(dataset, "invalid_timeout", f"{location}.timeout_seconds", "must be between 0.05 and 300")
            )
            valid = False
            timeout = 10

        expect = item.get("expect")
        if not isinstance(expect, dict):
            issues.append(Issue(dataset, "invalid_expect", f"{location}.expect", "must be an object"))
            valid = False
            expect = {}
        else:
            unknown = sorted(set(expect) - EXPECTATION_KEYS)
            if unknown:
                issues.append(
                    Issue(dataset, "unknown_expectation", f"{location}.expect", "unknown keys: " + ", ".join(unknown))
                )
                valid = False
            exit_code = expect.get("exit_code")
            if isinstance(exit_code, bool) or not isinstance(exit_code, int):
                issues.append(
                    Issue(dataset, "invalid_exit_code", f"{location}.expect.exit_code", "must be an integer")
                )
                valid = False
            for key in ("stdout_exact", "stderr_exact"):
                if key in expect and not isinstance(expect[key], str):
                    issues.append(Issue(dataset, "invalid_expectation", f"{location}.expect.{key}", "must be a string"))
                    valid = False
            for key in ("stdout_contains", "stderr_contains"):
                if key in expect and not _validate_string_list(expect[key]):
                    issues.append(Issue(dataset, "invalid_expectation", f"{location}.expect.{key}", "must be a string array"))
                    valid = False

        if valid:
            parsed.append(
                CommandCase(normalized_id, tuple(argv), str(cwd), float(timeout), dict(expect))
            )
    return sorted(parsed, key=lambda case: case.case_id)


def _load_command_file(path: Path, label: str, issues: list[Issue]) -> list[Any]:
    data = _load_json(path, "command_cases", label, issues)
    if data is None:
        return []
    if not isinstance(data, dict):
        issues.append(Issue("command_cases", "invalid_root", "$", "root must be an object"))
        return []
    version = data.get("schema_version")
    if isinstance(version, bool) or version != COMMAND_SCHEMA_VERSION:
        issues.append(
            Issue("command_cases", "unsupported_schema", "$.schema_version", f"must equal {COMMAND_SCHEMA_VERSION}")
        )
        return []
    cases = data.get("cases")
    if not isinstance(cases, list):
        issues.append(Issue("command_cases", "invalid_cases", "$.cases", "must be an array"))
        return []
    return cases


def _normalize_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _clip(value: str) -> str:
    if len(value) <= MAX_CAPTURE_CHARS:
        return value
    return value[:MAX_CAPTURE_CHARS] + "\n<output truncated>\n"


def _json_subset(expected: Any, actual: Any, path: str = "$") -> str | None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return f"{path} is not an object"
        for key in sorted(expected):
            if key not in actual:
                return f"{path}.{key} is missing"
            failure = _json_subset(expected[key], actual[key], f"{path}.{key}")
            if failure:
                return failure
        return None
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return f"{path} is not an array"
        if len(actual) < len(expected):
            return f"{path} has fewer than {len(expected)} items"
        for index, item in enumerate(expected):
            failure = _json_subset(item, actual[index], f"{path}[{index}]")
            if failure:
                return failure
        return None
    if actual != expected:
        return f"{path} expected {expected!r}, got {actual!r}"
    return None


def _evaluate_expectations(case: CommandCase, returncode: int, stdout: str, stderr: str) -> list[str]:
    failures: list[str] = []
    expected = case.expect
    if returncode != expected["exit_code"]:
        failures.append(f"exit code expected {expected['exit_code']}, got {returncode}")
    for stream_name, stream in (("stdout", stdout), ("stderr", stderr)):
        exact_key = f"{stream_name}_exact"
        if exact_key in expected and stream != _normalize_output(expected[exact_key]):
            failures.append(f"{stream_name} did not match exactly")
        contains_key = f"{stream_name}_contains"
        for needle in expected.get(contains_key, []):
            if needle not in stream:
                failures.append(f"{stream_name} missing required text: {needle!r}")
    if "stdout_json_subset" in expected:
        try:
            actual_json = json.loads(stdout)
        except json.JSONDecodeError:
            failures.append("stdout is not valid JSON")
        else:
            mismatch = _json_subset(expected["stdout_json_subset"], actual_json)
            if mismatch:
                failures.append("stdout JSON mismatch: " + mismatch)
    return failures


def _run_command(case: CommandCase, repo: Path) -> dict[str, Any]:
    base = {
        "id": case.case_id,
        "status": "fail",
        "timed_out": False,
        "exit_code": None,
        "failures": [],
    }
    try:
        cwd = _safe_repo_path(repo, case.cwd, must_be="directory")
        script = _safe_repo_path(repo, case.argv[1], must_be="file")
        if script.suffix.casefold() != ".py":
            raise ValueError("command target must be a .py file")
    except ValueError as exc:
        base["failures"] = [f"unsafe command path: {exc}"]
        return base

    env = {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    # Windows uses a small set of inherited variables to locate system DLLs.
    for name in ("PATH", "SYSTEMROOT", "WINDIR"):
        if name in os.environ:
            env[name] = os.environ[name]
    command = [sys.executable, str(script), *case.argv[2:]]
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=case.timeout_seconds,
            check=False,
        )
        stdout = _normalize_output(completed.stdout)
        stderr = _normalize_output(completed.stderr)
        failures = _evaluate_expectations(case, completed.returncode, stdout, stderr)
        base["exit_code"] = completed.returncode
        base["failures"] = failures
    except subprocess.TimeoutExpired as exc:
        stdout = _normalize_output(exc.stdout)
        stderr = _normalize_output(exc.stderr)
        base["timed_out"] = True
        base["failures"] = [f"timed out after {case.timeout_seconds:g} seconds"]
    except OSError as exc:
        stdout = ""
        stderr = ""
        base["failures"] = [f"could not start command: {exc.__class__.__name__}"]

    if not base["failures"]:
        base["status"] = "pass"
    else:
        base["stdout"] = _clip(stdout)
        base["stderr"] = _clip(stderr)
    return base


def _dataset_status(dataset: str, issues: list[Issue]) -> str:
    return "fail" if any(issue.dataset == dataset for issue in issues) else "pass"


def _build_report(
    issues: list[Issue],
    eval_count: int,
    trigger_count: int,
    trigger_true: int,
    trigger_false: int,
    command_results: list[dict[str, Any]],
    commands_skipped: bool,
) -> dict[str, Any]:
    ordered_issues = sorted(issues, key=lambda item: (item.dataset, item.location, item.code, item.message))
    command_failures = sum(item["status"] == "fail" for item in command_results)
    passed = not ordered_issues and command_failures == 0
    command_status = "skipped" if commands_skipped else (
        "fail" if _dataset_status("command_cases", issues) == "fail" or command_failures else "pass"
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "scope": "structural-and-deterministic-only",
        "semantic_grading_performed": False,
        "passed": passed,
        "summary": {
            "static_evals": eval_count,
            "trigger_cases": trigger_count,
            "trigger_true": trigger_true,
            "trigger_false": trigger_false,
            "command_cases": len(command_results),
            "command_passed": len(command_results) - command_failures,
            "command_failed": command_failures,
            "schema_errors": len(ordered_issues),
        },
        "datasets": {
            "skill_evals": {"status": _dataset_status("skill_evals", issues), "count": eval_count},
            "trigger_dataset": {"status": _dataset_status("trigger_dataset", issues), "count": trigger_count},
            "command_cases": {"status": command_status, "count": len(command_results)},
        },
        "command_results": command_results,
        "errors": [issue.as_dict() for issue in ordered_issues],
        "limitations": [
            "Prompt expectations were schema-checked only; no model output was generated or semantically graded.",
            "Command cases run trusted bundled Skill-local Python scripts without a shell; this is not an operating-system sandbox.",
        ],
    }


def _markdown(report: dict[str, Any]) -> str:
    def escaped(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    status = "PASS" if report["passed"] else "FAIL"
    summary = report["summary"]
    lines = [
        f"# Skill eval report — {status}",
        "",
        "This report covers structural validation and deterministic commands only. "
        "It did not generate model output or perform semantic grading.",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "|---|---:|",
        f"| Static prompt evals | {summary['static_evals']} |",
        f"| Trigger cases | {summary['trigger_cases']} |",
        f"| Deterministic commands passed | {summary['command_passed']} |",
        f"| Deterministic commands failed | {summary['command_failed']} |",
        f"| Schema errors | {summary['schema_errors']} |",
        "",
        "## Deterministic command cases",
        "",
        "| ID | Status | Exit | Details |",
        "|---|---|---:|---|",
    ]
    if report["command_results"]:
        for item in report["command_results"]:
            details = "; ".join(item["failures"]) or "—"
            exit_code = "—" if item["exit_code"] is None else item["exit_code"]
            lines.append(
                f"| {escaped(item['id'])} | {item['status'].upper()} | {exit_code} | {escaped(details)} |"
            )
    else:
        lines.append("| — | SKIPPED | — | No command cases executed |")

    lines.extend(["", "## Schema errors", ""])
    if report["errors"]:
        for issue in report["errors"]:
            lines.append(
                f"- `{escaped(issue['dataset'])}:{escaped(issue['location'])}` "
                f"({escaped(issue['code'])}): {escaped(issue['message'])}"
            )
    else:
        lines.append("- None.")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    default_repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=default_repo, help="Skill bundle root.")
    parser.add_argument(
        "--evals",
        default="evals/evals.json",
        help="Skill-root-relative static Skill eval JSON.",
    )
    parser.add_argument(
        "--triggers",
        default="evals/trigger-prompts.csv",
        help="Skill-root-relative trigger CSV.",
    )
    parser.add_argument(
        "--commands",
        default="evals/deterministic-cases.json",
        help="Skill-root-relative deterministic command case JSON.",
    )
    parser.add_argument("--skip-commands", action="store_true", help="Validate datasets without executing commands.")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        repo = args.repo.resolve(strict=True)
    except OSError:
        print("Repository root does not exist.", file=sys.stderr)
        return 2
    if not repo.is_dir():
        print("Repository root must be a directory.", file=sys.stderr)
        return 2

    issues: list[Issue] = []
    eval_count = trigger_count = trigger_true = trigger_false = 0
    embedded_cases: list[Any] = []

    try:
        eval_path = _safe_repo_path(repo, args.evals, must_be="file")
    except ValueError as exc:
        issues.append(Issue("skill_evals", "unsafe_path", args.evals, str(exc)))
    else:
        eval_data = _load_json(eval_path, "skill_evals", args.evals, issues)
        if eval_data is not None:
            eval_count, embedded_cases = _validate_static_evals(eval_data, issues)

    try:
        trigger_path = _safe_repo_path(repo, args.triggers, must_be="file")
    except ValueError as exc:
        issues.append(Issue("trigger_dataset", "unsafe_path", args.triggers, str(exc)))
    else:
        trigger_count, trigger_true, trigger_false = _validate_triggers(
            trigger_path, args.triggers, issues
        )

    command_results: list[dict[str, Any]] = []
    if not args.skip_commands:
        command_cases = list(embedded_cases)
        try:
            command_path = _safe_repo_path(repo, args.commands, must_be="file")
        except ValueError as exc:
            issues.append(Issue("command_cases", "unsafe_path", args.commands, str(exc)))
        else:
            command_cases.extend(_load_command_file(command_path, args.commands, issues))
        parsed_cases = _parse_command_cases(command_cases, issues)
        command_results = [_run_command(case, repo) for case in parsed_cases]

    report = _build_report(
        issues,
        eval_count,
        trigger_count,
        trigger_true,
        trigger_false,
        command_results,
        args.skip_commands,
    )
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_markdown(report), end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
