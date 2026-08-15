#!/usr/bin/env python3
"""Read-only GitHub Actions and skill supply-chain guard with optional hash manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
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
RUN_RE = re.compile(
    r"^(?P<indent>\s*)-?\s*run:\s*(?P<body>.*)$",
    flags=re.IGNORECASE,
)
UNTRUSTED_RUN_RE = re.compile(
    r"\$\{\{\s*(?:github\.event(?:\.|\[)|github\.head_ref\b)",
    flags=re.IGNORECASE,
)
SKILL_INSTALL_PATHS = (
    "skills/ai-project-copilot",
    ".agents/skills/ai-project-copilot",
)
MAX_WORKFLOW_BYTES = 2 * 1024 * 1024
MAX_HASH_FILE_BYTES = 128 * 1024 * 1024
MAX_HASH_FILES = 20_000


def _structural_line_items(lines: list[str]) -> list[tuple[int, str]]:
    """Return active YAML lines while excluding literal/folded scalar bodies."""
    result: list[tuple[int, str]] = []
    block_indent: int | None = None
    scalar_start = re.compile(
        r"^\s*(?:-\s*)?[^:#][^:]*:\s*[|>][+-]?\s*(?:#.*)?$"
    )
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


def _read_workflow(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("workflow must be a regular non-symlink file")
    size = path.stat().st_size
    if size > MAX_WORKFLOW_BYTES:
        raise ValueError(f"workflow exceeds {MAX_WORKFLOW_BYTES} bytes")
    with path.open("rb") as handle:
        data = handle.read(MAX_WORKFLOW_BYTES + 1)
    if len(data) > MAX_WORKFLOW_BYTES:
        raise ValueError(f"workflow exceeds {MAX_WORKFLOW_BYTES} bytes")
    return data.decode("utf-8", errors="replace")


def _workflow_findings(path: Path, rel: str) -> list[Finding]:
    text = _read_workflow(path)
    lines = text.splitlines()
    findings: list[Finding] = []
    structural_items = _structural_line_items(lines)
    active_text = "\n".join(line for _, line in structural_items)
    has_permissions = bool(
        re.search(
            r"^\s*permissions\s*:",
            active_text,
            flags=re.MULTILINE | re.IGNORECASE,
        )
    )
    has_pr_target = bool(
        re.search(
            r"^\s*pull_request_target\s*:",
            active_text,
            flags=re.MULTILINE | re.IGNORECASE,
        )
    )
    has_workflow_run = bool(
        re.search(
            r"^\s*workflow_run\s*:",
            active_text,
            flags=re.MULTILINE | re.IGNORECASE,
        )
    )
    if not has_permissions:
        findings.append(
            Finding(
                "medium",
                "missing-permissions",
                rel,
                "workflow has no explicit permissions block; review least privilege",
            )
        )
    if re.search(
        r"^\s*permissions:\s*write-all\s*$",
        active_text,
        flags=re.MULTILINE | re.IGNORECASE,
    ):
        findings.append(
            Finding("high", "write-all", rel, "workflow requests write-all permissions")
        )
    if has_pr_target:
        findings.append(
            Finding(
                "high",
                "privileged-trigger",
                rel,
                "pull_request_target is present; verify untrusted fork code is never executed with elevated token/secrets",
            )
        )
    if has_workflow_run:
        findings.append(
            Finding(
                "medium",
                "workflow-run-trigger",
                rel,
                "workflow_run is present; verify trust boundary between triggering and privileged workflow",
            )
        )

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
            findings.append(
                Finding(
                    "medium",
                    "mutable-action-ref",
                    rel,
                    f"line {lineno}: `{value}` uses a mutable tag/branch instead of an immutable commit SHA",
                )
            )

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
            findings.append(
                Finding(
                    "high",
                    "event-interpolation",
                    rel,
                    f"line {start_line}: untrusted GitHub event/head-ref data is interpolated directly into a run command; pass it through env/input handling instead",
                )
            )
    if (has_pr_target or has_workflow_run) and checkout_present:
        findings.append(
            Finding(
                "high",
                "privileged-checkout",
                rel,
                "privileged trigger and checkout appear together; verify checkout ref cannot select untrusted code",
            )
        )
    return findings


def _hash_file(path: Path) -> str:
    size = path.stat().st_size
    if size > MAX_HASH_FILE_BYTES:
        raise ValueError(
            f"file exceeds hash safety limit of {MAX_HASH_FILE_BYTES} bytes: {path}"
        )
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _hash_files(
    skill_dir: Path,
    rel_root: str,
) -> tuple[list[tuple[str, str]], list[Finding]]:
    entries: list[tuple[str, str]] = []
    findings: list[Finding] = []
    # Check is_symlink() before exists(): exists() is false for dangling links.
    if skill_dir.is_symlink():
        return entries, [
            Finding(
                "high",
                "skill-root-symlink",
                rel_root,
                "skill root must be a regular directory, not a symlink",
            )
        ]
    if not skill_dir.exists():
        return entries, findings
    if not skill_dir.is_dir():
        return entries, [
            Finding(
                "high",
                "invalid-skill-root",
                rel_root,
                "skill root exists but is not a directory",
            )
        ]

    for dirpath, dirnames, filenames in os.walk(
        skill_dir,
        topdown=True,
        followlinks=False,
    ):
        base = Path(dirpath)
        safe_dirs: list[str] = []
        for name in sorted(dirnames):
            child = base / name
            rel = child.relative_to(skill_dir).as_posix()
            if name == "__pycache__":
                continue
            if child.is_symlink():
                findings.append(
                    Finding(
                        "high",
                        "skill-directory-symlink",
                        f"{rel_root}/{rel}",
                        "symlinked directories are excluded from integrity hashing",
                    )
                )
                continue
            safe_dirs.append(name)
        dirnames[:] = safe_dirs

        for name in sorted(filenames):
            path = base / name
            rel = path.relative_to(skill_dir).as_posix()
            full_rel = f"{rel_root}/{rel}"
            if "__pycache__" in path.parts:
                continue
            if path.is_symlink():
                findings.append(
                    Finding(
                        "high",
                        "skill-file-symlink",
                        full_rel,
                        "symlinked files are excluded from integrity hashing",
                    )
                )
                continue
            if not path.is_file():
                findings.append(
                    Finding(
                        "medium",
                        "special-skill-file",
                        full_rel,
                        "non-regular file is excluded from integrity hashing",
                    )
                )
                continue
            if len(entries) >= MAX_HASH_FILES:
                raise ValueError(
                    f"skill tree exceeds hash file limit of {MAX_HASH_FILES}"
                )
            try:
                entries.append((rel, _hash_file(path)))
            except ValueError as exc:
                findings.append(
                    Finding("medium", "unhashable-skill-file", full_rel, str(exc))
                )
    return entries, findings


def _absolute_lexical(repo: Path, raw: Path) -> Path:
    candidate = raw.expanduser()
    if not candidate.is_absolute():
        candidate = repo / candidate
    absolute = Path(os.path.abspath(os.fspath(candidate)))
    try:
        absolute.relative_to(repo)
    except ValueError as exc:
        raise ValueError(
            "integrity manifest must remain inside the repository"
        ) from exc
    return absolute


def _reject_symlink_components(repo: Path, path: Path) -> None:
    relative = path.relative_to(repo)
    current = repo
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(
                f"refusing symlink component in integrity manifest path: {current}"
            )


def _manifest_target(
    repo: Path,
    manifest: Path,
    skill_dirs: list[tuple[str, Path]],
) -> Path:
    target = _absolute_lexical(repo, manifest)
    _reject_symlink_components(repo, target)
    resolved = target.resolve(strict=False)
    try:
        resolved.relative_to(repo)
    except ValueError as exc:
        raise ValueError(
            "integrity manifest must remain inside the repository"
        ) from exc

    for rel, _ in skill_dirs:
        skill_root = (repo / rel).resolve(strict=False)
        try:
            resolved.relative_to(skill_root)
        except ValueError:
            continue
        raise ValueError(
            "integrity manifest must be outside the hashed skill directory"
        )
    return resolved


def _atomic_manifest_write(repo: Path, target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target = _absolute_lexical(repo, target)
    _reject_symlink_components(repo, target)
    fd, temp_name = tempfile.mkstemp(
        prefix=".aipc-manifest-",
        suffix=".tmp",
        dir=str(target.parent),
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        target = _absolute_lexical(repo, target)
        _reject_symlink_components(repo, target)
        os.replace(temp_path, target)
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


def scan(repo: Path, manifest: Path | None = None) -> GuardReport:
    repo = repo.expanduser().resolve()
    if not repo.is_dir():
        raise ValueError(f"repository directory does not exist: {repo}")

    findings: list[Finding] = []
    warnings: list[str] = []
    workflow_paths: list[Path] = []
    workflows_dir = repo / ".github" / "workflows"

    if workflows_dir.is_symlink():
        findings.append(
            Finding(
                "high",
                "workflows-directory-symlink",
                ".github/workflows",
                "workflow directory is a symlink and was not followed",
            )
        )
    elif workflows_dir.exists() and workflows_dir.is_dir():
        for path in sorted(workflows_dir.iterdir()):
            if path.suffix.casefold() not in {".yml", ".yaml"}:
                continue
            rel = path.relative_to(repo).as_posix()
            if path.is_symlink():
                findings.append(
                    Finding(
                        "high",
                        "workflow-symlink",
                        rel,
                        "symlinked workflow was not followed or scanned",
                    )
                )
                continue
            if not path.is_file():
                continue
            workflow_paths.append(path)
            try:
                findings.extend(_workflow_findings(path, rel))
            except (OSError, ValueError) as exc:
                findings.append(
                    Finding("high", "unreadable-workflow", rel, str(exc))
                )

    skill_dirs = [(rel, repo / rel) for rel in SKILL_INSTALL_PATHS]
    hashes: list[tuple[str, str]] = []
    for rel, skill_dir in skill_dirs:
        try:
            local_hashes, local_findings = _hash_files(skill_dir, rel)
        except (OSError, ValueError) as exc:
            local_hashes = []
            local_findings = [
                Finding("high", "skill-hash-failed", rel, str(exc))
            ]
        findings.extend(local_findings)
        hashes.extend(
            (f"{rel}/{path}", digest) for path, digest in local_hashes
        )
        if skill_dir.exists() and not local_hashes:
            findings.append(
                Finding(
                    "medium",
                    "empty-skill-manifest",
                    rel,
                    "skill directory exists but no regular files were hashable",
                )
            )

    if not hashes:
        roots = ", ".join(f"`{path}`" for path in SKILL_INSTALL_PATHS)
        warnings.append(
            f"no hashable skill files detected under supported installation roots: {roots}"
        )

    manifest_written: str | None = None
    if manifest is not None:
        target = _manifest_target(repo, manifest, skill_dirs)
        content = "".join(
            f"{digest}  {rel}\n" for rel, digest in sorted(hashes)
        )
        _atomic_manifest_write(repo, target, content)
        manifest_written = str(target)

    penalty = {"low": 4, "medium": 10, "high": 24, "critical": 40}
    score = max(
        0,
        100 - sum(penalty.get(item.severity, 8) for item in findings),
    )
    return GuardReport(
        score,
        findings,
        len(workflow_paths),
        len(hashes),
        manifest_written,
        warnings,
    )


def markdown(report: GuardReport) -> str:
    lines = [
        "# Supply-chain guard",
        "",
        f"- Guard score: **{report.score}/100**",
        f"- Workflow files scanned: **{report.workflow_files}**",
        f"- Skill files hashed: **{report.integrity_files}**",
    ]
    if report.manifest_written:
        lines.append(f"- Integrity manifest: `{report.manifest_written}`")
    lines.extend(["", "## Findings"])
    if report.findings:
        for item in report.findings:
            lines.append(
                f"- **{item.severity.upper()}** `{item.code}` in "
                f"`{item.path}` — {item.message}"
            )
    else:
        lines.append("- No heuristic findings detected")
    if report.warnings:
        lines.extend(["", "## Warnings"])
        lines.extend(f"- {item}" for item in report.warnings)
    lines.extend(
        [
            "",
            "> This is a focused heuristic review, not a complete security audit. Confirm findings against the workflow's actual trust model.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        help="optional path to write SHA-256 manifest; omitted by default",
    )
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
