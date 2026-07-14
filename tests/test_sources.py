import hashlib
import json
import stat
import tempfile
import unittest
from pathlib import Path

from study_compiler.errors import ValidationError
from study_compiler.sources import (
    add_local_source,
    add_snippet,
    add_url_source,
    attach_web_snapshot,
    list_sources,
)
from study_compiler.storage import initialize_vault


class SourceRegistryTests(unittest.TestCase):
    def test_imports_local_paper_as_immutable_hashed_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "Learn"
            paper = root / "paper.pdf"
            content = b"%PDF-1.7\nLearning material\n"
            paper.write_bytes(content)
            initialize_vault(vault)

            result = add_local_source(
                vault, paper, kind="paper", title="A useful paper"
            )

            snapshot = vault / result["source"]["snapshot"]
            self.assertEqual(snapshot.read_bytes(), content)
            self.assertEqual(
                result["source"]["sha256"], hashlib.sha256(content).hexdigest()
            )
            self.assertEqual(result["source"]["kind"], "paper")
            self.assertFalse(result["duplicate"])
            self.assertEqual(stat.S_IMODE(snapshot.stat().st_mode), 0o444)

            duplicate = add_local_source(vault, paper, kind="paper")
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(duplicate["source"]["id"], result["source"]["id"])
            self.assertEqual(len(list_sources(vault)), 1)

    def test_rejects_oversized_local_source_before_copying(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "Learn"
            document = root / "large.txt"
            document.write_text("too large", encoding="utf-8")
            initialize_vault(vault)

            with self.assertRaisesRegex(ValidationError, "exceeds"):
                add_local_source(vault, document, kind="document", max_bytes=3)

            self.assertEqual(list_sources(vault), [])

    def test_registers_https_url_without_fetching(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir) / "Learn"
            initialize_vault(vault)

            result = add_url_source(
                vault,
                "https://Example.com/paper?id=42#section",
                title="Web paper",
            )

            source = result["source"]
            self.assertEqual(source["origin"], "https://example.com/paper?id=42")
            self.assertIsNone(source["snapshot"])
            self.assertEqual(source["kind"], "web")
            self.assertEqual(len(list_sources(vault)), 1)

    def test_attaches_agent_fetched_snapshot_to_registered_web_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "Learn"
            snapshot_input = root / "article.md"
            snapshot_input.write_text(
                "# Article\n\nA grounded explanation.", encoding="utf-8"
            )
            initialize_vault(vault)
            source = add_url_source(vault, "https://example.com/article")["source"]

            result = attach_web_snapshot(vault, source["id"], snapshot_input)

            attached = result["source"]
            snapshot = vault / attached["snapshot"]
            self.assertEqual(snapshot.read_text(), snapshot_input.read_text())
            self.assertEqual(attached["status"], "new")
            self.assertEqual(attached["reference_sha256"], source["sha256"])
            self.assertEqual(
                attached["sha256"],
                hashlib.sha256(snapshot_input.read_bytes()).hexdigest(),
            )
            self.assertEqual(stat.S_IMODE(snapshot.stat().st_mode), 0o444)

    def test_rejects_unsafe_web_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir) / "Learn"
            initialize_vault(vault)

            unsafe_urls = (
                "http://example.com/article",
                "https://localhost/private",
                "https://127.0.0.1/private",
                "https://169.254.169.254/latest/meta-data",
                "https://10.0.0.1/private",
                "https://user:password@example.com/private",
            )
            for url in unsafe_urls:
                with self.subTest(url=url):
                    with self.assertRaises(ValidationError):
                        add_url_source(vault, url)

    def test_saves_chat_snippet_as_source_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir) / "Learn"
            initialize_vault(vault)

            result = add_snippet(
                vault,
                title="Agent explanation",
                text="User: explain attention\nAgent: attention mixes context.",
                kind="chat",
            )

            source = result["source"]
            snapshot = vault / source["snapshot"]
            self.assertIn(
                "attention mixes context", snapshot.read_text(encoding="utf-8")
            )
            metadata = json.loads((snapshot.parent / "metadata.json").read_text())
            self.assertEqual(metadata["id"], source["id"])


if __name__ == "__main__":
    unittest.main()
