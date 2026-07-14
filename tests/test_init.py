import json
import tempfile
import unittest
from pathlib import Path

from study_compiler.digest import build_digest
from study_compiler.errors import StateError, ValidationError
from study_compiler.sources import add_snippet
from study_compiler.storage import initialize_vault, require_vault, safe_child


class InitializeVaultTests(unittest.TestCase):
    def test_initialization_migrates_legacy_learning_wiki_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir) / "Learn"
            legacy = vault / ".learning-wiki"
            legacy.mkdir(parents=True)
            (legacy / "manifest.json").write_text(
                '{"schema_version": 1, "sources": {}}\n', encoding="utf-8"
            )
            (legacy / "learning-state.json").write_text(
                '{"schema_version": 1, "lessons": {}}\n', encoding="utf-8"
            )

            result = initialize_vault(vault)

            self.assertTrue(result["migrated"])
            self.assertTrue((vault / ".study-compiler/manifest.json").is_file())
            self.assertFalse(legacy.exists())

    def test_initializes_documented_vault_layout_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir) / "Learn"

            result = initialize_vault(vault)

            self.assertEqual(result["workspace"], str(vault.resolve()))
            self.assertTrue(result["created"])
            for relative in (
                ".study-compiler/manifest.json",
                ".study-compiler/learning-state.json",
                ".study-compiler/link-patches",
                "sources",
                "_staging/lessons",
                "_staging/digests",
                "_staging/link-patches",
                "exports",
                "lessons",
                "reviews",
                "index.md",
                "log.md",
            ):
                self.assertTrue((vault / relative).exists(), relative)

            manifest = json.loads((vault / ".study-compiler/manifest.json").read_text())
            state = json.loads(
                (vault / ".study-compiler/learning-state.json").read_text()
            )
            self.assertEqual(manifest, {"schema_version": 1, "sources": {}})
            self.assertEqual(state, {"schema_version": 1, "lessons": {}})

    def test_reinitialization_preserves_existing_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir) / "Learn"
            initialize_vault(vault)
            manifest_path = vault / ".study-compiler/manifest.json"
            custom = {
                "schema_version": 1,
                "sources": {"source-1": {"title": "Keep me"}},
            }
            manifest_path.write_text(json.dumps(custom), encoding="utf-8")

            result = initialize_vault(vault)

            self.assertFalse(result["created"])
            self.assertEqual(json.loads(manifest_path.read_text()), custom)

    def test_safe_child_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir) / "Learn"
            vault.mkdir()

            with self.assertRaises(ValidationError):
                safe_child(vault, "..", "outside.md")

    def test_initialization_rejects_a_symlinked_control_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "Learn"
            vault.mkdir()
            outside_log = root / "outside.log"
            outside_log.write_text("outside\n", encoding="utf-8")
            (vault / "log.md").symlink_to(outside_log)

            with self.assertRaisesRegex(
                (StateError, ValidationError), "symlink|workspace"
            ):
                initialize_vault(vault)

            self.assertEqual(outside_log.read_text(encoding="utf-8"), "outside\n")

    def test_require_vault_rejects_a_symlinked_state_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "Learn"
            external_state = root / "external-state"
            external_state.mkdir()
            (external_state / "manifest.json").write_text(
                '{"schema_version": 1, "sources": {}}', encoding="utf-8"
            )
            (external_state / "learning-state.json").write_text(
                '{"schema_version": 1, "lessons": {}}', encoding="utf-8"
            )
            vault.mkdir()
            (vault / ".study-compiler").symlink_to(
                external_state, target_is_directory=True
            )

            with self.assertRaisesRegex(StateError, "symlink"):
                require_vault(vault)

    def test_digest_rejects_a_symlinked_staging_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "Learn"
            outside = root / "outside"
            outside.mkdir()
            initialize_vault(vault)
            add_snippet(vault, title="Source", text="Grounded source.")
            digests = vault / "_staging/digests"
            digests.rmdir()
            digests.symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(
                (StateError, ValidationError), "symlink|workspace"
            ):
                build_digest(vault)

            self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
