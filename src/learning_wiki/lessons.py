from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .errors import StateError, ValidationError
from .storage import read_json, require_vault, safe_child, utc_now, write_json

SLUG_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
REQUIRED_HEADINGS = (
    "Learning Objectives",
    "Intuition",
    "Example",
    "Explain It Back",
    "Sources",
)
MAX_RETELLING_CHARS = 100_000


def require_slug(value: str) -> str:
    if not SLUG_PATTERN.fullmatch(value):
        raise ValidationError("slug must contain lowercase letters, numbers, or hyphens")
    return value


def _require_nonempty_sections(body: str) -> None:
    positions: list[tuple[int, int, str]] = []
    for heading in REQUIRED_HEADINGS:
        match = re.search(rf"(?m)^## {re.escape(heading)}\s*$", body)
        if match is None:
            raise ValidationError(f"lesson is missing required section: {heading}")
        positions.append((match.start(), match.end(), heading))
    positions.sort()
    for index, (_, end, heading) in enumerate(positions):
        next_start = positions[index + 1][0] if index + 1 < len(positions) else len(body)
        if not body[end:next_start].strip():
            raise ValidationError(f"lesson section must not be empty: {heading}")


def _render_lesson(metadata: dict[str, Any], body: str) -> str:
    frontmatter = ["---"]
    for key in ("title", "slug", "source_ids", "created", "updated", "status"):
        frontmatter.append(f"{key}: {json.dumps(metadata[key], ensure_ascii=False)}")
    frontmatter.append("---")
    return "\n".join(frontmatter) + "\n\n" + body.strip() + "\n"


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
        try:
            metadata[key] = json.loads(raw_value)
        except json.JSONDecodeError as error:
            raise ValidationError(f"invalid lesson frontmatter value: {key}") from error
    required = {"title", "slug", "source_ids", "created", "updated", "status"}
    missing = required - metadata.keys()
    if missing:
        raise ValidationError(f"lesson frontmatter missing: {', '.join(sorted(missing))}")
    require_slug(metadata["slug"])
    if not isinstance(metadata["source_ids"], list) or not metadata["source_ids"]:
        raise ValidationError("lesson must cite at least one source ID")
    _require_nonempty_sections(body)
    return metadata, body.strip()


def _manifest(vault: Path) -> dict[str, Any]:
    manifest = read_json(vault / ".learning-wiki/manifest.json")
    if not isinstance(manifest.get("sources"), dict):
        raise StateError("manifest.json has an unsupported schema")
    return manifest


def _learning_state(vault: Path) -> dict[str, Any]:
    state = read_json(vault / ".learning-wiki/learning-state.json")
    if not isinstance(state.get("lessons"), dict):
        raise StateError("learning-state.json has an unsupported schema")
    return state


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
    normalized_title = title.strip()
    if not normalized_title or len(normalized_title) > 300:
        raise ValidationError("title must contain 1 to 300 characters")
    _require_nonempty_sections(body)
    unique_source_ids = list(dict.fromkeys(source_ids))
    if not unique_source_ids:
        raise ValidationError("lesson must cite at least one source ID")
    manifest = _manifest(root)
    unknown = [source_id for source_id in unique_source_ids if source_id not in manifest["sources"]]
    if unknown:
        raise ValidationError(f"unknown source ID: {unknown[0]}")

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
    }
    staged.write_text(_render_lesson(metadata, body), encoding="utf-8")

    state = _learning_state(root)
    state["lessons"][slug] = {
        "status": "draft",
        "source_ids": unique_source_ids,
        "mastery_score": None,
        "can_explain": False,
        "last_reviewed": None,
        "next_review": None,
    }
    write_json(root / ".learning-wiki/learning-state.json", state)
    for source_id in unique_source_ids:
        source = manifest["sources"][source_id]
        source["status"] = "staged"
        source["lesson_slugs"] = sorted(set(source.get("lesson_slugs", [])) | {slug})
    write_json(root / ".learning-wiki/manifest.json", manifest)
    with (root / "log.md").open("a", encoding="utf-8") as log:
        log.write(f"- {timestamp} DRAFT_CREATE slug={slug}\n")
    return {"path": staged.relative_to(root).as_posix(), "slug": slug, "status": "draft"}


def list_drafts(vault: Path) -> list[dict[str, Any]]:
    root = require_vault(vault)
    drafts: list[dict[str, Any]] = []
    for path in sorted((root / "_staging/lessons").glob("*.md")):
        metadata, _ = parse_lesson(path.read_text(encoding="utf-8"))
        drafts.append(metadata | {"path": path.relative_to(root).as_posix()})
    return drafts


def revise_draft(vault: Path, slug: str, *, body: str) -> dict[str, Any]:
    root = require_vault(vault)
    require_slug(slug)
    _require_nonempty_sections(body)
    staged = safe_child(root, "_staging", "lessons", f"{slug}.md")
    if not staged.is_file():
        raise StateError(f"staged lesson not found: {slug}")
    metadata, _ = parse_lesson(staged.read_text(encoding="utf-8"))
    timestamp = utc_now()
    metadata["updated"] = timestamp
    staged.write_text(_render_lesson(metadata, body), encoding="utf-8")
    with (root / "log.md").open("a", encoding="utf-8") as log:
        log.write(f"- {timestamp} DRAFT_REVISE slug={slug}\n")
    return {"path": staged.relative_to(root).as_posix(), "slug": slug, "status": "draft"}


def _iso_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValidationError("review timestamp must include a timezone")
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    normalized_retelling = retelling.strip()
    if not normalized_retelling:
        raise ValidationError("retelling must not be empty")
    if len(normalized_retelling) > MAX_RETELLING_CHARS:
        raise ValidationError(f"retelling exceeds {MAX_RETELLING_CHARS} characters")
    staged = root / "_staging/lessons" / f"{slug}.md"
    approved = root / "lessons" / f"{slug}.md"
    if not staged.exists() and not approved.exists():
        raise StateError(f"lesson not found: {slug}")

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
    review_path = safe_child(root, "reviews", f"{slug}.jsonl")
    with review_path.open("a", encoding="utf-8") as review_file:
        review_file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    state = _learning_state(root)
    lesson_state = state["lessons"].setdefault(slug, {})
    lesson_state.update(
        {
            "mastery_score": score,
            "can_explain": score >= 4,
            "last_reviewed": record["reviewed_at"],
            "next_review": record["next_review"],
        }
    )
    write_json(root / ".learning-wiki/learning-state.json", state)
    with (root / "log.md").open("a", encoding="utf-8") as log:
        log.write(f"- {record['reviewed_at']} RECALL slug={slug} score={score}\n")
    return record | {"can_explain": score >= 4}


def render_index(vault: Path) -> str:
    entries: list[tuple[str, str]] = []
    for path in sorted((vault / "lessons").glob("*.md")):
        metadata, _ = parse_lesson(path.read_text(encoding="utf-8"))
        entries.append((metadata["slug"], metadata["title"]))
    lines = ["# Learning Notes", ""]
    if entries:
        lines.extend(f"- [[lessons/{slug}|{title}]]" for slug, title in entries)
    else:
        lines.append("No approved lessons yet.")
    return "\n".join(lines) + "\n"


def _rebuild_index(vault: Path) -> None:
    (vault / "index.md").write_text(render_index(vault), encoding="utf-8")


def promote_draft(vault: Path, slug: str, *, approve: bool) -> dict[str, Any]:
    root = require_vault(vault)
    require_slug(slug)
    if not approve:
        raise StateError("promotion requires explicit --approve")
    staged = safe_child(root, "_staging", "lessons", f"{slug}.md")
    destination = safe_child(root, "lessons", f"{slug}.md")
    if not staged.is_file():
        raise StateError(f"staged lesson not found: {slug}")
    if destination.exists():
        raise StateError(f"approved lesson already exists: {slug}")
    metadata, body = parse_lesson(staged.read_text(encoding="utf-8"))
    timestamp = utc_now()
    metadata["status"] = "approved"
    metadata["updated"] = timestamp
    destination.write_text(_render_lesson(metadata, body), encoding="utf-8")
    staged.unlink()

    state = _learning_state(root)
    lesson_state = state["lessons"].setdefault(slug, {})
    lesson_state.update({"status": "approved", "approved_at": timestamp})
    write_json(root / ".learning-wiki/learning-state.json", state)
    manifest = _manifest(root)
    for source_id in metadata["source_ids"]:
        source = manifest["sources"].get(source_id)
        if source is None:
            raise StateError(f"lesson cites unknown source ID: {source_id}")
        source["status"] = "integrated"
    write_json(root / ".learning-wiki/manifest.json", manifest)
    _rebuild_index(root)
    with (root / "log.md").open("a", encoding="utf-8") as log:
        log.write(f"- {timestamp} DRAFT_PROMOTE slug={slug}\n")
    return {"path": destination.relative_to(root).as_posix(), "slug": slug, "status": "approved"}
