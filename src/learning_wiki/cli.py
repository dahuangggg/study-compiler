from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .errors import LearningWikiError
from .storage import initialize_vault


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="learning-wiki",
        description="Compile sources into reviewed, human-readable learning notes.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    init = subcommands.add_parser("init", help="initialize a learning vault")
    init.add_argument("vault", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = initialize_vault(args.vault)
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

