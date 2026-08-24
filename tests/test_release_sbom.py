from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
SCRIPT = ROOT / "tools" / "generate_release_sbom.py"


class ReleaseSbomTests(unittest.TestCase):
    def run_script(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [PYTHON, str(SCRIPT), *args],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=check,
        )

    def test_sbom_is_deterministic_and_records_release_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / "ai-project-copilot.skill.zip"
            artifact.write_bytes(b"deterministic release fixture\n")
            first = root / "first.cdx.json"
            second = root / "second.cdx.json"
            args = (
                "--artifact",
                str(artifact),
                "--version",
                "v2.3.0",
                "--source-commit",
                "a" * 40,
                "--repository",
                "https://github.com/example/ai-project-copilot",
            )
            self.run_script(*args, "--output", str(first))
            self.run_script(*args, "--output", str(second))
            self.assertEqual(first.read_bytes(), second.read_bytes())

            sbom = json.loads(first.read_text(encoding="utf-8"))
            self.assertEqual("CycloneDX", sbom["bomFormat"])
            self.assertEqual("1.6", sbom["specVersion"])
            self.assertEqual("v2.3.0", sbom["metadata"]["component"]["version"])
            self.assertEqual("a" * 40, sbom["metadata"]["properties"][0]["value"])
            self.assertEqual(artifact.name, sbom["components"][0]["name"])
            self.assertEqual("SHA-256", sbom["components"][0]["hashes"][0]["alg"])

    def test_sbom_refuses_unsafe_or_invalid_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / "artifact.zip"
            artifact.write_bytes(b"fixture")
            output = root / "output.cdx.json"
            base = (
                "--artifact",
                str(artifact),
                "--output",
                str(output),
                "--version",
                "v2.3.0",
                "--source-commit",
                "b" * 40,
                "--repository",
                "https://github.com/example/ai-project-copilot",
            )
            self.run_script(*base)
            overwrite = self.run_script(*base, check=False)
            self.assertEqual(2, overwrite.returncode)
            self.assertIn("refusing to overwrite", overwrite.stderr)

            invalid_version = self.run_script(*base[:-6], "--version", "2.3.0", *base[-4:], check=False)
            self.assertEqual(2, invalid_version.returncode)
            self.assertIn("SemVer", invalid_version.stderr)


if __name__ == "__main__":
    unittest.main()
