#!/usr/bin/env python3
"""Deterministic, read-only PR/change risk analysis from a diff or changed-file list."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Change:
    path: str
    additions: int = 0
    deletions: int = 0
    status: str = "modified"


@dataclass(frozen=True)
class RiskReport:
    score: int
    level: str
    categories: list[str]
    reasons: list[str]
    changed_files: int
    additions: int
    deletions: int
    review_lanes: list[str]
    test_recommendations: list[str]
    breaking_change_hints: list[str]
    human_gate_required: bool


CATEGORY_RULES: list[tuple[str, int, tuple[str, ...]]] = [
    ("security/auth", 28, ("auth", "authentication", "authorization", "security", "crypto", "permission", "permissions", "oauth", "jwt", "token", "tokens", "secret", "secrets", "credential", "credentials", "iam", "login")),
    ("data/schema/migration", 24, ("migration", "migrations", "schema", "database", "db/", ".sql", "alembic", "prisma")),
    ("ci/supply-chain", 20, (".github/workflows/", "dependabot", "renovate", "package-lock", "pnpm-lock", "yarn.lock", "cargo.lock", "requirements", "pyproject.toml")),
    ("public-api/contracts", 20, ("api/", "public/", "sdk", "proto", "openapi", "graphql", "contract", "types/", "schema/")),
    ("deploy/config", 17, ("dockerfile", "docker-compose", "terraform", "infra/", "k8s", "helm", "deploy", "config", ".env")),
    ("core-runtime", 10, ("src/", "lib/", "app/", "server/", "core/", "cmd/")),
]


def _norm(path: str) -> str:
    return path.replace("\\", "/").casefold()


def _matches_term(path: str, term: str) -> bool:
    low = _norm(path)
    needle = term.casefold()
    if "/" in needle or needle.startswith("."):
        return needle in low
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", low) is not None



TEST_DIRS = {"test", "tests", "spec", "specs", "__tests__"}
SOURCE_DIRS = {"src", "lib", "app", "server", "core", "cmd", "pkg", "internal"}
ROOT_SOURCE_NAMES = {
    "main.py", "app.py", "server.py", "manage.py", "main.go", "main.rs",
    "index.js", "index.ts", "app.jsx", "app.tsx", "program.cs",
}


def _path_parts(path: str) -> tuple[str, ...]:
    return tuple(part for part in _norm(path).split("/") if part)


def _is_test_path(path: str) -> bool:
    parts = _path_parts(path)
    if not parts:
        return False
    if any(part in TEST_DIRS for part in parts[:-1]):
        return True
    name = parts[-1]
    return (
        name.startswith(("test_", "spec_"))
        or "_test." in name
        or name.endswith((".test.js", ".test.jsx", ".test.ts", ".test.tsx", ".spec.js", ".spec.jsx", ".spec.ts", ".spec.tsx"))
    )


def _is_source_path(path: str) -> bool:
    parts = _path_parts(path)
    if not parts:
        return False
    return any(part in SOURCE_DIRS for part in parts[:-1]) or (len(parts) == 1 and parts[0] in ROOT_SOURCE_NAMES)


def _is_docs_path(path: str) -> bool:
    parts = _path_parts(path)
    if not parts:
        return False
    name = parts[-1]
    return (
        "docs" in parts[:-1]
        or name.startswith("readme")
        or name.startswith("changelog")
        or name.startswith("roadmap")
        or name in {"architecture.md", "design.md"}
    )


def _nonnegative_count(value: object, field: str) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a non-negative integer")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        result = int(value.strip())
    else:
        raise ValueError(f"{field} must be a non-negative integer")
    if result < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return result

def parse_changes_json(path: Path) -> list[Change]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("changes", []) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("changes JSON must be a list or an object with a changes list")
    result: list[Change] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("path"):
            raise ValueError("each change must be an object with a path")
        result.append(Change(
            path=str(item["path"]),
            additions=_nonnegative_count(item.get("additions", 0), "additions"),
            deletions=_nonnegative_count(item.get("deletions", 0), "deletions"),
            status=str(item.get("status", "modified")),
        ))
    return result


def parse_patch(path: Path) -> list[Change]:
    text = path.read_text(encoding="utf-8", errors="replace")
    changes: dict[str, list[int]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            try:
                parts = shlex.split(line)
            except ValueError:
                parts = []
            if len(parts) >= 4 and parts[0] == "diff" and parts[1] == "--git":
                candidate = parts[3]
                current = candidate[2:] if candidate.startswith("b/") else candidate
                changes.setdefault(current, [0, 0])
            else:
                current = None
            continue
        if current is None:
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            changes[current][0] += 1
        elif line.startswith("-"):
            changes[current][1] += 1
    return [Change(path=name, additions=vals[0], deletions=vals[1]) for name, vals in sorted(changes.items())]


def git_changes(repo: Path, base: str, head: str) -> list[Change]:
    command = ["git", "-C", str(repo), "diff", "--numstat", f"{base}...{head}"]
    proc = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode:
        raise ValueError(proc.stderr.strip() or "git diff failed")
    changes: list[Change] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add, delete, name = parts[0], parts[1], parts[-1]
        changes.append(Change(name, 0 if add == "-" else int(add), 0 if delete == "-" else int(delete)))
    return changes


def analyze(changes: list[Change]) -> RiskReport:
    if not changes:
        return RiskReport(0, "none", [], ["no changed files supplied"], 0, 0, 0, [], [], [], False)

    additions = sum(max(0, item.additions) for item in changes)
    deletions = sum(max(0, item.deletions) for item in changes)
    total_delta = additions + deletions
    paths = [_norm(item.path) for item in changes]
    categories: list[str] = []
    reasons: list[str] = []
    score = 0

    for category, weight, terms in CATEGORY_RULES:
        hits = sorted({item.path for item in changes if any(_matches_term(item.path, term) for term in terms)})
        if hits:
            categories.append(category)
            score += weight
            reasons.append(f"{category}: {len(hits)} changed file(s), e.g. {', '.join(hits[:3])}")

    source_changed = any(_is_source_path(item.path) for item in changes)
    tests_changed = any(_is_test_path(item.path) for item in changes)
    docs_changed = any(_is_docs_path(item.path) for item in changes)

    if total_delta >= 1200:
        score += 18
        reasons.append(f"large change surface: {total_delta} changed lines")
    elif total_delta >= 500:
        score += 12
        reasons.append(f"medium-large change surface: {total_delta} changed lines")
    elif total_delta >= 180:
        score += 6
        reasons.append(f"moderate change surface: {total_delta} changed lines")

    if len(changes) >= 25:
        score += 10
        reasons.append(f"wide file surface: {len(changes)} files")
    elif len(changes) >= 10:
        score += 5
        reasons.append(f"multi-file surface: {len(changes)} files")

    if source_changed and not tests_changed:
        score += 10
        reasons.append("source changed without an obvious test-file change")

    if "public-api/contracts" in categories and not docs_changed:
        score += 6
        reasons.append("public API/contract changed without obvious documentation update")

    if categories == [] and docs_changed and not source_changed:
        score = min(score, 8)
        reasons.append("documentation-only change surface")

    score = max(0, min(100, score))
    level = "low" if score < 25 else "medium" if score < 50 else "high" if score < 75 else "critical"

    review_lanes: list[str] = ["behavior + regression"] if source_changed else []
    test_recommendations: list[str] = []
    breaking: list[str] = []

    if "security/auth" in categories:
        review_lanes += ["security + authorization", "permission boundary"]
        test_recommendations += ["negative authorization tests", "secret/token handling test", "malformed credential/input test"]
        breaking.append("auth/security behavior changed; verify compatibility and privilege boundaries")
    if "data/schema/migration" in categories:
        review_lanes += ["schema + rollback", "data integrity"]
        test_recommendations += ["forward migration test", "rollback/recovery test", "old-data compatibility test"]
        breaking.append("schema/migration changed; require migration and rollback notes")
    if "ci/supply-chain" in categories:
        review_lanes.append("CI + supply chain")
        test_recommendations += ["CI workflow dry run or trusted branch run", "dependency/action provenance review"]
    if "public-api/contracts" in categories:
        review_lanes.append("API compatibility")
        test_recommendations += ["contract/API compatibility tests", "consumer-facing example or docs check"]
        breaking.append("public API/contract surface changed; verify SemVer impact")
    if "deploy/config" in categories:
        review_lanes += ["deployment + configuration", "rollback"]
        test_recommendations += ["config default/validation test", "deployment preflight or dry run"]
    if source_changed and not tests_changed:
        test_recommendations.append("add or identify regression coverage for changed behavior")

    review_lanes = list(dict.fromkeys(review_lanes))
    test_recommendations = list(dict.fromkeys(test_recommendations))
    breaking = list(dict.fromkeys(breaking))
    human_gate = score >= 50 or "security/auth" in categories or "data/schema/migration" in categories

    return RiskReport(
        score=score,
        level=level,
        categories=categories,
        reasons=reasons,
        changed_files=len(changes),
        additions=additions,
        deletions=deletions,
        review_lanes=review_lanes,
        test_recommendations=test_recommendations,
        breaking_change_hints=breaking,
        human_gate_required=human_gate,
    )


def markdown(report: RiskReport) -> str:
    lines = [
        "# Change risk report", "",
        f"- Risk: **{report.level.upper()}** ({report.score}/100)",
        f"- Files: **{report.changed_files}**", f"- Lines: **+{report.additions} / -{report.deletions}**",
        f"- Human gate recommended: **{'yes' if report.human_gate_required else 'no'}**",
    ]
    for title, values in (
        ("Risk evidence", report.reasons), ("Review lanes", report.review_lanes),
        ("Test recommendations", report.test_recommendations), ("Breaking-change hints", report.breaking_change_hints),
    ):
        lines.extend(["", f"## {title}"])
        lines.extend(f"- {value}" for value in values)
        if not values:
            lines.append("- None detected")
    lines.extend(["", "> This score prioritizes review effort. It is not proof that a change is safe or unsafe.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--changes-json", type=Path)
    source.add_argument("--patch", type=Path)
    source.add_argument("--repo", type=Path)
    parser.add_argument("--base", default="main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    try:
        if args.changes_json:
            changes = parse_changes_json(args.changes_json)
        elif args.patch:
            changes = parse_patch(args.patch)
        else:
            changes = git_changes(args.repo, args.base, args.head)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    report = analyze(changes)
    if args.format == "json":
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(markdown(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
