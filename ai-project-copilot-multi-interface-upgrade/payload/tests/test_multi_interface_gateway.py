from __future__ import annotations

import io
import json
import re
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "ai-project-copilot" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from project_copilot_api import CopilotHTTPServer
from project_copilot_core import CopilotEngine, ExecutionPolicy, ValidationError, invoke
from project_copilot_mcp import (
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    MODERN_PROTOCOL,
    PROTOCOL_META_KEY,
    SERVER_INFO_META_KEY,
    MAX_MESSAGE_BYTES,
    MCPAdapter,
    serve_stdio,
)


FAKE_SCRIPT = r'''#!/usr/bin/env python3
import json
import sys
from pathlib import Path

name = Path(__file__).stem
args = sys.argv[1:]
if name == "workflow_router":
    prompt = args[args.index("--prompt") + 1]
    if prompt == "BIG":
        print("x" * 10000000)
    else:
        routes = []
        text = prompt.lower()
        if "review" in text:
            routes.append({"mode": "review"})
        if "security" in text or "secure" in text:
            routes.append({"mode": "secure"})
        if "release" in text:
            routes.append({"mode": "release"})
        if not routes:
            routes.append({"mode": "discover"})
        print(json.dumps({"routes": routes}))
elif name == "repo_context":
    print(json.dumps({"focus_files": [{"path": "src/auth.py"}]}))
elif name == "change_risk":
    print(json.dumps({"level": "medium", "score": 42}))
elif name == "supply_chain_guard":
    print(json.dumps({"score": 96, "findings": []}))
elif name == "mcp_config_audit":
    print(json.dumps({"findings": []}))
elif name == "release_intel":
    print(json.dumps({"release_ready": True, "suggested_version": "2.2.0"}))
elif name == "maintainer_triage":
    print(json.dumps({"priority": "medium"}))
elif name == "run_skill_evals":
    print(json.dumps({"passed": 3, "failed": 0}))
else:
    print(json.dumps({"name": name, "args": args}))
'''


def modern_meta(protocol: str = MODERN_PROTOCOL) -> dict[str, object]:
    return {
        "_meta": {
            PROTOCOL_META_KEY: protocol,
            CLIENT_INFO_META_KEY: {"name": "test-client", "version": "1.0"},
            CLIENT_CAPABILITIES_META_KEY: {},
        }
    }


class MultiInterfaceGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.skill = self.base / "skill"
        self.scripts = self.skill / "scripts"
        self.repo = self.base / "repo"
        self.scripts.mkdir(parents=True)
        self.repo.mkdir()
        (self.repo / ".git").mkdir()
        for name in (
            "workflow_router",
            "repo_context",
            "change_risk",
            "supply_chain_guard",
            "mcp_config_audit",
            "release_intel",
            "maintainer_triage",
            "run_skill_evals",
        ):
            (self.scripts / f"{name}.py").write_text(FAKE_SCRIPT, encoding="utf-8")
        self.engine = CopilotEngine(
            self.skill,
            ExecutionPolicy(allowed_roots=(self.base,), timeout_seconds=10, max_capture_bytes=4096),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_capability_registry_is_fixed_and_has_expected_surfaces(self) -> None:
        names = {item["name"] for item in self.engine.capability_specs()}
        self.assertEqual(
            {
                "route",
                "analyze_repository",
                "review_changes",
                "scan_security",
                "release_readiness",
                "maintainer_triage",
                "run_evals",
                "copilot_run",
            },
            names,
        )

    def test_repository_analysis_uses_existing_helper_and_parses_json(self) -> None:
        result = invoke(self.engine, "analyze_repository", {"repo": str(self.repo), "task": "review auth"})
        self.assertEqual("completed", result["status"])
        self.assertEqual("src/auth.py", result["data"]["focus_files"][0]["path"])
        self.assertNotIn("shell", " ".join(result["results"][0]["argv"]))

    def test_security_capability_composes_two_existing_scanners(self) -> None:
        result = invoke(self.engine, "scan_security", {"repo": str(self.repo)})
        self.assertEqual("completed", result["status"])
        self.assertEqual(2, len(result["results"]))
        self.assertEqual(96, result["data"][0]["score"])
        self.assertEqual([], result["data"][1]["findings"])

    def test_allowed_root_blocks_path_escape(self) -> None:
        outside = Path(tempfile.mkdtemp())
        try:
            with self.assertRaises(ValidationError):
                invoke(self.engine, "analyze_repository", {"repo": str(outside), "task": "x"})
        finally:
            outside.rmdir()

    def test_unknown_arguments_are_rejected_by_registry_schema(self) -> None:
        with self.assertRaises(ValidationError):
            invoke(self.engine, "route", {"prompt": "review", "unexpected": "ignored-before"})

    def test_capture_is_bounded_while_draining_large_output(self) -> None:
        small = CopilotEngine(
            self.skill,
            ExecutionPolicy(allowed_roots=(self.base,), timeout_seconds=10, max_capture_bytes=1024),
        )
        result = invoke(small, "route", {"prompt": "BIG"})
        item = result["results"][0]
        self.assertTrue(item["stdout_truncated"])
        self.assertLess(len(item["stdout"].encode("utf-8")), 1400)
        match = re.search(r"of (?P<total>\d+) bytes\]$", item["stdout"])
        self.assertIsNotNone(match)
        self.assertGreaterEqual(int(match.group("total")), 10_000_001)

    def test_goal_orchestrator_executes_review_and_security(self) -> None:
        result = invoke(self.engine, "copilot_run", {"goal": "review this change for security", "repo": str(self.repo)})
        self.assertEqual("completed", result["status"])
        self.assertEqual(["review", "secure"], result["lanes"])
        stages = {item["stage"]: item["status"] for item in result["stages"]}
        self.assertEqual("completed", stages["discover"])
        self.assertEqual("completed", stages["review"])
        self.assertEqual("completed", stages["secure"])

    def test_goal_orchestrator_reports_partial_when_required_release_inputs_are_missing(self) -> None:
        result = invoke(self.engine, "copilot_run", {"goal": "release this", "repo": str(self.repo)})
        self.assertEqual("partial", result["status"])
        release = next(item for item in result["stages"] if item["stage"] == "release")
        self.assertEqual("skipped", release["status"])

    def test_mcp_2026_discovery_and_tool_results_match_final_shape(self) -> None:
        adapter = MCPAdapter(self.engine)
        discover = adapter.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": modern_meta()}
        )
        assert discover is not None
        self.assertEqual("complete", discover["result"]["resultType"])
        self.assertIn(MODERN_PROTOCOL, discover["result"]["supportedVersions"])
        self.assertNotIn("serverInfo", discover["result"])
        self.assertEqual("ai-project-copilot", discover["result"]["_meta"][SERVER_INFO_META_KEY]["name"])

        listed = adapter.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": modern_meta()})
        assert listed is not None
        self.assertEqual("complete", listed["result"]["resultType"])
        self.assertTrue(any(item["name"] == "scan_security" for item in listed["result"]["tools"]))

        params = {"name": "route", "arguments": {"prompt": "review"}, **modern_meta()}
        called = adapter.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": params})
        assert called is not None
        self.assertEqual("complete", called["result"]["resultType"])
        self.assertFalse(called["result"]["isError"])
        self.assertEqual("completed", called["result"]["structuredContent"]["status"])

    def test_mcp_2026_rejects_missing_or_unsupported_protocol_metadata(self) -> None:
        adapter = MCPAdapter(self.engine)
        missing = adapter.handle({"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {}})
        assert missing is not None
        self.assertEqual(-32602, missing["error"]["code"])

        unsupported = adapter.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "server/discover", "params": modern_meta("2099-01-01")}
        )
        assert unsupported is not None
        self.assertEqual(-32022, unsupported["error"]["code"])
        self.assertIn(MODERN_PROTOCOL, unsupported["error"]["data"]["supported"])

    def test_mcp_unknown_tool_is_invalid_params_not_tool_execution_error(self) -> None:
        adapter = MCPAdapter(self.engine)
        params = {"name": "does_not_exist", "arguments": {}, **modern_meta()}
        response = adapter.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
        assert response is not None
        self.assertEqual(-32602, response["error"]["code"])

    def test_mcp_legacy_initialize_supports_latest_and_older_legacy(self) -> None:
        for protocol in ("2025-11-25", "2025-06-18"):
            adapter = MCPAdapter(self.engine)
            response = adapter.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": protocol, "clientInfo": {"name": "test", "version": "1"}},
                }
            )
            assert response is not None
            self.assertEqual(protocol, response["result"]["protocolVersion"])
            listed = adapter.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            assert listed is not None
            self.assertIn("tools", listed["result"])

    def test_mcp_oversized_line_is_drained_before_next_valid_request(self) -> None:
        valid = json.dumps(
            {"jsonrpc": "2.0", "id": 77, "method": "ping", "params": modern_meta()}
        ).encode("utf-8")
        source = io.BytesIO((b"x" * (MAX_MESSAGE_BYTES + 32)) + b"\n" + valid + b"\n")
        output = io.StringIO()

        self.assertEqual(0, serve_stdio(MCPAdapter(self.engine), source, output))

        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(2, len(responses))
        self.assertEqual(-32600, responses[0]["error"]["code"])
        self.assertIn("size limit", responses[0]["error"]["data"])
        self.assertEqual(77, responses[1]["id"])
        self.assertEqual("complete", responses[1]["result"]["resultType"])

    def test_rest_requires_bearer_when_configured_and_runs_capability(self) -> None:
        server = CopilotHTTPServer(("127.0.0.1", 0), self.engine, "test-secret", request_timeout=2, max_concurrent_requests=4)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/v1/run"
            body = json.dumps({"capability": "route", "arguments": {"prompt": "review"}}).encode("utf-8")
            unauthorized = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(unauthorized, timeout=2)
            self.assertEqual(401, caught.exception.code)

            request = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-secret"},
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual("completed", payload["status"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_rest_returns_200_for_partial_orchestration(self) -> None:
        server = CopilotHTTPServer(("127.0.0.1", 0), self.engine, None, request_timeout=2, max_concurrent_requests=2)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/v1/run"
            body = json.dumps(
                {"capability": "copilot_run", "arguments": {"goal": "release this", "repo": str(self.repo)}}
            ).encode("utf-8")
            request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=2) as response:
                self.assertEqual(200, response.status)
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual("partial", payload["status"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
