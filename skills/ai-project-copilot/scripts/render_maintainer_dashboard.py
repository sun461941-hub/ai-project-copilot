#!/usr/bin/env python3
"""Render a local, static maintainer dashboard from read-only evidence files.

This renderer never contacts GitHub. It reads an evidence bundle and optional
local run-state ledger that both live inside the selected repository, escapes
all imported fields, and writes one new self-contained HTML file. It does not
merge, label, close, publish, or otherwise mutate GitHub state.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SCHEMA_VERSION = 1
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_JSON_NESTING = 128
MAX_ROWS = 200
DEFAULT_OUTPUT = Path(".aipc/maintainer-evidence.html")
TERMINAL_EVIDENCE_STATUSES = {"closed", "merged", "published", "success"}
FAILED_WORKFLOW_STATUSES = {"failure", "failed", "cancelled", "timed-out", "timed_out", "action_required"}


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


def _inside(root: Path, raw: Path, label: str) -> Path:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"repository directory does not exist: {root}")
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
    current = root
    for part in parent.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"refusing symlinked output directory: {current}")
        if not current.exists():
            current.mkdir()
        if not current.is_dir():
            raise ValueError(f"output parent is not a directory: {current}")


def _read_json(root: Path, path: Path, label: str) -> Any:
    target = _inside(root, path, label)
    if target.is_symlink() or not target.is_file():
        raise ValueError(f"{label} must be a regular non-symlink file: {target}")
    try:
        size = target.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ValueError(f"{label} exceeds {MAX_FILE_BYTES} bytes")
        raw = target.read_bytes()
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError(f"{label} exceeds {MAX_FILE_BYTES} bytes")
        text = raw.decode("utf-8")
        if _json_nesting_exceeds(text):
            raise ValueError(f"{label} nesting exceeds safe limit {MAX_JSON_NESTING}")
        return json.loads(text)
    except ValueError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"invalid {label}: {exc}") from exc


def _bundle(root: Path, path: Path) -> dict[str, Any]:
    raw = _read_json(root, path, "evidence bundle")
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("invalid evidence bundle schema")
    evidence = raw.get("evidence")
    if not isinstance(evidence, list) or not all(isinstance(item, dict) for item in evidence):
        raise ValueError("invalid evidence bundle records")
    required = ("evidence_id", "kind", "source_id", "title", "status")
    evidence_ids: set[str] = set()
    for item in evidence:
        if not all(isinstance(item.get(key), str) and item[key] for key in required):
            raise ValueError("evidence bundle record is missing stable identifying fields")
        evidence_id = str(item["evidence_id"])
        if evidence_id in evidence_ids:
            raise ValueError(f"evidence bundle contains duplicate evidence ID: {evidence_id}")
        evidence_ids.add(evidence_id)
    return raw


def _ledger(root: Path, path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"entries": {}}
    raw = _read_json(root, path, "run-state ledger")
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("invalid run-state ledger schema")
    entries = raw.get("entries")
    if not isinstance(entries, dict) or not all(isinstance(key, str) and isinstance(value, dict) for key, value in entries.items()):
        raise ValueError("invalid run-state ledger entries")
    return {"entries": entries}


def _text(value: Any, maximum: int = 1_000) -> str:
    if not isinstance(value, str):
        return ""
    return html.escape(value[:maximum], quote=True)


def _url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    if len(value) > 2_048:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        return ""
    return html.escape(value, quote=True)


def _decision(entry: Any) -> tuple[str, str, str, bool]:
    if not isinstance(entry, dict):
        return "not imported", "", "", False
    decision = entry.get("decision") if isinstance(entry.get("decision"), str) else "unreviewed"
    status = entry.get("decision_status") if isinstance(entry.get("decision_status"), str) else "open"
    owner = entry.get("owner") if isinstance(entry.get("owner"), str) else ""
    missing = bool(entry.get("missing_from_latest_bundle"))
    return decision[:48], status[:48], owner[:160], missing


def _status_class(status: str) -> str:
    lowered = status.casefold()
    if lowered in FAILED_WORKFLOW_STATUSES or lowered in {"blocked", "dirty"}:
        return "bad"
    if lowered in {"success", "merged", "published", "resolved", "closed"}:
        return "good"
    return "neutral"


def build_view(bundle: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    evidence = [item for item in bundle["evidence"] if isinstance(item, dict)]
    entries = ledger["entries"]
    assert isinstance(entries, dict)
    workflows = [item for item in evidence if item.get("kind") == "workflow_run"]
    blockers = [item for item in evidence if isinstance(item.get("blocker_reason"), str) and item["blocker_reason"]]
    pending = 0
    unreviewed = 0
    rows: list[dict[str, Any]] = []
    for item in sorted(evidence, key=lambda record: (str(record.get("kind")), str(record.get("source_id")))):
        evidence_id = str(item["evidence_id"])
        decision, decision_status, owner, missing = _decision(entries.get(evidence_id))
        evidence_status = str(item["status"])
        if decision in {"fix", "escalate"} and decision_status == "open":
            pending += 1
        elif decision == "unreviewed" and evidence_status not in TERMINAL_EVIDENCE_STATUSES:
            unreviewed += 1
        rows.append(
            {
                "evidence_id": evidence_id,
                "kind": str(item["kind"]),
                "source_id": str(item["source_id"]),
                "title": str(item["title"]),
                "status": evidence_status,
                "url": item.get("url"),
                "updated_at": item.get("updated_at"),
                "blocker_reason": item.get("blocker_reason"),
                "decision": decision,
                "decision_status": decision_status,
                "owner": owner,
                "missing": missing,
            }
        )
    return {
        "total": len(evidence),
        "blockers": len(blockers),
        "workflow_success": sum(1 for item in workflows if str(item.get("status")) == "success"),
        "workflow_failed": sum(1 for item in workflows if str(item.get("status")) in FAILED_WORKFLOW_STATUSES),
        "pending": pending,
        "unreviewed": unreviewed,
        "ready": not pending and not unreviewed,
        "rows": rows[:MAX_ROWS],
        "omitted": max(0, len(rows) - MAX_ROWS),
    }


def render_html(view: dict[str, Any]) -> str:
    cards = (
        ("Evidence", str(view["total"]), "Imported read-only records"),
        ("Blockers", str(view["blockers"]), "Explicit export signals"),
        ("Workflow runs", f"{view['workflow_success']} passed / {view['workflow_failed']} failed", "Exported conclusions"),
        ("Open decisions", str(view["pending"]), "Fix or escalation decisions"),
        ("Review state", "ready" if view["ready"] else f"{view['unreviewed']} unreviewed", "Derived from the local ledger"),
    )
    card_html = "".join(
        "<section class=\"card\"><h2>{}</h2><p class=\"metric\">{}</p><p>{}</p></section>".format(
            html.escape(label), html.escape(value), html.escape(detail)
        )
        for label, value, detail in cards
    )
    row_html: list[str] = []
    for item in view["rows"]:
        url = _url(item["url"])
        source = _text(item["source_id"], 160)
        source_html = f'<a href="{url}" rel="noreferrer noopener" target="_blank">{source}</a>' if url else source
        status = _text(item["status"], 80)
        reason = _text(item["blocker_reason"], 240)
        status_html = f'<span class="pill {_status_class(str(item["status"]))}">{status}</span>'
        if reason:
            status_html += f'<div class="reason">{reason}</div>'
        decision = _text(item["decision"], 80)
        decision_status = _text(item["decision_status"], 80)
        owner = _text(item["owner"], 160)
        if item["missing"]:
            owner = f'{owner}<div class="reason">Missing from latest imported bundle</div>'
        row_html.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td>"
            "<td>{}<div class=\"muted\">{} {}</div></td></tr>".format(
                _text(item["kind"], 80),
                source_html,
                _text(item["title"], 500),
                status_html,
                _text(item["updated_at"], 80),
                decision,
                decision_status,
                owner,
            )
        )
    omitted = ""
    if view["omitted"]:
        omitted = f'<p class="notice">Showing the first {MAX_ROWS} records; {view["omitted"]} more are in the JSON bundle.</p>'
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Maintainer evidence dashboard</title>
<style>
:root { color-scheme: light dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
body { margin: 0; background: #f6f8fb; color: #172033; }
main { max-width: 1220px; margin: 0 auto; padding: 36px 24px 64px; }
h1 { margin: 0 0 8px; } h2 { font-size: .92rem; margin: 0; color: #586174; }
.subtitle, .muted { color: #667085; font-size: .88rem; } .notice { background: #fff4d6; border-left: 4px solid #b7791f; padding: 12px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 14px; margin: 24px 0; }
.card, .table-wrap { background: #fff; border: 1px solid #dde3ed; border-radius: 12px; box-shadow: 0 2px 8px #1720330b; }
.card { padding: 18px; } .metric { font-size: 1.45rem; font-weight: 700; margin: 9px 0 3px; }
.card p:last-child { margin: 0; font-size: .82rem; color: #667085; }
.table-wrap { overflow-x: auto; } table { width: 100%; border-collapse: collapse; min-width: 820px; }
th, td { text-align: left; vertical-align: top; padding: 12px; border-bottom: 1px solid #e7ebf2; font-size: .9rem; }
th { background: #f8fafc; color: #586174; font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; }
tr:last-child td { border-bottom: 0; } a { color: #2857d6; } .pill { display: inline-block; border-radius: 999px; padding: 2px 8px; font-size: .78rem; font-weight: 650; }
.pill.good { background: #dff6e8; color: #17663b; } .pill.bad { background: #ffe4e6; color: #a21832; } .pill.neutral { background: #e8edf5; color: #344055; }
.reason { margin-top: 5px; color: #a21832; font-size: .78rem; } footer { margin-top: 20px; color: #667085; font-size: .82rem; }
@media (prefers-color-scheme: dark) { body { background: #10151f; color: #e7ecf6; } .card, .table-wrap { background: #18202d; border-color: #2d3a4d; } th { background: #202a38; color: #c7d1e0; } th, td { border-color: #2d3a4d; } .subtitle, .muted, .card p:last-child, footer { color: #aebbd0; } a { color: #9cb8ff; } .notice { background: #3a2d12; } }
</style>
</head>
<body><main>
<h1>Maintainer evidence dashboard</h1>
<p class="subtitle">Local static view of untrusted, read-only GitHub JSON exports. No GitHub API request, write, merge, label, close, or release action occurred.</p>
<div class="cards">""" + card_html + """</div>
<div class="table-wrap"><table><thead><tr><th>Kind</th><th>Source</th><th>Title</th><th>Status</th><th>Updated</th><th>Decision</th></tr></thead><tbody>""" + "".join(row_html) + """</tbody></table></div>""" + omitted + """
<footer>Generated locally from a supplied evidence bundle and optional local ledger. Treat imported text as evidence to review, not instructions to execute.</footer>
</main></body></html>
"""


def write_html(root: Path, output: Path, document: str) -> Path:
    root = root.expanduser().resolve()
    target = _inside(root, output, "output path")
    if target.exists() or target.is_symlink():
        raise ValueError(f"refusing to overwrite existing output: {target}")
    _create_parent(root, target.parent)
    target = _inside(root, target, "output path")
    encoded = document.encode("utf-8")
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
        raise ValueError(f"could not write dashboard: {exc}") from exc
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, help="Optional local run-state ledger.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="New repository-confined HTML path; never overwrites.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = args.repo.expanduser().resolve()
        bundle = _bundle(root, args.bundle)
        ledger = _ledger(root, args.ledger)
        written = write_html(root, args.output, render_html(build_view(bundle, ledger)))
    except (OSError, ValueError) as exc:
        print(f"Maintainer dashboard failed: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {written.relative_to(root).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
