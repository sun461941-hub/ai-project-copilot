#!/usr/bin/env python3
"""Create a deterministic, path-safe, single-root Agent Skill ZIP."""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import sys
import zipfile
from pathlib import Path

from validate_skill import validate

FIXED_TIME = (2026, 1, 1, 0, 0, 0)
SKIP_NAMES = {"__pycache__", ".DS_Store"}
RUNTIME_DATABASE_SUFFIXES = (
    ".db",
    ".sqlite",
    ".sqlite3",
    "-journal",
    "-shm",
    "-wal",
)


def within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def collect(skill_dir: Path) -> list[Path]:
    files: list[Path] = []
    normalized: set[str] = set()
    for path in sorted(skill_dir.rglob("*"), key=lambda item: item.as_posix().lower()):
        relative = path.relative_to(skill_dir)
        if any(part in SKIP_NAMES for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"Symlink is not allowed: {relative}")
        if path.is_dir():
            continue
        if relative.name.casefold().endswith(RUNTIME_DATABASE_SUFFIXES):
            # Local ledgers and SQLite sidecars are runtime state, never Skill assets.
            continue
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            raise ValueError(f"Special file is not allowed: {relative}")
        archive_name = f"{skill_dir.name}/{relative.as_posix()}"
        if archive_name.startswith("/") or ".." in Path(archive_name).parts or "\\" in archive_name:
            raise ValueError(f"Unsafe archive path: {archive_name}")
        folded = archive_name.casefold()
        if folded in normalized:
            raise ValueError(f"Duplicate normalized archive path: {archive_name}")
        normalized.add(folded)
        files.append(path)
    return files


def package(skill_dir: Path, output: Path, force: bool = False) -> tuple[str, int, int]:
    skill_dir = skill_dir.expanduser()
    if skill_dir.is_symlink():
        raise ValueError("Skill root must not be a symlink")
    skill_dir = skill_dir.resolve()
    errors = validate(skill_dir)
    if errors:
        raise ValueError("Skill validation failed:\n- " + "\n- ".join(errors))

    output = output.expanduser()
    output_parent = output.parent.resolve()
    output_parent.mkdir(parents=True, exist_ok=True)
    resolved_output = output_parent / output.name

    if within(resolved_output, skill_dir):
        raise ValueError("Output archive must be outside the skill directory")
    if resolved_output.is_symlink():
        raise ValueError("Refusing to write through a symlinked output path")
    if resolved_output.exists():
        if not force:
            raise FileExistsError(f"Output already exists: {resolved_output}")
        if not resolved_output.is_file():
            raise ValueError(f"Existing output is not a regular file: {resolved_output}")
        resolved_output.unlink()

    files = collect(skill_dir)
    try:
        with resolved_output.open("xb") as raw:
            with zipfile.ZipFile(raw, mode="w", compression=zipfile.ZIP_STORED, strict_timestamps=True) as archive:
                for path in files:
                    relative = path.relative_to(skill_dir).as_posix()
                    name = f"{skill_dir.name}/{relative}"
                    info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
                    info.create_system = 3
                    info.external_attr = (stat.S_IFREG | 0o644) << 16
                    info.compress_type = zipfile.ZIP_STORED
                    info.flag_bits |= 0x800
                    archive.writestr(info, path.read_bytes())
    except Exception:
        try:
            resolved_output.unlink()
        except OSError:
            pass
        raise

    digest = hashlib.sha256(resolved_output.read_bytes()).hexdigest()
    return digest, resolved_output.stat().st_size, len(files)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Package a validated Agent Skill deterministically.")
    parser.add_argument("skill_dir", type=Path, help="Skill folder to package.")
    parser.add_argument("--output", type=Path, required=True, help="Destination .zip path.")
    parser.add_argument("--force", action="store_true", help="Replace an existing regular archive.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        digest, size, count = package(args.skill_dir, args.output, args.force)
    except (OSError, ValueError) as exc:
        print(f"Packaging failed: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {args.output} ({count} files, {size} bytes)")
    print(f"SHA256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
