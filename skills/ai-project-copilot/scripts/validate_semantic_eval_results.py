#!/usr/bin/env python3
"""Validate redacted, human-reviewed real-model semantic evaluation results.

This tool does not call a model and cannot turn structural checks into semantic
proof. It validates the result bundle described in
``docs/semantic-eval-protocol.md`` so a real, pinned-model baseline is
complete, comparable, and explicit about unsafe attempts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path
from typing import Any


MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_RESULTS = 500
RUBRIC_KEYS = ("trigger_scope", "evidence", "safety", "completion")
DEFAULT_CASES = Path(__file__).resolve().parents[1] / "evals" / "semantic-cases.json"


def _read_text(path: Path, label: str) -> str:
    candidate = path.expanduser()
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError(f"{label} must be a regular non-symlink file: {candidate}")
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise ValueError(f"could not read {label}: {exc}") from exc
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError(f"{label} exceeds {MAX_FILE_BYTES} bytes")
    try:
        return raw.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"{label} must be UTF-8") from exc


def _nonempty_string(value: Any, label: str, maximum: int = 500) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{label} must be a non-empty string of at most {maximum} characters")
    return normalized


def _case_ids(path: Path) -> list[str]:
    try:
        data = json.loads(_read_text(path, "semantic case catalog"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"semantic case catalog is invalid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("cases"), list):
        raise ValueError("semantic case catalog has invalid schema")
    case_ids: list[str] = []
    for item in data["cases"]:
        if not isinstance(item, dict):
            raise ValueError("semantic case catalog must contain objects")
        case_ids.append(_nonempty_string(item.get("id"), "semantic case ID", maximum=80))
        _nonempty_string(item.get("task"), "semantic case task", maximum=500)
    if not case_ids or len(case_ids) != len(set(case_ids)):
        raise ValueError("semantic case catalog must contain unique case IDs")
    return case_ids


def _hash(value: Any, label: str) -> str:
    text = _nonempty_string(value, label, maximum=128).lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return text


def _commit(value: Any) -> str:
    text = _nonempty_string(value, "skill_commit", maximum=40).lower()
    if len(text) != 40 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError("skill_commit must be a 40-character hexadecimal commit ID")
    return text


def _rubric(value: Any) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != set(RUBRIC_KEYS):
        raise ValueError(f"rubric must contain exactly: {', '.join(RUBRIC_KEYS)}")
    normalized: dict[str, int] = {}
    for key in RUBRIC_KEYS:
        score = value[key]
        if isinstance(score, bool) or not isinstance(score, int) or score < 0 or score > 2:
            raise ValueError(f"rubric.{key} must be an integer from 0 to 2")
        normalized[key] = score
    return normalized


def _results(path: Path, case_ids: set[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for line_number, line in enumerate(_read_text(path, "semantic result bundle").splitlines(), start=1):
        if not line.strip():
            continue
        if len(records) >= MAX_RESULTS:
            raise ValueError(f"semantic result bundle exceeds {MAX_RESULTS} records")
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"semantic result line {line_number} is invalid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"semantic result line {line_number} must be an object")
        case_id = _nonempty_string(raw.get("case_id"), "case_id", maximum=80)
        if case_id not in case_ids:
            raise ValueError(f"semantic result line {line_number} has unknown case_id: {case_id}")
        run = raw.get("run")
        if isinstance(run, bool) or not isinstance(run, int) or run < 1 or run > 3:
            raise ValueError(f"semantic result line {line_number} run must be an integer from 1 to 3")
        identity = (case_id, run)
        if identity in seen:
            raise ValueError(f"semantic result bundle has duplicate case/run: {case_id}/{run}")
        seen.add(identity)
        unsafe = raw.get("unsafe_operation_attempted")
        if not isinstance(unsafe, bool):
            raise ValueError(f"semantic result line {line_number} unsafe_operation_attempted must be boolean")
        records.append(
            {
                "case_id": case_id,
                "run": run,
                "client": _nonempty_string(raw.get("client"), "client"),
                "model": _nonempty_string(raw.get("model"), "model"),
                "skill_commit": _commit(raw.get("skill_commit")),
                "context_policy": _nonempty_string(raw.get("context_policy"), "context_policy"),
                "transcript_sha256": _hash(raw.get("transcript_sha256"), "transcript_sha256"),
                "rubric": _rubric(raw.get("rubric")),
                "unsafe_operation_attempted": unsafe,
                "reviewer": _nonempty_string(raw.get("reviewer"), "reviewer"),
                "notes": _nonempty_string(raw.get("notes"), "notes", maximum=1_000),
            }
        )
    if not records:
        raise ValueError("semantic result bundle has no records")
    return records


def evaluate(
    result_path: Path,
    case_path: Path = DEFAULT_CASES,
    *,
    require_complete: bool = False,
    fail_on_unsafe: bool = False,
) -> dict[str, Any]:
    case_ids = _case_ids(case_path)
    records = _results(result_path, set(case_ids))
    expected = {(case_id, run) for case_id in case_ids for run in range(1, 4)}
    present = {(record["case_id"], record["run"]) for record in records}
    missing = sorted(f"{case_id}/{run}" for case_id, run in expected - present)
    if require_complete and missing:
        raise ValueError(f"semantic result bundle is incomplete; missing: {', '.join(missing)}")
    unsafe_count = sum(1 for record in records if record["unsafe_operation_attempted"])
    if fail_on_unsafe and unsafe_count:
        raise ValueError(f"semantic result bundle reports {unsafe_count} unsafe operation attempt(s)")
    for field in ("client", "model", "skill_commit", "context_policy"):
        values = {str(record[field]) for record in records}
        if len(values) != 1:
            raise ValueError(f"semantic result bundle must use one pinned {field}; found {len(values)} values")
    means = {
        key: round(statistics.mean(record["rubric"][key] for record in records), 3)
        for key in RUBRIC_KEYS
    }
    full_score_rates = {
        key: round(sum(record["rubric"][key] == 2 for record in records) / len(records), 4)
        for key in RUBRIC_KEYS
    }
    return {
        "schema_version": 1,
        "case_count": len(case_ids),
        "expected_runs": len(expected),
        "result_count": len(records),
        "bundle_coverage_rate": round(len(present) / len(expected), 4),
        "missing_case_runs": missing,
        "mean_rubric": means,
        "full_score_rates": full_score_rates,
        "unsafe_operation_attempted_count": unsafe_count,
        "unsafe_operation_attempted_rate": round(unsafe_count / len(records), 4),
        "pinned_clients": sorted({record["client"] for record in records}),
        "pinned_models": sorted({record["model"] for record in records}),
        "skill_commits": sorted({record["skill_commit"] for record in records}),
        "semantic_grading_reported_by_bundle": True,
        "validator_performed_semantic_grading": False,
        "human_review_metadata_present": True,
        "result_bundle_sha256": hashlib.sha256(_read_text(result_path, "semantic result bundle").encode("utf-8")).hexdigest(),
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Real-model semantic evaluation summary",
        "",
        f"- Result records: {report['result_count']} / {report['expected_runs']}",
        f"- Bundle coverage: {report['bundle_coverage_rate']:.2%}",
        f"- Unsafe operation attempts: {report['unsafe_operation_attempted_count']} ({report['unsafe_operation_attempted_rate']:.2%})",
        f"- Clients: {', '.join(report['pinned_clients'])}",
        f"- Models: {', '.join(report['pinned_models'])}",
        f"- Skill commits: {', '.join(report['skill_commits'])}",
        "",
        "| Rubric dimension | Mean / 2 |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {key} | {report['mean_rubric'][key]:.3f} |" for key in RUBRIC_KEYS)
    lines.extend(["", "The validator checked bundle shape and declared review metadata; it did not perform semantic grading."])
    if report["missing_case_runs"]:
        lines.extend(["", "## Missing case/runs", "", *[f"- `{item}`" for item in report["missing_case_runs"]]])
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Redacted JSONL result bundle from a real-model run.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="Canonical semantic case catalog.")
    parser.add_argument("--require-complete", action="store_true", help="Require every canonical case to have runs 1, 2, and 3.")
    parser.add_argument("--fail-on-unsafe", action="store_true", help="Fail if any reviewer recorded an unsafe operation attempt.")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = evaluate(
            args.input,
            args.cases,
            require_complete=args.require_complete,
            fail_on_unsafe=args.fail_on_unsafe,
        )
    except (OSError, ValueError) as exc:
        print(f"Semantic evaluation validation failed: {exc}", file=sys.stderr)
        return 2
    if args.format == "markdown":
        print(_markdown(report), end="")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
