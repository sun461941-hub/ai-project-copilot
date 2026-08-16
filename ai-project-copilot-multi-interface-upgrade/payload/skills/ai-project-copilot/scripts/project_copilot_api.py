#!/usr/bin/env python3
"""Minimal dependency-free REST adapter for AI Project Copilot.

This adapter is intentionally small: it exposes read-oriented deterministic Project
Copilot capabilities and does not provide arbitrary command execution.  It binds to
loopback by default.  Binding to a non-loopback address requires a bearer token loaded
from an environment variable.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from typing import Any

from project_copilot_core import CopilotEngine, CopilotError, ExecutionPolicy, invoke

MAX_REQUEST_BYTES = 1024 * 1024


def _is_loopback(host: str) -> bool:
    if host in {"localhost", "::1"}:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _bearer(headers: Any) -> str | None:
    raw = headers.get("Authorization")
    if not raw or not raw.startswith("Bearer "):
        return None
    return raw[7:].strip()


class CopilotHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        engine: CopilotEngine,
        api_key: str | None,
        *,
        request_timeout: float = 30.0,
        max_concurrent_requests: int = 16,
    ):
        if request_timeout <= 0 or request_timeout > 300:
            raise ValueError("request_timeout must be > 0 and <= 300 seconds")
        if max_concurrent_requests < 1 or max_concurrent_requests > 256:
            raise ValueError("max_concurrent_requests must be between 1 and 256")
        self.request_timeout = float(request_timeout)
        self._request_slots = threading.BoundedSemaphore(max_concurrent_requests)
        super().__init__(server_address, CopilotHandler)
        self.engine = engine
        self.api_key = api_key

    def get_request(self) -> tuple[Any, Any]:
        request, address = super().get_request()
        request.settimeout(self.request_timeout)
        return request, address

    def process_request(self, request: Any, client_address: Any) -> None:
        self._request_slots.acquire()
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


class CopilotHandler(BaseHTTPRequestHandler):
    server: CopilotHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003 - stdlib hook name
        print(f"[aipc-api] {self.address_string()} {format % args}", file=sys.stderr)

    def _json(self, status: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        if self.headers.get("Origin"):
            # This API is intended for trusted agents/programs, not arbitrary browser
            # JavaScript.  Rejecting Origin-bearing requests also reduces local DNS
            # rebinding exposure.
            self.close_connection = True
            self._json(HTTPStatus.FORBIDDEN, {"status": "error", "error": "browser Origin requests are not accepted"})
            return False
        required = self.server.api_key
        if required is None:
            return True
        supplied = _bearer(self.headers)
        if supplied is None or not hmac.compare_digest(required, supplied):
            self.close_connection = True
            self._json(HTTPStatus.UNAUTHORIZED, {"status": "error", "error": "invalid bearer token"})
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._authorized():
            return
        if self.path == "/health":
            self._json(HTTPStatus.OK, {"status": "ok", "service": "ai-project-copilot"})
        elif self.path == "/v1/capabilities":
            self._json(HTTPStatus.OK, {"capabilities": self.server.engine.capability_specs()})
        else:
            self._json(HTTPStatus.NOT_FOUND, {"status": "error", "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._authorized():
            return
        if self.path != "/v1/run":
            self._json(HTTPStatus.NOT_FOUND, {"status": "error", "error": "not found"})
            return
        content_type = self.headers.get("Content-Type", "")
        if content_type and content_type.split(";", 1)[0].strip().lower() != "application/json":
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"status": "error", "error": "Content-Type must be application/json"})
            return
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            self._json(HTTPStatus.LENGTH_REQUIRED, {"status": "error", "error": "Content-Length is required"})
            return
        try:
            length = int(raw_length)
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, {"status": "error", "error": "invalid Content-Length"})
            return
        if length < 0 or length > MAX_REQUEST_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"status": "error", "error": "request body too large"})
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"status": "error", "error": f"invalid JSON: {exc}"})
            return
        if not isinstance(body, dict):
            self._json(HTTPStatus.BAD_REQUEST, {"status": "error", "error": "body must be a JSON object"})
            return
        capability = body.get("capability", "copilot_run")
        arguments = body.get("arguments")
        if arguments is None and capability == "copilot_run" and "goal" in body:
            arguments = {key: value for key, value in body.items() if key != "capability"}
        if not isinstance(capability, str) or not isinstance(arguments, dict):
            self._json(HTTPStatus.BAD_REQUEST, {"status": "error", "error": "capability must be a string and arguments an object"})
            return
        try:
            result = invoke(self.server.engine, capability, arguments)
        except CopilotError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"status": "error", "error": str(exc)})
            return
        status = HTTPStatus.OK if result.get("status") in {"completed", "partial"} else HTTPStatus.UNPROCESSABLE_ENTITY
        self._json(status, result)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI Project Copilot REST adapter")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--allow-root", action="append", default=[], help="repository/file root visible to API callers; repeatable")
    parser.add_argument("--api-key-env", default="AIPC_API_KEY", help="environment variable containing bearer token")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--max-capture-bytes", type=int, default=2 * 1024 * 1024)
    parser.add_argument("--request-timeout", type=float, default=30.0, help="per-connection HTTP read/write timeout")
    parser.add_argument("--max-concurrent-requests", type=int, default=16, help="bound simultaneous REST request threads")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 1 <= args.port <= 65535:
        print("port must be between 1 and 65535", file=sys.stderr)
        return 2
    api_key = os.environ.get(args.api_key_env)
    if api_key is not None and not api_key:
        api_key = None
    if not _is_loopback(args.host) and not api_key:
        print(
            f"refusing to bind {args.host!r} without a bearer token in environment variable {args.api_key_env}",
            file=sys.stderr,
        )
        return 2
    roots = tuple(Path(value) for value in (args.allow_root or [str(Path.cwd())]))
    try:
        engine = CopilotEngine(
            policy=ExecutionPolicy(
                allowed_roots=roots,
                timeout_seconds=args.timeout,
                max_capture_bytes=args.max_capture_bytes,
            )
        )
        server = CopilotHTTPServer(
            (args.host, args.port),
            engine,
            api_key,
            request_timeout=args.request_timeout,
            max_concurrent_requests=args.max_concurrent_requests,
        )
    except (CopilotError, OSError, ValueError) as exc:
        print(f"failed to start API: {exc}", file=sys.stderr)
        return 2
    token_state = "enabled" if api_key else "disabled (loopback only)"
    print(
        f"AI Project Copilot REST listening on http://{args.host}:{args.port}; bearer auth {token_state}; allowed roots: {', '.join(map(str, engine.policy.allowed_roots))}",
        file=sys.stderr,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
