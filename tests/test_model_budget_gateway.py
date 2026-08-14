from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "ai-project-copilot" / "scripts"


def load_script(name: str):
    path = SCRIPTS / f"{name}.py"
    scripts_text = str(SCRIPTS)
    if scripts_text not in sys.path:
        sys.path.insert(0, scripts_text)
    spec = importlib.util.spec_from_file_location(f"aipc_{name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeClient:
    def __init__(self, gateway, responses, counts=None):
        self.gateway = gateway
        self.responses = list(responses)
        self.counts = counts or {"quality": 10, "balanced": 9, "economy": 8}
        self.count_payloads = []
        self.provider_payloads = []

    def count_input_tokens(self, payload):
        self.count_payloads.append(dict(payload))
        return self.gateway.TokenCount(self.counts[str(payload["model"])], 1.0)

    def create_stream(self, payload, *, on_text_delta=None):
        self.provider_payloads.append(dict(payload))
        response = self.responses.pop(0)
        text = self.gateway.extract_output_text(response)
        if text and on_text_delta is not None:
            on_text_delta(text)
        return self.gateway.ProviderCall(
            dict(response), text, str(response["model"]), 5.0, 20.0, 1, None, None, None
        )


class ModelBudgetGatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.budget = load_script("model_budget_autopilot")
        cls.gateway = load_script("model_budget_gateway")

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "state.sqlite3"
        self.user = "trusted-user"
        self.cards = [
            self.budget.PriceCard("quality", 30, 3, 35, 100),
            self.budget.PriceCard("balanced", 10, 1, 12, 40),
            self.budget.PriceCard("economy", 2, 0, 3, 10),
        ]
        self.budget.configure_user(
            self.db,
            self.user,
            2_500,
            40,
            30,
            10,
            ["quality", "balanced", "economy"],
            self.cards,
        )

    def request(self, model="quality"):
        return {
            "model": model,
            "instructions": "Be concise.",
            "input": [{"role": "user", "content": "Return OK"}],
            "reasoning": {"effort": "low"},
            "text": {"verbosity": "low"},
            "max_output_tokens": 10,
        }

    def response(
        self,
        response_id,
        model,
        *,
        status="completed",
        output="OK",
        input_tokens=10,
        output_tokens=1,
    ):
        return {
            "id": response_id,
            "model": model,
            "service_tier": "default",
            "status": status,
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": output}],
                }
            ],
            "usage": {
                "input_tokens": input_tokens,
                "input_tokens_details": {
                    "cached_tokens": 2,
                    "cache_write_tokens": 1,
                },
                "output_tokens": output_tokens,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": input_tokens + output_tokens,
            },
        }

    def execute(self, client, **kwargs):
        return self.gateway.execute_budgeted_request(
            db=self.db,
            user_key=self.user,
            request_id=kwargs.pop("request_id", "attempt-one"),
            logical_request_id=kwargs.pop("logical_request_id", "task-one"),
            request_payload=kwargs.pop("request_payload", self.request()),
            task_class=kwargs.pop("task_class", "routine"),
            client=client,
            quality_policy=kwargs.pop("quality_policy", None),
            on_text_delta=kwargs.pop("on_text_delta", None),
            **kwargs,
        )

    def test_count_payload_preserves_rendering_fields_and_drops_output_fields(self):
        request = self.request()
        request.update(
            {
                "conversation": "conv_123",
                "previous_response_id": "resp_old",
                "personality": "pragmatic",
                "tools": [{"type": "function", "name": "x", "parameters": {}}],
                "tool_choice": "auto",
                "stream": True,
                "metadata": {"private": "not-rendered"},
            }
        )
        count = self.gateway.build_count_payload(request, "balanced")
        self.assertEqual("balanced", count["model"])
        for key in (
            "previous_response_id",
            "personality",
            "instructions",
            "input",
            "tools",
            "tool_choice",
            "reasoning",
            "text",
        ):
            self.assertIn(key, count)
        self.assertNotIn("max_output_tokens", count)
        self.assertNotIn("stream", count)
        self.assertNotIn("metadata", count)
        self.assertNotIn("conversation", count)

    def test_downgrade_executes_selected_payload_and_settles_real_usage(self):
        client = FakeClient(
            self.gateway,
            [self.response("resp_1", "balanced-2026-08-01")],
        )
        deltas = []
        result = self.execute(client, on_text_delta=deltas.append)
        self.assertEqual("success", result.final_status)
        self.assertEqual("balanced", result.final_model)
        self.assertEqual("balanced-2026-08-01", result.served_model)
        self.assertEqual(["OK"], deltas)
        self.assertEqual(1, len(client.provider_payloads))
        sent = client.provider_payloads[0]
        self.assertEqual("balanced", sent["model"])
        self.assertIs(True, sent["stream"])
        self.assertIs(False, sent["store"])
        route = result.attempts[0]["route"]
        self.assertEqual("downgrade", route["action"])
        self.assertEqual(
            result.attempts[0]["provider_payload_sha256"],
            route["request_payload_sha256"],
        )
        self.assertEqual(11, result.total_tokens)
        self.assertEqual(2, result.total_cached_tokens)
        self.assertEqual(1, result.total_cache_write_tokens)

    def test_incomplete_fallback_gets_exactly_one_upgrade(self):
        client = FakeClient(
            self.gateway,
            [
                self.response("resp_1", "balanced", status="incomplete", output="partial"),
                self.response("resp_2", "quality-2026-08-01", output="final"),
            ],
        )
        result = self.execute(client)
        self.assertEqual("success", result.final_status)
        self.assertEqual("quality", result.final_model)
        self.assertEqual(2, len(result.attempts))
        self.assertEqual(["balanced", "quality"], [item["model"] for item in client.provider_payloads])
        self.assertEqual("quality-upgrade", result.attempts[1]["route"]["action"])
        self.assertEqual("final", result.output_text)

    def test_quality_command_failure_gets_one_upgrade_and_sees_no_api_key(self):
        checker = self.root / "quality.py"
        checker.write_text(
            "import os, sys\n"
            "assert 'OPENAI_API_KEY' not in os.environ\n"
            "raise SystemExit(1 if sys.argv[1] == '1' else 0)\n",
            encoding="utf-8",
        )
        policy = self.gateway.QualityPolicy(("{python}", str(checker), "{attempt}"), 10)
        client = FakeClient(
            self.gateway,
            [
                self.response("resp_1", "balanced", output="weak"),
                self.response("resp_2", "quality", output="strong"),
            ],
        )
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "must-not-leak"}):
            result = self.execute(client, quality_policy=policy)
        self.assertEqual("success", result.final_status)
        self.assertEqual(2, len(result.attempts))
        self.assertEqual("fail", result.attempts[0]["quality_evidence"]["gate"])
        self.assertEqual("pass", result.attempts[1]["quality_evidence"]["gate"])

    def test_quality_evaluator_error_does_not_spend_on_upgrade(self):
        checker = self.root / "error.py"
        checker.write_text("raise SystemExit(7)\n", encoding="utf-8")
        policy = self.gateway.QualityPolicy((sys.executable, str(checker)), 10)
        client = FakeClient(
            self.gateway,
            [self.response("resp_1", "balanced", output="ungraded")],
        )
        result = self.execute(client, quality_policy=policy)
        self.assertEqual("quality-evaluator-error", result.final_status)
        self.assertEqual(1, len(client.provider_payloads))
        self.assertEqual("error", result.attempts[0]["quality_evidence"]["gate"])

    def test_quality_timeout_does_not_trigger_an_upgrade(self):
        checker = self.root / "slow.py"
        checker.write_text("import time\ntime.sleep(2)\n", encoding="utf-8")
        policy = self.gateway.QualityPolicy((sys.executable, str(checker)), 1)
        client = FakeClient(
            self.gateway,
            [self.response("resp_slow", "balanced", output="ungraded")],
        )
        result = self.execute(client, quality_policy=policy)
        evidence = result.attempts[0]["quality_evidence"]
        self.assertEqual("quality-evaluator-error", result.final_status)
        self.assertEqual("error", evidence["gate"])
        self.assertTrue(evidence["timed_out"])
        self.assertEqual(1, len(client.provider_payloads))

    def test_block_never_calls_generation(self):
        other = self.root / "tiny.sqlite3"
        self.budget.configure_user(
            other,
            self.user,
            1,
            40,
            30,
            10,
            ["quality", "balanced", "economy"],
            self.cards,
        )
        client = FakeClient(self.gateway, [])
        result = self.gateway.execute_budgeted_request(
            db=other,
            user_key=self.user,
            request_id="blocked",
            logical_request_id="blocked",
            request_payload=self.request(),
            task_class="routine",
            client=client,
        )
        self.assertEqual("blocked", result.final_status)
        self.assertEqual([], client.provider_payloads)

    def test_route_replay_is_not_executable_and_does_not_call_again(self):
        client = FakeClient(
            self.gateway,
            [self.response("resp_1", "balanced")],
        )
        self.execute(client)
        with self.assertRaisesRegex(self.gateway.GatewayError, "not executable"):
            self.execute(client)
        self.assertEqual(1, len(client.provider_payloads))

    def test_unrecognized_served_model_fails_closed_and_leaves_reservation(self):
        client = FakeClient(
            self.gateway,
            [self.response("resp_1", "different-provider-model")],
        )
        with self.assertRaisesRegex(self.gateway.ProviderError, "not authorized"):
            self.execute(client)
        status = self.budget.get_status(self.db, self.user)
        self.assertGreater(status.reserved_nano_usd, 0)
        self.assertEqual(0, status.committed_nano_usd)

    def test_served_model_requires_exact_or_real_date_snapshot_unless_mapped(self):
        matches = self.gateway._served_model_matches
        self.assertTrue(matches("quality", "quality", None))
        self.assertTrue(matches("quality", "quality-2026-08-01", None))
        self.assertFalse(matches("quality", "quality-pro", None))
        self.assertFalse(matches("quality", "quality-2026-13-40", None))
        self.assertFalse(matches("gpt-4o", "gpt-4o-mini", None))
        self.assertFalse(matches("quality-2026-08-01", "quality-2026-08-01-2026-08-02", None))
        self.assertTrue(
            matches("quality", "quality-provider-alias", {"quality": ["quality-provider-alias"]})
        )

    def test_callback_failure_is_captured_by_stream_client(self):
        events = [
            {"type": "response.created", "sequence_number": 0, "response": {"id": "resp_1"}},
            {"type": "response.output_text.delta", "sequence_number": 1, "delta": "OK"},
            {
                "type": "response.completed",
                "sequence_number": 2,
                "response": self.response("resp_1", "quality"),
            },
        ]
        raw = b"".join(
            b"data: " + json.dumps(event).encode("utf-8") + b"\n\n" for event in events
        )

        def opener(request, timeout):
            return contextlib.closing(io.BytesIO(raw))

        client = self.gateway.OpenAIResponsesClient(
            "test-key",
            api_base_url="https://example.test/v1",
            opener=opener,
        )

        def broken(delta):
            raise RuntimeError("display broke")

        result = client.create_stream(
            self.gateway.build_provider_payload(self.request(), "quality"),
            on_text_delta=broken,
        )
        self.assertEqual("RuntimeError", result.display_error)
        self.assertEqual("completed", result.response["status"])

    def test_long_provider_call_renews_the_active_lease(self):
        with mock.patch.object(self.gateway.budget, "renew_reservation") as renew:
            with self.gateway._LeaseRenewer(self.db, self.user, "attempt", 1):
                time.sleep(0.7)
        self.assertGreaterEqual(renew.call_count, 1)

    def test_request_contract_is_exact_countable_text_only_and_private_by_default(self):
        template = self.gateway._request_template(self.request())
        self.assertIs(False, template["store"])
        self.assertEqual("default", template["service_tier"])
        with self.assertRaisesRegex(ValueError, "prompt templates are not supported"):
            self.gateway._request_template(
                {"model": "quality", "prompt": {"id": "pmpt_1"}, "max_output_tokens": 1}
            )
        request_with_tools = self.request()
        request_with_tools["tools"] = [{"type": "function", "name": "x"}]
        with self.assertRaisesRegex(ValueError, "tools are not supported"):
            self.gateway._request_template(request_with_tools)
        request_with_conversation = self.request()
        request_with_conversation["conversation"] = "conv_mutable"
        with self.assertRaisesRegex(ValueError, "conversation is not supported"):
            self.gateway._request_template(request_with_conversation)
        request_with_previous_response = self.request()
        request_with_previous_response["previous_response_id"] = "resp_immutable"
        previous_template = self.gateway._request_template(request_with_previous_response)
        self.assertEqual("resp_immutable", previous_template["previous_response_id"])
        request_with_unknown_output = self.request()
        request_with_unknown_output["modalities"] = ["audio"]
        with self.assertRaisesRegex(ValueError, "outside the reviewed v2.1"):
            self.gateway._request_template(request_with_unknown_output)
        for tier in ("auto", "fast", "priority", "flex", None):
            request_with_tier = self.request()
            request_with_tier["service_tier"] = tier
            with self.subTest(service_tier=tier), self.assertRaisesRegex(
                ValueError, "service_tier must be 'default'"
            ):
                self.gateway._request_template(request_with_tier)
        with self.assertRaisesRegex(ValueError, "control characters"):
            self.gateway.OpenAIResponsesClient("bad\nkey")

    def test_experiment_fingerprints_bind_requested_model_task_and_protection_policy(self):
        quality = self.gateway._request_template(self.request("quality"))
        economy = self.gateway._request_template(self.request("economy"))
        baseline = self.gateway._request_template_sha256(quality, "routine")
        self.assertNotEqual(
            baseline,
            self.gateway._request_template_sha256(economy, "routine"),
        )
        self.assertNotEqual(
            baseline,
            self.gateway._request_template_sha256(quality, "security"),
        )

        config = self.budget.get_config(self.db, self.user)
        pricing = self.gateway._pricing_policy_sha256(config, 0, {})
        changed = replace(config, protected_tasks=["release"])
        self.assertNotEqual(
            pricing,
            self.gateway._pricing_policy_sha256(changed, 0, {}),
        )
        self.assertNotEqual(
            pricing,
            self.gateway._pricing_policy_sha256(
                config, 0, {"quality": ("quality-preview",)}
            ),
        )

    def test_non_text_inputs_fail_before_count_or_generation(self):
        invalid_inputs = [
            {"role": "user", "content": "mapping-not-array"},
            [{"type": "input_image", "image_url": "https://example.test/a.png"}],
            [{
                "role": "user",
                "content": [{"type": "input_image", "image_url": "https://example.test/a.png"}],
            }],
            [{
                "role": "user",
                "content": [{"type": "input_file", "file_id": "file_1"}],
            }],
            [{
                "role": "user",
                "content": [{"type": "input_audio", "audio": "AAAA"}],
            }],
            [{
                "role": "user",
                "content": "hidden image",
                "image_url": "https://example.test/hidden.png",
            }],
            [{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "hidden file", "file_id": "file_1"}
                ],
            }],
            [{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "hidden audio", "audio": {}}
                ],
            }],
        ]
        for index, value in enumerate(invalid_inputs):
            client = FakeClient(self.gateway, [])
            request = self.request()
            request["input"] = value
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, "text-only|text or"):
                self.execute(
                    client,
                    request_id=f"non-text-{index}",
                    logical_request_id=f"non-text-{index}",
                    request_payload=request,
                )
            self.assertEqual([], client.count_payloads)
            self.assertEqual([], client.provider_payloads)

        valid = self.request()
        valid["input"] = [
            {"role": "user", "content": [{"type": "input_text", "text": "question"}]},
            {"role": "assistant", "content": [{"type": "output_text", "text": "answer"}]},
            {"role": "assistant", "content": [{"type": "refusal", "refusal": "no"}]},
        ]
        self.assertEqual(valid["input"], self.gateway._request_template(valid)["input"])

    def test_non_default_or_unconfirmed_service_tier_fails_closed(self):
        client = FakeClient(self.gateway, [])
        request = self.request()
        request["service_tier"] = "fast"
        with self.assertRaisesRegex(ValueError, "service_tier must be 'default'"):
            self.execute(
                client,
                request_id="fast-tier",
                logical_request_id="fast-tier",
                request_payload=request,
            )
        self.assertEqual([], client.count_payloads)
        self.assertEqual([], client.provider_payloads)

        for tier in (None, "fast"):
            response = self.response(f"resp-tier-{tier}", "balanced")
            if tier is None:
                response.pop("service_tier")
            else:
                response["service_tier"] = tier
            tier_client = FakeClient(self.gateway, [response])
            with self.subTest(provider_tier=tier), self.assertRaisesRegex(
                self.gateway.ProviderError, "required default service tier"
            ):
                self.execute(
                    tier_client,
                    request_id=f"provider-tier-{tier}",
                    logical_request_id=f"provider-tier-{tier}",
                )
            status = self.budget.get_status(self.db, self.user)
            self.assertGreater(status.reserved_nano_usd, 0)

    def test_quality_policy_hash_includes_normalized_working_directory(self):
        first = self.root / "first"
        second = self.root / "second"
        first.mkdir()
        second.mkdir()
        argv = (sys.executable, "checker.py")
        first_policy = self.gateway.QualityPolicy(argv, 10, first)
        equivalent_first = self.gateway.QualityPolicy(argv, 10, first / ".." / "first")
        second_policy = self.gateway.QualityPolicy(argv, 10, second)
        self.assertEqual(
            self.gateway.quality_policy_sha256(first_policy),
            self.gateway.quality_policy_sha256(equivalent_first),
        )
        self.assertNotEqual(
            self.gateway.quality_policy_sha256(first_policy),
            self.gateway.quality_policy_sha256(second_policy),
        )

    def test_projected_extra_cost_is_preserved_in_actual_settlement(self):
        client = FakeClient(self.gateway, [self.response("resp_extra", "balanced")])
        result = self.execute(client, projected_extra_cost_nano_usd=50)
        usage = result.attempts[0]["usage"]
        self.assertEqual(174, usage["actual_cost_nano_usd"])
        self.assertEqual(174, result.total_cost_nano_usd)

    def test_quality_output_is_bounded_before_it_enters_the_trace(self):
        checker = self.root / "noisy.py"
        checker.write_text("print('x' * 100000)\n", encoding="utf-8")
        policy = self.gateway.QualityPolicy((sys.executable, str(checker)), 10)
        client = FakeClient(self.gateway, [self.response("resp_noisy", "balanced")])
        result = self.execute(client, quality_policy=policy)
        output = result.attempts[0]["quality_evidence"]["stdout"]
        self.assertLess(len(output), 8_100)
        self.assertIn("[clipped]", output)

    def test_concurrent_same_gateway_request_executes_provider_once(self):
        client = FakeClient(self.gateway, [self.response("resp_once", "balanced")])

        def invoke():
            try:
                return self.execute(client).final_status
            except self.gateway.GatewayError:
                return "non-executable-replay"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: invoke(), range(2)))
        self.assertEqual(["non-executable-replay", "success"], sorted(outcomes))
        self.assertEqual(1, len(client.provider_payloads))

    def test_configuration_change_after_counting_fails_before_provider_execution(self):
        client = FakeClient(self.gateway, [])

        def count_then_reconfigure(*args, **kwargs):
            self.budget.configure_user(
                self.db,
                self.user,
                2_500,
                40,
                30,
                10,
                ["quality", "balanced", "economy"],
                self.cards,
            )
            return {"quality": 10, "balanced": 9, "economy": 8}, 1.0

        with mock.patch.object(
            self.gateway, "_count_ladder", side_effect=count_then_reconfigure
        ), self.assertRaisesRegex(ValueError, "changed after request projection"):
            self.execute(client, request_id="config-race", logical_request_id="config-race")
        self.assertEqual([], client.provider_payloads)
        self.assertEqual(0, self.budget.get_status(self.db, self.user).reserved_nano_usd)

    def test_http_headers_and_provider_timing_are_recorded_without_redirects(self):
        response_payload = self.response("resp_headers", "quality")
        events = [
            {"type": "response.created", "sequence_number": 0, "response": {"id": "resp_headers"}},
            {"type": "response.completed", "sequence_number": 1, "response": response_payload},
        ]
        raw = b"".join(
            b"data: " + json.dumps(event).encode("utf-8") + b"\n\n" for event in events
        )
        seen = []

        class Response(io.BytesIO):
            headers = {"x-request-id": "req_http_1", "openai-processing-ms": "12.5"}

        def opener(request, timeout):
            seen.append(request)
            return Response(raw)

        client = self.gateway.OpenAIResponsesClient(
            "test-key",
            api_base_url="https://example.test/v1",
            opener=opener,
            project="proj_1",
            organization="org_1",
        )
        result = client.create_stream(
            self.gateway.build_provider_payload(self.request(), "quality")
        )
        headers = dict(seen[0].header_items())
        self.assertEqual("proj_1", headers["Openai-project"])
        self.assertEqual("org_1", headers["Openai-organization"])
        self.assertIn("X-client-request-id", headers)
        self.assertEqual("req_http_1", result.provider_http_request_id)
        self.assertEqual(12.5, result.openai_processing_ms)

    def test_uncertain_http_timeout_keeps_reservation_but_validation_rejection_releases(self):
        class HttpFailingClient(FakeClient):
            def __init__(self, gateway, status):
                super().__init__(gateway, [])
                self.status = status

            def create_stream(self, payload, *, on_text_delta=None):
                error_body = io.BytesIO(b'{"error":{"message":"rejected"}}')

                def opener(request, timeout):
                    raise self.gateway.urllib.error.HTTPError(
                        request.full_url,
                        self.status,
                        "provider error",
                        {},
                        error_body,
                    )

                transport = self.gateway.OpenAIResponsesClient(
                    "test-key",
                    api_base_url="https://example.test/v1",
                    opener=opener,
                )
                return transport.create_stream(payload, on_text_delta=on_text_delta)

        for status, should_remain_reserved in ((400, False), (408, True), (409, True)):
            request_id = f"http-{status}"
            client = HttpFailingClient(self.gateway, status)
            reserved_before = self.budget.get_status(
                self.db, self.user
            ).reserved_nano_usd
            with self.subTest(status=status), self.assertRaisesRegex(
                self.gateway.ProviderError, f"HTTP {status}"
            ):
                self.execute(
                    client,
                    request_id=request_id,
                    logical_request_id=request_id,
                )
            reserved_after = self.budget.get_status(
                self.db, self.user
            ).reserved_nano_usd
            if should_remain_reserved:
                self.assertGreater(reserved_after, reserved_before)
            else:
                self.assertEqual(reserved_before, reserved_after)

    def test_stream_fails_closed_on_missing_terminal_id_mismatch_or_usage(self):
        good = self.response("resp_1", "quality")
        cases = [
            ([{"type": "response.created", "sequence_number": 0, "response": {"id": "resp_1"}}], "without a terminal"),
            ([
                {"type": "response.created", "sequence_number": 0, "response": {"id": "resp_other"}},
                {"type": "response.completed", "sequence_number": 1, "response": good},
            ], "id changed"),
            ([{
                "type": "response.completed",
                "sequence_number": 0,
                "response": {key: value for key, value in good.items() if key != "usage"},
            }], "omitted usage"),
        ]
        for events, message in cases:
            raw = b"".join(
                b"data: " + json.dumps(event).encode("utf-8") + b"\n\n" for event in events
            )

            def opener(request, timeout, data=raw):
                return contextlib.closing(io.BytesIO(data))

            client = self.gateway.OpenAIResponsesClient(
                "test-key", api_base_url="https://example.test/v1", opener=opener
            )
            with self.subTest(message=message), self.assertRaisesRegex(
                self.gateway.ProviderError, message
            ):
                client.create_stream(
                    self.gateway.build_provider_payload(self.request(), "quality")
                )

    def test_sse_rejects_duplicate_terminal_and_non_monotonic_sequence(self):
        response = self.response("resp_1", "quality")
        duplicate = [
            {"type": "response.completed", "sequence_number": 1, "response": response},
            {"type": "response.completed", "sequence_number": 2, "response": response},
        ]
        non_monotonic = [
            {"type": "response.created", "sequence_number": 2, "response": {"id": "resp_1"}},
            {"type": "response.completed", "sequence_number": 2, "response": response},
        ]
        for events, message in ((duplicate, "more than one terminal"), (non_monotonic, "strictly increasing")):
            raw = b"".join(
                b"data: " + json.dumps(event).encode() + b"\n\n" for event in events
            )

            def opener(request, timeout, data=raw):
                return contextlib.closing(io.BytesIO(data))

            client = self.gateway.OpenAIResponsesClient(
                "test-key", api_base_url="https://example.test/v1", opener=opener
            )
            with self.subTest(message=message), self.assertRaisesRegex(
                self.gateway.ProviderError, message
            ):
                client.create_stream(self.gateway.build_provider_payload(self.request(), "quality"))

    def test_sse_supports_comments_crlf_multiline_data_and_unknown_events(self):
        response = self.response("resp_crlf", "quality")
        terminal_json = json.dumps(
            {"type": "response.completed", "sequence_number": 2, "response": response}
        )
        split_at = terminal_json.index('"response"')
        raw = (
            b": keepalive\r\n"
            b"event: response.created\r\n"
            b"data: {\"type\":\"response.created\",\"sequence_number\":0,"
            b"\"response\":{\"id\":\"resp_crlf\"}}\r\n\r\n"
            b"data: {\"type\":\"future.event\",\"sequence_number\":1}\r\n\r\n"
            + b"data: " + terminal_json[:split_at].encode("utf-8") + b"\r\n"
            + b"data: " + terminal_json[split_at:].encode("utf-8") + b"\r\n\r\n"
        )

        def opener(request, timeout):
            return contextlib.closing(io.BytesIO(raw))

        client = self.gateway.OpenAIResponsesClient(
            "test-key", api_base_url="https://example.test/v1", opener=opener
        )
        result = client.create_stream(
            self.gateway.build_provider_payload(self.request(), "quality")
        )
        self.assertEqual("resp_crlf", result.response["id"])

    def test_stream_rejects_terminal_mismatch_post_terminal_and_duplicate_created(self):
        completed = self.response("resp_1", "quality")
        incomplete = self.response("resp_1", "quality", status="incomplete")
        cases = [
            ([
                {"type": "response.completed", "sequence_number": 0, "response": incomplete},
            ], "event type does not match"),
            ([
                {"type": "response.completed", "sequence_number": 0, "response": completed},
                {"type": "future.event", "sequence_number": 1},
            ], "event after the terminal"),
            ([
                {"type": "response.created", "sequence_number": 0, "response": {"id": "resp_1"}},
                {"type": "response.created", "sequence_number": 1, "response": {"id": "resp_1"}},
                {"type": "response.completed", "sequence_number": 2, "response": completed},
            ], "created more than once"),
        ]
        for events, message in cases:
            raw = b"".join(
                b"data: " + json.dumps(event).encode("utf-8") + b"\n\n" for event in events
            )

            def opener(request, timeout, data=raw):
                return contextlib.closing(io.BytesIO(data))

            client = self.gateway.OpenAIResponsesClient(
                "test-key", api_base_url="https://example.test/v1", opener=opener
            )
            with self.subTest(message=message), self.assertRaisesRegex(
                self.gateway.ProviderError, message
            ):
                client.create_stream(
                    self.gateway.build_provider_payload(self.request(), "quality")
                )

    def test_exact_payload_builder_hashes_selected_model_inside_transaction(self):
        request = self.request()

        def builder(model):
            return self.gateway._canonical_json_bytes(
                self.gateway.build_provider_payload(request, model)
            )

        decision = self.budget.route_request(
            self.db,
            self.user,
            "builder-route",
            "quality",
            10,
            10,
            request_payload_builder=builder,
        )
        self.assertEqual("balanced", decision.selected_model)
        self.assertEqual(hashlib.sha256(builder("balanced")).hexdigest(), decision.request_payload_sha256)
        replay = self.budget.route_request(
            self.db,
            self.user,
            "builder-route",
            "quality",
            10,
            10,
            request_payload_builder=builder,
        )
        self.assertFalse(replay.execution_authorized)
        self.assertIsNone(replay.selected_model)

    def test_cli_missing_key_is_clean_error(self):
        request = self.root / "request.json"
        request.write_text(json.dumps(self.request()), encoding="utf-8")
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "sys.stderr", new_callable=io.StringIO
        ) as stderr:
            code = self.gateway.main(
                [
                    "--db",
                    str(self.db),
                    "--user",
                    self.user,
                    "--request-id",
                    "cli",
                    "--request",
                    str(request),
                ]
            )
        self.assertEqual(2, code)
        self.assertIn("OPENAI_API_KEY is required", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
