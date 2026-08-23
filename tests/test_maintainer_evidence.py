from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "ai-project-copilot" / "scripts"


def load_script(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"aipc_evidence_{name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class MaintainerEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.syncer = load_script("github_evidence_sync")
        cls.ledger = load_script("run_state_ledger")
        cls.dashboard = load_script("render_maintainer_dashboard")

    def _write_exports(self, root: Path, issue_title: str = "Document release gate") -> Path:
        exports = root / "exports"
        exports.mkdir(parents=True)
        (exports / "issues.json").write_text(
            json.dumps(
                [
                    {
                        "number": 42,
                        "title": issue_title,
                        "state": "open",
                        "labels": [{"name": "security"}],
                        "html_url": "https://github.com/example/demo/issues/42",
                        "updated_at": "2026-08-24T00:00:00Z",
                        "body": "This field is intentionally ignored.",
                    }
                ]
            ),
            encoding="utf-8",
        )
        (exports / "pull_requests.json").write_text(
            json.dumps(
                {
                    "pull_requests": [
                        {
                            "number": 7,
                            "title": "Harden release validation",
                            "state": "open",
                            "mergeable": False,
                            "html_url": "https://github.com/example/demo/pull/7",
                            "updated_at": "2026-08-24T00:00:00Z",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (exports / "workflow_runs.json").write_text(
            json.dumps(
                {
                    "workflow_runs": [
                        {
                            "id": 99,
                            "name": "CI",
                            "status": "completed",
                            "conclusion": "failure",
                            "html_url": "https://github.com/example/demo/actions/runs/99",
                            "updated_at": "2026-08-24T00:00:00Z",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (exports / "releases.json").write_text(
            json.dumps(
                [
                    {
                        "tag_name": "v2.1.2",
                        "name": "2.1.2",
                        "published_at": "2026-08-24T00:00:00Z",
                        "html_url": "https://github.com/example/demo/releases/tag/v2.1.2",
                    }
                ]
            ),
            encoding="utf-8",
        )
        return exports

    def test_sync_normalizes_records_and_preserves_stable_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            exports = self._write_exports(repo)
            first = self.syncer.build_bundle(exports)
            self.assertEqual(4, len(first["evidence"]))
            self.assertEqual(3, first["summary"]["blocker_count"])
            self.assertTrue(all(record["untrusted"] for record in first["evidence"]))
            issue = next(record for record in first["evidence"] if record["kind"] == "issue")
            self.assertNotIn("body", issue)
            self.assertIn("security", issue["labels"])

            (exports / "issues.json").write_text(
                json.dumps([{"number": 42, "title": "Renamed issue", "state": "closed"}]),
                encoding="utf-8",
            )
            second = self.syncer.build_bundle(exports)
            changed_issue = next(record for record in second["evidence"] if record["kind"] == "issue")
            self.assertEqual(issue["evidence_id"], changed_issue["evidence_id"])
            self.assertEqual("closed", changed_issue["status"])

    def test_checked_in_export_fixture_is_a_complete_offline_demo(self) -> None:
        bundle = self.syncer.build_bundle(ROOT / "examples" / "github-export")
        self.assertEqual(4, len(bundle["evidence"]))
        self.assertEqual(
            {"issue", "pull_request", "workflow_run", "release"},
            {record["kind"] for record in bundle["evidence"]},
        )

    def test_sync_rejects_deep_json_and_unsafe_output_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            exports = repo / "exports"
            exports.mkdir()
            (exports / "issues.json").write_text("[" * 129 + "]" * 129, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "nesting exceeds"):
                self.syncer.build_bundle(exports)

            good = self._write_exports(repo / "good")
            bundle = self.syncer.build_bundle(good)
            with self.assertRaisesRegex(ValueError, "inside repository"):
                self.syncer.write_bundle(repo, Path("../escape.json"), bundle)
            written = self.syncer.write_bundle(repo, Path(".aipc/evidence.json"), bundle)
            self.assertTrue(written.is_file())
            with self.assertRaisesRegex(ValueError, "overwrite"):
                self.syncer.write_bundle(repo, Path(".aipc/evidence.json"), bundle)

    def test_ledger_retains_decisions_when_imported_record_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            exports = self._write_exports(repo)
            bundle = self.syncer.build_bundle(exports)
            first_path = self.syncer.write_bundle(repo, Path(".aipc/evidence-first.json"), bundle)
            initialized = self.ledger.initialize(repo)
            self.assertEqual(".aipc/maintainer-ledger.json", initialized["ledger"])
            synced = self.ledger.sync(repo, Path(".aipc/maintainer-ledger.json"), first_path.relative_to(repo.resolve()))
            self.assertEqual(4, synced["created"])
            pull = next(record for record in bundle["evidence"] if record["kind"] == "pull_request")
            self.ledger.decide(
                repo,
                Path(".aipc/maintainer-ledger.json"),
                pull["evidence_id"],
                "fix",
                "open",
                note="Reproduce and fix the merge conflict.",
            )
            self.assertEqual(1, len(self.ledger.status(repo, Path(".aipc/maintainer-ledger.json"))["pending"]))

            (exports / "pull_requests.json").write_text(
                json.dumps({"pull_requests": [{"number": 7, "title": "Conflict fixed", "state": "closed"}]}),
                encoding="utf-8",
            )
            revised = self.syncer.build_bundle(exports)
            second_path = self.syncer.write_bundle(repo, Path(".aipc/evidence-second.json"), revised)
            self.ledger.sync(repo, Path(".aipc/maintainer-ledger.json"), second_path.relative_to(repo.resolve()))
            report = self.ledger.status(repo, Path(".aipc/maintainer-ledger.json"))
            self.assertEqual("fix", report["pending"][0]["decision"])
            raw = json.loads((repo / ".aipc" / "maintainer-ledger.json").read_text(encoding="utf-8"))
            self.assertEqual("Conflict fixed", raw["entries"][pull["evidence_id"]]["evidence"]["title"])

    def test_ledger_requires_explanation_for_sensitive_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            exports = self._write_exports(repo)
            bundle = self.syncer.build_bundle(exports)
            evidence_path = self.syncer.write_bundle(repo, Path(".aipc/evidence.json"), bundle)
            self.ledger.initialize(repo)
            self.ledger.sync(repo, Path(".aipc/maintainer-ledger.json"), evidence_path.relative_to(repo.resolve()))
            evidence_id = bundle["evidence"][0]["evidence_id"]
            with self.assertRaisesRegex(ValueError, "human owner"):
                self.ledger.decide(repo, Path(".aipc/maintainer-ledger.json"), evidence_id, "escalate", "open")
            with self.assertRaisesRegex(ValueError, "evidence note"):
                self.ledger.decide(repo, Path(".aipc/maintainer-ledger.json"), evidence_id, "decline", "resolved")

    def test_ledger_and_dashboard_reject_duplicate_evidence_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            bundle = self.syncer.build_bundle(self._write_exports(repo))
            duplicate = dict(bundle)
            duplicate["evidence"] = [bundle["evidence"][0], dict(bundle["evidence"][0])]
            (repo / "duplicate.json").write_text(json.dumps(duplicate), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate evidence ID"):
                self.ledger._bundle(repo, Path("duplicate.json"))
            with self.assertRaisesRegex(ValueError, "duplicate evidence ID"):
                self.dashboard._bundle(repo, Path("duplicate.json"))

    def test_dashboard_escapes_exported_html_and_keeps_decision_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            exports = self._write_exports(repo, "<script>alert(1)</script>")
            bundle = self.syncer.build_bundle(exports)
            markdown = self.syncer.markdown(bundle)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", markdown)
            self.assertNotIn("<script>alert(1)</script>", markdown)
            self.assertIn("&amp;lt;script&amp;gt;", self.syncer._markdown_inline("&lt;script&gt;"))
            bundle_path = self.syncer.write_bundle(repo, Path(".aipc/evidence.json"), bundle)
            self.ledger.initialize(repo)
            self.ledger.sync(repo, Path(".aipc/maintainer-ledger.json"), bundle_path.relative_to(repo.resolve()))
            issue = next(record for record in bundle["evidence"] if record["kind"] == "issue")
            self.ledger.decide(
                repo,
                Path(".aipc/maintainer-ledger.json"),
                issue["evidence_id"],
                "escalate",
                "open",
                owner="release-manager",
                note="Security label needs a human call.",
            )
            view = self.dashboard.build_view(
                self.dashboard._bundle(repo, bundle_path.relative_to(repo.resolve())),
                self.dashboard._ledger(repo, Path(".aipc/maintainer-ledger.json")),
            )
            output = self.dashboard.write_html(repo, Path("reports/evidence.html"), self.dashboard.render_html(view))
            document = output.read_text(encoding="utf-8")
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", document)
            self.assertNotIn("<script>alert(1)</script>", document)
            self.assertIn("escalate", document)
            with self.assertRaisesRegex(ValueError, "overwrite"):
                self.dashboard.write_html(repo, Path("reports/evidence.html"), self.dashboard.render_html(view))


if __name__ == "__main__":
    unittest.main()
