from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
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

    def test_demo_runbook_uses_the_checked_in_fixture_and_explicit_local_decision(self) -> None:
        text = (ROOT / "DEMO.md").read_text(encoding="utf-8")
        self.assertIn("examples/github-export", text)
        self.assertIn("638789e58bc5a2f97842", text)
        self.assertIn("--decision escalate", text)
        self.assertIn("does **not** call GitHub", text)

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

    def test_ledger_uses_revisions_and_records_decision_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            bundle = self.syncer.build_bundle(self._write_exports(repo))
            bundle_path = self.syncer.write_bundle(repo, Path(".aipc/evidence.json"), bundle)
            initialized = self.ledger.initialize(repo)
            self.assertEqual(0, initialized["revision"])
            synced = self.ledger.sync(
                repo,
                Path(".aipc/maintainer-ledger.json"),
                bundle_path.relative_to(repo.resolve()),
            )
            self.assertEqual(1, synced["revision"])
            evidence_id = bundle["evidence"][0]["evidence_id"]

            event = self.ledger.decide(
                repo,
                Path(".aipc/maintainer-ledger.json"),
                evidence_id,
                "observe",
                "resolved",
                actor="codex-maintainer",
                source_commit="49f91b6ac12b7e2c42e6e7ff160b30c7cc10c02f",
                expected_revision=1,
            )
            self.assertEqual(2, event["ledger_revision"])
            self.assertEqual("codex-maintainer", event["actor"])
            self.assertEqual("49f91b6ac12b7e2c42e6e7ff160b30c7cc10c02f", event["source_commit"])
            self.assertTrue(event["recorded_at"].endswith("Z"))
            self.assertEqual(2, self.ledger.status(repo, Path(".aipc/maintainer-ledger.json"))["revision"])

            with self.assertRaisesRegex(ValueError, "revision conflict"):
                self.ledger.decide(
                    repo,
                    Path(".aipc/maintainer-ledger.json"),
                    evidence_id,
                    "observe",
                    "resolved",
                    expected_revision=1,
                )
            raw = json.loads((repo / ".aipc" / "maintainer-ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(1, len(raw["entries"][evidence_id]["history"]))

    def test_ledger_fails_closed_when_a_separate_process_holds_the_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            bundle = self.syncer.build_bundle(self._write_exports(repo))
            bundle_path = self.syncer.write_bundle(repo, Path(".aipc/evidence.json"), bundle)
            self.ledger.initialize(repo)
            lock = repo / ".aipc" / ".maintainer-ledger.json.lock"
            lock.write_text('{"pid": 999}', encoding="utf-8")
            with mock.patch.object(self.ledger, "LOCK_TIMEOUT_SECONDS", 0):
                with self.assertRaisesRegex(ValueError, "locked by another process"):
                    self.ledger.sync(
                        repo,
                        Path(".aipc/maintainer-ledger.json"),
                        bundle_path.relative_to(repo.resolve()),
                    )

    def test_ledger_stale_lock_recovery_requires_explicit_proven_inactive_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            bundle = self.syncer.build_bundle(self._write_exports(repo))
            bundle_path = self.syncer.write_bundle(repo, Path(".aipc/evidence.json"), bundle)
            self.ledger.initialize(repo)
            lock = repo / ".aipc" / ".maintainer-ledger.json.lock"
            lock.write_text(
                json.dumps(
                    {
                        "pid": 424242,
                        "hostname": self.ledger.socket.gethostname(),
                        "created_at": "2000-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(self.ledger, "_pid_is_active", return_value=False):
                report = self.ledger.lock_status(
                    repo,
                    Path(".aipc/maintainer-ledger.json"),
                    minimum_stale_age_seconds=0,
                )
                self.assertTrue(report["recoverable"])
                with self.assertRaisesRegex(ValueError, "force-stale-lock"):
                    self.ledger.recover_stale_lock(
                        repo,
                        Path(".aipc/maintainer-ledger.json"),
                        minimum_stale_age_seconds=0,
                    )
                recovered = self.ledger.recover_stale_lock(
                    repo,
                    Path(".aipc/maintainer-ledger.json"),
                    minimum_stale_age_seconds=0,
                    force_stale_lock=True,
                )
            self.assertFalse(lock.exists())
            archived = repo / recovered["archived_lock"]
            self.assertEqual(json.loads(archived.read_text(encoding="utf-8"))["pid"], 424242)
            self.assertEqual(
                1,
                self.ledger.sync(
                    repo,
                    Path(".aipc/maintainer-ledger.json"),
                    bundle_path.relative_to(repo.resolve()),
                )["revision"],
            )

    def test_ledger_stale_lock_recovery_refuses_foreign_or_legacy_lock_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            self.ledger.initialize(repo)
            lock = repo / ".aipc" / ".maintainer-ledger.json.lock"
            lock.write_text(
                json.dumps({"pid": 424242, "hostname": "other-host", "created_at": "2000-01-01T00:00:00Z"}),
                encoding="utf-8",
            )
            foreign = self.ledger.lock_status(repo, Path(".aipc/maintainer-ledger.json"), minimum_stale_age_seconds=0)
            self.assertFalse(foreign["recoverable"])
            self.assertIn("different host", foreign["recovery_reason"])
            with self.assertRaisesRegex(ValueError, "different host"):
                self.ledger.recover_stale_lock(
                    repo,
                    Path(".aipc/maintainer-ledger.json"),
                    minimum_stale_age_seconds=0,
                    force_stale_lock=True,
                )
            lock.write_text('{"pid": 424242}', encoding="utf-8")
            legacy = self.ledger.lock_status(repo, Path(".aipc/maintainer-ledger.json"), minimum_stale_age_seconds=0)
            self.assertFalse(legacy["metadata_valid"])
            with self.assertRaisesRegex(ValueError, "metadata"):
                self.ledger.recover_stale_lock(
                    repo,
                    Path(".aipc/maintainer-ledger.json"),
                    minimum_stale_age_seconds=0,
                    force_stale_lock=True,
                )

    def test_ledger_lock_status_is_read_only_when_ledger_parent_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            report = self.ledger.lock_status(repo, Path("new-state/maintainer-ledger.json"))
            self.assertFalse(report["locked"])
            self.assertFalse((repo / "new-state").exists())

    def test_concurrent_decisions_keep_both_history_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            bundle = self.syncer.build_bundle(self._write_exports(repo))
            bundle_path = self.syncer.write_bundle(repo, Path(".aipc/evidence.json"), bundle)
            self.ledger.initialize(repo)
            self.ledger.sync(
                repo,
                Path(".aipc/maintainer-ledger.json"),
                bundle_path.relative_to(repo.resolve()),
            )
            evidence_ids = [record["evidence_id"] for record in bundle["evidence"][:2]]
            start = threading.Barrier(3)

            def decide(evidence_id: str) -> dict[str, object]:
                start.wait(timeout=2)
                return self.ledger.decide(
                    repo,
                    Path(".aipc/maintainer-ledger.json"),
                    evidence_id,
                    "observe",
                    "resolved",
                    actor="parallel-maintainer",
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(decide, evidence_id) for evidence_id in evidence_ids]
                start.wait(timeout=2)
                events = [future.result(timeout=5) for future in futures]

            self.assertEqual({evidence_ids[0], evidence_ids[1]}, {event["evidence_id"] for event in events})
            raw = json.loads((repo / ".aipc" / "maintainer-ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(3, raw["revision"])
            self.assertTrue(all(len(raw["entries"][evidence_id]["history"]) == 1 for evidence_id in evidence_ids))

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

    def test_sync_uses_fallback_identifiers_and_rejects_duplicate_import_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            exports = Path(temp)
            issues_path = exports / "issues.json"
            issues_path.write_text(
                json.dumps(
                    [
                        {"number": None, "id": 101, "title": "First partial export", "state": "open"},
                        {"number": None, "id": 102, "title": "Second partial export", "state": "open"},
                    ]
                ),
                encoding="utf-8",
            )
            bundle = self.syncer.build_bundle(exports)
            self.assertEqual(["issue:101", "issue:102"], [record["source_id"] for record in bundle["evidence"]])
            self.assertEqual(2, len({record["evidence_id"] for record in bundle["evidence"]}))

            issues_path.write_text(
                json.dumps(
                    [
                        {"number": None, "id": 101, "title": "Original record", "state": "open"},
                        {"number": None, "id": 101, "title": "Duplicate record", "state": "open"},
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate evidence ID"):
                self.syncer.build_bundle(exports)

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
