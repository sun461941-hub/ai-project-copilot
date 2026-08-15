#!/usr/bin/env python3
"""Apply and verify the fix5 gateway hardening patch.

This tool is intentionally exact and fail-closed. It modifies only source
blocks known to exist in the current fix3.0/main baseline. If any block has
drifted, it refuses to write rather than guessing.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

TARGET = Path("skills/ai-project-copilot/scripts/model_budget_gateway.py")
CI_PATH = Path(".github/workflows/ci.yml")
RELEASE_PATH = Path(".github/workflows/release.yml")
CHANGELOG_PATH = Path("CHANGELOG.md")
TEST_PATH = Path("tests/test_fix5_gateway_hardening.py")
BOOTSTRAP_WORKFLOW = Path(".github/workflows/apply-fix5-mobile.yml")

FIX4_MARKER = "FIX4_PROVIDER_JSON_HARDENING = True"
FIX5_MARKER = "FIX5_GATEWAY_JSON_HARDENING = True"

TEST_CONTENT = r"""from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "ai-project-copilot" / "scripts"
GATEWAY = SCRIPTS / "model_budget_gateway.py"


def load_gateway():
    original = list(sys.path)
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("fix5_real_gateway", GATEWAY)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = original


class FakeResponse(io.BytesIO):
    headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False


class GatewayJSONHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_gateway()

    def test_real_gateway_contains_complete_fix(self):
        self.assertTrue(self.mod.FIX4_PROVIDER_JSON_HARDENING)
        self.assertTrue(self.mod.FIX5_GATEWAY_JSON_HARDENING)
        self.assertEqual(self.mod.MAX_JSON_NESTING, 256)

    def test_depth_scanner_ignores_brackets_inside_strings(self):
        text = json.dumps({"value": "[" * 2000 + "{" * 2000})
        self.assertFalse(self.mod._json_nesting_exceeds(text))

    def test_provider_loader_rejects_deep_json_as_provider_error(self):
        payload = ("[" * 300 + "0" + "]" * 300).encode("utf-8")
        with self.assertRaisesRegex(self.mod.ProviderError, "nesting"):
            self.mod._load_provider_json(
                payload,
                "test provider payload",
                request_may_have_started=True,
            )

    def test_http_error_body_fails_closed_without_traceback(self):
        payload = ("[" * 300 + "0" + "]" * 300).encode("utf-8")
        self.assertEqual(
            self.mod._safe_provider_error_payload(payload),
            "provider returned a non-JSON or unsafe error body",
        )

    def test_sse_deep_event_is_controlled_provider_error(self):
        body = ("[" * 300 + "0" + "]" * 300).encode("utf-8")
        stream = FakeResponse(b"data: " + body + b"\n\n")
        with self.assertRaisesRegex(self.mod.ProviderError, "nesting"):
            list(self.mod.iter_sse_events(stream))

    def test_token_count_deep_json_is_controlled_provider_error(self):
        body = ("[" * 300 + "0" + "]" * 300).encode("utf-8")

        def opener(request, timeout):
            return FakeResponse(body)

        client = self.mod.OpenAIResponsesClient(
            "test-key",
            opener=opener,
            clock=lambda: 1.0,
        )
        with self.assertRaisesRegex(self.mod.ProviderError, "nesting"):
            client.count_input_tokens({"model": "test-model", "input": "hello"})

    def test_local_request_deep_json_is_rejected_before_parser(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "request.json"
            path.write_text("[" * 300 + "0" + "]" * 300, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "nesting"):
                self.mod._read_json(path, "request")

    def test_normal_provider_json_still_parses(self):
        value = self.mod._load_provider_json(
            b'{"input_tokens": 7}',
            "token count response",
            request_may_have_started=False,
        )
        self.assertEqual(value, {"input_tokens": 7})


if __name__ == "__main__":
    unittest.main()
"""

def _gateway_replacements() -> tuple[tuple[str, str, str], ...]:
    constants_old = """MAX_SSE_EVENT_BYTES = 10 * 1024 * 1024
MAX_QUALITY_CAPTURE_CHARS = 8_000
MAX_QUALITY_CAPTURE_BYTES = MAX_QUALITY_CAPTURE_CHARS * 4
COUNT_PAYLOAD_FIELDS = frozenset(
"""
    constants_new = """MAX_SSE_EVENT_BYTES = 10 * 1024 * 1024
MAX_QUALITY_CAPTURE_CHARS = 8_000
MAX_QUALITY_CAPTURE_BYTES = MAX_QUALITY_CAPTURE_CHARS * 4
FIX4_PROVIDER_JSON_HARDENING = True
FIX5_GATEWAY_JSON_HARDENING = True
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

    canonical_old = """    except (TypeError, ValueError) as exc:
        raise ValueError(f"request must be canonical JSON: {exc}") from exc
"""
    canonical_new = """    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError(f"request must be canonical JSON: {exc}") from exc
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
        (constants_old, constants_new, "constants and depth scanner"),
        (canonical_old, canonical_new, "canonical JSON recursion handling"),
        (read_old, read_new, "provider JSON loader"),
        (error_old, error_new, "HTTP error JSON"),
        (sse_old, sse_new, "SSE event JSON"),
        (count_old, count_new, "token-count JSON"),
        (quality_old, quality_new, "quality-policy JSON"),
        (read_json_old, read_json_new, "local request JSON"),
    )


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if path.is_symlink():
            raise ValueError(f"refusing to replace symlink: {path}")
        os.replace(temp_path, path)
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


def _safe_path(repo: Path, relative: Path, *, require_file: bool = True) -> Path:
    if relative.is_absolute():
        raise ValueError(f"path must be repository-relative: {relative}")
    candidate = Path(os.path.abspath(os.fspath(repo / relative)))
    try:
        rel = candidate.relative_to(repo)
    except ValueError as exc:
        raise ValueError(f"path escapes repository: {relative}") from exc
    current = repo
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"path contains symlink component: {current}")
    if require_file and not candidate.is_file():
        raise ValueError(f"required file does not exist: {relative}")
    return candidate


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise ValueError(
            f"cannot patch {label}: expected one exact source block, found {count}"
        )
    return text.replace(old, new, 1)


def _gateway_complete(text: str) -> bool:
    required = (
        FIX4_MARKER,
        FIX5_MARKER,
        "def _load_provider_json(",
        '"provider error body"',
        '"provider SSE event"',
        '"token count response"',
        "_json_nesting_exceeds(text)",
        "except (TypeError, ValueError, RecursionError)",
    )
    return all(item in text for item in required)


def patch_gateway(text: str) -> tuple[str, bool]:
    if FIX5_MARKER in text:
        if not _gateway_complete(text):
            raise ValueError("fix5 marker exists but gateway hardening is incomplete")
        return text, False
    updated = text
    for old, new, label in _gateway_replacements():
        updated = _replace_once(updated, old, new, label)
    if not _gateway_complete(updated):
        raise ValueError("gateway hardening postcondition failed")
    return updated, True


def patch_ci(text: str) -> tuple[str, bool]:
    marker = "Verify gateway JSON hardening"
    if marker in text:
        return text, False
    old = """      - name: Run deterministic Skill evals
        run: >-
          python skills/ai-project-copilot/scripts/run_skill_evals.py
          --format json

      - name: Run unit tests
"""
    new = """      - name: Run deterministic Skill evals
        run: >-
          python skills/ai-project-copilot/scripts/run_skill_evals.py
          --format json

      - name: Verify gateway JSON hardening
        run: python tools/apply_fix5_mobile_release.py --repo . --check

      - name: Run unit tests
"""
    return _replace_once(text, old, new, "CI gateway verification"), True


def patch_release(text: str) -> tuple[str, bool]:
    old = 'description: "Existing signed/reviewed tag to publish (for example v3.1.0)"'
    new = 'description: "Existing reviewed tag to publish (for example v3.1.0)"'
    if new in text:
        return text, False
    return _replace_once(text, old, new, "release tag description"), True


def patch_changelog(text: str) -> tuple[str, bool]:
    marker = "**Gateway JSON boundary hardening**"
    if marker in text:
        return text, False
    old = "## [Unreleased]\n"
    new = """## [Unreleased]

### Fixed
- **Gateway JSON boundary hardening** now rejects excessive nesting before parsing provider HTTP error bodies, SSE events, token-count responses, request files, served-model maps, and quality-policy files;
- direct regression tests load the real `model_budget_gateway.py` implementation instead of only testing a source patcher;
- CI explicitly verifies that the gateway hardening remains applied;
- release workflow wording now says `reviewed tag`, matching the checks it actually performs instead of implying cryptographic signature verification.

"""
    return _replace_once(text, old, new, "changelog Unreleased section"), True


def apply(repo: Path, *, check: bool) -> list[str]:
    repo = repo.expanduser().resolve()
    if not repo.is_dir():
        raise ValueError(f"repository directory does not exist: {repo}")

    gateway_path = _safe_path(repo, TARGET)
    ci_path = _safe_path(repo, CI_PATH)
    release_path = _safe_path(repo, RELEASE_PATH)
    changelog_path = _safe_path(repo, CHANGELOG_PATH)

    gateway_text = gateway_path.read_text(encoding="utf-8")
    ci_text = ci_path.read_text(encoding="utf-8")
    release_text = release_path.read_text(encoding="utf-8")
    changelog_text = changelog_path.read_text(encoding="utf-8")

    gateway_new, gateway_changed = patch_gateway(gateway_text)
    ci_new, ci_changed = patch_ci(ci_text)
    release_new, release_changed = patch_release(release_text)
    changelog_new, changelog_changed = patch_changelog(changelog_text)

    test_path = _safe_path(repo, TEST_PATH, require_file=False)
    test_changed = (
        not test_path.exists()
        or test_path.read_text(encoding="utf-8") != TEST_CONTENT
    )

    changes = []
    for changed, label in (
        (gateway_changed, TARGET.as_posix()),
        (ci_changed, CI_PATH.as_posix()),
        (release_changed, RELEASE_PATH.as_posix()),
        (changelog_changed, CHANGELOG_PATH.as_posix()),
        (test_changed, TEST_PATH.as_posix()),
    ):
        if changed:
            changes.append(label)

    if check:
        if changes:
            raise ValueError(
                "fix5 is incomplete; files still requiring changes: " + ", ".join(changes)
            )
        if not _gateway_complete(gateway_text):
            raise ValueError("gateway hardening verification failed")
        return []

    if gateway_changed:
        _atomic_write(gateway_path, gateway_new)
    if ci_changed:
        _atomic_write(ci_path, ci_new)
    if release_changed:
        _atomic_write(release_path, release_new)
    if changelog_changed:
        _atomic_write(changelog_path, changelog_new)
    if test_changed:
        _atomic_write(test_path, TEST_CONTENT)

    # The bootstrap workflow is intentionally one-use. The output branch deletes
    # it, so merging the pull request removes its temporary write-capable workflow.
    bootstrap = _safe_path(repo, BOOTSTRAP_WORKFLOW, require_file=False)
    if bootstrap.exists():
        bootstrap.unlink()
        changes.append(BOOTSTRAP_WORKFLOW.as_posix() + " (deleted after use)")

    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        changes = apply(args.repo, check=args.check)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    if args.check:
        print("fix5 gateway hardening: applied and complete")
    elif changes:
        print("fix5 applied:")
        for item in changes:
            print(f"- {item}")
    else:
        print("fix5 already applied; no changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
