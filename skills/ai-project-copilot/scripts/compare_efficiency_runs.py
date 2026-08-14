#!/usr/bin/env python3
"""Compare paired aligned gateway-format records without hiding failures or retries."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from dataclasses import asdict, dataclass
from decimal import Decimal, DecimalException, localcontext
from pathlib import Path
from typing import Any, Mapping, Sequence


MAX_INPUT_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class RunRecord:
    task_id: str
    final_status: str
    success: bool
    total_tokens: int
    total_cost_nano_usd: int
    e2e_ms: float
    ttft_ms: float | None
    attempts: int
    request_template_sha256: str
    quality_policy_sha256: str
    pricing_policy_sha256: str


@dataclass(frozen=True)
class EffectReport:
    schema_version: str
    paired_tasks: int
    task_ids_aligned: bool
    baseline_successes: int
    candidate_successes: int
    success_regressions: list[str]
    quality_policy_aligned: bool
    quality_policy_mismatches: list[str]
    request_templates_aligned: bool
    request_template_mismatches: list[str]
    pricing_policy_aligned: bool
    pricing_policy_mismatches: list[str]
    baseline_total_tokens: int
    candidate_total_tokens: int
    token_savings_percent: float | None
    baseline_total_cost_nano_usd: int
    candidate_total_cost_nano_usd: int
    cost_savings_percent: float | None
    baseline_total_e2e_ms: float
    candidate_total_e2e_ms: float
    e2e_latency_reduction_percent: float | None
    e2e_speedup: float | None
    baseline_ttft_median_ms: float | None
    candidate_ttft_median_ms: float | None
    ttft_reduction_percent: float | None
    baseline_attempts: int
    candidate_attempts: int
    safe_to_adopt: bool
    decision_reasons: list[str]


def _number(value: object, label: str, *, integer: bool = False) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    if integer:
        if not isinstance(value, int):
            raise ValueError(f"{label} must be an integer")
        if value < 0:
            raise ValueError(f"{label} must be non-negative")
        return value
    try:
        numeric = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite supported number") from exc
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError(f"{label} must be finite and non-negative")
    return numeric


def _attempt_count(value: object) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    raise ValueError("attempts must be an array or non-negative integer")


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{label} must be a 64-character SHA-256")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{label} must be hexadecimal") from exc
    return value.lower()


def normalize_record(payload: Mapping[str, Any], index: int) -> RunRecord:
    task_id = payload.get("task_id", payload.get("logical_request_hash"))
    if not isinstance(task_id, str) or not task_id:
        raise ValueError(f"record {index} requires task_id or logical_request_hash")
    status = payload.get("final_status")
    if not isinstance(status, str) or not status:
        raise ValueError(f"record {task_id} requires final_status")
    tokens = _number(payload.get("total_tokens"), f"{task_id}.total_tokens", integer=True)
    cost = _number(
        payload.get("total_cost_nano_usd"),
        f"{task_id}.total_cost_nano_usd",
        integer=True,
    )
    e2e = _number(payload.get("e2e_ms"), f"{task_id}.e2e_ms")
    raw_ttft = payload.get("ttft_ms")
    ttft = None if raw_ttft is None else float(_number(raw_ttft, f"{task_id}.ttft_ms"))
    return RunRecord(
        task_id,
        status,
        status == "success",
        int(tokens),
        int(cost),
        float(e2e),
        ttft,
        _attempt_count(payload.get("attempts", [])),
        _sha256(
            payload.get("request_template_sha256"),
            f"{task_id}.request_template_sha256",
        ),
        _sha256(payload.get("quality_policy_sha256"), f"{task_id}.quality_policy_sha256"),
        _sha256(payload.get("pricing_policy_sha256"), f"{task_id}.pricing_policy_sha256"),
    )


def _load_json_or_jsonl(path: Path) -> list[Mapping[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"input must be a regular, non-symlink file: {path}")
    data = path.read_bytes()
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError(f"input exceeds {MAX_INPUT_BYTES} bytes: {path}")
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"input is not UTF-8: {path}") from exc
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        parsed = None
    if isinstance(parsed, Mapping):
        records = parsed.get("runs")
        if records is None:
            records = [parsed]
    elif isinstance(parsed, list):
        records = parsed
    else:
        records = []
        for line_number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except (json.JSONDecodeError, RecursionError) as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
            records.append(item)
    if not isinstance(records, list) or not records:
        raise ValueError(f"input contains no run records: {path}")
    if any(not isinstance(item, Mapping) for item in records):
        raise ValueError(f"every run record must be a JSON object: {path}")
    return list(records)


def load_runs(path: Path) -> dict[str, RunRecord]:
    result: dict[str, RunRecord] = {}
    for index, payload in enumerate(_load_json_or_jsonl(path), 1):
        record = normalize_record(payload, index)
        if record.task_id in result:
            raise ValueError(f"duplicate task id in {path}: {record.task_id}")
        result[record.task_id] = record
    return result


def _decimal_ratio(numerator: int | float, denominator: int | float, label: str) -> float:
    try:
        with localcontext() as context:
            context.prec = 50
            result = Decimal(str(numerator)) / Decimal(str(denominator))
        converted = float(result)
    except (DecimalException, OverflowError, ValueError) as exc:
        raise ValueError(f"{label} exceeds the supported numeric range") from exc
    if not math.isfinite(converted):
        raise ValueError(f"{label} exceeds the supported numeric range")
    return converted


def _saving(baseline: int | float, candidate: int | float) -> float | None:
    if baseline == 0:
        return 0.0 if candidate == 0 else None
    try:
        with localcontext() as context:
            context.prec = 50
            percent = (
                Decimal(1) - Decimal(str(candidate)) / Decimal(str(baseline))
            ) * Decimal(100)
        converted = float(percent)
    except (DecimalException, OverflowError, ValueError) as exc:
        raise ValueError("saving percentage exceeds the supported numeric range") from exc
    if not math.isfinite(converted):
        raise ValueError("saving percentage exceeds the supported numeric range")
    return round(converted, 4)


def _speedup(baseline: int | float, candidate: int | float) -> float | None:
    if candidate == 0:
        return None
    return round(_decimal_ratio(baseline, candidate, "speedup"), 4)


def _finite_sum(values: Sequence[float], label: str) -> float:
    try:
        total = math.fsum(values)
    except OverflowError as exc:
        raise ValueError(f"{label} aggregate exceeds the supported numeric range") from exc
    if not math.isfinite(total):
        raise ValueError(f"{label} aggregate exceeds the supported numeric range")
    return total


def compare_runs(
    baseline: Mapping[str, RunRecord],
    candidate: Mapping[str, RunRecord],
) -> EffectReport:
    baseline_ids = set(baseline)
    candidate_ids = set(candidate)
    if baseline_ids != candidate_ids:
        missing = sorted(baseline_ids - candidate_ids)
        extra = sorted(candidate_ids - baseline_ids)
        raise ValueError(f"task IDs are not aligned; missing={missing}, extra={extra}")
    ids = sorted(baseline_ids)
    regressions = [
        task_id
        for task_id in ids
        if baseline[task_id].success and not candidate[task_id].success
    ]
    policy_mismatches = [
        task_id
        for task_id in ids
        if baseline[task_id].quality_policy_sha256
        != candidate[task_id].quality_policy_sha256
    ]
    request_mismatches = [
        task_id
        for task_id in ids
        if baseline[task_id].request_template_sha256
        != candidate[task_id].request_template_sha256
    ]
    pricing_mismatches = [
        task_id
        for task_id in ids
        if baseline[task_id].pricing_policy_sha256
        != candidate[task_id].pricing_policy_sha256
    ]
    baseline_successes = sum(record.success for record in baseline.values())
    candidate_successes = sum(record.success for record in candidate.values())
    baseline_tokens = sum(record.total_tokens for record in baseline.values())
    candidate_tokens = sum(record.total_tokens for record in candidate.values())
    baseline_cost = sum(record.total_cost_nano_usd for record in baseline.values())
    candidate_cost = sum(record.total_cost_nano_usd for record in candidate.values())
    baseline_e2e = _finite_sum(
        [record.e2e_ms for record in baseline.values()], "baseline E2E"
    )
    candidate_e2e = _finite_sum(
        [record.e2e_ms for record in candidate.values()], "candidate E2E"
    )
    baseline_ttft = [record.ttft_ms for record in baseline.values() if record.ttft_ms is not None]
    candidate_ttft = [record.ttft_ms for record in candidate.values() if record.ttft_ms is not None]
    baseline_ttft_median = (
        round(statistics.median(baseline_ttft), 3) if len(baseline_ttft) == len(ids) else None
    )
    candidate_ttft_median = (
        round(statistics.median(candidate_ttft), 3) if len(candidate_ttft) == len(ids) else None
    )
    reasons: list[str] = []
    if baseline_successes == 0:
        reasons.append("baseline contains no successful tasks")
    if candidate_successes == 0:
        reasons.append("candidate contains no successful tasks")
    if regressions:
        reasons.append("candidate regressed one or more baseline-successful tasks")
    if candidate_successes < baseline_successes:
        reasons.append("candidate success count is lower than baseline")
    if policy_mismatches:
        reasons.append("baseline and candidate did not use the same quality policy")
    if request_mismatches:
        reasons.append("baseline and candidate did not use the same frozen request template")
    if pricing_mismatches:
        reasons.append("baseline and candidate did not use the same price-card policy")
    token_savings = _saving(baseline_tokens, candidate_tokens)
    cost_savings = _saving(baseline_cost, candidate_cost)
    e2e_reduction = _saving(baseline_e2e, candidate_e2e)
    if token_savings is None or token_savings <= 0:
        reasons.append("candidate did not reduce aggregate total tokens")
    if cost_savings is None or cost_savings <= 0:
        reasons.append("candidate did not reduce aggregate price-card cost")
    if e2e_reduction is None or e2e_reduction <= 0:
        reasons.append("candidate did not reduce aggregate end-to-end latency")
    safe = (
        not regressions
        and not policy_mismatches
        and not request_mismatches
        and not pricing_mismatches
        and baseline_successes > 0
        and candidate_successes > 0
        and candidate_successes >= baseline_successes
    )
    if safe:
        reasons.insert(0, "recorded success signal did not regress; efficiency metrics remain separate")
    ttft_reduction = (
        _saving(baseline_ttft_median, candidate_ttft_median)
        if baseline_ttft_median is not None and candidate_ttft_median is not None
        else None
    )
    return EffectReport(
        "aipc.effects.v1",
        len(ids),
        True,
        baseline_successes,
        candidate_successes,
        regressions,
        not policy_mismatches,
        policy_mismatches,
        not request_mismatches,
        request_mismatches,
        not pricing_mismatches,
        pricing_mismatches,
        baseline_tokens,
        candidate_tokens,
        token_savings,
        baseline_cost,
        candidate_cost,
        cost_savings,
        round(baseline_e2e, 3),
        round(candidate_e2e, 3),
        e2e_reduction,
        _speedup(baseline_e2e, candidate_e2e),
        baseline_ttft_median,
        candidate_ttft_median,
        ttft_reduction,
        sum(record.attempts for record in baseline.values()),
        sum(record.attempts for record in candidate.values()),
        safe,
        reasons,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare aligned gateway-format records including failures and retries."
    )
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument(
        "--require-improvement",
        action="store_true",
        help="Also fail unless Token, cost, and E2E metrics all improve.",
    )
    return parser


def _emit(report: EffectReport, output_format: str) -> None:
    if output_format == "json":
        print(
            json.dumps(
                asdict(report),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
        )
        return
    def display(value: object, suffix: str = "") -> str:
        return "n/a" if value is None else f"{value}{suffix}"

    print("# Paired Efficiency Report")
    print(f"- paired tasks: `{report.paired_tasks}`")
    print(f"- safe by configured success gate: `{str(report.safe_to_adopt).lower()}`")
    print(f"- Token savings: `{display(report.token_savings_percent, '%')}`")
    print(f"- cost savings: `{display(report.cost_savings_percent, '%')}`")
    print(f"- E2E latency reduction: `{display(report.e2e_latency_reduction_percent, '%')}`")
    print(f"- E2E speedup: `{display(report.e2e_speedup, 'x')}`")
    print(f"- baseline median TTFT: `{display(report.baseline_ttft_median_ms, ' ms')}`")
    print(f"- candidate median TTFT: `{display(report.candidate_ttft_median_ms, ' ms')}`")
    print(f"- TTFT reduction: `{display(report.ttft_reduction_percent, '%')}`")
    for reason in report.decision_reasons:
        print(f"- {reason}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = compare_runs(load_runs(args.baseline), load_runs(args.candidate))
        _emit(report, args.format)
        if not report.safe_to_adopt:
            return 3
        if args.require_improvement and not all(
            value is not None and value > 0
            for value in (
                report.token_savings_percent,
                report.cost_savings_percent,
                report.e2e_latency_reduction_percent,
            )
        ):
            return 4
        return 0
    except (ValueError, OSError, OverflowError, ArithmeticError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
