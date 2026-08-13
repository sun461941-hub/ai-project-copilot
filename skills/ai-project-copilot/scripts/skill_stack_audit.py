#!/usr/bin/env python3
"""Read-only inventory and overlap audit for locally installed Agent Skills."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "when", "use", "using",
    "skill", "agent", "agents", "user", "users", "into", "your", "you", "are", "to",
    "a", "an", "or", "of", "in", "on", "is", "be", "as", "by", "it", "any",
    "help", "helps", "do", "not", "will", "can", "want", "wants",
}
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]{2,}")


@dataclass(frozen=True)
class SkillRecord:
    name: str
    path: str
    description: str
    compatibility: str
    scripts: int
    references: int
    assets: int
    line_count: int
    warnings: list[str]


@dataclass(frozen=True)
class Overlap:
    left: str
    right: str
    score: float
    shared_terms: list[str]


@dataclass(frozen=True)
class StackReport:
    roots: list[str]
    skill_count: int
    skills: list[SkillRecord]
    duplicate_names: dict[str, list[str]]
    overlaps: list[Overlap]
    warnings: list[str]


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    lines = text[4:end].splitlines()
    result: dict[str, str] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line or line[0].isspace() or ":" not in line:
            index += 1
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        raw_value = value.strip()
        if raw_value in {">", ">-", ">+", "|", "|-", "|+"}:
            block: list[str] = []
            index += 1
            while index < len(lines):
                child = lines[index]
                if child and not child[0].isspace():
                    break
                block.append(child.strip())
                index += 1
            if raw_value.startswith(">"):
                # Trigger/overlap analysis needs the scalar content, not exact
                # YAML folding whitespace. Preserve paragraph boundaries lightly.
                paragraphs: list[str] = []
                current: list[str] = []
                for child in block:
                    if child:
                        current.append(child)
                    elif current:
                        paragraphs.append(" ".join(current))
                        current = []
                if current:
                    paragraphs.append(" ".join(current))
                result[key] = "\n".join(paragraphs)
            else:
                result[key] = "\n".join(block).rstrip("\n")
            continue
        result[key] = raw_value.strip('"').strip("'")
        index += 1
    return result


def _count_files(path: Path) -> int:
    if not path.exists() or not path.is_dir():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file() and not item.is_symlink())


def _tokens(text: str) -> set[str]:
    return {token for token in TOKEN_RE.findall(text.casefold()) if token not in STOPWORDS}


def _discover(root: Path, max_depth: int) -> list[Path]:
    root = root.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return []
    results: list[Path] = []
    base_depth = len(root.parts)
    for path in root.rglob("SKILL.md"):
        try:
            depth = len(path.parent.resolve().parts) - base_depth
        except OSError:
            continue
        if depth <= max_depth and path.is_file() and not path.is_symlink():
            results.append(path)
    return sorted(set(results))


def scan(roots: list[Path], max_depth: int = 4, overlap_threshold: float = 0.42) -> StackReport:
    resolved_roots: list[str] = []
    skill_paths: list[Path] = []
    warnings: list[str] = []
    for root in roots:
        expanded = root.expanduser().resolve()
        resolved_roots.append(str(expanded))
        if not expanded.exists():
            warnings.append(f"root not found: {expanded}")
            continue
        skill_paths.extend(_discover(expanded, max_depth))

    records: list[SkillRecord] = []
    tokens_by_path: dict[str, set[str]] = {}
    for skill_path in sorted(set(skill_paths)):
        try:
            text = skill_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            warnings.append(f"could not read {skill_path}: {exc}")
            continue
        values = _frontmatter(text)
        name = values.get("name") or skill_path.parent.name
        description = values.get("description", "")
        compatibility = values.get("compatibility", "")
        local_warnings: list[str] = []
        if not values:
            local_warnings.append("missing or malformed YAML frontmatter")
        if not NAME_RE.fullmatch(name):
            local_warnings.append("name is not portable lowercase-hyphen format")
        if not description:
            local_warnings.append("description is empty; discovery/triggering may be weak")
        elif len(description) > 1024:
            local_warnings.append("description exceeds 1024 characters")
        line_count = len(text.splitlines())
        if line_count > 500:
            local_warnings.append("SKILL.md exceeds 500 lines; consider progressive disclosure")
        record = SkillRecord(
            name=name,
            path=str(skill_path.parent.resolve()),
            description=description,
            compatibility=compatibility,
            scripts=_count_files(skill_path.parent / "scripts"),
            references=_count_files(skill_path.parent / "references"),
            assets=_count_files(skill_path.parent / "assets"),
            line_count=line_count,
            warnings=local_warnings,
        )
        records.append(record)
        tokens_by_path[record.path] = _tokens(f"{name} {description}")

    duplicate_names: dict[str, list[str]] = {}
    by_name: dict[str, list[str]] = {}
    for record in records:
        by_name.setdefault(record.name, []).append(record.path)
    duplicate_names = {name: paths for name, paths in sorted(by_name.items()) if len(paths) > 1}

    overlaps: list[Overlap] = []
    for index, left in enumerate(records):
        lt = tokens_by_path[left.path]
        for right in records[index + 1 :]:
            rt = tokens_by_path[right.path]
            if not lt or not rt:
                continue
            shared = lt & rt
            union = lt | rt
            score = len(shared) / len(union)
            if len(shared) >= 4 and score >= overlap_threshold:
                overlaps.append(Overlap(left.name, right.name, round(score, 2), sorted(shared)[:12]))
    overlaps.sort(key=lambda item: (-item.score, item.left, item.right))

    if duplicate_names:
        warnings.append("duplicate skill names detected; client precedence may be ambiguous")
    if overlaps:
        warnings.append("high description overlap detected; tighten triggers or compose a smaller skill stack")

    return StackReport(
        roots=resolved_roots,
        skill_count=len(records),
        skills=records,
        duplicate_names=duplicate_names,
        overlaps=overlaps,
        warnings=warnings,
    )


def default_roots(project: Path) -> list[Path]:
    project = project.expanduser().resolve()
    home = Path.home()
    return [
        project / ".agents" / "skills",
        project / ".github" / "skills",
        project / ".claude" / "skills",
        home / ".agents" / "skills",
        home / ".copilot" / "skills",
        home / ".claude" / "skills",
    ]


def markdown(report: StackReport) -> str:
    lines = [
        "# Agent Skill stack audit", "",
        f"- Skills discovered: **{report.skill_count}**",
        f"- Roots scanned: **{len(report.roots)}**",
    ]
    lines.extend(["", "## Skills"])
    if report.skills:
        for skill in report.skills:
            extras = f"scripts={skill.scripts}, refs={skill.references}, assets={skill.assets}, lines={skill.line_count}"
            lines.append(f"- **{skill.name}** — `{skill.path}` ({extras})")
            for warning in skill.warnings:
                lines.append(f"  - warning: {warning}")
    else:
        lines.append("- None detected")
    lines.extend(["", "## Duplicate names"])
    if report.duplicate_names:
        for name, paths in report.duplicate_names.items():
            lines.append(f"- **{name}**")
            lines.extend(f"  - `{path}`" for path in paths)
    else:
        lines.append("- None")
    lines.extend(["", "## Potential trigger overlap"])
    if report.overlaps:
        for item in report.overlaps:
            lines.append(f"- **{item.left} ↔ {item.right}** — {item.score:.2f}; shared: {', '.join(item.shared_terms)}")
    else:
        lines.append("- No high-overlap pairs detected")
    if report.warnings:
        lines.extend(["", "## Stack warnings"])
        lines.extend(f"- {item}" for item in report.warnings)
    lines.extend([
        "",
        "> This is a local, read-only inventory. It does not install, update, trust, or execute third-party skills.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="project used to derive common skill roots")
    parser.add_argument("--root", type=Path, action="append", help="explicit skill root; repeatable")
    parser.add_argument("--max-depth", type=int, default=4)
    parser.add_argument("--overlap-threshold", type=float, default=0.42)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    roots = args.root or default_roots(args.project)
    report = scan(roots, max_depth=max(0, args.max_depth), overlap_threshold=max(0.0, min(1.0, args.overlap_threshold)))
    if args.format == "json":
        payload = asdict(report)
        payload["skills"] = [asdict(item) for item in report.skills]
        payload["overlaps"] = [asdict(item) for item in report.overlaps]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(markdown(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
