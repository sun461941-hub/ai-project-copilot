from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "ai-project-copilot"
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_script(name: str):
    path = SKILL / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"v2_{name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class V2IntelligenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.router = load_script("workflow_router")
        cls.context = load_script("repo_context")
        cls.risk = load_script("change_risk")
        cls.release = load_script("release_intel")
        cls.guard = load_script("supply_chain_guard")
        cls.stack = load_script("skill_stack_audit")
        cls.convergence = load_script("review_convergence")
        cls.bootstrap = load_script("ai_ready_bootstrap")
        cls.mcp = load_script("mcp_config_audit")

    def test_router_composes_review_security_release(self) -> None:
        routes = self.router.route("Review this PR for security risk, then prepare the release")
        modes = [item.mode for item in routes]
        self.assertIn("review", modes)
        self.assertIn("secure", modes)
        self.assertIn("release", modes)

    def test_router_avoids_short_substring_false_positives(self) -> None:
        cases = {
            "Prepare a README for this project": "review",
            "Update the roadmap": "review",
            "Stage the files": "release",
            "Explain program architecture": "review",
        }
        for prompt, forbidden in cases.items():
            with self.subTest(prompt=prompt):
                modes = [item.mode for item in self.router.route(prompt)]
                self.assertNotIn(forbidden, modes)

    def test_repo_context_detects_high_signal_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "src" / "auth").mkdir(parents=True)
            (repo / "src" / "auth" / "login.py").write_text("def login(): pass\n", encoding="utf-8")
            (repo / "tests").mkdir()
            (repo / "tests" / "test_login.py").write_text("def test_login(): pass\n", encoding="utf-8")
            (repo / ".github" / "workflows").mkdir(parents=True)
            (repo / ".github" / "workflows" / "ci.yml").write_text("name: CI\npermissions:\n  contents: read\n", encoding="utf-8")
            (repo / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
            (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
            result = self.context.build_context(repo, "change auth login")
            self.assertTrue(any(item["name"] == "Python" for item in result.languages))
            self.assertIn("pyproject.toml", result.manifests)
            self.assertIn(".github/workflows/ci.yml", result.ci)
            self.assertTrue(any(item["path"] == "src/auth/login.py" for item in result.focus_files))

    def test_repo_context_maps_explicit_chinese_auth_wording_to_auth_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "src" / "auth").mkdir(parents=True)
            (repo / "src" / "auth" / "login.py").write_text("def login(): pass\n", encoding="utf-8")
            (repo / "tests").mkdir()
            (repo / "tests" / "test_login.py").write_text("def test_login(): pass\n", encoding="utf-8")
            (repo / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

            result = self.context.build_context(repo, "审查认证模块修改")
            self.assertTrue(any(item["path"] == "src/auth/login.py" for item in result.focus_files))

    def test_repo_context_ignores_generic_focus_stopwords(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "docs").mkdir()
            (repo / "docs" / "trust-evals-and-security.md").write_text("x\n", encoding="utf-8")
            (repo / "examples").mkdir()
            (repo / "examples" / "android.json").write_text("{}\n", encoding="utf-8")
            result = self.context.build_context(repo, "review authentication and release readiness")
            self.assertTrue(all("and" not in item["matched"] for item in result.focus_files))

    def test_change_risk_flags_security_schema_ci_and_missing_tests(self) -> None:
        changes = [
            self.risk.Change("src/auth/middleware.py", 120, 30),
            self.risk.Change("migrations/001.sql", 40, 0),
            self.risk.Change(".github/workflows/ci.yml", 10, 2),
        ]
        report = self.risk.analyze(changes)
        self.assertGreaterEqual(report.score, 70)
        self.assertIn("security/auth", report.categories)
        self.assertIn("data/schema/migration", report.categories)
        self.assertIn("ci/supply-chain", report.categories)
        self.assertTrue(report.human_gate_required)
        self.assertTrue(any("regression" in item for item in report.test_recommendations))

    def test_change_risk_avoids_security_substring_false_positives(self) -> None:
        for path in ("docs/authoring.md", "docs/secretary-guide.md"):
            with self.subTest(path=path):
                report = self.risk.analyze([self.risk.Change(path, 4, 1)])
                self.assertNotIn("security/auth", report.categories)

    def test_change_risk_parses_quoted_patch_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            patch = Path(temp) / "space.patch"
            patch.write_text(
                'diff --git "a/docs/foo bar.md" "b/docs/foo bar.md"\n'
                '--- "a/docs/foo bar.md"\n'
                '+++ "b/docs/foo bar.md"\n'
                '@@ -1 +1 @@\n-old\n+new\n',
                encoding="utf-8",
            )
            changes = self.risk.parse_patch(patch)
            self.assertEqual(1, len(changes))
            self.assertEqual("docs/foo bar.md", changes[0].path)
            self.assertEqual((1, 1), (changes[0].additions, changes[0].deletions))

    def test_docs_only_change_is_low_risk(self) -> None:
        report = self.risk.analyze([self.risk.Change("docs/guide.md", 20, 2)])
        self.assertLess(report.score, 25)
        self.assertEqual("low", report.level)

    def test_release_intel_semver(self) -> None:
        minor = self.release.classify(["feat: add context map", "fix: repair parser"], "1.1.0")
        self.assertEqual("minor", minor.bump)
        self.assertEqual("1.2.0", minor.suggested_version)
        breaking = self.release.classify(["feat!: replace public API\n\nBREAKING CHANGE: migration required"], "1.2.0")
        self.assertEqual("major", breaking.bump)
        self.assertEqual("2.0.0", breaking.suggested_version)
        self.assertTrue(breaking.migration_notes_required)
        self.assertTrue(breaking.release_ready)

    def test_release_intel_blocks_empty_release_and_supports_breaking_hyphen(self) -> None:
        empty = self.release.classify([], "1.2.3")
        self.assertFalse(empty.release_ready)
        self.assertEqual("none", empty.bump)
        breaking = self.release.classify(["feat: replace API\n\nBREAKING-CHANGE: migration guide included"], "1.2.3")
        self.assertEqual("major", breaking.bump)
        self.assertTrue(breaking.release_ready)

    def test_release_intel_blocks_unexplained_breaking_change(self) -> None:
        report = self.release.classify(["feat!: remove old client interface"], "2.0.0")
        self.assertFalse(report.release_ready)
        self.assertTrue(report.blockers)

    def test_supply_chain_guard_detects_workflow_risks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            workflows = repo / ".github" / "workflows"
            workflows.mkdir(parents=True)
            (workflows / "danger.yml").write_text(
                "name: danger\non:\n  pull_request_target:\npermissions: write-all\njobs:\n  x:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v7\n      - run: echo '${{ github.event.pull_request.title }}'\n",
                encoding="utf-8",
            )
            skill = repo / "skills" / "ai-project-copilot"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("demo\n", encoding="utf-8")
            report = self.guard.scan(repo)
            codes = {item.code for item in report.findings}
            self.assertIn("write-all", codes)
            self.assertIn("privileged-trigger", codes)
            self.assertIn("mutable-action-ref", codes)
            self.assertIn("event-interpolation", codes)
            self.assertIn("privileged-checkout", codes)
            self.assertLess(report.score, 60)


    def test_supply_chain_guard_ignores_commented_triggers_and_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            workflows = repo / ".github" / "workflows"
            workflows.mkdir(parents=True)
            (workflows / "ci.yml").write_text(
                "name: CI\n# pull_request_target:\npermissions:\n  contents: read\n"
                "jobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      # - run: echo '${{ github.event.pull_request.title }}'\n"
                "      - uses: actions/checkout@0123456789abcdef0123456789abcdef01234567\n",
                encoding="utf-8",
            )
            skill = repo / "skills" / "ai-project-copilot"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("demo\n", encoding="utf-8")
            report = self.guard.scan(repo)
            codes = {item.code for item in report.findings}
            self.assertNotIn("privileged-trigger", codes)
            self.assertNotIn("privileged-checkout", codes)
            self.assertNotIn("event-interpolation", codes)
            self.assertEqual(100, report.score)

    def test_mcp_config_audit_flags_literal_secret_shell_and_unpinned_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / ".mcp.json").write_text(
                json.dumps({
                    "mcpServers": {
                        "demo": {
                            "command": "bash",
                            "args": ["-c", "npx server-package"],
                            "apiToken": "real-looking-literal-token-value",
                            "url": "http://example.invalid/mcp"
                        },
                        "dynamic": {
                            "command": "npx",
                            "args": ["-y", "some-mcp-server"]
                        }
                    }
                }),
                encoding="utf-8",
            )
            report = self.mcp.scan(repo)
            codes = {item.code for item in report.findings}
            self.assertIn("hardcoded-secret", codes)
            self.assertIn("insecure-transport", codes)
            self.assertIn("shell-wrapper", codes)
            self.assertIn("unpinned-runner-package", codes)
            self.assertLess(report.score, 70)

    def test_mcp_audit_handles_windows_executable_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / ".mcp.json").write_text(
                json.dumps({"mcpServers": {"x": {"command": r"C:\\Tools\\npx.cmd", "args": ["-y", "server-pkg"]}}}),
                encoding="utf-8",
            )
            report = self.mcp.scan(repo)
            self.assertIn("unpinned-runner-package", {item.code for item in report.findings})

    def test_mcp_audit_detects_vendor_prefixed_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / ".mcp.json").write_text(
                json.dumps({
                    "mcpServers": {
                        "literal": {"OPENAI_API_KEY": "sk-live-1234567890"},
                        "referenced": {"ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}"},
                    }
                }),
                encoding="utf-8",
            )
            report = self.mcp.scan(repo)
            secret_locations = {
                item.location for item in report.findings if item.code == "hardcoded-secret"
            }
            self.assertIn("$.mcpServers.literal.OPENAI_API_KEY", secret_locations)
            self.assertNotIn("$.mcpServers.referenced.ANTHROPIC_API_KEY", secret_locations)

    def test_supply_chain_manifest_cannot_hash_itself(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            skill = repo / "skills" / "ai-project-copilot"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("demo\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                self.guard.scan(repo, Path("skills/ai-project-copilot/MANIFEST.sha256"))

    def test_skill_stack_audit_detects_duplicate_names_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for folder in ("one", "two"):
                skill = root / folder
                skill.mkdir()
                (skill / "SKILL.md").write_text(
                    "---\nname: shared-skill\ndescription: Use this skill for repository review security release workflows and maintainer review tasks. Do not use for one-off explanations.\n---\n# Shared\n",
                    encoding="utf-8",
                )
            report = self.stack.scan([root])
            self.assertEqual(2, report.skill_count)
            self.assertIn("shared-skill", report.duplicate_names)
            self.assertTrue(report.warnings)

    def test_ai_ready_bootstrap_refuses_symlink_escape(self) -> None:
        if not hasattr(Path, "symlink_to"):
            self.skipTest("symlinks unavailable")
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            repo = Path(temp)
            try:
                (repo / ".github").symlink_to(Path(outside), target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation unavailable")
            with self.assertRaises(ValueError):
                self.bootstrap.bootstrap(repo, ["copilot"], force=False)
            self.assertFalse((Path(outside) / "copilot-instructions.md").exists())

    def test_ai_ready_bootstrap_preserves_existing_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "src").mkdir()
            (repo / "src" / "app.py").write_text("print('x')\n", encoding="utf-8")
            (repo / "tests").mkdir()
            (repo / "tests" / "test_app.py").write_text("def test_x(): pass\n", encoding="utf-8")
            (repo / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
            (repo / "AGENTS.md").write_text("keep me\n", encoding="utf-8")
            report = self.bootstrap.bootstrap(repo, ["agents", "copilot"], force=False)
            self.assertEqual("keep me\n", (repo / "AGENTS.md").read_text(encoding="utf-8"))
            self.assertIn("AGENTS.md", report.skipped)
            self.assertIn(".github/copilot-instructions.md", report.created)
            generated = (repo / ".github" / "copilot-instructions.md").read_text(encoding="utf-8")
            self.assertIn("Generated as an evidence-based starting point", generated)

    def test_review_convergence_blocks_unfinished_agent_threads(self) -> None:
        threads = [
            self.convergence.ThreadState("t1", "fix", "open", "bug is real", "", ""),
            self.convergence.ThreadState("t2", "escalate", "open", "needs design decision", "", "maintainer"),
            self.convergence.ThreadState("t3", "decline", "resolved", "not supported by repo conventions", "", ""),
        ]
        report = self.convergence.analyze(threads)
        self.assertFalse(report.ready_for_rereview)
        self.assertIn("t1", report.agent_actionable_open)
        self.assertTrue(any("t2" in item for item in report.human_handoffs))
        threads[0] = self.convergence.ThreadState("t1", "fix", "resolved", "bug fixed", "abc123", "")
        report = self.convergence.analyze(threads)
        self.assertTrue(report.ready_for_rereview)

    def test_skill_eval_suite_is_substantive(self) -> None:
        data = json.loads((SKILL / "evals" / "evals.json").read_text(encoding="utf-8"))
        self.assertEqual("ai-project-copilot", data["skill_name"])
        self.assertEqual("2.1.1", data["version"])
        self.assertGreaterEqual(len(data["evals"]), 16)
        self.assertTrue(all(item.get("expectations") for item in data["evals"]))

    def test_v2_readme_keeps_public_evidence_contract(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8").casefold()
        for phrase in ("60-second", "## limitations", "model weights", "architecture", "evals", "security"):
            self.assertIn(phrase, text)

    def test_v2_skill_is_context_bounded(self) -> None:
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn('version: "2.1.1"', text)
        self.assertLessEqual(len(text.splitlines()), 500)
        for name in (
            "capability-router.md", "codebase-context.md", "pr-review-loop.md",
            "release-intelligence.md", "security-governance.md", "quality-orchestration.md",
            "ai-ready-and-skill-stack.md", "review-convergence.md",
        ):
            self.assertTrue((SKILL / "references" / name).exists())


if __name__ == "__main__":
    unittest.main()
