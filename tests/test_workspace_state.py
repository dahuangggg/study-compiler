import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from study_compiler.doctor import inspect_vault
from study_compiler.digest import build_digest
from study_compiler.errors import StateError, ValidationError
from study_compiler.lessons import (
    create_draft,
    list_drafts,
    parse_lesson,
    promote_draft,
    record_recall,
    revise_draft,
)
from study_compiler.sources import add_snippet, add_url_source, set_source_coverage
from study_compiler.storage import initialize_vault


def lesson_body(*source_ids: str) -> str:
    cited_sources = "\n".join(f"- {source_id}" for source_id in source_ids)
    return f"""## Learning Objectives

Explain why attention mixes information across tokens.

## Intuition

Attention lets each token select useful context from other tokens.

## Example

In a pronoun-resolution example, the pronoun can attend to its antecedent.

## Explain It Back

Explain attention using an example of your own.

## Sources

{cited_sources}
"""


class WorkspaceStateContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp_dir.name) / "Learn"
        initialize_vault(self.vault)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @property
    def manifest_path(self) -> Path:
        return self.vault / ".study-compiler/manifest.json"

    @property
    def learning_state_path(self) -> Path:
        return self.vault / ".study-compiler/learning-state.json"

    def add_source(self, text: str = "Attention mixes relevant context.") -> dict:
        return add_snippet(
            self.vault,
            title="Attention explanation",
            text=text,
        )["source"]

    def create_lesson(self, source: dict, slug: str = "attention") -> None:
        create_draft(
            self.vault,
            slug=slug,
            title="Understanding Attention",
            source_ids=[source["id"]],
            body=lesson_body(source["id"]),
        )

    def assert_doctor_error(self, *fragments: str) -> None:
        report = inspect_vault(self.vault)
        self.assertFalse(report["ok"], report)
        rendered_errors = "\n".join(report["errors"]).lower()
        for fragment in fragments:
            self.assertIn(fragment.lower(), rendered_errors, report["errors"])

    def test_create_draft_keeps_manifest_and_source_metadata_identical(self) -> None:
        source = self.add_source()

        self.create_lesson(source)

        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        metadata_path = self.vault / "sources" / source["id"] / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["sources"][source["id"]], metadata)
        staged = self.vault / "_staging/lessons/attention.md"
        self.assertIn("format_version: 3", staged.read_text(encoding="utf-8"))

    def test_lesson_parser_rejects_ambiguous_relationship_schema_values(self) -> None:
        source = self.add_source()
        self.create_lesson(source)
        staged = self.vault / "_staging/lessons/attention.md"
        content = staged.read_text(encoding="utf-8")
        cases = (
            (
                content.replace("relationships: []", "relationships: null"),
                "relationships",
            ),
            (
                content.replace("format_version: 3", "format_version: 3.0"),
                "format_version",
            ),
            (
                content.replace(
                    "relationships: []", "relationships: []\nrelationships: []"
                ),
                "duplicate",
            ),
            (
                content.replace(
                    "relationships: []",
                    'relationships: [{"target": "[[lessons/one]]", '
                    '"target": "[[lessons/two]]", "type": "uses"}]',
                ),
                "duplicate",
            ),
            (
                content.replace("relationships: []", "custom: " + ("9" * 10_000)),
                "frontmatter",
            ),
            (
                content.replace("relationships: []", "custom: NaN"),
                "non-finite",
            ),
            (
                content.replace("relationships: []", "custom: 1e999"),
                "non-finite",
            ),
        )

        for malformed, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValidationError, message):
                    parse_lesson(malformed)

    def test_format_v2_still_requires_exact_source_citations(self) -> None:
        source = self.add_source()
        self.create_lesson(source)
        content = (self.vault / "_staging/lessons/attention.md").read_text(
            encoding="utf-8"
        )
        content = content.replace("format_version: 3", "format_version: 2")
        prefix, _, suffix = content.rpartition(source["id"])
        content = prefix + "missing-source-citation" + suffix

        with self.assertRaisesRegex(ValidationError, "Sources section"):
            parse_lesson(content)

    def test_doctor_detects_source_metadata_drift(self) -> None:
        source = self.add_source()
        metadata_path = self.vault / "sources" / source["id"] / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["title"] = "A title that exists only in metadata"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

        self.assert_doctor_error("metadata", "drift")

    def test_doctor_warns_for_legacy_mutable_metadata_drift(self) -> None:
        source = self.add_source()
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest_source = manifest["sources"][source["id"]]
        manifest_source.pop("coverage_status")
        manifest_source["status"] = "staged"
        manifest_source["lesson_slugs"] = ["legacy-lesson"]
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        metadata_path = self.vault / "sources" / source["id"] / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata.pop("coverage_status")
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

        report = inspect_vault(self.vault)

        self.assertTrue(report["ok"], report)
        self.assertIn("legacy", "\n".join(report["warnings"]).lower())

    def test_source_coverage_defaults_and_updates_in_both_records(self) -> None:
        source = self.add_source()
        self.assertEqual(source["coverage_status"], "pending")

        set_source_coverage(self.vault, source["id"], status="complete")

        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        metadata_path = self.vault / "sources" / source["id"] / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["sources"][source["id"]]["coverage_status"], "complete"
        )
        self.assertEqual(manifest["sources"][source["id"]], metadata)

        with self.assertRaisesRegex(ValidationError, "coverage"):
            set_source_coverage(self.vault, source["id"], status="finished")

    def test_doctor_detects_ghost_lesson_in_learning_state(self) -> None:
        source = self.add_source()
        state = json.loads(self.learning_state_path.read_text(encoding="utf-8"))
        state["lessons"]["ghost-lesson"] = {
            "status": "draft",
            "source_ids": [source["id"]],
            "mastery_score": None,
            "can_explain": False,
            "last_reviewed": None,
            "next_review": None,
        }
        self.learning_state_path.write_text(json.dumps(state), encoding="utf-8")

        self.assert_doctor_error("ghost", "lesson")

    def test_doctor_detects_invalid_lesson_status(self) -> None:
        source = self.add_source()
        self.create_lesson(source)
        state = json.loads(self.learning_state_path.read_text(encoding="utf-8"))
        state["lessons"]["attention"]["status"] = "not-a-status"
        self.learning_state_path.write_text(json.dumps(state), encoding="utf-8")

        self.assert_doctor_error("status")

    def test_doctor_detects_invalid_lesson_source_ids_type(self) -> None:
        source = self.add_source()
        self.create_lesson(source)
        state = json.loads(self.learning_state_path.read_text(encoding="utf-8"))
        state["lessons"]["attention"]["source_ids"] = source["id"]
        self.learning_state_path.write_text(json.dumps(state), encoding="utf-8")

        self.assert_doctor_error("source_ids")

    def test_doctor_detects_invalid_mastery_score_type(self) -> None:
        source = self.add_source()
        self.create_lesson(source)
        state = json.loads(self.learning_state_path.read_text(encoding="utf-8"))
        state["lessons"]["attention"]["mastery_score"] = "high"
        self.learning_state_path.write_text(json.dumps(state), encoding="utf-8")

        self.assert_doctor_error("mastery_score")

    def test_doctor_validates_learning_timestamps_and_prompt_state(self) -> None:
        source = self.add_source()
        self.create_lesson(source)
        state = json.loads(self.learning_state_path.read_text(encoding="utf-8"))
        lesson = state["lessons"]["attention"]
        lesson["last_reviewed"] = "not-an-iso-timestamp"
        lesson["next_review"] = "also-not-a-timestamp"
        lesson["awaiting_response"] = True
        lesson["prompted_at"] = None
        self.learning_state_path.write_text(json.dumps(state), encoding="utf-8")

        self.assert_doctor_error("last_reviewed", "next_review", "prompted_at")

    def test_planning_rejects_noncanonical_or_mistyped_learning_state(self) -> None:
        source = self.add_source()
        self.create_lesson(source)
        baseline = json.loads(self.learning_state_path.read_text(encoding="utf-8"))
        corruptions = (
            {"awaiting_response": "false"},
            {
                "mastery_score": 4,
                "can_explain": True,
                "last_reviewed": "2026-07-13T09:00:00+00:00",
                "next_review": "2026-07-20T09:00:00Z",
            },
        )

        for corruption in corruptions:
            with self.subTest(corruption=corruption):
                state = json.loads(json.dumps(baseline))
                state["lessons"]["attention"].update(corruption)
                self.learning_state_path.write_text(json.dumps(state), encoding="utf-8")

                with self.assertRaises(StateError):
                    build_digest(
                        self.vault,
                        period="daily",
                        now=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
                    )
                self.assertFalse(inspect_vault(self.vault)["ok"])

    def test_doctor_reconciles_latest_review_with_learning_state(self) -> None:
        source = self.add_source()
        self.create_lesson(source)
        from study_compiler.lessons import record_recall

        record_recall(
            self.vault,
            "attention",
            score=4,
            retelling="Each token gathers context relevant to its job.",
        )
        state = json.loads(self.learning_state_path.read_text(encoding="utf-8"))
        state["lessons"]["attention"]["mastery_score"] = 1
        self.learning_state_path.write_text(json.dumps(state), encoding="utf-8")

        self.assert_doctor_error("review", "mastery")

    def test_doctor_detects_review_history_that_moves_backwards(self) -> None:
        source = self.add_source()
        self.create_lesson(source)
        record_recall(
            self.vault,
            "attention",
            score=4,
            retelling="Tokens gather context relevant to their current job.",
            now=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
        )
        review_path = self.vault / "reviews/attention.jsonl"
        latest = json.loads(review_path.read_text(encoding="utf-8"))
        older = latest | {
            "reviewed_at": "2026-07-13T09:00:00Z",
            "next_review": "2026-07-20T09:00:00Z",
        }
        with review_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(older) + "\n")

        self.assert_doctor_error("chronological")

    def test_doctor_detects_deleted_review_history(self) -> None:
        source = self.add_source()
        self.create_lesson(source)
        record_recall(
            self.vault,
            "attention",
            score=4,
            retelling="Each token gathers context relevant to its job.",
        )
        (self.vault / "reviews/attention.jsonl").unlink()

        self.assert_doctor_error("review", "missing")

    def test_doctor_reconciles_source_lesson_links(self) -> None:
        source = self.add_source()
        self.create_lesson(source)
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["sources"][source["id"]].pop("lesson_slugs")
        manifest["sources"][source["id"]]["status"] = "new"
        manifest["sources"][source["id"]]["coverage_status"] = "pending"
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        metadata_path = self.vault / "sources" / source["id"] / "metadata.json"
        metadata_path.write_text(
            json.dumps(manifest["sources"][source["id"]]), encoding="utf-8"
        )

        self.assert_doctor_error("lesson", "link")

    def test_doctor_requires_a_snapshot_to_belong_to_its_source(self) -> None:
        first = self.add_source("First source bytes.")
        second = self.add_source("Second source bytes.")
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        first_record = manifest["sources"][first["id"]]
        second_record = manifest["sources"][second["id"]]
        for field in ("snapshot", "sha256", "size", "media_type"):
            first_record[field] = second_record[field]
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        metadata_path = self.vault / "sources" / first["id"] / "metadata.json"
        metadata_path.write_text(json.dumps(first_record), encoding="utf-8")

        self.assert_doctor_error("snapshot", "source directory")

    def test_create_draft_rejects_web_source_without_snapshot(self) -> None:
        source = add_url_source(
            self.vault,
            "https://example.com/attention",
            title="Attention on the web",
        )["source"]

        with self.assertRaisesRegex(ValidationError, "snapshot"):
            self.create_lesson(source)

    def test_create_draft_requires_every_source_id_in_sources_section(self) -> None:
        first = self.add_source("Attention combines context from other tokens.")
        second = self.add_source("Queries select relevant keys and values.")

        with self.assertRaisesRegex(ValidationError, second["id"]):
            create_draft(
                self.vault,
                slug="attention-evidence",
                title="Attention Evidence",
                source_ids=[first["id"], second["id"]],
                body=lesson_body(first["id"]),
            )

    def test_parse_and_revise_require_source_ids_in_sources_section(self) -> None:
        source = self.add_source()
        self.create_lesson(source)
        staged = self.vault / "_staging/lessons/attention.md"
        content = staged.read_text(encoding="utf-8")
        without_citation = lesson_body("chat-not-the-registered-source")

        with self.assertRaisesRegex(ValidationError, source["id"]):
            parse_lesson(content.replace(lesson_body(source["id"]), without_citation))
        with self.assertRaisesRegex(ValidationError, source["id"]):
            revise_draft(self.vault, "attention", body=without_citation)

    def test_sources_section_requires_an_exact_source_id_token(self) -> None:
        source = self.add_source()
        misleading_body = lesson_body(f"prefix{source['id']}suffix")

        with self.assertRaisesRegex(ValidationError, source["id"]):
            create_draft(
                self.vault,
                slug="exact-citation",
                title="Exact Citation",
                source_ids=[source["id"]],
                body=misleading_body,
            )

    def test_legacy_lesson_remains_readable_but_requires_revision_before_promotion(
        self,
    ) -> None:
        source = self.add_source()
        self.create_lesson(source)
        staged = self.vault / "_staging/lessons/attention.md"
        legacy = staged.read_text(encoding="utf-8").replace("format_version: 3\n", "")
        legacy = legacy.replace(
            f"- {source['id']}", "- legacy prose without an explicit ID"
        )
        staged.write_text(legacy, encoding="utf-8")

        drafts = list_drafts(self.vault)
        report = inspect_vault(self.vault)

        self.assertEqual(drafts[0]["format_version"], 1)
        self.assertTrue(report["ok"], report)
        self.assertIn("legacy", "\n".join(report["warnings"]).lower())
        with self.assertRaisesRegex(ValidationError, source["id"]):
            promote_draft(self.vault, "attention", approve=True)

    def test_promotion_keeps_partial_coverage_and_metadata_in_sync(self) -> None:
        source = self.add_source()
        self.create_lesson(source)

        promote_draft(self.vault, "attention", approve=True)

        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        source_record = manifest["sources"][source["id"]]
        metadata_path = self.vault / "sources" / source["id"] / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(source_record["coverage_status"], "partial")
        self.assertEqual(source_record, metadata)

    def test_workspace_lock_is_exclusive_and_reusable(self) -> None:
        from study_compiler.state import workspace_lock

        with workspace_lock(self.vault):
            with self.assertRaises(StateError):
                with workspace_lock(self.vault):
                    self.fail("a second writer acquired the workspace lock")

        with workspace_lock(self.vault):
            pass

    def test_digest_build_obeys_the_workspace_writer_lock(self) -> None:
        from study_compiler.state import workspace_lock

        self.add_source()
        with workspace_lock(self.vault):
            with self.assertRaisesRegex(StateError, "locked"):
                build_digest(self.vault)


if __name__ == "__main__":
    unittest.main()
