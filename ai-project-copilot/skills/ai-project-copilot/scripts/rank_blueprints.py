#!/usr/bin/env python3
"""Rank bundled AI project blueprints from explicit priorities and constraints."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

COMPLEXITY = {"small": 1, "medium": 2, "large": 3}


def csv_set(value: str) -> set[str]:
    return {part.strip().lower() for part in value.split(",") if part.strip()}


def load_blueprints() -> list[dict[str, Any]]:
    path = Path(__file__).resolve().parents[1] / "references" / "blueprints.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not load blueprint catalog: {exc}") from exc
    if not isinstance(data, list):
        raise SystemExit("Blueprint catalog must contain a JSON array.")
    return data


def rank(
    blueprints: list[dict[str, Any]],
    priorities: set[str],
    constraints: set[str],
    max_complexity: str,
) -> list[dict[str, Any]]:
    max_level = COMPLEXITY[max_complexity]
    ranked: list[dict[str, Any]] = []

    for blueprint in blueprints:
        level = COMPLEXITY.get(str(blueprint.get("complexity", "large")), 3)
        if level > max_level:
            continue

        tags = {str(tag).lower() for tag in blueprint.get("tags", [])}
        modules = {str(module).lower() for module in blueprint.get("modules", [])}
        searchable = tags | modules | {
            str(blueprint.get("category", "")).lower().replace(" ", "-"),
            str(blueprint.get("id", "")).lower(),
        }

        matched_priorities = sorted(priorities & searchable)
        matched_constraints = sorted(constraints & searchable)
        score = 4 * len(matched_priorities) + 3 * len(matched_constraints)

        # Useful default signal when a broad request provides no tags.
        if not priorities and not constraints:
            score += int("visual-demo" in tags) + int("open-source" in tags)

        # Prefer a smaller first vertical slice when relevance is equal.
        score += 4 - level

        item = dict(blueprint)
        item["score"] = score
        item["matched_priorities"] = matched_priorities
        item["matched_constraints"] = matched_constraints
        ranked.append(item)

    ranked.sort(
        key=lambda item: (
            -int(item["score"]),
            COMPLEXITY.get(str(item.get("complexity", "large")), 3),
            str(item.get("name", "")).lower(),
        )
    )
    return ranked


def print_table(items: list[dict[str, Any]]) -> None:
    if not items:
        print("No blueprints satisfy the selected complexity limit.")
        return

    width = max(len(str(item["name"])) for item in items)
    for index, item in enumerate(items, start=1):
        matches = item["matched_priorities"] + item["matched_constraints"]
        match_text = ", ".join(matches) if matches else "general showcase fit"
        print(
            f"{index:>2}. {item['name']:<{width}}  "
            f"score={item['score']:>2}  complexity={item['complexity']:<6}  "
            f"matches={match_text}"
        )
        print(f"    {item['pitch']}")
        print(f"    Wow: {item['wow']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rank the 24 bundled AI showcase blueprints."
    )
    parser.add_argument(
        "--priorities",
        default="",
        help="Comma-separated desired tags, for example local-first,visual-demo,developer-tools.",
    )
    parser.add_argument(
        "--constraints",
        default="",
        help="Comma-separated hard context signals, for example android,privacy,offline.",
    )
    parser.add_argument(
        "--max-complexity",
        choices=tuple(COMPLEXITY),
        default="large",
        help="Exclude ideas above this implementation size.",
    )
    parser.add_argument("--limit", type=int, default=5, help="Number of results to print.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit < 1 or args.limit > 24:
        print("--limit must be between 1 and 24", file=sys.stderr)
        return 2

    items = rank(
        load_blueprints(),
        csv_set(args.priorities),
        csv_set(args.constraints),
        args.max_complexity,
    )[: args.limit]

    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
    else:
        print_table(items)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
