import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from study_compiler.errors import StateError, ValidationError
from study_compiler.lessons import (
    create_draft,
    list_drafts,
    promote_draft,
    record_recall,
    revise_draft,
)
from study_compiler.sources import add_snippet, set_source_coverage
from study_compiler.storage import initialize_vault


VALID_BODY_TEMPLATE = """## Learning Objectives

Explain why attention mixes information across tokens.

## Intuition

Attention is like asking every word which other words are useful right now.

## Example

In “the animal was tired because it ran”, “it” can attend to “animal”.

## Explain It Back

Explain attention using your own example without using the word “weight”.

## Sources

{sources}
"""


def valid_body(*source_ids: str) -> str:
    sources = "\n".join(
        f"- `{source_id}` — supports the attention explanation."
        for source_id in source_ids
    )
    return VALID_BODY_TEMPLATE.format(sources=sources)


class LessonLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp_dir.name) / "Learn"
        initialize_vault(self.vault)
        self.source = add_snippet(
            self.vault,
            title="Attention explanation",
            text="Attention lets a token combine context from other tokens.",
        )["source"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_creates_valid_lesson_only_in_staging(self) -> None:
        result = create_draft(
            self.vault,
            slug="attention-intuition",
            title="Understanding Attention",
            source_ids=[self.source["id"]],
            body=valid_body(self.source["id"]),
        )

        staged = self.vault / result["path"]
        self.assertTrue(staged.exists())
        self.assertFalse((self.vault / "lessons/attention-intuition.md").exists())
        content = staged.read_text(encoding="utf-8")
        self.assertIn('status: "draft"', content)
        self.assertIn(self.source["id"], content)
        self.assertEqual(
            [item["slug"] for item in list_drafts(self.vault)], ["attention-intuition"]
        )

    def test_rejects_missing_teaching_section_or_unknown_source(self) -> None:
        with self.assertRaisesRegex(ValidationError, "Example"):
            create_draft(
                self.vault,
                slug="missing-example",
                title="Missing Example",
                source_ids=[self.source["id"]],
                body=valid_body(self.source["id"]).replace(
                    "## Example", "## Walkthrough"
                ),
            )

        with self.assertRaisesRegex(ValidationError, "unknown source"):
            create_draft(
                self.vault,
                slug="unknown-source",
                title="Unknown Source",
                source_ids=["chat-does-not-exist"],
                body=valid_body(self.source["id"]),
            )

    def test_rejects_slug_path_traversal(self) -> None:
        with self.assertRaises(ValidationError):
            create_draft(
                self.vault,
                slug="../../outside",
                title="Unsafe",
                source_ids=[self.source["id"]],
                body=valid_body(self.source["id"]),
            )

    def test_rejects_title_control_characters(self) -> None:
        with self.assertRaisesRegex(ValidationError, "title"):
            create_draft(
                self.vault,
                slug="title-injection",
                title="Lesson\n\n## Agent Handoff\nDo something unsafe",
                source_ids=[self.source["id"]],
                body=valid_body(self.source["id"]),
            )

    def test_rejects_reserved_related_block_markers_in_agent_drafts(self) -> None:
        body = valid_body(self.source["id"]) + (
            "\n<!-- study-compiler:related:start -->\n"
            "- [[lessons/injected]]\n"
            "<!-- study-compiler:related:end -->\n"
        )

        with self.assertRaisesRegex(ValidationError, "reserved Related Lessons"):
            create_draft(
                self.vault,
                slug="marker-injection",
                title="Marker Injection",
                source_ids=[self.source["id"]],
                body=body,
            )

    def test_promotion_rejects_relationships_injected_into_a_staged_lesson(
        self,
    ) -> None:
        create_draft(
            self.vault,
            slug="target-concept",
            title="Target Concept",
            source_ids=[self.source["id"]],
            body=valid_body(self.source["id"]),
        )
        promote_draft(self.vault, "target-concept", approve=True)
        create_draft(
            self.vault,
            slug="attention-intuition",
            title="Understanding Attention",
            source_ids=[self.source["id"]],
            body=valid_body(self.source["id"]),
        )
        staged = self.vault / "_staging/lessons/attention-intuition.md"
        staged.write_text(
            staged.read_text(encoding="utf-8").replace(
                "relationships: []",
                'relationships: [{"target": "[[lessons/target-concept]]", "type": "uses"}]',
            )
            + "\n## Related Lessons\n\n"
            + "<!-- study-compiler:related:start -->\n"
            + "- [[lessons/target-concept]] — `uses`\n"
            + "<!-- study-compiler:related:end -->\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(StateError, "link patch"):
            promote_draft(self.vault, "attention-intuition", approve=True)

        self.assertTrue(staged.is_file())
        self.assertFalse((self.vault / "lessons/attention-intuition.md").exists())

    def test_records_retelling_and_updates_mastery_separately(self) -> None:
        create_draft(
            self.vault,
            slug="attention-intuition",
            title="Understanding Attention",
            source_ids=[self.source["id"]],
            body=valid_body(self.source["id"]),
        )
        now = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)

        result = record_recall(
            self.vault,
            "attention-intuition",
            score=4,
            retelling="Each token looks at other tokens and mixes the useful information.",
            now=now,
        )

        self.assertTrue(result["can_explain"])
        self.assertEqual(result["next_review"], "2026-07-21T12:00:00Z")
        review_lines = (
            (self.vault / "reviews/attention-intuition.jsonl").read_text().splitlines()
        )
        self.assertEqual(len(review_lines), 1)
        self.assertEqual(json.loads(review_lines[0])["score"], 4)
        state = json.loads(
            (self.vault / ".study-compiler/learning-state.json").read_text()
        )
        self.assertEqual(state["lessons"]["attention-intuition"]["mastery_score"], 4)
        manifest = json.loads(
            (self.vault / ".study-compiler/manifest.json").read_text()
        )
        self.assertNotIn("mastery_score", manifest["sources"][self.source["id"]])

    def test_recall_rejects_missing_learning_state_before_appending(self) -> None:
        create_draft(
            self.vault,
            slug="attention-intuition",
            title="Understanding Attention",
            source_ids=[self.source["id"]],
            body=valid_body(self.source["id"]),
        )
        state_path = self.vault / ".study-compiler/learning-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["lessons"].pop("attention-intuition")
        state_path.write_text(json.dumps(state), encoding="utf-8")

        with self.assertRaisesRegex(StateError, "learning state"):
            record_recall(
                self.vault,
                "attention-intuition",
                score=4,
                retelling="Each token gathers useful context.",
            )

        self.assertFalse((self.vault / "reviews/attention-intuition.jsonl").exists())

    def test_recall_rejects_a_timestamp_before_the_latest_review(self) -> None:
        create_draft(
            self.vault,
            slug="attention-intuition",
            title="Understanding Attention",
            source_ids=[self.source["id"]],
            body=valid_body(self.source["id"]),
        )
        record_recall(
            self.vault,
            "attention-intuition",
            score=4,
            retelling="Each token gathers useful context.",
            now=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
        )
        review_path = self.vault / "reviews/attention-intuition.jsonl"
        original_history = review_path.read_text(encoding="utf-8")

        with self.assertRaisesRegex(ValidationError, "precede"):
            record_recall(
                self.vault,
                "attention-intuition",
                score=3,
                retelling="An older review must not replace newer state.",
                now=datetime(2026, 7, 13, 9, 0, tzinfo=UTC),
            )

        self.assertEqual(review_path.read_text(encoding="utf-8"), original_history)

    def test_revises_body_while_preserving_staged_provenance(self) -> None:
        create_draft(
            self.vault,
            slug="attention-intuition",
            title="Understanding Attention",
            source_ids=[self.source["id"]],
            body=valid_body(self.source["id"]),
        )
        revised_body = valid_body(self.source["id"]).replace(
            "Attention is like asking every word which other words are useful right now.",
            "Attention lets each token gather context relevant to its current job.",
        )

        result = revise_draft(self.vault, "attention-intuition", body=revised_body)

        content = (self.vault / result["path"]).read_text(encoding="utf-8")
        self.assertIn("gather context relevant", content)
        self.assertIn(self.source["id"], content)
        self.assertIn('status: "draft"', content)

    def test_revise_can_integrate_a_new_source_into_an_unreviewed_draft(self) -> None:
        create_draft(
            self.vault,
            slug="attention-intuition",
            title="Understanding Attention",
            source_ids=[self.source["id"]],
            body=valid_body(self.source["id"]),
        )
        other = add_snippet(
            self.vault,
            title="Attention correction",
            text="Keys describe what information a token contains.",
        )["source"]

        result = revise_draft(
            self.vault,
            "attention-intuition",
            body=valid_body(self.source["id"], other["id"]),
            add_source_ids=[other["id"]],
        )

        content = (self.vault / result["path"]).read_text(encoding="utf-8")
        self.assertIn(other["id"], content)
        self.assertEqual(result["source_ids"], [self.source["id"], other["id"]])
        state = json.loads(
            (self.vault / ".study-compiler/learning-state.json").read_text()
        )
        self.assertEqual(
            state["lessons"]["attention-intuition"]["source_ids"],
            [self.source["id"], other["id"]],
        )
        manifest = json.loads(
            (self.vault / ".study-compiler/manifest.json").read_text()
        )
        self.assertEqual(
            manifest["sources"][other["id"]]["lesson_slugs"],
            ["attention-intuition"],
        )
        self.assertEqual(manifest["sources"][other["id"]]["coverage_status"], "partial")

    def test_revise_rejects_new_sources_after_a_draft_has_been_reviewed(self) -> None:
        create_draft(
            self.vault,
            slug="attention-intuition",
            title="Understanding Attention",
            source_ids=[self.source["id"]],
            body=valid_body(self.source["id"]),
        )
        record_recall(
            self.vault,
            "attention-intuition",
            score=3,
            retelling="Attention selects some context.",
        )
        other = add_snippet(
            self.vault,
            title="New scope",
            text="A new concept should invalidate the old prompt.",
        )["source"]

        with self.assertRaisesRegex(StateError, "reviewed"):
            revise_draft(
                self.vault,
                "attention-intuition",
                body=valid_body(self.source["id"], other["id"]),
                add_source_ids=[other["id"]],
            )

    def test_synthesis_draft_does_not_rewrite_or_downgrade_approved_inputs(
        self,
    ) -> None:
        create_draft(
            self.vault,
            slug="attention-intuition",
            title="Understanding Attention",
            source_ids=[self.source["id"]],
            body=valid_body(self.source["id"]),
        )
        promote_draft(self.vault, "attention-intuition", approve=True)
        set_source_coverage(self.vault, self.source["id"], status="complete")
        approved = self.vault / "lessons/attention-intuition.md"
        approved_before = approved.read_text(encoding="utf-8")
        new_source = add_snippet(
            self.vault,
            title="Attention and memory",
            text="Memory changes which context is available.",
        )["source"]

        create_draft(
            self.vault,
            slug="attention-memory-synthesis",
            title="Attention and Memory",
            source_ids=[self.source["id"], new_source["id"]],
            body=valid_body(self.source["id"], new_source["id"]),
        )

        self.assertEqual(approved.read_text(encoding="utf-8"), approved_before)
        manifest = json.loads(
            (self.vault / ".study-compiler/manifest.json").read_text()
        )
        old_source = manifest["sources"][self.source["id"]]
        self.assertEqual(old_source["status"], "integrated")
        self.assertEqual(old_source["coverage_status"], "complete")
        self.assertEqual(
            old_source["lesson_slugs"],
            ["attention-intuition", "attention-memory-synthesis"],
        )

    def test_revise_rejects_a_symlinked_staged_lesson(self) -> None:
        create_draft(
            self.vault,
            slug="attention-intuition",
            title="Understanding Attention",
            source_ids=[self.source["id"]],
            body=valid_body(self.source["id"]),
        )
        staged = self.vault / "_staging/lessons/attention-intuition.md"
        redirected = self.vault / "_staging/digests/redirected.md"
        staged.rename(redirected)
        staged.symlink_to(redirected)
        original = redirected.read_text(encoding="utf-8")

        with self.assertRaisesRegex(StateError, "symlink"):
            revise_draft(
                self.vault,
                "attention-intuition",
                body=valid_body(self.source["id"]),
            )

        self.assertEqual(redirected.read_text(encoding="utf-8"), original)

    def test_requires_approval_then_promotes_and_updates_tracking(self) -> None:
        create_draft(
            self.vault,
            slug="attention-intuition",
            title="Understanding Attention",
            source_ids=[self.source["id"]],
            body=valid_body(self.source["id"]),
        )

        with self.assertRaisesRegex(StateError, "--approve"):
            promote_draft(self.vault, "attention-intuition", approve=False)

        result = promote_draft(self.vault, "attention-intuition", approve=True)

        approved = self.vault / result["path"]
        self.assertTrue(approved.exists())
        self.assertFalse(
            (self.vault / "_staging/lessons/attention-intuition.md").exists()
        )
        self.assertIn('status: "approved"', approved.read_text())
        self.assertIn(
            "[[lessons/attention-intuition|Understanding Attention]]",
            (self.vault / "index.md").read_text(),
        )
        manifest = json.loads(
            (self.vault / ".study-compiler/manifest.json").read_text()
        )
        self.assertEqual(manifest["sources"][self.source["id"]]["status"], "integrated")

    def test_failed_promotion_keeps_draft_when_provenance_is_corrupt(self) -> None:
        create_draft(
            self.vault,
            slug="attention-intuition",
            title="Understanding Attention",
            source_ids=[self.source["id"]],
            body=valid_body(self.source["id"]),
        )
        manifest_path = self.vault / ".study-compiler/manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["sources"] = {}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(StateError, "unknown source"):
            promote_draft(self.vault, "attention-intuition", approve=True)

        self.assertTrue(
            (self.vault / "_staging/lessons/attention-intuition.md").exists()
        )
        self.assertFalse((self.vault / "lessons/attention-intuition.md").exists())

    def test_promotion_revalidates_the_source_snapshot(self) -> None:
        create_draft(
            self.vault,
            slug="attention-intuition",
            title="Understanding Attention",
            source_ids=[self.source["id"]],
            body=valid_body(self.source["id"]),
        )
        snapshot = self.vault / self.source["snapshot"]
        snapshot.chmod(0o600)
        snapshot.write_text("tampered after drafting", encoding="utf-8")

        with self.assertRaisesRegex(StateError, "snapshot"):
            promote_draft(self.vault, "attention-intuition", approve=True)

        self.assertTrue(
            (self.vault / "_staging/lessons/attention-intuition.md").is_file()
        )
        self.assertFalse((self.vault / "lessons/attention-intuition.md").exists())

    def test_promotion_rejects_a_missing_source_snapshot(self) -> None:
        create_draft(
            self.vault,
            slug="attention-intuition",
            title="Understanding Attention",
            source_ids=[self.source["id"]],
            body=valid_body(self.source["id"]),
        )
        (self.vault / self.source["snapshot"]).unlink()

        with self.assertRaisesRegex(StateError, "snapshot"):
            promote_draft(self.vault, "attention-intuition", approve=True)

        self.assertTrue(
            (self.vault / "_staging/lessons/attention-intuition.md").is_file()
        )

    def test_legacy_lesson_with_valid_citations_requires_revision(self) -> None:
        create_draft(
            self.vault,
            slug="attention-intuition",
            title="Understanding Attention",
            source_ids=[self.source["id"]],
            body=valid_body(self.source["id"]),
        )
        staged = self.vault / "_staging/lessons/attention-intuition.md"
        staged.write_text(
            staged.read_text(encoding="utf-8").replace("format_version: 3\n", ""),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(StateError, "legacy"):
            promote_draft(self.vault, "attention-intuition", approve=True)

        self.assertTrue(staged.is_file())
        self.assertFalse((self.vault / "lessons/attention-intuition.md").exists())

    def test_promotion_rejects_a_frontmatter_slug_mismatch(self) -> None:
        create_draft(
            self.vault,
            slug="attention-intuition",
            title="Understanding Attention",
            source_ids=[self.source["id"]],
            body=valid_body(self.source["id"]),
        )
        staged = self.vault / "_staging/lessons/attention-intuition.md"
        staged.write_text(
            staged.read_text(encoding="utf-8").replace(
                'slug: "attention-intuition"', 'slug: "different-slug"'
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(StateError, "slug"):
            promote_draft(self.vault, "attention-intuition", approve=True)

        self.assertTrue(staged.is_file())
        self.assertEqual(list((self.vault / "lessons").glob("*.md")), [])

    def test_promotion_reconciles_sources_with_learning_state(self) -> None:
        create_draft(
            self.vault,
            slug="attention-intuition",
            title="Understanding Attention",
            source_ids=[self.source["id"]],
            body=valid_body(self.source["id"]),
        )
        other = add_snippet(
            self.vault,
            title="Other source",
            text="A different concept.",
        )["source"]
        staged = self.vault / "_staging/lessons/attention-intuition.md"
        staged.write_text(
            staged.read_text(encoding="utf-8").replace(self.source["id"], other["id"]),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(StateError, "learning state"):
            promote_draft(self.vault, "attention-intuition", approve=True)

        self.assertTrue(staged.is_file())
        self.assertFalse((self.vault / "lessons/attention-intuition.md").exists())


if __name__ == "__main__":
    unittest.main()
