#!/usr/bin/env python3
"""Produce a transparent, deterministic AI-project repository readiness audit."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

IGNORED_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "target",
    "__pycache__",
}
TEXT_SUFFIXES = {
    ".md", ".txt", ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt",
    ".kts", ".go", ".rs", ".rb", ".php", ".swift", ".c", ".cc", ".cpp",
    ".h", ".hpp", ".cs", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".properties", ".gradle", ".sh", ".ps1", ".html", ".css", ".sql",
}
SOURCE_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".kts", ".go",
    ".rs", ".rb", ".php", ".swift", ".c", ".cc", ".cpp", ".cs", ".html",
}
SECRET_PATTERNS = {
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


@dataclass(frozen=True)
class Check:
    id: str
    title: str
    points: int
    passed: bool
    evidence: str
    recommendation: str

    @property
    def earned(self) -> int:
        return self.points if self.passed else 0


def walk_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        try:
            relative_parts = path.relative_to(root).parts
        except ValueError:
            continue
        if any(part in IGNORED_DIRS for part in relative_parts):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        yield path


def read_small_text(path: Path, limit: int = 1_000_000) -> str:
    try:
        if path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def any_name(files: list[Path], names: set[str]) -> bool:
    lowered = {path.name.lower() for path in files}
    return bool(lowered & {name.lower() for name in names})


def contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(pattern.lower() in lower for pattern in patterns)


def secret_findings(files: list[Path], root: Path) -> list[str]:
    findings: list[str] = []
    for path in files:
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {".env"}:
            continue
        relative = path.relative_to(root)
        if path.name.startswith(".env") and path.name not in {".env.example", ".env.sample", ".env.template"}:
            findings.append(f"sensitive environment filename: {relative}")
        text = read_small_text(path)
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{label} pattern in {relative}")
    return findings[:20]


def build_checks(root: Path) -> tuple[list[Check], list[str]]:
    files = list(walk_files(root))
    rels = [path.relative_to(root).as_posix().lower() for path in files]
    readme_path = next((p for p in files if p.name.lower() in {"readme.md", "readme.rst", "readme.txt"}), None)
    readme = read_small_text(readme_path) if readme_path else ""
    readme_head = "\n".join(readme.splitlines()[:50])
    findings = secret_findings(files, root)

    source_present = any(path.suffix.lower() in SOURCE_SUFFIXES for path in files)
    tests_present = any(
        "/tests/" in f"/{rel}/" or "/test/" in f"/{rel}/" or Path(rel).name.startswith("test_")
        for rel in rels
    )
    ci_present = any(rel.startswith(".github/workflows/") and rel.endswith((".yml", ".yaml")) for rel in rels)
    evals_present = any("eval" in part for rel in rels for part in Path(rel).parts)
    examples_present = any(
        part in {"example", "examples", "sample", "samples", "fixtures", "demo"}
        for rel in rels
        for part in Path(rel).parts
    )
    media_present = any(
        rel.startswith(("docs/", "assets/")) and Path(rel).suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".mp4"}
        for rel in rels
    )
    env_example = any(Path(rel).name in {".env.example", ".env.sample", ".env.template"} for rel in rels)

    checks = [
        Check("source", "Runnable source exists", 7, source_present, "source files detected" if source_present else "no common source files detected", "Add the working vertical slice, not only design documents."),
        Check("readme", "README exists", 7, bool(readme_path), str(readme_path.relative_to(root)) if readme_path else "missing", "Add a README led by the user outcome."),
        Check("promise", "README leads with a product promise", 4, contains_any(readme_head, ("turn ", "helps ", "build ", "local", "visual", "assistant", "copilot", "runtime")), "value-oriented language found near the top" if readme else "README unavailable", "State the target user and one-sentence outcome before installation."),
        Check("quickstart", "Quick start contains commands", 6, "```" in readme and contains_any(readme, ("quick start", "quickstart", "getting started", "install", "run")), "README contains a command block and setup language" if readme else "README unavailable", "Add reproducible commands from a clean environment."),
        Check("demo", "Demo path is documented", 6, contains_any(readme, ("## demo", "60-second", "60 second", "sample workflow", "example workflow")), "demo language found" if readme else "README unavailable", "Document a realistic one-minute path to the wow moment."),
        Check("architecture", "Architecture or data flow is documented", 6, contains_any(readme, ("architecture", "data flow", "local/cloud", "local-first", "mermaid")) or any("architecture" in rel or "adr" in rel for rel in rels), "architecture/data-flow evidence found", "Explain the core loop and local/cloud boundary."),
        Check("limitations", "Limitations are explicit", 4, contains_any(readme, ("limitation", "not supported", "experimental", "what this is not", "out of scope")), "limitation language found" if readme else "README unavailable", "Place honest unsupported/experimental notes near the capability."),
        Check("license", "License is present", 5, any_name(files, {"LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING"}), "license file found", "Add an OSI-compatible license or clear project terms."),
        Check("tests", "Automated tests exist", 8, tests_present, "test paths detected" if tests_present else "no tests detected", "Add happy-path, failure, malformed-input, and regression tests."),
        Check("ci", "CI workflow exists", 7, ci_present, "GitHub Actions workflow found" if ci_present else "no GitHub Actions workflow detected", "Run validation and tests on push and pull requests."),
        Check("evals", "AI evaluation assets exist", 7, evals_present or contains_any(readme, ("evaluation", "evals", "benchmark")), "eval evidence found", "Version a small task set and explainable graders."),
        Check("security", "Security and data-boundary guidance exists", 6, any_name(files, {"SECURITY.md"}) or contains_any(readme, ("privacy", "security", "retention", "data boundary")), "security/privacy evidence found", "Document secrets, retention, permissions, and untrusted input."),
        Check("models", "Model/provider licensing boundary is stated", 5, contains_any(readme, ("model license", "model weights", "redistribute", "user-imported", "provider")), "model/provider boundary language found" if readme else "README unavailable", "State model source, version, license, and whether weights are bundled."),
        Check("examples", "Realistic examples or fixtures exist", 5, examples_present, "example/sample/fixture path found" if examples_present else "no example path detected", "Include a deterministic sample that reaches the core outcome."),
        Check("visual", "Real visual/demo asset exists", 3, media_present, "visual media found under docs/assets" if media_present else "no visual media detected", "Add a real screenshot, short recording, or architecture graphic."),
        Check("contributing", "Contribution guidance exists", 3, any_name(files, {"CONTRIBUTING.md"}), "CONTRIBUTING.md found", "Explain setup, tests, scope, and contribution expectations."),
        Check("configuration", "Configuration example exists", 3, env_example or contains_any(readme, ("environment variable", ".env.example", "configuration")), "configuration guidance found", "Add `.env.example` or explicit no-secret configuration instructions."),
        Check("secrets", "No obvious committed secrets", 8, not findings, "no high-confidence secret pattern detected" if not findings else "; ".join(findings), "Remove and rotate leaked credentials; keep only placeholders."),
    ]
    return checks, findings


def grade(score: int) -> str:
    if score >= 90:
        return "showcase-ready"
    if score >= 75:
        return "strong foundation"
    if score >= 55:
        return "working but under-documented"
    return "early prototype"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit an AI project repository using transparent checks.")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository to inspect.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.repo.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        print(f"Repository directory does not exist: {root}", file=sys.stderr)
        return 2

    checks, findings = build_checks(root)
    score = sum(check.earned for check in checks)
    maximum = sum(check.points for check in checks)
    result = {
        "repository": str(root),
        "score": score,
        "maximum": maximum,
        "percentage": round(score * 100 / maximum),
        "grade": grade(round(score * 100 / maximum)),
        "checks": [{**asdict(check), "earned": check.earned} for check in checks],
        "secret_findings": findings,
        "note": "This score checks repository evidence and hygiene; it is not a substitute for product or security review.",
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"AI project readiness: {result['percentage']}/100 — {result['grade']}")
        print()
        for check in checks:
            mark = "PASS" if check.passed else "MISS"
            print(f"[{mark}] {check.title} ({check.earned}/{check.points})")
            print(f"       {check.evidence}")
            if not check.passed:
                print(f"       Next: {check.recommendation}")
        print()
        print(result["note"])

    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
