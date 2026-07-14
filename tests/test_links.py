import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from study_compiler.doctor import inspect_vault
from study_compiler.errors import StateError, ValidationError
from study_compiler.lessons import create_draft, parse_lesson, promote_draft
from study_compiler.links import (
    apply_link_patch,
    export_graph,
    graph_report,
    list_link_patches,
    stage_link_patch,
)
from study_compiler.sources import add_snippet
from study_compiler.storage import initialize_vault

from tests.test_lessons import valid_body


def relation(
    target: str,
    relation_type: str = "uses",
    *,
    confidence: str = "extracted",
    score: int = 6,
    reason: str = "The source lesson directly relies on the target concept.",
) -> dict[str, object]:
    return {
        "target": target,
        "type": relation_type,
        "confidence": confidence,
        "score": score,
        "reason": reason,
    }


class TypedLinkPatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp_dir.name) / "Learn"
        initialize_vault(self.vault)
        self.source = add_snippet(
            self.vault,
            title="Attention",
            text="Attention uses token representations.",
        )["source"]
        for slug, title in (
            ("attention", "Attention"),
            ("token-representations", "Token Representations"),
        ):
            create_draft(
                self.vault,
                slug=slug,
                title=title,
                source_ids=[self.source["id"]],
                body=valid_body(self.source["id"]),
            )
            promote_draft(self.vault, slug, approve=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_stage_is_additive_idempotent_and_does_not_edit_approved_notes(
        self,
    ) -> None:
        attention = self.vault / "lessons/attention.md"
        before = attention.read_bytes()
        now = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)

        first = stage_link_patch(
            self.vault,
            "attention",
            relations=[relation("token-representations")],
            now=now,
        )
        second = stage_link_patch(
            self.vault,
            "attention",
            relations=[relation("token-representations")],
            now=datetime(2026, 7, 14, 10, 0, tzinfo=UTC),
        )

        self.assertEqual(first["patch_id"], second["patch_id"])
        self.assertEqual(first["path"], second["path"])
        self.assertEqual(first["created_at"], second["created_at"])
        self.assertEqual(attention.read_bytes(), before)
        patch = json.loads((self.vault / first["path"]).read_text(encoding="utf-8"))
        self.assertEqual(patch["status"], "staged")
        self.assertEqual(patch["source_slug"], "attention")
        self.assertRegex(patch["base_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(patch["relations"][0]["target"], "token-representations")
        self.assertEqual(len(list_link_patches(self.vault)), 1)

    def test_stage_rejects_ambiguous_invalid_or_unapproved_relationships(self) -> None:
        create_draft(
            self.vault,
            slug="draft-only",
            title="Draft Only",
            source_ids=[self.source["id"]],
            body=valid_body(self.source["id"]),
        )

        cases = (
            ([relation("attention")], "self"),
            ([relation("missing")], "approved"),
            ([relation("draft-only")], "approved"),
            ([relation("token-representations", relation_type="invented")], "type"),
            ([relation("token-representations", score=2)], "score"),
            ([relation("token-representations", score=14)], "score"),
            (
                [
                    relation(
                        "token-representations",
                        confidence="extracted",
                        score=4,
                    )
                ],
                "confidence",
            ),
            (
                [
                    relation("token-representations", relation_type="uses"),
                    relation("token-representations", relation_type="extends"),
                ],
                "multiple types",
            ),
        )
        for relations, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex((StateError, ValidationError), message):
                    stage_link_patch(
                        self.vault,
                        "attention",
                        relations=relations,
                    )

    def test_stage_rejects_a_second_type_for_an_existing_target(self) -> None:
        first = stage_link_patch(
            self.vault,
            "attention",
            relations=[relation("token-representations", relation_type="uses")],
        )
        apply_link_patch(self.vault, first["patch_id"], approve=True)

        with self.assertRaisesRegex(StateError, "already has a relationship"):
            stage_link_patch(
                self.vault,
                "attention",
                relations=[relation("token-representations", relation_type="extends")],
            )

    def test_stage_rejects_relationships_beyond_the_lesson_limit(self) -> None:
        first = stage_link_patch(
            self.vault,
            "attention",
            relations=[relation("token-representations")],
        )
        apply_link_patch(self.vault, first["patch_id"], approve=True)
        create_draft(
            self.vault,
            slug="another-target",
            title="Another Target",
            source_ids=[self.source["id"]],
            body=valid_body(self.source["id"]),
        )
        promote_draft(self.vault, "another-target", approve=True)

        with patch("study_compiler.schemas.MAX_LESSON_RELATIONSHIPS", 1):
            with self.assertRaisesRegex(ValidationError, "bounded"):
                stage_link_patch(
                    self.vault,
                    "attention",
                    relations=[relation("another-target")],
                )

    def test_stage_rejects_lesson_and_learning_state_source_drift(self) -> None:
        other_source = add_snippet(
            self.vault,
            title="Other evidence",
            text="Unrelated evidence.",
        )["source"]
        state_path = self.vault / ".study-compiler/learning-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["lessons"]["attention"]["source_ids"] = [other_source["id"]]
        state_path.write_text(json.dumps(state), encoding="utf-8")

        with self.assertRaisesRegex(StateError, "sources do not match"):
            stage_link_patch(
                self.vault,
                "attention",
                relations=[relation("token-representations")],
            )

    def test_apply_requires_exact_approval_and_changes_only_navigation_metadata(
        self,
    ) -> None:
        attention = self.vault / "lessons/attention.md"
        target = self.vault / "lessons/token-representations.md"
        target_before = target.read_bytes()
        state_path = self.vault / ".study-compiler/learning-state.json"
        state_before = json.loads(state_path.read_text(encoding="utf-8"))
        before_metadata, before_body = parse_lesson(
            attention.read_text(encoding="utf-8")
        )
        staged = stage_link_patch(
            self.vault,
            "attention",
            relations=[relation("token-representations")],
            now=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
        )

        with self.assertRaisesRegex(StateError, "--approve"):
            apply_link_patch(self.vault, staged["patch_id"], approve=False)

        result = apply_link_patch(
            self.vault,
            staged["patch_id"],
            approve=True,
            now=datetime(2026, 7, 14, 10, 0, tzinfo=UTC),
        )

        after_metadata, after_body = parse_lesson(attention.read_text(encoding="utf-8"))
        self.assertEqual(
            after_metadata["relationships"],
            [{"target": "[[lessons/token-representations]]", "type": "uses"}],
        )
        for field in ("title", "slug", "source_ids", "created", "status"):
            self.assertEqual(after_metadata[field], before_metadata[field])
        for heading in (
            "Learning Objectives",
            "Intuition",
            "Example",
            "Explain It Back",
            "Sources",
        ):
            before_section = before_body.split(f"## {heading}", 1)[1].split("\n## ", 1)[
                0
            ]
            after_section = after_body.split(f"## {heading}", 1)[1].split("\n## ", 1)[0]
            self.assertEqual(after_section.strip(), before_section.strip())
        self.assertIn("<!-- study-compiler:related:start -->", after_body)
        self.assertIn("[[lessons/token-representations]]", after_body)
        self.assertEqual(target.read_bytes(), target_before)
        self.assertEqual(
            json.loads(state_path.read_text(encoding="utf-8")), state_before
        )
        self.assertFalse((self.vault / staged["path"]).exists())
        audit = (
            self.vault / ".study-compiler/link-patches" / f"{staged['patch_id']}.json"
        )
        self.assertTrue(audit.is_file())
        self.assertEqual(json.loads(audit.read_text())["status"], "applied")
        self.assertEqual(result["status"], "applied")

    def test_apply_fails_closed_when_the_source_lesson_changed(self) -> None:
        staged = stage_link_patch(
            self.vault,
            "attention",
            relations=[relation("token-representations")],
        )
        attention = self.vault / "lessons/attention.md"
        attention.write_text(
            attention.read_text(encoding="utf-8").replace(
                "Attention is like asking", "Attention can be understood as asking"
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(StateError, "stale"):
            apply_link_patch(self.vault, staged["patch_id"], approve=True)

        self.assertTrue((self.vault / staged["path"]).is_file())
        metadata, _ = parse_lesson(attention.read_text(encoding="utf-8"))
        self.assertEqual(metadata["relationships"], [])

    def test_apply_timestamp_cannot_precede_patch_creation(self) -> None:
        staged = stage_link_patch(
            self.vault,
            "attention",
            relations=[relation("token-representations")],
            now=datetime(2026, 7, 14, 10, 0, tzinfo=UTC),
        )

        with self.assertRaisesRegex(ValidationError, "precede"):
            apply_link_patch(
                self.vault,
                staged["patch_id"],
                approve=True,
                now=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
            )

    def test_link_patch_rejects_malformed_owned_markers(self) -> None:
        attention = self.vault / "lessons/attention.md"
        attention.write_text(
            attention.read_text(encoding="utf-8")
            + "\n<!-- study-compiler:related:start -->\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(StateError, "Related Lessons block"):
            stage_link_patch(
                self.vault,
                "attention",
                relations=[relation("token-representations")],
            )

    def test_apply_recovers_after_the_lesson_write_but_before_audit_finalize(
        self,
    ) -> None:
        staged = stage_link_patch(
            self.vault,
            "attention",
            relations=[relation("token-representations")],
        )
        from study_compiler import links as links_module

        real_write_json = links_module.write_json

        def fail_final_audit(path: Path, data: dict[str, object]) -> None:
            if data.get("status") == "applied":
                raise OSError("simulated crash after lesson write")
            real_write_json(path, data)

        with patch("study_compiler.links.write_json", side_effect=fail_final_audit):
            with self.assertRaisesRegex(OSError, "simulated crash"):
                apply_link_patch(self.vault, staged["patch_id"], approve=True)

        recovered = apply_link_patch(self.vault, staged["patch_id"], approve=True)

        self.assertEqual(recovered["status"], "applied")
        metadata, _ = parse_lesson(
            (self.vault / "lessons/attention.md").read_text(encoding="utf-8")
        )
        self.assertEqual(len(metadata["relationships"]), 1)

    def test_apply_enriches_an_existing_inline_link_without_duplicating_it(
        self,
    ) -> None:
        attention = self.vault / "lessons/attention.md"
        attention.write_text(
            attention.read_text(encoding="utf-8").replace(
                "Attention is like asking every word which other words are useful right now.",
                "Attention uses [[lessons/token-representations]] as its inputs.",
            ),
            encoding="utf-8",
        )
        staged = stage_link_patch(
            self.vault,
            "attention",
            relations=[relation("token-representations")],
        )

        apply_link_patch(self.vault, staged["patch_id"], approve=True)

        content = attention.read_text(encoding="utf-8")
        self.assertEqual(content.count("[[lessons/token-representations]]"), 2)
        self.assertNotIn("<!-- study-compiler:related:start -->", content)

    def test_apply_preserves_custom_obsidian_frontmatter(self) -> None:
        attention = self.vault / "lessons/attention.md"
        attention.write_text(
            attention.read_text(encoding="utf-8").replace(
                "relationships: []",
                'aliases: ["Attention Mechanism"]\ntags: ["ai"]\nrelationships: []',
            ),
            encoding="utf-8",
        )
        staged = stage_link_patch(
            self.vault,
            "attention",
            relations=[relation("token-representations")],
        )

        apply_link_patch(self.vault, staged["patch_id"], approve=True)

        metadata, _ = parse_lesson(attention.read_text(encoding="utf-8"))
        self.assertEqual(metadata["aliases"], ["Attention Mechanism"])
        self.assertEqual(metadata["tags"], ["ai"])

    def test_doctor_detects_an_applied_relationship_removed_from_the_lesson(
        self,
    ) -> None:
        staged = stage_link_patch(
            self.vault,
            "attention",
            relations=[relation("token-representations")],
        )
        apply_link_patch(self.vault, staged["patch_id"], approve=True)
        attention = self.vault / "lessons/attention.md"
        content = attention.read_text(encoding="utf-8").replace(
            'relationships: [{"target": "[[lessons/token-representations]]", "type": "uses"}]',
            "relationships: []",
        )
        block_start = content.index("## Related Lessons")
        attention.write_text(content[:block_start].rstrip() + "\n", encoding="utf-8")

        report = inspect_vault(self.vault)

        self.assertFalse(report["ok"])
        self.assertTrue(
            any(
                "applied link patch relation is missing" in item
                for item in report["errors"]
            ),
            report["errors"],
        )

    def test_doctor_detects_and_apply_recovers_applied_patch_cleanup(self) -> None:
        staged = stage_link_patch(
            self.vault,
            "attention",
            relations=[relation("token-representations")],
        )
        staged_path = self.vault / staged["path"]
        staged_bytes = staged_path.read_bytes()
        apply_link_patch(self.vault, staged["patch_id"], approve=True)
        staged_path.write_bytes(staged_bytes)

        incomplete = inspect_vault(self.vault)

        self.assertFalse(incomplete["ok"])
        self.assertTrue(
            any("cleanup is incomplete" in item for item in incomplete["errors"]),
            incomplete["errors"],
        )
        apply_link_patch(self.vault, staged["patch_id"], approve=True)
        self.assertFalse(staged_path.exists())
        self.assertTrue(inspect_vault(self.vault)["ok"])

    def test_applied_history_never_reports_success_after_relation_rollback(
        self,
    ) -> None:
        attention = self.vault / "lessons/attention.md"
        base_bytes = attention.read_bytes()
        staged = stage_link_patch(
            self.vault,
            "attention",
            relations=[relation("token-representations")],
        )
        apply_link_patch(self.vault, staged["patch_id"], approve=True)
        attention.write_bytes(base_bytes)

        with self.assertRaisesRegex(
            StateError, "applied link patch relation is missing"
        ):
            stage_link_patch(
                self.vault,
                "attention",
                relations=[relation("token-representations")],
            )
        with self.assertRaisesRegex(
            StateError, "applied link patch relation is missing"
        ):
            apply_link_patch(self.vault, staged["patch_id"], approve=True)

    def test_doctor_rejects_typed_relationship_without_applied_patch_audit(
        self,
    ) -> None:
        staged = stage_link_patch(
            self.vault,
            "attention",
            relations=[relation("token-representations")],
        )
        apply_link_patch(self.vault, staged["patch_id"], approve=True)
        audit = (
            self.vault / ".study-compiler/link-patches" / f"{staged['patch_id']}.json"
        )
        audit.unlink()

        report = inspect_vault(self.vault)

        self.assertFalse(report["ok"])
        self.assertTrue(
            any(
                "approved relationship has no applied link patch" in item
                for item in report["errors"]
            ),
            report["errors"],
        )

    def test_link_audit_uses_the_active_legacy_state_directory(self) -> None:
        current_state = self.vault / ".study-compiler"
        legacy_state = self.vault / ".learning-wiki"
        current_state.rename(legacy_state)
        staged = stage_link_patch(
            self.vault,
            "attention",
            relations=[relation("token-representations")],
        )

        apply_link_patch(self.vault, staged["patch_id"], approve=True)

        self.assertFalse(current_state.exists())
        self.assertTrue(
            (legacy_state / "link-patches" / f"{staged['patch_id']}.json").is_file()
        )
        self.assertTrue(inspect_vault(self.vault)["ok"])

    def test_doctor_rejects_a_symlinked_legacy_link_audit_directory(self) -> None:
        current_state = self.vault / ".study-compiler"
        legacy_state = self.vault / ".learning-wiki"
        current_state.rename(legacy_state)
        link_patches = legacy_state / "link-patches"
        link_patches.rmdir()
        outside = Path(self.temp_dir.name) / "outside-link-patches"
        outside.mkdir()
        link_patches.symlink_to(outside, target_is_directory=True)

        report = inspect_vault(self.vault)

        self.assertFalse(report["ok"])
        self.assertTrue(
            any(
                "link-patches" in item and "symlink" in item
                for item in report["errors"]
            ),
            report["errors"],
        )

    def test_doctor_rejects_a_file_as_legacy_link_audit_directory(self) -> None:
        current_state = self.vault / ".study-compiler"
        legacy_state = self.vault / ".learning-wiki"
        current_state.rename(legacy_state)
        link_patches = legacy_state / "link-patches"
        link_patches.rmdir()
        link_patches.write_text("not a directory", encoding="utf-8")

        report = inspect_vault(self.vault)

        self.assertFalse(report["ok"])
        self.assertTrue(
            any(
                "link-patches" in item and "directory" in item
                for item in report["errors"]
            ),
            report["errors"],
        )

    def test_doctor_rejects_noninteger_or_pathological_patch_schema(self) -> None:
        staged = stage_link_patch(
            self.vault,
            "attention",
            relations=[relation("token-representations")],
        )
        path = self.vault / staged["path"]
        original = json.loads(path.read_text(encoding="utf-8"))
        for schema_version in (True, 1.0):
            with self.subTest(schema_version=schema_version):
                corrupted = dict(original)
                corrupted["schema_version"] = schema_version
                path.write_text(json.dumps(corrupted), encoding="utf-8")
                report = inspect_vault(self.vault)
                self.assertFalse(report["ok"])
                self.assertTrue(
                    any("unsupported schema" in item for item in report["errors"]),
                    report["errors"],
                )
        path.write_text(
            '{"schema_version": ' + ("9" * 10_000) + "}",
            encoding="utf-8",
        )
        report = inspect_vault(self.vault)
        self.assertFalse(report["ok"])
        self.assertTrue(any("invalid" in item for item in report["errors"]))


class GraphHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp_dir.name) / "Learn"
        initialize_vault(self.vault)
        source = add_snippet(
            self.vault, title="Graph source", text="Alpha builds on beta."
        )["source"]
        for slug in ("alpha", "beta"):
            create_draft(
                self.vault,
                slug=slug,
                title=slug.title(),
                source_ids=[source["id"]],
                body=valid_body(source["id"]),
            )
            promote_draft(self.vault, slug, approve=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _connect(self) -> None:
        patch = stage_link_patch(
            self.vault,
            "alpha",
            relations=[relation("beta", relation_type="extends")],
        )
        apply_link_patch(self.vault, patch["patch_id"], approve=True)

    def test_graph_preserves_direction_and_distinguishes_health_signals(self) -> None:
        self._connect()

        report = graph_report(self.vault)

        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["unreferenced"], ["alpha"])
        self.assertEqual(report["dead_ends"], ["beta"])
        self.assertEqual(report["isolated"], [])
        self.assertEqual(report["counts"]["typed_edges"], 1)

    def test_graph_ignores_wikilink_examples_inside_code_fences(self) -> None:
        alpha = self.vault / "lessons/alpha.md"
        alpha.write_text(
            alpha.read_text(encoding="utf-8")
            + "\n```markdown\n[[lessons/does-not-exist]]\n```\n",
            encoding="utf-8",
        )

        report = graph_report(self.vault)

        self.assertEqual(report["broken_links"], [])
        self.assertEqual(report["isolated"], ["alpha", "beta"])

    def test_graph_ignores_multibacktick_and_unclosed_fenced_code(self) -> None:
        alpha = self.vault / "lessons/alpha.md"
        alpha.write_text(
            alpha.read_text(encoding="utf-8")
            + "\n``[[lessons/inline-example]]``\n"
            + "~~~markdown\n[[lessons/fenced-example]]\n",
            encoding="utf-8",
        )

        report = graph_report(self.vault)

        self.assertEqual(report["broken_links"], [])
        self.assertEqual(report["isolated"], ["alpha", "beta"])

    def test_graph_counts_obsidian_heading_links_as_lesson_edges(self) -> None:
        alpha = self.vault / "lessons/alpha.md"
        alpha.write_text(
            alpha.read_text(encoding="utf-8")
            + "\nSee [[lessons/beta#Worked Example|the worked example]].\n",
            encoding="utf-8",
        )

        report = graph_report(self.vault)

        self.assertEqual(report["broken_links"], [])
        self.assertEqual(report["isolated"], [])
        self.assertEqual(report["counts"]["untyped_edges"], 1)

    def test_graph_ignores_indented_commented_and_escaped_link_examples(self) -> None:
        alpha = self.vault / "lessons/alpha.md"
        alpha.write_text(
            alpha.read_text(encoding="utf-8")
            + "\n    [[lessons/indented-example]]\n"
            + "<!-- [[lessons/comment-example]] -->\n"
            + "\\[[lessons/escaped-example]]\n"
            + "`multiline code\n[[lessons/multiline-code-example]]\nend`\n",
            encoding="utf-8",
        )

        report = graph_report(self.vault)

        self.assertEqual(report["broken_links"], [])
        self.assertEqual(report["isolated"], ["alpha", "beta"])

    def test_graph_keeps_nested_list_and_even_backslash_lesson_links(self) -> None:
        alpha = self.vault / "lessons/alpha.md"
        alpha.write_text(
            alpha.read_text(encoding="utf-8")
            + "\n- Related concepts:\n"
            + "    - [[lessons/beta]]\n"
            + "\\\\[[lessons/beta]]\n",
            encoding="utf-8",
        )

        report = graph_report(self.vault)

        self.assertEqual(report["broken_links"], [])
        self.assertEqual(report["isolated"], [])
        self.assertEqual(report["counts"]["untyped_edges"], 1)

    def test_graph_distinguishes_links_from_code_inside_blockquotes(self) -> None:
        alpha = self.vault / "lessons/alpha.md"
        alpha.write_text(
            alpha.read_text(encoding="utf-8")
            + "\n> See [[lessons/beta]].\n"
            + "> ~~~markdown\n"
            + "> [[lessons/blockquote-fenced-example]]\n"
            + "> ~~~\n"
            + ">     [[lessons/blockquote-indented-example]]\n",
            encoding="utf-8",
        )

        report = graph_report(self.vault)

        self.assertEqual(report["broken_links"], [])
        self.assertEqual(report["isolated"], [])
        self.assertEqual(report["counts"]["untyped_edges"], 1)

    def test_graph_escapes_control_characters_in_invalid_link_errors(self) -> None:
        alpha = self.vault / "lessons/alpha.md"
        alpha.write_text(
            alpha.read_text(encoding="utf-8") + "\n[[lessons/\x1b[2Jspoof]]\n",
            encoding="utf-8",
        )

        report = graph_report(self.vault)
        rendered = "\n".join(report["errors"])

        self.assertNotIn("\x1b", rendered)
        self.assertIn("\\u001b", rendered)

    def test_graph_export_is_stable_directed_and_typed(self) -> None:
        self._connect()

        first = export_graph(self.vault)
        second = export_graph(self.vault)

        self.assertEqual(first, second)
        self.assertTrue(first["directed"])
        self.assertEqual([node["id"] for node in first["nodes"]], ["alpha", "beta"])
        self.assertEqual(
            first["edges"],
            [
                {
                    "source": "alpha",
                    "target": "beta",
                    "type": "extends",
                    "typed": True,
                }
            ],
        )

    def test_graph_reports_missing_typed_target_and_doctor_reuses_the_check(
        self,
    ) -> None:
        alpha = self.vault / "lessons/alpha.md"
        content = alpha.read_text(encoding="utf-8")
        content = content.replace(
            "relationships: []",
            'relationships: [{"target": "[[lessons/missing]]", "type": "uses"}]',
        )
        alpha.write_text(content, encoding="utf-8")

        report = graph_report(self.vault)
        doctor = inspect_vault(self.vault)

        self.assertFalse(report["ok"])
        self.assertTrue(any("missing target" in item for item in report["errors"]))
        self.assertFalse(doctor["ok"])
        self.assertTrue(any("missing target" in item for item in doctor["errors"]))

    def test_strict_graph_check_fails_on_isolated_lessons_only(self) -> None:
        normal = graph_report(self.vault)
        strict = graph_report(self.vault, strict=True)

        self.assertTrue(normal["ok"])
        self.assertFalse(strict["ok"])
        self.assertEqual(strict["isolated"], ["alpha", "beta"])


if __name__ == "__main__":
    unittest.main()
