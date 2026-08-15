#!/usr/bin/env python3
"""Apply fix4 provider/local JSON hardening to model_budget_gateway.py safely.

This is an idempotent, fail-closed source patcher. It intentionally modifies the
large gateway file in place instead of shipping a stale full-file replacement
that could overwrite fix3 changes.
"""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from pathlib import Path

MARKER = "FIX4_PROVIDER_JSON_HARDENING = True"
TARGET = Path("skills/ai-project-copilot/scripts/model_budget_gateway.py")


def _replacements() -> tuple[tuple[str, str, str], ...]:
    constants_old = """MAX_SSE_EVENT_BYTES = 10 * 1024 * 1024
MAX_QUALITY_CAPTURE_CHARS = 8_000
MAX_QUALITY_CAPTURE_BYTES = MAX_QUALITY_CAPTURE_CHARS * 4
COUNT_PAYLOAD_FIELDS = frozenset(
"""
    constants_new = """MAX_SSE_EVENT_BYTES = 10 * 1024 * 1024
MAX_QUALITY_CAPTURE_CHARS = 8_000
MAX_QUALITY_CAPTURE_BYTES = MAX_QUALITY_CAPTURE_CHARS * 4
FIX4_PROVIDER_JSON_HARDENING = True
MAX_JSON_NESTING = 256


def _json_nesting_exceeds(text: str, maximum: int = MAX_JSON_NESTING) -> bool:
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\\\":
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


COUNT_PAYLOAD_FIELDS = frozenset(
"""

    read_old = """def _read_limited(response: BinaryIO, maximum: int) -> bytes:
    data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ProviderError(
            f"provider response exceeded {maximum} bytes",
            request_may_have_started=True,
        )
    return data


def _safe_provider_error_payload(data: bytes) -> str:
"""
    read_new = """def _read_limited(response: BinaryIO, maximum: int) -> bytes:
    data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ProviderError(
            f"provider response exceeded {maximum} bytes",
            request_may_have_started=True,
        )
    return data


def _load_provider_json(
    data: bytes,
    label: str,
    *,
    request_may_have_started: bool,
) -> object:
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise ProviderError(
            f"{label} was not valid UTF-8",
            request_may_have_started=request_may_have_started,
        ) from exc
    if _json_nesting_exceeds(text):
        raise ProviderError(
            f"{label} nesting exceeds the safe limit of {MAX_JSON_NESTING}",
            request_may_have_started=request_may_have_started,
        )
    try:
        return json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ProviderError(
            f"{label} was invalid JSON: {exc}",
            request_may_have_started=request_may_have_started,
        ) from exc


def _safe_provider_error_payload(data: bytes) -> str:
"""

    error_old = """    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return "provider returned a non-JSON error body"
"""
    error_new = """    try:
        payload = _load_provider_json(
            data,
            "provider error body",
            request_may_have_started=True,
        )
    except ProviderError:
        return "provider returned a non-JSON or unsafe error body"
"""

    sse_old = """            try:
                event = json.loads(body.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ProviderError(
                    f"provider returned malformed SSE JSON: {exc}",
                    request_may_have_started=True,
                ) from exc
"""
    sse_new = """            event = _load_provider_json(
                body,
                "provider SSE event",
                request_may_have_started=True,
            )
"""

    count_old = """        try:
            result = json.loads(data.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ProviderError(
                f"token count response was invalid JSON: {exc}",
                request_may_have_started=False,
            ) from exc
"""
    count_new = """        result = _load_provider_json(
            data,
            "token count response",
            request_may_have_started=False,
        )
"""

    quality_old = """    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"quality policy is invalid JSON: {exc}") from exc
"""
    quality_new = """    try:
        text = data.decode("utf-8")
        if _json_nesting_exceeds(text):
            raise ValueError(
                f"quality policy nesting exceeds the safe limit of {MAX_JSON_NESTING}"
            )
        payload = json.loads(text)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"quality policy is invalid JSON: {exc}") from exc
"""

    read_json_old = """    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"{label} is invalid JSON: {exc}") from exc
"""
    read_json_new = """    try:
        text = data.decode("utf-8")
        if _json_nesting_exceeds(text):
            raise ValueError(
                f"{label} nesting exceeds the safe limit of {MAX_JSON_NESTING}"
            )
        payload = json.loads(text)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"{label} is invalid JSON: {exc}") from exc
"""

    return (
        (constants_old, constants_new, "constants/helper"),
        (read_old, read_new, "provider JSON loader"),
        (error_old, error_new, "HTTP error body"),
        (sse_old, sse_new, "SSE event"),
        (count_old, count_new, "token count response"),
        (quality_old, quality_new, "quality policy"),
        (read_json_old, read_json_new, "local request JSON"),
    )


def patch_text(text: str) -> tuple[str, bool]:
    if MARKER in text:
        required = (
            "def _load_provider_json(",
            '"provider SSE event"',
            '"token count response"',
            "_json_nesting_exceeds(text)",
        )
        missing = [item for item in required if item not in text]
        if missing:
            raise ValueError(
                "gateway contains the fix4 marker but is incomplete: "
                + ", ".join(missing)
            )
        return text, False

    updated = text
    for old, new, label in _replacements():
        count = updated.count(old)
        if count != 1:
            raise ValueError(
                f"cannot apply {label}: expected one exact source block, found {count}; "
                "the gateway source has drifted and must be reviewed manually"
            )
        updated = updated.replace(old, new, 1)
    return updated, True


def _safe_target(repo: Path, raw: Path) -> Path:
    candidate = raw.expanduser()
    if not candidate.is_absolute():
        candidate = repo / candidate
    absolute = Path(os.path.abspath(os.fspath(candidate)))
    try:
        relative = absolute.relative_to(repo)
    except ValueError as exc:
        raise ValueError("gateway target must remain inside repository") from exc
    current = repo
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"gateway target contains symlink component: {current}")
    resolved = absolute.resolve(strict=False)
    try:
        resolved.relative_to(repo)
    except ValueError as exc:
        raise ValueError("gateway target must remain inside repository") from exc
    return resolved


def patch_file(path: Path, *, backup: bool = False, check: bool = False) -> bool:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"gateway target must be a regular non-symlink file: {path}")
    raw = path.read_bytes()
    if len(raw) > 20 * 1024 * 1024:
        raise ValueError("gateway target unexpectedly exceeds 20 MB")
    text = raw.decode("utf-8")
    updated, changed = patch_text(text)
    if check:
        if changed:
            raise ValueError("fix4 gateway hardening is not applied")
        return False
    if not changed:
        return False

    if backup:
        backup_path = path.with_suffix(path.suffix + ".fix4.bak")
        if backup_path.exists() or backup_path.is_symlink():
            raise ValueError(f"refusing to overwrite backup: {backup_path}")
        shutil.copyfile(path, backup_path)

    encoded = updated.encode("utf-8")
    fd, temp_name = tempfile.mkstemp(
        prefix=".model-budget-gateway-fix4-",
        suffix=".py",
        dir=str(path.parent),
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if path.is_symlink():
            raise ValueError("gateway target became a symlink during patching")
        os.replace(temp_path, path)
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--target", type=Path, default=TARGET)
    parser.add_argument("--backup", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    if not repo.is_dir():
        parser.error(f"repository directory does not exist: {repo}")
    try:
        target = _safe_target(repo, args.target)
        changed = patch_file(target, backup=args.backup, check=args.check)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    if args.check:
        print("fix4 gateway hardening: applied and complete")
    elif changed:
        print(f"patched {target.relative_to(repo).as_posix()}")
    else:
        print("fix4 gateway hardening already applied; no changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
