import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path

from study_compiler.errors import StateError
from study_compiler.installer import SKILL_NAMES, choose_agent_names, install_skills
from tests.test_lessons import valid_body


class SkillInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.skills = self.root / "bundled-skills"
        for name in SKILL_NAMES:
            skill = self.skills / name
            (skill / "agents").mkdir(parents=True)
            (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
            (skill / "agents/openai.yaml").write_text(
                f"name: {name}\n", encoding="utf-8"
            )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_symlink_install_is_idempotent(self) -> None:
        target = self.root / "agent-skills"

        first = install_skills(self.skills, [target])
        second = install_skills(self.skills, [target])

        self.assertEqual(first[0]["installed"], list(SKILL_NAMES))
        self.assertEqual(first[0]["unchanged"], [])
        self.assertEqual(second[0]["installed"], [])
        self.assertEqual(second[0]["unchanged"], list(SKILL_NAMES))
        for name in SKILL_NAMES:
            self.assertTrue((target / name).is_symlink())
            self.assertEqual((target / name).resolve(), (self.skills / name).resolve())

    def test_existing_skill_is_preserved_without_force(self) -> None:
        target = self.root / "agent-skills"
        conflict = target / SKILL_NAMES[0]
        conflict.mkdir(parents=True)
        marker = conflict / "mine.txt"
        marker.write_text("keep me", encoding="utf-8")

        with self.assertRaisesRegex(StateError, "already exists"):
            install_skills(self.skills, [target])

        self.assertEqual(marker.read_text(encoding="utf-8"), "keep me")

    def test_legacy_managed_skill_links_are_replaced_without_force(self) -> None:
        target = self.root / "agent-skills"
        legacy_root = self.root / "learning-wiki/share/learning-wiki/skills"
        for name in SKILL_NAMES:
            legacy_skill = legacy_root / name
            legacy_skill.mkdir(parents=True)
            (legacy_skill / "SKILL.md").write_text("legacy\n", encoding="utf-8")
            target.mkdir(parents=True, exist_ok=True)
            (target / name).symlink_to(legacy_skill, target_is_directory=True)

        result = install_skills(self.skills, [target])

        self.assertEqual(result[0]["installed"], list(SKILL_NAMES))
        for name in SKILL_NAMES:
            self.assertEqual((target / name).resolve(), (self.skills / name).resolve())

    def test_copy_install_is_idempotent_and_force_replaces_changed_copy(self) -> None:
        target = self.root / "agent-skills"
        install_skills(self.skills, [target], copy=True)
        changed = target / SKILL_NAMES[0] / "SKILL.md"
        changed.write_text("changed", encoding="utf-8")

        with self.assertRaisesRegex(StateError, "already exists"):
            install_skills(self.skills, [target], copy=True)

        result = install_skills(self.skills, [target], copy=True, force=True)

        self.assertFalse((target / SKILL_NAMES[0]).is_symlink())
        self.assertEqual(changed.read_text(encoding="utf-8"), f"# {SKILL_NAMES[0]}\n")
        self.assertIn(SKILL_NAMES[0], result[0]["installed"])

    def test_agent_selector_supports_arrow_navigation_and_space_toggle(self) -> None:
        output = StringIO()

        selected = choose_agent_names(
            ["codex"],
            keys=iter(["down", "space", "enter"]),
            output=output,
        )

        self.assertEqual(selected, ["codex", "claude"])
        self.assertIn("↑/↓ Move", output.getvalue())
        self.assertNotIn("选择", output.getvalue())
        self.assertIn("Codex", output.getvalue())
        self.assertIn("Claude Code", output.getvalue())


class SetupCommandTests(unittest.TestCase):
    def test_one_command_installer_works_in_an_isolated_home(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            vault = root / "Learn"
            environment = os.environ.copy()
            environment.pop("PYTHONPATH", None)
            environment.update(
                {
                    "HOME": str(home),
                    "STUDY_COMPILER_BIN_DIR": str(root / "bin"),
                    "STUDY_COMPILER_TOOL_DIR": str(root / "tools"),
                    "UV_CACHE_DIR": str(root / "uv-cache"),
                }
            )

            completed = subprocess.run(
                [
                    "bash",
                    str(project_root / "install.sh"),
                    "--workspace",
                    str(vault),
                    "--agent",
                    "codex",
                ],
                cwd=project_root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("Study Compiler is ready", completed.stdout)
            self.assertIn(str(vault.resolve()), completed.stdout)
            self.assertNotIn('{"targets":', completed.stdout)
            combined_output = completed.stdout + completed.stderr
            self.assertNotIn("Resolved 1 package", combined_output)
            self.assertNotIn("Prepared 1 package", combined_output)
            self.assertNotIn("study-compiler==", combined_output)
            self.assertTrue((root / "bin/study-compiler").exists())
            for name in SKILL_NAMES:
                installed = home / ".codex/skills" / name
                self.assertTrue(installed.is_symlink())
                self.assertTrue(
                    installed.resolve().is_relative_to((root / "tools").resolve())
                )
                self.assertTrue((installed / "SKILL.md").is_file())

    def test_one_command_installer_help_does_not_install(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            completed = subprocess.run(
                ["bash", str(project_root / "install.sh"), "--help"],
                cwd=project_root,
                env={**os.environ, "HOME": temp_dir},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("./install.sh --workspace", completed.stdout)
            self.assertFalse((Path(temp_dir) / ".codex").exists())

    @unittest.skipUnless(shutil.which("uv"), "uv is required for cache regression")
    def test_reinstall_rebuilds_changed_local_source_at_the_same_version(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            checkout = root / "checkout"
            shutil.copytree(
                project_root,
                checkout,
                ignore=shutil.ignore_patterns(
                    ".git", ".venv", "build", "dist", "*.egg-info", "__pycache__"
                ),
            )
            package_init = checkout / "src/study_compiler/__init__.py"
            package_init.write_text(
                package_init.read_text(encoding="utf-8")
                + '\nINSTALL_TEST_MARKER = "first"\n',
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.pop("PYTHONPATH", None)
            environment.update(
                {
                    "HOME": str(root / "home"),
                    "STUDY_COMPILER_BIN_DIR": str(root / "bin"),
                    "STUDY_COMPILER_TOOL_DIR": str(root / "tools"),
                    "UV_CACHE_DIR": str(root / "uv-cache"),
                }
            )
            command = [
                "bash",
                str(checkout / "install.sh"),
                "--agent",
                "codex",
                "--workspace",
                str(root / "Learn"),
            ]

            first = subprocess.run(
                command,
                cwd=checkout,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            installed_python = root / "tools/study-compiler/bin/python"
            installed_init = next(
                (root / "tools/study-compiler").glob(
                    "lib/python*/site-packages/study_compiler/__init__.py"
                )
            )
            self.assertIn(
                'INSTALL_TEST_MARKER = "first"',
                installed_init.read_text(encoding="utf-8"),
                first.stderr,
            )
            first_marker = subprocess.run(
                [
                    str(installed_python),
                    "-c",
                    "import study_compiler; print(study_compiler.INSTALL_TEST_MARKER)",
                ],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(first_marker.returncode, 0, first_marker.stderr)
            self.assertEqual(first_marker.stdout.strip(), "first")
            package_init.write_text(
                package_init.read_text(encoding="utf-8").replace(
                    'INSTALL_TEST_MARKER = "first"',
                    'INSTALL_TEST_MARKER = "second"',
                ),
                encoding="utf-8",
            )

            second = subprocess.run(
                command,
                cwd=checkout,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            marker = subprocess.run(
                [
                    str(installed_python),
                    "-c",
                    "import study_compiler; print(study_compiler.INSTALL_TEST_MARKER)",
                ],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(marker.returncode, 0, marker.stderr)
            self.assertEqual(marker.stdout.strip(), "second")

    def test_setup_prints_human_readable_summary_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "Learn"
            target = root / "agent-skills"

            completed = self.run_cli(
                "setup",
                "--workspace",
                str(vault),
                "--target",
                str(target),
                "--copy",
                home=root / "home",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("Study Compiler is ready", completed.stdout)
            self.assertIn("7 skills ready", completed.stdout)
            self.assertIn(str(vault.resolve()), completed.stdout)
            self.assertNotIn('{"targets":', completed.stdout)
            self.assertTrue((vault / ".study-compiler/manifest.json").is_file())
            for name in SKILL_NAMES:
                self.assertTrue((target / name / "SKILL.md").is_file())
                self.assertFalse((target / name).is_symlink())
            config = json.loads(
                (root / "home/.study-compiler/config.json").read_text(encoding="utf-8")
            )
            self.assertEqual(config["workspace"], str(vault.resolve()))

    def test_setup_json_flag_keeps_machine_readable_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "Learn"
            target = root / "agent-skills"

            completed = self.run_cli(
                "setup",
                "--workspace",
                str(vault),
                "--target",
                str(target),
                "--copy",
                "--json",
                home=root / "home",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["workspace"]["workspace"], str(vault.resolve()))
            self.assertEqual(payload["targets"][0]["installed"], list(SKILL_NAMES))

    def test_setup_configures_watched_source_roots_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "Learn"
            notes = root / "Notes"
            chats = root / "Chats"
            notes.mkdir()
            chats.mkdir()

            completed = self.run_cli(
                "setup",
                "--workspace",
                str(vault),
                "--source-root",
                str(notes),
                "--source-root",
                str(chats),
                "--target",
                str(root / "agent-skills"),
                "--copy",
                "--json",
                home=root / "home",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(
                payload["workspace"]["source_watch"]["roots"],
                sorted([str(notes.resolve()), str(chats.resolve())]),
            )
            watch = json.loads(
                (vault / ".study-compiler/source-watch.json").read_text()
            )
            self.assertEqual(
                watch["roots"], sorted([str(notes.resolve()), str(chats.resolve())])
            )

    def test_configured_cli_run_lifecycle_is_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            vault = root / "Learn"
            target = root / "agent-skills"
            setup = self.run_cli(
                "setup",
                "--workspace",
                str(vault),
                "--target",
                str(target),
                "--copy",
                "--json",
                home=home,
            )
            self.assertEqual(setup.returncode, 0, setup.stderr)
            imported = self.run_cli(
                "source",
                "add-snippet",
                str(vault),
                "--title",
                "Run source",
                "--text",
                "A source for the configured run.",
                home=home,
            )
            self.assertEqual(imported.returncode, 0, imported.stderr)
            source_id = json.loads(imported.stdout)["source"]["id"]

            started_process = self.run_cli(
                "run", "start", "--period", "daily", home=home
            )
            self.assertEqual(started_process.returncode, 0, started_process.stderr)
            started = json.loads(started_process.stdout)
            resumed = self.run_cli("run", "resume", started["run_id"], home=home)
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            self.assertEqual(json.loads(resumed.stdout)["status"], "resumed")

            body_path = root / "lesson-body.md"
            body_path.write_text(valid_body(source_id), encoding="utf-8")
            drafted = self.run_cli(
                "draft",
                "create",
                str(vault),
                "--slug",
                "configured-run",
                "--title",
                "Configured Run",
                "--source",
                source_id,
                "--content-file",
                str(body_path),
                home=home,
            )
            self.assertEqual(drafted.returncode, 0, drafted.stderr)

            action = started["next_action"]
            artifact = vault / started["artifact_path"]
            artifact.write_text(
                "---\n"
                f"run_id: {json.dumps(started['run_id'])}\n"
                f"action_type: {json.dumps(action['type'])}\n"
                f"action_id: {json.dumps(action['id'])}\n"
                "---\n\n# Learning Session\n\nConfigured CLI result.\n",
                encoding="utf-8",
            )
            finished = self.run_cli(
                "run",
                "finish",
                started["run_id"],
                "--artifact",
                str(artifact),
                home=home,
            )
            self.assertEqual(finished.returncode, 0, finished.stderr)
            self.assertEqual(json.loads(finished.stdout)["status"], "completed")

            status = self.run_cli("status", "--json", home=home)
            self.assertEqual(status.returncode, 0, status.stderr)
            status_payload = json.loads(status.stdout)
            self.assertEqual(status_payload["workspace"], str(vault.resolve()))
            self.assertIsNone(status_payload["active_run"])

            doctor = self.run_cli("doctor", str(vault), "--json", home=home)
            self.assertEqual(doctor.returncode, 0, doctor.stderr)
            doctor_payload = json.loads(doctor.stdout)
            self.assertTrue(doctor_payload["ok"], doctor_payload["errors"])

    def run_cli(
        self, *arguments: str, home: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        project_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(project_root / "src")
        if home is not None:
            environment["HOME"] = str(home)
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
