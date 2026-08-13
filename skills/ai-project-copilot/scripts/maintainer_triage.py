#!/usr/bin/env python3
"""Deterministic pre-triage for open-source GitHub issues.

This helper never writes to GitHub. It turns issue text into a reviewable
suggestion so maintainers can apply labels consistently before a human decides.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

SECURITY = (
    "security", "vulnerability", "vulnerable", "cve", "rce", "xss",
    "csrf", "secret", "credential", "token leak", "auth bypass",
)
BUG = (
    "bug", "crash", "error", "exception", "regression", "broken", "fails",
    "failure", "incorrect", "unexpected", "does not work", "doesn't work",
)
DOCS = (
    "documentation", "docs", "readme", "typo", "spelling", "example",
    "tutorial", "guide",
)
FEATURE = (
    "feature", "request", "enhancement", "support for", "add support",
    "would like", "proposal", "improve",
)
REPRO = (
    "steps to reproduce", "reproduce", "reproduction", "minimal example",
    "mre", "traceback", "stack trace", "logs", "log output",
)
HIGH_IMPACT = (
    "data loss", "corruption", "blocking", "blocks release", "production down",
    "cannot start", "won't start", "cannot launch",
)
STARTER = (
    "typo", "spelling", "readme", "docs", "documentation", "small test",
    "test coverage", "example",
)


@dataclass(frozen=True)
class TriageResult:
    labels: list[str]
    priority: str
    difficulty: str
    confidence: float
    needs: list[str]
    reasons: list[str]


def _contains(text: str, terms: Iterable[str]) -> list[str]:
    haystack = text.casefold()
    return [term for term in terms if term in haystack]


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text, flags=re.UNICODE))


def triage_issue(title: str, body: str) -> TriageResult:
    text = f"{title}\n{body}".strip()
    labels: list[str] = []
    reasons: list[str] = []
    needs: list[str] = []

    security_hits = _contains(text, SECURITY)
    bug_hits = _contains(text, BUG)
    docs_hits = _contains(text, DOCS)
    feature_hits = _contains(text, FEATURE)
    repro_hits = _contains(text, REPRO)
    high_hits = _contains(text, HIGH_IMPACT)
    starter_hits = _contains(text, STARTER)

    if security_hits:
        labels.append("security")
        reasons.append(f"security signals: {', '.join(security_hits[:3])}")

    if bug_hits:
        labels.append("bug")
        reasons.append(f"bug signals: {', '.join(bug_hits[:3])}")

    if docs_hits:
        labels.append("documentation")
        reasons.append(f"documentation signals: {', '.join(docs_hits[:3])}")

    if feature_hits and "bug" not in labels:
        labels.append("enhancement")
        reasons.append(f"feature signals: {', '.join(feature_hits[:3])}")

    if "bug" in labels and not repro_hits:
        labels.append("needs-reproduction")
        needs.append("Add deterministic reproduction steps, logs, or a minimal example.")
        reasons.append("bug report has no explicit reproduction evidence")

    if not title.strip():
        needs.append("Add a concise issue title.")

    if _word_count(body) < 12:
        needs.append("Add expected behavior, actual behavior, and environment details.")

    if "security" in labels or high_hits:
        priority = "high"
        labels.append("priority:high")
        if high_hits:
            reasons.append(f"high-impact signals: {', '.join(high_hits[:3])}")
    elif "bug" in labels:
        priority = "normal"
    else:
        priority = "normal"

    if security_hits:
        difficulty = "advanced"
    elif docs_hits and not bug_hits:
        difficulty = "starter"
    elif starter_hits and not security_hits:
        difficulty = "starter"
    elif len(text) > 1800 or len(set(labels) & {"bug", "enhancement"}) > 1:
        difficulty = "advanced"
    else:
        difficulty = "moderate"

    if difficulty == "starter" and "security" not in labels:
        labels.append("good first issue")

    # Confidence is deliberately conservative and transparent.
    signal_groups = sum(bool(x) for x in (security_hits, bug_hits, docs_hits, feature_hits))
    confidence = min(0.95, 0.45 + 0.12 * signal_groups + (0.08 if repro_hits else 0))
    if not labels:
        labels.append("needs-triage")
        confidence = min(confidence, 0.55)
        reasons.append("no strong deterministic category signal")

    order = {
        "security": 0,
        "bug": 1,
        "documentation": 2,
        "enhancement": 3,
        "needs-reproduction": 4,
        "good first issue": 5,
        "priority:high": 6,
        "needs-triage": 7,
    }
    labels = sorted(dict.fromkeys(labels), key=lambda x: (order.get(x, 99), x))

    return TriageResult(
        labels=labels,
        priority=priority,
        difficulty=difficulty,
        confidence=round(confidence, 2),
        needs=needs,
        reasons=reasons,
    )


def _load_issue(path: Path) -> tuple[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("issue JSON must be an object")
    return str(data.get("title", "")), str(data.get("body", ""))


def _markdown(result: TriageResult) -> str:
    labels = ", ".join(f"`{label}`" for label in result.labels)
    lines = [
        "# Maintainer pre-triage",
        "",
        f"- Suggested labels: {labels}",
        f"- Priority: **{result.priority}**",
        f"- Difficulty: **{result.difficulty}**",
        f"- Confidence: **{result.confidence:.2f}**",
    ]
    if result.needs:
        lines.extend(["", "## Missing evidence"])
        lines.extend(f"- {item}" for item in result.needs)
    if result.reasons:
        lines.extend(["", "## Why"])
        lines.extend(f"- {item}" for item in result.reasons)
    lines.extend([
        "",
        "> This is a deterministic suggestion, not an autonomous GitHub action. "
        "A maintainer should review it before applying labels or closing an issue.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--issue-json", type=Path, help="JSON object with title/body fields")
    source.add_argument("--title", help="Issue title; use with --body")
    parser.add_argument("--body", default="", help="Issue body when --title is used")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()

    if args.issue_json:
        title, body = _load_issue(args.issue_json)
    else:
        title, body = args.title or "", args.body

    result = triage_issue(title, body)
    if args.format == "json":
        print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
    else:
        print(_markdown(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
