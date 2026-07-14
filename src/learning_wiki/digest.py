from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import StateError, ValidationError
from .lessons import list_drafts
from .storage import read_json, require_vault

PERIODS = {"daily", "weekly", "monthly"}


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValidationError("digest timestamp must include a timezone")
    return (
        value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _load_state(vault: Path, name: str, key: str) -> dict[str, Any]:
    state = read_json(vault / ".learning-wiki" / name)
    if not isinstance(state.get(key), dict):
        raise StateError(f"{name} has an unsupported schema")
    return state


def build_digest(
    vault: Path,
    *,
    period: str = "weekly",
    now: datetime | None = None,
) -> dict[str, Any]:
    root = require_vault(vault)
    if period not in PERIODS:
        raise ValidationError(f"unsupported digest period: {period}")
    generated_at = now or datetime.now(UTC)
    generated_timestamp = _timestamp(generated_at)
    manifest = _load_state(root, "manifest.json", "sources")
    learning_state = _load_state(root, "learning-state.json", "lessons")

    new_sources = sorted(
        (
            source
            for source in manifest["sources"].values()
            if source.get("status") == "new"
        ),
        key=lambda source: source["id"],
    )
    staged_lessons = list_drafts(root)
    review_items: list[dict[str, Any]] = []
    for slug, lesson in sorted(learning_state["lessons"].items()):
        score = lesson.get("mastery_score")
        next_review = lesson.get("next_review")
        due = isinstance(next_review, str) and next_review <= generated_timestamp
        weak = isinstance(score, int) and score < 4
        if due or weak:
            review_items.append(
                {
                    "slug": slug,
                    "score": score,
                    "due": due,
                    "next_review": next_review,
                }
            )

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
        "## New Sources",
        "",
    ]
    if new_sources:
        lines.extend(
            f"- `{source['id']}` — {source['title']} ({source['kind']})"
            for source in new_sources
        )
    else:
        lines.append("- None")
    lines.extend(["", "## Staged Lessons", ""])
    if staged_lessons:
        lines.extend(
            f"- `{lesson['slug']}` — {lesson['title']}" for lesson in staged_lessons
        )
    else:
        lines.append("- None")
    lines.extend(["", "## Review Due", ""])
    if review_items:
        for item in review_items:
            score = (
                f"score {item['score']}" if item["score"] is not None else "not scored"
            )
            reason = "due" if item["due"] else "weak recall"
            lines.append(f"- `{item['slug']}` — {score}; {reason}")
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
    filename = (
        f"{generated_at.astimezone(UTC).date().isoformat()}-{period}-learning-agenda.md"
    )
    destination = root / "_staging/digests" / filename
    destination.write_text("\n".join(lines), encoding="utf-8")
    with (root / "log.md").open("a", encoding="utf-8") as log:
        log.write(f"- {generated_timestamp} DIGEST_BUILD period={period}\n")
    return {
        "path": destination.relative_to(root).as_posix(),
        "period": period,
        "counts": {
            "new_sources": len(new_sources),
            "staged_lessons": len(staged_lessons),
            "review_items": len(review_items),
        },
    }
