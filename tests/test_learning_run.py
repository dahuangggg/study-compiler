import json
import os
import stat
import tempfile
import unittest
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

from study_compiler.doctor import inspect_vault
from study_compiler.errors import StateError, ValidationError
from study_compiler.installer import SKILL_NAMES
from study_compiler.lessons import create_draft, list_drafts, record_recall
from study_compiler.sources import add_snippet, add_url_source, attach_web_snapshot
from study_compiler.storage import initialize_vault


def lesson_body(source_id: str) -> str:
    return f"""## Learning Objectives

Explain why attention mixes information across tokens.

## Intuition

Attention lets each token gather context relevant to its current job.

## Example

In a pronoun-resolution example, the pronoun attends to its antecedent.

## Explain It Back

Explain attention using an example of your own.

## Sources

- {source_id}
"""


def require_public_module(test: unittest.TestCase, name: str):
    try:
        return import_module(name)
    except ModuleNotFoundError as error:
        test.fail(f"missing required public module {name}: {error}")


class WorkspaceConfigTests(unittest.TestCase):
    def test_write_config_persists_workspace_and_resolve_workspace_uses_it(
        self,
    ) -> None:
        config = require_public_module(self, "study_compiler.config")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            workspace = root / "Learn"
            initialize_vault(workspace)

            config.write_config(workspace, home=home)

            config_path = home / ".study-compiler/config.json"
            self.assertTrue(config_path.is_file())
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["workspace"], str(workspace.resolve()))
            self.assertEqual(config.resolve_workspace(home=home), workspace.resolve())
            self.assertEqual(stat.S_IMODE(config_path.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)

    def test_write_config_rejects_a_symlinked_config_directory(self) -> None:
        config = require_public_module(self, "study_compiler.config")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            home.mkdir()
            external = root / "external"
            external.mkdir()
            (home / ".study-compiler").symlink_to(external, target_is_directory=True)
            workspace = root / "Learn"
            initialize_vault(workspace)

            with self.assertRaisesRegex(StateError, "symlink"):
                config.write_config(workspace, home=home)

            self.assertEqual(list(external.iterdir()), [])

    def test_explicit_workspace_takes_precedence_over_saved_config(self) -> None:
        config = require_public_module(self, "study_compiler.config")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            saved_workspace = root / "Saved"
            explicit_workspace = root / "Explicit"
            initialize_vault(saved_workspace)
            initialize_vault(explicit_workspace)
            config.write_config(saved_workspace, home=home)

            resolved = config.resolve_workspace(explicit_workspace, home=home)

            self.assertEqual(resolved, explicit_workspace.resolve())


class LearningRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.vault = self.root / "Learn"
        initialize_vault(self.vault)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def add_pending_source(self) -> dict[str, object]:
        return add_snippet(
            self.vault,
            title="Attention source",
            text="Attention combines relevant token context.",
        )["source"]

    def read_run(self, run_id: str) -> dict[str, object]:
        path = self.vault / ".study-compiler/runs" / f"{run_id}.json"
        self.assertTrue(path.is_file())
        return json.loads(path.read_text(encoding="utf-8"))

    def write_session_result(
        self, started: dict[str, object], *, body: str = "One bounded result."
    ) -> Path:
        artifact = self.vault / str(started["artifact_path"])
        action = started["next_action"]
        self.assertIsInstance(action, dict)
        artifact.write_text(
            "---\n"
            f"run_id: {json.dumps(started['run_id'])}\n"
            f"action_type: {json.dumps(action['type'])}\n"
            f"action_id: {json.dumps(action['id'])}\n"
            "---\n\n"
            f"# Learning Session\n\n{body}\n",
            encoding="utf-8",
        )
        return artifact

    def fulfill_source_action(self, started: dict[str, object], *, slug: str) -> None:
        action = started["next_action"]
        self.assertEqual(action["type"], "source")
        create_draft(
            self.vault,
            slug=slug,
            title=f"Lesson {slug}",
            source_ids=[action["id"]],
            body=lesson_body(str(action["id"])),
        )

    def test_start_run_with_work_creates_active_record_and_staged_agenda(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        now = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)

        result = runs.start_run(self.vault, period="daily", now=now)

        self.assertEqual(result["status"], "started")
        self.assertTrue(result["next_action"])
        self.assertTrue(result["run_id"])
        agenda_path = Path(result["agenda_path"])
        self.assertFalse(agenda_path.is_absolute())
        self.assertEqual(agenda_path.parts[:2], ("_staging", "digests"))
        self.assertTrue((self.vault / agenda_path).is_file())
        record = self.read_run(result["run_id"])
        self.assertEqual(record["status"], "active")
        self.assertEqual(record["period"], "daily")
        self.assertEqual(record["agenda_path"], agenda_path.as_posix())
        self.assertEqual(
            result["artifact_path"],
            f"_staging/digests/{result['run_id']}-learning-session.md",
        )

    def test_resume_run_returns_the_same_active_work_without_creating_a_new_run(
        self,
    ) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        started = runs.start_run(self.vault)

        resumed = runs.resume_run(self.vault, started["run_id"])

        self.assertEqual(resumed["status"], "resumed")
        self.assertEqual(resumed["run_id"], started["run_id"])
        self.assertEqual(resumed["agenda_path"], started["agenda_path"])
        self.assertEqual(resumed["artifact_path"], started["artifact_path"])
        self.assertEqual(
            len(list((self.vault / ".study-compiler/runs").glob("*.json"))), 1
        )

    def test_start_run_rejects_a_second_active_run(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        runs.start_run(
            self.vault,
            period="daily",
            now=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
        )

        with self.assertRaisesRegex(StateError, "active"):
            runs.start_run(
                self.vault,
                period="weekly",
                now=datetime(2026, 7, 14, 9, 5, tzinfo=UTC),
            )

    def test_start_run_reports_corrupt_source_schema_without_a_traceback(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        source = self.add_pending_source()
        manifest_path = self.vault / ".study-compiler/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["sources"][source["id"]].pop("title")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(StateError, "title"):
            runs.start_run(self.vault)

    def test_start_run_rejects_corrupt_source_lesson_links(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        source = self.add_pending_source()
        manifest_path = self.vault / ".study-compiler/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["sources"][source["id"]]["lesson_slugs"] = "not-a-list"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(StateError, "lesson links"):
            runs.start_run(self.vault)

    def test_start_run_without_work_records_no_op_without_an_agenda(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")

        result = runs.start_run(
            self.vault,
            period="daily",
            now=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
        )

        self.assertEqual(result["status"], "no-op")
        record = self.read_run(result["run_id"])
        self.assertEqual(record["status"], "no-op")
        self.assertEqual(list((self.vault / "_staging/digests").glob("*.md")), [])

    def test_start_run_treats_unsnapshotted_web_source_as_actionable(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        source = add_url_source(
            self.vault,
            "https://example.com/article",
            title="Web article",
        )["source"]

        result = runs.start_run(
            self.vault,
            period="daily",
            now=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
        )

        self.assertEqual(result["status"], "started")
        self.assertEqual(result["next_action"]["type"], "source_snapshot")
        self.assertEqual(result["next_action"]["id"], source["id"])

    def test_finish_run_completes_a_healthy_run_with_a_staged_artifact(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        started = runs.start_run(
            self.vault,
            now=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
        )
        self.fulfill_source_action(started, slug="completed-run-source")
        artifact = self.write_session_result(started)

        result = runs.finish_run(
            self.vault,
            started["run_id"],
            artifact,
            now=datetime(2026, 7, 14, 9, 10, tzinfo=UTC),
        )

        self.assertEqual(result["status"], "completed")
        record = self.read_run(started["run_id"])
        self.assertEqual(record["status"], "completed")
        self.assertEqual(
            record["artifact"],
            f"_staging/digests/{started['run_id']}-learning-session.md",
        )
        self.assertEqual(len(record["artifact_sha256"]), 64)
        self.assertGreater(record["artifact_size"], 0)
        self.assertTrue(inspect_vault(self.vault)["ok"])

        artifact.unlink()
        report = inspect_vault(self.vault)
        self.assertFalse(report["ok"], report)
        self.assertIn("artifact", "\n".join(report["errors"]).lower())

    def test_finish_run_accepts_a_legacy_workspace_without_source_watch_state(
        self,
    ) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        (self.vault / ".study-compiler/source-watch.json").unlink()
        self.add_pending_source()
        started = runs.start_run(self.vault)
        self.fulfill_source_action(started, slug="legacy-workspace-source")
        artifact = self.write_session_result(started)

        result = runs.finish_run(self.vault, started["run_id"], artifact)

        self.assertEqual(result["status"], "completed")
        self.assertFalse((self.vault / ".study-compiler/source-watch.json").exists())
        self.assertTrue(inspect_vault(self.vault)["ok"])

    def test_consecutive_runs_keep_distinct_agendas(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        first = runs.start_run(
            self.vault,
            period="daily",
            now=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
        )
        self.fulfill_source_action(first, slug="first-run-source")
        artifact = self.write_session_result(first, body="First result.")
        runs.finish_run(
            self.vault,
            first["run_id"],
            artifact,
            now=datetime(2026, 7, 14, 9, 10, tzinfo=UTC),
        )

        second = runs.start_run(
            self.vault,
            period="daily",
            now=datetime(2026, 7, 14, 9, 20, tzinfo=UTC),
        )

        self.assertNotEqual(first["agenda_path"], second["agenda_path"])
        self.assertTrue((self.vault / first["agenda_path"]).is_file())
        self.assertTrue((self.vault / second["agenda_path"]).is_file())

    def test_a_run_cannot_reuse_another_runs_agenda_receipt(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        first = runs.start_run(self.vault, now=datetime(2026, 7, 14, 9, 0, tzinfo=UTC))
        self.fulfill_source_action(first, slug="first-lesson")
        runs.finish_run(
            self.vault,
            first["run_id"],
            self.write_session_result(first),
            now=datetime(2026, 7, 14, 9, 5, tzinfo=UTC),
        )
        second = runs.start_run(
            self.vault, now=datetime(2026, 7, 14, 10, 0, tzinfo=UTC)
        )
        first_record = self.read_run(first["run_id"])
        second_record = self.read_run(second["run_id"])
        for field in ("agenda_path", "agenda_sha256", "agenda_size"):
            second_record[field] = first_record[field]
        second_path = self.vault / ".study-compiler/runs" / f"{second['run_id']}.json"
        second_path.write_text(json.dumps(second_record), encoding="utf-8")

        with self.assertRaisesRegex(StateError, "agenda_path"):
            runs.resume_run(self.vault, second["run_id"])
        report = inspect_vault(self.vault)
        self.assertFalse(report["ok"], report)
        self.assertIn("agenda_path", "\n".join(report["errors"]))

    def test_finish_run_rejects_the_deterministic_agenda_as_the_result(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        started = runs.start_run(self.vault)
        agenda = self.vault / started["agenda_path"]

        with self.assertRaisesRegex(ValidationError, "agenda"):
            runs.finish_run(self.vault, started["run_id"], agenda)

        self.assertEqual(self.read_run(started["run_id"])["status"], "active")

    def test_finish_run_requires_the_recorded_source_action_to_be_completed(
        self,
    ) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        started = runs.start_run(self.vault)
        artifact = self.write_session_result(started)

        with self.assertRaisesRegex(StateError, "source action"):
            runs.finish_run(self.vault, started["run_id"], artifact)

        self.fulfill_source_action(started, slug="fulfilled-source-action")
        result = runs.finish_run(self.vault, started["run_id"], artifact)
        self.assertEqual(result["status"], "completed")

    def test_finish_run_requires_a_web_snapshot_for_snapshot_action(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        source = add_url_source(
            self.vault,
            "https://example.com/article",
            title="Web article",
        )["source"]
        started = runs.start_run(self.vault)
        artifact = self.write_session_result(started)

        with self.assertRaisesRegex(StateError, "snapshot action"):
            runs.finish_run(self.vault, started["run_id"], artifact)

        snapshot = self.root / "article.md"
        snapshot.write_text("# Article\n\nGrounded material.\n", encoding="utf-8")
        attach_web_snapshot(self.vault, source["id"], snapshot)
        result = runs.finish_run(self.vault, started["run_id"], artifact)
        self.assertEqual(result["status"], "completed")

    def test_finish_run_requires_a_nonempty_markdown_artifact(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        started = runs.start_run(self.vault)
        artifact = self.vault / "_staging/digests/result.txt"
        artifact.write_text("result", encoding="utf-8")

        with self.assertRaisesRegex(ValidationError, "Markdown"):
            runs.finish_run(self.vault, started["run_id"], artifact)

        artifact = self.vault / str(started["artifact_path"])
        artifact.write_text("   \n", encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "empty"):
            runs.finish_run(self.vault, started["run_id"], artifact)

    def test_finish_run_requires_binding_in_leading_frontmatter(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        started = runs.start_run(self.vault)
        self.fulfill_source_action(started, slug="bound-artifact-source")
        action = started["next_action"]
        artifact = self.vault / str(started["artifact_path"])
        artifact.write_text(
            "# Learning Session\n\n"
            f"run_id: {json.dumps(started['run_id'])}\n"
            f"action_type: {json.dumps(action['type'])}\n"
            f"action_id: {json.dumps(action['id'])}\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValidationError, "frontmatter"):
            runs.finish_run(self.vault, started["run_id"], artifact)

    def test_finish_run_rejects_an_oversized_agent_artifact(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        started = runs.start_run(self.vault)
        artifact = self.write_session_result(
            started,
            body="x" * (runs.MAX_ARTIFACT_BYTES + 1),
        )

        with self.assertRaisesRegex(ValidationError, "byte limit"):
            runs.finish_run(self.vault, started["run_id"], artifact)

    def test_finish_run_rejects_an_artifact_outside_the_workspace(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        started = runs.start_run(self.vault)
        outside = self.root / "outside.md"
        outside.write_text("untrusted output", encoding="utf-8")

        with self.assertRaises(ValidationError):
            runs.finish_run(self.vault, started["run_id"], outside)

        self.assertEqual(self.read_run(started["run_id"])["status"], "active")

    def test_finish_run_rejects_an_artifact_outside_staging(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        started = runs.start_run(self.vault)
        artifact = self.vault / "agent-output.md"
        artifact.write_text("not staged", encoding="utf-8")

        with self.assertRaises(ValidationError):
            runs.finish_run(self.vault, started["run_id"], artifact)

        self.assertEqual(self.read_run(started["run_id"])["status"], "active")

    def test_finish_run_rejects_a_missing_staged_artifact(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        started = runs.start_run(self.vault)
        missing = self.vault / "_staging/digests/missing.md"

        with self.assertRaises(ValidationError):
            runs.finish_run(self.vault, started["run_id"], missing)

        self.assertEqual(self.read_run(started["run_id"])["status"], "active")

    def test_finish_run_requires_a_healthy_doctor_report(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        started = runs.start_run(self.vault)
        self.fulfill_source_action(started, slug="doctor-gated-source")
        artifact = self.write_session_result(started)
        (self.vault / ".study-compiler/learning-state.json").write_text(
            "not-json", encoding="utf-8"
        )

        with self.assertRaises(StateError):
            runs.finish_run(self.vault, started["run_id"], artifact)

        self.assertEqual(self.read_run(started["run_id"])["status"], "active")

    def test_completed_prompt_waits_for_learner_before_replanning_it(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        review_source = self.add_pending_source()
        pending_source = add_snippet(
            self.vault,
            title="Next source",
            text="A second concept to compile.",
        )["source"]
        create_draft(
            self.vault,
            slug="awaiting-review",
            title="Awaiting Review",
            source_ids=[review_source["id"]],
            body=lesson_body(str(review_source["id"])),
        )
        record_recall(
            self.vault,
            "awaiting-review",
            score=2,
            retelling="I am still unsure.",
            now=datetime(2000, 1, 1, tzinfo=UTC),
        )
        first = runs.start_run(self.vault)
        self.assertEqual(first["next_action"]["type"], "review")
        artifact = self.write_session_result(first)
        runs.finish_run(self.vault, first["run_id"], artifact)

        waiting = runs.workspace_status(self.vault)
        self.assertEqual(waiting["awaiting_learner"], 1)
        self.assertEqual(waiting["due_reviews"], 0)
        self.assertEqual(waiting["staged_lessons"], 0)

        second = runs.start_run(self.vault)

        self.assertEqual(second["next_action"]["type"], "source")
        self.assertIn(
            second["next_action"]["id"],
            {review_source["id"], pending_source["id"]},
        )

    def test_completed_review_with_new_recall_does_not_wait_for_another_answer(
        self,
    ) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        source = self.add_pending_source()
        create_draft(
            self.vault,
            slug="answered-review",
            title="Answered Review",
            source_ids=[source["id"]],
            body=lesson_body(str(source["id"])),
        )
        record_recall(
            self.vault,
            "answered-review",
            score=2,
            retelling="I am still unsure.",
            now=datetime(2026, 7, 12, 9, 0, tzinfo=UTC),
        )
        started = runs.start_run(
            self.vault,
            period="daily",
            now=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
        )
        self.assertEqual(started["next_action"]["type"], "review")
        record_recall(
            self.vault,
            "answered-review",
            score=4,
            retelling="Each token gathers the context useful for its current job.",
            now=datetime(2026, 7, 14, 9, 5, tzinfo=UTC),
        )
        artifact = self.write_session_result(started)

        runs.finish_run(
            self.vault,
            started["run_id"],
            artifact,
            now=datetime(2026, 7, 14, 9, 10, tzinfo=UTC),
        )

        state = json.loads(
            (self.vault / ".study-compiler/learning-state.json").read_text(
                encoding="utf-8"
            )
        )
        lesson = state["lessons"]["answered-review"]
        self.assertFalse(lesson["awaiting_response"])
        self.assertEqual(lesson["last_reviewed"], "2026-07-14T09:05:00Z")

    def test_resume_rejects_a_symlinked_run_record(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        started = runs.start_run(self.vault)
        run_path = self.vault / ".study-compiler/runs" / f"{started['run_id']}.json"
        redirected = run_path.with_name("redirected.json")
        run_path.rename(redirected)
        run_path.symlink_to(redirected)

        with self.assertRaisesRegex(StateError, "symlink"):
            runs.resume_run(self.vault, started["run_id"])

    def test_resume_reports_a_corrupt_active_record_without_key_error(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        started = runs.start_run(self.vault)
        run_path = self.vault / ".study-compiler/runs" / f"{started['run_id']}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        record.pop("artifact_path")
        run_path.write_text(json.dumps(record), encoding="utf-8")

        with self.assertRaisesRegex(StateError, "artifact_path"):
            runs.resume_run(self.vault, started["run_id"])

    def test_fail_run_records_failure_and_exposes_it_in_workspace_status(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        started = runs.start_run(
            self.vault,
            now=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
        )

        result = runs.fail_run(
            self.vault,
            started["run_id"],
            "agent timed out",
            now=datetime(2026, 7, 14, 9, 5, tzinfo=UTC),
        )

        self.assertEqual(result["status"], "failed")
        record = self.read_run(started["run_id"])
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["error"], "agent timed out")
        status = runs.workspace_status(self.vault)
        self.assertIsNone(status["active_run"])
        self.assertEqual(status["last_failure"]["run_id"], started["run_id"])
        self.assertEqual(status["last_failure"]["error"], "agent timed out")

    def test_fail_run_rejects_an_oversized_failure_message(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        started = runs.start_run(self.vault)

        with self.assertRaisesRegex(ValidationError, "too long"):
            runs.fail_run(self.vault, started["run_id"], "x" * 2001)

        self.assertEqual(self.read_run(started["run_id"])["status"], "active")

    def test_terminal_run_timestamps_cannot_precede_the_start(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        started = runs.start_run(
            self.vault, now=datetime(2026, 7, 14, 10, 0, tzinfo=UTC)
        )

        with self.assertRaisesRegex(ValidationError, "precede"):
            runs.fail_run(
                self.vault,
                started["run_id"],
                "clock moved backwards",
                now=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
            )

        self.fulfill_source_action(started, slug="timestamp-order")
        artifact = self.write_session_result(started)
        with self.assertRaisesRegex(ValidationError, "precede"):
            runs.finish_run(
                self.vault,
                started["run_id"],
                artifact,
                now=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
            )
        self.assertEqual(self.read_run(started["run_id"])["status"], "active")

    def test_persisted_failure_text_must_not_contain_terminal_controls(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        started = runs.start_run(self.vault)
        runs.fail_run(self.vault, started["run_id"], "agent timed out")
        run_path = self.vault / ".study-compiler/runs" / f"{started['run_id']}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        record["error"] = "\x1b[2JINJECT"
        run_path.write_text(json.dumps(record), encoding="utf-8")

        with self.assertRaisesRegex(StateError, "error"):
            runs.workspace_status(self.vault)
        report = inspect_vault(self.vault)
        self.assertFalse(report["ok"], report)
        self.assertIn("error", "\n".join(report["errors"]).lower())

    def test_run_schema_type_corruption_fails_without_type_errors(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        started = runs.start_run(self.vault)
        run_path = self.vault / ".study-compiler/runs" / f"{started['run_id']}.json"
        baseline = json.loads(run_path.read_text(encoding="utf-8"))

        corruptions = (
            ("status", []),
            ("period", []),
            ("agenda_path", "\x00"),
            ("started_at", "2026-07-14T09:00:00+00:00"),
            ("next_action.type", []),
        )
        for field, value in corruptions:
            with self.subTest(field=field):
                record = json.loads(json.dumps(baseline))
                if field == "next_action.type":
                    record["next_action"]["type"] = value
                else:
                    record[field] = value
                run_path.write_text(json.dumps(record), encoding="utf-8")

                with self.assertRaises(StateError):
                    runs.workspace_status(self.vault)
                self.assertFalse(inspect_vault(self.vault)["ok"])

    @unittest.skipIf(os.name == "nt", "POSIX permissions are required")
    def test_unreadable_agenda_fails_resume_and_doctor_cleanly(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        started = runs.start_run(self.vault)
        agenda = self.vault / started["agenda_path"]
        agenda.chmod(0)
        try:
            if os.access(agenda, os.R_OK):
                self.skipTest("current user can still read mode-000 files")
            with self.assertRaisesRegex(StateError, "read"):
                runs.resume_run(self.vault, started["run_id"])
            report = inspect_vault(self.vault)
            self.assertFalse(report["ok"], report)
            self.assertIn("unreadable", "\n".join(report["errors"]).lower())
        finally:
            agenda.chmod(0o600)

    def test_invalid_utf8_staged_lesson_fails_all_planning_surfaces_cleanly(
        self,
    ) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        source = self.add_pending_source()
        create_draft(
            self.vault,
            slug="invalid-utf8",
            title="Invalid UTF-8",
            source_ids=[source["id"]],
            body=lesson_body(str(source["id"])),
        )
        staged = self.vault / "_staging/lessons/invalid-utf8.md"
        staged.write_bytes(b"\xff")

        with self.assertRaisesRegex(StateError, "lesson"):
            list_drafts(self.vault)
        with self.assertRaisesRegex(StateError, "lesson"):
            runs.workspace_status(self.vault)
        with self.assertRaisesRegex(StateError, "lesson"):
            runs.start_run(self.vault)
        self.assertFalse(inspect_vault(self.vault)["ok"])

    def test_doctor_validates_learning_run_records(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        started = runs.start_run(self.vault)
        run_path = self.vault / ".study-compiler/runs" / f"{started['run_id']}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        record["agenda_path"] = "../../outside.md"
        run_path.write_text(json.dumps(record), encoding="utf-8")

        report = inspect_vault(self.vault)

        self.assertFalse(report["ok"], report)
        self.assertIn("agenda", "\n".join(report["errors"]).lower())
        self.assertIn("agenda_path", "\n".join(report["errors"]).lower())

    def test_doctor_rejects_a_run_without_its_action_precondition(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        started = runs.start_run(self.vault)
        run_path = self.vault / ".study-compiler/runs" / f"{started['run_id']}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        record.pop("precondition")
        run_path.write_text(json.dumps(record), encoding="utf-8")

        report = inspect_vault(self.vault)

        self.assertFalse(report["ok"], report)
        self.assertIn("precondition", "\n".join(report["errors"]).lower())

    def test_doctor_rejects_empty_run_action_receipts(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        self.add_pending_source()
        started = runs.start_run(self.vault)
        run_path = self.vault / ".study-compiler/runs" / f"{started['run_id']}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        record["next_action"] = {}
        record["precondition"] = {}
        run_path.write_text(json.dumps(record), encoding="utf-8")

        report = inspect_vault(self.vault)

        self.assertFalse(report["ok"], report)
        self.assertIn("next_action", "\n".join(report["errors"]).lower())

    def test_workspace_status_summarizes_actionable_learning_work(self) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        lesson_source = self.add_pending_source()
        add_url_source(
            self.vault,
            "https://example.com/unsnapshotted",
            title="Unsnapshotted web source",
        )
        create_draft(
            self.vault,
            slug="attention-status",
            title="Attention status",
            source_ids=[lesson_source["id"]],
            body=lesson_body(str(lesson_source["id"])),
        )
        record_recall(
            self.vault,
            "attention-status",
            score=2,
            retelling="I still confuse which context is selected.",
            now=datetime(2000, 1, 1, tzinfo=UTC),
        )

        status = runs.workspace_status(self.vault)

        self.assertEqual(status["pending_sources"], 1)
        self.assertEqual(status["unsnapshotted_urls"], 1)
        self.assertEqual(status["staged_lessons"], 1)
        self.assertEqual(status["due_reviews"], 1)
        self.assertIsNone(status["active_run"])
        self.assertIsNone(status["last_failure"])

        started = runs.start_run(self.vault)
        active_status = runs.workspace_status(self.vault)
        self.assertEqual(active_status["active_run"]["run_id"], started["run_id"])

    def test_workspace_status_separates_unsnapshotted_from_draftable_sources(
        self,
    ) -> None:
        runs = require_public_module(self, "study_compiler.runs")
        add_url_source(
            self.vault,
            "https://example.com/unsnapshotted",
            title="Unsnapshotted web source",
        )

        status = runs.workspace_status(self.vault)

        self.assertEqual(status["pending_sources"], 0)
        self.assertEqual(status["unsnapshotted_urls"], 1)


class LearningRunSkillTests(unittest.TestCase):
    def test_installer_includes_learning_run_skill(self) -> None:
        self.assertIn("learning-run", SKILL_NAMES)

    def test_learning_run_skill_exists_in_the_repository(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        self.assertTrue((project_root / "skills/learning-run/SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
