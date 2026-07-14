from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .errors import LearningWikiError, ValidationError
from .digest import build_digest
from .doctor import inspect_vault
from .lessons import create_draft, list_drafts, promote_draft, record_recall
from .sources import add_local_source, add_snippet, add_url_source, attach_web_snapshot, list_sources
from .storage import initialize_vault


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="learning-wiki",
        description="Compile sources into reviewed, human-readable learning notes.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    init = subcommands.add_parser("init", help="initialize a learning vault")
    init.add_argument("vault", type=Path)

    source = subcommands.add_parser("source", help="register immutable learning sources")
    source_commands = source.add_subparsers(dest="source_command", required=True)

    source_add = source_commands.add_parser("add", help="snapshot a local source file")
    source_add.add_argument("vault", type=Path)
    source_add.add_argument("path", type=Path)
    source_add.add_argument("--kind", choices=("paper", "document", "chat"), required=True)
    source_add.add_argument("--title")

    source_url = source_commands.add_parser("add-url", help="register an HTTPS web reference")
    source_url.add_argument("vault", type=Path)
    source_url.add_argument("url")
    source_url.add_argument("--title")

    source_snippet = source_commands.add_parser("add-snippet", help="snapshot a chat excerpt")
    source_snippet.add_argument("vault", type=Path)
    source_snippet.add_argument("--kind", choices=("chat",), default="chat")
    source_snippet.add_argument("--title", required=True)
    snippet_input = source_snippet.add_mutually_exclusive_group(required=True)
    snippet_input.add_argument("--text")
    snippet_input.add_argument("--from-file", type=Path)

    source_web_snapshot = source_commands.add_parser(
        "snapshot-web", help="attach an Agent-fetched snapshot to a web source"
    )
    source_web_snapshot.add_argument("vault", type=Path)
    source_web_snapshot.add_argument("source_id")
    source_web_snapshot.add_argument("path", type=Path)

    source_list = source_commands.add_parser("list", help="list registered sources")
    source_list.add_argument("vault", type=Path)
    source_list.add_argument("--json", action="store_true")

    draft = subcommands.add_parser("draft", help="manage staged learning lessons")
    draft_commands = draft.add_subparsers(dest="draft_command", required=True)
    draft_create = draft_commands.add_parser("create", help="validate and stage a lesson")
    draft_create.add_argument("vault", type=Path)
    draft_create.add_argument("--slug", required=True)
    draft_create.add_argument("--title", required=True)
    draft_create.add_argument("--source", dest="source_ids", nargs="+", required=True)
    draft_create.add_argument("--content-file", type=Path, required=True)
    draft_list = draft_commands.add_parser("list", help="list staged lessons")
    draft_list.add_argument("vault", type=Path)
    draft_list.add_argument("--json", action="store_true")
    draft_promote = draft_commands.add_parser("promote", help="approve and promote a lesson")
    draft_promote.add_argument("vault", type=Path)
    draft_promote.add_argument("slug")
    draft_promote.add_argument("--approve", action="store_true")

    recall = subcommands.add_parser("recall", help="record explain-it-back attempts")
    recall_commands = recall.add_subparsers(dest="recall_command", required=True)
    recall_record = recall_commands.add_parser("record", help="record a learner retelling")
    recall_record.add_argument("vault", type=Path)
    recall_record.add_argument("slug")
    recall_record.add_argument("--score", type=int, choices=range(1, 6), required=True)
    recall_record.add_argument("--retelling-file", type=Path, required=True)

    digest = subcommands.add_parser("digest", help="build periodic learning agendas")
    digest_commands = digest.add_subparsers(dest="digest_command", required=True)
    digest_build = digest_commands.add_parser("build", help="build a staged learning agenda")
    digest_build.add_argument("vault", type=Path)
    digest_build.add_argument("--period", choices=("daily", "weekly", "monthly"), default="weekly")

    doctor = subcommands.add_parser("doctor", help="validate all vault invariants")
    doctor.add_argument("vault", type=Path)
    doctor.add_argument("--json", action="store_true")
    return parser


def _read_text_file(path: Path, label: str) -> str:
    try:
        return path.expanduser().read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ValidationError(f"cannot read {label}: {path}") from error


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = initialize_vault(args.vault)
        elif args.command == "source" and args.source_command == "add":
            result = add_local_source(args.vault, args.path, kind=args.kind, title=args.title)
        elif args.command == "source" and args.source_command == "add-url":
            result = add_url_source(args.vault, args.url, title=args.title)
        elif args.command == "source" and args.source_command == "add-snippet":
            text = args.text
            if args.from_file is not None:
                text = _read_text_file(args.from_file, "snippet file")
            result = add_snippet(args.vault, title=args.title, text=text, kind=args.kind)
        elif args.command == "source" and args.source_command == "snapshot-web":
            result = attach_web_snapshot(args.vault, args.source_id, args.path)
        elif args.command == "source" and args.source_command == "list":
            sources = list_sources(args.vault)
            if not args.json:
                for source in sources:
                    print(f"{source['id']}\t{source['kind']}\t{source['title']}")
                return 0
            result = {"sources": sources}
        elif args.command == "draft" and args.draft_command == "create":
            result = create_draft(
                args.vault,
                slug=args.slug,
                title=args.title,
                source_ids=args.source_ids,
                body=_read_text_file(args.content_file, "lesson content file"),
            )
        elif args.command == "draft" and args.draft_command == "list":
            drafts = list_drafts(args.vault)
            if not args.json:
                for draft in drafts:
                    print(f"{draft['slug']}\t{draft['title']}")
                return 0
            result = {"drafts": drafts}
        elif args.command == "draft" and args.draft_command == "promote":
            result = promote_draft(args.vault, args.slug, approve=args.approve)
        elif args.command == "recall" and args.recall_command == "record":
            result = record_recall(
                args.vault,
                args.slug,
                score=args.score,
                retelling=_read_text_file(args.retelling_file, "retelling file"),
            )
        elif args.command == "digest" and args.digest_command == "build":
            result = build_digest(args.vault, period=args.period)
        elif args.command == "doctor":
            result = inspect_vault(args.vault)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            else:
                status = "PASS" if result["ok"] else "FAIL"
                print(f"Learning Wiki doctor: {status}")
                for error in result["errors"]:
                    print(f"error: {error}")
                for warning in result["warnings"]:
                    print(f"warning: {warning}")
            return 0 if result["ok"] else 1
        else:  # pragma: no cover - argparse owns command validation
            parser.error("unknown command")
            return 2
    except LearningWikiError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def entrypoint() -> None:
    raise SystemExit(main())
