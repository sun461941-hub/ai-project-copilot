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
MAX_CACHE_BYTES = 5 * 1024 * 1024
MAX_INPUT_BYTES = 512 * 1024 * 1024
MAX_JSON_NESTING = 256


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


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


def _lexical_inside(root: Path, raw: Path, label: str) -> Path:
    """Validate containment and every existing path component before resolving."""
    root = root.expanduser().resolve()
    candidate = raw.expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    absolute = Path(os.path.abspath(os.fspath(candidate)))
    try:
        relative = absolute.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must remain inside the repository") from exc

    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"symlink path components are not {label}-safe: {current}")

    resolved = absolute.resolve(strict=False)
    if not _within(resolved, root):
        raise ValueError(f"{label} escapes repository")
    return resolved


def _resolve_inside(root: Path, raw: str) -> Path:
    return _lexical_inside(root, Path(raw), "cache input")


def _sha256_file(path: Path) -> str:
    size = path.stat().st_size
    if size > MAX_INPUT_BYTES:
        raise ValueError(f"cache input exceeds {MAX_INPUT_BYTES} bytes: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(
    root: Path,
    command: str,
    inputs: list[str],
) -> tuple[str, list[dict[str, object]]]:
    root = root.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"repository directory does not exist: {root}")
    records: list[dict[str, object]] = []
    for raw in sorted(dict.fromkeys(inputs)):
        path = _resolve_inside(root, raw)
        rel = path.relative_to(root).as_posix()
        if not path.exists():
            records.append(
                {"path": rel, "exists": False, "sha256": None, "size": None}
            )
        elif path.is_symlink() or not path.is_file():
            raise ValueError(f"cache input must be a regular non-symlink file: {raw}")
        else:
            size = path.stat().st_size
            records.append(
                {
                    "path": rel,
                    "exists": True,
                    "sha256": _sha256_file(path),
                    "size": size,
                }
            )
    payload = {"command": command, "inputs": records}
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), records


def _cache_path(root: Path, raw: Path) -> Path:
    return _lexical_inside(root, raw, "cache path")


def _default_cache() -> dict[str, object]:
    return {"version": 1, "entries": {}}


def _load(path: Path) -> dict[str, object]:
    if path.is_symlink():
        raise ValueError("refusing to read a symlinked evidence cache")
    if not path.exists():
        return _default_cache()
    if not path.is_file():
        raise ValueError("evidence cache must be a regular file")
    try:
        size = path.stat().st_size
        if size > MAX_CACHE_BYTES:
            raise ValueError(f"evidence cache exceeds {MAX_CACHE_BYTES} bytes")
        with path.open("rb") as handle:
            raw = handle.read(MAX_CACHE_BYTES + 1)
        if len(raw) > MAX_CACHE_BYTES:
            raise ValueError(f"evidence cache exceeds {MAX_CACHE_BYTES} bytes")
        text = raw.decode("utf-8")
        if _json_nesting_exceeds(text):
            raise ValueError(
                f"evidence cache nesting exceeds the safe limit of {MAX_JSON_NESTING}"
            )
        data = json.loads(text)
    except ValueError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"invalid evidence cache: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("invalid evidence cache structure")
    version = data.get("version", 1)
    entries = data.get("entries", {})
    if version != 1 or not isinstance(entries, dict):
        raise ValueError("invalid evidence cache structure")
    data["version"] = 1
    data["entries"] = entries
    return data


def _atomic_write(root: Path, path: Path, data: dict[str, object]) -> None:
    root = root.expanduser().resolve()
    path = _cache_path(root, path)
    encoded = (
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_CACHE_BYTES:
        raise ValueError(f"evidence cache exceeds {MAX_CACHE_BYTES} bytes")

    path.parent.mkdir(parents=True, exist_ok=True)
    path = _cache_path(root, path)
    fd, temp_name = tempfile.mkstemp(
        prefix=".evidence-",
        suffix=".json",
        dir=str(path.parent),
    )
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
        path = _cache_path(root, path)
        os.replace(temp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            temp_path.unlink()
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


def check_entry(
    root: Path,
    cache: Path,
    entry: str,
    command: str,
    inputs: list[str],
    critical: bool = False,
) -> CacheCheck:
    fp, records = fingerprint(root, command, inputs)
    if critical:
        return CacheCheck(
            entry,
            fp,
            False,
            False,
            "critical/final gates must be rerun",
            None,
            records,
        )
    data = _load(_cache_path(root, cache))
    item = data.get("entries", {}).get(entry) if isinstance(
        data.get("entries"), dict
    ) else None
    if not isinstance(item, dict):
        return CacheCheck(
            entry,
            fp,
            False,
            False,
            "no cached evidence for this entry",
            None,
            records,
        )
    if item.get("fingerprint") != fp:
        return CacheCheck(
            entry,
            fp,
            False,
            False,
            "command or input fingerprint changed",
            None,
            records,
        )
    if item.get("status") != "pass":
        return CacheCheck(
            entry,
            fp,
            False,
            False,
            "only passing evidence is reusable",
            str(item.get("summary", "")) or None,
            records,
        )
    return CacheCheck(
        entry,
        fp,
        True,
        True,
        "exact fingerprint match",
        str(item.get("summary", "")) or None,
        records,
    )


def record_entry(
    root: Path,
    cache: Path,
    entry: str,
    command: str,
    inputs: list[str],
    status: str,
    summary: str,
) -> dict[str, object]:
    root = root.expanduser().resolve()
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
    _atomic_write(root, path, data)
    return {
        "entry": entry,
        "fingerprint": fp,
        "status": status,
        "cache": path.relative_to(root).as_posix(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("check", "record"):
        item = sub.add_parser(name)
        item.add_argument("--repo", type=Path, required=True)
        item.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
        item.add_argument("--entry", required=True)
        item.add_argument("--command", required=True)
        item.add_argument("--input", action="append", default=[])
        if name == "check":
            item.add_argument("--critical", action="store_true")
        else:
            item.add_argument("--status", choices=("pass", "fail"), required=True)
            item.add_argument("--summary", default="")
    args = parser.parse_args()
    try:
        if args.action == "check":
            result = check_entry(
                args.repo,
                args.cache,
                args.entry,
                args.command,
                args.input,
                args.critical,
            )
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        else:
            result = record_entry(
                args.repo,
                args.cache,
                args.entry,
                args.command,
                args.input,
                args.status,
                args.summary,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
