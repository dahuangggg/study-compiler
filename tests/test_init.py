import json
import tempfile
import unittest
from pathlib import Path

from learning_wiki.errors import ValidationError
from learning_wiki.storage import initialize_vault, safe_child


class InitializeVaultTests(unittest.TestCase):
    def test_initializes_documented_vault_layout_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir) / "Learn"

            result = initialize_vault(vault)

            self.assertEqual(result["vault"], str(vault.resolve()))
            self.assertTrue(result["created"])
            for relative in (
                ".learning-wiki/manifest.json",
                ".learning-wiki/learning-state.json",
                "sources",
                "_staging/lessons",
                "_staging/digests",
                "lessons",
                "reviews",
                "index.md",
                "log.md",
            ):
                self.assertTrue((vault / relative).exists(), relative)

            manifest = json.loads((vault / ".learning-wiki/manifest.json").read_text())
            state = json.loads(
                (vault / ".learning-wiki/learning-state.json").read_text()
            )
            self.assertEqual(manifest, {"schema_version": 1, "sources": {}})
            self.assertEqual(state, {"schema_version": 1, "lessons": {}})

    def test_reinitialization_preserves_existing_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir) / "Learn"
            initialize_vault(vault)
            manifest_path = vault / ".learning-wiki/manifest.json"
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


if __name__ == "__main__":
    unittest.main()
