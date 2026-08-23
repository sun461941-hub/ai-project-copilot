from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "ai-project-copilot" / "scripts"


def load_script(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"aipc_{name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ContextAcceleratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.governor = load_script("token_governor")
        cls.accel = load_script("context_accelerator")
        cls.compactor = load_script("tool_output_compactor")
        cls.cache = load_script("evidence_cache")

    def test_fast_mode_for_docs_only_change(self) -> None:
        plan = self.governor.plan_task("Fix a typo in the README", ["README.md"])
        self.assertEqual("FAST", plan.mode)
        self.assertEqual("single agent", plan.multi_agent)
        self.assertLessEqual(plan.max_focus_files, 8)

    def test_balanced_mode_for_normal_feature(self) -> None:
        plan = self.governor.plan_task("Add a CLI feature and tests", ["src/cli.py", "tests/test_cli.py"])
        self.assertEqual("BALANCED", plan.mode)
        self.assertEqual("medium", plan.recommended_reasoning_effort)

    def test_deep_mode_for_security_or_release(self) -> None:
        security = self.governor.plan_task("Audit authentication permissions", ["src/auth/session.py"])
        release = self.governor.plan_task("Prepare a release", ["CHANGELOG.md"])
        self.assertEqual("DEEP", security.mode)
        self.assertEqual("DEEP", release.mode)

    def test_deep_mode_for_high_risk_paths_even_with_benign_prompt(self) -> None:
        plan = self.governor.plan_task("Update this file", [".github/workflows/release.yml"])
        self.assertEqual("DEEP", plan.mode)

    def test_governor_avoids_substring_false_positives(self) -> None:
        plan = self.governor.plan_task("Update a roadmap preview report", ["docs/roadmap.md"])
        self.assertEqual("FAST", plan.mode)
        self.assertFalse(any("review" in reason for reason in plan.reasons))

    def _repo(self, root: Path) -> None:
        (root / "src" / "auth").mkdir(parents=True)
        (root / "tests").mkdir()
        (root / "docs").mkdir()
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / "AGENTS.md").write_text("root rules\n", encoding="utf-8")
        (root / "src" / "AGENTS.md").write_text("src rules\n", encoding="utf-8")
        (root / "src" / "auth" / "token.py").write_text("def token(): return 'x'\n", encoding="utf-8")
        (root / "tests" / "test_token.py").write_text("def test_token(): assert True\n", encoding="utf-8")
        (root / "README.md").write_text("# Demo\n", encoding="utf-8")
        (root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
        (root / ".github" / "workflows" / "ci.yml").write_text("name: CI\n", encoding="utf-8")
        for i in range(120):
            (root / "docs" / f"note-{i:03}.md").write_text("notes\n", encoding="utf-8")

    def test_accelerator_selects_changed_file_instructions_and_related_test(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            self._repo(repo)
            packet = self.accel.compile_context(repo, "fix token bug", ["src/auth/token.py"])
            self.assertIn("src/auth/token.py", packet.files_to_read)
            self.assertIn("AGENTS.md", packet.governing_instructions)
            self.assertIn("src/AGENTS.md", packet.governing_instructions)
            self.assertIn("tests/test_token.py", packet.tests_to_consider)
            self.assertLess(len(packet.files_to_read), packet.scan["files_scanned"])

    def test_accelerator_fast_path_stays_small(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            self._repo(repo)
            packet = self.accel.compile_context(repo, "Fix a README typo", ["README.md"])
            self.assertEqual("FAST", packet.mode)
            self.assertLessEqual(len(packet.files_to_read), 8)
            self.assertEqual("sparse-fast", packet.scan["scan_mode"])
            self.assertEqual(0, packet.scan["files_scanned"])

    def test_accelerator_deep_path_is_still_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            self._repo(repo)
            packet = self.accel.compile_context(repo, "Security release audit", [".github/workflows/ci.yml"])
            self.assertEqual("DEEP", packet.mode)
            self.assertLessEqual(len(packet.files_to_read), 48)

    def test_accelerator_rejects_parent_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            self._repo(repo)
            with self.assertRaises(ValueError):
                self.accel.compile_context(repo, "fix", ["../outside.py"])

    def test_accelerator_rejects_absolute_changed_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            self._repo(repo)
            with self.assertRaises(ValueError):
                self.accel.compile_context(repo, "fix", ["/tmp/outside.py"])

    def test_compactor_preserves_short_output(self) -> None:
        raw = "one\ntwo\n3 passed\n"
        result = self.compactor.compact_text(raw, 20)
        self.assertFalse(result.truncated)
        self.assertIn("3 passed", result.text)
        self.assertEqual(0.0, result.char_reduction)

    def test_compactor_reduces_noisy_test_log_and_preserves_failure(self) -> None:
        lines = [f"tests/test_bulk.py::test_{i} PASSED" for i in range(1000)]
        lines += ["FAILED tests/test_auth.py::test_expired - AssertionError: expected 401", "2 failed, 999 passed in 8.2s"]
        raw = "\n".join(lines) + "\n"
        result = self.compactor.compact_text(raw, 60)
        self.assertTrue(result.truncated)
        self.assertIn("FAILED tests/test_auth.py::test_expired", result.text)
        self.assertIn("2 failed, 999 passed", result.text)
        self.assertIn(result.raw_sha256, result.text)
        self.assertGreater(result.char_reduction, 0.8)
        self.assertLessEqual(result.compact_lines, 60)

    def test_compactor_preserves_python_traceback_neighborhood(self) -> None:
        lines = [f"ordinary output {index}" for index in range(120)]
        lines += [
            "Traceback (most recent call last):",
            "ValueError: controlled sample failure",
        ]
        lines += [f"ordinary output {index}" for index in range(120, 180)]
        lines.append("FAILED (failures=1)")
        result = self.compactor.compact_text("\n".join(lines) + "\n", 80)
        self.assertIn("Traceback (most recent call last):", result.text)
        self.assertIn("ValueError: controlled sample failure", result.text)
        self.assertIn("FAILED (failures=1)", result.text)

    def test_compactor_clips_pathological_single_line(self) -> None:
        raw = "x" * 50_000 + "\nFAILED test_x\n"
        result = self.compactor.compact_text(raw, 20)
        self.assertLess(result.compact_chars, 10_000)
        self.assertIn("line clipped", result.text)
        self.assertIn("FAILED test_x", result.text)

    def test_compactor_rejects_too_small_budget(self) -> None:
        with self.assertRaises(ValueError):
            self.compactor.compact_text("x\n", 7)

    def test_evidence_cache_hit_then_input_change_miss(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            source = repo / "src.py"
            source.write_text("x=1\n", encoding="utf-8")
            self.cache.record_entry(repo, Path(".aipc/cache/evidence.json"), "unit", "python -m test", ["src.py"], "pass", "ok")
            hit = self.cache.check_entry(repo, Path(".aipc/cache/evidence.json"), "unit", "python -m test", ["src.py"])
            self.assertTrue(hit.reusable)
            source.write_text("x=2\n", encoding="utf-8")
            miss = self.cache.check_entry(repo, Path(".aipc/cache/evidence.json"), "unit", "python -m test", ["src.py"])
            self.assertFalse(miss.reusable)
            self.assertIn("fingerprint changed", miss.reason)

    def test_evidence_cache_command_change_misses(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "a.py").write_text("x=1\n", encoding="utf-8")
            self.cache.record_entry(repo, Path("cache.json"), "unit", "pytest a.py", ["a.py"], "pass", "ok")
            miss = self.cache.check_entry(repo, Path("cache.json"), "unit", "pytest -q a.py", ["a.py"])
            self.assertFalse(miss.hit)

    def test_evidence_cache_never_reuses_failed_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "a.py").write_text("x=1\n", encoding="utf-8")
            self.cache.record_entry(repo, Path("cache.json"), "unit", "pytest", ["a.py"], "fail", "1 failed")
            check = self.cache.check_entry(repo, Path("cache.json"), "unit", "pytest", ["a.py"])
            self.assertFalse(check.reusable)
            self.assertIn("passing evidence", check.reason)

    def test_evidence_cache_critical_gate_always_bypasses(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "a.py").write_text("x=1\n", encoding="utf-8")
            self.cache.record_entry(repo, Path("cache.json"), "release", "pytest", ["a.py"], "pass", "ok")
            check = self.cache.check_entry(repo, Path("cache.json"), "release", "pytest", ["a.py"], critical=True)
            self.assertFalse(check.reusable)
            self.assertIn("must be rerun", check.reason)

    def test_evidence_cache_rejects_input_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            with self.assertRaises(ValueError):
                self.cache.fingerprint(repo, "x", ["../outside"])

    def test_evidence_cache_rejects_symlink_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            target = repo / "target.txt"
            target.write_text("x\n", encoding="utf-8")
            link = repo / "link.txt"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlinks unavailable")
            with self.assertRaises(ValueError):
                self.cache.fingerprint(repo, "x", ["link.txt"])

    def test_evidence_cache_rejects_cache_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "a.py").write_text("x=1\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                self.cache.record_entry(repo, Path("../cache.json"), "x", "cmd", ["a.py"], "pass", "ok")

    def test_governor_supports_chinese_task_signals(self) -> None:
        fast = self.governor.plan_task("修复 README 里的错别字", ["README.md"])
        deep = self.governor.plan_task("检查发布工作流的安全权限", [".github/workflows/release.yml"])
        self.assertEqual("FAST", fast.mode)
        self.assertEqual("DEEP", deep.mode)

    def test_accelerator_never_drops_changed_files_when_change_set_exceeds_default_cap(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "src").mkdir()
            changed = []
            for i in range(60):
                rel = f"src/file_{i:02}.py"
                (repo / rel).write_text("x=1\n", encoding="utf-8")
                changed.append(rel)
            packet = self.accel.compile_context(repo, "large refactor", changed, max_files=100)
            self.assertTrue(set(changed).issubset(set(packet.files_to_read)))

    def test_evidence_cache_rejects_directory_inputs_and_missing_record_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "dir").mkdir()
            with self.assertRaises(ValueError):
                self.cache.fingerprint(repo, "cmd", ["dir"])
            with self.assertRaises(ValueError):
                self.cache.record_entry(repo, Path("cache.json"), "x", "cmd", ["missing.py"], "pass", "ok")

    def test_evidence_cache_does_not_store_plain_command_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "a.py").write_text("x=1\n", encoding="utf-8")
            cache_path = Path("cache.json")
            self.cache.record_entry(repo, cache_path, "x", "pytest --token SUPERSECRET", ["a.py"], "pass", "ok")
            raw = (repo / cache_path).read_text(encoding="utf-8")
            self.assertNotIn("SUPERSECRET", raw)
            self.assertIn("command_sha256", raw)

    def test_repo_context_partial_scan_does_not_claim_global_absence(self) -> None:
        context = load_script("repo_context")
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "src").mkdir()
            for i in range(20):
                (repo / "src" / f"f_{i:02}.py").write_text("x=1\n", encoding="utf-8")
            result = context.build_context(repo, "feature", max_files=3)
            joined = "\n".join(result.warnings)
            self.assertIn("file scan capped", joined)
            self.assertIn("scanned subset", joined)
            self.assertNotIn("no obvious test files detected", joined)

    def test_deleted_changed_path_is_kept_as_delta_evidence_not_read_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "AGENTS.md").write_text("rules\n", encoding="utf-8")
            packet = self.accel.compile_context(repo, "fix deleted file", ["src/deleted.py"])
            self.assertIn("src/deleted.py", packet.changed_files)
            self.assertNotIn("src/deleted.py", packet.files_to_read)
            self.assertTrue(any("diff/history" in note for note in packet.notes))

    def test_git_status_rename_keeps_new_and_old_paths(self) -> None:
        import shutil
        import subprocess
        if shutil.which("git") is None:
            self.skipTest("git unavailable")
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / "src").mkdir()
            (repo / "AGENTS.md").write_text("rules\n", encoding="utf-8")
            (repo / "src" / "old.py").write_text("x=1\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
            subprocess.run(["git", "mv", "src/old.py", "src/new file.py"], cwd=repo, check=True)
            packet = self.accel.compile_context(repo, "rename file", [], use_git_status=True)
            self.assertIn("src/new file.py", packet.changed_files)
            self.assertIn("src/old.py", packet.changed_files)
            self.assertIn("src/new file.py", packet.files_to_read)
            self.assertNotIn("src/old.py", packet.files_to_read)

    def test_git_status_preserves_dot_prefixed_hidden_paths(self) -> None:
        import shutil
        import subprocess
        if shutil.which("git") is None:
            self.skipTest("git unavailable")
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / ".github" / "workflows").mkdir(parents=True)
            workflow = repo / ".github" / "workflows" / "ci.yml"
            environment = repo / ".env.example"
            workflow.write_text("name: CI\n", encoding="utf-8")
            environment.write_text("MODE=safe\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
            workflow.write_text("name: changed\n", encoding="utf-8")
            environment.write_text("MODE=changed\n", encoding="utf-8")

            changed = self.accel._git_changed_files(repo)
            self.assertIn(".github/workflows/ci.yml", changed)
            self.assertIn(".env.example", changed)
            self.assertNotIn("github/workflows/ci.yml", changed)
            packet = self.accel.compile_context(repo, "small edit", [], use_git_status=True)
            self.assertEqual("DEEP", packet.mode)
            self.assertIn(".github/workflows/ci.yml", packet.changed_files)
            self.assertIn(".env.example", packet.changed_files)

    def test_this_repository_has_no_supply_chain_guard_findings(self) -> None:
        guard = load_script("supply_chain_guard")
        report = guard.scan(ROOT)
        self.assertEqual(100, report.score)
        self.assertEqual([], report.findings)

    def test_local_evidence_cache_directory_is_gitignored(self) -> None:
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".aipc/", ignore)

    def test_context_reference_and_scripts_are_present(self) -> None:
        skill = ROOT / "skills" / "ai-project-copilot"
        self.assertTrue((skill / "references" / "context-accelerator.md").exists())
        self.assertTrue((skill / "references" / "github-evidence-ledger.md").exists())
        for name in (
            "token_governor.py", "context_accelerator.py", "tool_output_compactor.py", "evidence_cache.py",
            "github_evidence_sync.py", "run_state_ledger.py", "render_maintainer_dashboard.py",
        ):
            self.assertTrue((skill / "scripts" / name).exists())

    def test_skill_core_is_leaner_than_250_lines(self) -> None:
        skill = ROOT / "skills" / "ai-project-copilot" / "SKILL.md"
        lines = skill.read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(len(lines), 250)
        self.assertIn("Context Accelerator", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
