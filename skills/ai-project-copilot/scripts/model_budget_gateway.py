#!/usr/bin/env python3
"""Execute an OpenAI Responses request through Model Budget Autopilot.

The gateway is the trusted integration layer that the local budget ledger does
not provide on its own.  It counts the exact provider input, obtains one-shot
execution authorization, streams the selected model, settles provider usage,
runs an optional deterministic quality command, and performs at most one
authorized quality upgrade.

Only the Python standard library is required.  The API key is read from the
environment and is never written to the ledger, trace, or command output.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import datetime as dt
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterable, Mapping, Protocol, Sequence

import model_budget_autopilot as budget


API_BASE_URL = "https://api.openai.com/v1"
MAX_REQUEST_BYTES = 10 * 1024 * 1024
MAX_RESPONSE_BYTES = 25 * 1024 * 1024
MAX_SSE_EVENT_BYTES = 10 * 1024 * 1024
MAX_QUALITY_CAPTURE_CHARS = 8_000
MAX_QUALITY_CAPTURE_BYTES = MAX_QUALITY_CAPTURE_CHARS * 4
COUNT_PAYLOAD_FIELDS = frozenset(
    {
        "input",
        "instructions",
        "model",
        "parallel_tool_calls",
        "personality",
        "previous_response_id",
        "reasoning",
        "text",
        "tool_choice",
        "tools",
        "truncation",
    }
)
REVIEWED_PROVIDER_FIELDS = COUNT_PAYLOAD_FIELDS | frozenset(
    {
        "background",
        "include",
        "max_output_tokens",
        "metadata",
        "prompt_cache_key",
        "prompt_cache_retention",
        "safety_identifier",
        "service_tier",
        "store",
        "stream",
        "temperature",
        "top_p",
        "user",
    }
)
TERMINAL_STREAM_EVENTS = frozenset(
    {"response.completed", "response.incomplete", "response.failed"}
)
DEFINITELY_UNSTARTED_HTTP_STATUSES = frozenset(
    {400, 401, 403, 404, 405, 413, 415, 422, 429}
)


class GatewayError(RuntimeError):
    """A safe, user-facing gateway failure."""


class ProviderError(GatewayError):
    """A provider call failed before a complete response could be settled."""

    def __init__(self, message: str, *, request_may_have_started: bool) -> None:
        super().__init__(message)
        self.request_may_have_started = request_may_have_started


@dataclass(frozen=True)
class TokenCount:
    input_tokens: int
    latency_ms: float


@dataclass(frozen=True)
class ProviderCall:
    response: dict[str, Any]
    output_text: str
    served_model: str
    ttft_ms: float | None
    latency_ms: float
    text_delta_count: int
    display_error: str | None
    provider_http_request_id: str | None
    openai_processing_ms: float | None


@dataclass(frozen=True)
class QualityPolicy:
    argv: tuple[str, ...]
    timeout_seconds: int = 120
    working_directory: Path | None = None


@dataclass(frozen=True)
class QualityEvidence:
    gate: str
    reason: str
    policy: str
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    duration_ms: float


@dataclass(frozen=True)
class AttemptTrace:
    attempt_number: int
    route: dict[str, Any]
    input_tokens_by_model: dict[str, int]
    projected_input_tokens: int
    count_latency_ms: float
    request_template_sha256: str
    provider_payload_sha256: str
    provider_response_id: str
    provider_http_request_id: str | None
    selected_model: str
    served_model: str
    response_status: str
    output_text: str
    display_error: str | None
    lease_renewal_error: str | None
    ttft_ms: float | None
    openai_processing_ms: float | None
    provider_latency_ms: float
    attempt_e2e_ms: float
    usage: dict[str, Any]
    quality_evidence: dict[str, Any]
    quality_decision: dict[str, Any]


@dataclass(frozen=True)
class GatewayResult:
    schema_version: str
    provider: str
    logical_request_hash: str
    final_status: str
    final_model: str | None
    served_model: str | None
    output_text: str
    attempts: list[dict[str, Any]]
    total_input_tokens: int
    total_cached_tokens: int
    total_cache_write_tokens: int
    total_output_tokens: int
    total_reasoning_tokens: int
    total_tokens: int
    total_cost_nano_usd: int
    total_cost_usd: str
    estimated_cost_savings_nano_usd: int
    token_savings: None
    count_latency_ms: float
    ttft_ms: float | None
    provider_latency_ms: float
    e2e_ms: float
    quality_policy: str
    request_template_sha256: str
    quality_policy_sha256: str
    pricing_policy_sha256: str


class ResponsesClient(Protocol):
    def count_input_tokens(self, payload: Mapping[str, Any]) -> TokenCount:
        ...

    def create_stream(
        self,
        payload: Mapping[str, Any],
        *,
        on_text_delta: Callable[[str], None] | None = None,
    ) -> ProviderCall:
        ...


class _LeaseRenewer:
    """Keep one active reservation alive while a provider stream is running."""

    def __init__(self, db: Path, user_key: str, request_id: str, ttl_seconds: int) -> None:
        self.db = db
        self.user_key = user_key
        self.request_id = request_id
        self.ttl_seconds = ttl_seconds
        self.error: str | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        interval = max(0.25, min(self.ttl_seconds / 3.0, 300.0))
        while not self._stop.wait(interval):
            try:
                budget.renew_reservation(
                    self.db,
                    self.user_key,
                    self.request_id,
                    self.ttl_seconds,
                )
            except Exception as exc:
                self.error = type(exc).__name__
                return

    def __enter__(self) -> "_LeaseRenewer":
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5.0)


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"request must be canonical JSON: {exc}") from exc


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _positive_int(value: object, label: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} cannot exceed {maximum}")
    return value


def _nonnegative_int(value: object, label: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} cannot exceed {maximum}")
    return value


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    result = value.strip()
    if len(result) > 256:
        raise ValueError(f"{label} must be at most 256 characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in result):
        raise ValueError(f"{label} cannot contain control characters")
    return result


def _validate_text_only_input(value: object) -> None:
    """Reject every Responses input shape that can carry a non-text modality."""
    if isinstance(value, str):
        return
    if not isinstance(value, list):
        raise ValueError("input must be text or an array of text-only messages")
    for item_index, item in enumerate(value):
        label = f"input[{item_index}]"
        if not isinstance(item, Mapping):
            raise ValueError(f"{label} must be a text-only message object")
        item_type = item.get("type", "message")
        if item_type != "message":
            raise ValueError(f"{label} type {item_type!r} is not supported by the text-only gateway")
        extra_item_keys = sorted(set(item) - {"type", "role", "content"})
        if extra_item_keys:
            raise ValueError(
                f"{label} contains fields outside the text-only message contract: "
                + ", ".join(extra_item_keys)
            )
        role = item.get("role")
        if role not in {"developer", "system", "user", "assistant"}:
            raise ValueError(
                f"{label}.role must be developer, system, user, or assistant"
            )
        content = item.get("content")
        if isinstance(content, str):
            continue
        if not isinstance(content, list):
            raise ValueError(f"{label}.content must be text or an array of text parts")
        for part_index, part in enumerate(content):
            part_label = f"{label}.content[{part_index}]"
            if not isinstance(part, Mapping):
                raise ValueError(f"{part_label} must be a text part object")
            part_type = part.get("type")
            if part_type in {"input_text", "output_text"}:
                extra_part_keys = sorted(set(part) - {"type", "text"})
                if extra_part_keys:
                    raise ValueError(
                        f"{part_label} contains fields outside the text-only part contract: "
                        + ", ".join(extra_part_keys)
                    )
                if not isinstance(part.get("text"), str):
                    raise ValueError(f"{part_label}.text must be a string")
            elif part_type == "refusal":
                extra_part_keys = sorted(set(part) - {"type", "refusal"})
                if extra_part_keys:
                    raise ValueError(
                        f"{part_label} contains fields outside the text-only part contract: "
                        + ", ".join(extra_part_keys)
                    )
                if not isinstance(part.get("refusal"), str):
                    raise ValueError(f"{part_label}.refusal must be a string")
            else:
                raise ValueError(
                    f"{part_label} type {part_type!r} is not supported by the text-only gateway"
                )


def _request_template(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable request body before the router substitutes a model."""
    if not isinstance(payload, Mapping):
        raise ValueError("request payload must be a JSON object")
    request = copy.deepcopy(dict(payload))
    if any(not isinstance(key, str) for key in request):
        raise ValueError("request object keys must be strings")
    unsupported = sorted(set(request) - REVIEWED_PROVIDER_FIELDS - {"conversation", "prompt"})
    if unsupported:
        raise ValueError(
            "request contains fields outside the reviewed v2.1 text gateway contract: "
            + ", ".join(unsupported)
        )
    requested_model = _identifier(request.get("model"), "request model")
    request["model"] = requested_model
    if "prompt" in request:
        raise ValueError(
            "prompt templates are not supported because the input-token endpoint "
            "cannot count them as an exact request"
        )
    if "conversation" in request:
        raise ValueError(
            "conversation is not supported because mutable server-side state cannot be "
            "counted and retried as one immutable request"
        )
    if "input" not in request:
        raise ValueError("request must include input")
    _validate_text_only_input(request["input"])
    if request.get("tools"):
        raise ValueError(
            "tools are not supported by this text-only gateway because tool loops "
            "and variable provider tool charges are not reconciled"
        )
    request["max_output_tokens"] = _positive_int(
        request.get("max_output_tokens"), "max_output_tokens", maximum=budget.SQLITE_MAX_INT
    )
    if request.get("background") is True:
        raise ValueError("background responses are not supported by the synchronous gateway")
    if "service_tier" in request and request["service_tier"] != "default":
        raise ValueError(
            "service_tier must be 'default' so price-card settlement cannot omit a tier premium"
        )
    request["service_tier"] = "default"
    request.pop("stream", None)
    request.setdefault("store", False)
    encoded = _canonical_json_bytes(request)
    if len(encoded) > MAX_REQUEST_BYTES:
        raise ValueError(f"request JSON exceeds {MAX_REQUEST_BYTES} bytes")
    return request


def build_count_payload(payload: Mapping[str, Any], model: str) -> dict[str, Any]:
    """Preserve every input-rendering field supported by the count endpoint."""
    result = {
        key: copy.deepcopy(value)
        for key, value in payload.items()
        if key in COUNT_PAYLOAD_FIELDS
    }
    result["model"] = _identifier(model, "count model")
    if "input" not in result:
        raise ValueError("count payload must include input")
    return result


def build_provider_payload(payload: Mapping[str, Any], model: str) -> dict[str, Any]:
    result = copy.deepcopy(dict(payload))
    result["model"] = _identifier(model, "selected model")
    result["stream"] = True
    return result


def _read_limited(response: BinaryIO, maximum: int) -> bytes:
    data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ProviderError(
            f"provider response exceeded {maximum} bytes",
            request_may_have_started=True,
        )
    return data


def _safe_provider_error_payload(data: bytes) -> str:
    if not data:
        return "provider returned no error body"
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return "provider returned a non-JSON error body"
    if isinstance(payload, Mapping):
        error = payload.get("error")
        if isinstance(error, Mapping) and isinstance(error.get("message"), str):
            return error["message"][:1_000]
    return "provider returned an unrecognized error body"


def iter_sse_events(stream: BinaryIO) -> Iterable[dict[str, Any]]:
    """Parse data-only SSE events with bounded memory and strict JSON objects."""
    data_lines: list[bytes] = []
    total = 0
    stream_total = 0
    while True:
        raw = stream.readline(MAX_SSE_EVENT_BYTES + 1)
        stream_total += len(raw)
        if stream_total > MAX_RESPONSE_BYTES:
            raise ProviderError(
                "provider event stream exceeded the total safety limit",
                request_may_have_started=True,
            )
        if len(raw) > MAX_SSE_EVENT_BYTES:
            raise ProviderError(
                "provider SSE line exceeded the safety limit",
                request_may_have_started=True,
            )
        if raw == b"":
            if data_lines:
                raw = b"\n"
            else:
                break
        if raw in (b"\n", b"\r\n"):
            if not data_lines:
                if stream.closed:
                    break
                continue
            body = b"\n".join(data_lines)
            data_lines = []
            total = 0
            if body.strip() == b"[DONE]":
                break
            try:
                event = json.loads(body.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ProviderError(
                    f"provider returned malformed SSE JSON: {exc}",
                    request_may_have_started=True,
                ) from exc
            if not isinstance(event, dict):
                raise ProviderError(
                    "provider SSE data must be a JSON object",
                    request_may_have_started=True,
                )
            yield event
            if raw == b"\n" and stream.closed:
                break
            continue
        line = raw.rstrip(b"\r\n")
        if line.startswith(b"data:"):
            value = line[5:]
            if value.startswith(b" "):
                value = value[1:]
            total += len(value)
            if total > MAX_SSE_EVENT_BYTES:
                raise ProviderError(
                    "provider SSE event exceeded the safety limit",
                    request_may_have_started=True,
                )
            data_lines.append(value)


def extract_output_text(response: Mapping[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str):
        return direct
    chunks: list[str] = []
    output = response.get("output", [])
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, Mapping):
            continue
        content = item.get("content", [])
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, Mapping):
                continue
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                chunks.append(str(part["text"]))
            elif part.get("type") == "refusal" and isinstance(part.get("refusal"), str):
                chunks.append(str(part["refusal"]))
    return "".join(chunks)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


class OpenAIResponsesClient:
    """Minimal standard-library client for count + streaming Responses calls."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: int = 600,
        api_base_url: str = API_BASE_URL,
        opener: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        project: str | None = None,
        organization: str | None = None,
    ) -> None:
        self.api_key = _identifier(api_key, "OPENAI_API_KEY")
        self.timeout_seconds = _positive_int(
            timeout_seconds, "timeout_seconds", maximum=3_600
        )
        if api_base_url != API_BASE_URL and opener is None:
            raise ValueError("custom API base URLs require an explicitly injected test transport")
        self.api_base_url = api_base_url.rstrip("/")
        self.opener = opener or urllib.request.build_opener(_NoRedirect()).open
        self.clock = clock
        self.project = _identifier(project, "OPENAI_PROJECT") if project else None
        self.organization = (
            _identifier(organization, "OPENAI_ORGANIZATION") if organization else None
        )

    def _request(self, path: str, payload: Mapping[str, Any]) -> urllib.request.Request:
        data = _canonical_json_bytes(payload)
        if len(data) > MAX_REQUEST_BYTES:
            raise ValueError(f"request JSON exceeds {MAX_REQUEST_BYTES} bytes")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "ai-project-copilot-model-budget-gateway/2.1",
            "X-Client-Request-Id": str(uuid.uuid4()),
        }
        if self.project is not None:
            headers["OpenAI-Project"] = self.project
        if self.organization is not None:
            headers["OpenAI-Organization"] = self.organization
        return urllib.request.Request(
            f"{self.api_base_url}/{path.lstrip('/')}",
            data=data,
            method="POST",
            headers=headers,
        )

    def _open(self, request: urllib.request.Request) -> Any:
        try:
            return self.opener(request, timeout=self.timeout_seconds)
        except urllib.error.HTTPError as exc:
            body = exc.read(64 * 1024)
            safe = _safe_provider_error_payload(body)
            raise ProviderError(
                f"provider HTTP {exc.code}: {safe}",
                request_may_have_started=(
                    exc.code not in DEFINITELY_UNSTARTED_HTTP_STATUSES
                ),
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(
                f"provider connection failed: {type(exc).__name__}",
                request_may_have_started=True,
            ) from exc

    def count_input_tokens(self, payload: Mapping[str, Any]) -> TokenCount:
        started = self.clock()
        request = self._request("responses/input_tokens", payload)
        with self._open(request) as response:
            data = _read_limited(response, MAX_RESPONSE_BYTES)
        latency = (self.clock() - started) * 1_000
        try:
            result = json.loads(data.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ProviderError(
                f"token count response was invalid JSON: {exc}",
                request_may_have_started=False,
            ) from exc
        if not isinstance(result, Mapping):
            raise ProviderError(
                "token count response must be an object",
                request_may_have_started=False,
            )
        value = result.get("input_tokens")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ProviderError(
                "token count response omitted a valid input_tokens integer",
                request_may_have_started=False,
            )
        return TokenCount(value, round(latency, 3))

    def create_stream(
        self,
        payload: Mapping[str, Any],
        *,
        on_text_delta: Callable[[str], None] | None = None,
    ) -> ProviderCall:
        started = self.clock()
        request = self._request("responses", payload)
        terminal: dict[str, Any] | None = None
        terminal_event_type: str | None = None
        created_response_id: str | None = None
        first_text_at: float | None = None
        deltas = 0
        last_sequence = -1
        display_error: str | None = None
        with self._open(request) as response:
            response_headers = getattr(response, "headers", None)
            provider_http_request_id = (
                response_headers.get("x-request-id")
                if response_headers is not None else None
            )
            processing_header = (
                response_headers.get("openai-processing-ms")
                if response_headers is not None else None
            )
            try:
                openai_processing_ms = (
                    float(processing_header) if processing_header is not None else None
                )
                if openai_processing_ms is not None and (
                    openai_processing_ms < 0 or not math.isfinite(openai_processing_ms)
                ):
                    openai_processing_ms = None
            except (TypeError, ValueError, OverflowError):
                openai_processing_ms = None
            for event in iter_sse_events(response):
                event_type = event.get("type")
                if terminal is not None:
                    if event_type in TERMINAL_STREAM_EVENTS:
                        raise ProviderError(
                            "provider stream emitted more than one terminal response",
                            request_may_have_started=True,
                        )
                    raise ProviderError(
                        "provider stream emitted an event after the terminal response",
                        request_may_have_started=True,
                    )
                sequence = event.get("sequence_number")
                if sequence is not None:
                    if isinstance(sequence, bool) or not isinstance(sequence, int):
                        raise ProviderError(
                            "provider sequence_number must be an integer",
                            request_may_have_started=True,
                        )
                    if sequence <= last_sequence:
                        raise ProviderError(
                            "provider sequence_number is not strictly increasing",
                            request_may_have_started=True,
                        )
                    last_sequence = sequence
                if event_type == "response.created":
                    created = event.get("response")
                    if not isinstance(created, Mapping) or not isinstance(created.get("id"), str):
                        raise ProviderError(
                            "response.created omitted a response id",
                            request_may_have_started=True,
                        )
                    if created_response_id is not None:
                        raise ProviderError(
                            "provider stream emitted response.created more than once",
                            request_may_have_started=True,
                        )
                    created_response_id = str(created["id"])
                elif event_type in {"response.output_text.delta", "response.refusal.delta"}:
                    delta = event.get("delta")
                    if isinstance(delta, str) and delta:
                        if first_text_at is None:
                            first_text_at = self.clock()
                        deltas += 1
                        if on_text_delta is not None:
                            try:
                                on_text_delta(delta)
                            except Exception as exc:
                                if display_error is None:
                                    display_error = type(exc).__name__
                elif event_type in TERMINAL_STREAM_EVENTS:
                    candidate = event.get("response")
                    if not isinstance(candidate, dict):
                        raise ProviderError(
                            f"{event_type} omitted a response object",
                            request_may_have_started=True,
                        )
                    terminal = candidate
                    terminal_event_type = str(event_type)
                elif event_type == "error":
                    message = event.get("message")
                    safe = message[:1_000] if isinstance(message, str) else "stream error"
                    raise ProviderError(safe, request_may_have_started=True)
        finished = self.clock()
        if terminal is None:
            raise ProviderError(
                "provider stream ended without a terminal response event",
                request_may_have_started=True,
            )
        served_model = _identifier(terminal.get("model"), "provider served model")
        terminal_response_id = _identifier(terminal.get("id"), "provider response id")
        if created_response_id is not None and created_response_id != terminal_response_id:
            raise ProviderError(
                "provider response id changed between created and terminal events",
                request_may_have_started=True,
            )
        if terminal.get("status") not in budget.RESPONSE_STATUSES:
            raise ProviderError(
                "provider terminal response has an unsupported status",
                request_may_have_started=True,
            )
        if terminal_event_type != f"response.{terminal['status']}":
            raise ProviderError(
                "provider terminal event type does not match response status",
                request_may_have_started=True,
            )
        if not isinstance(terminal.get("usage"), Mapping):
            raise ProviderError(
                "provider terminal response omitted usage",
                request_may_have_started=True,
            )
        return ProviderCall(
            terminal,
            extract_output_text(terminal),
            served_model,
            round((first_text_at - started) * 1_000, 3)
            if first_text_at is not None else None,
            round((finished - started) * 1_000, 3),
            deltas,
            display_error,
            provider_http_request_id,
            openai_processing_ms,
        )


def _served_model_matches(
    selected_model: str,
    served_model: str,
    served_model_map: Mapping[str, Sequence[str]] | None,
) -> bool:
    if served_model == selected_model:
        return True
    selected_suffix = selected_model.rsplit("-", 3)[-3:]
    selected_is_snapshot = False
    if len(selected_suffix) == 3:
        candidate = "-".join(selected_suffix)
        try:
            selected_is_snapshot = dt.date.fromisoformat(candidate).isoformat() == candidate
        except ValueError:
            pass
    prefix = selected_model + "-"
    if not selected_is_snapshot and served_model.startswith(prefix):
        suffix = served_model[len(prefix):]
        if len(suffix) == 10:
            try:
                if dt.date.fromisoformat(suffix).isoformat() == suffix:
                    return True
            except ValueError:
                pass
    if served_model_map is None:
        return False
    allowed = served_model_map.get(selected_model, ())
    return served_model in allowed


def settlement_payload(
    response: Mapping[str, Any],
    selected_model: str,
    *,
    served_model_map: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Bind a provider snapshot/alias to the configured price-card model key."""
    if response.get("service_tier") != "default":
        raise ProviderError(
            "provider response did not confirm the required default service tier",
            request_may_have_started=True,
        )
    served = _identifier(response.get("model"), "provider served model")
    if not _served_model_matches(selected_model, served, served_model_map):
        raise ProviderError(
            f"provider served model {served!r} is not authorized for {selected_model!r}",
            request_may_have_started=True,
        )
    result = copy.deepcopy(dict(response))
    result["provider_served_model"] = served
    result["model"] = selected_model
    return result


def _clip(value: str) -> str:
    if len(value) <= MAX_QUALITY_CAPTURE_CHARS:
        return value
    return value[:MAX_QUALITY_CAPTURE_CHARS] + "\n...[clipped]"


def _decode_quality_capture(data: bytearray, truncated: bool) -> str:
    value = _clip(bytes(data).decode("utf-8", errors="replace"))
    if truncated and "...[clipped]" not in value:
        value += "\n...[clipped]"
    return value


def _drain_quality_pipe(
    pipe: BinaryIO,
    captured: bytearray,
    truncated: list[bool],
) -> None:
    try:
        while True:
            chunk = pipe.read(8_192)
            if not chunk:
                return
            remaining = MAX_QUALITY_CAPTURE_BYTES - len(captured)
            if remaining > 0:
                captured.extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated[0] = True
    except (OSError, ValueError):
        return


def _bounded_quality_subprocess(
    argv: Sequence[str],
    *,
    cwd: Path | None,
    environment: Mapping[str, str],
    timeout_seconds: int,
) -> tuple[int | None, bool, str, str]:
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        env=dict(environment),
    )
    assert process.stdout is not None and process.stderr is not None
    stdout_data = bytearray()
    stderr_data = bytearray()
    stdout_truncated = [False]
    stderr_truncated = [False]
    readers = [
        threading.Thread(
            target=_drain_quality_pipe,
            args=(process.stdout, stdout_data, stdout_truncated),
            daemon=True,
        ),
        threading.Thread(
            target=_drain_quality_pipe,
            args=(process.stderr, stderr_data, stderr_truncated),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        return_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            process.kill()
        except OSError:
            pass
        try:
            return_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return_code = None
    for reader in readers:
        reader.join(timeout=2)
    for reader, pipe in zip(readers, (process.stdout, process.stderr)):
        try:
            pipe.close()
        except OSError:
            pass
        if reader.is_alive():
            reader.join(timeout=1)
    return (
        return_code,
        timed_out,
        _decode_quality_capture(stdout_data, stdout_truncated[0]),
        _decode_quality_capture(stderr_data, stderr_truncated[0]),
    )


def load_quality_policy(path: Path | None) -> QualityPolicy | None:
    if path is None:
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("quality policy must be a regular, non-symlink JSON file")
    data = path.read_bytes()
    if len(data) > 1_000_000:
        raise ValueError("quality policy exceeds 1 MB")
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"quality policy is invalid JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("quality policy must be a JSON object")
    argv = payload.get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(item, str) or not item for item in argv)
    ):
        raise ValueError("quality policy argv must be a non-empty string array")
    timeout = _positive_int(payload.get("timeout_seconds", 120), "quality timeout", maximum=900)
    cwd_value = payload.get("working_directory")
    cwd = None
    if cwd_value is not None:
        if not isinstance(cwd_value, str) or not cwd_value:
            raise ValueError("quality working_directory must be a non-empty string")
        cwd = Path(cwd_value).expanduser().resolve()
        if not cwd.is_dir():
            raise ValueError("quality working_directory must exist")
    return QualityPolicy(tuple(argv), timeout, cwd)


def quality_policy_sha256(policy: QualityPolicy | None) -> str:
    if policy is None:
        return hashlib.sha256(b"provider-status-only").hexdigest()
    effective_working_directory = (
        policy.working_directory if policy.working_directory is not None else Path.cwd()
    ).expanduser().resolve()
    return _sha256_json(
        {
            "argv": list(policy.argv),
            "timeout_seconds": policy.timeout_seconds,
            "working_directory": str(effective_working_directory),
        }
    )


def _request_template_sha256(
    template: Mapping[str, Any],
    task_class: str,
) -> str:
    """Bind every frozen request input that must be invariant across A/B runs."""
    if task_class not in budget.TASK_CLASSES:
        raise ValueError(f"task_class must be one of: {', '.join(budget.TASK_CLASSES)}")
    return _sha256_json(
        {
            "request_template": dict(template),
            "task_class": task_class,
        }
    )


def _normalize_served_model_map(
    value: Mapping[str, Sequence[str]] | None,
) -> dict[str, tuple[str, ...]]:
    if value is None:
        return {}
    result: dict[str, tuple[str, ...]] = {}
    for raw_model, raw_values in value.items():
        model = _identifier(raw_model, "served-model map key")
        if isinstance(raw_values, (str, bytes)) or not isinstance(raw_values, Sequence):
            raise ValueError("served-model map values must be string arrays")
        models = tuple(
            sorted({_identifier(item, "served-model map value") for item in raw_values})
        )
        result[model] = models
    return dict(sorted(result.items()))


def _pricing_policy_sha256(
    config: budget.BudgetConfig,
    projected_extra_cost_nano_usd: int,
    served_model_map: Mapping[str, Sequence[str]],
) -> str:
    """Bind pricing and safety invariants while leaving routing thresholds experimental."""
    return _sha256_json(
        {
            "cost_basis": "configured-price-card-nano-usd-v1",
            "models": list(config.models),
            "prices": [dataclasses.asdict(card) for card in config.prices],
            "projected_extra_cost_nano_usd": projected_extra_cost_nano_usd,
            "protected_tasks": sorted(config.protected_tasks),
            "served_model_map": {
                model: list(values) for model, values in served_model_map.items()
            },
            "service_tier": "default",
        }
    )


def run_quality_policy(
    policy: QualityPolicy | None,
    response: Mapping[str, Any],
    output_text: str,
    attempt_number: int,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> QualityEvidence:
    status = response.get("status")
    if status != "completed":
        return QualityEvidence(
            "fail",
            f"provider response status is {status!r}",
            "provider-status",
            None,
            False,
            "",
            "",
            0.0,
        )
    if policy is None:
        return QualityEvidence(
            "pass",
            "provider response completed; no semantic quality command configured",
            "provider-status-only",
            None,
            False,
            "",
            "",
            0.0,
        )
    with tempfile.TemporaryDirectory(prefix="aipc-quality-") as temp:
        root = Path(temp)
        response_path = root / "response.json"
        text_path = root / "output.txt"
        response_path.write_bytes(_canonical_json_bytes(response))
        text_path.write_text(output_text, encoding="utf-8")
        try:
            os.chmod(response_path, 0o600)
            os.chmod(text_path, 0o600)
        except OSError:
            pass
        replacements = {
            "{python}": sys.executable,
            "{response_json}": str(response_path),
            "{output_text}": str(text_path),
            "{attempt}": str(attempt_number),
        }
        argv = [replacements.get(item, item) for item in policy.argv]
        started = clock()
        try:
            safe_environment = {
                key: value
                for key, value in os.environ.items()
                if key.upper()
                in {
                    "HOME", "LANG", "LC_ALL", "PATH", "PATHEXT", "PYTHONPATH",
                    "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "USERPROFILE", "VIRTUAL_ENV",
                    "WINDIR",
                }
            }
            return_code, timed_out, stdout, stderr = _bounded_quality_subprocess(
                argv,
                cwd=policy.working_directory,
                environment=safe_environment,
                timeout_seconds=policy.timeout_seconds,
            )
            if timed_out:
                duration = (clock() - started) * 1_000
                return QualityEvidence(
                    "error",
                    f"quality command timed out after {policy.timeout_seconds}s",
                    "command",
                    return_code,
                    True,
                    stdout,
                    stderr,
                    round(duration, 3),
                )
        except OSError as exc:
            duration = (clock() - started) * 1_000
            return QualityEvidence(
                "error",
                f"quality command could not start: {type(exc).__name__}",
                "command",
                None,
                False,
                "",
                "",
                round(duration, 3),
            )
        duration = (clock() - started) * 1_000
        if return_code == 0:
            gate = "pass"
            reason = "quality command passed and provider response completed"
        elif return_code == 1:
            gate = "fail"
            reason = "quality command reported a model-quality failure"
        else:
            gate = "error"
            reason = f"quality command evaluator error exit={return_code}"
        return QualityEvidence(
            gate,
            reason,
            "command",
            return_code,
            False,
            stdout,
            stderr,
            round(duration, 3),
        )


def _wire(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {key: _wire(item) for key, item in dataclasses.asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _count_ladder(
    client: ResponsesClient,
    request: Mapping[str, Any],
    models: Sequence[str],
    *,
    workers: int = 4,
) -> tuple[dict[str, int], float]:
    started = time.monotonic()
    results: dict[str, int] = {}
    failures: list[str] = []

    def invoke(model: str) -> tuple[str, TokenCount]:
        return model, client.count_input_tokens(build_count_payload(request, model))

    with ThreadPoolExecutor(max_workers=min(workers, len(models))) as executor:
        futures = {executor.submit(invoke, model): model for model in models}
        for future in as_completed(futures):
            model = futures[future]
            try:
                resolved, count = future.result()
            except Exception as exc:
                failures.append(f"{model}: {exc}")
            else:
                results[resolved] = count.input_tokens
    if failures:
        raise ProviderError(
            "input token counting failed for the reviewed ladder: " + "; ".join(failures),
            request_may_have_started=False,
        )
    return results, round((time.monotonic() - started) * 1_000, 3)


def _child_request_id(request_id: str) -> str:
    suffix = "-quality-upgrade"
    if len(request_id) + len(suffix) <= 256:
        return request_id + suffix
    digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:24]
    return f"upgrade-{digest}"


def execute_budgeted_request(
    *,
    db: Path,
    user_key: str,
    request_id: str,
    logical_request_id: str,
    request_payload: Mapping[str, Any],
    task_class: str,
    client: ResponsesClient,
    quality_policy: QualityPolicy | None = None,
    reservation_ttl_seconds: int = budget.DEFAULT_RESERVATION_TTL_SECONDS,
    projected_extra_cost_nano_usd: int = 0,
    served_model_map: Mapping[str, Sequence[str]] | None = None,
    on_text_delta: Callable[[str], None] | None = None,
) -> GatewayResult:
    """Run one logical task, including at most one quality-authorized upgrade."""
    started = time.monotonic()
    projected_extra_cost_nano_usd = _nonnegative_int(
        projected_extra_cost_nano_usd,
        "projected_extra_cost_nano_usd",
        maximum=budget.SQLITE_MAX_INT,
    )
    template = _request_template(request_payload)
    requested_model = str(template["model"])
    template_hash = _request_template_sha256(template, task_class)
    normalized_served_model_map = _normalize_served_model_map(served_model_map)
    config = budget.get_config(db, user_key)
    if requested_model not in config.models:
        raise ValueError("request model is not in the configured reviewed ladder")
    counts, count_latency = _count_ladder(client, template, config.models)
    projected_input = max(counts.values())
    projected_output = int(template["max_output_tokens"])
    attempts: list[AttemptTrace] = []
    current_request_id = _identifier(request_id, "request_id")
    current_requested_model = requested_model
    parent_request_id: str | None = None
    total_count_latency = count_latency
    terminal_override: str | None = None
    policy_hash = quality_policy_sha256(quality_policy)
    pricing_hash = _pricing_policy_sha256(
        config,
        projected_extra_cost_nano_usd,
        normalized_served_model_map,
    )

    for attempt_number in (1, 2):
        attempt_started = time.monotonic()
        route = budget.route_request(
            db,
            user_key,
            current_request_id,
            current_requested_model,
            projected_input,
            projected_output,
            request_payload_builder=lambda model: _canonical_json_bytes(
                build_provider_payload(template, model)
            ),
            projected_extra_cost_nano_usd=projected_extra_cost_nano_usd,
            task_class=task_class,
            logical_request_id=logical_request_id,
            parent_request_id=parent_request_id,
            reservation_ttl_seconds=reservation_ttl_seconds,
            expected_config_version=config.config_version,
        )
        if route.action == "block":
            final = budget.get_final_result(db, user_key, logical_request_id)
            return GatewayResult(
                "aipc.gateway.v1",
                "openai-responses",
                final.logical_request_hash,
                final.final_status,
                final.final_model,
                attempts[-1].served_model if attempts else None,
                attempts[-1].output_text if attempts else "",
                [_wire(item) for item in attempts],
                final.total_input_tokens,
                final.total_cached_tokens,
                final.total_cache_write_tokens,
                final.total_output_tokens,
                final.total_reasoning_tokens,
                final.total_tokens,
                final.total_cost_nano_usd,
                final.total_cost_usd,
                final.estimated_cost_savings_nano_usd,
                None,
                total_count_latency,
                attempts[-1].ttft_ms if attempts else None,
                sum(item.provider_latency_ms for item in attempts),
                round((time.monotonic() - started) * 1_000, 3),
                "command" if quality_policy else "provider-status-only",
                template_hash,
                policy_hash,
                pricing_hash,
            )
        if not route.execution_authorized or route.selected_model is None:
            raise GatewayError(
                "route receipt is not executable; use a new request_id instead of replaying it"
            )
        selected_model = route.selected_model
        provider_payload = build_provider_payload(template, selected_model)
        provider_hash = _sha256_json(provider_payload)
        if provider_hash != route.request_payload_sha256:
            raise GatewayError("route payload hash does not match the exact provider request")
        lease: _LeaseRenewer
        try:
            with _LeaseRenewer(
                db, user_key, current_request_id, reservation_ttl_seconds
            ) as lease:
                call = client.create_stream(provider_payload, on_text_delta=on_text_delta)
        except ProviderError as exc:
            if not exc.request_may_have_started:
                budget.release_reservation(db, user_key, current_request_id)
            raise
        raw_response = call.response
        settled_payload = settlement_payload(
            raw_response,
            selected_model,
            served_model_map=normalized_served_model_map,
        )
        settled_payload["extra_cost_nano_usd"] = projected_extra_cost_nano_usd
        usage = budget.settle_usage(
            db,
            user_key,
            current_request_id,
            selected_model,
            settled_payload,
        )
        evidence = run_quality_policy(
            quality_policy,
            raw_response,
            call.output_text,
            attempt_number,
        )
        if evidence.gate in budget.QUALITY_GATES:
            quality: object = budget.assess_quality(
                db,
                user_key,
                current_request_id,
                evidence.gate,
                evidence.reason,
            )
            upgrade_authorized = bool(quality.automatic_upgrade_authorized)
            next_model = quality.next_model
        else:
            quality = {
                "recorded": False,
                "effective_quality": "unknown",
                "upgrade_recommended": False,
                "automatic_upgrade_authorized": False,
                "reason": evidence.reason,
            }
            upgrade_authorized = False
            next_model = None
            terminal_override = "quality-evaluator-error"
        attempts.append(
            AttemptTrace(
                attempt_number,
                _wire(route),
                dict(sorted(counts.items())),
                projected_input,
                count_latency if attempt_number == 1 else 0.0,
                template_hash,
                provider_hash,
                str(raw_response["id"]),
                call.provider_http_request_id,
                selected_model,
                call.served_model,
                str(raw_response["status"]),
                call.output_text,
                call.display_error,
                lease.error,
                call.ttft_ms,
                call.openai_processing_ms,
                call.latency_ms,
                round((time.monotonic() - attempt_started) * 1_000, 3),
                _wire(usage),
                _wire(evidence),
                _wire(quality),
            )
        )
        if not upgrade_authorized or next_model is None:
            break
        parent_request_id = current_request_id
        current_request_id = _child_request_id(request_id)
        current_requested_model = str(next_model)

    final = budget.get_final_result(db, user_key, logical_request_id)
    latest = attempts[-1] if attempts else None
    first_ttft = next(
        (item.ttft_ms for item in attempts if item.ttft_ms is not None), None
    )
    return GatewayResult(
        "aipc.gateway.v1",
        "openai-responses",
        final.logical_request_hash,
        terminal_override or final.final_status,
        final.final_model,
        latest.served_model if latest is not None else None,
        latest.output_text if latest is not None else "",
        [_wire(item) for item in attempts],
        final.total_input_tokens,
        final.total_cached_tokens,
        final.total_cache_write_tokens,
        final.total_output_tokens,
        final.total_reasoning_tokens,
        final.total_tokens,
        final.total_cost_nano_usd,
        final.total_cost_usd,
        final.estimated_cost_savings_nano_usd,
        None,
        total_count_latency,
        first_ttft,
        round(sum(item.provider_latency_ms for item in attempts), 3),
        round((time.monotonic() - started) * 1_000, 3),
        "command" if quality_policy else "provider-status-only",
        template_hash,
        policy_hash,
        pricing_hash,
    )


def _read_json(path: Path, label: str) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular, non-symlink JSON file")
    data = path.read_bytes()
    if len(data) > MAX_REQUEST_BYTES:
        raise ValueError(f"{label} exceeds {MAX_REQUEST_BYTES} bytes")
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"{label} is invalid JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label} must contain a JSON object")
    return payload


def _read_served_model_map(path: Path | None) -> Mapping[str, Sequence[str]] | None:
    if path is None:
        return None
    payload = _read_json(path, "served-model map")
    result: dict[str, tuple[str, ...]] = {}
    for selected, values in payload.items():
        if not isinstance(selected, str) or not selected:
            raise ValueError("served-model map keys must be non-empty strings")
        if (
            not isinstance(values, list)
            or any(not isinstance(item, str) or not item for item in values)
        ):
            raise ValueError("served-model map values must be string arrays")
        result[selected] = tuple(values)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Execute one budgeted OpenAI Responses task with provider-reported usage telemetry."
        )
    )
    parser.add_argument("--db", type=Path, default=Path(".aipc/model-budget.sqlite3"))
    parser.add_argument("--user", required=True, help="Trusted application user/budget key.")
    parser.add_argument("--request-id", required=True, help="Unique provider-attempt idempotency key.")
    parser.add_argument("--logical-request-id", help="Stable logical task id; defaults to request-id.")
    parser.add_argument("--request", required=True, type=Path, help="Responses API request JSON.")
    parser.add_argument("--task-class", choices=budget.TASK_CLASSES, default="routine")
    parser.add_argument("--quality-policy", type=Path, help="Deterministic command policy JSON.")
    parser.add_argument("--served-model-map", type=Path, help="Optional exact alias/snapshot map JSON.")
    parser.add_argument(
        "--reservation-ttl-seconds",
        type=int,
        default=budget.DEFAULT_RESERVATION_TTL_SECONDS,
    )
    parser.add_argument("--projected-extra-cost-nano-usd", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument(
        "--live-output",
        choices=("stderr", "none"),
        default="stderr",
        help="Stream visible text deltas without corrupting JSON stdout.",
    )
    parser.add_argument("--format", choices=("json", "jsonl", "markdown"), default="json")
    return parser


def _emit(result: GatewayResult, output_format: str) -> None:
    payload = _wire(result)
    if output_format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if output_format == "jsonl":
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return
    print("# Model Budget Gateway Result")
    print(f"- final status: `{result.final_status}`")
    print(f"- final model: `{result.final_model or 'none'}`")
    print(f"- provider served model: `{result.served_model or 'none'}`")
    print(f"- attempts: `{len(result.attempts)}`")
    print(f"- total tokens: `{result.total_tokens}`")
    print(f"- total cost: `${result.total_cost_usd}`")
    print(f"- TTFT: `{result.ttft_ms if result.ttft_ms is not None else 'n/a'} ms`")
    print(f"- end to end: `{result.e2e_ms} ms`")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required")
        request = _read_json(args.request, "request")
        policy = load_quality_policy(args.quality_policy)
        served_map = _read_served_model_map(args.served_model_map)
        client = OpenAIResponsesClient(
            api_key,
            timeout_seconds=args.timeout_seconds,
            project=os.environ.get("OPENAI_PROJECT"),
            organization=os.environ.get("OPENAI_ORGANIZATION"),
        )

        def on_delta(delta: str) -> None:
            if args.live_output == "stderr":
                sys.stderr.write(delta)
                sys.stderr.flush()

        result = execute_budgeted_request(
            db=args.db,
            user_key=args.user,
            request_id=args.request_id,
            logical_request_id=args.logical_request_id or args.request_id,
            request_payload=request,
            task_class=args.task_class,
            client=client,
            quality_policy=policy,
            reservation_ttl_seconds=args.reservation_ttl_seconds,
            projected_extra_cost_nano_usd=args.projected_extra_cost_nano_usd,
            served_model_map=served_map,
            on_text_delta=on_delta,
        )
        if args.live_output == "stderr":
            sys.stderr.write("\n")
        _emit(result, args.format)
        return 0 if result.final_status == "success" else 3
    except (ValueError, GatewayError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
