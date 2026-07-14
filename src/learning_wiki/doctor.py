from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .errors import LearningWikiError, ValidationError
from .lessons import parse_lesson, render_index
from .storage import read_json, require_vault, safe_child


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_state(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        return read_json(path)
    except LearningWikiError as error:
        errors.append(str(error))
        return None


def _check_source(
    root: Path,
    source_id: str,
    source: Any,
    errors: list[str],
    warnings: list[str],
) -> None:
    if not isinstance(source, dict):
        errors.append(f"source {source_id} is not an object")
        return
    if source.get("id") != source_id:
        errors.append(f"source ID mismatch: key={source_id}")
    metadata_path = root / "sources" / source_id / "metadata.json"
    if not metadata_path.is_file():
        errors.append(f"source {source_id} is missing metadata.json")
    else:
        metadata = _read_state(metadata_path, errors)
        if metadata is not None and metadata.get("id") != source_id:
            errors.append(f"source metadata ID mismatch: {source_id}")

    snapshot = source.get("snapshot")
    if snapshot is None:
        if source.get("kind") != "web":
            errors.append(f"source {source_id} is missing a snapshot")
        return
    if not isinstance(snapshot, str):
        errors.append(f"source {source_id} has an invalid snapshot path")
        return
    try:
        snapshot_path = safe_child(root, snapshot)
    except ValidationError:
        errors.append(f"source {source_id} snapshot escapes the vault")
        return
    if not snapshot_path.is_file():
        errors.append(f"source {source_id} snapshot is missing: {snapshot}")
        return
    expected_hash = source.get("sha256")
    if not isinstance(expected_hash, str) or _hash_file(snapshot_path) != expected_hash:
        errors.append(f"source {source_id} snapshot hash drift")
    if snapshot_path.stat().st_mode & 0o222:
        warnings.append(f"source {source_id} snapshot is writable")


def _check_lesson(
    path: Path,
    expected_status: str,
    source_ids: set[str],
    errors: list[str],
) -> None:
    try:
        metadata, _ = parse_lesson(path.read_text(encoding="utf-8"))
    except (LearningWikiError, OSError, UnicodeError, TypeError) as error:
        errors.append(f"invalid lesson {path.name}: {error}")
        return
    if metadata.get("status") != expected_status:
        errors.append(f"lesson {path.name} has status {metadata.get('status')}, expected {expected_status}")
    for source_id in metadata["source_ids"]:
        if source_id not in source_ids:
            errors.append(f"lesson {path.name} cites unknown source ID: {source_id}")


def inspect_vault(vault: Path) -> dict[str, Any]:
    root = require_vault(vault)
    errors: list[str] = []
    warnings: list[str] = []
    manifest = _read_state(root / ".learning-wiki/manifest.json", errors)
    learning_state = _read_state(root / ".learning-wiki/learning-state.json", errors)

    sources: dict[str, Any] = {}
    if manifest is not None:
        if manifest.get("schema_version") != 1 or not isinstance(manifest.get("sources"), dict):
            errors.append("manifest.json has an unsupported schema")
        else:
            sources = manifest["sources"]
            for source_id, source in sources.items():
                _check_source(root, source_id, source, errors, warnings)
            known_dirs = {path.name for path in (root / "sources").iterdir() if path.is_dir()}
            for orphan in sorted(known_dirs - sources.keys()):
                errors.append(f"orphan source directory: {orphan}")

    if learning_state is not None:
        if learning_state.get("schema_version") != 1 or not isinstance(learning_state.get("lessons"), dict):
            errors.append("learning-state.json has an unsupported schema")

    staged_paths = sorted((root / "_staging/lessons").glob("*.md"))
    approved_paths = sorted((root / "lessons").glob("*.md"))
    known_source_ids = set(sources)
    for path in staged_paths:
        _check_lesson(path, "draft", known_source_ids, errors)
    for path in approved_paths:
        _check_lesson(path, "approved", known_source_ids, errors)

    try:
        expected_index = render_index(root)
        actual_index = (root / "index.md").read_text(encoding="utf-8")
        if actual_index != expected_index:
            errors.append("index.md is stale")
    except (LearningWikiError, OSError, UnicodeError, TypeError) as error:
        errors.append(f"cannot validate index.md: {error}")

    for review_path in sorted((root / "reviews").glob("*.jsonl")):
        for line_number, line in enumerate(review_path.read_text(encoding="utf-8").splitlines(), start=1):
            try:
                record = json.loads(line)
                if not isinstance(record, dict) or record.get("score") not in range(1, 6):
                    raise ValueError("invalid score")
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                errors.append(f"invalid review {review_path.name}:{line_number}: {error}")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "sources": len(sources),
            "staged_lessons": len(staged_paths),
            "approved_lessons": len(approved_paths),
            "reviews": len(list((root / "reviews").glob("*.jsonl"))),
        },
    }

