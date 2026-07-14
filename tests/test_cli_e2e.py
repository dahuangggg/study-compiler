import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from study_compiler.cli import _read_text_file
from study_compiler.errors import ValidationError
from tests.test_lessons import valid_body


class CommandLineEndToEndTests(unittest.TestCase):
    def test_text_file_input_is_bounded_before_decoding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "oversized.md"
            path.write_bytes(b"1234")

            with self.assertRaisesRegex(ValidationError, "byte limit"):
                _read_text_file(path, "test input", max_bytes=3)

    def test_source_to_approved_learning_note_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "Learn"
            lesson_body = root / "lesson.md"
            retelling = root / "retelling.md"
            retelling.write_text(
                "Each token gathers useful context from other tokens.", encoding="utf-8"
            )

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
            lesson_body.write_text(valid_body(source_id), encoding="utf-8")

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

            coverage = self.run_cli(
                "source",
                "coverage",
                str(vault),
                source_id,
                "--status",
                "complete",
            )
            self.assertEqual(coverage.returncode, 0, coverage.stderr)
            self.assertEqual(
                json.loads(coverage.stdout)["source"]["coverage_status"],
                "complete",
            )

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

            promoted = self.run_cli(
                "draft", "promote", str(vault), "attention", "--approve"
            )
            self.assertEqual(promoted.returncode, 0, promoted.stderr)

            digest = self.run_cli("digest", "build", str(vault), "--period", "weekly")
            self.assertEqual(digest.returncode, 0, digest.stderr)

            doctor = self.run_cli("doctor", str(vault), "--json")
            self.assertEqual(doctor.returncode, 0, doctor.stderr)
            report = json.loads(doctor.stdout)
            self.assertTrue(report["ok"], report["errors"])
            self.assertTrue((vault / "lessons/attention.md").is_file())

    def test_typed_link_patch_and_graph_cli_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "Learn"
            self.assertEqual(self.run_cli("init", str(vault)).returncode, 0)
            imported = self.run_cli(
                "source",
                "add-snippet",
                str(vault),
                "--title",
                "Graph source",
                "--text",
                "Alpha uses beta.",
            )
            source_id = json.loads(imported.stdout)["source"]["id"]
            body = root / "lesson.md"
            body.write_text(valid_body(source_id), encoding="utf-8")
            for slug in ("alpha", "beta"):
                staged = self.run_cli(
                    "draft",
                    "create",
                    str(vault),
                    "--slug",
                    slug,
                    "--title",
                    slug.title(),
                    "--source",
                    source_id,
                    "--content-file",
                    str(body),
                )
                self.assertEqual(staged.returncode, 0, staged.stderr)
                promoted = self.run_cli(
                    "draft", "promote", str(vault), slug, "--approve"
                )
                self.assertEqual(promoted.returncode, 0, promoted.stderr)

            proposal = root / "proposal.json"
            proposal.write_text(
                json.dumps(
                    {
                        "relations": [
                            {
                                "target": "beta",
                                "type": "uses",
                                "confidence": "extracted",
                                "score": 6,
                                "reason": "Alpha directly uses beta.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            staged_patch = self.run_cli(
                "link",
                "stage",
                str(vault),
                "alpha",
                "--proposal-file",
                str(proposal),
            )
            self.assertEqual(staged_patch.returncode, 0, staged_patch.stderr)
            patch_id = json.loads(staged_patch.stdout)["patch_id"]

            unapproved = self.run_cli("link", "apply", str(vault), patch_id)
            self.assertEqual(unapproved.returncode, 2)
            self.assertIn("--approve", unapproved.stderr)
            applied = self.run_cli("link", "apply", str(vault), patch_id, "--approve")
            self.assertEqual(applied.returncode, 0, applied.stderr)

            checked = self.run_cli("graph", "check", str(vault), "--json")
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual(json.loads(checked.stdout)["counts"]["typed_edges"], 1)
            exported = self.run_cli("graph", "export", str(vault))
            self.assertEqual(exported.returncode, 0, exported.stderr)
            self.assertTrue(json.loads(exported.stdout)["directed"])
            doctor = self.run_cli("doctor", str(vault), "--json")
            self.assertEqual(doctor.returncode, 0, doctor.stderr)
            self.assertEqual(
                json.loads(doctor.stdout)["counts"]["typed_relationships"], 1
            )

    def test_link_stage_rejects_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "Learn"
            self.assertEqual(self.run_cli("init", str(vault)).returncode, 0)
            proposal = root / "proposal.json"
            proposal.write_text('{"relations": [], "relations": []}', encoding="utf-8")

            result = self.run_cli(
                "link",
                "stage",
                str(vault),
                "alpha",
                "--proposal-file",
                str(proposal),
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("duplicate", result.stderr.lower())

    def test_link_stage_rejects_pathological_json_without_a_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "Learn"
            self.assertEqual(self.run_cli("init", str(vault)).returncode, 0)
            proposal = root / "proposal.json"
            proposal.write_text(
                '{"relations": [{"target": "beta", "type": "uses", '
                '"confidence": "extracted", "score": '
                + ("9" * 10_000)
                + ', "reason": "direct"}]}',
                encoding="utf-8",
            )

            result = self.run_cli(
                "link",
                "stage",
                str(vault),
                "alpha",
                "--proposal-file",
                str(proposal),
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("invalid JSON", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_graph_export_cannot_overwrite_managed_workspace_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir) / "Learn"
            self.assertEqual(self.run_cli("init", str(vault)).returncode, 0)
            manifest = vault / ".study-compiler/manifest.json"
            before = manifest.read_bytes()

            unsafe = self.run_cli(
                "graph",
                "export",
                str(vault),
                "--output",
                ".study-compiler/manifest.json",
            )

            self.assertEqual(unsafe.returncode, 2)
            self.assertEqual(manifest.read_bytes(), before)
            safe = self.run_cli("graph", "export", str(vault), "--output", "graph.json")
            self.assertEqual(safe.returncode, 0, safe.stderr)
            self.assertEqual(json.loads(safe.stdout)["path"], "exports/graph.json")
            self.assertTrue((vault / "exports/graph.json").is_file())

    def test_link_commands_reject_nonstring_enums_without_tracebacks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "Learn"
            self.assertEqual(self.run_cli("init", str(vault)).returncode, 0)
            for field, value in (("type", []), ("confidence", {})):
                proposal = root / f"{field}.json"
                relation = {
                    "target": "beta",
                    "type": "uses",
                    "confidence": "extracted",
                    "score": 6,
                    "reason": "direct",
                }
                relation[field] = value
                proposal.write_text(
                    json.dumps({"relations": [relation]}), encoding="utf-8"
                )
                result = self.run_cli(
                    "link",
                    "stage",
                    str(vault),
                    "alpha",
                    "--proposal-file",
                    str(proposal),
                )
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("Traceback", result.stderr)

            patch_id = "lp-0000000000000000"
            patch_path = vault / "_staging/link-patches" / f"{patch_id}.json"
            patch_path.write_text('{"status": []}', encoding="utf-8")
            shown = self.run_cli("link", "show", str(vault), patch_id)
            self.assertEqual(shown.returncode, 2)
            self.assertNotIn("Traceback", shown.stderr)

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        project_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(project_root / "src")
        return subprocess.run(
            [sys.executable, "-m", "study_compiler", *arguments],
            cwd=project_root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
