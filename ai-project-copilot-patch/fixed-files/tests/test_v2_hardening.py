from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "ai-project-copilot" / "scripts"


def load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    # dataclasses expects the module to be registered during class creation.
    import sys
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class V2HardeningTests(unittest.TestCase):
    def test_triage_avoids_substring_security_false_positive(self) -> None:
        triage = load_module("maintainer_triage")
        result = triage.triage_issue(
            "Secretary guide needs a wording update",
            "Please update the secretary guide wording and add context for contributors.",
        )
        self.assertNotIn("security", result.labels)

    def test_bug_with_minimal_example_is_not_automatically_good_first_issue(self) -> None:
        triage = load_module("maintainer_triage")
        result = triage.triage_issue(
            "Crash when opening a project",
            "Steps to reproduce: open a project, click import, then it crashes. Minimal example is attached with logs and environment details.",
        )
        self.assertIn("bug", result.labels)
        self.assertNotIn("good first issue", result.labels)
        self.assertNotEqual("starter", result.difficulty)

    def test_router_does_not_treat_report_or_preview_as_repo_review(self) -> None:
        router = load_module("workflow_router")
        report_routes = router.route("Generate a polished status report")
        preview_modes = {item.mode for item in router.route("Preview the output before publishing later")}
        self.assertEqual(1, report_routes[0].score)
        self.assertIn("no strong lane keyword", report_routes[0].reasons[0])
        self.assertNotIn("quality", preview_modes)

    def test_change_risk_detects_monorepo_source_and_real_tests(self) -> None:
        risk = load_module("change_risk")
        report = risk.analyze([
            risk.Change("packages/api/src/handler.py", 40, 3),
            risk.Change("packages/api/tests/test_handler.py", 25, 0),
        ])
        self.assertFalse(any("without an obvious test-file change" in reason for reason in report.reasons))

    def test_change_risk_does_not_treat_testimonial_as_test(self) -> None:
        risk = load_module("change_risk")
        report = risk.analyze([risk.Change("packages/web/src/testimonial.py", 20, 2)])
        self.assertTrue(any("without an obvious test-file change" in reason for reason in report.reasons))

    def test_change_json_rejects_negative_or_fractional_counts(self) -> None:
        risk = load_module("change_risk")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "changes.json"
            path.write_text(json.dumps({"changes": [{"path": "src/a.py", "additions": -1}]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                risk.parse_changes_json(path)
            path.write_text(json.dumps({"changes": [{"path": "src/a.py", "additions": 1.5}]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                risk.parse_changes_json(path)

    def test_repo_context_rejects_nonpositive_file_cap_without_false_test_paths(self) -> None:
        context = load_module("repo_context")
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "src").mkdir()
            (repo / "src" / "testimonial.py").write_text("x = 1\n", encoding="utf-8")
            result = context.build_context(repo, max_files=10)
            self.assertEqual([], result.tests)
            with self.assertRaises(ValueError):
                context.build_context(repo, max_files=0)

    def test_release_semver_accepts_prerelease_build_and_uppercase_types(self) -> None:
        release = load_module("release_intel")
        self.assertEqual((2, 0, 0), release.parse_version("v2.0.0-rc.1+build.7"))
        report = release.classify(["FEAT: add final polish"], "2.0.0-rc.1")
        self.assertEqual("minor", report.bump)
        self.assertEqual("2.0.0", report.suggested_version)
        self.assertIn("Features", report.groups)
        with self.assertRaises(ValueError):
            release.parse_version("01.2.3")

    def test_release_does_not_mark_plain_breaking_phrase_as_footer(self) -> None:
        release = load_module("release_intel")
        report = release.classify(["docs: explain why this is not a breaking change"], "1.2.3")
        self.assertNotEqual("major", report.bump)
        self.assertFalse(report.migration_notes_required)

    def test_review_convergence_rejects_empty_duplicate_and_unexplained_decline(self) -> None:
        convergence = load_module("review_convergence")
        self.assertFalse(convergence.analyze([]).ready_for_rereview)
        duplicate = [
            convergence.ThreadState("same", "fix", "resolved", "fixed", "abc", ""),
            convergence.ThreadState("same", "fix", "resolved", "fixed again", "def", ""),
        ]
        self.assertFalse(convergence.analyze(duplicate).ready_for_rereview)
        decline = [convergence.ThreadState("d1", "decline", "resolved", "", "", "")]
        self.assertFalse(convergence.analyze(decline).ready_for_rereview)

    def test_review_convergence_requires_owner_for_resolved_escalation(self) -> None:
        convergence = load_module("review_convergence")
        missing_owner = [
            convergence.ThreadState("e1", "escalate", "resolved", "maintainer decision recorded", "", "")
        ]
        report = convergence.analyze(missing_owner)
        self.assertFalse(report.ready_for_rereview)
        self.assertTrue(any("no human owner" in item for item in report.blockers))

        assigned = [
            convergence.ThreadState("e1", "escalate", "resolved", "maintainer decision recorded", "", "release-owner")
        ]
        self.assertTrue(convergence.analyze(assigned).ready_for_rereview)

    def test_supply_chain_detects_multiline_run_interpolation_and_confines_manifest(self) -> None:
        guard = load_module("supply_chain_guard")
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            workflows = repo / ".github" / "workflows"
            workflows.mkdir(parents=True)
            (workflows / "ci.yml").write_text(
                "name: CI\npermissions:\n  contents: read\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: |\n          echo '${{ github.event.pull_request.title }}'\n",
                encoding="utf-8",
            )
            skill = repo / "skills" / "ai-project-copilot"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("demo\n", encoding="utf-8")
            report = guard.scan(repo)
            self.assertIn("event-interpolation", {item.code for item in report.findings})
            with self.assertRaises(ValueError):
                guard.scan(repo, Path("../outside.sha256"))

    def test_supply_chain_refuses_dangling_manifest_symlink(self) -> None:
        guard = load_module("supply_chain_guard")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            skill = repo / "skills" / "ai-project-copilot"
            outside = root / "outside"
            skill.mkdir(parents=True)
            outside.mkdir()
            (skill / "SKILL.md").write_text("demo\n", encoding="utf-8")
            manifest = repo / "MANIFEST.sha256"
            try:
                manifest.symlink_to(outside / "MANIFEST.sha256")
            except OSError:
                self.skipTest("symlinks unavailable")
            with self.assertRaisesRegex(ValueError, "through a symlink"):
                guard.scan(repo, Path("MANIFEST.sha256"))
            self.assertFalse((outside / "MANIFEST.sha256").exists())

    def test_mcp_audit_avoids_tokenizer_false_positive_and_supports_real_env_refs(self) -> None:
        mcp = load_module("mcp_config_audit")
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / ".mcp.json").write_text(json.dumps({
                "mcpServers": {
                    "local": {
                        "command": "node",
                        "tokenizer": "literal-model-name",
                        "apiToken": "$env:MCP_TOKEN",
                        "url": "http://127.0.0.1:3000/mcp",
                    }
                }
            }), encoding="utf-8")
            report = mcp.scan(repo)
            codes = {item.code for item in report.findings}
            self.assertNotIn("hardcoded-secret", codes)
            self.assertNotIn("insecure-transport", codes)

    def test_mcp_explicit_path_cannot_escape_repo_and_bom_json_is_supported(self) -> None:
        mcp = load_module("mcp_config_audit")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            config = repo / "mcp.json"
            config.write_text("\ufeff" + json.dumps({"mcpServers": {}}), encoding="utf-8")
            report = mcp.scan(repo, [Path("mcp.json")])
            self.assertEqual(1, report.files_scanned)
            outside = root / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                mcp.scan(repo, [outside])

    def test_skill_stack_reads_folded_yaml_description(self) -> None:
        stack = load_module("skill_stack_audit")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "one"
            second = root / "two"
            first.mkdir(); second.mkdir()
            first.joinpath("SKILL.md").write_text(
                "---\nname: code-review-one\ndescription: >-\n  Review pull requests for security, regression, API,\n  compatibility, and test coverage risks.\n---\n# One\n", encoding="utf-8"
            )
            second.joinpath("SKILL.md").write_text(
                "---\nname: code-review-two\ndescription: >-\n  Review pull requests for security, regression, API,\n  compatibility, and test coverage issues.\n---\n# Two\n", encoding="utf-8"
            )
            report = stack.scan([root], overlap_threshold=0.3)
            self.assertTrue(all(skill.description != ">-" for skill in report.skills))
            self.assertTrue(report.overlaps)

    def test_supply_chain_ignores_fake_yaml_inside_run_block(self) -> None:
        guard = load_module("supply_chain_guard")
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            workflows = repo / ".github" / "workflows"
            workflows.mkdir(parents=True)
            (workflows / "safe.yml").write_text(
                "name: safe\npermissions:\n  contents: read\non:\n  push:\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: |\n          pull_request_target:\n          permissions: write-all\n          - uses: attacker/example@main\n",
                encoding="utf-8",
            )
            skill = repo / "skills" / "ai-project-copilot"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("demo\n", encoding="utf-8")
            codes = {item.code for item in guard.scan(repo).findings}
            self.assertNotIn("privileged-trigger", codes)
            self.assertNotIn("write-all", codes)
            self.assertNotIn("mutable-action-ref", codes)

    def test_supply_chain_hashes_both_skill_roots_and_warns_when_none_exist(self) -> None:
        guard = load_module("supply_chain_guard")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "both"
            for rel in ("skills/ai-project-copilot", ".agents/skills/ai-project-copilot"):
                skill = repo / rel
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text("demo\n", encoding="utf-8")
            report = guard.scan(repo)
            self.assertEqual(2, report.integrity_files)
            self.assertEqual([], report.warnings)
            with self.assertRaises(ValueError):
                guard.scan(repo, Path(".agents/skills/ai-project-copilot/MANIFEST.sha256"))

            empty_repo = root / "empty"
            empty_repo.mkdir()
            empty_report = guard.scan(empty_repo)
            self.assertEqual(0, empty_report.integrity_files)
            self.assertTrue(any("no hashable skill files" in item for item in empty_report.warnings))

    def test_release_core_api_treats_blank_messages_as_empty_delta(self) -> None:
        release = load_module("release_intel")
        report = release.classify(["", "   ", "\n\t"], "1.2.3")
        self.assertFalse(report.release_ready)
        self.assertEqual("none", report.bump)
        self.assertTrue(any("no commits supplied" in item for item in report.blockers))


if __name__ == "__main__":
    unittest.main()
