#!/usr/bin/env python3
"""Compact noisy tool/test output while preserving failures, summaries, and a raw hash."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

SUMMARY_PATTERNS = (
    re.compile(r"\b\d+\s+(?:passed|failed|error|errors|skipped|xfailed|xpassed)\b", re.I),
    re.compile(r"^Ran\s+\d+\s+tests?\b", re.I),
    re.compile(r"^(?:OK|FAILED)(?:\s|$)", re.I),
    re.compile(r"^Tests?:\s+\d+", re.I),
)
FAILURE_PATTERNS = (
    re.compile(r"^(?:FAILED|ERROR)\s+", re.I),
    re.compile(r"\b(?:AssertionError|Traceback \(most recent call last\)|Exception|Segmentation fault)\b", re.I),
    re.compile(r"^(?:fatal|error):", re.I),
    re.compile(r"\b(?:FAIL|FAILED|ERROR)\b", re.I),
)


@dataclass(frozen=True)
class CompactResult:
    raw_sha256: str
    raw_lines: int
    raw_chars: int
    compact_lines: int
    compact_chars: int
    char_reduction: float
    truncated: bool
    text: str


def _clip_line(line: str, limit: int = 1200) -> str:
    if len(line) <= limit:
        return line
    digest = hashlib.sha256(line.encode("utf-8", errors="replace")).hexdigest()[:12]
    return line[:limit] + f" … <line clipped sha256:{digest}>"


def _interesting_indices(lines: list[str]) -> set[int]:
    keep: set[int] = set()
    for index, line in enumerate(lines):
        if any(pattern.search(line) for pattern in SUMMARY_PATTERNS + FAILURE_PATTERNS):
            for nearby in range(max(0, index - 2), min(len(lines), index + 4)):
                keep.add(nearby)
    return keep


def compact_text(text: str, max_lines: int = 80) -> CompactResult:
    if max_lines < 8:
        raise ValueError("max_lines must be at least 8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    raw_lines_list = normalized.splitlines()
    raw_lines = len(raw_lines_list)
    raw_chars = len(normalized)
    raw_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    if raw_lines <= max_lines and all(len(line) <= 1200 for line in raw_lines_list):
        output = "\n".join(_clip_line(line) for line in raw_lines_list)
        if normalized.endswith("\n"):
            output += "\n"
        return CompactResult(raw_hash, raw_lines, raw_chars, len(output.splitlines()), len(output), 0.0, False, output)

    keep = _interesting_indices(raw_lines_list)
    head_count = min(8, max_lines // 4)
    tail_count = min(12, max_lines // 4)
    keep.update(range(min(head_count, raw_lines)))
    keep.update(range(max(0, raw_lines - tail_count), raw_lines))

    selected = sorted(keep)
    # If failure context alone exceeds the budget, retain the earliest and latest evidence.
    if len(selected) > max_lines - 3:
        head = selected[: max(1, (max_lines - 3) // 2)]
        tail = selected[-max(1, (max_lines - 3) - len(head)) :]
        selected = sorted(dict.fromkeys(head + tail))

    output_lines: list[str] = []
    previous = -2
    omitted_total = 0
    for index in selected:
        if previous >= 0 and index > previous + 1:
            gap = index - previous - 1
            omitted_total += gap
            output_lines.append(f"… <{gap} lines omitted> …")
        output_lines.append(_clip_line(raw_lines_list[index]))
        previous = index
    if selected:
        omitted_total += max(0, selected[0]) + max(0, raw_lines - selected[-1] - 1)
    else:
        omitted_total = raw_lines

    footer = f"[AIPC compacted output: raw_sha256={raw_hash} raw_lines={raw_lines} omitted_lines={omitted_total}]"
    output_lines.append(footer)
    if len(output_lines) > max_lines:
        output_lines = output_lines[: max_lines - 1] + [footer]
    output = "\n".join(output_lines) + "\n"
    compact_chars = len(output)
    reduction = 0.0 if raw_chars == 0 else max(0.0, 1.0 - compact_chars / raw_chars)
    return CompactResult(
        raw_sha256=raw_hash,
        raw_lines=raw_lines,
        raw_chars=raw_chars,
        compact_lines=len(output.splitlines()),
        compact_chars=compact_chars,
        char_reduction=round(reduction, 6),
        truncated=True,
        text=output,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input", type=Path, help="Read raw output from this file; otherwise stdin is used.")
    parser.add_argument("--max-lines", type=int, default=80)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()
    try:
        text = args.input.read_text(encoding="utf-8", errors="replace") if args.input else sys.stdin.read()
        result = compact_text(text, args.max_lines)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.format == "json":
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print(result.text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
