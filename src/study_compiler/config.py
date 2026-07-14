from __future__ import annotations

from pathlib import Path

from .errors import StateError
from .storage import read_json, require_vault, secure_directory, write_json


CONFIG_DIRECTORY = ".study-compiler"
CONFIG_FILENAME = "config.json"


def _config_path(home: Path | None) -> Path:
    resolved_home = (home if home is not None else Path.home()).expanduser().resolve()
    return resolved_home / CONFIG_DIRECTORY / CONFIG_FILENAME


def write_config(workspace: Path, *, home: Path | None = None) -> dict[str, str]:
    """Persist the default Study Compiler workspace for Agent-facing commands."""
    root = require_vault(workspace)
    path = _config_path(home)
    secure_directory(path.parent)
    write_json(path, {"workspace": str(root)})
    return {"path": str(path), "workspace": str(root)}


def resolve_workspace(
    explicit: Path | None = None, *, home: Path | None = None
) -> Path:
    """Resolve an explicit workspace first, then the per-user saved workspace."""
    if explicit is not None:
        return require_vault(explicit)

    path = _config_path(home)
    try:
        payload = read_json(path)
    except StateError as error:
        raise StateError(
            "no Study Compiler workspace configured; pass --workspace or run setup"
        ) from error
    configured = payload.get("workspace")
    if not isinstance(configured, str) or not configured.strip():
        raise StateError(f"invalid workspace config: {path}")
    return require_vault(Path(configured))
