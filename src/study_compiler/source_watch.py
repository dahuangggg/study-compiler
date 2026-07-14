from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from .errors import StateError, ValidationError
from .schemas import SHA256_PATTERN, SOURCE_ID_PATTERN
from .sources import (
    DEFAULT_MAX_BYTES,
    _add_local_source_unlocked,
    _load_manifest,
    _open_regular_source,
)
from .state import persist_manifest, workspace_lock
from .storage import (
    parse_utc_timestamp,
    read_json,
    require_vault,
    state_file,
    utc_now,
    write_json,
)


WATCH_SCHEMA_VERSION = 1
SUPPORTED_SUFFIXES = {
    ".csv",
    ".htm",
    ".html",
    ".json",
    ".jsonl",
    ".md",
    ".pdf",
    ".tsv",
    ".txt",
}
MAX_WATCHED_FILES = 10_000


class _TooLargeWatchedSource(Exception):
    pass


def _watch_path(root: Path) -> Path:
    return state_file(root, "source-watch.json")


def _default_watch() -> dict[str, Any]:
    return {"schema_version": WATCH_SCHEMA_VERSION, "roots": [], "files": {}}


def _canonical_absolute(value: Any) -> bool:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        return False
    try:
        return str(Path(value).expanduser().resolve()) == value
    except (OSError, ValueError):
        return False


def validate_source_watch(
    payload: Any, *, workspace: Path | None = None
) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise StateError("source-watch.json has an unsupported schema")
    roots = payload.get("roots")
    files = payload.get("files")
    if (
        not isinstance(roots, list)
        or roots != sorted(set(roots))
        or not all(_canonical_absolute(root) for root in roots)
        or not isinstance(files, dict)
    ):
        raise StateError("source-watch.json has an unsupported schema")
    event_sequences: list[int] = []
    for path, record in files.items():
        if not _canonical_absolute(path) or not isinstance(record, dict):
            raise StateError("source-watch.json has an invalid file record")
        digest = record.get("sha256")
        size = record.get("size")
        modified_ns = record.get("modified_ns")
        source_id = record.get("source_id")
        versions = record.get("versions")
        previous_paths = record.get("previous_paths", [])
        synced_at = record.get("synced_at")
        if (
            not isinstance(digest, str)
            or not SHA256_PATTERN.fullmatch(digest)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or isinstance(modified_ns, bool)
            or not isinstance(modified_ns, int)
            or modified_ns < 0
            or not isinstance(source_id, str)
            or not SOURCE_ID_PATTERN.fullmatch(source_id)
            or not isinstance(versions, list)
            or not versions
            or versions[-1] != source_id
            or not all(
                isinstance(item, str) and SOURCE_ID_PATTERN.fullmatch(item)
                for item in versions
            )
            or not isinstance(previous_paths, list)
            or previous_paths != sorted(set(previous_paths))
            or not all(_canonical_absolute(item) for item in previous_paths)
        ):
            raise StateError("source-watch.json has an invalid file record")
        try:
            parse_utc_timestamp(synced_at)
        except ValueError as error:
            raise StateError(
                "source-watch.json has an invalid file timestamp"
            ) from error
        change = record.get("change")
        previous_source_id = record.get("previous_source_id")
        event_sequence = record.get("event_sequence")
        if change is not None and change not in {"new", "modified"}:
            raise StateError("source-watch.json has an invalid file change")
        if previous_source_id is not None and (
            not isinstance(previous_source_id, str)
            or not SOURCE_ID_PATTERN.fullmatch(previous_source_id)
            or previous_source_id == source_id
        ):
            raise StateError("source-watch.json has an invalid previous source ID")
        if event_sequence is not None and (
            isinstance(event_sequence, bool)
            or not isinstance(event_sequence, int)
            or event_sequence < 1
        ):
            raise StateError("source-watch.json has an invalid event sequence")
        if isinstance(event_sequence, int) and not isinstance(event_sequence, bool):
            event_sequences.append(event_sequence)
    if len(event_sequences) != len(set(event_sequences)):
        raise StateError("source-watch.json has duplicate event sequences")
    if workspace is not None:
        workspace_root = workspace.expanduser().resolve()
        root_paths = [Path(root) for root in roots]
        if any(_overlaps(workspace_root, root) for root in root_paths):
            raise StateError("source-watch.json has a root overlapping the workspace")
        for index, first in enumerate(root_paths):
            if any(_overlaps(first, second) for second in root_paths[index + 1 :]):
                raise StateError("source-watch.json has overlapping source roots")
    return payload


def load_source_watch(vault: Path) -> dict[str, Any]:
    root = require_vault(vault)
    path = _watch_path(root)
    if not path.exists():
        return _default_watch()
    return validate_source_watch(read_json(path), workspace=root)


def _overlaps(first: Path, second: Path) -> bool:
    return (
        first == second or first.is_relative_to(second) or second.is_relative_to(first)
    )


def configure_source_roots(vault: Path, roots: list[Path]) -> dict[str, Any]:
    root = require_vault(vault)
    canonical: list[Path] = []
    for candidate in roots:
        expanded = candidate.expanduser()
        if expanded.is_symlink():
            raise ValidationError(f"source root must not be a symlink: {expanded}")
        resolved = expanded.resolve()
        if not resolved.is_dir():
            raise ValidationError(f"source root is not a directory: {resolved}")
        if _overlaps(root, resolved):
            raise ValidationError(
                f"source root must not overlap the workspace: {resolved}"
            )
        if resolved in canonical:
            continue
        if any(_overlaps(resolved, existing) for existing in canonical):
            raise ValidationError(f"source roots must not overlap: {resolved}")
        canonical.append(resolved)
    rendered = sorted(str(path) for path in canonical)
    with workspace_lock(root):
        state = load_source_watch(root)
        state["roots"] = rendered
        _reconcile_manifest_watch_paths(root, state["files"], rendered)
        write_json(_watch_path(root), state)
    return {"workspace": str(root), "roots": rendered}


def _hash_file(path: Path, root: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    try:
        with _open_regular_source(path, allowed_roots=[root]) as (
            handle,
            opened_stat,
        ):
            if opened_stat.st_size > DEFAULT_MAX_BYTES:
                raise _TooLargeWatchedSource
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
            final_stat = os.fstat(handle.fileno())
            if (
                final_stat.st_size != opened_stat.st_size
                or final_stat.st_mtime_ns != opened_stat.st_mtime_ns
            ):
                raise StateError(f"watched source changed while scanning: {path}")
    except ValidationError as error:
        raise StateError(f"cannot safely read watched source: {path}") from error
    except OSError as error:
        raise StateError(f"cannot read watched source: {path}") from error
    return digest.hexdigest(), final_stat.st_size, final_stat.st_mtime_ns


def _discover(
    roots: list[str],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]], list[str]]:
    discovered: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, str]] = []
    missing_roots: list[str] = []
    for rendered_root in roots:
        root = Path(rendered_root)
        if root.is_symlink() or not root.is_dir():
            missing_roots.append(rendered_root)
            continue
        for directory, directory_names, file_names in os.walk(root, followlinks=False):
            current = Path(directory)
            retained_directories: list[str] = []
            for name in sorted(directory_names):
                candidate = current / name
                if name.startswith("."):
                    continue
                if candidate.is_symlink():
                    skipped.append({"path": str(candidate), "reason": "symlink"})
                    continue
                retained_directories.append(name)
            directory_names[:] = retained_directories
            for name in sorted(file_names):
                if name.startswith("."):
                    continue
                candidate = current / name
                if candidate.is_symlink():
                    skipped.append({"path": str(candidate), "reason": "symlink"})
                    continue
                if candidate.suffix.lower() not in SUPPORTED_SUFFIXES:
                    continue
                if not candidate.is_file():
                    continue
                try:
                    digest, size, modified_ns = _hash_file(candidate, root)
                except _TooLargeWatchedSource:
                    skipped.append({"path": str(candidate), "reason": "too-large"})
                    continue
                canonical = str(candidate)
                discovered[canonical] = {
                    "path": canonical,
                    "sha256": digest,
                    "size": size,
                    "modified_ns": modified_ns,
                }
                if len(discovered) > MAX_WATCHED_FILES:
                    raise StateError(
                        f"source watch exceeds {MAX_WATCHED_FILES} supported files"
                    )
    return (
        discovered,
        sorted(skipped, key=lambda item: item["path"]),
        sorted(missing_roots),
    )


def _under_roots(path: str, roots: list[str]) -> bool:
    candidate = Path(path)
    return any(candidate.is_relative_to(Path(root)) for root in roots)


def scan_source_changes(vault: Path) -> dict[str, Any]:
    root = require_vault(vault)
    state = load_source_watch(root)
    roots = state["roots"]
    discovered, skipped, missing_roots = _discover(roots)
    available_roots = [root for root in roots if root not in missing_roots]
    tracked = {
        path: record
        for path, record in state["files"].items()
        if _under_roots(path, available_roots)
    }
    result: dict[str, Any] = {
        "roots": roots,
        "new": [],
        "modified": [],
        "touched": [],
        "unchanged": [],
        "renamed": [],
        "deleted": [],
        "skipped": skipped,
        "missing_roots": missing_roots,
        "unavailable_roots": missing_roots,
    }
    for path, current in discovered.items():
        previous = tracked.get(path)
        if previous is None:
            result["new"].append(dict(current))
        elif previous["sha256"] != current["sha256"]:
            result["modified"].append(
                dict(current) | {"previous_source_id": previous["source_id"]}
            )
        elif (
            previous["modified_ns"] != current["modified_ns"]
            or previous["size"] != current["size"]
        ):
            result["touched"].append(
                dict(current) | {"source_id": previous["source_id"]}
            )
        else:
            result["unchanged"].append(
                dict(current) | {"source_id": previous["source_id"]}
            )
    for path, previous in tracked.items():
        if path not in discovered:
            result["deleted"].append(
                {
                    "path": path,
                    "sha256": previous["sha256"],
                    "source_id": previous["source_id"],
                }
            )

    new_by_hash: dict[str, list[dict[str, Any]]] = {}
    deleted_by_hash: dict[str, list[dict[str, Any]]] = {}
    for item in result["new"]:
        new_by_hash.setdefault(item["sha256"], []).append(item)
    for item in result["deleted"]:
        deleted_by_hash.setdefault(item["sha256"], []).append(item)
    renamed_new_paths: set[str] = set()
    renamed_old_paths: set[str] = set()
    for digest in sorted(new_by_hash.keys() & deleted_by_hash.keys()):
        new_items = new_by_hash[digest]
        deleted_items = deleted_by_hash[digest]
        if len(new_items) == 1 and len(deleted_items) == 1:
            new_item = new_items[0]
            deleted_item = deleted_items[0]
            result["renamed"].append(
                dict(new_item)
                | {
                    "previous_path": deleted_item["path"],
                    "source_id": deleted_item["source_id"],
                }
            )
            renamed_new_paths.add(new_item["path"])
            renamed_old_paths.add(deleted_item["path"])
    result["new"] = [
        item for item in result["new"] if item["path"] not in renamed_new_paths
    ]
    result["deleted"] = [
        item for item in result["deleted"] if item["path"] not in renamed_old_paths
    ]
    for key in ("new", "modified", "touched", "unchanged", "renamed", "deleted"):
        result[key].sort(key=lambda item: item["path"])
    return result


def _sync_next_source_change_unlocked(root: Path) -> dict[str, Any]:
    state = load_source_watch(root)
    delta = scan_source_changes(root)
    if delta["unavailable_roots"]:
        rendered = ", ".join(delta["unavailable_roots"])
        raise StateError(f"configured source roots are unavailable: {rendered}")
    files = state["files"]
    renamed = delta["renamed"]
    for item in renamed:
        previous_path = item["previous_path"]
        entry = files.pop(previous_path)
        entry.update(
            {
                "sha256": item["sha256"],
                "size": item["size"],
                "modified_ns": item["modified_ns"],
                "previous_paths": sorted(
                    set(entry.get("previous_paths", [])) | {previous_path}
                ),
            }
        )
        files[item["path"]] = entry

    for item in delta["touched"]:
        entry = files[item["path"]]
        entry["size"] = item["size"]
        entry["modified_ns"] = item["modified_ns"]

    candidates = delta["modified"] + delta["new"]
    synced: dict[str, Any] | None = None
    if candidates:
        candidate = candidates[0]
        path = Path(candidate["path"])
        change = "modified" if candidate in delta["modified"] else "new"
        previous_entry = files.get(candidate["path"])
        previous_source_id = (
            previous_entry.get("source_id")
            if isinstance(previous_entry, dict)
            else None
        )
        source_fields: dict[str, Any] = {
            "watch_paths": [candidate["path"]],
        }
        added = _add_local_source_unlocked(
            root,
            path,
            kind="document",
            title=path.stem,
            source_fields=source_fields,
            allowed_roots=[Path(value) for value in state["roots"]],
            expected_sha256=candidate["sha256"],
        )
        source = added["source"]
        try:
            stat = path.stat()
        except OSError as error:
            raise StateError(
                f"cannot inspect watched source after snapshot: {path}"
            ) from error
        versions = list(previous_entry.get("versions", [])) if previous_entry else []
        versions.append(source["id"])
        event_sequence = (
            max(
                (
                    record.get("event_sequence", 0)
                    for record in files.values()
                    if isinstance(record.get("event_sequence", 0), int)
                    and not isinstance(record.get("event_sequence", 0), bool)
                ),
                default=0,
            )
            + 1
        )
        files[candidate["path"]] = {
            "sha256": source["sha256"],
            "size": source["size"],
            "modified_ns": stat.st_mtime_ns,
            "source_id": source["id"],
            "versions": versions,
            "change": change,
            "event_sequence": event_sequence,
            "previous_paths": sorted(
                set(previous_entry.get("previous_paths", []))
                if previous_entry
                else set()
            ),
            "synced_at": utc_now(),
        }
        if previous_source_id is not None:
            files[candidate["path"]]["previous_source_id"] = previous_source_id
        synced = {
            "change": change,
            "path": candidate["path"],
            "previous_source_id": previous_source_id,
            "duplicate": added["duplicate"],
            "source": source,
        }
    if renamed or delta["touched"] or synced is not None:
        manifest = _reconcile_manifest_watch_paths(root, files, state["roots"])
        write_json(_watch_path(root), state)
        if synced is not None:
            synced["source"] = manifest["sources"][synced["source"]["id"]]
    return {"renamed": renamed, "touched": delta["touched"], "synced": synced}


def _reconcile_manifest_watch_paths(
    root: Path,
    files: dict[str, dict[str, Any]],
    active_roots: list[str],
) -> dict[str, Any]:
    manifest = _load_manifest(root)
    current_paths: dict[str, set[str]] = {}
    for path, record in files.items():
        source = manifest["sources"].get(record["source_id"])
        if isinstance(source, dict):
            legacy_change = source.get("watch_change")
            if "change" not in record and legacy_change in {"new", "modified"}:
                record["change"] = legacy_change
            legacy_previous = source.get("previous_source_id")
            if (
                "previous_source_id" not in record
                and isinstance(legacy_previous, str)
                and SOURCE_ID_PATTERN.fullmatch(legacy_previous)
                and legacy_previous != record["source_id"]
            ):
                record["previous_source_id"] = legacy_previous
        if _under_roots(path, active_roots):
            current_paths.setdefault(record["source_id"], set()).add(path)
    changed_source_ids: list[str] = []
    for source_id, source in manifest["sources"].items():
        changed = False
        desired_paths = sorted(current_paths.get(source_id, set()))
        if desired_paths:
            if source.get("watch_paths") != desired_paths:
                source["watch_paths"] = desired_paths
                changed = True
        elif "watch_paths" in source:
            source.pop("watch_paths")
            changed = True
        for obsolete in ("watch_change", "previous_source_id"):
            if obsolete in source:
                source.pop(obsolete)
                changed = True
        if changed:
            changed_source_ids.append(source_id)
    if changed_source_ids:
        persist_manifest(root, manifest, changed_source_ids)
    return manifest


def project_current_watch_metadata(
    vault: Path, sources: dict[str, Any]
) -> dict[str, Any]:
    """Project authoritative current watch records onto planner-only source copies."""
    state = load_source_watch(vault)
    active_roots = state["roots"]
    projected = {source_id: dict(source) for source_id, source in sources.items()}
    legacy_events = {
        source_id: (
            source.get("watch_change"),
            source.get("previous_source_id"),
        )
        for source_id, source in projected.items()
    }
    for source in projected.values():
        source.pop("watch_paths", None)
        source.pop("watch_change", None)
        source.pop("previous_source_id", None)
    current: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for path, record in state["files"].items():
        if _under_roots(path, active_roots):
            current.setdefault(record["source_id"], []).append((path, record))
    for source_id, entries in current.items():
        source = projected.get(source_id)
        if source is None:
            continue
        entries.sort(key=lambda item: item[0])
        selected_path, selected = max(
            entries,
            key=lambda item: (
                item[1].get("event_sequence", 0),
                item[1]["synced_at"],
                item[0],
            ),
        )
        legacy_change, legacy_previous = legacy_events.get(source_id, (None, None))
        source["watch_paths"] = [path for path, _ in entries]
        source["watch_path"] = selected_path
        source["watch_change"] = selected.get("change", legacy_change or "new")
        previous_source_id = selected.get("previous_source_id", legacy_previous)
        if previous_source_id is not None and previous_source_id != source_id:
            source["previous_source_id"] = previous_source_id
    return projected


def sync_next_source_change(vault: Path) -> dict[str, Any]:
    root = require_vault(vault)
    with workspace_lock(root):
        return _sync_next_source_change_unlocked(root)
