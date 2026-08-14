#!/usr/bin/env python3
"""Validate the AI Project Copilot skill without third-party dependencies."""

from __future__ import annotations

import argparse
import json
import re
import stat
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
BRAND_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
REFERENCE_RE = re.compile(r"(?<![A-Za-z0-9_.-])((?:references|scripts|assets)/[A-Za-z0-9_.@/+\-]+)")
TEXT_SUFFIXES = {".md", ".txt", ".py", ".json", ".yaml", ".yml", ".svg"}
RUNTIME_DATABASE_SUFFIXES = (
    ".db",
    ".sqlite",
    ".sqlite3",
    "-journal",
    "-shm",
    "-wal",
)
FORBIDDEN_SKILL_DOCS = {"readme.md", "installation_guide.md", "quick_reference.md", "changelog.md"}
REQUIRED_BLUEPRINT_FIELDS = {"id", "name", "category", "pitch", "wow", "mvp", "modules", "tags", "complexity"}


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must start with YAML frontmatter delimited by ---")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("SKILL.md frontmatter closing delimiter is missing")
    values: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if not line or line[0].isspace() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def parse_openai_yaml(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def is_regular_file(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except OSError:
        return False
    return stat.S_ISREG(mode)


def validate(skill_dir: Path) -> list[str]:
    errors: list[str] = []
    skill_dir = skill_dir.expanduser()

    if not skill_dir.exists() or not skill_dir.is_dir():
        return [f"Skill directory does not exist: {skill_dir}"]
    if skill_dir.is_symlink():
        errors.append("Skill directory must not be a symlink for distributable packaging")

    entries = {path.name for path in skill_dir.iterdir()}
    if "SKILL.md" not in entries:
        case_matches = [name for name in entries if name.lower() == "skill.md"]
        if case_matches:
            errors.append(f"Entry file must be named exactly SKILL.md, not {case_matches[0]}")
        else:
            errors.append("SKILL.md is missing")
        return errors

    skill_path = skill_dir / "SKILL.md"
    try:
        skill_text = skill_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [f"Could not read SKILL.md as UTF-8: {exc}"]

    try:
        frontmatter = parse_frontmatter(skill_text)
    except ValueError as exc:
        errors.append(str(exc))
        frontmatter = {}

    name = frontmatter.get("name", "")
    description = frontmatter.get("description", "")
    compatibility = frontmatter.get("compatibility", "")

    if not name:
        errors.append("Frontmatter field `name` is required")
    elif len(name) > 64 or not NAME_RE.fullmatch(name):
        errors.append("`name` must be <=64 characters using lowercase letters, digits, and single hyphens")
    elif name != skill_dir.name:
        errors.append(f"Skill name {name!r} must match parent directory {skill_dir.name!r}")

    if not description:
        errors.append("Frontmatter field `description` is required")
    elif len(description) > 1024:
        errors.append(f"Description is {len(description)} characters; maximum is 1024")
    elif "use this skill" not in description.lower() or "do not use" not in description.lower():
        errors.append("Description should state both positive trigger intent and a negative boundary")

    if compatibility and len(compatibility) > 500:
        errors.append("Compatibility field exceeds 500 characters")

    line_count = len(skill_text.splitlines())
    if line_count > 500:
        errors.append(f"SKILL.md has {line_count} lines; keep it at or below 500")

    for forbidden in FORBIDDEN_SKILL_DOCS:
        if any(path.name.lower() == forbidden for path in skill_dir.iterdir()):
            errors.append(f"Auxiliary document should remain outside the skill folder: {forbidden}")

    total_size = 0
    all_files: list[Path] = []
    for path in sorted(skill_dir.rglob("*")):
        relative = path.relative_to(skill_dir)
        if any(part in {"__pycache__", ".DS_Store"} for part in relative.parts):
            # Generated/runtime artifacts are ignored by validation and packaging.
            # This keeps validate -> test -> package workflows stable after Python
            # has created __pycache__ directories locally.
            continue
        if path.is_symlink():
            errors.append(f"Symlink is not allowed in the distributable skill: {relative}")
            continue
        if relative.name.casefold().endswith(RUNTIME_DATABASE_SUFFIXES):
            # SQLite ledgers and sidecars are local runtime state, not assets.
            continue
        if path.is_dir():
            continue
        if not is_regular_file(path):
            errors.append(f"Special file is not allowed: {relative}")
            continue
        try:
            size = path.stat().st_size
        except OSError as exc:
            errors.append(f"Could not stat {relative}: {exc}")
            continue
        total_size += size
        all_files.append(path)
        if size > 5_000_000:
            errors.append(f"File exceeds 5 MB safety limit: {relative}")
        if path.suffix.lower() in TEXT_SUFFIXES:
            try:
                path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                errors.append(f"Text file is not valid UTF-8: {relative}: {exc}")

    if total_size > 20_000_000:
        errors.append("Skill exceeds the 20 MB repository safety limit")

    for match in sorted(set(REFERENCE_RE.findall(skill_text))):
        target = skill_dir / match
        if not target.exists():
            errors.append(f"SKILL.md references a missing path: {match}")

    for script in sorted((skill_dir / "scripts").glob("*.py")):
        try:
            compile(script.read_text(encoding="utf-8"), str(script), "exec")
        except (OSError, UnicodeError, SyntaxError) as exc:
            errors.append(f"Python script does not compile: {script.name}: {exc}")

    openai_path = skill_dir / "agents" / "openai.yaml"
    if not openai_path.exists():
        errors.append("agents/openai.yaml is missing")
    else:
        try:
            openai_values = parse_openai_yaml(openai_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            errors.append(f"Could not read agents/openai.yaml: {exc}")
            openai_values = {}
        for field in ("display_name", "short_description", "default_prompt"):
            if not openai_values.get(field):
                errors.append(f"agents/openai.yaml is missing `{field}`")
        short_description = openai_values.get("short_description", "")
        if len(short_description) > 80:
            errors.append("openai.yaml short_description should be <=80 characters")
        if name and f"${name}" not in openai_values.get("default_prompt", ""):
            errors.append("openai.yaml default_prompt should explicitly invoke the skill name")
        brand = openai_values.get("brand_color", "")
        if brand and not BRAND_RE.fullmatch(brand):
            errors.append("openai.yaml brand_color must be a six-digit hex color")
        for icon_field in ("icon_small", "icon_large"):
            icon = openai_values.get(icon_field, "")
            if icon:
                icon_path = skill_dir / icon.removeprefix("./")
                if not icon_path.exists():
                    errors.append(f"openai.yaml references a missing {icon_field}: {icon}")
                elif icon_path.suffix.lower() == ".svg":
                    try:
                        ET.parse(icon_path)
                    except (ET.ParseError, OSError) as exc:
                        errors.append(f"Invalid SVG for {icon_field}: {exc}")

    blueprint_path = skill_dir / "references" / "blueprints.json"
    catalog_path = skill_dir / "references" / "showcase-projects.md"
    try:
        blueprints = json.loads(blueprint_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"Could not parse references/blueprints.json: {exc}")
        blueprints = []

    if not isinstance(blueprints, list) or len(blueprints) != 24:
        errors.append("Blueprint catalog must contain exactly 24 project objects")
    else:
        ids: set[str] = set()
        try:
            catalog_text = catalog_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            catalog_text = ""
        for index, item in enumerate(blueprints):
            if not isinstance(item, dict):
                errors.append(f"Blueprint #{index + 1} is not an object")
                continue
            missing = REQUIRED_BLUEPRINT_FIELDS - set(item)
            if missing:
                errors.append(f"Blueprint {item.get('id', index)!r} missing fields: {sorted(missing)}")
            item_id = str(item.get("id", ""))
            if not NAME_RE.fullmatch(item_id):
                errors.append(f"Invalid blueprint id: {item_id!r}")
            if item_id in ids:
                errors.append(f"Duplicate blueprint id: {item_id}")
            ids.add(item_id)
            if item.get("complexity") not in {"small", "medium", "large"}:
                errors.append(f"Invalid complexity for blueprint {item_id}")
            if str(item.get("name", "")) not in catalog_text:
                errors.append(f"Human-readable catalog is missing blueprint name: {item.get('name')}")

    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate an Agent Skill directory.")
    parser.add_argument("skill_dir", type=Path, help="Path to the skill folder.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    errors = validate(args.skill_dir)
    if errors:
        print(f"Validation failed with {len(errors)} issue(s):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"PASS: {args.skill_dir} is a valid AI Project Copilot skill bundle")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
