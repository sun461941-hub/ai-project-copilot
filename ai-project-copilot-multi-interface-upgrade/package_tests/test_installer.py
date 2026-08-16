from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import apply_multi_interface_patch as installer


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        scripts = self.repo / "skills/ai-project-copilot/scripts"
        scripts.mkdir(parents=True)
        self.skill = self.repo / installer.SKILL_REL
        self.original_skill = "# AI Project Copilot 2.1\n\nFor test.\n\n## Capability lanes\n\nRead lanes.\n"
        self.skill.write_text(self.original_skill, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_dry_run_does_not_mutate_repository(self) -> None:
        changes = installer.apply(self.repo, dry_run=True, force=False)
        self.assertTrue(any(item.startswith("add ") for item in changes))
        self.assertEqual(self.original_skill, self.skill.read_text(encoding="utf-8"))
        for source in installer.payload_files():
            self.assertFalse((self.repo / source.relative_to(installer.PAYLOAD)).exists())

    def test_apply_preflight_prevents_partial_install_on_late_conflict(self) -> None:
        sources = installer.payload_files()
        conflict_source = sources[-1]
        conflict = self.repo / conflict_source.relative_to(installer.PAYLOAD)
        conflict.parent.mkdir(parents=True, exist_ok=True)
        conflict.write_text("local modification", encoding="utf-8")
        with self.assertRaises(SystemExit):
            installer.apply(self.repo, dry_run=False, force=False)
        self.assertEqual("local modification", conflict.read_text(encoding="utf-8"))
        self.assertEqual(self.original_skill, self.skill.read_text(encoding="utf-8"))
        for source in sources[:-1]:
            self.assertFalse((self.repo / source.relative_to(installer.PAYLOAD)).exists())

    def test_apply_preflight_prevents_partial_install_when_skill_anchor_missing(self) -> None:
        self.skill.write_text("# AI Project Copilot 2.1\nno capability anchor\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            installer.apply(self.repo, dry_run=False, force=False)
        for source in installer.payload_files():
            self.assertFalse((self.repo / source.relative_to(installer.PAYLOAD)).exists())

    def test_apply_and_rollback_are_reversible(self) -> None:
        installer.apply(self.repo, dry_run=False, force=False)
        self.assertIn("## Multi-interface gateway", self.skill.read_text(encoding="utf-8"))
        for source in installer.payload_files():
            self.assertTrue((self.repo / source.relative_to(installer.PAYLOAD)).is_file())
        installer.rollback(self.repo, dry_run=False, force=False)
        self.assertEqual(self.original_skill, self.skill.read_text(encoding="utf-8"))
        for source in installer.payload_files():
            self.assertFalse((self.repo / source.relative_to(installer.PAYLOAD)).exists())

    def test_rollback_preflight_prevents_partial_removal_on_modified_file(self) -> None:
        installer.apply(self.repo, dry_run=False, force=False)
        sources = installer.payload_files()
        conflict_source = sources[0]  # encountered late by reversed rollback order in preview.1
        conflict = self.repo / conflict_source.relative_to(installer.PAYLOAD)
        conflict.write_text("developer changed this", encoding="utf-8")
        with self.assertRaises(SystemExit):
            installer.rollback(self.repo, dry_run=False, force=False)
        self.assertIn("## Multi-interface gateway", self.skill.read_text(encoding="utf-8"))
        for source in sources:
            self.assertTrue((self.repo / source.relative_to(installer.PAYLOAD)).is_file())


    def test_preexisting_gateway_section_is_not_owned_or_removed(self) -> None:
        preexisting = self.original_skill.replace(
            "\n## Capability lanes\n",
            installer.INSERT + "## Capability lanes\n",
            1,
        )
        self.skill.write_text(preexisting, encoding="utf-8")
        installer.apply(self.repo, dry_run=False, force=False)
        installer.rollback(self.repo, dry_run=False, force=False)
        self.assertEqual(preexisting, self.skill.read_text(encoding="utf-8"))

    def test_force_replacement_is_backed_up_and_restored_on_rollback(self) -> None:
        source = installer.payload_files()[0]
        target = self.repo / source.relative_to(installer.PAYLOAD)
        target.parent.mkdir(parents=True, exist_ok=True)
        original = b"developer-owned file before force apply\n"
        target.write_bytes(original)
        installer.apply(self.repo, dry_run=False, force=True)
        self.assertEqual(installer.sha256(source), installer.sha256(target))
        installer.rollback(self.repo, dry_run=False, force=False)
        self.assertEqual(original, target.read_bytes())


if __name__ == "__main__":
    unittest.main()
