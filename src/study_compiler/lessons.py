from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .errors import StateError, ValidationError
from .schemas import (
    normalize_lesson_relationships,
    snapshot_receipt,
    validate_learning_record,
    validate_source_record,
)
from .state import persist_manifest, workspace_lock
from .storage import (
    append_text,
    parse_utc_timestamp,
    read_json,
    require_vault,
    safe_child,
    state_file,
    utc_now,
    write_json,
    write_text,
)

SLUG_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
LESSON_FORMAT_VERSION = 3
CITATION_FORMAT_VERSION = 2
REQUIRED_HEADINGS = (
    "Learning Objectives",
    "Intuition",
    "Example",
    "Explain It Back",
    "Sources",
)
MAX_RETELLING_CHARS = 100_000
MAX_LESSON_BODY_BYTES = 5 * 1024 * 1024
RELATED_BLOCK_START = "<!-- study-compiler:related:start -->"
RELATED_BLOCK_END = "<!-- study-compiler:related:end -->"


def _validate_relationship_markers(
    body: str,
    relationships: list[dict[str, str]],
) -> None:
    starts = body.count(RELATED_BLOCK_START)
    ends = body.count(RELATED_BLOCK_END)
    if not relationships:
        if starts or ends:
            raise ValidationError(
                "lesson body contains reserved Related Lessons block markers"
            )
        return
    if starts == 0 and ends == 0:
        return
    if (
        starts != 1
        or ends != 1
        or body.index(RELATED_BLOCK_START) > body.index(RELATED_BLOCK_END)
    ):
        raise ValidationError("lesson has a malformed Related Lessons block")


def _require_title(value: Any) -> str:
    if not isinstance(value, str):
        raise ValidationError("title must contain 1 to 300 printable characters")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 300
        or any(not character.isprintable() for character in normalized)
    ):
        raise ValidationError("title must contain 1 to 300 printable characters")
    return normalized


def require_slug(value: str) -> str:
    if not isinstance(value, str) or not SLUG_PATTERN.fullmatch(value):
        raise ValidationError(
            "slug must contain lowercase letters, numbers, or hyphens"
        )
    return value


def _lesson_sections(body: str) -> dict[str, str]:
    if not isinstance(body, str):
        raise ValidationError("lesson body must be text")
    if len(body.encode("utf-8")) > MAX_LESSON_BODY_BYTES:
        raise ValidationError(f"lesson body exceeds {MAX_LESSON_BODY_BYTES} byte limit")
    positions: list[tuple[int, int, str]] = []
    for heading in REQUIRED_HEADINGS:
        match = re.search(rf"(?m)^## {re.escape(heading)}\s*$", body)
        if match is None:
            raise ValidationError(f"lesson is missing required section: {heading}")
        positions.append((match.start(), match.end(), heading))
    positions.sort()
    sections: dict[str, str] = {}
    for index, (_, end, heading) in enumerate(positions):
        next_start = (
            positions[index + 1][0] if index + 1 < len(positions) else len(body)
        )
        section = body[end:next_start].strip()
        if not section:
            raise ValidationError(f"lesson section must not be empty: {heading}")
        sections[heading] = section
    return sections


def _require_nonempty_sections(body: str) -> None:
    _lesson_sections(body)


def _normalize_source_ids(source_ids: Any) -> list[str]:
    if not isinstance(source_ids, list) or not source_ids:
        raise ValidationError("lesson must cite at least one source ID")
    if any(not isinstance(source_id, str) or not source_id for source_id in source_ids):
        raise ValidationError("lesson source IDs must be non-empty strings")
    return list(dict.fromkeys(source_ids))


def _require_source_citations(body: str, source_ids: list[str]) -> None:
    sources_section = _lesson_sections(body)["Sources"]
    missing = [
        source_id
        for source_id in source_ids
        if re.search(
            rf"(?<![A-Za-z0-9_-])`?{re.escape(source_id)}`?(?![A-Za-z0-9_-])",
            sources_section,
        )
        is None
    ]
    if missing:
        raise ValidationError(
            f"lesson Sources section must cite source ID: {missing[0]}"
        )


def _render_lesson(metadata: dict[str, Any], body: str) -> str:
    frontmatter = ["---"]
    managed_keys = (
        "title",
        "slug",
        "source_ids",
        "created",
        "updated",
        "status",
        "format_version",
        "relationships",
    )
    extra_keys = sorted(key for key in metadata if key not in managed_keys)
    for key in (*managed_keys, *extra_keys):
        frontmatter.append(
            f"{key}: {json.dumps(metadata[key], ensure_ascii=False, allow_nan=False)}"
        )
    frontmatter.append("---")
    return "\n".join(frontmatter) + "\n\n" + body.strip() + "\n"


def _lesson_timestamp(value: Any, field: str) -> datetime:
    try:
        return parse_utc_timestamp(value)
    except ValueError as error:
        raise ValidationError(f"lesson has an invalid {field}") from error


def _read_lesson_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise StateError(f"cannot read lesson file: {path.name}") from error


def _decode_frontmatter_value(raw_value: str, key: str) -> Any:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for nested_key, nested_value in pairs:
            if nested_key in value:
                raise ValidationError(
                    f"duplicate key in lesson frontmatter value: {key}.{nested_key}"
                )
            value[nested_key] = nested_value
        return value

    def reject_constant(value: str) -> Any:
        raise ValidationError(
            f"non-finite number in lesson frontmatter value: {key}: {value}"
        )

    def parse_finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValidationError(
                f"non-finite number in lesson frontmatter value: {key}: {value}"
            )
        return parsed

    try:
        return json.loads(
            raw_value,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
            parse_float=parse_finite_float,
        )
    except json.JSONDecodeError as error:
        raise ValidationError(f"invalid lesson frontmatter value: {key}") from error
    except (ValueError, RecursionError) as error:
        raise ValidationError(f"invalid lesson frontmatter value: {key}") from error


def parse_lesson(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---\n"):
        raise ValidationError("lesson is missing frontmatter")
    try:
        frontmatter_text, body = content[4:].split("\n---\n", 1)
    except ValueError as error:
        raise ValidationError("lesson frontmatter is not terminated") from error
    metadata: dict[str, Any] = {}
    for line in frontmatter_text.splitlines():
        if ": " not in line:
            raise ValidationError(f"invalid lesson frontmatter line: {line}")
        key, raw_value = line.split(": ", 1)
        if key in metadata:
            raise ValidationError(f"duplicate lesson frontmatter field: {key}")
        metadata[key] = _decode_frontmatter_value(raw_value, key)
    required = {"title", "slug", "source_ids", "created", "updated", "status"}
    missing = required - metadata.keys()
    if missing:
        raise ValidationError(
            f"lesson frontmatter missing: {', '.join(sorted(missing))}"
        )
    require_slug(metadata["slug"])
    metadata["title"] = _require_title(metadata["title"])
    metadata["source_ids"] = _normalize_source_ids(metadata["source_ids"])
    created = _lesson_timestamp(metadata["created"], "created")
    updated = _lesson_timestamp(metadata["updated"], "updated")
    if updated < created:
        raise ValidationError("lesson updated timestamp precedes created timestamp")
    if not isinstance(metadata["status"], str) or metadata["status"] not in {
        "draft",
        "approved",
    }:
        raise ValidationError("lesson has an invalid status")
    format_version = metadata.get("format_version", 1)
    if type(format_version) is not int or format_version not in {1, 2, 3}:
        raise ValidationError("lesson has an unsupported format_version")
    metadata["format_version"] = format_version
    metadata["relationships"] = normalize_lesson_relationships(
        metadata.get("relationships", []), source_slug=metadata["slug"]
    )
    if format_version < 3 and metadata["relationships"]:
        raise ValidationError("legacy lesson format cannot contain relationships")
    _validate_relationship_markers(body, metadata["relationships"])
    _require_nonempty_sections(body)
    if format_version >= CITATION_FORMAT_VERSION:
        _require_source_citations(body, metadata["source_ids"])
    return metadata, body.strip()


def _manifest(vault: Path) -> dict[str, Any]:
    manifest = read_json(state_file(vault, "manifest.json"))
    if not isinstance(manifest.get("sources"), dict):
        raise StateError("manifest.json has an unsupported schema")
    return manifest


def _learning_state(vault: Path) -> dict[str, Any]:
    state = read_json(state_file(vault, "learning-state.json"))
    if not isinstance(state.get("lessons"), dict):
        raise StateError("learning-state.json has an unsupported schema")
    return state


def _require_lesson_state(
    slug: str,
    metadata: dict[str, Any],
    state: dict[str, Any],
    *,
    expected_status: str,
) -> dict[str, Any]:
    if metadata.get("slug") != slug:
        raise StateError(f"lesson slug does not match file name: {slug}")
    if metadata.get("status") != expected_status:
        raise StateError(f"lesson has invalid status: {slug}")
    lesson_state = state["lessons"].get(slug)
    if not isinstance(lesson_state, dict):
        raise StateError(f"lesson has no learning state: {slug}")
    validate_learning_record(slug, lesson_state)
    if lesson_state.get("status") != expected_status:
        raise StateError(f"lesson status does not match learning state: {slug}")
    if lesson_state.get("source_ids") != metadata.get("source_ids"):
        raise StateError(f"lesson sources do not match learning state: {slug}")
    if expected_status == "draft" and metadata.get("relationships"):
        raise StateError(
            "staged lesson relationships require a separately approved link patch"
        )
    return lesson_state


def _require_staged_state(
    slug: str,
    metadata: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    return _require_lesson_state(
        slug,
        metadata,
        state,
        expected_status="draft",
    )


def _require_current_source_evidence(
    root: Path,
    source_id: str,
    source: Any,
) -> None:
    validate_source_record(source_id, source)
    metadata_path = safe_child(root, "sources", source_id, "metadata.json")
    metadata = read_json(metadata_path)
    if metadata != source:
        raise StateError(f"source metadata drift from manifest: {source_id}")

    snapshot = source.get("snapshot")
    if not isinstance(snapshot, str) or not snapshot:
        raise StateError(f"source is missing a snapshot: {source_id}")
    snapshot_path = safe_child(root, snapshot)
    source_directory = safe_child(root, "sources", source_id)
    if snapshot_path.parent != source_directory:
        raise StateError(
            f"source snapshot is outside its source directory: {source_id}"
        )
    if not snapshot_path.is_file():
        raise StateError(f"source snapshot is missing: {source_id}")
    digest, size = snapshot_receipt(snapshot_path)
    if digest != source.get("sha256") or size != source.get("size"):
        raise StateError(f"source snapshot receipt drift: {source_id}")


def create_draft(
    vault: Path,
    *,
    slug: str,
    title: str,
    source_ids: list[str],
    body: str,
) -> dict[str, Any]:
    root = require_vault(vault)
    require_slug(slug)
    normalized_title = _require_title(title)
    unique_source_ids = _normalize_source_ids(source_ids)
    _require_nonempty_sections(body)
    _validate_relationship_markers(body, [])
    with workspace_lock(root):
        manifest = _manifest(root)
        unknown = [
            source_id
            for source_id in unique_source_ids
            if source_id not in manifest["sources"]
        ]
        if unknown:
            raise ValidationError(f"unknown source ID: {unknown[0]}")
        _require_source_citations(body, unique_source_ids)
        for source_id in unique_source_ids:
            if manifest["sources"][source_id].get("snapshot") is None:
                raise ValidationError(f"source is missing a snapshot: {source_id}")

        staged = safe_child(root, "_staging", "lessons", f"{slug}.md")
        approved = safe_child(root, "lessons", f"{slug}.md")
        if staged.exists() or approved.exists():
            raise StateError(f"lesson already exists: {slug}")
        timestamp = utc_now()
        metadata = {
            "title": normalized_title,
            "slug": slug,
            "source_ids": unique_source_ids,
            "created": timestamp,
            "updated": timestamp,
            "status": "draft",
            "format_version": LESSON_FORMAT_VERSION,
            "relationships": [],
        }
        write_text(staged, _render_lesson(metadata, body), exclusive=True)

        state = _learning_state(root)
        state["lessons"][slug] = {
            "status": "draft",
            "source_ids": unique_source_ids,
            "mastery_score": None,
            "can_explain": False,
            "last_reviewed": None,
            "next_review": None,
            "awaiting_response": False,
            "prompted_at": None,
        }
        write_json(state_file(root, "learning-state.json"), state)
        for source_id in unique_source_ids:
            source = manifest["sources"][source_id]
            if source.get("status") == "new":
                source["status"] = "staged"
            if source.get("coverage_status") != "complete":
                source["coverage_status"] = "partial"
            source["lesson_slugs"] = sorted(
                set(source.get("lesson_slugs", [])) | {slug}
            )
        persist_manifest(root, manifest, unique_source_ids)
        append_text(
            safe_child(root, "log.md"),
            f"- {timestamp} DRAFT_CREATE slug={slug}\n",
        )
        return {
            "path": staged.relative_to(root).as_posix(),
            "slug": slug,
            "status": "draft",
        }


def list_drafts(vault: Path) -> list[dict[str, Any]]:
    root = require_vault(vault)
    drafts: list[dict[str, Any]] = []
    for path in sorted((root / "_staging/lessons").glob("*.md")):
        if path.is_symlink():
            raise StateError(f"staged lesson must not be a symlink: {path.name}")
        metadata, _ = parse_lesson(_read_lesson_text(path))
        drafts.append(metadata | {"path": path.relative_to(root).as_posix()})
    return drafts


def revise_draft(
    vault: Path,
    slug: str,
    *,
    body: str,
    add_source_ids: list[str] | None = None,
) -> dict[str, Any]:
    root = require_vault(vault)
    require_slug(slug)
    _require_nonempty_sections(body)
    with workspace_lock(root):
        staged = safe_child(root, "_staging", "lessons", f"{slug}.md")
        if not staged.is_file():
            raise StateError(f"staged lesson not found: {slug}")
        metadata, _ = parse_lesson(_read_lesson_text(staged))
        state = _learning_state(root)
        lesson_state = _require_staged_state(slug, metadata, state)
        requested_source_ids = _normalize_source_ids(
            add_source_ids or metadata["source_ids"]
        )
        new_source_ids = [
            source_id
            for source_id in requested_source_ids
            if source_id not in metadata["source_ids"]
        ]
        combined_source_ids = list(
            dict.fromkeys(metadata["source_ids"] + new_source_ids)
        )
        manifest = _manifest(root)
        unknown = [
            source_id
            for source_id in new_source_ids
            if source_id not in manifest["sources"]
        ]
        if unknown:
            raise ValidationError(f"unknown source ID: {unknown[0]}")
        for source_id in new_source_ids:
            if manifest["sources"][source_id].get("snapshot") is None:
                raise ValidationError(f"source is missing a snapshot: {source_id}")
        if new_source_ids:
            review_path = safe_child(root, "reviews", f"{slug}.jsonl")
            if (
                lesson_state.get("awaiting_response", False)
                or lesson_state.get("mastery_score") is not None
                or (review_path.is_file() and review_path.stat().st_size > 0)
            ):
                raise StateError(
                    "cannot add sources to a reviewed or awaiting-response draft; "
                    "create a staged synthesis instead"
                )
        _require_source_citations(body, combined_source_ids)
        _validate_relationship_markers(body, metadata["relationships"])
        timestamp = utc_now()
        metadata["source_ids"] = combined_source_ids
        metadata["updated"] = timestamp
        metadata["format_version"] = LESSON_FORMAT_VERSION
        write_text(staged, _render_lesson(metadata, body))
        if new_source_ids:
            lesson_state["source_ids"] = combined_source_ids
            write_json(state_file(root, "learning-state.json"), state)
            for source_id in new_source_ids:
                source = manifest["sources"][source_id]
                if source.get("status") == "new":
                    source["status"] = "staged"
                if source.get("coverage_status") != "complete":
                    source["coverage_status"] = "partial"
                source["lesson_slugs"] = sorted(
                    set(source.get("lesson_slugs", [])) | {slug}
                )
            persist_manifest(root, manifest, new_source_ids)
        append_text(
            safe_child(root, "log.md"),
            f"- {timestamp} "
            f"{'DRAFT_INTEGRATE' if new_source_ids else 'DRAFT_REVISE'} slug={slug}\n",
        )
        return {
            "path": staged.relative_to(root).as_posix(),
            "slug": slug,
            "status": "draft",
            "source_ids": combined_source_ids,
        }


def _iso_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValidationError("review timestamp must include a timezone")
    return (
        value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def record_recall(
    vault: Path,
    slug: str,
    *,
    score: int,
    retelling: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = require_vault(vault)
    require_slug(slug)
    if isinstance(score, bool) or score not in range(1, 6):
        raise ValidationError("score must be an integer from 1 to 5")
    if not isinstance(retelling, str):
        raise ValidationError("retelling must not be empty")
    normalized_retelling = retelling.strip()
    if not normalized_retelling:
        raise ValidationError("retelling must not be empty")
    if len(normalized_retelling) > MAX_RETELLING_CHARS:
        raise ValidationError(f"retelling exceeds {MAX_RETELLING_CHARS} characters")
    reviewed_at = now or datetime.now(UTC)
    intervals = {1: 1, 2: 1, 3: 3, 4: 7, 5: 14}
    next_review_at = reviewed_at + timedelta(days=intervals[score])
    record = {
        "slug": slug,
        "score": score,
        "retelling": normalized_retelling,
        "reviewed_at": _iso_timestamp(reviewed_at),
        "next_review": _iso_timestamp(next_review_at),
    }
    with workspace_lock(root):
        staged = safe_child(root, "_staging", "lessons", f"{slug}.md")
        approved = safe_child(root, "lessons", f"{slug}.md")
        staged_exists = staged.is_file()
        approved_exists = approved.is_file()
        if staged_exists and approved_exists:
            raise StateError(f"lesson exists in staging and approved: {slug}")
        if not staged_exists and not approved_exists:
            raise StateError(f"lesson not found: {slug}")
        lesson_path = staged if staged_exists else approved
        expected_status = "draft" if staged_exists else "approved"
        metadata, _ = parse_lesson(_read_lesson_text(lesson_path))
        state = _learning_state(root)
        lesson_state = _require_lesson_state(
            slug,
            metadata,
            state,
            expected_status=expected_status,
        )
        last_reviewed = lesson_state.get("last_reviewed")
        if last_reviewed is not None and parse_utc_timestamp(
            record["reviewed_at"]
        ) < parse_utc_timestamp(last_reviewed):
            raise ValidationError("review timestamp cannot precede latest review")

        review_path = safe_child(root, "reviews", f"{slug}.jsonl")
        append_text(
            review_path,
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
        )

        lesson_state.update(
            {
                "mastery_score": score,
                "can_explain": score >= 4,
                "last_reviewed": record["reviewed_at"],
                "next_review": record["next_review"],
                "awaiting_response": False,
                "prompted_at": None,
            }
        )
        write_json(state_file(root, "learning-state.json"), state)
        append_text(
            safe_child(root, "log.md"),
            f"- {record['reviewed_at']} RECALL slug={slug} score={score}\n",
        )
        return record | {"can_explain": score >= 4}


def render_index(vault: Path) -> str:
    entries: list[tuple[str, str]] = []
    for path in sorted((vault / "lessons").glob("*.md")):
        if path.is_symlink():
            raise StateError(f"approved lesson must not be a symlink: {path.name}")
        metadata, _ = parse_lesson(_read_lesson_text(path))
        entries.append((metadata["slug"], metadata["title"]))
    lines = ["# Learning Notes", ""]
    if entries:
        lines.extend(f"- [[lessons/{slug}|{title}]]" for slug, title in entries)
    else:
        lines.append("No approved lessons yet.")
    return "\n".join(lines) + "\n"


def _rebuild_index(vault: Path) -> None:
    write_text(safe_child(vault, "index.md"), render_index(vault))


def promote_draft(vault: Path, slug: str, *, approve: bool) -> dict[str, Any]:
    root = require_vault(vault)
    require_slug(slug)
    if not approve:
        raise StateError("promotion requires explicit --approve")
    with workspace_lock(root):
        staged = safe_child(root, "_staging", "lessons", f"{slug}.md")
        destination = safe_child(root, "lessons", f"{slug}.md")
        if not staged.is_file():
            raise StateError(f"staged lesson not found: {slug}")
        if destination.exists():
            raise StateError(f"approved lesson already exists: {slug}")
        metadata, body = parse_lesson(_read_lesson_text(staged))
        _require_source_citations(body, metadata["source_ids"])
        if metadata["format_version"] < LESSON_FORMAT_VERSION:
            raise StateError(f"legacy lesson must be revised before promotion: {slug}")
        state = _learning_state(root)
        lesson_state = _require_staged_state(slug, metadata, state)
        manifest = _manifest(root)
        for source_id in metadata["source_ids"]:
            if source_id not in manifest["sources"]:
                raise StateError(f"lesson cites unknown source ID: {source_id}")
            _require_current_source_evidence(
                root,
                source_id,
                manifest["sources"][source_id],
            )
        timestamp = utc_now()
        metadata["status"] = "approved"
        metadata["updated"] = timestamp
        metadata["format_version"] = LESSON_FORMAT_VERSION
        write_text(destination, _render_lesson(metadata, body), exclusive=True)
        staged.unlink()

        lesson_state.update({"status": "approved", "approved_at": timestamp})
        write_json(state_file(root, "learning-state.json"), state)
        for source_id in metadata["source_ids"]:
            source = manifest["sources"][source_id]
            source["status"] = "integrated"
        persist_manifest(root, manifest, metadata["source_ids"])
        _rebuild_index(root)
        append_text(
            safe_child(root, "log.md"),
            f"- {timestamp} DRAFT_PROMOTE slug={slug}\n",
        )
        return {
            "path": destination.relative_to(root).as_posix(),
            "slug": slug,
            "status": "approved",
        }
