from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "ai-project-copilot" / "scripts" / "maintainer_triage.py"
PYTHON = sys.executable


def run(*args: str) -> dict:
    result = subprocess.run(
        [PYTHON, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return json.loads(result.stdout)


class MaintainerTriageTests(unittest.TestCase):
    def test_docs_issue_is_starter_friendly(self) -> None:
        data = run(
            "--title", "README typo in install example",
            "--body", "The README has a typo in a small documentation example and the expected correction is clear.",
        )
        self.assertIn("documentation", data["labels"])
        self.assertIn("good first issue", data["labels"])
        self.assertEqual("starter", data["difficulty"])

    def test_bug_without_reproduction_requests_evidence(self) -> None:
        data = run("--title", "App crashes", "--body", "The app crashes and does not work for me.")
        self.assertIn("bug", data["labels"])
        self.assertIn("needs-reproduction", data["labels"])
        self.assertTrue(data["needs"])

    def test_security_signal_is_high_priority(self) -> None:
        data = run(
            "--title", "Possible token leak",
            "--body", "Security vulnerability: an API token leak appears in debug logs and may expose credentials.",
        )
        self.assertIn("security", data["labels"])
        self.assertIn("priority:high", data["labels"])
        self.assertEqual("high", data["priority"])
        self.assertEqual("advanced", data["difficulty"])

    def test_checked_in_example_is_reproducible(self) -> None:
        result = subprocess.run(
            [PYTHON, str(SCRIPT), "--issue-json", str(ROOT / "examples" / "issue-triage-input.json")],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        expected = json.loads((ROOT / "examples" / "issue-triage-output.json").read_text(encoding="utf-8"))
        self.assertEqual(expected, json.loads(result.stdout))


if __name__ == "__main__":
    unittest.main()
