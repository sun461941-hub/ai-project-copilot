#!/usr/bin/env python3
"""Local CLI adapter for the AI Project Copilot multi-interface core."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from project_copilot_core import CopilotEngine, CopilotError, ExecutionPolicy, invoke


def _print(value: Any, pretty: bool) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2 if pretty else None, sort_keys=pretty))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI Project Copilot multi-interface CLI")
    parser.add_argument("--timeout", type=float, default=120.0, help="per-helper timeout in seconds")
    parser.add_argument("--max-capture-bytes", type=int, default=2 * 1024 * 1024)
    parser.add_argument("--compact", action="store_true", help="emit compact JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("capabilities", help="list callable capabilities")

    route = sub.add_parser("route", help="route a natural-language task")
    route.add_argument("prompt")

    analyze = sub.add_parser("analyze", help="map repository context")
    analyze.add_argument("repo")
    analyze.add_argument("--task", required=True)

    review = sub.add_parser("review", help="analyze change risk")
    review.add_argument("repo", nargs="?")
    review.add_argument("--base", default="main")
    review.add_argument("--head", default="HEAD")
    review.add_argument("--patch")

    security = sub.add_parser("security", help="run supply-chain and MCP security scans")
    security.add_argument("repo")

    release = sub.add_parser("release", help="build release intelligence")
    release.add_argument("repo")
    release.add_argument("--from-ref", required=True)
    release.add_argument("--current-version", required=True)

    triage = sub.add_parser("triage", help="run issue pre-triage")
    triage.add_argument("issue_json")

    sub.add_parser("evals", help="run bundled deterministic Skill evals")

    run = sub.add_parser("run", help="route one goal and execute available deterministic stages")
    run.add_argument("goal")
    run.add_argument("--repo")
    run.add_argument("--base", default="main")
    run.add_argument("--head", default="HEAD")
    run.add_argument("--from-ref")
    run.add_argument("--current-version")
    run.add_argument("--issue-json")
    run.add_argument("--include-evals", action="store_true")

    raw = sub.add_parser("invoke", help="invoke a named capability using a JSON request")
    raw.add_argument("capability")
    raw.add_argument("--arguments", default="{}", help="JSON object")
    raw.add_argument("--request", type=Path, help="read JSON object from a file instead")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    engine = CopilotEngine(
        policy=ExecutionPolicy(timeout_seconds=args.timeout, max_capture_bytes=args.max_capture_bytes)
    )
    try:
        if args.command == "capabilities":
            _print({"capabilities": engine.capability_specs()}, not args.compact)
            return 0
        if args.command == "route":
            capability, payload = "route", {"prompt": args.prompt}
        elif args.command == "analyze":
            capability, payload = "analyze_repository", {"repo": args.repo, "task": args.task}
        elif args.command == "review":
            capability = "review_changes"
            payload = {"base": args.base, "head": args.head}
            if args.patch:
                payload["patch"] = args.patch
            elif args.repo:
                payload["repo"] = args.repo
            else:
                raise CopilotError("review requires repo or --patch")
        elif args.command == "security":
            capability, payload = "scan_security", {"repo": args.repo}
        elif args.command == "release":
            capability, payload = "release_readiness", {
                "repo": args.repo,
                "from_ref": args.from_ref,
                "current_version": args.current_version,
            }
        elif args.command == "triage":
            capability, payload = "maintainer_triage", {"issue_json": args.issue_json}
        elif args.command == "evals":
            capability, payload = "run_evals", {}
        elif args.command == "run":
            capability = "copilot_run"
            payload = {
                "goal": args.goal,
                "base": args.base,
                "head": args.head,
                "include_evals": args.include_evals,
            }
            for key in ("repo", "from_ref", "current_version", "issue_json"):
                value = getattr(args, key)
                if value is not None:
                    payload[key] = value
        else:
            capability = args.capability
            if args.request:
                payload = json.loads(args.request.read_text(encoding="utf-8"))
            else:
                payload = json.loads(args.arguments)
            if not isinstance(payload, dict):
                raise CopilotError("request arguments must decode to a JSON object")

        result = invoke(engine, capability, payload)
        _print(result, not args.compact)
        return 0 if result.get("status") == "completed" else 2
    except (CopilotError, json.JSONDecodeError, OSError) as exc:
        _print({"status": "error", "error": str(exc)}, not args.compact)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
