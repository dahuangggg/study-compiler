import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from study_compiler.doctor import inspect_vault
from study_compiler.errors import StateError, ValidationError
from study_compiler.source_watch import (
    configure_source_roots,
    scan_source_changes,
    sync_next_source_change,
)
from study_compiler.storage import initialize_vault
from study_compiler.sources import _add_local_source_unlocked as real_add_local_source


class SourceWatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.workspace = self.root / "Learn"
        self.sources = self.root / "Notes"
        self.sources.mkdir()
        initialize_vault(self.workspace)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_configures_canonical_deduplicated_source_roots(self) -> None:
        result = configure_source_roots(
            self.workspace,
            [self.sources, self.sources / "."],
        )

        self.assertEqual(result["roots"], [str(self.sources.resolve())])
        state = json.loads(
            (self.workspace / ".study-compiler/source-watch.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(state["roots"], [str(self.sources.resolve())])
        self.assertEqual(state["files"], {})

    def test_rejects_non_directory_symlink_and_workspace_overlap(self) -> None:
        with self.assertRaisesRegex(ValidationError, "directory"):
            configure_source_roots(self.workspace, [self.root / "missing"])

        alias = self.root / "NotesAlias"
        alias.symlink_to(self.sources, target_is_directory=True)
        with self.assertRaisesRegex(ValidationError, "symlink"):
            configure_source_roots(self.workspace, [alias])

        with self.assertRaisesRegex(ValidationError, "overlap"):
            configure_source_roots(self.workspace, [self.workspace])
        inside = self.workspace / "inbox"
        inside.mkdir()
        with self.assertRaisesRegex(ValidationError, "overlap"):
            configure_source_roots(self.workspace, [inside])

    def test_classifies_new_unchanged_touched_modified_and_deleted(self) -> None:
        configure_source_roots(self.workspace, [self.sources])
        source = self.sources / "attention.md"
        source.write_text("Attention combines context.\n", encoding="utf-8")

        initial = scan_source_changes(self.workspace)
        canonical_source = str(source.resolve())
        self.assertEqual([item["path"] for item in initial["new"]], [canonical_source])
        synced = sync_next_source_change(self.workspace)
        self.assertEqual(synced["synced"]["change"], "new")
        self.assertEqual(
            scan_source_changes(self.workspace)["unchanged"][0]["path"],
            canonical_source,
        )

        before = source.stat().st_mtime_ns
        os.utime(source, ns=(before + 1_000_000, before + 1_000_000))
        touched = scan_source_changes(self.workspace)
        self.assertEqual(
            [item["path"] for item in touched["touched"]], [canonical_source]
        )
        self.assertEqual(touched["modified"], [])

        source.write_text(
            "Attention uses queries, keys, and values.\n", encoding="utf-8"
        )
        modified = scan_source_changes(self.workspace)
        self.assertEqual(
            [item["path"] for item in modified["modified"]], [canonical_source]
        )
        self.assertIn("previous_source_id", modified["modified"][0])

        sync_next_source_change(self.workspace)
        source.unlink()
        deleted = scan_source_changes(self.workspace)
        self.assertEqual(
            [item["path"] for item in deleted["deleted"]], [canonical_source]
        )
        self.assertTrue((self.workspace / "sources").is_dir())

    def test_unique_same_hash_rename_rekeys_without_a_new_snapshot(self) -> None:
        configure_source_roots(self.workspace, [self.sources])
        old_path = self.sources / "old-name.md"
        new_path = self.sources / "new-name.md"
        old_path.write_text("A stable fragment.\n", encoding="utf-8")
        first = sync_next_source_change(self.workspace)["synced"]
        source_id = first["source"]["id"]
        source_directories_before = sorted(
            path.name for path in (self.workspace / "sources").iterdir()
        )

        old_path.rename(new_path)
        delta = scan_source_changes(self.workspace)

        self.assertEqual(
            [(item["previous_path"], item["path"]) for item in delta["renamed"]],
            [(str(old_path.resolve(strict=False)), str(new_path.resolve()))],
        )
        reconciled = sync_next_source_change(self.workspace)
        self.assertIsNone(reconciled["synced"])
        self.assertEqual(len(reconciled["renamed"]), 1)
        self.assertEqual(
            sorted(path.name for path in (self.workspace / "sources").iterdir()),
            source_directories_before,
        )
        state = json.loads(
            (self.workspace / ".study-compiler/source-watch.json").read_text()
        )
        canonical_old = str(old_path.resolve(strict=False))
        canonical_new = str(new_path.resolve())
        self.assertNotIn(canonical_old, state["files"])
        self.assertEqual(state["files"][canonical_new]["source_id"], source_id)
        self.assertIn(canonical_old, state["files"][canonical_new]["previous_paths"])

    def test_modification_creates_a_new_version_and_keeps_old_snapshot(self) -> None:
        configure_source_roots(self.workspace, [self.sources])
        source = self.sources / "conversation.md"
        source.write_text("First idea.\n", encoding="utf-8")
        first = sync_next_source_change(self.workspace)["synced"]
        first_id = first["source"]["id"]
        first_snapshot = self.workspace / first["source"]["snapshot"]

        source.write_text("First idea, then a corrected idea.\n", encoding="utf-8")
        second = sync_next_source_change(self.workspace)["synced"]
        second_id = second["source"]["id"]

        self.assertEqual(second["change"], "modified")
        self.assertEqual(second["previous_source_id"], first_id)
        self.assertNotEqual(second_id, first_id)
        self.assertTrue(first_snapshot.is_file())
        state = json.loads(
            (self.workspace / ".study-compiler/source-watch.json").read_text()
        )
        self.assertEqual(
            state["files"][str(source.resolve())]["versions"],
            [first_id, second_id],
        )
        self.assertTrue(inspect_vault(self.workspace)["ok"])

    def test_content_reversion_keeps_a_chronological_valid_version_history(
        self,
    ) -> None:
        configure_source_roots(self.workspace, [self.sources])
        source = self.sources / "revised.md"
        source.write_text("Version A.\n", encoding="utf-8")
        first_id = sync_next_source_change(self.workspace)["synced"]["source"]["id"]
        source.write_text("Version B.\n", encoding="utf-8")
        second_id = sync_next_source_change(self.workspace)["synced"]["source"]["id"]
        source.write_text("Version A.\n", encoding="utf-8")

        reverted = sync_next_source_change(self.workspace)["synced"]
        state = json.loads(
            (self.workspace / ".study-compiler/source-watch.json").read_text()
        )

        self.assertEqual(reverted["source"]["id"], first_id)
        self.assertEqual(
            state["files"][str(source.resolve())]["versions"],
            [first_id, second_id, first_id],
        )
        self.assertEqual(scan_source_changes(self.workspace)["modified"], [])
        self.assertTrue(inspect_vault(self.workspace)["ok"])

    def test_unavailable_root_never_becomes_delete_or_cross_root_rename(self) -> None:
        other_root = self.root / "OtherNotes"
        other_root.mkdir()
        configure_source_roots(self.workspace, [self.sources, other_root])
        watched = self.sources / "offline.md"
        watched.write_text("Same content.\n", encoding="utf-8")
        sync_next_source_change(self.workspace)
        offline_root = self.root / "OfflineNotes"
        self.sources.rename(offline_root)
        (other_root / "new.md").write_text("Same content.\n", encoding="utf-8")

        delta = scan_source_changes(self.workspace)

        self.assertEqual(delta["unavailable_roots"], [str(self.sources.resolve())])
        self.assertEqual(delta["deleted"], [])
        self.assertEqual(delta["renamed"], [])
        self.assertEqual(len(delta["new"]), 1)
        with self.assertRaisesRegex(StateError, "roots are unavailable"):
            sync_next_source_change(self.workspace)

    def test_symlink_swap_after_scan_cannot_snapshot_outside_the_source_root(
        self,
    ) -> None:
        configure_source_roots(self.workspace, [self.sources])
        watched = self.sources / "fragment.md"
        watched.write_text("SAFE\n", encoding="utf-8")
        secret = self.root / "secret.md"
        secret.write_text("SECRET\n", encoding="utf-8")

        def swap_then_add(*args, **kwargs):
            watched.unlink()
            watched.symlink_to(secret)
            return real_add_local_source(*args, **kwargs)

        with patch(
            "study_compiler.source_watch._add_local_source_unlocked",
            side_effect=swap_then_add,
        ):
            with self.assertRaisesRegex(ValidationError, "safe regular file"):
                sync_next_source_change(self.workspace)

        self.assertEqual(list((self.workspace / "sources").iterdir()), [])

    def test_parent_directory_symlink_swap_cannot_escape_the_source_root(self) -> None:
        configure_source_roots(self.workspace, [self.sources])
        watched_directory = self.sources / "topic"
        watched_directory.mkdir()
        watched = watched_directory / "fragment.md"
        watched.write_text("SAFE\n", encoding="utf-8")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "fragment.md").write_text("SECRET\n", encoding="utf-8")

        def swap_parent_then_add(*args, **kwargs):
            watched_directory.rename(self.root / "original-topic")
            watched_directory.symlink_to(outside, target_is_directory=True)
            return real_add_local_source(*args, **kwargs)

        with patch(
            "study_compiler.source_watch._add_local_source_unlocked",
            side_effect=swap_parent_then_add,
        ):
            with self.assertRaisesRegex(ValidationError, "safe regular file"):
                sync_next_source_change(self.workspace)

        self.assertEqual(list((self.workspace / "sources").iterdir()), [])

    def test_changed_bytes_after_scan_are_rejected_before_registration(self) -> None:
        configure_source_roots(self.workspace, [self.sources])
        watched = self.sources / "racing.md"
        watched.write_text("BEFORE\n", encoding="utf-8")

        def change_then_add(*args, **kwargs):
            watched.write_text("AFTER\n", encoding="utf-8")
            return real_add_local_source(*args, **kwargs)

        with patch(
            "study_compiler.source_watch._add_local_source_unlocked",
            side_effect=change_then_add,
        ):
            with self.assertRaisesRegex(StateError, "changed after scan"):
                sync_next_source_change(self.workspace)

        self.assertEqual(list((self.workspace / "sources").iterdir()), [])

    def test_duplicate_content_tracks_both_paths_without_duplicate_snapshot(
        self,
    ) -> None:
        configure_source_roots(self.workspace, [self.sources])
        first_path = self.sources / "first.md"
        second_path = self.sources / "second.md"
        first_path.write_text("Shared fragment.\n", encoding="utf-8")
        second_path.write_text("Shared fragment.\n", encoding="utf-8")

        first = sync_next_source_change(self.workspace)["synced"]
        second = sync_next_source_change(self.workspace)["synced"]

        self.assertTrue(second["duplicate"])
        self.assertEqual(first["source"]["id"], second["source"]["id"])
        self.assertEqual(len(list((self.workspace / "sources").iterdir())), 1)
        state = json.loads(
            (self.workspace / ".study-compiler/source-watch.json").read_text()
        )
        self.assertEqual(len(state["files"]), 2)

    def test_removing_a_root_removes_its_current_planner_watch_paths(self) -> None:
        other_root = self.root / "OtherNotes"
        other_root.mkdir()
        configure_source_roots(self.workspace, [self.sources, other_root])
        source = self.sources / "retired.md"
        source.write_text("Retired watched path.\n", encoding="utf-8")
        source_id = sync_next_source_change(self.workspace)["synced"]["source"]["id"]

        configure_source_roots(self.workspace, [other_root])

        manifest = json.loads(
            (self.workspace / ".study-compiler/manifest.json").read_text()
        )
        watch = json.loads(
            (self.workspace / ".study-compiler/source-watch.json").read_text()
        )
        self.assertNotIn("watch_paths", manifest["sources"][source_id])
        self.assertIn(str(source.resolve()), watch["files"])

    def test_ambiguous_same_hash_moves_stay_new_and_deleted(self) -> None:
        configure_source_roots(self.workspace, [self.sources])
        first = self.sources / "first.md"
        second = self.sources / "second.md"
        first.write_text("Repeated content.\n", encoding="utf-8")
        second.write_text("Repeated content.\n", encoding="utf-8")
        sync_next_source_change(self.workspace)
        sync_next_source_change(self.workspace)

        moved_first = self.sources / "moved-first.md"
        moved_second = self.sources / "moved-second.md"
        first.rename(moved_first)
        second.rename(moved_second)
        delta = scan_source_changes(self.workspace)

        self.assertEqual(delta["renamed"], [])
        self.assertEqual(len(delta["new"]), 2)
        self.assertEqual(len(delta["deleted"]), 2)

    def test_reports_oversized_supported_files_without_hashing_them(self) -> None:
        configure_source_roots(self.workspace, [self.sources])
        oversized = self.sources / "oversized.md"
        oversized.write_text("five!", encoding="utf-8")

        with patch("study_compiler.source_watch.DEFAULT_MAX_BYTES", 4):
            result = scan_source_changes(self.workspace)

        self.assertEqual(result["new"], [])
        self.assertEqual(
            result["skipped"],
            [{"path": str(oversized.resolve()), "reason": "too-large"}],
        )

    def test_ignores_hidden_and_unsupported_files_and_reports_symlinks(self) -> None:
        configure_source_roots(self.workspace, [self.sources])
        (self.sources / ".hidden.md").write_text("hidden", encoding="utf-8")
        hidden_directory = self.sources / ".git"
        hidden_directory.mkdir()
        (hidden_directory / "config.md").write_text("hidden", encoding="utf-8")
        (self.sources / "program.py").write_text("print('not learning material')")
        real = self.sources / "real.md"
        real.write_text("real", encoding="utf-8")
        alias = self.sources / "alias.md"
        alias.symlink_to(real)

        result = scan_source_changes(self.workspace)

        self.assertEqual(
            [item["path"] for item in result["new"]], [str(real.resolve())]
        )
        self.assertEqual(
            result["skipped"],
            [{"path": str(self.sources.resolve() / "alias.md"), "reason": "symlink"}],
        )

    def test_doctor_fails_closed_for_corrupt_watch_state(self) -> None:
        watch = self.workspace / ".study-compiler/source-watch.json"
        watch.write_text(
            '{"schema_version": 1, "roots": "not-a-list"}', encoding="utf-8"
        )

        report = inspect_vault(self.workspace)

        self.assertFalse(report["ok"])
        self.assertIn("source-watch", "\n".join(report["errors"]))

    def test_doctor_detects_manifest_watch_path_drift(self) -> None:
        configure_source_roots(self.workspace, [self.sources])
        source = self.sources / "drift.md"
        source.write_text("Tracked content.\n", encoding="utf-8")
        source_id = sync_next_source_change(self.workspace)["synced"]["source"]["id"]
        manifest_path = self.workspace / ".study-compiler/manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["sources"][source_id].pop("watch_paths")
        rendered = (
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        manifest_path.write_text(rendered, encoding="utf-8")
        (self.workspace / "sources" / source_id / "metadata.json").write_text(
            json.dumps(
                manifest["sources"][source_id],
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        report = inspect_vault(self.workspace)

        self.assertFalse(report["ok"])
        self.assertIn("watch paths do not match", "\n".join(report["errors"]))


if __name__ == "__main__":
    unittest.main()
