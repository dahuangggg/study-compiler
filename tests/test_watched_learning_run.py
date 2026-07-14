import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from study_compiler.doctor import inspect_vault
from study_compiler.errors import StateError, ValidationError
from study_compiler.lessons import (
    create_draft,
    promote_draft,
    record_recall,
    revise_draft,
)
from study_compiler.runs import fail_run, finish_run, start_run, workspace_status
from study_compiler.source_watch import configure_source_roots, sync_next_source_change
from study_compiler.sources import add_snippet, set_source_coverage
from study_compiler.storage import initialize_vault
from tests.test_lessons import valid_body


class WatchedLearningRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.workspace = self.root / "Learn"
        self.inbox = self.root / "Notes"
        self.inbox.mkdir()
        initialize_vault(self.workspace)
        configure_source_roots(self.workspace, [self.inbox])

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _artifact(
        self,
        started: dict,
        slug: str,
        *,
        include_cluster: bool = True,
        integration_mode: str = "create",
        members: list[str] | None = None,
    ) -> Path:
        action = started["next_action"]
        path = self.workspace / started["artifact_path"]
        lines = [
            "---",
            f"run_id: {json.dumps(started['run_id'])}",
            f"action_type: {json.dumps(action['type'])}",
            f"action_id: {json.dumps(action['id'])}",
        ]
        if include_cluster:
            lines.extend(
                [
                    'artifact_kind: "review_cluster"',
                    f"integration_mode: {json.dumps(integration_mode)}",
                    f"trigger_source_ids: {json.dumps([action['id']])}",
                    f"staged_slugs: {json.dumps([slug])}",
                    f"member_slugs: {json.dumps(members or [slug])}",
                ]
            )
        lines.extend(
            [
                "---",
                "",
                "# Learning Session",
                "",
                "## Result",
                "",
                "A staged lesson was created.",
                "",
                "## What Changed",
                "",
                "The watched fragment introduced one new claim.",
                "",
                "## Knowledge Connections",
                "",
                "This is the first lesson in the cluster.",
                "",
                "## Integrated Example",
                "",
                "Use the claim in one concrete case.",
                "",
                "## Explain It Back",
                "",
                "Explain the claim and transfer it to a new case.",
                "",
                "## Member Rubric",
                "",
                f"- `{slug}`: explain the mechanism and boundary.",
                "",
                "## Staged Artifacts",
                "",
                f"- `_staging/lessons/{slug}.md`",
                "",
                "## Next Learner Action",
                "",
                "Answer the joint explain-it-back prompt.",
                "",
            ]
        )
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def test_run_start_automatically_snapshots_a_new_watched_file(self) -> None:
        source_path = self.inbox / "paper.md"
        source_path.write_text("A paper fragment about attention.\n", encoding="utf-8")
        before = workspace_status(self.workspace)

        started = start_run(self.workspace)

        self.assertEqual(len(before["source_changes"]["new"]), 1)
        self.assertEqual(started["next_action"]["type"], "source")
        self.assertTrue(started["next_action"]["watched"])
        self.assertEqual(started["next_action"]["change"], "new")
        self.assertEqual(started["next_action"]["path"], str(source_path.resolve()))
        self.assertEqual(workspace_status(self.workspace)["source_changes"]["new"], [])

    def test_watched_source_finish_requires_and_records_a_review_cluster(self) -> None:
        source_path = self.inbox / "conversation.md"
        source_path.write_text("A useful dialogue fragment.\n", encoding="utf-8")
        started = start_run(self.workspace)
        source_id = started["next_action"]["id"]
        create_draft(
            self.workspace,
            slug="dialogue-insight",
            title="Dialogue Insight",
            source_ids=[source_id],
            body=valid_body(source_id),
        )

        with self.assertRaisesRegex(ValidationError, "review cluster"):
            finish_run(
                self.workspace,
                started["run_id"],
                self._artifact(started, "dialogue-insight", include_cluster=False),
            )

        artifact = self._artifact(started, "dialogue-insight")
        completed = finish_run(self.workspace, started["run_id"], artifact)

        self.assertEqual(
            completed["review_cluster"]["member_slugs"], ["dialogue-insight"]
        )
        state = json.loads(
            (self.workspace / ".study-compiler/learning-state.json").read_text()
        )
        self.assertTrue(state["lessons"]["dialogue-insight"]["awaiting_response"])

    def test_doctor_cross_checks_completed_cluster_members_and_staged_results(
        self,
    ) -> None:
        source_path = self.inbox / "cluster.md"
        source_path.write_text("A connected fragment.\n", encoding="utf-8")
        started = start_run(self.workspace)
        source_id = started["next_action"]["id"]
        create_draft(
            self.workspace,
            slug="cluster-result",
            title="Cluster Result",
            source_ids=[source_id],
            body=valid_body(source_id),
        )
        unrelated_source = add_snippet(
            self.workspace,
            title="Unrelated",
            text="An unrelated concept.",
        )["source"]
        create_draft(
            self.workspace,
            slug="unrelated-draft",
            title="Unrelated Draft",
            source_ids=[unrelated_source["id"]],
            body=valid_body(unrelated_source["id"]),
        )
        completed = finish_run(
            self.workspace,
            started["run_id"],
            self._artifact(
                started,
                "cluster-result",
                members=["cluster-result", "unrelated-draft"],
            ),
        )
        run_path = (
            self.workspace / ".study-compiler/runs" / f"{completed['run_id']}.json"
        )
        run_record = json.loads(run_path.read_text(encoding="utf-8"))

        run_record["review_cluster"]["member_slugs"].append("missing-member")
        run_path.write_text(json.dumps(run_record), encoding="utf-8")
        report = inspect_vault(self.workspace)
        self.assertFalse(report["ok"])
        self.assertIn("unknown member", "\n".join(report["errors"]))

        run_record["review_cluster"]["member_slugs"] = [
            "cluster-result",
            "unrelated-draft",
        ]
        run_record["review_cluster"]["staged_slugs"] = ["unrelated-draft"]
        run_path.write_text(json.dumps(run_record), encoding="utf-8")
        report = inspect_vault(self.workspace)
        self.assertFalse(report["ok"])
        self.assertIn("does not cite", "\n".join(report["errors"]))

        (self.workspace / "_staging/lessons/unrelated-draft.md").unlink()
        report = inspect_vault(self.workspace)
        self.assertFalse(report["ok"])
        self.assertIn("staged lesson is missing", "\n".join(report["errors"]))

    def test_doctor_requires_latest_cluster_prompt_or_a_later_recall(self) -> None:
        source_path = self.inbox / "prompt.md"
        source_path.write_text("A fragment to retrieve.\n", encoding="utf-8")
        started = start_run(self.workspace)
        source_id = started["next_action"]["id"]
        create_draft(
            self.workspace,
            slug="prompted-result",
            title="Prompted Result",
            source_ids=[source_id],
            body=valid_body(source_id),
        )
        completed = finish_run(
            self.workspace,
            started["run_id"],
            self._artifact(started, "prompted-result"),
        )
        state_path = self.workspace / ".study-compiler/learning-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(
            state["lessons"]["prompted-result"]["prompted_at"],
            completed["completed_at"],
        )
        state["lessons"]["prompted-result"].update(
            {"awaiting_response": False, "prompted_at": None}
        )
        state_path.write_text(json.dumps(state), encoding="utf-8")
        report = inspect_vault(self.workspace)
        self.assertFalse(report["ok"])
        self.assertIn("neither awaiting", "\n".join(report["errors"]))

        state["lessons"]["prompted-result"].update(
            {
                "awaiting_response": True,
                "prompted_at": "2000-01-01T00:00:00Z",
            }
        )
        state_path.write_text(json.dumps(state), encoding="utf-8")
        report = inspect_vault(self.workspace)
        self.assertFalse(report["ok"])
        self.assertIn("prompted_at", "\n".join(report["errors"]))

        state["lessons"]["prompted-result"].update(
            {
                "awaiting_response": True,
                "prompted_at": completed["completed_at"],
            }
        )
        state_path.write_text(json.dumps(state), encoding="utf-8")
        record_recall(
            self.workspace,
            "prompted-result",
            score=4,
            retelling="The new fragment connects to the existing mechanism.",
        )
        self.assertTrue(inspect_vault(self.workspace)["ok"])

    def test_doctor_allows_a_cluster_result_promoted_after_completion(self) -> None:
        source_path = self.inbox / "promote.md"
        source_path.write_text("A fragment ready for approval.\n", encoding="utf-8")
        started = start_run(self.workspace)
        source_id = started["next_action"]["id"]
        create_draft(
            self.workspace,
            slug="promoted-result",
            title="Promoted Result",
            source_ids=[source_id],
            body=valid_body(source_id),
        )
        finish_run(
            self.workspace,
            started["run_id"],
            self._artifact(started, "promoted-result"),
        )

        promote_draft(self.workspace, "promoted-result", approve=True)

        self.assertTrue(inspect_vault(self.workspace)["ok"])

    def test_active_run_prevents_a_second_run_from_syncing_another_file(self) -> None:
        first_path = self.inbox / "first.md"
        first_path.write_text("First fragment.\n", encoding="utf-8")
        first = start_run(self.workspace)
        second_path = self.inbox / "second.md"
        second_path.write_text("Second fragment.\n", encoding="utf-8")

        with self.assertRaisesRegex(StateError, "active"):
            start_run(self.workspace)

        status = workspace_status(self.workspace)
        self.assertEqual(
            [item["path"] for item in status["source_changes"]["new"]],
            [str(second_path.resolve())],
        )
        self.assertEqual(len(list((self.workspace / "sources").iterdir())), 1)
        fail_run(self.workspace, first["run_id"], "test cleanup")

    def test_modified_file_action_links_the_previous_source_version(self) -> None:
        source_path = self.inbox / "growing-chat.md"
        source_path.write_text("First exchange.\n", encoding="utf-8")
        first = start_run(self.workspace)
        first_source_id = first["next_action"]["id"]
        fail_run(self.workspace, first["run_id"], "simulate interruption")
        set_source_coverage(self.workspace, first_source_id, status="complete")

        source_path.write_text("First exchange.\nSecond exchange.\n", encoding="utf-8")
        second = start_run(self.workspace)

        self.assertEqual(second["next_action"]["change"], "modified")
        self.assertEqual(second["next_action"]["previous_source_id"], first_source_id)
        self.assertNotEqual(second["next_action"]["id"], first_source_id)

    def test_modified_current_version_wins_even_if_previous_run_failed(self) -> None:
        source_path = self.inbox / "uncertain.md"
        source_path.write_text("Initial claim.\n", encoding="utf-8")
        first = start_run(self.workspace)
        first_source_id = first["next_action"]["id"]
        fail_run(self.workspace, first["run_id"], "source changed before integration")
        source_path.write_text("Corrected claim.\n", encoding="utf-8")

        second = start_run(self.workspace)

        self.assertEqual(second["next_action"]["change"], "modified")
        self.assertEqual(second["next_action"]["previous_source_id"], first_source_id)
        self.assertNotEqual(second["next_action"]["id"], first_source_id)

    def test_latest_duplicate_path_event_drives_the_watched_action(self) -> None:
        first_path = self.inbox / "a.md"
        second_path = self.inbox / "b.md"
        first_path.write_text("Shared content X.\n", encoding="utf-8")
        source_x = sync_next_source_change(self.workspace)["synced"]["source"]
        second_path.write_text("Different content Y.\n", encoding="utf-8")
        source_y = sync_next_source_change(self.workspace)["synced"]["source"]
        second_path.write_text("Shared content X.\n", encoding="utf-8")

        duplicate = sync_next_source_change(self.workspace)["synced"]
        set_source_coverage(self.workspace, source_y["id"], status="complete")
        started = start_run(self.workspace)

        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(started["next_action"]["id"], source_x["id"])
        self.assertEqual(started["next_action"]["path"], str(second_path.resolve()))
        self.assertEqual(started["next_action"]["change"], "modified")
        self.assertEqual(started["next_action"]["previous_source_id"], source_y["id"])

    def test_watched_delta_takes_priority_over_a_due_review(self) -> None:
        review_source = add_snippet(
            self.workspace,
            title="Due review source",
            text="A concept that is due for review.",
        )["source"]
        create_draft(
            self.workspace,
            slug="due-review",
            title="Due Review",
            source_ids=[review_source["id"]],
            body=valid_body(review_source["id"]),
        )
        record_recall(
            self.workspace,
            "due-review",
            score=2,
            retelling="I am unsure.",
            now=datetime(2000, 1, 1, tzinfo=UTC),
        )
        watched = self.inbox / "new-fragment.md"
        watched.write_text("A changed learning fragment.\n", encoding="utf-8")

        started = start_run(self.workspace)

        self.assertEqual(started["next_action"]["type"], "source")
        self.assertTrue(started["next_action"]["watched"])

    def test_mtime_only_touch_is_reconciled_without_learning_work(self) -> None:
        source_path = self.inbox / "stable.md"
        source_path.write_text("Stable content.\n", encoding="utf-8")
        first = start_run(self.workspace)
        source_id = first["next_action"]["id"]
        fail_run(self.workspace, first["run_id"], "test cleanup")
        set_source_coverage(self.workspace, source_id, status="complete")
        source_path.touch()
        self.assertEqual(
            len(workspace_status(self.workspace)["source_changes"]["touched"]), 1
        )

        no_op = start_run(self.workspace)

        self.assertEqual(no_op["status"], "no-op")
        self.assertEqual(
            workspace_status(self.workspace)["source_changes"]["unchanged"][0]["path"],
            str(source_path.resolve()),
        )

    def test_deleted_source_is_report_only_and_does_not_create_work(self) -> None:
        source_path = self.inbox / "temporary.md"
        source_path.write_text(
            "A fragment that may later be removed.\n", encoding="utf-8"
        )
        first = start_run(self.workspace)
        source_id = first["next_action"]["id"]
        fail_run(self.workspace, first["run_id"], "test cleanup")
        set_source_coverage(self.workspace, source_id, status="complete")
        snapshot = next((self.workspace / "sources" / source_id).glob("snapshot.*"))
        source_path.unlink()

        status = workspace_status(self.workspace)
        no_op = start_run(self.workspace)

        self.assertEqual(
            [item["path"] for item in status["source_changes"]["deleted"]],
            [str(source_path.resolve(strict=False))],
        )
        self.assertEqual(no_op["status"], "no-op")
        self.assertTrue(snapshot.is_file())

    def test_watched_source_can_finish_by_integrating_an_unreviewed_draft(self) -> None:
        old_source = add_snippet(
            self.workspace,
            title="Existing attention note",
            text="Attention combines context.",
        )["source"]
        create_draft(
            self.workspace,
            slug="attention",
            title="Attention",
            source_ids=[old_source["id"]],
            body=valid_body(old_source["id"]),
        )
        source_path = self.inbox / "attention-update.md"
        source_path.write_text(
            "Keys describe available information.\n", encoding="utf-8"
        )
        started = start_run(self.workspace)
        new_source_id = started["next_action"]["id"]
        revise_draft(
            self.workspace,
            "attention",
            body=valid_body(old_source["id"], new_source_id),
            add_source_ids=[new_source_id],
        )

        completed = finish_run(
            self.workspace,
            started["run_id"],
            self._artifact(
                started,
                "attention",
                integration_mode="integrate_staged",
            ),
        )

        self.assertEqual(
            completed["review_cluster"]["integration_mode"], "integrate_staged"
        )


if __name__ == "__main__":
    unittest.main()
