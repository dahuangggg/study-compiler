from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .errors import StateError, ValidationError
from .lessons import list_drafts
from .schemas import validate_learning_record, validate_source_record
from .source_watch import project_current_watch_metadata
from .state import workspace_lock
from .storage import (
    append_text,
    parse_utc_timestamp,
    read_json,
    require_vault,
    safe_child,
    state_file,
    write_text,
)

PERIODS = {"daily", "weekly", "monthly"}
PERIOD_HORIZON_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValidationError("digest timestamp must include a timezone")
    return (
        value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _load_state(vault: Path, name: str, key: str) -> dict[str, Any]:
    state = read_json(state_file(vault, name))
    if not isinstance(state.get(key), dict):
        raise StateError(f"{name} has an unsupported schema")
    return state


def _source_coverage(source: dict[str, Any]) -> str:
    coverage = source.get("coverage_status")
    if isinstance(coverage, str):
        return coverage
    return "pending" if source.get("status") == "new" else "partial"


def plan_digest(
    *,
    sources: dict[str, dict[str, Any]],
    lessons: dict[str, dict[str, Any]],
    staged_lessons: list[dict[str, Any]],
    period: str,
    now: datetime,
) -> dict[str, Any]:
    """Return a deterministic, side-effect-free learning work plan."""
    if period not in PERIODS:
        raise ValidationError(f"unsupported digest period: {period}")
    for source_id, source in sources.items():
        validate_source_record(source_id, source)
    for slug, lesson in lessons.items():
        validate_learning_record(slug, lesson)
    for lesson in staged_lessons:
        if (
            not isinstance(lesson, dict)
            or not isinstance(lesson.get("slug"), str)
            or not isinstance(lesson.get("title"), str)
        ):
            raise StateError("staging contains an invalid lesson record")
    _timestamp(now)
    now_utc = now.astimezone(UTC)
    horizon = now_utc + timedelta(days=PERIOD_HORIZON_DAYS[period])

    snapshot_items: list[dict[str, Any]] = []
    source_items: list[dict[str, Any]] = []
    for source_id, source in sorted(sources.items()):
        coverage = _source_coverage(source)
        if source.get("kind") == "web" and not source.get("snapshot"):
            snapshot_items.append(
                {
                    "type": "source_snapshot",
                    "id": source_id,
                    "title": source["title"],
                    "kind": "web",
                    "url": source["origin"],
                }
            )
            continue
        if not source.get("snapshot") or coverage == "complete":
            continue
        source_items.append(
            {
                "type": "source",
                "id": source_id,
                "title": source["title"],
                "kind": source["kind"],
                "status": source.get("status"),
                "coverage_status": coverage,
            }
            | (
                {
                    "watched": True,
                    "change": source.get("watch_change"),
                    "path": source.get("watch_path", source["watch_paths"][0]),
                    "previous_source_id": source.get("previous_source_id"),
                }
                if source.get("watch_paths")
                else {}
            )
        )

    staged_lesson_items = [
        {
            "type": "staged_lesson",
            "id": lesson["slug"],
            "title": lesson["title"],
            "path": lesson.get("path"),
        }
        for lesson in sorted(staged_lessons, key=lambda lesson: lesson["slug"])
        if not lessons.get(lesson["slug"], {}).get("awaiting_response", False)
    ]

    review_items: list[dict[str, Any]] = []
    for slug, lesson in sorted(lessons.items()):
        if lesson.get("awaiting_response", False):
            continue
        score = lesson.get("mastery_score")
        next_review = lesson.get("next_review")
        next_review_at = (
            parse_utc_timestamp(next_review) if isinstance(next_review, str) else None
        )
        due = next_review_at is not None and next_review_at <= now_utc
        in_horizon = next_review_at is not None and next_review_at <= horizon
        weak = isinstance(score, int) and score < 4
        if in_horizon or weak:
            review_items.append(
                {
                    "type": "review",
                    "id": slug,
                    "score": score,
                    "due": due,
                    "weak": weak,
                    "next_review": next_review,
                }
            )

    watched_source_items = [item for item in source_items if item.get("watched")]
    regular_source_items = [item for item in source_items if not item.get("watched")]
    items = (
        watched_source_items
        + review_items
        + staged_lesson_items
        + snapshot_items
        + regular_source_items
    )
    return {
        "snapshot_items": snapshot_items,
        "source_items": source_items,
        "staged_lesson_items": staged_lesson_items,
        "review_items": review_items,
        "items": items,
        "next_action": items[0] if items else None,
    }


def _build_digest_unlocked(
    vault: Path,
    *,
    period: str = "weekly",
    now: datetime | None = None,
    filename: str | None = None,
) -> dict[str, Any]:
    root = require_vault(vault)
    if period not in PERIODS:
        raise ValidationError(f"unsupported digest period: {period}")
    generated_at = now or datetime.now(UTC)
    generated_timestamp = _timestamp(generated_at)
    manifest = _load_state(root, "manifest.json", "sources")
    learning_state = _load_state(root, "learning-state.json", "lessons")
    plan = plan_digest(
        sources=project_current_watch_metadata(root, manifest["sources"]),
        lessons=learning_state["lessons"],
        staged_lessons=list_drafts(root),
        period=period,
        now=generated_at,
    )
    snapshot_items = plan["snapshot_items"]
    source_items = plan["source_items"]
    staged_lesson_items = plan["staged_lesson_items"]
    review_items = plan["review_items"]

    lines = [
        "---",
        f"title: {json.dumps(f'{period.title()} Learning Agenda')}",
        f"period: {json.dumps(period)}",
        f"created: {json.dumps(generated_timestamp)}",
        'status: "draft"',
        "---",
        "",
        f"# {period.title()} Learning Agenda",
        "",
        "This agenda is deterministic input for the `learning-digest` Agent Skill.",
        "",
        "## Sources Needing Snapshot",
        "",
    ]
    if snapshot_items:
        lines.extend(
            f"- `{source['id']}` — "
            f"{json.dumps(source['title'], ensure_ascii=False)} "
            f"({json.dumps(source['url'], ensure_ascii=False)})"
            for source in snapshot_items
        )
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## New Sources",
            "",
        ]
    )
    if source_items:
        lines.extend(
            f"- `{source['id']}` — "
            f"{json.dumps(source['title'], ensure_ascii=False)} ({source['kind']})"
            for source in source_items
        )
    else:
        lines.append("- None")
    lines.extend(["", "## Staged Lessons", ""])
    if staged_lesson_items:
        lines.extend(
            f"- `{lesson['id']}` — {json.dumps(lesson['title'], ensure_ascii=False)}"
            for lesson in staged_lesson_items
        )
    else:
        lines.append("- None")
    lines.extend(["", "## Review Due", ""])
    if review_items:
        for item in review_items:
            score = (
                f"score {item['score']}" if item["score"] is not None else "not scored"
            )
            if item["due"]:
                reason = "due"
            elif item["weak"]:
                reason = "weak recall"
            else:
                reason = "upcoming review"
            lines.append(f"- `{item['id']}` — {score}; {reason}")
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Agent Handoff",
            "",
            "Choose the smallest useful learning session. Prefer fixing a weak explanation before adding more material.",
            "",
        ]
    )
    filename = filename or (
        f"{generated_at.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ').lower()}-"
        f"{period}-learning-agenda.md"
    )
    destination = safe_child(root, "_staging", "digests", filename)
    write_text(destination, "\n".join(lines), exclusive=True)
    append_text(
        safe_child(root, "log.md"),
        f"- {generated_timestamp} DIGEST_BUILD period={period}\n",
    )
    return {
        "path": destination.relative_to(root).as_posix(),
        "period": period,
        "counts": {
            "new_sources": sum(item["status"] == "new" for item in source_items),
            "staged_lessons": len(staged_lesson_items),
            "review_items": len(review_items),
        },
        "items": plan["items"],
        "next_action": plan["next_action"],
    }


def build_digest(
    vault: Path,
    *,
    period: str = "weekly",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build one agenda while respecting the workspace single-writer contract."""
    root = require_vault(vault)
    with workspace_lock(root):
        return _build_digest_unlocked(root, period=period, now=now)
