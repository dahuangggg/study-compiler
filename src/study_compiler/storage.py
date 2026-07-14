from __future__ import annotations

import json
import math
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import StateError, ValidationError

SCHEMA_VERSION = 1
STATE_DIRECTORY = ".study-compiler"
LEGACY_STATE_DIRECTORY = ".learning-wiki"
DIRECTORIES = (
    STATE_DIRECTORY,
    f"{STATE_DIRECTORY}/runs",
    f"{STATE_DIRECTORY}/link-patches",
    "sources",
    "_staging/lessons",
    "_staging/digests",
    "_staging/link-patches",
    "exports",
    "lessons",
    "reviews",
)
CONTROL_FILES = ("index.md", "log.md")
CORE_STATE_FILES = (
    "manifest.json",
    "learning-state.json",
    "source-watch.json",
    "workspace.lock",
)
UTC_TIMESTAMP_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc_timestamp(value: Any) -> datetime:
    """Parse the one canonical timestamp form used in persisted state."""
    if not isinstance(value, str) or not UTC_TIMESTAMP_PATTERN.fullmatch(value):
        raise ValueError("timestamp must use YYYY-MM-DDTHH:MM:SSZ")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def safe_child(root: Path, *parts: str) -> Path:
    resolved_root = root.expanduser().resolve()
    relative = Path(*parts)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValidationError("path must stay inside the workspace")
    try:
        logical_candidate = resolved_root / relative
        current = resolved_root
        for component in relative.parts:
            if component in {"", "."}:
                continue
            current /= component
            if current.is_symlink():
                raise StateError(f"managed path must not be a symlink: {relative}")
        candidate = logical_candidate.resolve()
    except (OSError, ValueError) as error:
        raise ValidationError(
            "path contains an invalid filesystem component"
        ) from error
    try:
        candidate.relative_to(resolved_root)
    except ValueError as error:
        raise ValidationError("path must stay inside the workspace") from error
    return candidate


def read_json(path: Path) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise StateError(f"duplicate key in state file {path.name}: {key}")
            value[key] = item
        return value

    def reject_constant(value: str) -> Any:
        raise StateError(f"non-finite number in state file {path.name}: {value}")

    def parse_finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise StateError(f"non-finite number in state file {path.name}: {value}")
        return parsed

    if path.is_symlink():
        raise StateError(f"state file must not be a symlink: {path.name}")
    try:
        data = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
            parse_float=parse_finite_float,
        )
    except FileNotFoundError as error:
        raise StateError(f"missing state file: {path.name}") from error
    except UnicodeError as error:
        raise StateError(f"invalid UTF-8 in {path.name}") from error
    except OSError as error:
        raise StateError(f"cannot read state file: {path.name}") from error
    except json.JSONDecodeError as error:
        raise StateError(f"invalid JSON in {path.name}: line {error.lineno}") from error
    except (ValueError, RecursionError) as error:
        raise StateError(f"invalid JSON in {path.name}") from error
    if not isinstance(data, dict):
        raise StateError(f"invalid state object in {path.name}")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    if path.is_symlink():
        raise StateError(f"state file must not be a symlink: {path.name}")
    secure_directory(path.parent)
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def secure_directory(path: Path) -> Path:
    """Create a private directory and reject symlink redirection."""
    if path.is_symlink():
        raise StateError(f"managed directory must not be a symlink: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise StateError(f"managed path is not a directory: {path}")
    try:
        path.chmod(0o700)
    except OSError:
        if os.name != "nt":
            raise
    return path


def write_text(
    path: Path,
    content: str,
    *,
    mode: int = 0o600,
    exclusive: bool = False,
) -> None:
    """Atomically write private UTF-8 text without following a leaf symlink."""
    if path.is_symlink():
        raise StateError(f"managed file must not be a symlink: {path}")
    secure_directory(path.parent)
    if exclusive and path.exists():
        raise StateError(f"managed file already exists: {path.name}")
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        if exclusive and path.exists():
            raise StateError(f"managed file already exists: {path.name}")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def append_text(path: Path, content: str, *, mode: int = 0o600) -> None:
    """Append private UTF-8 text while refusing a symlink leaf."""
    if path.is_symlink():
        raise StateError(f"managed file must not be a symlink: {path}")
    secure_directory(path.parent)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, mode)
    except OSError as error:
        raise StateError(f"cannot append managed file: {path.name}") from error
    try:
        try:
            os.fchmod(descriptor, mode)
        except OSError:
            if os.name != "nt":
                raise
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def state_directory(root: Path) -> Path:
    current = root / STATE_DIRECTORY
    if current.is_symlink():
        raise StateError(f"{STATE_DIRECTORY} must not be a symlink")
    if current.is_dir():
        return current
    legacy = root / LEGACY_STATE_DIRECTORY
    if legacy.is_symlink():
        raise StateError(f"{LEGACY_STATE_DIRECTORY} must not be a symlink")
    if legacy.is_dir():
        return legacy
    return current


def state_file(root: Path, name: str) -> Path:
    return state_directory(root) / name


def require_vault(vault: Path) -> Path:
    root = vault.expanduser().resolve()
    if not root.is_dir() or not state_directory(root).is_dir():
        raise StateError(f"not a Study Compiler workspace: {root}")
    for relative in DIRECTORIES:
        path = root / relative
        if path.is_symlink():
            raise StateError(f"managed directory must not be a symlink: {relative}")
        if path.exists() and not path.is_dir():
            raise StateError(f"managed path is not a directory: {relative}")
        if path.exists():
            try:
                path.resolve().relative_to(root)
            except ValueError as error:
                raise StateError(
                    f"managed directory escapes the workspace: {relative}"
                ) from error
    for relative in CONTROL_FILES:
        path = root / relative
        if path.is_symlink():
            raise StateError(f"managed file must not be a symlink: {relative}")
        if path.exists() and not path.is_file():
            raise StateError(f"managed path is not a file: {relative}")
    state_root = state_directory(root)
    for name in CORE_STATE_FILES:
        path = state_root / name
        if path.is_symlink():
            raise StateError(f"state file must not be a symlink: {name}")
        if path.exists() and not path.is_file():
            raise StateError(f"state path is not a file: {name}")
    return root


def initialize_vault(vault: Path) -> dict[str, Any]:
    root = vault.expanduser().resolve()
    if root.exists() and not root.is_dir():
        raise ValidationError(f"workspace path is not a directory: {root}")

    secure_directory(root)
    current_state = root / STATE_DIRECTORY
    legacy_state = root / LEGACY_STATE_DIRECTORY
    if current_state.is_symlink():
        raise StateError(f"{STATE_DIRECTORY} must not be a symlink")
    if legacy_state.is_symlink():
        raise StateError(f"{LEGACY_STATE_DIRECTORY} must not be a symlink")
    if current_state.exists() and legacy_state.exists():
        raise StateError(
            f"workspace contains both {STATE_DIRECTORY} and {LEGACY_STATE_DIRECTORY}"
        )
    migrated = legacy_state.is_dir() and not current_state.exists()
    if migrated:
        legacy_state.rename(current_state)
    created = not current_state.exists()
    for relative in DIRECTORIES:
        candidate = root / relative
        if candidate.is_symlink():
            raise StateError(f"managed directory must not be a symlink: {relative}")
        secure_directory(safe_child(root, relative))

    manifest = state_file(root, "manifest.json")
    if manifest.is_symlink():
        raise StateError("state file must not be a symlink: manifest.json")
    if not manifest.exists():
        write_json(manifest, {"schema_version": SCHEMA_VERSION, "sources": {}})

    state = state_file(root, "learning-state.json")
    if state.is_symlink():
        raise StateError("state file must not be a symlink: learning-state.json")
    if not state.exists():
        write_json(state, {"schema_version": SCHEMA_VERSION, "lessons": {}})

    source_watch = state_file(root, "source-watch.json")
    if source_watch.is_symlink():
        raise StateError("state file must not be a symlink: source-watch.json")
    if not source_watch.exists():
        write_json(
            source_watch,
            {"schema_version": SCHEMA_VERSION, "roots": [], "files": {}},
        )

    index = root / "index.md"
    if index.is_symlink():
        raise StateError("managed file must not be a symlink: index.md")
    if index.exists() and not index.is_file():
        raise StateError("managed path is not a file: index.md")
    if not index.exists():
        write_text(index, "# Learning Notes\n\nNo approved lessons yet.\n")

    log = root / "log.md"
    if log.is_symlink():
        raise StateError("managed file must not be a symlink: log.md")
    if log.exists() and not log.is_file():
        raise StateError("managed path is not a file: log.md")
    if not log.exists():
        write_text(log, f"# Study Compiler Log\n\n- {utc_now()} INIT\n")

    return {"created": created, "migrated": migrated, "workspace": str(root)}
