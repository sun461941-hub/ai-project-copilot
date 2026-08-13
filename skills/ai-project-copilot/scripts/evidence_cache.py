#!/usr/bin/env python3
"""Store/check exact-fingerprint evidence without executing commands.

The cache is intentionally conservative: only passing non-critical evidence can be
reused, and callers must rerun critical security/release/final gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_CACHE = Path(".aipc/cache/evidence.json")


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_inside(root: Path, raw: str) -> Path:
    original = Path(raw)
    unresolved = original if original.is_absolute() else root / original
    try:
        relative = unresolved.absolute().relative_to(root.absolute())
    except ValueError:
        relative = None
    if relative is not None:
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(f"symlink inputs are not cache-safe: {raw}")
    candidate = unresolved.resolve(strict=False)
    if not _within(candidate, root):
        raise ValueError(f"input escapes repository: {raw}")
    return candidate


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(root: Path, command: str, inputs: list[str]) -> tuple[str, list[dict[str, object]]]:
    root = root.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"repository directory does not exist: {root}")
    records: list[dict[str, object]] = []
    for raw in sorted(dict.fromkeys(inputs)):
        path = _resolve_inside(root, raw)
        rel = path.relative_to(root).as_posix()
        if not path.exists():
            records.append({"path": rel, "exists": False, "sha256": None, "size": None})
        elif not path.is_file():
            raise ValueError(f"cache input must be a regular file: {raw}")
        else:
            records.append({"path": rel, "exists": True, "sha256": _sha256_file(path), "size": path.stat().st_size})
    payload = {"command": command, "inputs": records}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), records


def _cache_path(root: Path, raw: Path) -> Path:
    root = root.expanduser().resolve()
    candidate = (root / raw).resolve(strict=False) if not raw.is_absolute() else raw.resolve(strict=False)
    if not _within(candidate, root):
        raise ValueError("cache path must remain inside the repository")
    if candidate.exists() and candidate.is_symlink():
        raise ValueError("refusing to write through a symlinked cache path")
    return candidate


def _load(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"version": 1, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid evidence cache: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("entries", {}), dict):
        raise ValueError("invalid evidence cache structure")
    data.setdefault("version", 1)
    data.setdefault("entries", {})
    return data


def _atomic_write(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".evidence-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        Path(temp_name).replace(path)
    except Exception:
        try:
            Path(temp_name).unlink()
        except OSError:
            pass
        raise


@dataclass(frozen=True)
class CacheCheck:
    entry: str
    fingerprint: str
    hit: bool
    reusable: bool
    reason: str
    cached_summary: str | None
    input_records: list[dict[str, object]]


def check_entry(root: Path, cache: Path, entry: str, command: str, inputs: list[str], critical: bool = False) -> CacheCheck:
    fp, records = fingerprint(root, command, inputs)
    if critical:
        return CacheCheck(entry, fp, False, False, "critical/final gates must be rerun", None, records)
    data = _load(_cache_path(root, cache))
    item = data.get("entries", {}).get(entry) if isinstance(data.get("entries"), dict) else None
    if not isinstance(item, dict):
        return CacheCheck(entry, fp, False, False, "no cached evidence for this entry", None, records)
    if item.get("fingerprint") != fp:
        return CacheCheck(entry, fp, False, False, "command or input fingerprint changed", None, records)
    if item.get("status") != "pass":
        return CacheCheck(entry, fp, False, False, "only passing evidence is reusable", str(item.get("summary", "")) or None, records)
    return CacheCheck(entry, fp, True, True, "exact fingerprint match", str(item.get("summary", "")) or None, records)


def record_entry(root: Path, cache: Path, entry: str, command: str, inputs: list[str], status: str, summary: str) -> dict[str, object]:
    if not entry.strip():
        raise ValueError("entry must not be blank")
    if status not in {"pass", "fail"}:
        raise ValueError("status must be pass or fail")
    fp, records = fingerprint(root, command, inputs)
    if any(not bool(item.get("exists")) for item in records):
        raise ValueError("all cache inputs must exist when recording evidence")
    path = _cache_path(root, cache)
    data = _load(path)
    entries = data.setdefault("entries", {})
    if not isinstance(entries, dict):
        raise ValueError("invalid evidence cache structure")
    entries[entry] = {
        "fingerprint": fp,
        "status": status,
        "summary": summary,
        "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
        "inputs": records,
    }
    _atomic_write(path, data)
    return {"entry": entry, "fingerprint": fp, "status": status, "cache": path.relative_to(root.resolve()).as_posix()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("check", "record"):
        p = sub.add_parser(name)
        p.add_argument("--repo", type=Path, required=True)
        p.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
        p.add_argument("--entry", required=True)
        p.add_argument("--command", required=True)
        p.add_argument("--input", action="append", default=[])
        if name == "check":
            p.add_argument("--critical", action="store_true")
        else:
            p.add_argument("--status", choices=("pass", "fail"), required=True)
            p.add_argument("--summary", default="")
    args = parser.parse_args()
    try:
        if args.action == "check":
            result = check_entry(args.repo, args.cache, args.entry, args.command, args.input, args.critical)
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        else:
            result = record_entry(args.repo, args.cache, args.entry, args.command, args.input, args.status, args.summary)
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
