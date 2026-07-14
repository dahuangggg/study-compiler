import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from learning_wiki.errors import StateError, ValidationError
from learning_wiki.lessons import (
    create_draft,
    list_drafts,
    promote_draft,
    record_recall,
    revise_draft,
)
from learning_wiki.sources import add_snippet
from learning_wiki.storage import initialize_vault


VALID_BODY = """## Learning Objectives

Explain why attention mixes information across tokens.

## Intuition

Attention is like asking every word which other words are useful right now.

## Example

In “the animal was tired because it ran”, “it” can attend to “animal”.

## Explain It Back

Explain attention using your own example without using the word “weight”.

## Sources

Grounded in the registered chat excerpt.
"""


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
            body=VALID_BODY,
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
                body=VALID_BODY.replace("## Example", "## Walkthrough"),
            )

        with self.assertRaisesRegex(ValidationError, "unknown source"):
            create_draft(
                self.vault,
                slug="unknown-source",
                title="Unknown Source",
                source_ids=["chat-does-not-exist"],
                body=VALID_BODY,
            )

    def test_rejects_slug_path_traversal(self) -> None:
        with self.assertRaises(ValidationError):
            create_draft(
                self.vault,
                slug="../../outside",
                title="Unsafe",
                source_ids=[self.source["id"]],
                body=VALID_BODY,
            )

    def test_records_retelling_and_updates_mastery_separately(self) -> None:
        create_draft(
            self.vault,
            slug="attention-intuition",
            title="Understanding Attention",
            source_ids=[self.source["id"]],
            body=VALID_BODY,
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
            (self.vault / ".learning-wiki/learning-state.json").read_text()
        )
        self.assertEqual(state["lessons"]["attention-intuition"]["mastery_score"], 4)
        manifest = json.loads((self.vault / ".learning-wiki/manifest.json").read_text())
        self.assertNotIn("mastery_score", manifest["sources"][self.source["id"]])

    def test_revises_body_while_preserving_staged_provenance(self) -> None:
        create_draft(
            self.vault,
            slug="attention-intuition",
            title="Understanding Attention",
            source_ids=[self.source["id"]],
            body=VALID_BODY,
        )
        revised_body = VALID_BODY.replace(
            "Attention is like asking every word which other words are useful right now.",
            "Attention lets each token gather context relevant to its current job.",
        )

        result = revise_draft(self.vault, "attention-intuition", body=revised_body)

        content = (self.vault / result["path"]).read_text(encoding="utf-8")
        self.assertIn("gather context relevant", content)
        self.assertIn(self.source["id"], content)
        self.assertIn('status: "draft"', content)

    def test_requires_approval_then_promotes_and_updates_tracking(self) -> None:
        create_draft(
            self.vault,
            slug="attention-intuition",
            title="Understanding Attention",
            source_ids=[self.source["id"]],
            body=VALID_BODY,
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
        manifest = json.loads((self.vault / ".learning-wiki/manifest.json").read_text())
        self.assertEqual(manifest["sources"][self.source["id"]]["status"], "integrated")

    def test_failed_promotion_keeps_draft_when_provenance_is_corrupt(self) -> None:
        create_draft(
            self.vault,
            slug="attention-intuition",
            title="Understanding Attention",
            source_ids=[self.source["id"]],
            body=VALID_BODY,
        )
        manifest_path = self.vault / ".learning-wiki/manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["sources"] = {}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(StateError, "unknown source"):
            promote_draft(self.vault, "attention-intuition", approve=True)

        self.assertTrue(
            (self.vault / "_staging/lessons/attention-intuition.md").exists()
        )
        self.assertFalse((self.vault / "lessons/attention-intuition.md").exists())


if __name__ == "__main__":
    unittest.main()
