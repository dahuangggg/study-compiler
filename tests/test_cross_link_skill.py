import unittest
from pathlib import Path

from study_compiler.installer import SKILL_NAMES, locate_bundled_skills


class LearningCrossLinkSkillTests(unittest.TestCase):
    def test_cross_link_skill_is_bundled_with_safe_staging_rules(self) -> None:
        skill = locate_bundled_skills() / "learning-cross-link" / "SKILL.md"
        content = skill.read_text(encoding="utf-8")

        self.assertIn("learning-cross-link", SKILL_NAMES)
        self.assertIn("study-compiler graph check", content)
        self.assertIn("study-compiler link stage", content)
        self.assertIn("scores of 6 or more", content)
        self.assertIn("scores from 3 to 5", content)
        self.assertIn("Never run `link apply`", content)
        for relation_type in (
            "extends",
            "implements",
            "contradicts",
            "derived_from",
            "uses",
            "replaces",
            "related_to",
        ):
            self.assertIn(f"`{relation_type}`", content)

    def test_scheduled_run_routes_no_op_graph_maintenance_without_approval(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        run_skill = (root / "skills/learning-run/SKILL.md").read_text(encoding="utf-8")
        approval_skill = (root / "skills/learning-stage-commit/SKILL.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("$learning-cross-link", run_skill)
        self.assertIn("never changes or approves notes", run_skill)
        self.assertIn("link apply <workspace> <patch-id> --approve", approval_skill)
        self.assertIn("Approval of one never authorizes the other", approval_skill)


if __name__ == "__main__":
    unittest.main()
