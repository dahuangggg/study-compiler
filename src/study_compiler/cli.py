from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .config import resolve_workspace, write_config
from .digest import build_digest
from .doctor import inspect_vault
from .errors import StudyCompilerError, ValidationError
from .installer import (
    choose_agent_names,
    detect_agent_names,
    format_setup_summary,
    install_skills,
    locate_bundled_skills,
    resolve_skill_targets,
)
from .lessons import (
    MAX_LESSON_BODY_BYTES,
    MAX_RETELLING_CHARS,
    create_draft,
    list_drafts,
    promote_draft,
    record_recall,
    revise_draft,
)
from .links import (
    MAX_PATCH_INPUT_BYTES,
    apply_link_patch,
    export_graph,
    export_graph_file,
    graph_report,
    list_link_patches,
    show_link_patch,
    stage_link_patch,
)
from .runs import fail_run, finish_run, resume_run, start_run, workspace_status
from .sources import (
    DEFAULT_MAX_BYTES,
    add_local_source,
    add_snippet,
    add_url_source,
    attach_web_snapshot,
    list_sources,
    set_source_coverage,
)
from .source_watch import configure_source_roots
from .storage import initialize_vault, require_vault, safe_child


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="study-compiler",
        description="Compile sources into reviewed, human-readable learning notes.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    setup = subcommands.add_parser(
        "setup", help="install Agent Skills and optionally initialize a workspace"
    )
    setup.add_argument("--workspace", "--vault", dest="workspace", type=Path)
    setup.add_argument(
        "--source-root",
        action="append",
        type=Path,
        default=[],
        help="external read-only learning source directory; repeatable",
    )
    setup.add_argument(
        "--agent",
        action="append",
        choices=("auto", "codex", "claude", "agents", "all"),
        default=[],
        help="Agent target to install for; repeatable (default: auto)",
    )
    setup.add_argument(
        "--target",
        action="append",
        type=Path,
        default=[],
        help="custom Agent skill directory; repeatable",
    )
    setup.add_argument("--copy", action="store_true", help="copy instead of symlink")
    setup.add_argument(
        "--force", action="store_true", help="replace conflicting bundled Skills"
    )
    setup.add_argument(
        "--json", action="store_true", help="print machine-readable JSON"
    )
    init = subcommands.add_parser("init", help="initialize a study workspace")
    init.add_argument("workspace", type=Path)
    init.add_argument(
        "--source-root",
        action="append",
        type=Path,
        default=[],
        help="external read-only learning source directory; repeatable",
    )

    source = subcommands.add_parser(
        "source", help="register immutable learning sources"
    )
    source_commands = source.add_subparsers(dest="source_command", required=True)

    source_add = source_commands.add_parser("add", help="snapshot a local source file")
    source_add.add_argument("workspace", type=Path)
    source_add.add_argument("path", type=Path)
    source_add.add_argument(
        "--kind", choices=("paper", "document", "chat"), required=True
    )
    source_add.add_argument("--title")

    source_url = source_commands.add_parser(
        "add-url", help="register an HTTPS web reference"
    )
    source_url.add_argument("workspace", type=Path)
    source_url.add_argument("url")
    source_url.add_argument("--title")

    source_snippet = source_commands.add_parser(
        "add-snippet", help="snapshot a chat excerpt"
    )
    source_snippet.add_argument("workspace", type=Path)
    source_snippet.add_argument("--kind", choices=("chat",), default="chat")
    source_snippet.add_argument("--title", required=True)
    snippet_input = source_snippet.add_mutually_exclusive_group(required=True)
    snippet_input.add_argument("--text")
    snippet_input.add_argument("--from-file", type=Path)

    source_web_snapshot = source_commands.add_parser(
        "snapshot-web", help="attach an Agent-fetched snapshot to a web source"
    )
    source_web_snapshot.add_argument("workspace", type=Path)
    source_web_snapshot.add_argument("source_id")
    source_web_snapshot.add_argument("path", type=Path)

    source_list = source_commands.add_parser("list", help="list registered sources")
    source_list.add_argument("workspace", type=Path)
    source_list.add_argument("--json", action="store_true")

    source_coverage = source_commands.add_parser(
        "coverage", help="set deterministic source compilation coverage"
    )
    source_coverage.add_argument("workspace", type=Path)
    source_coverage.add_argument("source_id")
    source_coverage.add_argument(
        "--status", choices=("pending", "partial", "complete"), required=True
    )

    draft = subcommands.add_parser("draft", help="manage staged learning lessons")
    draft_commands = draft.add_subparsers(dest="draft_command", required=True)
    draft_create = draft_commands.add_parser(
        "create", help="validate and stage a lesson"
    )
    draft_create.add_argument("workspace", type=Path)
    draft_create.add_argument("--slug", required=True)
    draft_create.add_argument("--title", required=True)
    draft_create.add_argument("--source", dest="source_ids", nargs="+", required=True)
    draft_create.add_argument("--content-file", type=Path, required=True)
    draft_list = draft_commands.add_parser("list", help="list staged lessons")
    draft_list.add_argument("workspace", type=Path)
    draft_list.add_argument("--json", action="store_true")
    draft_revise = draft_commands.add_parser(
        "revise", help="replace and revalidate a staged lesson body"
    )
    draft_revise.add_argument("workspace", type=Path)
    draft_revise.add_argument("slug")
    draft_revise.add_argument("--content-file", type=Path, required=True)
    draft_revise.add_argument(
        "--add-source",
        dest="add_source_ids",
        nargs="+",
        help="add immutable source provenance while integrating an unreviewed draft",
    )
    draft_promote = draft_commands.add_parser(
        "promote", help="approve and promote a lesson"
    )
    draft_promote.add_argument("workspace", type=Path)
    draft_promote.add_argument("slug")
    draft_promote.add_argument("--approve", action="store_true")

    link = subcommands.add_parser(
        "link", help="stage and approve typed cross-link patches"
    )
    link_commands = link.add_subparsers(dest="link_command", required=True)
    link_stage = link_commands.add_parser(
        "stage", help="stage additive typed relationships for one approved lesson"
    )
    link_stage.add_argument("workspace", type=Path)
    link_stage.add_argument("source_slug")
    link_stage.add_argument("--proposal-file", type=Path, required=True)
    link_list = link_commands.add_parser("list", help="list staged link patches")
    link_list.add_argument("workspace", type=Path)
    link_list.add_argument("--json", action="store_true")
    link_show = link_commands.add_parser("show", help="show one link patch")
    link_show.add_argument("workspace", type=Path)
    link_show.add_argument("patch_id")
    link_show.add_argument("--json", action="store_true")
    link_apply = link_commands.add_parser(
        "apply", help="explicitly approve and apply one link patch"
    )
    link_apply.add_argument("workspace", type=Path)
    link_apply.add_argument("patch_id")
    link_apply.add_argument("--approve", action="store_true")

    graph = subcommands.add_parser("graph", help="inspect the lesson knowledge graph")
    graph_commands = graph.add_subparsers(dest="graph_command", required=True)
    graph_check = graph_commands.add_parser(
        "check", help="validate links and report graph health"
    )
    graph_check.add_argument("workspace", type=Path)
    graph_check.add_argument("--json", action="store_true")
    graph_check.add_argument("--strict", action="store_true")
    graph_export = graph_commands.add_parser(
        "export", help="export stable directed graph JSON"
    )
    graph_export.add_argument("workspace", type=Path)
    graph_export.add_argument(
        "--output",
        type=Path,
        help="JSON filename under the workspace exports directory",
    )

    recall = subcommands.add_parser("recall", help="record explain-it-back attempts")
    recall_commands = recall.add_subparsers(dest="recall_command", required=True)
    recall_record = recall_commands.add_parser(
        "record", help="record a learner retelling"
    )
    recall_record.add_argument("workspace", type=Path)
    recall_record.add_argument("slug")
    recall_record.add_argument("--score", type=int, choices=range(1, 6), required=True)
    recall_record.add_argument("--retelling-file", type=Path, required=True)

    digest = subcommands.add_parser("digest", help="build periodic learning agendas")
    digest_commands = digest.add_subparsers(dest="digest_command", required=True)
    digest_build = digest_commands.add_parser(
        "build", help="build a staged learning agenda"
    )
    digest_build.add_argument("workspace", type=Path)
    digest_build.add_argument(
        "--period", choices=("daily", "weekly", "monthly"), default="weekly"
    )

    doctor = subcommands.add_parser("doctor", help="validate all workspace invariants")
    doctor.add_argument("workspace", type=Path)
    doctor.add_argument("--json", action="store_true")

    status = subcommands.add_parser("status", help="summarize actionable learning work")
    status.add_argument("--workspace", type=Path)
    status.add_argument("--json", action="store_true")

    run = subcommands.add_parser("run", help="manage one resumable learning run")
    run_commands = run.add_subparsers(dest="run_command", required=True)
    run_start = run_commands.add_parser("start", help="start the next learning run")
    run_start.add_argument("--workspace", type=Path)
    run_start.add_argument(
        "--period", choices=("daily", "weekly", "monthly"), default="daily"
    )
    run_finish = run_commands.add_parser("finish", help="complete an active run")
    run_finish.add_argument("run_id")
    run_finish.add_argument("--artifact", type=Path, required=True)
    run_finish.add_argument("--workspace", type=Path)
    run_resume = run_commands.add_parser("resume", help="resume an active run")
    run_resume.add_argument("run_id")
    run_resume.add_argument("--workspace", type=Path)
    run_fail = run_commands.add_parser("fail", help="record an active run failure")
    run_fail.add_argument("run_id")
    run_fail.add_argument("--message", required=True)
    run_fail.add_argument("--workspace", type=Path)
    return parser


def _read_text_file(path: Path, label: str, *, max_bytes: int) -> str:
    chunks: list[bytes] = []
    size = 0
    try:
        with path.expanduser().open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise ValidationError(f"{label} exceeds {max_bytes} byte limit")
                chunks.append(chunk)
    except OSError as error:
        raise ValidationError(f"cannot read {label}: {path}") from error
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeError as error:
        raise ValidationError(f"cannot read {label} as UTF-8: {path}") from error


def _read_json_object(path: Path, label: str, *, max_bytes: int) -> dict[str, object]:
    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValidationError(f"{label} contains a duplicate key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        raise ValidationError(f"{label} contains a non-finite number: {value}")

    text = _read_text_file(path, label, max_bytes=max_bytes)
    try:
        value = json.loads(
            text,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as error:
        raise ValidationError(
            f"invalid JSON in {label}: line {error.lineno}"
        ) from error
    except (ValueError, RecursionError) as error:
        raise ValidationError(f"invalid JSON in {label}") from error
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must contain a JSON object")
    return value


def _graph_export_path(workspace: Path, requested: Path) -> Path:
    root = require_vault(workspace)
    exports_root = safe_child(root, "exports")
    candidate = requested.expanduser()
    if candidate.is_absolute():
        try:
            relative = candidate.relative_to(exports_root)
        except ValueError as error:
            raise ValidationError(
                "graph export path must stay inside the workspace exports directory"
            ) from error
    else:
        relative = candidate
        if relative.parts and relative.parts[0] == "exports":
            relative = Path(*relative.parts[1:])
    if not relative.parts or any(part.startswith(".") for part in relative.parts):
        raise ValidationError("graph export path must be a visible file under exports")
    if relative.suffix.lower() != ".json":
        raise ValidationError("graph export path must end in .json")
    destination = safe_child(exports_root, *relative.parts)
    if destination.exists() and not destination.is_file():
        raise ValidationError("graph export destination must be a file")
    return destination


def _print_workspace_status(result: dict[str, object]) -> None:
    print(f"Study Compiler status: {result['workspace']}")
    print(f"pending sources: {result['pending_sources']}")
    print(f"unsnapshotted URLs: {result['unsnapshotted_urls']}")
    print(f"staged lessons: {result['staged_lessons']}")
    print(f"staged link patches: {result.get('staged_link_patches', 0)}")
    print(f"awaiting learner: {result['awaiting_learner']}")
    print(f"due reviews: {result['due_reviews']}")
    source_changes = result.get("source_changes")
    if isinstance(source_changes, dict):
        print(
            "source changes: "
            + ", ".join(
                f"{name}={len(source_changes.get(name, []))}"
                for name in ("new", "modified", "renamed", "deleted")
            )
        )
        unavailable_roots = source_changes.get("unavailable_roots", [])
        if isinstance(unavailable_roots, list) and unavailable_roots:
            print("unavailable source roots:")
            for unavailable_root in unavailable_roots:
                print(f"- {unavailable_root}")
    active = result.get("active_run")
    if isinstance(active, dict):
        print(f"active run: {active.get('run_id')}")
    else:
        print("active run: none")
    failure = result.get("last_failure")
    if isinstance(failure, dict):
        print(f"last failure: {failure.get('run_id')} — {failure.get('error')}")
    else:
        print("last failure: none")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            agents = args.agent
            if (
                not agents
                and not args.target
                and not args.json
                and sys.stdin.isatty()
                and sys.stdout.isatty()
            ):
                agents = choose_agent_names(detect_agent_names())
            targets = resolve_skill_targets(agents, args.target)
            target_reports = install_skills(
                locate_bundled_skills(),
                targets,
                copy=args.copy,
                force=args.force,
            )
            workspace_result = None
            if args.workspace is not None:
                workspace_result = initialize_vault(args.workspace)
                if args.source_root:
                    workspace_result["source_watch"] = configure_source_roots(
                        args.workspace, args.source_root
                    )
                write_config(args.workspace)
            elif args.source_root:
                raise ValidationError("--source-root requires --workspace")
            result = {
                "targets": target_reports,
                "workspace": workspace_result,
            }
            if args.json:
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            else:
                print(format_setup_summary(result))
            return 0
        elif args.command == "init":
            result = initialize_vault(args.workspace)
            if args.source_root:
                result["source_watch"] = configure_source_roots(
                    args.workspace, args.source_root
                )
        elif args.command == "source" and args.source_command == "add":
            result = add_local_source(
                args.workspace, args.path, kind=args.kind, title=args.title
            )
        elif args.command == "source" and args.source_command == "add-url":
            result = add_url_source(args.workspace, args.url, title=args.title)
        elif args.command == "source" and args.source_command == "add-snippet":
            text = args.text
            if args.from_file is not None:
                text = _read_text_file(
                    args.from_file,
                    "snippet file",
                    max_bytes=DEFAULT_MAX_BYTES,
                )
            result = add_snippet(
                args.workspace, title=args.title, text=text, kind=args.kind
            )
        elif args.command == "source" and args.source_command == "snapshot-web":
            result = attach_web_snapshot(args.workspace, args.source_id, args.path)
        elif args.command == "source" and args.source_command == "list":
            sources = list_sources(args.workspace)
            if not args.json:
                for source in sources:
                    print(f"{source['id']}\t{source['kind']}\t{source['title']}")
                return 0
            result = {"sources": sources}
        elif args.command == "source" and args.source_command == "coverage":
            result = set_source_coverage(
                args.workspace, args.source_id, status=args.status
            )
        elif args.command == "draft" and args.draft_command == "create":
            result = create_draft(
                args.workspace,
                slug=args.slug,
                title=args.title,
                source_ids=args.source_ids,
                body=_read_text_file(
                    args.content_file,
                    "lesson content file",
                    max_bytes=MAX_LESSON_BODY_BYTES,
                ),
            )
        elif args.command == "draft" and args.draft_command == "list":
            drafts = list_drafts(args.workspace)
            if not args.json:
                for draft in drafts:
                    print(f"{draft['slug']}\t{draft['title']}")
                return 0
            result = {"drafts": drafts}
        elif args.command == "draft" and args.draft_command == "revise":
            result = revise_draft(
                args.workspace,
                args.slug,
                body=_read_text_file(
                    args.content_file,
                    "lesson content file",
                    max_bytes=MAX_LESSON_BODY_BYTES,
                ),
                add_source_ids=args.add_source_ids,
            )
        elif args.command == "draft" and args.draft_command == "promote":
            result = promote_draft(args.workspace, args.slug, approve=args.approve)
        elif args.command == "link" and args.link_command == "stage":
            proposal = _read_json_object(
                args.proposal_file,
                "link proposal file",
                max_bytes=MAX_PATCH_INPUT_BYTES,
            )
            if set(proposal) != {"relations"}:
                raise ValidationError(
                    "link proposal must contain only the relations field"
                )
            result = stage_link_patch(
                args.workspace,
                args.source_slug,
                relations=proposal["relations"],
            )
        elif args.command == "link" and args.link_command == "list":
            patches = list_link_patches(args.workspace)
            if not args.json:
                for patch in patches:
                    state = "stale" if patch["stale"] else "ready"
                    print(
                        f"{patch['patch_id']}\t{patch['source_slug']}\t"
                        f"{len(patch['relations'])}\t{state}"
                    )
                return 0
            result = {"patches": patches}
        elif args.command == "link" and args.link_command == "show":
            patch = show_link_patch(args.workspace, args.patch_id)
            if not args.json:
                print(f"patch: {patch['patch_id']}")
                print(f"source: {patch['source_slug']}")
                print(f"status: {patch['status']}")
                for relation in patch["relations"]:
                    print(
                        f"- {relation['type']} {relation['target']} "
                        f"({relation['confidence']}, score={relation['score']})"
                    )
                print("changes: relationship metadata and Related Lessons block only")
                print(
                    "preserved: sources, teaching content, approval, mastery, reviews"
                )
                return 0
            result = patch
        elif args.command == "link" and args.link_command == "apply":
            result = apply_link_patch(
                args.workspace, args.patch_id, approve=args.approve
            )
        elif args.command == "graph" and args.graph_command == "check":
            result = graph_report(args.workspace, strict=args.strict)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            else:
                status = "PASS" if result["ok"] else "FAIL"
                print(f"Study Compiler graph: {status}")
                for error in result["errors"]:
                    print(f"error: {error}")
                for name in ("unreferenced", "dead_ends", "isolated"):
                    print(f"{name}: {len(result[name])}")
            return 0 if result["ok"] else 1
        elif args.command == "graph" and args.graph_command == "export":
            if args.output is None:
                graph_data = export_graph(args.workspace)
                print(json.dumps(graph_data, ensure_ascii=False, sort_keys=True))
                return 0
            destination = _graph_export_path(args.workspace, args.output)
            export_graph_file(args.workspace, destination)
            result = {
                "path": destination.relative_to(
                    require_vault(args.workspace)
                ).as_posix()
            }
        elif args.command == "recall" and args.recall_command == "record":
            result = record_recall(
                args.workspace,
                args.slug,
                score=args.score,
                retelling=_read_text_file(
                    args.retelling_file,
                    "retelling file",
                    max_bytes=MAX_RETELLING_CHARS * 4,
                ),
            )
        elif args.command == "digest" and args.digest_command == "build":
            result = build_digest(args.workspace, period=args.period)
        elif args.command == "doctor":
            result = inspect_vault(args.workspace)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            else:
                status = "PASS" if result["ok"] else "FAIL"
                print(f"Study Compiler doctor: {status}")
                for error in result["errors"]:
                    print(f"error: {error}")
                for warning in result["warnings"]:
                    print(f"warning: {warning}")
            return 0 if result["ok"] else 1
        elif args.command == "status":
            workspace = resolve_workspace(args.workspace)
            result = workspace_status(workspace)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            else:
                _print_workspace_status(result)
            return 0
        elif args.command == "run" and args.run_command == "start":
            workspace = resolve_workspace(args.workspace)
            result = start_run(workspace, period=args.period)
        elif args.command == "run" and args.run_command == "finish":
            workspace = resolve_workspace(args.workspace)
            result = finish_run(workspace, args.run_id, args.artifact)
        elif args.command == "run" and args.run_command == "resume":
            workspace = resolve_workspace(args.workspace)
            result = resume_run(workspace, args.run_id)
        elif args.command == "run" and args.run_command == "fail":
            workspace = resolve_workspace(args.workspace)
            result = fail_run(workspace, args.run_id, args.message)
        else:  # pragma: no cover - argparse owns command validation
            parser.error("unknown command")
            return 2
    except StudyCompilerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def entrypoint() -> None:
    raise SystemExit(main())
