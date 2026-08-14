#!/usr/bin/env python3
"""Check whether a fix/decline/escalate PR review loop has converged."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

DECISIONS = {"fix", "decline", "escalate"}
STATUSES = {"open", "resolved"}


@dataclass(frozen=True)
class ThreadState:
    id: str
    decision: str
    status: str
    evidence: str
    reply_sha: str
    owner: str


@dataclass(frozen=True)
class ConvergenceReport:
    total_threads: int
    resolved_threads: int
    agent_actionable_open: list[str]
    human_handoffs: list[str]
    audit_warnings: list[str]
    blockers: list[str]
    ready_for_rereview: bool


def load_threads(path: Path) -> list[ThreadState]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("threads", []) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("review state JSON must be a list or object with a threads list")
    threads: list[ThreadState] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"thread #{index} must be an object")
        thread_id = str(item.get("id") or f"thread-{index}").strip() or f"thread-{index}"
        if thread_id in seen_ids:
            raise ValueError(f"duplicate review thread id: {thread_id}")
        seen_ids.add(thread_id)
        threads.append(ThreadState(
            id=thread_id,
            decision=str(item.get("decision", "")).casefold(),
            status=str(item.get("status", "open")).casefold(),
            evidence=str(item.get("evidence", "")).strip(),
            reply_sha=str(item.get("reply_sha", "")).strip(),
            owner=str(item.get("owner", "")).strip(),
        ))
    return threads


def analyze(threads: list[ThreadState]) -> ConvergenceReport:
    blockers: list[str] = []
    actionable: list[str] = []
    handoffs: list[str] = []
    warnings: list[str] = []
    resolved = 0
    if not threads:
        blockers.append("no review threads supplied; convergence cannot be demonstrated from empty state")

    seen_ids: set[str] = set()
    for thread in threads:
        if thread.id in seen_ids:
            blockers.append(f"{thread.id}: duplicate review thread id makes the audit trail ambiguous")
            continue
        seen_ids.add(thread.id)
        if thread.decision not in DECISIONS:
            blockers.append(f"{thread.id}: invalid or missing decision `{thread.decision}`")
            continue
        if thread.status not in STATUSES:
            blockers.append(f"{thread.id}: invalid status `{thread.status}`")
            continue
        if not thread.evidence:
            if thread.decision in {"decline", "escalate"}:
                blockers.append(f"{thread.id}: `{thread.decision}` decision requires recorded evidence/rationale")
            else:
                warnings.append(f"{thread.id}: decision has no recorded evidence/rationale")
        if thread.decision == "escalate" and not thread.owner:
            blockers.append(f"{thread.id}: escalation has no human owner/handoff")
        if thread.status == "resolved":
            resolved += 1
            if thread.decision == "fix" and not thread.reply_sha:
                warnings.append(f"{thread.id}: resolved fix has no pushed commit SHA for audit trail")
            continue

        if thread.decision in {"fix", "decline"}:
            actionable.append(thread.id)
            blockers.append(f"{thread.id}: `{thread.decision}` thread is still open and awaits agent/maintainer action")
        elif thread.decision == "escalate":
            if thread.owner:
                handoffs.append(f"{thread.id} → {thread.owner}")

    ready = not blockers
    return ConvergenceReport(
        total_threads=len(threads),
        resolved_threads=resolved,
        agent_actionable_open=actionable,
        human_handoffs=handoffs,
        audit_warnings=warnings,
        blockers=blockers,
        ready_for_rereview=ready,
    )


def markdown(report: ConvergenceReport) -> str:
    lines = [
        "# Review convergence gate", "",
        f"- Total threads: **{report.total_threads}**",
        f"- Resolved: **{report.resolved_threads}**",
        f"- Ready to re-request review: **{'yes' if report.ready_for_rereview else 'no'}**",
    ]
    for title, values in (
        ("Agent-actionable open threads", report.agent_actionable_open),
        ("Explicit human handoffs", report.human_handoffs),
        ("Blockers", report.blockers),
        ("Audit warnings", report.audit_warnings),
    ):
        lines.extend(["", f"## {title}"])
        lines.extend(f"- {item}" for item in values)
        if not values:
            lines.append("- None")
    lines.extend([
        "",
        "> Convergence means every agent-actionable thread is handled and every escalation has an explicit human owner. It does not mean the PR is automatically safe to merge.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threads-json", type=Path, required=True)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    try:
        report = analyze(load_threads(args.threads_json))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    if args.format == "json":
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(markdown(report), end="")
    return 0 if report.ready_for_rereview else 1


if __name__ == "__main__":
    raise SystemExit(main())
