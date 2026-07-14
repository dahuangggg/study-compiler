import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from study_compiler import sources
from study_compiler.digest import build_digest, plan_digest
from study_compiler.doctor import inspect_vault
from study_compiler.errors import StateError
from study_compiler.lessons import create_draft, promote_draft, record_recall
from study_compiler.storage import initialize_vault


def valid_body(source_id: str) -> str:
    return f"""## Learning Objectives

Explain why attention mixes information across tokens.

## Intuition

Attention lets each token gather the context useful for its current job.

## Example

In “the animal was tired because it ran”, “it” can attend to “animal”.

## Explain It Back

Explain attention using your own example without using the word “weight”.

## Sources

- `{source_id}`
"""


class PlannerSemanticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.vault = self.root / "Learn"
        initialize_vault(self.vault)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _manifest_source(self, source_id: str) -> dict[str, object]:
        manifest = json.loads(
            (self.vault / ".study-compiler/manifest.json").read_text(encoding="utf-8")
        )
        return manifest["sources"][source_id]

    def test_new_sources_start_with_pending_coverage(self) -> None:
        document = self.root / "paper.txt"
        document.write_text("A local source.", encoding="utf-8")
        created = (
            sources.add_local_source(self.vault, document, kind="document"),
            sources.add_url_source(self.vault, "https://example.com/article"),
            sources.add_snippet(
                self.vault, title="Chat source", text="An agent explanation."
            ),
        )

        for result in created:
            source = result["source"]
            with self.subTest(source_id=source["id"]):
                self.assertEqual(source["coverage_status"], "pending")
                self.assertEqual(
                    self._manifest_source(source["id"])["coverage_status"],
                    "pending",
                )

    def test_drafting_makes_coverage_partial_and_promotion_keeps_it_partial(
        self,
    ) -> None:
        source = sources.add_snippet(
            self.vault,
            title="Attention source",
            text="Attention lets a token combine context from other tokens.",
        )["source"]

        create_draft(
            self.vault,
            slug="attention",
            title="Attention",
            source_ids=[source["id"]],
            body=valid_body(source["id"]),
        )

        self.assertEqual(
            self._manifest_source(source["id"])["coverage_status"], "partial"
        )

        promote_draft(self.vault, "attention", approve=True)

        self.assertEqual(
            self._manifest_source(source["id"])["coverage_status"], "partial"
        )

    def test_complete_source_coverage_removes_source_from_digest(self) -> None:
        source = sources.add_snippet(
            self.vault,
            title="Attention source",
            text="Attention lets a token combine context from other tokens.",
        )["source"]
        create_draft(
            self.vault,
            slug="attention",
            title="Attention",
            source_ids=[source["id"]],
            body=valid_body(source["id"]),
        )

        sources.set_source_coverage(self.vault, source["id"], status="complete")
        result = build_digest(
            self.vault,
            period="weekly",
            now=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
        )

        digest = (self.vault / result["path"]).read_text(encoding="utf-8")
        source_items = [item for item in result["items"] if item["type"] == "source"]
        self.assertEqual(
            self._manifest_source(source["id"])["coverage_status"], "complete"
        )
        self.assertNotIn(source["id"], [item["id"] for item in source_items])
        self.assertNotIn(f"- `{source['id']}`", digest)

    def test_unsnapshotted_web_source_is_a_snapshot_action_not_a_lesson_source(
        self,
    ) -> None:
        source = sources.add_url_source(
            self.vault,
            "https://example.com/article",
            title="Web article",
        )["source"]

        result = build_digest(
            self.vault,
            period="daily",
            now=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
        )

        self.assertEqual(
            result["items"],
            [
                {
                    "type": "source_snapshot",
                    "id": source["id"],
                    "title": "Web article",
                    "kind": "web",
                    "url": "https://example.com/article",
                }
            ],
        )
        self.assertEqual(result["next_action"], result["items"][0])
        digest = (self.vault / result["path"]).read_text(encoding="utf-8")
        snapshot_section = digest.split("## Sources Needing Snapshot\n\n", 1)[1].split(
            "\n\n## New Sources", 1
        )[0]
        new_source_section = digest.split("## New Sources\n\n", 1)[1].split(
            "\n\n## Staged Lessons", 1
        )[0]
        self.assertIn(source["id"], snapshot_section)
        self.assertNotIn(source["id"], new_source_section)

    def test_digest_period_controls_upcoming_review_horizon(self) -> None:
        source = sources.add_snippet(
            self.vault,
            title="Attention source",
            text="Attention lets a token combine context from other tokens.",
        )["source"]
        create_draft(
            self.vault,
            slug="attention",
            title="Attention",
            source_ids=[source["id"]],
            body=valid_body(source["id"]),
        )
        record_recall(
            self.vault,
            "attention",
            score=4,
            retelling="Each token gathers the context relevant to its current job.",
            now=datetime(2026, 7, 13, 9, 0, tzinfo=UTC),
        )
        digest_now = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)

        daily = build_digest(self.vault, period="daily", now=digest_now)
        weekly = build_digest(self.vault, period="weekly", now=digest_now)
        monthly = build_digest(self.vault, period="monthly", now=digest_now)

        def review_section(result: dict[str, object]) -> str:
            digest = (self.vault / result["path"]).read_text(encoding="utf-8")
            return digest.split("## Review Due\n\n", 1)[1].split(
                "\n\n## Agent Handoff", 1
            )[0]

        self.assertNotIn("attention", review_section(daily))
        self.assertIn("attention", review_section(weekly))
        self.assertIn("attention", review_section(monthly))

    def test_digest_returns_additive_prioritized_work_items(self) -> None:
        pending_source = sources.add_snippet(
            self.vault, title="Pending source", text="An unprocessed concept."
        )["source"]
        lesson_source = sources.add_snippet(
            self.vault,
            title="Attention source",
            text="Attention lets a token combine context from other tokens.",
        )["source"]
        create_draft(
            self.vault,
            slug="attention",
            title="Attention",
            source_ids=[lesson_source["id"]],
            body=valid_body(lesson_source["id"]),
        )
        record_recall(
            self.vault,
            "attention",
            score=2,
            retelling="I still confuse the query with the result.",
            now=datetime(2026, 7, 14, 8, 0, tzinfo=UTC),
        )

        result = build_digest(
            self.vault,
            period="weekly",
            now=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
        )

        self.assertIn("path", result)
        self.assertEqual(result["period"], "weekly")
        self.assertIn("counts", result)
        self.assertIsInstance(result["items"], list)
        self.assertEqual(result["next_action"], result["items"][0])
        self.assertEqual(
            [item["type"] for item in result["items"]],
            ["review", "staged_lesson", "source", "source"],
        )
        self.assertEqual(result["items"][0]["id"], "attention")
        self.assertEqual(
            {item["id"] for item in result["items"] if item["type"] == "source"},
            {pending_source["id"], lesson_source["id"]},
        )

    def test_planner_rejects_invalid_source_coverage(self) -> None:
        source = sources.add_snippet(
            self.vault, title="Source", text="A source with corrupt coverage."
        )["source"]
        source["coverage_status"] = "unknown"

        with self.assertRaisesRegex(StateError, "coverage"):
            plan_digest(
                sources={source["id"]: source},
                lessons={},
                staged_lessons=[],
                period="daily",
                now=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
            )

    def test_planner_rejects_invalid_learning_state_records(self) -> None:
        with self.assertRaisesRegex(StateError, "learning[- ]state"):
            plan_digest(
                sources={},
                lessons={"broken": "not-a-record"},
                staged_lessons=[],
                period="daily",
                now=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
            )

    def test_missing_web_origin_fails_doctor_and_planning_cleanly(self) -> None:
        source = sources.add_url_source(
            self.vault,
            "https://example.com/article",
            title="Web article",
        )["source"]
        manifest_path = self.vault / ".study-compiler/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["sources"][source["id"]].pop("origin")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        metadata_path = self.vault / "sources" / source["id"] / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata.pop("origin")
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

        report = inspect_vault(self.vault)
        self.assertFalse(report["ok"], report)
        self.assertIn("origin", "\n".join(report["errors"]).lower())
        with self.assertRaisesRegex(StateError, "origin"):
            build_digest(
                self.vault,
                period="daily",
                now=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
            )


if __name__ == "__main__":
    unittest.main()
