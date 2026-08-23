#!/usr/bin/env python3
"""Normalize read-only GitHub JSON exports into a compact evidence bundle.

The tool never calls GitHub and never performs repository writes unless an
explicit, repository-confined ``--output`` path is supplied. Exported titles,
labels, and URLs are treated as untrusted data and are normalized as display
evidence only; they are never executed as commands or instructions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
MAX_EXPORT_BYTES = 5 * 1024 * 1024
MAX_BUNDLE_BYTES = 5 * 1024 * 1024
MAX_JSON_NESTING = 128
MAX_RECORDS_PER_KIND = 5_000
MAX_TITLE_CHARS = 280
MAX_LABELS = 16

EXPORT_FILES: dict[str, tuple[str, ...]] = {
    "issues": ("issues.json",),
    "pull_requests": ("pull_requests.json", "pulls.json"),
    "workflow_runs": ("workflow_runs.json", "workflows.json"),
    "releases": ("releases.json",),
}
PAYLOAD_KEYS: dict[str, tuple[str, ...]] = {
    "issues": ("issues", "items", "data"),
    "pull_requests": ("pull_requests", "pulls", "items", "data"),
    "workflow_runs": ("workflow_runs", "workflows", "items", "data"),
    "releases": ("releases", "items", "data"),
}
SAFE_STATUS = re.compile(r"^[a-z0-9_.-]{1,48}$")


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


def _read_json(path: Path, label: str) -> Any:
    if path.is_symlink():
        raise ValueError(f"refusing symlinked {label}: {path}")
    if not path.is_file():
        raise ValueError(f"{label} must be a regular file: {path}")
    try:
        size = path.stat().st_size
        if size > MAX_EXPORT_BYTES:
            raise ValueError(f"{label} exceeds {MAX_EXPORT_BYTES} bytes: {path.name}")
        raw = path.read_bytes()
        if len(raw) > MAX_EXPORT_BYTES:
            raise ValueError(f"{label} exceeds {MAX_EXPORT_BYTES} bytes: {path.name}")
        text = raw.decode("utf-8")
        if _json_nesting_exceeds(text):
            raise ValueError(f"{label} nesting exceeds safe limit {MAX_JSON_NESTING}: {path.name}")
        return json.loads(text)
    except ValueError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"invalid {label} JSON in {path.name}: {exc}") from exc


def _records(payload: Any, kind: str, path: Path) -> list[dict[str, Any]]:
    value = payload
    if isinstance(payload, dict):
        for key in PAYLOAD_KEYS[kind]:
            candidate = payload.get(key)
            if isinstance(candidate, list):
                value = candidate
                break
        else:
            # A single API-like record is useful for small fixtures and exports.
            if any(key in payload for key in ("id", "number", "tag_name")):
                value = [payload]
    if not isinstance(value, list):
        raise ValueError(f"{path.name} must contain a JSON array or a known array wrapper")
    if len(value) > MAX_RECORDS_PER_KIND:
        raise ValueError(f"{path.name} contains more than {MAX_RECORDS_PER_KIND} records")
    if not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{path.name} records must be JSON objects")
    return [dict(item) for item in value]


def load_exports(input_dir: Path) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    root = input_dir.expanduser()
    if root.is_symlink():
        raise ValueError(f"refusing symlinked export directory: {root}")
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"export directory does not exist: {root}")

    loaded: dict[str, list[dict[str, Any]]] = {}
    filenames: list[str] = []
    for kind, names in EXPORT_FILES.items():
        path = next((root / name for name in names if (root / name).exists() or (root / name).is_symlink()), None)
        if path is None:
            loaded[kind] = []
            continue
        payload = _read_json(path, f"{kind} export")
        loaded[kind] = _records(payload, kind, path)
        filenames.append(path.name)
    if not filenames:
        expected = ", ".join(sorted({name for names in EXPORT_FILES.values() for name in names}))
        raise ValueError(f"no recognized GitHub export found; expected one of: {expected}")
    return loaded, sorted(filenames)


def _clean_text(value: Any, maximum: int = MAX_TITLE_CHARS) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = "".join(char if char >= " " or char in "\n\t" else " " for char in value)
    cleaned = " ".join(cleaned.split())
    return cleaned[:maximum]


def _identifier(value: Any, fallback: str) -> str:
    if isinstance(value, bool) or value is None:
        return fallback
    text = _clean_text(str(value), 80)
    return text or fallback


def _status(value: Any, fallback: str = "unknown") -> str:
    text = _clean_text(str(value) if value is not None else "", 48).casefold().replace(" ", "-")
    return text if SAFE_STATUS.fullmatch(text) else fallback


def _safe_url(value: Any) -> str:
    text = _clean_text(value, 1_024)
    return text if text.startswith(("https://", "http://")) else ""


def _labels(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        raw = item.get("name") if isinstance(item, dict) else item
        label = _clean_text(raw, 80)
        if label and label not in result:
            result.append(label)
        if len(result) >= MAX_LABELS:
            break
    return result


def _evidence_id(kind: str, source_id: str) -> str:
    payload = f"aipc.github-evidence.v{SCHEMA_VERSION}|{kind}|{source_id}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:20]


def _record(
    kind: str,
    source_id: str,
    title: str,
    status: str,
    url: str,
    updated_at: str,
    labels: list[str] | None = None,
    blocker_reason: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "evidence_id": _evidence_id(kind, source_id),
        "kind": kind,
        "source_id": source_id,
        "title": title,
        "status": status,
        "url": url,
        "updated_at": updated_at,
        "untrusted": True,
    }
    if labels:
        result["labels"] = labels
    if blocker_reason:
        result["blocker_reason"] = blocker_reason
    return result


def _issue(item: dict[str, Any]) -> dict[str, Any]:
    ident = _identifier(item.get("number", item.get("id")), "unknown")
    labels = _labels(item.get("labels"))
    label_tokens = {label.casefold() for label in labels}
    blocker = "explicit blocker/security label" if label_tokens & {"blocker", "critical", "security"} else None
    return _record(
        "issue",
        f"issue:{ident}",
        _clean_text(item.get("title")) or f"Issue #{ident}",
        _status(item.get("state")),
        _safe_url(item.get("html_url")),
        _clean_text(item.get("updated_at"), 64),
        labels,
        blocker,
    )


def _pull_request(item: dict[str, Any]) -> dict[str, Any]:
    ident = _identifier(item.get("number", item.get("id")), "unknown")
    merged = bool(item.get("merged")) or bool(item.get("merged_at"))
    status = "merged" if merged else _status(item.get("state"))
    conflict = item.get("mergeable") is False or _status(item.get("mergeable_state"), "") in {"dirty", "blocked"}
    return _record(
        "pull_request",
        f"pull_request:{ident}",
        _clean_text(item.get("title")) or f"Pull request #{ident}",
        status,
        _safe_url(item.get("html_url")),
        _clean_text(item.get("updated_at"), 64),
        _labels(item.get("labels")),
        "merge conflict or blocked merge state" if conflict else None,
    )


def _workflow_run(item: dict[str, Any]) -> dict[str, Any]:
    ident = _identifier(item.get("id", item.get("run_number")), "unknown")
    conclusion = _status(item.get("conclusion"), "")
    status = conclusion or _status(item.get("status"))
    failed = status in {"failure", "failed", "cancelled", "timed-out", "timed_out", "action_required"}
    return _record(
        "workflow_run",
        f"workflow_run:{ident}",
        _clean_text(item.get("name")) or f"Workflow run {ident}",
        status,
        _safe_url(item.get("html_url")),
        _clean_text(item.get("updated_at", item.get("created_at")), 64),
        blocker_reason="workflow did not complete successfully" if failed else None,
    )


def _release(item: dict[str, Any]) -> dict[str, Any]:
    ident = _identifier(item.get("tag_name", item.get("id")), "unknown")
    if bool(item.get("draft")):
        status = "draft"
    elif bool(item.get("prerelease")):
        status = "prerelease"
    else:
        status = "published"
    return _record(
        "release",
        f"release:{ident}",
        _clean_text(item.get("name")) or f"Release {ident}",
        status,
        _safe_url(item.get("html_url")),
        _clean_text(item.get("published_at", item.get("created_at")), 64),
    )


NORMALIZERS = {
    "issues": _issue,
    "pull_requests": _pull_request,
    "workflow_runs": _workflow_run,
    "releases": _release,
}


def build_bundle(input_dir: Path) -> dict[str, Any]:
    exports, filenames = load_exports(input_dir)
    evidence: list[dict[str, Any]] = []
    for kind, records in exports.items():
        normalizer = NORMALIZERS[kind]
        evidence.extend(normalizer(item) for item in records)
    evidence.sort(key=lambda item: (str(item["kind"]), str(item["source_id"])))

    status_counts: dict[str, dict[str, int]] = {}
    for kind in ("issue", "pull_request", "workflow_run", "release"):
        records = [item for item in evidence if item["kind"] == kind]
        counts: dict[str, int] = {"total": len(records)}
        for record in records:
            status = str(record["status"])
            counts[status] = counts.get(status, 0) + 1
        status_counts[kind] = counts
    blockers = [
        {
            key: item[key]
            for key in ("evidence_id", "kind", "source_id", "title", "status", "url", "blocker_reason")
            if key in item
        }
        for item in evidence
        if item.get("blocker_reason")
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "kind": "github-json-export",
            "files": filenames,
            "network_accessed": False,
            "untrusted_content": True,
        },
        "summary": {
            "counts": status_counts,
            "blocker_count": len(blockers),
            "blockers": blockers,
        },
        "evidence": evidence,
    }


def _inside(root: Path, raw: Path, label: str) -> Path:
    root = root.expanduser().resolve()
    candidate = raw.expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    absolute = Path(os.path.abspath(os.fspath(candidate)))
    try:
        relative = absolute.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must remain inside repository") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"refusing symlinked {label} component: {current}")
    return absolute


def _create_parent(root: Path, parent: Path) -> None:
    relative = parent.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"refusing symlinked output directory: {current}")
        if not current.exists():
            current.mkdir()
        if not current.is_dir():
            raise ValueError(f"output parent is not a directory: {current}")


def write_bundle(root: Path, output: Path, bundle: dict[str, Any]) -> Path:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"repository directory does not exist: {root}")
    target = _inside(root, output, "output path")
    if target.exists() or target.is_symlink():
        raise ValueError(f"refusing to overwrite existing output: {target}")
    _create_parent(root, target.parent)
    target = _inside(root, target, "output path")
    encoded = (json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > MAX_BUNDLE_BYTES:
        raise ValueError(f"evidence bundle exceeds {MAX_BUNDLE_BYTES} bytes")
    try:
        fd = os.open(os.fspath(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    except OSError as exc:
        raise ValueError(f"could not write evidence bundle: {exc}") from exc
    return target


def _markdown_inline(value: Any, maximum: int = 500) -> str:
    """Render imported text as a harmless Markdown inline literal."""
    text = _clean_text(value, maximum)
    replacements = (
        ("\\", "\\\\"),
        ("&", "&amp;"),
        ("`", "\\`"),
        ("*", "\\*"),
        ("_", "\\_"),
        ("[", "\\["),
        ("]", "\\]"),
        ("|", "\\|"),
        ("<", "&lt;"),
        (">", "&gt;"),
    )
    for source, escaped in replacements:
        text = text.replace(source, escaped)
    return text


def markdown(bundle: dict[str, Any]) -> str:
    source = bundle["source"]
    summary = bundle["summary"]
    lines = [
        "# GitHub evidence snapshot",
        "",
        "This report was built from local, untrusted JSON exports. No GitHub API call or write occurred.",
        "",
        "## Imported files",
        "",
    ]
    lines.extend(f"- `{name}`" for name in source["files"])
    lines.extend(["", "## Counts", "", "| Kind | Status counts |", "| --- | --- |"])
    for kind, counts in summary["counts"].items():
        detail = ", ".join(f"{key}: {value}" for key, value in sorted(counts.items()))
        lines.append(f"| {kind} | {detail} |")
    blockers = summary["blockers"]
    lines.extend(["", "## Blockers", ""])
    if not blockers:
        lines.append("No explicit blocker signal was found in the imported export.")
    else:
        for item in blockers:
            kind = _markdown_inline(item["kind"], 80)
            source_id = _markdown_inline(item["source_id"], 160)
            title = _markdown_inline(item["title"])
            reason = _markdown_inline(item["blocker_reason"], 240)
            lines.append(f"- **{kind}** {source_id} — {title}: {reason}")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True, help="Directory containing GitHub JSON exports.")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root used only for a requested output path.")
    parser.add_argument("--output", type=Path, help="New, repository-confined JSON bundle path; never overwrites.")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        bundle = build_bundle(args.input_dir)
        written = write_bundle(args.repo, args.output, bundle) if args.output else None
    except (OSError, ValueError) as exc:
        print(f"GitHub evidence sync failed: {exc}", file=sys.stderr)
        return 2
    if written:
        print(f"wrote {written.relative_to(args.repo.expanduser().resolve()).as_posix()}", file=sys.stderr)
    if args.format == "json":
        print(json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(markdown(bundle), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
