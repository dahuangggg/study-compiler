import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_lessons import VALID_BODY


class CommandLineEndToEndTests(unittest.TestCase):
    def test_source_to_approved_learning_note_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "Learn"
            lesson_body = root / "lesson.md"
            lesson_body.write_text(VALID_BODY, encoding="utf-8")
            retelling = root / "retelling.md"
            retelling.write_text("Each token gathers useful context from other tokens.", encoding="utf-8")

            initialized = self.run_cli("init", str(vault))
            self.assertEqual(initialized.returncode, 0, initialized.stderr)

            imported = self.run_cli(
                "source",
                "add-snippet",
                str(vault),
                "--kind",
                "chat",
                "--title",
                "Attention chat",
                "--text",
                "Attention lets a token combine context from other tokens.",
            )
            self.assertEqual(imported.returncode, 0, imported.stderr)
            source_id = json.loads(imported.stdout)["source"]["id"]

            staged = self.run_cli(
                "draft",
                "create",
                str(vault),
                "--slug",
                "attention",
                "--title",
                "Understanding Attention",
                "--source",
                source_id,
                "--content-file",
                str(lesson_body),
            )
            self.assertEqual(staged.returncode, 0, staged.stderr)

            recalled = self.run_cli(
                "recall",
                "record",
                str(vault),
                "attention",
                "--score",
                "4",
                "--retelling-file",
                str(retelling),
            )
            self.assertEqual(recalled.returncode, 0, recalled.stderr)

            unapproved = self.run_cli("draft", "promote", str(vault), "attention")
            self.assertEqual(unapproved.returncode, 2)
            self.assertIn("--approve", unapproved.stderr)

            promoted = self.run_cli("draft", "promote", str(vault), "attention", "--approve")
            self.assertEqual(promoted.returncode, 0, promoted.stderr)

            digest = self.run_cli("digest", "build", str(vault), "--period", "weekly")
            self.assertEqual(digest.returncode, 0, digest.stderr)

            doctor = self.run_cli("doctor", str(vault), "--json")
            self.assertEqual(doctor.returncode, 0, doctor.stderr)
            report = json.loads(doctor.stdout)
            self.assertTrue(report["ok"], report["errors"])
            self.assertTrue((vault / "lessons/attention.md").is_file())

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        project_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(project_root / "src")
        return subprocess.run(
            [sys.executable, "-m", "learning_wiki", *arguments],
            cwd=project_root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()

