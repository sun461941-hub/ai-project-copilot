#!/usr/bin/env python3
"""Read-only GitHub Actions and skill supply-chain guard with optional hash manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    path: str
    message: str

@dataclass(frozen=True)
class GuardReport:
    score: int
    findings: list[Finding]
    workflow_files: int
    integrity_files: int
    manifest_written: str | None
    warnings: list[str]

SHA_REF = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
USES_RE = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)")
RUN_RE = re.compile(r"^(?P<indent>\s*)-?\s*run:\s*(?P<body>.*)$", flags=re.IGNORECASE)
UNTRUSTED_RUN_RE = re.compile(r"\$\{\{\s*(?:github\.event(?:\.|\[)|github\.head_ref\b)", flags=re.IGNORECASE)
SKILL_INSTALL_PATHS = (
    "skills/ai-project-copilot",
    ".agents/skills/ai-project-copilot",
)

def _structural_line_items(lines: list[str]) -> list[tuple[int, str]]:
    """Return active YAML lines while excluding literal/folded scalar bodies."""
    result: list[tuple[int, str]] = []
    block_indent: int | None = None
    scalar_start = re.compile(r"^\s*(?:-\s*)?[^:#][^:]*:\s*[|>][+-]?\s*(?:#.*)?$")
    for lineno, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if block_indent is not None:
            if not stripped:
                continue
            if indent > block_indent:
                continue
            block_indent = None
        if stripped.startswith("#"):
            continue
        result.append((lineno, line))
        if scalar_start.match(line):
            block_indent = indent
    return result

def _workflow_findings(path: Path, rel: str) -> list[Finding]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    findings: list[Finding] = []
    structural_items = _structural_line_items(lines)
    active_text = "\n".join(line for _, line in structural_items)
    has_permissions = bool(re.search(r"^\s*permissions\s*:", active_text, flags=re.MULTILINE | re.IGNORECASE))
    has_pr_target = bool(re.search(r"^\s*pull_request_target\s*:", active_text, flags=re.MULTILINE | re.IGNORECASE))
    has_workflow_run = bool(re.search(r"^\s*workflow_run\s*:", active_text, flags=re.MULTILINE | re.IGNORECASE))
    if not has_permissions:
        findings.append(Finding("medium", "missing-permissions", rel, "workflow has no explicit permissions block; review least privilege"))
    if re.search(r"^\s*permissions:\s*write-all\s*$", active_text, flags=re.MULTILINE | re.IGNORECASE):
        findings.append(Finding("high", "write-all", rel, "workflow requests write-all permissions"))
    if has_pr_target:
        findings.append(Finding("high", "privileged-trigger", rel, "pull_request_target is present; verify untrusted fork code is never executed with elevated token/secrets"))
    if has_workflow_run:
        findings.append(Finding("medium", "workflow-run-trigger", rel, "workflow_run is present; verify trust boundary between triggering and privileged workflow"))
    checkout_present = False
    for lineno, line in structural_items:
        match = USES_RE.match(line)
        if not match:
            continue
        value = match.group(1)
        if value.casefold().startswith("actions/checkout@"):
            checkout_present = True
        if value.startswith("./") or "@" not in value:
            continue
        ref = value.rsplit("@", 1)[1]
        if not SHA_REF.fullmatch(ref):
            findings.append(Finding("medium", "mutable-action-ref", rel, f"line {lineno}: `{value}` uses a mutable tag/branch instead of an immutable commit SHA"))
    # GitHub expressions are expanded before the shell runs. Scan the entire
    # YAML run scalar (including |/> block bodies), not only the `run:` line.
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.lstrip().startswith("#"):
            index += 1
            continue
        match = RUN_RE.match(line)
        if not match:
            index += 1
            continue
        start_line = index + 1
        body = match.group("body").strip()
        fragment = body
        if body.startswith(("|", ">")):
            base_indent = len(match.group("indent"))
            block: list[str] = []
            cursor = index + 1
            while cursor < len(lines):
                child = lines[cursor]
                stripped = child.lstrip()
                if not stripped:
                    block.append(child)
                    cursor += 1
                    continue
                indent = len(child) - len(stripped)
                if indent <= base_indent:
                    break
                block.append(child)
                cursor += 1
            fragment = "\n".join(block)
            index = cursor
        else:
            index += 1
        if UNTRUSTED_RUN_RE.search(fragment):
            findings.append(Finding("high", "event-interpolation", rel, f"line {start_line}: untrusted GitHub event/head-ref data is interpolated directly into a run command; pass it through env/input handling instead"))
    if (has_pr_target or has_workflow_run) and checkout_present:
        findings.append(Finding("high", "privileged-checkout", rel, "privileged trigger and checkout appear together; verify checkout ref cannot select untrusted code"))
    return findings

def _hash_files(skill_dir: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    if not skill_dir.exists():
        return entries
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file() or path.is_symlink() or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(skill_dir).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append((rel, digest))
    return entries

def scan(repo: Path, manifest: Path | None = None) -> GuardReport:
    repo = repo.expanduser().resolve()
    if not repo.is_dir():
        raise ValueError(f"repository directory does not exist: {repo}")
    workflows_dir = repo / ".github" / "workflows"
    workflow_paths = sorted(list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml"))) if workflows_dir.exists() else []
    findings: list[Finding] = []
    for path in workflow_paths:
        findings.extend(_workflow_findings(path, path.relative_to(repo).as_posix()))
    skill_dirs = [(rel, repo / rel) for rel in SKILL_INSTALL_PATHS]
    hashes: list[tuple[str, str]] = []
    warnings: list[str] = []
    for rel, skill_dir in skill_dirs:
        local_hashes = _hash_files(skill_dir)
        hashes.extend((f"{rel}/{path}", digest) for path, digest in local_hashes)
        if skill_dir.exists() and not local_hashes:
            findings.append(Finding("medium", "empty-skill-manifest", rel, "skill directory exists but no regular files were hashable"))
    if not hashes:
        roots = ", ".join(f"`{path}`" for path in SKILL_INSTALL_PATHS)
        warnings.append(f"no hashable skill files detected under supported installation roots: {roots}")
    manifest_written: str | None = None
    if manifest is not None:
        requested = manifest.expanduser()
        if not requested.is_absolute():
            requested = repo / requested
        # Reject dangling links too: exists() is false for them.
        if requested.is_symlink():
            raise ValueError("refusing to write integrity manifest through a symlink")
        target = requested.parent.resolve() / requested.name
        try:
            target.relative_to(repo)
        except ValueError as exc:
            raise ValueError("integrity manifest must remain inside the repository") from exc
        for _, skill_dir in skill_dirs:
            try:
                target.relative_to(skill_dir.resolve())
            except ValueError:
                continue
            else:
                raise ValueError("integrity manifest must be outside the hashed skill directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        content = "".join(f"{digest}  {rel}\n" for rel, digest in hashes)
        target.write_text(content, encoding="utf-8")
        manifest_written = str(target)
    penalty = {"low": 4, "medium": 10, "high": 24, "critical": 40}
    score = max(0, 100 - sum(penalty.get(item.severity, 8) for item in findings))
    return GuardReport(score, findings, len(workflow_paths), len(hashes), manifest_written, warnings)

def markdown(report: GuardReport) -> str:
    lines = [
        "# Supply-chain guard", "",
        f"- Guard score: **{report.score}/100**",
        f"- Workflow files scanned: **{report.workflow_files}**",
        f"- Skill files hashed: **{report.integrity_files}**",
    ]
    if report.manifest_written:
        lines.append(f"- Integrity manifest: `{report.manifest_written}`")
    lines.extend(["", "## Findings"])
    if report.findings:
        for item in report.findings:
            lines.append(f"- **{item.severity.upper()}** `{item.code}` in `{item.path}` — {item.message}")
    else:
        lines.append("- No heuristic findings detected")
    if report.warnings:
        lines.extend(["", "## Warnings"])
        lines.extend(f"- {item}" for item in report.warnings)
    lines.extend(["", "> This is a focused heuristic review, not a complete security audit. Confirm findings against the workflow's actual trust model.", ""])
    return "\n".join(lines)

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, help="optional path to write SHA-256 manifest; omitted by default")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    try:
        report = scan(args.repo, args.manifest)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.format == "json":
        payload = asdict(report)
        payload["findings"] = [asdict(item) for item in report.findings]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(markdown(report), end="")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
