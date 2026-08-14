#!/usr/bin/env python3
"""Create a deterministic, read-only codebase/context map for an agent task."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "dist", "build",
    "coverage", ".next", ".turbo", ".cache", "target", "vendor", "__pycache__",
}
LANGUAGE_SUFFIXES = {
    ".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript/React", ".jsx": "JavaScript/React",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".go": "Go", ".rs": "Rust",
    ".cs": "C#", ".cpp": "C++", ".cc": "C++", ".c": "C", ".h": "C/C++",
    ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".dart": "Dart", ".scala": "Scala",
    ".sql": "SQL", ".vue": "Vue", ".svelte": "Svelte", ".sh": "Shell", ".ps1": "PowerShell",
}
MANIFEST_NAMES = {
    "package.json", "pyproject.toml", "requirements.txt", "poetry.lock", "uv.lock", "pdm.lock",
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "Cargo.toml", "Cargo.lock",
    "go.mod", "go.sum", "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle",
    "Gemfile", "Gemfile.lock", "composer.json", "pubspec.yaml", "Podfile", "Package.swift",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
}
ENTRYPOINT_NAMES = {
    "main.py", "app.py", "server.py", "index.js", "index.ts", "main.ts", "main.js", "Program.cs",
    "main.go", "main.rs", "App.tsx", "App.jsx", "manage.py", "wsgi.py", "asgi.py",
}
GOVERNANCE_NAMES = {
    "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "CODEOWNERS", "FUNDING.yml",
    "dependabot.yml", "renovate.json", "AGENTS.md", "CLAUDE.md", "GEMINI.md", "copilot-instructions.md",
}
DOC_NAMES = {"README.md", "CHANGELOG.md", "ROADMAP.md", "ARCHITECTURE.md", "DESIGN.md"}

TASK_STOPWORDS = {
    "a", "an", "and", "or", "the", "this", "that", "to", "of", "in", "on",
    "with", "from", "for", "please", "into", "by", "as", "is", "are", "be",
}

# Small, path-oriented aliases for common authentication task wording.  This
# is deliberately not a general translation table: aliases are added only
# when a complete English token or an explicit Chinese phrase is present.
TASK_TOKEN_ALIASES = {
    "authentication": {"auth"},
    "authenticate": {"auth"},
    "authorization": {"auth"},
    "authorize": {"auth"},
    "signin": {"auth", "login"},
    "认证": {"auth"},
    "身份验证": {"auth"},
    "登录": {"auth", "login"},
}


@dataclass(frozen=True)
class ContextMap:
    root: str
    file_count: int
    languages: list[dict[str, object]]
    manifests: list[str]
    entrypoints: list[str]
    tests: list[str]
    ci: list[str]
    docs: list[str]
    governance: list[str]
    high_signal_files: list[str]
    focus_files: list[dict[str, object]]
    warnings: list[str]


def _iter_files(root: Path, max_files: int) -> tuple[list[Path], list[str]]:
    if max_files < 1:
        raise ValueError("max_files must be at least 1")
    files: list[Path] = []
    warnings: list[str] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name.casefold(), reverse=True)
        except OSError as exc:
            warnings.append(f"could not read {current}: {exc}")
            continue
        for path in entries:
            if path.name in IGNORE_DIRS:
                continue
            try:
                if path.is_symlink():
                    continue
                if path.is_dir():
                    stack.append(path)
                elif path.is_file():
                    if len(files) >= max_files:
                        warnings.append(f"file scan capped at {max_files} files")
                        return files, warnings
                    files.append(path)
            except OSError:
                continue
    return files, warnings


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()



def _is_test_path(rel: str) -> bool:
    low = rel.casefold()
    parts = tuple(part for part in low.split("/") if part)
    if any(part in {"test", "tests", "spec", "specs", "__tests__"} for part in parts[:-1]):
        return True
    name = parts[-1] if parts else low
    return (
        name.startswith(("test_", "spec_"))
        or "_test." in name
        or name.endswith((".test.js", ".test.jsx", ".test.ts", ".test.tsx", ".spec.js", ".spec.jsx", ".spec.ts", ".spec.tsx"))
    )

def _task_tokens(task: str) -> set[str]:
    normalized = task.casefold()
    tokens = {
        token
        for token in re.findall(r"[A-Za-z0-9_\-\u4e00-\u9fff]+", normalized)
        if len(token) >= 2 and token not in TASK_STOPWORDS
    }
    for phrase, aliases in TASK_TOKEN_ALIASES.items():
        is_chinese_phrase = any("\u4e00" <= char <= "\u9fff" for char in phrase)
        if (phrase in normalized if is_chinese_phrase else phrase in tokens):
            tokens.update(aliases)
    return tokens


def build_context(root: Path, task: str = "", max_files: int = 5000) -> ContextMap:
    root = root.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"repository directory does not exist: {root}")

    files, warnings = _iter_files(root, max_files)
    rels = [_rel(root, path) for path in files]
    language_counter: Counter[str] = Counter()
    manifests: list[str] = []
    entrypoints: list[str] = []
    tests: list[str] = []
    ci: list[str] = []
    docs: list[str] = []
    governance: list[str] = []

    for path, rel in zip(files, rels):
        language = LANGUAGE_SUFFIXES.get(path.suffix.lower())
        if language:
            language_counter[language] += 1
        if path.name in MANIFEST_NAMES:
            manifests.append(rel)
        if path.name in ENTRYPOINT_NAMES or rel.startswith(("cmd/", "bin/")):
            entrypoints.append(rel)
        low = rel.casefold()
        if _is_test_path(rel):
            tests.append(rel)
        if low.startswith(".github/workflows/") or low in {"azure-pipelines.yml", ".gitlab-ci.yml", "jenkinsfile"}:
            ci.append(rel)
        if path.name in DOC_NAMES or low.startswith("docs/"):
            docs.append(rel)
        if path.name in GOVERNANCE_NAMES or low.startswith(".github/issue_template/"):
            governance.append(rel)

    priority_names = set(MANIFEST_NAMES) | ENTRYPOINT_NAMES | GOVERNANCE_NAMES | DOC_NAMES
    high_signal = [rel for path, rel in zip(files, rels) if path.name in priority_names or rel in ci]
    high_signal = sorted(dict.fromkeys(high_signal))[:80]

    tokens = _task_tokens(task)
    focus: list[dict[str, object]] = []
    if tokens:
        for rel in rels:
            low = rel.casefold()
            matched = sorted(token for token in tokens if token in low)
            if not matched:
                continue
            score = len(matched) * 10
            if any(rel == item for item in high_signal):
                score += 4
            if any(piece in low for piece in ("test", "spec", "readme", "workflow", "security")):
                score += 2
            focus.append({"path": rel, "score": score, "matched": matched[:6]})
        focus.sort(key=lambda item: (-int(item["score"]), str(item["path"])))
    else:
        focus = [{"path": rel, "score": 1, "matched": []} for rel in high_signal[:30]]

    scan_capped = any(item.startswith("file scan capped at ") for item in warnings)
    if not tests:
        warnings.append("test discovery incomplete due scan cap; no tests observed in scanned subset" if scan_capped else "no obvious test files detected")
    if not ci:
        warnings.append("CI discovery incomplete due scan cap; no workflow observed in scanned subset" if scan_capped else "no obvious CI workflow detected")
    if not any(Path(x).name == "SECURITY.md" for x in governance):
        warnings.append("SECURITY.md not observed in scanned subset" if scan_capped else "SECURITY.md not detected")
    if not any(Path(x).name == "CONTRIBUTING.md" for x in governance):
        warnings.append("CONTRIBUTING.md not observed in scanned subset" if scan_capped else "CONTRIBUTING.md not detected")

    languages = [
        {"name": name, "files": count}
        for name, count in language_counter.most_common(12)
    ]
    return ContextMap(
        root=str(root),
        file_count=len(files),
        languages=languages,
        manifests=sorted(manifests),
        entrypoints=sorted(dict.fromkeys(entrypoints))[:50],
        tests=sorted(tests)[:80],
        ci=sorted(ci),
        docs=sorted(docs)[:80],
        governance=sorted(governance)[:80],
        high_signal_files=high_signal,
        focus_files=focus[:40],
        warnings=warnings,
    )


def markdown(result: ContextMap) -> str:
    lines = ["# Repository context map", "", f"- Root: `{result.root}`", f"- Files scanned: **{result.file_count}**"]
    if result.languages:
        lines.append("- Languages: " + ", ".join(f"{x['name']} ({x['files']})" for x in result.languages))
    for title, values in (
        ("Manifests", result.manifests), ("Entrypoints", result.entrypoints), ("CI", result.ci),
        ("Tests", result.tests), ("Docs", result.docs), ("Governance", result.governance),
    ):
        lines.extend(["", f"## {title}"])
        lines.extend(f"- `{value}`" for value in values[:20])
        if not values:
            lines.append("- None detected")
    lines.extend(["", "## Task focus files"])
    for item in result.focus_files[:20]:
        matched = ", ".join(item["matched"])
        suffix = f" — matched: {matched}" if matched else ""
        lines.append(f"- `{item['path']}` (score {item['score']}){suffix}")
    if result.warnings:
        lines.extend(["", "## Evidence gaps"])
        lines.extend(f"- {item}" for item in result.warnings)
    lines.extend(["", "> This map is path/evidence based. Read the selected files before making architectural claims.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--task", default="")
    parser.add_argument("--max-files", type=int, default=5000)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    try:
        result = build_context(args.repo, args.task, args.max_files)
    except ValueError as exc:
        parser.error(str(exc))
    if args.format == "json":
        print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
    else:
        print(markdown(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
