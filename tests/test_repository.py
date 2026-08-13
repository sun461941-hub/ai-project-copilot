from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "ai-project-copilot"
PYTHON = sys.executable


def run(*args: str, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


class RepositoryTests(unittest.TestCase):
    def test_skill_validator_passes(self) -> None:
        result = run("tools/validate_skill.py", str(SKILL))
        self.assertIn("PASS", result.stdout)

    def test_blueprint_catalog_has_24_unique_entries(self) -> None:
        data = json.loads((SKILL / "references" / "blueprints.json").read_text(encoding="utf-8"))
        self.assertEqual(24, len(data))
        self.assertEqual(24, len({item["id"] for item in data}))
        catalog = (SKILL / "references" / "showcase-projects.md").read_text(encoding="utf-8")
        for item in data:
            self.assertIn(item["name"], catalog)

    def test_android_video_blueprint_ranks_first_for_matching_request(self) -> None:
        result = run(
            str(SKILL / "scripts" / "rank_blueprints.py"),
            "--priorities", "local-first,video,android,visual-demo",
            "--constraints", "privacy,mobile",
            "--limit", "3",
            "--json",
        )
        data = json.loads(result.stdout)
        self.assertEqual("android-local-video-runtime", data[0]["id"])

    def test_init_project_docs_never_overwrites_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            first = run(str(SKILL / "scripts" / "init_project_docs.py"), "--repo", str(repo), "--json")
            first_data = json.loads(first.stdout)
            self.assertEqual(5, len(first_data["created"]))
            brief = repo / "docs" / "ai-project" / "project-brief.md"
            brief.write_text("custom\n", encoding="utf-8")
            second = run(str(SKILL / "scripts" / "init_project_docs.py"), "--repo", str(repo), "--json")
            second_data = json.loads(second.stdout)
            self.assertEqual(0, len(second_data["created"]))
            self.assertEqual(5, len(second_data["skipped"]))
            self.assertEqual("custom\n", brief.read_text(encoding="utf-8"))

    def test_repo_audit_emits_transparent_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "src").mkdir()
            (repo / "src" / "app.py").write_text("print('demo')\n", encoding="utf-8")
            (repo / "tests").mkdir()
            (repo / "tests" / "test_app.py").write_text("def test_demo(): assert True\n", encoding="utf-8")
            (repo / ".github" / "workflows").mkdir(parents=True)
            (repo / ".github" / "workflows" / "ci.yml").write_text("name: CI\n", encoding="utf-8")
            (repo / "README.md").write_text(
                "# Demo Copilot\n\nTurn data into a cited artifact.\n\n"
                "## Quick start\n```bash\npython src/app.py\n```\n\n"
                "## Demo\nUse the sample workflow.\n\n"
                "## Architecture and data flow\nLocal-first.\n\n"
                "## Limitations\nExperimental.\n\n"
                "## Models and licensing\nNo model weights are redistributed.\n\n"
                "## Evaluation\nRun tests.\n",
                encoding="utf-8",
            )
            (repo / "LICENSE").write_text("MIT\n", encoding="utf-8")
            result = run(str(SKILL / "scripts" / "audit_repo.py"), "--repo", str(repo), "--json")
            data = json.loads(result.stdout)
            self.assertEqual(100, data["maximum"])
            self.assertGreaterEqual(data["percentage"], 55)
            self.assertTrue(any(check["id"] == "secrets" for check in data["checks"]))

    def test_packaging_is_deterministic_and_single_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            one = Path(temp) / "one.zip"
            two = Path(temp) / "two.zip"
            run("tools/package_skill.py", str(SKILL), "--output", str(one))
            run("tools/package_skill.py", str(SKILL), "--output", str(two))
            self.assertEqual(one.read_bytes(), two.read_bytes())
            self.assertEqual(
                hashlib.sha256(one.read_bytes()).hexdigest(),
                hashlib.sha256(two.read_bytes()).hexdigest(),
            )
            with zipfile.ZipFile(one) as archive:
                names = archive.namelist()
                self.assertTrue(names)
                self.assertTrue(all(name.startswith("ai-project-copilot/") for name in names))
                self.assertTrue(all("\\" not in name for name in names))
                self.assertIn("ai-project-copilot/SKILL.md", names)
                self.assertNotIn("ai-project-copilot/README.md", names)

    def test_packager_refuses_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "skill.zip"
            output.write_bytes(b"keep-me")
            result = run(
                "tools/package_skill.py",
                str(SKILL),
                "--output", str(output),
                check=False,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertEqual(b"keep-me", output.read_bytes())

    def test_checked_in_ranking_example_is_reproducible(self) -> None:
        result = run(
            str(SKILL / "scripts" / "rank_blueprints.py"),
            "--priorities", "local-first,video,android,visual-demo",
            "--constraints", "privacy,mobile",
            "--limit", "3",
            "--json",
        )
        expected = json.loads((ROOT / "examples" / "android-local-video-ranking.json").read_text(encoding="utf-8"))
        self.assertEqual(expected, json.loads(result.stdout))

    def test_this_repository_is_showcase_ready(self) -> None:
        result = run(str(SKILL / "scripts" / "audit_repo.py"), "--repo", str(ROOT), "--json")
        data = json.loads(result.stdout)
        self.assertEqual(100, data["percentage"])
        self.assertEqual("showcase-ready", data["grade"])
        self.assertEqual([], data["secret_findings"])

    def test_trigger_eval_dataset_is_balanced(self) -> None:
        with (ROOT / "evals" / "trigger-prompts.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(20, len(rows))
        labels = [row["should_trigger"].lower() for row in rows]
        self.assertEqual(10, labels.count("true"))
        self.assertEqual(10, labels.count("false"))
        self.assertEqual({"train", "validation"}, {row["split"] for row in rows})


if __name__ == "__main__":
    unittest.main()
