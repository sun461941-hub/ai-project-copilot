#!/usr/bin/env python3
"""Copy AI project documentation templates into a target repository safely."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

TEMPLATE_NAMES = (
    "project-brief.md",
    "architecture-decision.md",
    "demo-script.md",
    "readme-ai-section.md",
    "project-score.json",
)


def safe_directory(path: Path) -> None:
    current = path
    while True:
        # ``Path.exists()`` is false for a dangling symlink.  Check the link
        # itself so a not-yet-created target cannot redirect later writes.
        if current.is_symlink():
            raise ValueError(f"Refusing symlinked destination component: {current}")
        if current.parent == current:
            break
        current = current.parent


def initialize(repo: Path, output: Path, force: bool) -> dict[str, list[str]]:
    repo = repo.expanduser().resolve()
    if not repo.exists() or not repo.is_dir():
        raise ValueError(f"Repository directory does not exist: {repo}")

    candidate = output.expanduser() if output.is_absolute() else repo / output
    safe_directory(candidate)
    destination = candidate.resolve()
    try:
        destination.relative_to(repo)
    except ValueError as exc:
        raise ValueError(f"Destination must remain inside repository: {candidate}") from exc
    destination.mkdir(parents=True, exist_ok=True)

    template_dir = Path(__file__).resolve().parents[1] / "assets" / "templates"
    created: list[str] = []
    skipped: list[str] = []

    for name in TEMPLATE_NAMES:
        source = template_dir / name
        target = destination / name
        if target.exists() and not force:
            skipped.append(str(target.relative_to(repo)))
            continue
        if target.is_symlink():
            raise ValueError(f"Refusing to overwrite symlink: {target}")
        shutil.copyfile(source, target)
        created.append(str(target.relative_to(repo)))

    return {"created": created, "skipped": skipped}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Initialize docs/ai-project from bundled templates without overwriting by default."
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Target repository.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/ai-project"),
        help="Destination relative to the target repository.",
    )
    parser.add_argument("--force", action="store_true", help="Replace existing regular files.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = initialize(args.repo, args.output, args.force)
    except (OSError, ValueError) as exc:
        print(f"Initialization failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for path in result["created"]:
            print(f"created  {path}")
        for path in result["skipped"]:
            print(f"skipped  {path} (already exists)")
        if not result["created"] and not result["skipped"]:
            print("No templates were processed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
