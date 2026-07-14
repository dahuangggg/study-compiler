import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from learning_wiki.digest import build_digest
from learning_wiki.doctor import inspect_vault
from learning_wiki.lessons import create_draft, promote_draft, record_recall
from learning_wiki.sources import add_local_source, add_snippet
from learning_wiki.storage import initialize_vault

from tests.test_lessons import VALID_BODY


class DigestAndDoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.vault = self.root / "Learn"
        initialize_vault(self.vault)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_digest_agenda_includes_new_staged_and_weak_learning_items(self) -> None:
        new_source = add_snippet(self.vault, title="Unprocessed", text="A new idea.")["source"]
        lesson_source = add_snippet(self.vault, title="Lesson", text="Attention uses context.")["source"]
        create_draft(
            self.vault,
            slug="attention",
            title="Attention",
            source_ids=[lesson_source["id"]],
            body=VALID_BODY,
        )
        record_recall(
            self.vault,
            "attention",
            score=2,
            retelling="I still confuse the query and the result.",
            now=datetime(2026, 7, 10, tzinfo=UTC),
        )

        result = build_digest(
            self.vault,
            period="weekly",
            now=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
        )

        digest = (self.vault / result["path"]).read_text(encoding="utf-8")
        self.assertIn(new_source["id"], digest)
        self.assertIn("attention", digest)
        self.assertIn("score 2", digest)
        self.assertEqual(result["counts"], {"new_sources": 1, "staged_lessons": 1, "review_items": 1})
        self.assertTrue(result["path"].startswith("_staging/digests/"))

    def test_doctor_passes_for_complete_learning_flow(self) -> None:
        source = add_snippet(self.vault, title="Lesson", text="Attention uses context.")["source"]
        create_draft(
            self.vault,
            slug="attention",
            title="Attention",
            source_ids=[source["id"]],
            body=VALID_BODY,
        )
        record_recall(self.vault, "attention", score=4, retelling="Tokens mix relevant context.")
        promote_draft(self.vault, "attention", approve=True)
        build_digest(self.vault, period="weekly")

        report = inspect_vault(self.vault)

        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["counts"]["approved_lessons"], 1)

    def test_doctor_detects_snapshot_hash_drift(self) -> None:
        document = self.root / "paper.txt"
        document.write_text("original", encoding="utf-8")
        source = add_local_source(self.vault, document, kind="paper")["source"]
        snapshot = self.vault / source["snapshot"]
        snapshot.chmod(0o644)
        snapshot.write_text("tampered", encoding="utf-8")

        report = inspect_vault(self.vault)

        self.assertFalse(report["ok"])
        self.assertTrue(any("hash drift" in error for error in report["errors"]))

    def test_doctor_reports_malformed_state_without_crashing(self) -> None:
        (self.vault / ".learning-wiki/learning-state.json").write_text("{broken", encoding="utf-8")

        report = inspect_vault(self.vault)

        self.assertFalse(report["ok"])
        self.assertTrue(any("learning-state.json" in error for error in report["errors"]))

    def test_doctor_detects_path_escape_and_stale_index(self) -> None:
        source = add_snippet(self.vault, title="Lesson", text="Attention uses context.")["source"]
        create_draft(
            self.vault,
            slug="attention",
            title="Attention",
            source_ids=[source["id"]],
            body=VALID_BODY,
        )
        promote_draft(self.vault, "attention", approve=True)
        manifest_path = self.vault / ".learning-wiki/manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["sources"][source["id"]]["snapshot"] = "../../outside.md"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        (self.vault / "index.md").write_text("# stale\n", encoding="utf-8")

        report = inspect_vault(self.vault)

        self.assertFalse(report["ok"])
        self.assertTrue(any("escapes the vault" in error for error in report["errors"]))
        self.assertTrue(any("index.md is stale" in error for error in report["errors"]))


if __name__ == "__main__":
    unittest.main()

