from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import StateError, StudyCompilerError, ValidationError
from .graph import (
    export_graph,
    export_graph_file,
    graph_report,
    replace_related_block,
    validate_related_block,
)
from .lessons import (
    LESSON_FORMAT_VERSION,
    _render_lesson,
    parse_lesson,
    require_slug,
)
from .schemas import (
    RELATIONSHIP_TYPES,
    normalize_lesson_relationships,
    validate_learning_record,
)
from .state import workspace_lock
from .storage import (
    append_text,
    parse_utc_timestamp,
    read_json,
    require_vault,
    safe_child,
    state_directory,
    state_file,
    utc_now,
    write_json,
    write_text,
)

PATCH_SCHEMA_VERSION = 1
PATCH_KIND = "typed-link-patch"
PATCH_ID_PREFIX = "lp-"
MAX_PATCH_RELATIONS = 50
MAX_RELATION_REASON = 500
MAX_PATCH_INPUT_BYTES = 256 * 1024
PATCH_CONFIDENCE = {"extracted", "inferred"}


def _timestamp(value: datetime | None) -> str:
    if value is None:
        return utc_now()
    if value.tzinfo is None:
        raise ValidationError("link patch timestamp must include a timezone")
    return (
        value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _receipt(payload: bytes) -> dict[str, Any]:
    return {"sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}


def _file_receipt(path: Path) -> dict[str, Any]:
    try:
        return _receipt(path.read_bytes())
    except OSError as error:
        raise StateError(f"cannot read approved lesson: {path.name}") from error


def _approved_lesson(
    root: Path,
    slug: str,
) -> tuple[Path, dict[str, Any], str]:
    require_slug(slug)
    path = safe_child(root, "lessons", f"{slug}.md")
    if path.is_symlink():
        raise StateError(f"approved lesson must not be a symlink: {slug}")
    if not path.is_file():
        raise StateError(f"related lesson must be approved: {slug}")
    try:
        metadata, body = parse_lesson(path.read_text(encoding="utf-8"))
    except StudyCompilerError as error:
        raise StateError(f"approved lesson is invalid: {slug}: {error}") from error
    except (OSError, UnicodeError) as error:
        raise StateError(f"cannot read approved lesson: {slug}") from error
    if metadata.get("slug") != slug or metadata.get("status") != "approved":
        raise StateError(f"related lesson must be approved: {slug}")
    if metadata.get("format_version") == 1:
        raise StateError(f"legacy lesson cannot receive a link patch: {slug}")
    validate_related_block(body)

    learning_state = read_json(state_file(root, "learning-state.json"))
    lessons = learning_state.get("lessons")
    record = lessons.get(slug) if isinstance(lessons, dict) else None
    if not isinstance(record, dict) or record.get("status") != "approved":
        raise StateError(f"related lesson must be approved in learning state: {slug}")
    validate_learning_record(slug, record)
    if record.get("source_ids") != metadata.get("source_ids"):
        raise StateError(f"approved lesson sources do not match learning state: {slug}")
    return path, metadata, body


def _normalize_reason(value: Any) -> str:
    if not isinstance(value, str):
        raise ValidationError("relationship reason must be text")
    normalized = " ".join(value.split())
    if (
        not normalized
        or len(normalized) > MAX_RELATION_REASON
        or any(not character.isprintable() for character in normalized)
    ):
        raise ValidationError("relationship reason must be concise printable text")
    return normalized


def _normalize_proposals(
    source_slug: str,
    relations: Any,
) -> list[dict[str, Any]]:
    if (
        not isinstance(relations, list)
        or not relations
        or len(relations) > MAX_PATCH_RELATIONS
    ):
        raise ValidationError("link patch relations must be a non-empty bounded list")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    seen_targets: set[str] = set()
    for relation in relations:
        if not isinstance(relation, dict) or set(relation) != {
            "target",
            "type",
            "confidence",
            "score",
            "reason",
        }:
            raise ValidationError(
                "link relation must contain target, type, confidence, score, and reason"
            )
        target = require_slug(relation.get("target"))
        if target == source_slug:
            raise ValidationError("link patch must not contain a self relationship")
        relation_type = relation.get("type")
        if (
            not isinstance(relation_type, str)
            or relation_type not in RELATIONSHIP_TYPES
        ):
            raise ValidationError("link relation has an invalid type")
        confidence = relation.get("confidence")
        score = relation.get("score")
        if not isinstance(confidence, str) or confidence not in PATCH_CONFIDENCE:
            raise ValidationError("link relation has invalid confidence")
        if (
            isinstance(score, bool)
            or not isinstance(score, int)
            or score not in range(3, 14)
        ):
            raise ValidationError("link relation score must be from 3 to 13")
        if (score >= 6) != (confidence == "extracted"):
            raise ValidationError("link relation confidence does not match its score")
        key = (target, str(relation_type))
        if key in seen:
            raise ValidationError("link patch contains a duplicate relationship")
        if target in seen_targets:
            raise ValidationError(
                "link patch must not assign multiple types to one target"
            )
        seen.add(key)
        seen_targets.add(target)
        normalized.append(
            {
                "target": target,
                "type": relation_type,
                "confidence": confidence,
                "score": score,
                "reason": _normalize_reason(relation.get("reason")),
            }
        )
    return sorted(normalized, key=lambda item: (item["target"], item["type"]))


def _canonical_patch_id(
    source_slug: str,
    base_sha256: str,
    relations: list[dict[str, Any]],
) -> str:
    payload = json.dumps(
        {
            "source_slug": source_slug,
            "base_sha256": base_sha256,
            "relations": relations,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return PATCH_ID_PREFIX + hashlib.sha256(payload).hexdigest()[:16]


def _validate_patch(
    record: Any, *, expected_status: str | None = None
) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise StateError("link patch must be a JSON object")
    status = record.get("status")
    if not isinstance(status, str) or status not in {"staged", "applying", "applied"}:
        raise StateError("link patch has an invalid status")
    base_fields = {
        "schema_version",
        "kind",
        "status",
        "patch_id",
        "source_slug",
        "source_path",
        "base_sha256",
        "base_size",
        "relations",
        "created_at",
    }
    result_fields = {"applied_at", "result_sha256", "result_size"}
    allowed = base_fields | (
        result_fields if status in {"applying", "applied"} else set()
    )
    if set(record) != allowed:
        raise StateError("link patch has unsupported fields")
    schema_version = record.get("schema_version")
    if type(schema_version) is not int or schema_version != PATCH_SCHEMA_VERSION:
        raise StateError("link patch has an unsupported schema")
    if record.get("kind") != PATCH_KIND:
        raise StateError("link patch has an invalid kind")
    if expected_status is not None and status != expected_status:
        raise StateError(f"link patch is not {expected_status}")
    source_slug = record.get("source_slug")
    try:
        require_slug(source_slug)
    except ValidationError as error:
        raise StateError("link patch has an invalid source slug") from error
    if record.get("source_path") != f"lessons/{source_slug}.md":
        raise StateError("link patch has an invalid source path")
    base_hash = record.get("base_sha256")
    base_size = record.get("base_size")
    if (
        not isinstance(base_hash, str)
        or len(base_hash) != 64
        or any(character not in "0123456789abcdef" for character in base_hash)
        or isinstance(base_size, bool)
        or not isinstance(base_size, int)
        or base_size < 0
    ):
        raise StateError("link patch has an invalid base receipt")
    try:
        relations = _normalize_proposals(source_slug, record.get("relations"))
    except ValidationError as error:
        raise StateError(f"link patch is invalid: {error}") from error
    if relations != record.get("relations"):
        raise StateError("link patch relations are not canonical")
    expected_id = _canonical_patch_id(source_slug, base_hash, relations)
    if record.get("patch_id") != expected_id:
        raise StateError("link patch ID does not match its content")
    try:
        parse_utc_timestamp(record.get("created_at"))
    except ValueError as error:
        raise StateError("link patch has an invalid created_at") from error
    if status in {"applying", "applied"}:
        try:
            applied_at = parse_utc_timestamp(record.get("applied_at"))
            created_at = parse_utc_timestamp(record["created_at"])
        except ValueError as error:
            raise StateError("link patch has an invalid applied_at") from error
        if applied_at < created_at:
            raise StateError("link patch applied before it was created")
        result_hash = record.get("result_sha256")
        result_size = record.get("result_size")
        if (
            not isinstance(result_hash, str)
            or len(result_hash) != 64
            or any(character not in "0123456789abcdef" for character in result_hash)
            or isinstance(result_size, bool)
            or not isinstance(result_size, int)
            or result_size < 0
        ):
            raise StateError("link patch has an invalid result receipt")
    return record


def _staged_path(root: Path, patch_id: str) -> Path:
    if (
        not isinstance(patch_id, str)
        or not patch_id.startswith(PATCH_ID_PREFIX)
        or len(patch_id) != len(PATCH_ID_PREFIX) + 16
        or any(character not in "0123456789abcdef" for character in patch_id[3:])
    ):
        raise ValidationError("invalid link patch ID")
    return safe_child(root, "_staging", "link-patches", f"{patch_id}.json")


def _history_path(root: Path, patch_id: str) -> Path:
    _staged_path(root, patch_id)
    return safe_child(state_directory(root), "link-patches", f"{patch_id}.json")


def _required_relationships(record: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (f"[[lessons/{relation['target']}]]", relation["type"])
        for relation in record["relations"]
    }


def _require_applied_relationships(root: Path, record: dict[str, Any]) -> None:
    _, metadata, _ = _approved_lesson(root, record["source_slug"])
    current = {(item["target"], item["type"]) for item in metadata["relationships"]}
    for target, relation_type in sorted(_required_relationships(record)):
        if (target, relation_type) not in current:
            raise StateError(
                "applied link patch relation is missing: "
                f"{record['patch_id']} {record['source_slug']} "
                f"{relation_type} {target}"
            )


def stage_link_patch(
    vault: Path,
    source_slug: str,
    *,
    relations: list[dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    root = require_vault(vault)
    require_slug(source_slug)
    normalized = _normalize_proposals(source_slug, relations)
    with workspace_lock(root):
        source_path, metadata, _ = _approved_lesson(root, source_slug)
        existing = {
            (item["target"].removeprefix("[[lessons/").removesuffix("]]"), item["type"])
            for item in metadata["relationships"]
        }
        existing_targets = {target for target, _ in existing}
        for proposal in normalized:
            _approved_lesson(root, proposal["target"])
            if proposal["target"] in existing_targets:
                raise StateError(
                    "source lesson already has a relationship to target: "
                    f"{source_slug} {proposal['target']}"
                )
            if (proposal["target"], proposal["type"]) in existing:
                raise StateError(
                    "link relationship already exists: "
                    f"{source_slug} {proposal['type']} {proposal['target']}"
                )
        normalize_lesson_relationships(
            metadata["relationships"]
            + [
                {
                    "target": f"[[lessons/{proposal['target']}]]",
                    "type": proposal["type"],
                }
                for proposal in normalized
            ],
            source_slug=source_slug,
        )
        receipt = _file_receipt(source_path)
        patch_id = _canonical_patch_id(source_slug, receipt["sha256"], normalized)
        record = {
            "schema_version": PATCH_SCHEMA_VERSION,
            "kind": PATCH_KIND,
            "status": "staged",
            "patch_id": patch_id,
            "source_slug": source_slug,
            "source_path": f"lessons/{source_slug}.md",
            "base_sha256": receipt["sha256"],
            "base_size": receipt["size"],
            "relations": normalized,
            "created_at": _timestamp(now),
        }
        path = _staged_path(root, patch_id)
        if path.exists():
            existing_record = _validate_patch(read_json(path), expected_status="staged")
            comparable = dict(existing_record)
            comparable["created_at"] = record["created_at"]
            if comparable != record:
                raise StateError(f"link patch ID collision: {patch_id}")
            return existing_record | {
                "path": path.relative_to(root).as_posix(),
                "idempotent": True,
            }
        history = _history_path(root, patch_id)
        if history.exists():
            applied = _validate_patch(read_json(history))
            if applied["status"] == "applied":
                _require_applied_relationships(root, applied)
            return applied | {
                "path": history.relative_to(root).as_posix(),
                "idempotent": True,
            }
        write_json(path, record)
        append_text(
            safe_child(root, "log.md"),
            f"- {record['created_at']} LINK_PATCH_STAGE patch_id={patch_id} source={source_slug}\n",
        )
        return record | {"path": path.relative_to(root).as_posix(), "idempotent": False}


def list_link_patches(vault: Path) -> list[dict[str, Any]]:
    root = require_vault(vault)
    with workspace_lock(root):
        patches: list[dict[str, Any]] = []
        for path in sorted((root / "_staging/link-patches").glob("*.json")):
            record = _validate_patch(read_json(path), expected_status="staged")
            source_path = safe_child(root, record["source_path"])
            current = _file_receipt(source_path) if source_path.is_file() else None
            patches.append(
                record
                | {
                    "path": path.relative_to(root).as_posix(),
                    "stale": current is None
                    or current
                    != {
                        "sha256": record["base_sha256"],
                        "size": record["base_size"],
                    },
                }
            )
        return patches


def show_link_patch(vault: Path, patch_id: str) -> dict[str, Any]:
    root = require_vault(vault)
    with workspace_lock(root):
        staged = _staged_path(root, patch_id)
        history = _history_path(root, patch_id)
        path = staged if staged.is_file() else history
        if not path.is_file():
            raise StateError(f"link patch not found: {patch_id}")
        record = _validate_patch(read_json(path))
        return record | {"path": path.relative_to(root).as_posix()}


def _result_for_patch(
    root: Path,
    patch: dict[str, Any],
    *,
    applied_at: str,
) -> tuple[Path, str, dict[str, Any]]:
    source_slug = patch["source_slug"]
    source_path, metadata, body = _approved_lesson(root, source_slug)
    if parse_utc_timestamp(applied_at) < parse_utc_timestamp(metadata["updated"]):
        raise ValidationError("link patch timestamp cannot precede lesson updated time")
    for proposal in patch["relations"]:
        _approved_lesson(root, proposal["target"])
    additions = [
        {
            "target": f"[[lessons/{proposal['target']}]]",
            "type": proposal["type"],
        }
        for proposal in patch["relations"]
    ]
    existing_keys = {
        (item["target"], item["type"]) for item in metadata["relationships"]
    }
    duplicates = [
        item for item in additions if (item["target"], item["type"]) in existing_keys
    ]
    if duplicates:
        raise StateError("link patch relationship already exists; restage the patch")
    combined = normalize_lesson_relationships(
        metadata["relationships"] + additions,
        source_slug=source_slug,
    )
    metadata["relationships"] = combined
    metadata["format_version"] = LESSON_FORMAT_VERSION
    metadata["updated"] = applied_at
    rendered_body = replace_related_block(body, combined)
    content = _render_lesson(metadata, rendered_body)
    parsed_metadata, parsed_body = parse_lesson(content)
    if parsed_metadata["relationships"] != combined:
        raise StateError("link patch result did not preserve relationships")
    if replace_related_block(body, []) != replace_related_block(parsed_body, []):
        raise StateError("link patch attempted to change lesson teaching content")
    return source_path, content, _receipt(content.encode("utf-8"))


def apply_link_patch(
    vault: Path,
    patch_id: str,
    *,
    approve: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = require_vault(vault)
    if not approve:
        raise StateError("link patch application requires explicit --approve")
    with workspace_lock(root):
        staged_path = _staged_path(root, patch_id)
        history_path = _history_path(root, patch_id)
        history = read_json(history_path) if history_path.is_file() else None
        if history is not None:
            history = _validate_patch(history)
            if history["status"] == "applied":
                _require_applied_relationships(root, history)
                if staged_path.is_file():
                    staged = _validate_patch(
                        read_json(staged_path), expected_status="staged"
                    )
                    for key in (
                        "schema_version",
                        "kind",
                        "patch_id",
                        "source_slug",
                        "source_path",
                        "base_sha256",
                        "base_size",
                        "relations",
                        "created_at",
                    ):
                        if staged.get(key) != history.get(key):
                            raise StateError(
                                "applied link patch cleanup record does not match staging"
                            )
                    staged_path.unlink()
                return history | {"path": history_path.relative_to(root).as_posix()}
        if not staged_path.is_file():
            raise StateError(f"staged link patch not found: {patch_id}")
        patch = _validate_patch(read_json(staged_path), expected_status="staged")
        source_path = safe_child(root, patch["source_path"])
        current = _file_receipt(source_path) if source_path.is_file() else None
        base = {"sha256": patch["base_sha256"], "size": patch["base_size"]}

        applied_at = (
            history["applied_at"]
            if isinstance(history, dict) and history.get("status") == "applying"
            else _timestamp(now)
        )
        if parse_utc_timestamp(applied_at) < parse_utc_timestamp(patch["created_at"]):
            raise ValidationError(
                "link patch application cannot precede patch creation"
            )
        if history is not None and history.get("status") == "applying":
            for key in (
                "schema_version",
                "kind",
                "patch_id",
                "source_slug",
                "source_path",
                "base_sha256",
                "base_size",
                "relations",
                "created_at",
            ):
                if history.get(key) != patch.get(key):
                    raise StateError(
                        "link patch recovery record does not match staging"
                    )
            if current == {
                "sha256": history["result_sha256"],
                "size": history["result_size"],
            }:
                history["status"] = "applied"
                write_json(history_path, history)
                staged_path.unlink()
                return history | {"path": history_path.relative_to(root).as_posix()}
            if current != base:
                raise StateError("link patch recovery found conflicting lesson bytes")
        elif current != base:
            raise StateError(
                "link patch is stale; restage it against the current lesson"
            )

        source_path, content, result_receipt = _result_for_patch(
            root, patch, applied_at=applied_at
        )
        if (
            history is not None
            and history.get("status") == "applying"
            and (
                result_receipt["sha256"] != history["result_sha256"]
                or result_receipt["size"] != history["result_size"]
            )
        ):
            raise StateError("link patch recovery result does not match its receipt")

        applying = patch | {
            "status": "applying",
            "applied_at": applied_at,
            "result_sha256": result_receipt["sha256"],
            "result_size": result_receipt["size"],
        }
        write_json(history_path, applying)
        write_text(source_path, content)
        applying["status"] = "applied"
        write_json(history_path, applying)
        staged_path.unlink()
        append_text(
            safe_child(root, "log.md"),
            f"- {applied_at} LINK_PATCH_APPLY patch_id={patch_id} source={patch['source_slug']}\n",
        )
        return applying | {"path": history_path.relative_to(root).as_posix()}


def inspect_link_patches_unlocked(root: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    staged_count = 0
    applied_count = 0
    audited_relationships: dict[str, set[tuple[str, str]]] = {}
    staged_paths = sorted((root / "_staging/link-patches").glob("*.json"))
    try:
        history_directory = safe_child(state_directory(root), "link-patches")
    except StudyCompilerError as error:
        errors.append(f"invalid link patch history directory: {error}")
        history_paths: list[Path] = []
    else:
        if history_directory.exists() and not history_directory.is_dir():
            errors.append(
                "invalid link patch history directory: link-patches is not a directory"
            )
            history_paths = []
        else:
            history_paths = sorted(history_directory.glob("*.json"))
    duplicate_ids = {path.stem for path in staged_paths} & {
        path.stem for path in history_paths
    }
    for patch_id in sorted(duplicate_ids):
        errors.append(f"applied link patch cleanup is incomplete: {patch_id}")
    for path, expected in (
        *[(path, "staged") for path in staged_paths],
        *[(path, None) for path in history_paths],
    ):
        if path.is_symlink():
            errors.append(f"link patch must not be a symlink: {path.name}")
            continue
        try:
            record = _validate_patch(read_json(path), expected_status=expected)
            if record["patch_id"] != path.stem:
                raise StateError("link patch file name does not match its ID")
            if expected == "staged":
                staged_count += 1
                source_path = safe_child(root, record["source_path"])
                current = _file_receipt(source_path) if source_path.is_file() else None
                if current != {
                    "sha256": record["base_sha256"],
                    "size": record["base_size"],
                }:
                    warnings.append(f"staged link patch is stale: {record['patch_id']}")
            else:
                if record["status"] == "applying":
                    errors.append(
                        f"link patch application is incomplete: {record['patch_id']}"
                    )
                elif record["status"] != "applied":
                    errors.append(
                        f"link patch history is not applied: {record['patch_id']}"
                    )
                else:
                    _require_applied_relationships(root, record)
                    applied_count += 1
                    audited_relationships.setdefault(
                        record["source_slug"], set()
                    ).update(_required_relationships(record))
        except (StudyCompilerError, OSError, UnicodeError, TypeError) as error:
            errors.append(f"invalid link patch {path.name}: {error}")
    for path in sorted((root / "lessons").glob("*.md")):
        if path.is_symlink():
            continue
        try:
            metadata, _ = parse_lesson(path.read_text(encoding="utf-8"))
        except (StudyCompilerError, OSError, UnicodeError, TypeError):
            continue
        audited = audited_relationships.get(metadata["slug"], set())
        for relationship in metadata["relationships"]:
            key = (relationship["target"], relationship["type"])
            if key not in audited:
                errors.append(
                    "approved relationship has no applied link patch: "
                    f"{metadata['slug']} {relationship['type']} "
                    f"{relationship['target']}"
                )
    return {
        "errors": errors,
        "warnings": warnings,
        "staged": staged_count,
        "applied": applied_count,
    }


__all__ = [
    "apply_link_patch",
    "export_graph",
    "export_graph_file",
    "graph_report",
    "inspect_link_patches_unlocked",
    "list_link_patches",
    "show_link_patch",
    "stage_link_patch",
]
