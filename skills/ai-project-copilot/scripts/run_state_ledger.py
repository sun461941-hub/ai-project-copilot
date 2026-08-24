#!/usr/bin/env python3
"""Keep explicit, repository-confined decisions for imported maintainer evidence.

The ledger is intentionally local and JSON-exportable. It does not contact
GitHub, perform a merge, publish a release, or hide unresolved decisions in
agent memory. Evidence IDs come from the read-only evidence bundle and remain
stable when an item's mutable title or status changes.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 1
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_JSON_NESTING = 128
DEFAULT_LEDGER = Path(".aipc/maintainer-ledger.json")
DECISIONS = ("unreviewed", "fix", "decline", "escalate", "observe")
DECISION_STATUSES = ("open", "resolved")
TERMINAL_EVIDENCE_STATUSES = {"closed", "merged", "published", "success"}
LOCK_TIMEOUT_SECONDS = 5.0
LOCK_RETRY_SECONDS = 0.05


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
            raise ValueError(f"refusing symlinked ledger directory: {current}")
        if not current.exists():
            current.mkdir()
        if not current.is_dir():
            raise ValueError(f"ledger parent is not a directory: {current}")


@contextmanager
def _ledger_lock(root: Path, ledger_path: Path) -> Iterator[Path]:
    """Take a repository-confined cross-process lock for one ledger mutation.

    Atomic replacement protects an individual write, but it cannot prevent two
    agents from independently reading the same revision and overwriting each
    other's decisions.  An exclusive sibling lock serializes the full
    read-modify-write transaction on Windows, macOS, and Linux.
    """
    root = root.expanduser().resolve()
    target = _inside(root, ledger_path, "ledger path")
    _create_parent(root, target.parent)
    lock_path = _inside(root, target.with_name(f".{target.name}.lock"), "ledger lock path")
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    fd: int | None = None
    while fd is None:
        if lock_path.is_symlink():
            raise ValueError(f"refusing symlinked ledger lock: {lock_path}")
        if lock_path.exists() and not lock_path.is_file():
            raise ValueError(f"ledger lock path must be a regular file: {lock_path}")
        try:
            fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ValueError(f"ledger is locked by another process: {lock_path}")
            time.sleep(LOCK_RETRY_SECONDS)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "created_at": _utc_timestamp()}) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        yield target
    finally:
        if lock_path.is_symlink() or not lock_path.is_file():
            raise ValueError(f"ledger lock changed while held: {lock_path}")
        try:
            lock_path.unlink()
        except OSError as exc:
            raise ValueError(f"could not release ledger lock: {lock_path}") from exc


def _utc_timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalized_actor(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("decision actor must be a non-empty string")
    actor = " ".join(value.split())
    if not actor:
        raise ValueError("decision actor must be a non-empty string")
    if len(actor) > 160 or any(ord(character) < 32 or ord(character) == 127 for character in actor):
        raise ValueError("decision actor must be a safe string of at most 160 characters")
    return actor


def _normalized_source_commit(value: str) -> str | None:
    if not isinstance(value, str):
        raise ValueError("source commit must be a 7-64 character hexadecimal commit ID")
    source_commit = value.strip()
    if not source_commit:
        return None
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", source_commit):
        raise ValueError("source commit must be a 7-64 character hexadecimal commit ID")
    return source_commit.lower()


def _expected_revision(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("expected revision must be a non-negative integer")
    return value


def _require_expected_revision(data: dict[str, Any], expected_revision: int | None) -> int:
    revision = data.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("invalid run-state ledger revision")
    expected_revision = _expected_revision(expected_revision)
    if expected_revision is not None and revision != expected_revision:
        raise ValueError(f"ledger revision conflict: expected {expected_revision}, found {revision}")
    return revision


def _read_json(path: Path, label: str) -> Any:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular non-symlink file: {path}")
    try:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ValueError(f"{label} exceeds {MAX_FILE_BYTES} bytes")
        raw = path.read_bytes()
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


def _blank_ledger() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "revision": 0, "entries": {}, "bundle_sha256": None}


def _load_ledger(path: Path) -> dict[str, Any]:
    raw = _read_json(path, "run-state ledger")
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("invalid run-state ledger schema")
    entries = raw.get("entries")
    if not isinstance(entries, dict) or not all(isinstance(key, str) and isinstance(value, dict) for key, value in entries.items()):
        raise ValueError("invalid run-state ledger entries")
    revision = raw.get("revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("invalid run-state ledger revision")
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": revision,
        "entries": entries,
        "bundle_sha256": raw.get("bundle_sha256"),
    }


def _write(root: Path, path: Path, data: dict[str, Any], overwrite: bool) -> Path:
    root = root.expanduser().resolve()
    target = _inside(root, path, "ledger path")
    if target.exists() or target.is_symlink():
        if not overwrite:
            raise ValueError(f"refusing to overwrite existing ledger: {target}")
        if target.is_symlink() or not target.is_file():
            raise ValueError(f"ledger path must be a regular file: {target}")
    _create_parent(root, target.parent)
    target = _inside(root, target, "ledger path")
    encoded = (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        raise ValueError(f"run-state ledger exceeds {MAX_FILE_BYTES} bytes")
    fd, temp_name = tempfile.mkstemp(prefix=".ledger-", suffix=".json", dir=str(target.parent))
    temp_path = Path(temp_name)
    try:
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):
            pass
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        target = _inside(root, target, "ledger path")
        if target.is_symlink():
            raise ValueError(f"refusing symlinked ledger path: {target}")
        os.replace(temp_path, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise
    return target


def initialize(root: Path, ledger: Path = DEFAULT_LEDGER) -> dict[str, Any]:
    root = root.expanduser().resolve()
    ledger_path = _inside(root, ledger, "ledger path")
    with _ledger_lock(root, ledger_path):
        path = _write(root, ledger_path, _blank_ledger(), overwrite=False)
    return {"ledger": path.relative_to(root).as_posix(), "created": True, "revision": 0}


def _bundle(root: Path, path: Path) -> tuple[dict[str, Any], str]:
    target = _inside(root, path, "evidence bundle path")
    raw = _read_json(target, "evidence bundle")
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("invalid evidence bundle schema")
    evidence = raw.get("evidence")
    if not isinstance(evidence, list) or not all(isinstance(item, dict) for item in evidence):
        raise ValueError("invalid evidence bundle records")
    evidence_ids: set[str] = set()
    for item in evidence:
        required = ("evidence_id", "kind", "source_id", "title", "status")
        if not all(isinstance(item.get(key), str) and item.get(key) for key in required):
            raise ValueError("evidence bundle record is missing stable identifying fields")
        evidence_id = str(item["evidence_id"])
        if evidence_id in evidence_ids:
            raise ValueError(f"evidence bundle contains duplicate evidence ID: {evidence_id}")
        evidence_ids.add(evidence_id)
    encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return raw, hashlib.sha256(encoded).hexdigest()


def _new_entry(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence": record,
        "decision": "unreviewed",
        "decision_status": "open",
        "owner": None,
        "note": "",
        "history": [],
        "missing_from_latest_bundle": False,
    }


def sync(
    root: Path,
    ledger: Path,
    bundle: Path,
    *,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    root = root.expanduser().resolve()
    ledger_path = _inside(root, ledger, "ledger path")
    with _ledger_lock(root, ledger_path):
        data = _load_ledger(ledger_path)
        revision = _require_expected_revision(data, expected_revision)
        imported, digest = _bundle(root, bundle)
        entries = data["entries"]
        assert isinstance(entries, dict)
        seen: set[str] = set()
        created = 0
        updated = 0
        for record in imported["evidence"]:
            evidence_id = str(record["evidence_id"])
            seen.add(evidence_id)
            existing = entries.get(evidence_id)
            if not isinstance(existing, dict):
                entries[evidence_id] = _new_entry(record)
                created += 1
                continue
            existing["evidence"] = record
            existing["missing_from_latest_bundle"] = False
            existing.setdefault("decision", "unreviewed")
            existing.setdefault("decision_status", "open")
            existing.setdefault("owner", None)
            existing.setdefault("note", "")
            existing.setdefault("history", [])
            updated += 1
        for evidence_id, entry in entries.items():
            if evidence_id not in seen and isinstance(entry, dict):
                entry["missing_from_latest_bundle"] = True
        data["bundle_sha256"] = digest
        data["revision"] = revision + 1
        _write(root, ledger_path, data, overwrite=True)
    return {
        "created": created,
        "updated": updated,
        "missing_from_latest_bundle": sum(
            1 for entry in entries.values() if isinstance(entry, dict) and entry.get("missing_from_latest_bundle")
        ),
        "ledger": ledger_path.relative_to(root).as_posix(),
        "bundle_sha256": digest,
        "revision": revision + 1,
    }


def decide(
    root: Path,
    ledger: Path,
    evidence_id: str,
    decision: str,
    decision_status: str,
    owner: str = "",
    note: str = "",
    *,
    actor: str = "local-maintainer",
    source_commit: str = "",
    expected_revision: int | None = None,
) -> dict[str, Any]:
    if decision not in DECISIONS or decision == "unreviewed":
        raise ValueError("decision must be fix, decline, escalate, or observe")
    if decision_status not in DECISION_STATUSES:
        raise ValueError("decision status must be open or resolved")
    owner = " ".join(owner.split())[:160]
    note = " ".join(note.split())[:1_000]
    if decision == "escalate" and not owner:
        raise ValueError("escalate decisions require a human owner")
    if decision == "decline" and not note:
        raise ValueError("decline decisions require a concise evidence note")
    actor = _normalized_actor(actor)
    source_commit = _normalized_source_commit(source_commit)
    root = root.expanduser().resolve()
    path = _inside(root, ledger, "ledger path")
    with _ledger_lock(root, path):
        data = _load_ledger(path)
        revision = _require_expected_revision(data, expected_revision)
        entries = data["entries"]
        assert isinstance(entries, dict)
        entry = entries.get(evidence_id)
        if not isinstance(entry, dict):
            raise ValueError(f"unknown evidence ID: {evidence_id}")
        history = entry.setdefault("history", [])
        if not isinstance(history, list):
            raise ValueError("invalid decision history")
        sequence = len(history) + 1
        next_revision = revision + 1
        event = {
            "event_id": hashlib.sha256(
                f"{evidence_id}|{sequence}|{next_revision}|{decision}|{decision_status}|{owner}|{note}".encode("utf-8")
            ).hexdigest()[:20],
            "sequence": sequence,
            "ledger_revision": next_revision,
            "recorded_at": _utc_timestamp(),
            "actor": actor,
            "source_commit": source_commit,
            "decision": decision,
            "decision_status": decision_status,
            "owner": owner or None,
            "note": note,
        }
        history.append(event)
        entry["decision"] = decision
        entry["decision_status"] = decision_status
        entry["owner"] = owner or None
        entry["note"] = note
        data["revision"] = next_revision
        _write(root, path, data, overwrite=True)
    return {"evidence_id": evidence_id, **event}


def status(root: Path, ledger: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    data = _load_ledger(_inside(root, ledger, "ledger path"))
    entries = data["entries"]
    assert isinstance(entries, dict)
    pending: list[dict[str, Any]] = []
    unreviewed: list[dict[str, Any]] = []
    for evidence_id, entry in sorted(entries.items()):
        if not isinstance(entry, dict):
            continue
        evidence = entry.get("evidence")
        if not isinstance(evidence, dict):
            continue
        decision = str(entry.get("decision", "unreviewed"))
        decision_status = str(entry.get("decision_status", "open"))
        item = {
            "evidence_id": evidence_id,
            "kind": evidence.get("kind"),
            "source_id": evidence.get("source_id"),
            "title": evidence.get("title"),
            "evidence_status": evidence.get("status"),
            "decision": decision,
            "decision_status": decision_status,
            "owner": entry.get("owner"),
            "missing_from_latest_bundle": bool(entry.get("missing_from_latest_bundle")),
        }
        if decision in {"fix", "escalate"} and decision_status == "open":
            pending.append(item)
        elif decision == "unreviewed" and str(evidence.get("status")) not in TERMINAL_EVIDENCE_STATUSES:
            unreviewed.append(item)
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": data.get("revision"),
        "ledger_entries": len(entries),
        "pending": pending,
        "unreviewed": unreviewed,
        "ready_for_next_review": not pending and not unreviewed,
        "bundle_sha256": data.get("bundle_sha256"),
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Maintainer run-state ledger",
        "",
        f"- Revision: {report['revision']}",
        f"- Entries: {report['ledger_entries']}",
        f"- Pending fix/escalation decisions: {len(report['pending'])}",
        f"- Unreviewed non-terminal evidence: {len(report['unreviewed'])}",
        f"- Ready for next review: `{str(report['ready_for_next_review']).lower()}`",
        "",
    ]
    for heading, items in (("Pending decisions", report["pending"]), ("Unreviewed evidence", report["unreviewed"])):
        lines.extend([f"## {heading}", ""])
        if not items:
            lines.append("None.")
        else:
            for item in items:
                owner = f" — owner: {item['owner']}" if item.get("owner") else ""
                lines.append(f"- `{item['source_id']}` {item['title']} ({item['decision']}/{item['decision_status']}){owner}")
        lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("init", "sync", "decide", "status"):
        item = sub.add_parser(name)
        item.add_argument("--repo", type=Path, required=True)
        item.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
        if name == "sync":
            item.add_argument("--bundle", type=Path, required=True)
            item.add_argument("--expected-revision", type=int)
        if name == "decide":
            item.add_argument("--evidence-id", required=True)
            item.add_argument("--decision", choices=DECISIONS[1:], required=True)
            item.add_argument("--status", dest="decision_status", choices=DECISION_STATUSES, default="open")
            item.add_argument("--owner", default="")
            item.add_argument("--note", default="")
            item.add_argument("--actor", default="local-maintainer")
            item.add_argument("--source-commit", default="")
            item.add_argument("--expected-revision", type=int)
        if name == "status":
            item.add_argument("--format", choices=("json", "markdown"), default="markdown")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "init":
            result = initialize(args.repo, args.ledger)
        elif args.action == "sync":
            result = sync(args.repo, args.ledger, args.bundle, expected_revision=args.expected_revision)
        elif args.action == "decide":
            result = decide(
                args.repo,
                args.ledger,
                args.evidence_id,
                args.decision,
                args.decision_status,
                args.owner,
                args.note,
                actor=args.actor,
                source_commit=args.source_commit,
                expected_revision=args.expected_revision,
            )
        else:
            result = status(args.repo, args.ledger)
    except (OSError, ValueError) as exc:
        print(f"Run-state ledger failed: {exc}", file=sys.stderr)
        return 2
    if args.action == "status" and args.format == "markdown":
        print(_markdown(result), end="")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
