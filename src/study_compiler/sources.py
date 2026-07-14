from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Iterator
from urllib.parse import urlsplit

from .errors import StateError, ValidationError
from .schemas import normalize_https_url
from .state import persist_manifest, workspace_lock
from .storage import (
    append_text,
    read_json,
    require_vault,
    safe_child,
    secure_directory,
    state_file,
    utc_now,
)

DEFAULT_MAX_BYTES = 50 * 1024 * 1024
LOCAL_KINDS = {"paper", "document", "chat"}
COVERAGE_STATUSES = {"pending", "partial", "complete"}


def _manifest_path(vault: Path) -> Path:
    return state_file(vault, "manifest.json")


def _load_manifest(vault: Path) -> dict[str, Any]:
    manifest = read_json(_manifest_path(vault))
    if manifest.get("schema_version") != 1 or not isinstance(
        manifest.get("sources"), dict
    ):
        raise StateError("manifest.json has an unsupported schema")
    return manifest


def _snapshot_suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix) else ".bin"


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _open_no_follow(path: Path, *, dir_fd: int | None = None, directory: bool) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        return os.open(path, flags, dir_fd=dir_fd)
    except OSError as error:
        raise ValidationError(f"source is not a safe regular file: {path}") from error


def _open_beneath_root(path: Path, root: Path) -> int:
    relative = path.relative_to(root)
    if not relative.parts:
        raise ValidationError(f"source is not a regular file: {path}")
    directory_fd = _open_no_follow(root, directory=True)
    try:
        for part in relative.parts[:-1]:
            next_fd = _open_no_follow(Path(part), dir_fd=directory_fd, directory=True)
            os.close(directory_fd)
            directory_fd = next_fd
        return _open_no_follow(
            Path(relative.parts[-1]), dir_fd=directory_fd, directory=False
        )
    finally:
        os.close(directory_fd)


@contextmanager
def _open_regular_source(
    source_path: Path,
    *,
    allowed_roots: list[Path] | None = None,
) -> Iterator[tuple[BinaryIO, os.stat_result]]:
    """Open one regular file without following watched-path symlinks."""
    path = _absolute_without_resolving(source_path)
    if allowed_roots is None:
        descriptor = _open_no_follow(path, directory=False)
    else:
        roots = sorted(
            (_absolute_without_resolving(root) for root in allowed_roots),
            key=lambda root: len(root.parts),
            reverse=True,
        )
        matching_root = next(
            (root for root in roots if path.is_relative_to(root)), None
        )
        if matching_root is None:
            raise ValidationError(f"source is outside configured roots: {path}")
        descriptor = _open_beneath_root(path, matching_root)
    try:
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise ValidationError(f"source is not a regular file: {path}")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = -1
            yield source, opened_stat
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _stream_snapshot_to_temp(
    source_path: Path,
    destination_directory: Path,
    *,
    max_bytes: int,
    allowed_roots: list[Path] | None = None,
    expected_sha256: str | None = None,
) -> tuple[Path, str, int]:
    """Copy and hash the exact bytes read from one descriptor with a hard limit."""
    secure_directory(destination_directory)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=".snapshot.", dir=destination_directory
    )
    temp_path = Path(temp_name)
    digest = hashlib.sha256()
    size = 0
    try:
        with (
            _open_regular_source(source_path, allowed_roots=allowed_roots) as (
                source,
                opened_stat,
            ),
            os.fdopen(descriptor, "wb") as target,
        ):
            if opened_stat.st_size > max_bytes:
                raise ValidationError(f"source exceeds {max_bytes} byte limit")
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise ValidationError(f"source exceeds {max_bytes} byte limit")
                digest.update(chunk)
                target.write(chunk)
            final_stat = os.fstat(source.fileno())
            if (
                final_stat.st_size != opened_stat.st_size
                or final_stat.st_mtime_ns != opened_stat.st_mtime_ns
            ):
                raise StateError(
                    f"source changed while being snapshotted: {source_path}"
                )
            target.flush()
            os.fsync(target.fileno())
        actual_sha256 = digest.hexdigest()
        if expected_sha256 is not None and actual_sha256 != expected_sha256:
            raise StateError(f"watched source changed after scan: {source_path}")
        temp_path.chmod(0o400)
        return temp_path, actual_sha256, size
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temp_path.unlink(missing_ok=True)
        raise


def _require_kind(kind: str) -> str:
    if kind not in LOCAL_KINDS:
        raise ValidationError(f"unsupported source kind: {kind}")
    return kind


def _require_title(title: str) -> str:
    if not isinstance(title, str):
        raise ValidationError("title must contain 1 to 300 printable characters")
    normalized = title.strip()
    if not normalized:
        raise ValidationError("title must not be empty")
    if len(normalized) > 300 or any(
        not character.isprintable() for character in normalized
    ):
        raise ValidationError("title must contain 1 to 300 printable characters")
    return normalized


def _require_coverage_status(status: str) -> str:
    if not isinstance(status, str) or status not in COVERAGE_STATUSES:
        raise ValidationError("coverage status must be pending, partial, or complete")
    return status


def _source_id(kind: str, digest: str) -> str:
    return f"{kind}-{digest[:16]}"


def _relative(path: Path, vault: Path) -> str:
    return path.relative_to(vault).as_posix()


def _record_source(
    vault: Path, manifest: dict[str, Any], record: dict[str, Any]
) -> dict[str, Any]:
    source_id = record["id"]
    source_dir = safe_child(vault, "sources", source_id)
    if source_dir.exists():
        raise StateError(f"orphan source directory already exists: {source_id}")
    secure_directory(source_dir)
    manifest["sources"][source_id] = record
    persist_manifest(vault, manifest, [source_id])
    append_text(
        safe_child(vault, "log.md"),
        f"- {record['imported_at']} SOURCE_ADD id={source_id} kind={record['kind']}\n",
    )
    return {"duplicate": False, "source": record}


def _add_local_source_unlocked(
    root: Path,
    source_path: Path,
    *,
    kind: str,
    title: str | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    source_fields: dict[str, Any] | None = None,
    allowed_roots: list[Path] | None = None,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    _require_kind(kind)
    path = _absolute_without_resolving(source_path)
    normalized_title = _require_title(title or path.stem)
    temp_snapshot, digest, size = _stream_snapshot_to_temp(
        path,
        safe_child(root, "sources"),
        max_bytes=max_bytes,
        allowed_roots=allowed_roots,
        expected_sha256=expected_sha256,
    )
    source_id = _source_id(kind, digest)
    manifest = _load_manifest(root)
    existing = manifest["sources"].get(source_id)
    if existing:
        temp_snapshot.unlink(missing_ok=True)
        if source_fields:
            watch_paths = source_fields.get("watch_paths", [])
            if watch_paths:
                existing["watch_paths"] = sorted(
                    set(existing.get("watch_paths", [])) | set(watch_paths)
                )
            persist_manifest(root, manifest, [source_id])
        return {"duplicate": True, "source": existing}

    source_dir = safe_child(root, "sources", source_id)
    if source_dir.exists():
        temp_snapshot.unlink(missing_ok=True)
        raise StateError(f"orphan source directory already exists: {source_id}")
    secure_directory(source_dir)
    snapshot = safe_child(source_dir, f"snapshot{_snapshot_suffix(path)}")
    os.replace(temp_snapshot, snapshot)
    snapshot.chmod(0o400)
    imported_at = utc_now()
    record = {
        "id": source_id,
        "kind": kind,
        "title": normalized_title,
        "origin": str(path),
        "snapshot": _relative(snapshot, root),
        "sha256": digest,
        "size": size,
        "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "imported_at": imported_at,
        "status": "new",
        "coverage_status": "pending",
    }
    if source_fields:
        record.update(source_fields)
    manifest["sources"][source_id] = record
    persist_manifest(root, manifest, [source_id])
    append_text(
        safe_child(root, "log.md"),
        f"- {imported_at} SOURCE_ADD id={source_id} kind={kind}\n",
    )
    return {"duplicate": False, "source": record}


def add_local_source(
    vault: Path,
    source_path: Path,
    *,
    kind: str,
    title: str | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    root = require_vault(vault)
    with workspace_lock(root):
        return _add_local_source_unlocked(
            root,
            source_path,
            kind=kind,
            title=title,
            max_bytes=max_bytes,
        )


def add_url_source(
    vault: Path, url: str, *, title: str | None = None
) -> dict[str, Any]:
    root = require_vault(vault)
    normalized = normalize_https_url(url)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    source_id = _source_id("web", digest)
    normalized_title = _require_title(
        title or urlsplit(normalized).hostname or "Web source"
    )
    with workspace_lock(root):
        manifest = _load_manifest(root)
        existing = manifest["sources"].get(source_id)
        if existing:
            return {"duplicate": True, "source": existing}

        record = {
            "id": source_id,
            "kind": "web",
            "title": normalized_title,
            "origin": normalized,
            "snapshot": None,
            "sha256": digest,
            "size": 0,
            "media_type": "text/html",
            "imported_at": utc_now(),
            "status": "new",
            "coverage_status": "pending",
        }
        return _record_source(root, manifest, record)


def attach_web_snapshot(
    vault: Path,
    source_id: str,
    snapshot_path: Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    root = require_vault(vault)
    path = snapshot_path.expanduser().resolve()
    if not path.is_file():
        raise ValidationError(f"snapshot is not a regular file: {path}")
    with workspace_lock(root):
        manifest = _load_manifest(root)
        source = manifest["sources"].get(source_id)
        if source is None:
            raise ValidationError(f"unknown source ID: {source_id}")
        if source.get("kind") != "web":
            raise ValidationError(f"source is not a web reference: {source_id}")
        if source.get("snapshot") is not None:
            raise StateError(f"web source already has a snapshot: {source_id}")
        source_dir = safe_child(root, "sources", source_id)
        suffix = (
            _snapshot_suffix(path)
            if _snapshot_suffix(path) in {".html", ".md", ".txt"}
            else ".txt"
        )
        destination = safe_child(source_dir, f"snapshot{suffix}")
        if destination.exists():
            raise StateError(f"source snapshot already exists: {source_id}")
        temp_snapshot, digest, size = _stream_snapshot_to_temp(
            path, source_dir, max_bytes=max_bytes
        )
        try:
            if destination.exists():
                raise StateError(f"source snapshot already exists: {source_id}")
            os.replace(temp_snapshot, destination)
        except Exception:
            temp_snapshot.unlink(missing_ok=True)
            raise
        destination.chmod(0o400)
        source["reference_sha256"] = source["sha256"]
        source["sha256"] = digest
        source["size"] = size
        source["snapshot"] = _relative(destination, root)
        source["media_type"] = mimetypes.guess_type(destination.name)[0] or "text/plain"
        source["snapshot_at"] = utc_now()
        persist_manifest(root, manifest, [source_id])
        append_text(
            safe_child(root, "log.md"),
            f"- {source['snapshot_at']} WEB_SNAPSHOT id={source_id}\n",
        )
        return {"source": source}


def add_snippet(
    vault: Path,
    *,
    title: str,
    text: str,
    kind: str = "chat",
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    root = require_vault(vault)
    if kind != "chat":
        raise ValidationError("snippets currently support kind=chat only")
    normalized_title = _require_title(title)
    if not text.strip():
        raise ValidationError("snippet text must not be empty")
    payload = text.encode("utf-8")
    if len(payload) > max_bytes:
        raise ValidationError(f"source exceeds {max_bytes} byte limit")
    digest = hashlib.sha256(payload).hexdigest()
    source_id = _source_id(kind, digest)
    with workspace_lock(root):
        manifest = _load_manifest(root)
        existing = manifest["sources"].get(source_id)
        if existing:
            return {"duplicate": True, "source": existing}

        source_dir = safe_child(root, "sources", source_id)
        if source_dir.exists():
            raise StateError(f"orphan source directory already exists: {source_id}")
        secure_directory(source_dir)
        snapshot = source_dir / "snippet.md"
        snapshot.write_bytes(payload)
        snapshot.chmod(0o400)
        imported_at = utc_now()
        record = {
            "id": source_id,
            "kind": kind,
            "title": normalized_title,
            "origin": "inline:chat",
            "snapshot": _relative(snapshot, root),
            "sha256": digest,
            "size": len(payload),
            "media_type": "text/markdown",
            "imported_at": imported_at,
            "status": "new",
            "coverage_status": "pending",
        }
        manifest["sources"][source_id] = record
        persist_manifest(root, manifest, [source_id])
        append_text(
            safe_child(root, "log.md"),
            f"- {imported_at} SOURCE_ADD id={source_id} kind={kind}\n",
        )
        return {"duplicate": False, "source": record}


def set_source_coverage(
    vault: Path,
    source_id: str,
    *,
    status: str,
) -> dict[str, Any]:
    root = require_vault(vault)
    if not isinstance(source_id, str) or not source_id:
        raise ValidationError("source ID must be a non-empty string")
    coverage_status = _require_coverage_status(status)
    with workspace_lock(root):
        manifest = _load_manifest(root)
        source = manifest["sources"].get(source_id)
        if source is None:
            raise ValidationError(f"unknown source ID: {source_id}")
        source["coverage_status"] = coverage_status
        persist_manifest(root, manifest, [source_id])
        changed_at = utc_now()
        append_text(
            safe_child(root, "log.md"),
            f"- {changed_at} SOURCE_COVERAGE id={source_id} status={coverage_status}\n",
        )
        return {"source": source}


def list_sources(vault: Path) -> list[dict[str, Any]]:
    root = require_vault(vault)
    manifest = _load_manifest(root)
    return sorted(
        manifest["sources"].values(), key=lambda item: (item["imported_at"], item["id"])
    )
