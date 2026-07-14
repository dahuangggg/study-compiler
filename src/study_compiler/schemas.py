from __future__ import annotations

import hashlib
import ipaddress
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .errors import StateError, ValidationError
from .storage import parse_utc_timestamp

SOURCE_ID_PATTERN = re.compile(r"(?:paper|document|chat|web)-[0-9a-f]{16}")
SLUG_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
SOURCE_KINDS = {"paper", "document", "chat", "web"}
SOURCE_STATUSES = {"new", "staged", "integrated"}
COVERAGE_STATUSES = {"pending", "partial", "complete"}
RELATIONSHIP_TYPES = {
    "extends",
    "implements",
    "contradicts",
    "derived_from",
    "uses",
    "replaces",
    "related_to",
}
RELATIONSHIP_TARGET_PATTERN = re.compile(r"\[\[lessons/([a-z0-9]+(?:-[a-z0-9]+)*)\]\]")
MAX_LESSON_RELATIONSHIPS = 100


def relationship_target_slug(value: Any) -> str:
    if not isinstance(value, str):
        raise ValidationError("relationship target must be a canonical lesson wikilink")
    match = RELATIONSHIP_TARGET_PATTERN.fullmatch(value)
    if match is None:
        raise ValidationError("relationship target must be a canonical lesson wikilink")
    return match.group(1)


def normalize_lesson_relationships(
    value: Any, *, source_slug: str
) -> list[dict[str, str]]:
    if value is None:
        raise ValidationError("lesson relationships must be a bounded list")
    if not isinstance(value, list) or len(value) > MAX_LESSON_RELATIONSHIPS:
        raise ValidationError("lesson relationships must be a bounded list")
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    seen_targets: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"target", "type"}:
            raise ValidationError(
                "lesson relationship must contain only target and type"
            )
        target = item.get("target")
        relation_type = item.get("type")
        target_slug = relationship_target_slug(target)
        if target_slug == source_slug:
            raise ValidationError("lesson relationship must not be a self-link")
        if (
            not isinstance(relation_type, str)
            or relation_type not in RELATIONSHIP_TYPES
        ):
            raise ValidationError("lesson relationship has an invalid type")
        key = (target_slug, relation_type)
        if key in seen:
            raise ValidationError("lesson relationship is duplicated")
        if target_slug in seen_targets:
            raise ValidationError("lesson relationship target has multiple types")
        seen.add(key)
        seen_targets.add(target_slug)
        normalized.append({"target": str(target), "type": relation_type})
    return sorted(normalized, key=lambda item: (item["target"], item["type"]))


def normalize_https_url(raw_url: str) -> str:
    if not isinstance(raw_url, str):
        raise ValidationError("invalid URL")
    try:
        parsed = urlsplit(raw_url.strip())
        port = parsed.port
    except ValueError as error:
        raise ValidationError("invalid URL") from error
    if parsed.scheme.lower() != "https":
        raise ValidationError("web sources must use HTTPS")
    if not parsed.hostname:
        raise ValidationError("URL must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValidationError("URL credentials are not allowed")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise ValidationError("local network URLs are not allowed")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValidationError("private or reserved IP URLs are not allowed")

    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        rendered_host = f"{rendered_host}:{port}"
    return urlunsplit(("https", rendered_host, parsed.path or "/", parsed.query, ""))


def validate_source_record(source_id: Any, source: Any) -> dict[str, Any]:
    label = f"source {source_id}"
    if not isinstance(source_id, str) or not SOURCE_ID_PATTERN.fullmatch(source_id):
        raise StateError(f"{label} has an invalid ID")
    if not isinstance(source, dict):
        raise StateError(f"{label} is not an object")
    if source.get("id") != source_id:
        raise StateError(f"{label} has an ID mismatch")
    kind = source.get("kind")
    if not isinstance(kind, str) or kind not in SOURCE_KINDS:
        raise StateError(f"{label} has an invalid kind")
    title = source.get("title")
    if (
        not isinstance(title, str)
        or not title.strip()
        or len(title.strip()) > 300
        or any(not character.isprintable() for character in title.strip())
    ):
        raise StateError(f"{label} has an invalid title")
    origin = source.get("origin")
    if not isinstance(origin, str) or not origin:
        raise StateError(f"{label} has an invalid origin")
    if kind == "web":
        try:
            normalized_origin = normalize_https_url(origin)
        except ValidationError as error:
            raise StateError(f"{label} has an invalid origin") from error
        if normalized_origin != origin:
            raise StateError(f"{label} has a non-canonical origin")

    digest = source.get("sha256")
    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
        raise StateError(f"{label} has an invalid sha256")
    size = source.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise StateError(f"{label} has an invalid size")
    if not isinstance(source.get("media_type"), str) or not source["media_type"]:
        raise StateError(f"{label} has an invalid media_type")
    try:
        parse_utc_timestamp(source.get("imported_at"))
    except ValueError as error:
        raise StateError(f"{label} has an invalid imported_at") from error

    status = source.get("status")
    if not isinstance(status, str) or status not in SOURCE_STATUSES:
        raise StateError(f"{label} has an invalid status")
    coverage = source.get("coverage_status")
    if coverage is not None and (
        not isinstance(coverage, str) or coverage not in COVERAGE_STATUSES
    ):
        raise StateError(f"{label} has an invalid coverage_status")
    snapshot = source.get("snapshot")
    if snapshot is None:
        if kind != "web":
            raise StateError(f"{label} is missing a snapshot")
    elif not isinstance(snapshot, str) or not snapshot:
        raise StateError(f"{label} has an invalid snapshot path")

    lesson_slugs = source.get("lesson_slugs")
    if lesson_slugs is not None and (
        not isinstance(lesson_slugs, list)
        or not all(
            isinstance(slug, str) and SLUG_PATTERN.fullmatch(slug)
            for slug in lesson_slugs
        )
    ):
        raise StateError(f"{label} has invalid lesson links")
    reference_hash = source.get("reference_sha256")
    if reference_hash is not None and (
        not isinstance(reference_hash, str)
        or not SHA256_PATTERN.fullmatch(reference_hash)
    ):
        raise StateError(f"{label} has an invalid reference_sha256")
    snapshot_at = source.get("snapshot_at")
    if snapshot_at is not None:
        try:
            parse_utc_timestamp(snapshot_at)
        except ValueError as error:
            raise StateError(f"{label} has an invalid snapshot_at") from error
    watch_paths = source.get("watch_paths")
    if watch_paths is not None and (
        not isinstance(watch_paths, list)
        or not watch_paths
        or not all(
            isinstance(path, str) and Path(path).is_absolute() for path in watch_paths
        )
        or watch_paths != sorted(set(watch_paths))
    ):
        raise StateError(f"{label} has invalid watch_paths")
    watch_change = source.get("watch_change")
    if watch_change is not None and watch_change not in {"new", "modified"}:
        raise StateError(f"{label} has an invalid watch_change")
    previous_source_id = source.get("previous_source_id")
    if previous_source_id is not None and (
        not isinstance(previous_source_id, str)
        or not SOURCE_ID_PATTERN.fullmatch(previous_source_id)
        or previous_source_id == source_id
    ):
        raise StateError(f"{label} has an invalid previous_source_id")
    return source


def validate_learning_record(slug: Any, lesson: Any) -> dict[str, Any]:
    label = f"learning-state lesson {slug}"
    if not isinstance(slug, str) or not SLUG_PATTERN.fullmatch(slug):
        raise StateError(f"{label} has an invalid slug")
    if not isinstance(lesson, dict):
        raise StateError(f"{label} must be an object")
    status = lesson.get("status")
    if not isinstance(status, str) or status not in {"draft", "approved"}:
        raise StateError(f"{label} has an invalid status")
    source_ids = lesson.get("source_ids")
    if (
        not isinstance(source_ids, list)
        or not source_ids
        or not all(
            isinstance(source_id, str) and SOURCE_ID_PATTERN.fullmatch(source_id)
            for source_id in source_ids
        )
    ):
        raise StateError(f"{label} has invalid source_ids")

    mastery_score = lesson.get("mastery_score")
    if mastery_score is not None and (
        isinstance(mastery_score, bool)
        or not isinstance(mastery_score, int)
        or mastery_score not in range(1, 6)
    ):
        raise StateError(f"{label} has invalid mastery_score")
    can_explain = lesson.get("can_explain")
    if not isinstance(can_explain, bool):
        raise StateError(f"{label} has invalid can_explain")
    if can_explain != (isinstance(mastery_score, int) and mastery_score >= 4):
        raise StateError(f"{label} can_explain does not match mastery_score")

    last_reviewed = lesson.get("last_reviewed")
    next_review = lesson.get("next_review")
    if (last_reviewed is None) != (next_review is None):
        raise StateError(f"{label} has an incomplete review schedule")
    if (mastery_score is None) != (last_reviewed is None):
        raise StateError(f"{label} mastery and review schedule do not match")
    if last_reviewed is not None:
        try:
            last_moment = parse_utc_timestamp(last_reviewed)
            next_moment = parse_utc_timestamp(next_review)
        except ValueError as error:
            raise StateError(f"{label} has invalid review timestamps") from error
        if next_moment <= last_moment:
            raise StateError(f"{label} next_review must follow last_reviewed")

    approved_at = lesson.get("approved_at")
    if approved_at is not None:
        try:
            parse_utc_timestamp(approved_at)
        except ValueError as error:
            raise StateError(f"{label} has invalid approved_at") from error
    awaiting_response = lesson.get("awaiting_response", False)
    if not isinstance(awaiting_response, bool):
        raise StateError(f"{label} has invalid awaiting_response")
    prompted_at = lesson.get("prompted_at")
    if awaiting_response:
        try:
            parse_utc_timestamp(prompted_at)
        except ValueError as error:
            raise StateError(f"{label} has invalid prompted_at") from error
    elif prompted_at is not None:
        raise StateError(f"{label} has prompted_at while not awaiting a response")
    return lesson


def snapshot_receipt(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
    except OSError as error:
        raise StateError(f"cannot read source snapshot: {path.name}") from error
    return digest.hexdigest(), size
