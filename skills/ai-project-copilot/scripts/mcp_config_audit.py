#!/usr/bin/env python3
"""Read-only audit for common MCP client configuration risks."""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

COMMON_PATHS = (
    ".mcp.json",
    "mcp.json",
    ".cursor/mcp.json",
    ".vscode/mcp.json",
    ".gemini/mcp.json",
)
SECRET_KEY_PARTS = {"token", "secret", "password", "credential", "credentials"}
SECRET_KEY_COMPOUNDS = {
    "api_key",
    "apikey",
    "private_key",
    "access_token",
    "client_secret",
}
ENV_REF = re.compile(
    r"^(?:\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*|"
    r"\$env:[A-Za-z_][A-Za-z0-9_]*|%[A-Za-z_][A-Za-z0-9_]*%|"
    r"env:[A-Za-z_][A-Za-z0-9_]*|\{\{[^{}]+\}\})$",
    flags=re.IGNORECASE,
)
PLACEHOLDER = re.compile(
    r"(?:example|placeholder|changeme|replace[-_ ]?me|your[-_ ]|dummy|test[-_ ]?only)",
    re.IGNORECASE,
)
PINNED = re.compile(r"(?:^|@)v?\d+(?:\.\d+){1,2}(?:[-+][A-Za-z0-9_.-]+)?$")
MAX_CONFIG_BYTES = 2 * 1024 * 1024
MAX_JSON_NESTING = 256
MAX_NODES = 100_000


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
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", expanded).strip("_").casefold()
    if any(
        normalized == compound or normalized.endswith(f"_{compound}")
        for compound in SECRET_KEY_COMPOUNDS
    ):
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


def _json_nesting_exceeds(text: str, maximum: int = MAX_JSON_NESTING) -> bool:
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > maximum:
                return True
        elif char in "]}":
            depth = max(0, depth - 1)
    return False


def _walk(value: Any, prefix: str = "$") -> Iterator[tuple[str, Any, str]]:
    """Iterative walk with a hard node cap; never recurse on attacker JSON."""
    stack: list[tuple[str, Any]] = [(prefix, value)]
    visited = 0
    while stack:
        location, current = stack.pop()
        visited += 1
        if visited > MAX_NODES:
            raise ValueError(f"MCP config exceeds the safe node limit of {MAX_NODES}")
        if isinstance(current, dict):
            items = list(current.items())
            for key, child in reversed(items):
                child_location = f"{location}.{key}"
                stack.append((child_location, child))
                yield str(key), child, child_location
        elif isinstance(current, list):
            for index in range(len(current) - 1, -1, -1):
                child = current[index]
                child_location = f"{location}[{index}]"
                stack.append((child_location, child))
                yield str(index), child, child_location


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
    if package.startswith("@"):
        slash = package.find("/")
        suffix_at = package.rfind("@")
        pinned = (
            slash > 0
            and suffix_at > slash
            and bool(PINNED.search(package[suffix_at:]))
        )
    else:
        suffix_at = package.rfind("@")
        pinned = suffix_at > 0 and bool(PINNED.search(package[suffix_at:]))
    if command == "uvx":
        pinned = "==" in package and bool(re.search(r"==\d", package))
    return None if pinned else package


def _lexical_candidate(repo: Path, raw: Path) -> Path:
    candidate = raw.expanduser()
    if not candidate.is_absolute():
        candidate = repo / candidate
    absolute = Path(os.path.abspath(os.fspath(candidate)))
    try:
        absolute.relative_to(repo)
    except ValueError as exc:
        raise ValueError(
            f"MCP config path must remain inside repository: {raw}"
        ) from exc
    return absolute


def _first_symlink_component(repo: Path, path: Path) -> Path | None:
    try:
        relative = path.relative_to(repo)
    except ValueError:
        return path
    current = repo
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return current
    return None


def _read_config(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("MCP config must be a regular non-symlink file")
    size = path.stat().st_size
    if size > MAX_CONFIG_BYTES:
        raise ValueError(f"MCP config exceeds {MAX_CONFIG_BYTES} bytes")
    with path.open("rb") as handle:
        raw = handle.read(MAX_CONFIG_BYTES + 1)
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError(f"MCP config exceeds {MAX_CONFIG_BYTES} bytes")
    return raw.decode("utf-8-sig")


def _audit_config(path: Path, rel: str) -> tuple[list[Finding], list[str]]:
    findings: list[Finding] = []
    warnings: list[str] = []
    try:
        text = _read_config(path)
        if _json_nesting_exceeds(text):
            raise ValueError(
                f"MCP config nesting exceeds the safe limit of {MAX_JSON_NESTING}"
            )
        data = json.loads(text)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ) as exc:
        return [
            Finding(
                "high",
                "invalid-config",
                rel,
                "$",
                f"MCP config could not be parsed/audited safely: {exc}",
            )
        ], warnings

    stack: list[tuple[str, Any]] = [("$", data)]
    visited = 0
    try:
        while stack:
            prefix, obj = stack.pop()
            visited += 1
            if visited > MAX_NODES:
                raise ValueError(
                    f"MCP config exceeds the safe node limit of {MAX_NODES}"
                )
            if isinstance(obj, dict):
                command = obj.get("command")
                args = obj.get("args", [])
                if (
                    isinstance(command, str)
                    and isinstance(args, list)
                    and all(isinstance(item, str) for item in args)
                ):
                    package = _package_unpinned(command, list(args))
                    if package:
                        findings.append(
                            Finding(
                                "medium",
                                "unpinned-runner-package",
                                rel,
                                prefix,
                                f"`{command}` launches `{package}` without an explicit version pin",
                            )
                        )
                    if "@latest" in " ".join(args).casefold():
                        findings.append(
                            Finding(
                                "medium",
                                "latest-package",
                                rel,
                                prefix,
                                "MCP launcher explicitly requests @latest; use a reviewed version",
                            )
                        )

                for key, value in reversed(list(obj.items())):
                    location = f"{prefix}.{key}"
                    folded_key = str(key).casefold()
                    if isinstance(value, str):
                        if _is_secret_key(str(key)):
                            stripped = value.strip()
                            if (
                                stripped
                                and not _is_env_reference(stripped)
                                and not PLACEHOLDER.search(stripped)
                            ):
                                findings.append(
                                    Finding(
                                        "high",
                                        "hardcoded-secret",
                                        rel,
                                        location,
                                        "secret-like setting contains a literal value; prefer an environment/secure-settings reference",
                                    )
                                )
                        if (
                            folded_key
                            in {"url", "endpoint", "serverurl", "server_url"}
                            and value.strip().casefold().startswith("http://")
                            and not _is_loopback_http(value)
                        ):
                            findings.append(
                                Finding(
                                    "medium",
                                    "insecure-transport",
                                    rel,
                                    location,
                                    "remote MCP endpoint uses http:// instead of an encrypted transport",
                                )
                            )
                        if folded_key == "command":
                            name = _command_name(value)
                            if name in {
                                "sh",
                                "bash",
                                "zsh",
                                "cmd",
                                "cmd.exe",
                                "powershell",
                                "pwsh",
                            }:
                                findings.append(
                                    Finding(
                                        "medium",
                                        "shell-wrapper",
                                        rel,
                                        location,
                                        f"MCP server launches through `{value}`; review argument injection and trust boundary",
                                    )
                                )
                    stack.append((location, value))
            elif isinstance(obj, list):
                for index in range(len(obj) - 1, -1, -1):
                    stack.append((f"{prefix}[{index}]", obj[index]))
    except ValueError as exc:
        return [
            Finding(
                "high",
                "invalid-config",
                rel,
                "$",
                f"MCP config could not be audited safely: {exc}",
            )
        ], warnings

    if not findings:
        warnings.append(
            f"{rel}: no configured-risk heuristic fired; still review server identity, permissions, and data access"
        )
    return findings, warnings


def scan(repo: Path, paths: list[Path] | None = None) -> MCPAuditReport:
    repo = repo.expanduser().resolve()
    if not repo.exists() or not repo.is_dir():
        raise ValueError(f"repository directory does not exist: {repo}")

    findings: list[Finding] = []
    warnings: list[str] = []
    candidates: list[Path] = []

    requested_paths = paths if paths else [Path(item) for item in COMMON_PATHS]
    for item in requested_paths:
        candidate = _lexical_candidate(repo, item)
        symlink = _first_symlink_component(repo, candidate)
        rel = candidate.relative_to(repo).as_posix()
        if symlink is not None:
            message = f"MCP config path contains symlink component: {symlink}"
            if paths:
                raise ValueError(message)
            findings.append(Finding("high", "config-symlink", rel, "$", message))
            continue
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(repo)
        except ValueError as exc:
            raise ValueError(
                f"MCP config path must remain inside repository: {item}"
            ) from exc
        if candidate.exists() and candidate.is_file():
            candidates.append(candidate)

    unique = sorted({path for path in candidates})
    for path in unique:
        rel = path.relative_to(repo).as_posix()
        local_findings, local_warnings = _audit_config(path, rel)
        findings.extend(local_findings)
        warnings.extend(local_warnings)

    if not unique and not findings:
        warnings.append("no common MCP JSON config detected")

    penalty = {"low": 4, "medium": 12, "high": 28, "critical": 45}
    score = max(
        0,
        100 - sum(penalty.get(item.severity, 8) for item in findings),
    )
    return MCPAuditReport(len(unique), score, findings, warnings)


def markdown(report: MCPAuditReport) -> str:
    lines = [
        "# MCP configuration audit",
        "",
        f"- Files scanned: **{report.files_scanned}**",
        f"- Heuristic score: **{report.score}/100**",
        "",
        "## Findings",
    ]
    if report.findings:
        for item in report.findings:
            lines.append(
                f"- **{item.severity.upper()}** `{item.code}` in `{item.path}` "
                f"`{item.location}` — {item.message}"
            )
    else:
        lines.append("- No configured-risk heuristic findings")
    if report.warnings:
        lines.extend(["", "## Warnings"])
        lines.extend(f"- {item}" for item in report.warnings)
    lines.extend(
        [
            "",
            "> This is a configuration-focused heuristic. It does not prove the MCP server implementation, package, credentials, or remote service is trustworthy.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument(
        "--path",
        type=Path,
        action="append",
        help="explicit MCP JSON config path; repeatable",
    )
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
