#!/usr/bin/env python3
"""Deterministic SemVer recommendation and release-note grouping from commit messages."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReleaseReport:
    current_version: str
    suggested_version: str
    bump: str
    groups: dict[str, list[str]]
    blockers: list[str]
    migration_notes_required: bool
    release_ready: bool
    release_notes: str


TYPE_GROUPS = {
    "feat": "Features",
    "fix": "Fixes",
    "perf": "Performance",
    "security": "Security",
    "docs": "Documentation",
    "refactor": "Maintenance",
    "test": "Maintenance",
    "build": "Maintenance",
    "ci": "Maintenance",
    "chore": "Maintenance",
    "style": "Maintenance",
}
ORDER = ["Breaking", "Features", "Fixes", "Performance", "Security", "Documentation", "Maintenance", "Other"]
SEMVER_RE = re.compile(
    r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
BREAKING_RE = re.compile(r"^\s*BREAKING(?:[ -]CHANGES?)\s*:", flags=re.IGNORECASE | re.MULTILINE)


def _version_match(text: str) -> re.Match[str]:
    match = SEMVER_RE.fullmatch(text.strip())
    if not match:
        raise ValueError(f"invalid SemVer version: {text}")
    return match


def parse_version(text: str) -> tuple[int, int, int]:
    match = _version_match(text)
    return tuple(int(match.group(index)) for index in (1, 2, 3))


def bump_version(current: str, bump: str) -> str:
    match = _version_match(current)
    major, minor, patch = (int(match.group(index)) for index in (1, 2, 3))
    # A prerelease already targets this core version. The safest deterministic
    # recommendation is to stabilize that target rather than accidentally skip
    # to the next patch/minor solely because RC commits exist.
    if match.group(4) is not None:
        return f"{major}.{minor}.{patch}"
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    return f"{major}.{minor}.{patch}"


def load_commits(path: Path) -> list[str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("commits", [])
    if not isinstance(raw, list):
        raise ValueError("commits JSON must be a list or an object with a commits list")
    messages: list[str] = []
    for item in raw:
        if isinstance(item, str):
            messages.append(item)
        elif isinstance(item, dict):
            messages.append(str(item.get("message", item.get("subject", ""))))
        else:
            raise ValueError("commit entries must be strings or objects")
    return [m.strip() for m in messages if m.strip()]


def git_commits(repo: Path, from_ref: str | None, to_ref: str) -> list[str]:
    if from_ref:
        rev = f"{from_ref}..{to_ref}"
    else:
        tag = subprocess.run(["git", "-C", str(repo), "describe", "--tags", "--abbrev=0"], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.strip()
        rev = f"{tag}..{to_ref}" if tag else to_ref
    proc = subprocess.run(["git", "-C", str(repo), "log", rev, "--pretty=%B%x1e"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode:
        raise ValueError(proc.stderr.strip() or "git log failed")
    return [chunk.strip() for chunk in proc.stdout.split("\x1e") if chunk.strip()]


def classify(messages: list[str], current_version: str) -> ReleaseReport:
    # Public callers may pass blank commit entries even though load_commits()
    # filters them. Normalize here as well so the core API cannot crash on an
    # empty/whitespace-only message.
    messages = [message.strip() for message in messages if message and message.strip()]
    groups: dict[str, list[str]] = {name: [] for name in ORDER}
    bump = "none"
    blockers: list[str] = []
    migration_required = False

    for message in messages:
        first = message.splitlines()[0].strip()
        folded = message.casefold()
        conventional = re.match(
            r"(?P<type>[a-z]+)(?:\([^)]*\))?(?P<breaking>!)?:\s*(?P<title>.+)",
            first,
            flags=re.IGNORECASE,
        )
        is_breaking = bool(BREAKING_RE.search(message) or (conventional and conventional.group("breaking")))
        if is_breaking:
            group = "Breaking"
            bump = "major"
            migration_required = True
        else:
            ctype = conventional.group("type").casefold() if conventional else ""
            group = TYPE_GROUPS.get(ctype, "Other")
            if ctype == "feat" and bump not in {"major"}:
                bump = "minor"
            elif ctype in {"fix", "perf", "security"} and bump not in {"major", "minor"}:
                bump = "patch"
            elif bump == "none" and ctype in TYPE_GROUPS:
                bump = "patch"
        title = conventional.group("title") if conventional else first
        groups[group].append(title)

    if messages and bump == "none":
        bump = "patch"
    if not messages:
        blockers.append("no commits supplied; there is no deterministic release delta to publish")
    if migration_required:
        migration_words = ("migration", "migrate", "upgrade guide", "compatibility", "breaking note", "迁移", "升级说明")
        if not any(any(word in msg.casefold() for word in migration_words) for msg in messages):
            blockers.append("breaking change detected without an explicit migration/upgrade note in commit messages")

    suggested = bump_version(current_version, bump)
    notes_lines = [f"# {suggested}", ""]
    for group in ORDER:
        items = groups[group]
        if not items:
            continue
        notes_lines.append(f"## {group}")
        notes_lines.extend(f"- {item}" for item in items)
        notes_lines.append("")
    notes = "\n".join(notes_lines).rstrip() + "\n"
    groups = {name: items for name, items in groups.items() if items}
    return ReleaseReport(
        current_version=current_version,
        suggested_version=suggested,
        bump=bump,
        groups=groups,
        blockers=blockers,
        migration_notes_required=migration_required,
        release_ready=not blockers,
        release_notes=notes,
    )


def markdown(report: ReleaseReport) -> str:
    lines = [
        "# Release intelligence", "",
        f"- Current: **{report.current_version}**",
        f"- Suggested: **{report.suggested_version}**",
        f"- SemVer bump: **{report.bump}**",
        f"- Migration notes required: **{'yes' if report.migration_notes_required else 'no'}**",
        f"- Deterministic blockers clear: **{'yes' if report.release_ready else 'no'}**",
    ]
    if report.blockers:
        lines.extend(["", "## Blockers"])
        lines.extend(f"- {item}" for item in report.blockers)
    lines.extend(["", "## Draft release notes", "", report.release_notes.rstrip(), "", "> Validate CI, artifacts, permissions, and the actual release diff before publishing.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--commits-json", type=Path)
    source.add_argument("--repo", type=Path)
    parser.add_argument("--from-ref")
    parser.add_argument("--to-ref", default="HEAD")
    parser.add_argument("--current-version", required=True)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    try:
        parse_version(args.current_version)
        messages = load_commits(args.commits_json) if args.commits_json else git_commits(args.repo, args.from_ref, args.to_ref)
        report = classify(messages, args.current_version)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    if args.format == "json":
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(markdown(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
