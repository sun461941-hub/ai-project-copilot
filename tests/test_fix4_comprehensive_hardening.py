from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "ai-project-copilot" / "scripts"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class EvidenceCacheHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module(
            "fix4_evidence_cache",
            SCRIPTS / "evidence_cache.py",
        )

    def test_deep_cache_is_rejected_before_json_parser(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "input.txt").write_text("ok", encoding="utf-8")
            cache = repo / ".aipc" / "cache" / "evidence.json"
            cache.parent.mkdir(parents=True)
            cache.write_text("[" * 300 + "0" + "]" * 300, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "nesting"):
                self.mod.check_entry(
                    repo,
                    Path(".aipc/cache/evidence.json"),
                    "unit",
                    "python test.py",
                    ["input.txt"],
                )

    def test_brackets_inside_json_string_are_not_counted(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "input.txt").write_text("ok", encoding="utf-8")
            self.mod.record_entry(
                repo,
                Path(".aipc/cache/evidence.json"),
                "unit",
                "python test.py",
                ["input.txt"],
                "pass",
                "[" * 1000,
            )
            result = self.mod.check_entry(
                repo,
                Path(".aipc/cache/evidence.json"),
                "unit",
                "python test.py",
                ["input.txt"],
            )
            self.assertTrue(result.reusable)

    def test_dangling_cache_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "input.txt").write_text("ok", encoding="utf-8")
            cache = repo / ".aipc" / "cache" / "evidence.json"
            cache.parent.mkdir(parents=True)
            try:
                cache.symlink_to(repo / "outside-does-not-exist.json")
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation unavailable")
            with self.assertRaisesRegex(ValueError, "symlink"):
                self.mod.record_entry(
                    repo,
                    Path(".aipc/cache/evidence.json"),
                    "unit",
                    "python test.py",
                    ["input.txt"],
                    "pass",
                    "ok",
                )


class MCPAuditHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module(
            "fix4_mcp_config_audit",
            SCRIPTS / "mcp_config_audit.py",
        )

    def test_deep_mcp_json_becomes_controlled_finding(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / ".mcp.json").write_text(
                "[" * 300 + "0" + "]" * 300,
                encoding="utf-8",
            )
            report = self.mod.scan(repo)
            self.assertIn("invalid-config", {item.code for item in report.findings})

    def test_string_brackets_are_not_depth(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / ".mcp.json").write_text(
                json.dumps({"description": "[" * 2000}),
                encoding="utf-8",
            )
            report = self.mod.scan(repo)
            self.assertNotIn("invalid-config", {item.code for item in report.findings})

    def test_default_config_symlink_is_reported_not_followed(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            outside = repo / "outside.json"
            outside.write_text('{"token": "literal-secret"}', encoding="utf-8")
            target = repo / ".mcp.json"
            try:
                target.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation unavailable")
            report = self.mod.scan(repo)
            self.assertEqual(report.files_scanned, 0)
            self.assertIn("config-symlink", {item.code for item in report.findings})


class SupplyChainHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module(
            "fix4_supply_chain_guard",
            SCRIPTS / "supply_chain_guard.py",
        )

    def test_manifest_dangling_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            skill = repo / "skills" / "ai-project-copilot"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("test", encoding="utf-8")
            manifest = repo / "dist" / "SHA256SUMS.txt"
            manifest.parent.mkdir()
            try:
                manifest.symlink_to(repo / "missing-output.txt")
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation unavailable")
            with self.assertRaisesRegex(ValueError, "symlink"):
                self.mod.scan(repo, Path("dist/SHA256SUMS.txt"))

    def test_dangling_skill_root_symlink_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            root = repo / "skills"
            root.mkdir()
            skill = root / "ai-project-copilot"
            try:
                skill.symlink_to(repo / "missing-skill-directory", target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation unavailable")
            report = self.mod.scan(repo)
            self.assertIn("skill-root-symlink", {item.code for item in report.findings})

    def test_workflow_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            workflows = repo / ".github" / "workflows"
            workflows.mkdir(parents=True)
            outside = repo / "outside.yml"
            outside.write_text("permissions: write-all\n", encoding="utf-8")
            link = workflows / "linked.yml"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation unavailable")
            report = self.mod.scan(repo)
            self.assertIn("workflow-symlink", {item.code for item in report.findings})
            self.assertEqual(report.workflow_files, 0)


class WorkflowHardeningTests(unittest.TestCase):
    def test_ci_has_timeouts_concurrency_and_diagnostics(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("concurrency:", text)
        self.assertGreaterEqual(text.count("timeout-minutes:"), 2)
        self.assertIn("Runtime diagnostics", text)
        self.assertIn("retention-days:", text)
        self.assertIn("gate:", text)
        self.assertIn("needs: [test, package]", text)
        self.assertIn('test "$TEST_RESULT" = "success"', text)
        self.assertIn('test "$PACKAGE_RESULT" = "success"', text)
        self.assertIn("Smoke-test packaged skill", text)
        self.assertIn("Verify preview patch applies", text)

    def test_release_is_manual_and_uses_release_environment(self):
        text = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("environment:", text)
        self.assertIn("name: release", text)
        self.assertNotIn('tags:\n      - "v*"', text)
        self.assertIn("contents: write", text)
        self.assertIn("contents: read", text)


class RetiredRepairKitTests(unittest.TestCase):
    def test_superseded_gateway_patcher_is_not_in_current_worktree(self):
        self.assertFalse((ROOT / "tools" / "apply_fix4_gateway_patch.py").exists())

    def test_codeowners_cover_critical_automation_paths(self):
        owners = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
        for path in (
            "/.github/workflows/",
            "/tools/package_skill.py",
            "/skills/ai-project-copilot/scripts/model_budget_autopilot.py",
            "/skills/ai-project-copilot/scripts/model_budget_gateway.py",
            "/ai-project-copilot-multi-interface-upgrade/apply_multi_interface_patch.py",
            "/ai-project-copilot-multi-interface-upgrade/payload/skills/ai-project-copilot/scripts/project_copilot_api.py",
            "/ai-project-copilot-multi-interface-upgrade/payload/skills/ai-project-copilot/scripts/project_copilot_mcp.py",
        ):
            self.assertIn(path, owners)


if __name__ == "__main__":
    unittest.main()
