import unittest
from pathlib import Path


class ProductNamingTests(unittest.TestCase):
    def test_public_product_surfaces_use_study_compiler_name(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        pyproject = (project_root / "pyproject.toml").read_text(encoding="utf-8")
        readme = (project_root / "README.md").read_text(encoding="utf-8")

        self.assertIn('name = "study-compiler"', pyproject)
        self.assertIn('study-compiler = "study_compiler.cli:entrypoint"', pyproject)
        self.assertTrue((project_root / "src/study_compiler/__main__.py").is_file())
        self.assertFalse((project_root / "src/learning_wiki/__init__.py").exists())
        self.assertTrue(readme.startswith("# Study Compiler\n"))

    def test_installer_help_is_english_only(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        install_script = (project_root / "install.sh").read_text(encoding="utf-8")

        self.assertIn("Install Study Compiler", install_script)
        self.assertNotIn("安装", install_script)
        self.assertNotIn("正在", install_script)


if __name__ == "__main__":
    unittest.main()
