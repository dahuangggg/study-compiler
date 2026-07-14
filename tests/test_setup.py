import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from learning_wiki.errors import StateError
from learning_wiki.installer import SKILL_NAMES, install_skills


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
                    "LEARNING_WIKI_BIN_DIR": str(root / "bin"),
                    "LEARNING_WIKI_TOOL_DIR": str(root / "tools"),
                    "UV_CACHE_DIR": str(root / "uv-cache"),
                }
            )

            completed = subprocess.run(
                [
                    "bash",
                    str(project_root / "install.sh"),
                    "--vault",
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
            payload = json.loads(completed.stdout.splitlines()[-1])
            self.assertEqual(payload["vault"]["vault"], str(vault.resolve()))
            self.assertTrue((root / "bin/learning-wiki").exists())
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
            self.assertIn("./install.sh --vault", completed.stdout)
            self.assertFalse((Path(temp_dir) / ".codex").exists())

    def test_setup_initializes_vault_and_installs_all_bundled_skills(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            vault = root / "Learn"
            target = root / "agent-skills"

            completed = self.run_cli(
                "setup",
                "--vault",
                str(vault),
                "--target",
                str(target),
                "--copy",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["vault"]["vault"], str(vault.resolve()))
            self.assertEqual(payload["targets"][0]["installed"], list(SKILL_NAMES))
            self.assertTrue((vault / ".learning-wiki/manifest.json").is_file())
            for name in SKILL_NAMES:
                self.assertTrue((target / name / "SKILL.md").is_file())
                self.assertFalse((target / name).is_symlink())

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
