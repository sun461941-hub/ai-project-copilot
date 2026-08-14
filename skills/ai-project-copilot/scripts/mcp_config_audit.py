#!/usr/bin/env python3
"""Read-only audit for common MCP client configuration risks."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

COMMON_PATHS = (
    ".mcp.json",
    "mcp.json",
    ".cursor/mcp.json",
    ".vscode/mcp.json",
    ".gemini/mcp.json",
)
SECRET_KEY_PARTS = {"token", "secret", "password", "credential", "credentials"}
SECRET_KEY_COMPOUNDS = {"api_key", "apikey", "private_key", "access_token", "client_secret"}
ENV_REF = re.compile(
    r"^(?:\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*|"
    r"\$env:[A-Za-z_][A-Za-z0-9_]*|%[A-Za-z_][A-Za-z0-9_]*%|"
    r"env:[A-Za-z_][A-Za-z0-9_]*|\{\{[^{}]+\}\})$",
    flags=re.IGNORECASE,
)
PLACEHOLDER = re.compile(r"(?:example|placeholder|changeme|replace[-_ ]?me|your[-_ ]|dummy|test[-_ ]?only)", re.IGNORECASE)
PINNED = re.compile(r"(?:^|@)v?\d+(?:\.\d+){1,2}(?:[-+][A-Za-z0-9_.-]+)?$")


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    path: str
    location: str
    message: str


@dataclass(frozen=True)
class MCPAuditReport:
    files_scanned: int
    score: int
    findings: list[Finding]
    warnings: list[str]


def _is_env_reference(value: str) -> bool:
    return bool(ENV_REF.fullmatch(value.strip()))


def _is_secret_key(key: str) -> bool:
    # Split snake/kebab/dotted and camelCase names while avoiding substring
    # accidents such as `tokenizer` or `secretaryMode`.
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", expanded).strip("_").casefold()
    # Accept a vendor/application prefix (OPENAI_API_KEY, anthropicApiKey,
    # MY_SERVICE_ACCESS_TOKEN) while still avoiding generic ``key`` matches.
    if any(normalized == compound or normalized.endswith(f"_{compound}") for compound in SECRET_KEY_COMPOUNDS):
        return True
    parts = [part for part in normalized.split("_") if part]
    return any(part in SECRET_KEY_PARTS for part in parts)


def _is_loopback_http(value: str) -> bool:
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return False
    if parsed.scheme.casefold() != "http":
        return False
    host = (parsed.hostname or "").casefold()
    return host in {"localhost", "127.0.0.1", "::1"}


def _walk(value: Any, prefix: str = "$"):
    if isinstance(value, dict):
        for key, child in value.items():
            location = f"{prefix}.{key}"
            yield key, child, location
            yield from _walk(child, location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            location = f"{prefix}[{index}]"
            yield str(index), child, location
            yield from _walk(child, location)


def _command_name(command: str) -> str:
    return command.replace("\\", "/").rsplit("/", 1)[-1].casefold()


def _package_unpinned(command: str, args: list[str]) -> str | None:
    command = _command_name(command)
    if command not in {"npx", "npx.cmd", "uvx", "bunx", "pnpx"}:
        return None
    candidates = [arg for arg in args if arg and not arg.startswith("-")]
    if not candidates:
        return None
    package = candidates[0]
    if package in {"--", "run"} and len(candidates) > 1:
        package = candidates[1]
    # Scoped npm package without a trailing version is not pinned: @scope/pkg.
    if package.startswith("@"):
        slash = package.find("/")
        suffix_at = package.rfind("@")
        pinned = slash > 0 and suffix_at > slash and bool(PINNED.search(package[suffix_at:]))
    else:
        suffix_at = package.rfind("@")
        pinned = suffix_at > 0 and bool(PINNED.search(package[suffix_at:]))
    if command == "uvx":
        pinned = "==" in package and bool(re.search(r"==\d", package))
    return None if pinned else package


def _audit_config(path: Path, rel: str) -> tuple[list[Finding], list[str]]:
    findings: list[Finding] = []
    warnings: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        return [Finding("high", "invalid-config", rel, "$", f"MCP config could not be parsed as JSON: {exc}")], warnings

    for key, value, location in _walk(data):
        folded_key = key.casefold()
        if isinstance(value, str):
            if _is_secret_key(key):
                stripped = value.strip()
                if stripped and not _is_env_reference(stripped) and not PLACEHOLDER.search(stripped):
                    findings.append(Finding("high", "hardcoded-secret", rel, location, "secret-like setting contains a literal value; prefer an environment/secure-settings reference"))
            if folded_key in {"url", "endpoint", "serverurl", "server_url"} and value.strip().casefold().startswith("http://") and not _is_loopback_http(value):
                findings.append(Finding("medium", "insecure-transport", rel, location, "remote MCP endpoint uses http:// instead of an authenticated/encrypted transport"))
            if folded_key == "command":
                command = _command_name(value)
                if command in {"sh", "bash", "zsh", "cmd", "cmd.exe", "powershell", "pwsh"}:
                    findings.append(Finding("medium", "shell-wrapper", rel, location, f"MCP server launches through `{value}`; review argument injection and trust boundary"))

    # Inspect common server objects for command + args package pinning.
    def visit(obj: Any, prefix: str = "$") -> None:
        if isinstance(obj, dict):
            command = obj.get("command")
            args = obj.get("args", [])
            if isinstance(command, str) and isinstance(args, list) and all(isinstance(item, str) for item in args):
                package = _package_unpinned(command, list(args))
                if package:
                    findings.append(Finding("medium", "unpinned-runner-package", rel, prefix, f"`{command}` launches `{package}` without an explicit version pin"))
                joined = " ".join(args).casefold()
                if "@latest" in joined:
                    findings.append(Finding("medium", "latest-package", rel, prefix, "MCP launcher explicitly requests @latest; use a reviewed version for reproducibility"))
            for key, child in obj.items():
                visit(child, f"{prefix}.{key}")
        elif isinstance(obj, list):
            for index, child in enumerate(obj):
                visit(child, f"{prefix}[{index}]")
    visit(data)

    if not findings:
        warnings.append(f"{rel}: no configured-risk heuristic fired; still review server identity, permissions, and data access")
    return findings, warnings


def scan(repo: Path, paths: list[Path] | None = None) -> MCPAuditReport:
    repo = repo.expanduser().resolve()
    if not repo.exists() or not repo.is_dir():
        raise ValueError(f"repository directory does not exist: {repo}")
    candidates: list[Path] = []
    if paths:
        for item in paths:
            candidate = item.expanduser()
            if not candidate.is_absolute():
                candidate = repo / candidate
            resolved = candidate.resolve()
            try:
                resolved.relative_to(repo)
            except ValueError as exc:
                raise ValueError(f"MCP config path must remain inside repository: {item}") from exc
            candidates.append(resolved)
    else:
        candidates = [repo / item for item in COMMON_PATHS]
    candidates = sorted({path.resolve() for path in candidates if path.exists() and path.is_file() and not path.is_symlink()})

    findings: list[Finding] = []
    warnings: list[str] = []
    for path in candidates:
        try:
            rel = path.relative_to(repo).as_posix()
        except ValueError:
            rel = str(path)
        try:
            local_findings, local_warnings = _audit_config(path, rel)
        except RecursionError:
            local_findings = [Finding("high", "invalid-config", rel, "$", "MCP config nesting is too deep to audit safely")]
            local_warnings = []
        findings.extend(local_findings)
        warnings.extend(local_warnings)
    if not candidates:
        warnings.append("no common MCP JSON config detected")

    penalty = {"low": 4, "medium": 12, "high": 28, "critical": 45}
    score = max(0, 100 - sum(penalty.get(item.severity, 8) for item in findings))
    return MCPAuditReport(len(candidates), score, findings, warnings)


def markdown(report: MCPAuditReport) -> str:
    lines = [
        "# MCP configuration audit", "",
        f"- Files scanned: **{report.files_scanned}**",
        f"- Heuristic score: **{report.score}/100**",
        "", "## Findings",
    ]
    if report.findings:
        for item in report.findings:
            lines.append(f"- **{item.severity.upper()}** `{item.code}` in `{item.path}` `{item.location}` — {item.message}")
    else:
        lines.append("- No configured-risk heuristic findings")
    if report.warnings:
        lines.extend(["", "## Warnings"])
        lines.extend(f"- {item}" for item in report.warnings)
    lines.extend([
        "",
        "> This is a configuration-focused heuristic. It does not prove the MCP server implementation, package, credentials, or remote service is trustworthy.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--path", type=Path, action="append", help="explicit MCP JSON config path; repeatable")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    try:
        report = scan(args.repo, args.path)
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
