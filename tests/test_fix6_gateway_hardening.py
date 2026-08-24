from __future__ import annotations

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
        spec = importlib.util.spec_from_file_location("fix6_real_gateway", GATEWAY)
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
        self.assertTrue(self.mod.FIX6_GATEWAY_JSON_HARDENING)
        self.assertEqual(self.mod.MAX_JSON_NESTING, 256)

    def test_depth_scanner_ignores_brackets_and_escapes_inside_strings(self):
        text = json.dumps({"value": "[" * 2000 + "{" * 2000 + r'\\\"[]{}'})
        self.assertFalse(self.mod._json_nesting_exceeds(text))

    def test_provider_loader_rejects_deep_json_as_provider_error(self):
        payload = ("[" * 300 + "0" + "]" * 300).encode("utf-8")
        with self.assertRaisesRegex(self.mod.ProviderError, "nesting"):
            self.mod._load_provider_json(
                payload,
                "test provider payload",
                request_may_have_started=True,
            )

    def test_http_error_body_fails_closed(self):
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

    def test_quality_policy_deep_json_is_rejected_before_parser(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "quality.json"
            path.write_text("[" * 300 + "0" + "]" * 300, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "nesting"):
                self.mod.load_quality_policy(path)

    def test_normal_provider_json_still_parses(self):
        value = self.mod._load_provider_json(
            b'{"input_tokens": 7}',
            "token count response",
            request_may_have_started=False,
        )
        self.assertEqual(value, {"input_tokens": 7})

    def test_canonical_json_converts_recursion_to_value_error(self):
        value = []
        cursor = value
        for _ in range(1500):
            child = []
            cursor.append(child)
            cursor = child
        with self.assertRaisesRegex(ValueError, "canonical JSON"):
            self.mod._canonical_json_bytes(value)

    def test_canonical_json_rejects_self_referential_list_before_serialization(self):
        value: list[object] = []
        value.append(value)
        with self.assertRaisesRegex(ValueError, "cyclic container"):
            self.mod._canonical_json_bytes(value)

    def test_canonical_json_rejects_self_referential_mapping_before_serialization(self):
        value: dict[str, object] = {}
        value["self"] = value
        with self.assertRaisesRegex(ValueError, "cyclic container"):
            self.mod._canonical_json_bytes(value)


if __name__ == "__main__":
    unittest.main()
