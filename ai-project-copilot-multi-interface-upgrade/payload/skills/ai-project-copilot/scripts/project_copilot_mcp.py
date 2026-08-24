#!/usr/bin/env python3
"""MCP stdio adapter for AI Project Copilot.

Supports the stateless MCP 2026-07-28 request-metadata era and legacy initialize
handshakes. Only Project Copilot's fixed capability registry is exposed; callers
cannot submit arbitrary shell commands.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, BinaryIO, Mapping, TextIO

from project_copilot_core import (
    CopilotEngine,
    CopilotError,
    ExecutionPolicy,
    ValidationError,
    VERSION,
    invoke,
)

MODERN_PROTOCOL = "2026-07-28"
LEGACY_PROTOCOLS = ("2025-11-25", "2025-06-18")
SERVER_NAME = "ai-project-copilot"
PROTOCOL_META_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_INFO_META_KEY = "io.modelcontextprotocol/clientInfo"
CLIENT_CAPABILITIES_META_KEY = "io.modelcontextprotocol/clientCapabilities"
SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"
MAX_MESSAGE_BYTES = 1024 * 1024
MESSAGE_DRAIN_CHUNK_BYTES = 64 * 1024
MAX_JSON_NESTING = 256


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
UNSUPPORTED_PROTOCOL_VERSION = -32022


def _result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        payload["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": payload}


class MCPAdapter:
    def __init__(self, engine: CopilotEngine):
        self.engine = engine
        self.legacy_protocol: str | None = None

    def _server_meta(self) -> dict[str, Any]:
        return {SERVER_INFO_META_KEY: {"name": SERVER_NAME, "version": VERSION}}

    def _modern_result(self, request_id: Any, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        body = dict(payload or {})
        body["resultType"] = body.get("resultType", "complete")
        body["_meta"] = {**self._server_meta(), **dict(body.get("_meta", {}))}
        return _result(request_id, body)

    def _request_meta(self, message: Mapping[str, Any]) -> Mapping[str, Any] | None:
        params = message.get("params")
        if not isinstance(params, Mapping):
            return None
        meta = params.get("_meta")
        return meta if isinstance(meta, Mapping) else None

    def _validate_modern_request(self, message: Mapping[str, Any]) -> dict[str, Any] | None:
        meta = self._request_meta(message)
        if meta is None:
            return _error(message.get("id"), -32602, "Invalid params", "modern MCP requests require params._meta")
        requested = meta.get(PROTOCOL_META_KEY)
        if not isinstance(requested, str):
            return _error(message.get("id"), -32602, "Invalid params", f"missing required _meta.{PROTOCOL_META_KEY}")
        if requested != MODERN_PROTOCOL:
            return _error(
                message.get("id"),
                UNSUPPORTED_PROTOCOL_VERSION,
                "Unsupported protocol version",
                {"supported": [MODERN_PROTOCOL, *LEGACY_PROTOCOLS], "requested": requested},
            )
        capabilities = meta.get(CLIENT_CAPABILITIES_META_KEY)
        if not isinstance(capabilities, Mapping):
            return _error(
                message.get("id"),
                -32602,
                "Invalid params",
                f"missing required _meta.{CLIENT_CAPABILITIES_META_KEY}",
            )
        client_info = meta.get(CLIENT_INFO_META_KEY)
        if client_info is not None and not isinstance(client_info, Mapping):
            return _error(message.get("id"), -32602, "Invalid params", f"_meta.{CLIENT_INFO_META_KEY} must be an object")
        return None

    def _is_modern(self, message: Mapping[str, Any]) -> bool:
        meta = self._request_meta(message)
        return isinstance(meta, Mapping) and meta.get(PROTOCOL_META_KEY) == MODERN_PROTOCOL

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": item["name"],
                "description": item["description"],
                "inputSchema": item["inputSchema"],
                "annotations": {
                    "readOnlyHint": not item.get("consequential", False),
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
            }
            for item in self.engine.capability_specs()
        ]

    def handle(self, message: Mapping[str, Any]) -> dict[str, Any] | None:
        if message.get("jsonrpc") != "2.0":
            return _error(message.get("id"), -32600, "Invalid Request")
        method = message.get("method")
        request_id = message.get("id")
        if not isinstance(method, str):
            return _error(request_id, -32600, "Invalid Request")
        is_notification = "id" not in message

        if method in {"notifications/initialized", "notifications/cancelled"}:
            return None

        if method == "initialize":
            params = message.get("params") if isinstance(message.get("params"), Mapping) else {}
            requested = params.get("protocolVersion")
            protocol = requested if requested in LEGACY_PROTOCOLS else LEGACY_PROTOCOLS[0]
            self.legacy_protocol = protocol
            return None if is_notification else _result(
                request_id,
                {
                    "protocolVersion": protocol,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": VERSION},
                    "instructions": "AI Project Copilot deterministic engineering tools; consequential writes require human authority.",
                },
            )

        modern = self._is_modern(message) or method == "server/discover"
        if modern:
            invalid = self._validate_modern_request(message)
            if invalid is not None:
                return None if is_notification else invalid
        elif self.legacy_protocol is None:
            return None if is_notification else _error(
                request_id,
                -32602,
                "Invalid params",
                "request is neither a valid MCP 2026-07-28 request nor part of an initialized legacy session",
            )

        if method == "ping":
            if is_notification:
                return None
            return self._modern_result(request_id) if modern else _result(request_id, {})

        if method == "server/discover":
            if is_notification:
                return None
            return self._modern_result(
                request_id,
                {
                    "supportedVersions": [MODERN_PROTOCOL, *LEGACY_PROTOCOLS],
                    "capabilities": {"tools": {"listChanged": False}},
                    "instructions": (
                        "Use read-oriented Project Copilot tools for repository context, review risk, security, "
                        "release intelligence, deterministic evals, and goal routing. Consequential repository writes, "
                        "merge, publish, deploy, delete, and permission changes remain human-controlled."
                    ),
                },
            )

        if method == "tools/list":
            if is_notification:
                return None
            payload = {"tools": self.tools()}
            return self._modern_result(request_id, payload) if modern else _result(request_id, payload)

        if method == "tools/call":
            params = message.get("params")
            if not isinstance(params, Mapping):
                return _error(request_id, -32602, "Invalid params", "params must be an object")
            name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(name, str) or not isinstance(arguments, Mapping):
                return _error(request_id, -32602, "Invalid params", "tool name must be a string and arguments an object")
            try:
                result = invoke(self.engine, name, arguments)
            except ValidationError as exc:
                return _error(request_id, -32602, "Invalid params", str(exc))
            except CopilotError as exc:
                payload = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
                return self._modern_result(request_id, payload) if modern else _result(request_id, payload)
            text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
            payload = {
                "content": [{"type": "text", "text": text}],
                "structuredContent": result,
                "isError": result.get("status") == "failed",
            }
            return self._modern_result(request_id, payload) if modern else _result(request_id, payload)

        return None if is_notification else _error(request_id, -32601, "Method not found", method)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI Project Copilot MCP stdio adapter")
    parser.add_argument("--allow-root", action="append", default=[], help="repository/file root visible to MCP callers; repeatable")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--max-capture-bytes", type=int, default=2 * 1024 * 1024)
    return parser


def _write_response(output: TextIO, response: dict[str, Any]) -> None:
    output.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
    output.flush()


def _drain_to_newline(source: BinaryIO) -> None:
    """Discard the remainder of one rejected oversized frame.

    ``readline(limit)`` returns a prefix without consuming the line terminator
    when a client exceeds the limit.  Draining through that terminator keeps the
    next JSON-RPC frame aligned instead of turning one bad request into many
    parse errors.
    """
    while True:
        remainder = source.readline(MESSAGE_DRAIN_CHUNK_BYTES)
        if not remainder or remainder.endswith(b"\n"):
            return


def serve_stdio(
    adapter: MCPAdapter,
    input_stream: BinaryIO | None = None,
    output_stream: TextIO | None = None,
) -> int:
    source = input_stream if input_stream is not None else sys.stdin.buffer
    output = output_stream if output_stream is not None else sys.stdout
    while True:
        encoded = source.readline(MAX_MESSAGE_BYTES + 1)
        if not encoded:
            break
        if len(encoded) > MAX_MESSAGE_BYTES:
            if not encoded.endswith(b"\n"):
                _drain_to_newline(source)
            response = _error(None, -32600, "Invalid Request", "message exceeds size limit")
            _write_response(output, response)
            continue
        try:
            raw = encoded.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            response = _error(None, -32700, "Parse error", str(exc))
            _write_response(output, response)
            continue
        if not raw:
            continue
        try:
            if _json_nesting_exceeds(raw):
                raise ValueError("JSON nesting exceeds safe limit")
            message = json.loads(raw)
            if not isinstance(message, dict):
                response = _error(None, -32600, "Invalid Request")
            else:
                response = adapter.handle(message)
        except (json.JSONDecodeError, ValueError, RecursionError) as exc:
            response = _error(None, -32700, "Parse error", str(exc))
        except Exception as exc:  # Fail closed at the protocol boundary; log detail to stderr.
            print(f"[aipc-mcp] internal error: {exc}", file=sys.stderr)
            response = _error(None, -32603, "Internal error")
        if response is not None:
            _write_response(output, response)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    roots = tuple(Path(value) for value in (args.allow_root or [str(Path.cwd())]))
    try:
        engine = CopilotEngine(
            policy=ExecutionPolicy(
                allowed_roots=roots,
                timeout_seconds=args.timeout,
                max_capture_bytes=args.max_capture_bytes,
            )
        )
    except CopilotError as exc:
        print(f"failed to configure MCP server: {exc}", file=sys.stderr)
        return 2
    protocols = ",".join((MODERN_PROTOCOL, *LEGACY_PROTOCOLS))
    print(
        f"[aipc-mcp] {SERVER_NAME} {VERSION}; protocols={protocols}; allowed_roots={','.join(map(str, engine.policy.allowed_roots))}",
        file=sys.stderr,
    )
    return serve_stdio(MCPAdapter(engine))


if __name__ == "__main__":
    raise SystemExit(main())
