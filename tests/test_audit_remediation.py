from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "ai-project-copilot" / "scripts"
PREVIEW = ROOT / "ai-project-copilot-multi-interface-upgrade"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class AuditRemediationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(SCRIPTS))
        cls.gateway = load("audit_gateway", SCRIPTS / "model_budget_gateway.py")
        payload_scripts = PREVIEW / "payload/skills/ai-project-copilot/scripts"
        sys.path.insert(0, str(payload_scripts))
        cls.preview_api = load("audit_preview_api", payload_scripts / "project_copilot_api.py")
        cls.preview_mcp = load("audit_preview_mcp", payload_scripts / "project_copilot_mcp.py")
        cls.installer = load("audit_preview_installer", PREVIEW / "apply_multi_interface_patch.py")

    def test_release_rebuild_uses_distinct_output(self) -> None:
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("--output dist/second.skill.zip", workflow)
        self.assertIn("cmp dist/first.skill.zip dist/second.skill.zip", workflow)

    def test_gateway_rejects_deep_provider_json_without_recursion_error(self) -> None:
        raw = b"[" * 10_000 + b"0" + b"]" * 10_000
        self.assertIn("unsafe", self.gateway._safe_provider_error_payload(raw))
        with self.assertRaises(self.gateway.ProviderError):
            next(self.gateway.iter_sse_events(io.BytesIO(b"data: " + raw + b"\n\n")))

    def test_preview_json_boundaries_reject_deep_messages(self) -> None:
        deep = "[" * 300 + "0" + "]" * 300
        self.assertTrue(self.preview_api._json_nesting_exceeds(deep))
        self.assertTrue(self.preview_mcp._json_nesting_exceeds(deep))

    def test_preview_installer_rejects_symlinked_target_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            repo = Path(temp)
            skill = repo / "skills/ai-project-copilot"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# AI Project Copilot 2.1\n\n## Capability lanes\n", encoding="utf-8")
            try:
                (skill / "scripts").symlink_to(Path(outside), target_is_directory=True)
            except OSError:
                self.skipTest("symlinks unavailable")
            with self.assertRaisesRegex(SystemExit, "symlink"):
                self.installer.validate_repo(repo)


if __name__ == "__main__":
    unittest.main()
