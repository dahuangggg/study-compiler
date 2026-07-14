from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import StateError, ValidationError

SCHEMA_VERSION = 1
DIRECTORIES = (
    ".learning-wiki",
    "sources",
    "_staging/lessons",
    "_staging/digests",
    "lessons",
    "reviews",
)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_child(root: Path, *parts: str) -> Path:
    resolved_root = root.expanduser().resolve()
    candidate = resolved_root.joinpath(*parts).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as error:
        raise ValidationError("path must stay inside the vault") from error
    return candidate


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise StateError(f"missing state file: {path.name}") from error
    except json.JSONDecodeError as error:
        raise StateError(f"invalid JSON in {path.name}: line {error.lineno}") from error
    if not isinstance(data, dict):
        raise StateError(f"invalid state object in {path.name}")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def require_vault(vault: Path) -> Path:
    root = vault.expanduser().resolve()
    if not root.is_dir() or not (root / ".learning-wiki").is_dir():
        raise StateError(f"not a learning-wiki vault: {root}")
    return root


def initialize_vault(vault: Path) -> dict[str, Any]:
    root = vault.expanduser().resolve()
    if root.exists() and not root.is_dir():
        raise ValidationError(f"vault path is not a directory: {root}")

    created = not (root / ".learning-wiki").exists()
    root.mkdir(parents=True, exist_ok=True)
    for relative in DIRECTORIES:
        safe_child(root, relative).mkdir(parents=True, exist_ok=True)

    manifest = root / ".learning-wiki/manifest.json"
    if not manifest.exists():
        write_json(manifest, {"schema_version": SCHEMA_VERSION, "sources": {}})

    state = root / ".learning-wiki/learning-state.json"
    if not state.exists():
        write_json(state, {"schema_version": SCHEMA_VERSION, "lessons": {}})

    index = root / "index.md"
    if not index.exists():
        index.write_text("# Learning Notes\n\nNo approved lessons yet.\n", encoding="utf-8")

    log = root / "log.md"
    if not log.exists():
        log.write_text(f"# Learning Wiki Log\n\n- {utc_now()} INIT\n", encoding="utf-8")

    return {"created": created, "vault": str(root)}

