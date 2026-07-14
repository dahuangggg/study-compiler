from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .digest import _build_digest_unlocked, plan_digest
from .doctor import _inspect_vault_unlocked
from .errors import StateError, ValidationError
from .lessons import list_drafts
from .schemas import SOURCE_ID_PATTERN, validate_learning_record, validate_source_record
from .source_watch import (
    _sync_next_source_change_unlocked,
    project_current_watch_metadata,
    scan_source_changes,
)
from .state import workspace_lock
from .storage import (
    append_text,
    parse_utc_timestamp,
    read_json,
    require_vault,
    safe_child,
    secure_directory,
    state_directory,
    state_file,
    write_json,
)


RUN_ID_PATTERN = re.compile(r"[a-z0-9-]{1,128}")
MAX_FAILURE_MESSAGE = 2000
MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
RUN_STATUSES = {"active", "completed", "failed", "no-op"}
RUN_PERIODS = {"daily", "weekly", "monthly"}
REVIEW_CLUSTER_MODES = {"create", "integrate_staged", "synthesize"}
REVIEW_CLUSTER_HEADINGS = (
    "What Changed",
    "Knowledge Connections",
    "Integrated Example",
    "Explain It Back",
    "Member Rubric",
    "Staged Artifacts",
    "Next Learner Action",
)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValidationError("run timestamp must include a timezone")
    return (
        value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _now(value: datetime | None) -> datetime:
    moment = value or datetime.now(UTC)
    _timestamp(moment)
    return moment


def _valid_record_timestamp(value: Any) -> bool:
    try:
        parse_utc_timestamp(value)
    except ValueError:
        return False
    return True


def _valid_receipt(record: dict[str, Any], label: str) -> bool:
    digest = record.get(f"{label}_sha256")
    size = record.get(f"{label}_size")
    return (
        isinstance(digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
        and not isinstance(size, bool)
        and isinstance(size, int)
        and size >= 0
    )


def _validate_recorded_action(
    run_id: str,
    action: Any,
    precondition: Any,
) -> None:
    if not isinstance(action, dict):
        raise StateError(f"learning run has an invalid next_action: {run_id}")
    action_type = action.get("type")
    action_id = action.get("id")
    if not isinstance(action_type, str) or action_type not in {
        "source",
        "source_snapshot",
        "review",
        "staged_lesson",
    }:
        raise StateError(f"learning run has an invalid next_action type: {run_id}")
    if not isinstance(action_id, str) or not action_id:
        raise StateError(f"learning run has an invalid next_action ID: {run_id}")
    watched = action.get("watched")
    if watched is not None:
        path = action.get("path")
        change = action.get("change")
        previous_source_id = action.get("previous_source_id")
        if (
            watched is not True
            or action_type != "source"
            or change not in {"new", "modified"}
            or not isinstance(path, str)
            or not Path(path).is_absolute()
            or (
                previous_source_id is not None
                and (
                    not isinstance(previous_source_id, str)
                    or not SOURCE_ID_PATTERN.fullmatch(previous_source_id)
                )
            )
        ):
            raise StateError(
                f"learning run has invalid watched-source action: {run_id}"
            )
    if not isinstance(precondition, dict):
        raise StateError(f"learning run has an invalid precondition: {run_id}")
    if action_type in {"source", "source_snapshot"}:
        lesson_slugs = precondition.get("lesson_slugs")
        if not isinstance(lesson_slugs, list) or not all(
            isinstance(slug, str) for slug in lesson_slugs
        ):
            raise StateError(f"learning run has an invalid precondition: {run_id}")
    else:
        if "last_reviewed" not in precondition:
            raise StateError(f"learning run has an invalid precondition: {run_id}")
        last_reviewed = precondition.get("last_reviewed")
        if last_reviewed is not None and not _valid_record_timestamp(last_reviewed):
            raise StateError(f"learning run has an invalid precondition: {run_id}")
        review_count = precondition.get("review_count")
        if review_count is not None and (
            isinstance(review_count, bool)
            or not isinstance(review_count, int)
            or review_count < 0
        ):
            raise StateError(f"learning run has an invalid precondition: {run_id}")


def _validate_run_record(record: dict[str, Any], run_id: str) -> dict[str, Any]:
    if record.get("run_id") != run_id or not RUN_ID_PATTERN.fullmatch(run_id):
        raise StateError(f"learning run ID mismatch: {run_id}")
    status = record.get("status")
    if not isinstance(status, str) or status not in RUN_STATUSES:
        raise StateError(f"invalid learning run status: {run_id}")
    period = record.get("period")
    if not isinstance(period, str) or period not in RUN_PERIODS:
        raise StateError(f"learning run has an invalid period: {run_id}")
    if not _valid_record_timestamp(record.get("started_at")):
        raise StateError(f"learning run has an invalid started_at: {run_id}")
    started_at = parse_utc_timestamp(record["started_at"])

    if status == "no-op":
        if record.get("next_action") is not None:
            raise StateError(f"learning run has an invalid next_action: {run_id}")
        if not _valid_record_timestamp(record.get("completed_at")):
            raise StateError(f"learning run has an invalid completed_at: {run_id}")
        if parse_utc_timestamp(record["completed_at"]) < started_at:
            raise StateError(f"learning run completed before it started: {run_id}")
        return record

    _validate_recorded_action(
        run_id,
        record.get("next_action"),
        record.get("precondition"),
    )
    for field in ("agenda_path", "artifact_path"):
        if not isinstance(record.get(field), str) or not record[field]:
            raise StateError(f"learning run has an invalid {field}: {run_id}")
    expected_artifact = f"_staging/digests/{run_id}-learning-session.md"
    expected_agenda = f"_staging/digests/{run_id}-learning-agenda.md"
    if record["agenda_path"] != expected_agenda:
        raise StateError(f"learning run has an invalid agenda_path: {run_id}")
    if record["artifact_path"] != expected_artifact:
        raise StateError(f"learning run has an invalid artifact_path: {run_id}")
    if not _valid_receipt(record, "agenda"):
        raise StateError(f"learning run has an invalid agenda receipt: {run_id}")

    if status == "completed":
        if not _valid_record_timestamp(record.get("completed_at")):
            raise StateError(f"learning run has an invalid completed_at: {run_id}")
        if parse_utc_timestamp(record["completed_at"]) < started_at:
            raise StateError(f"learning run completed before it started: {run_id}")
        if record.get("artifact") != expected_artifact:
            raise StateError(f"learning run has an invalid artifact: {run_id}")
        if not _valid_receipt(record, "artifact"):
            raise StateError(f"learning run has an invalid artifact receipt: {run_id}")
        action = record.get("next_action")
        if isinstance(action, dict) and action.get("watched"):
            cluster = _validate_cluster_record(record.get("review_cluster"), run_id)
            if cluster["trigger_source_ids"] != [action.get("id")]:
                raise StateError(
                    f"learning run has an invalid review cluster: {run_id}"
                )
    elif status == "failed":
        if not _valid_record_timestamp(record.get("failed_at")):
            raise StateError(f"learning run has an invalid failed_at: {run_id}")
        if parse_utc_timestamp(record["failed_at"]) < started_at:
            raise StateError(f"learning run failed before it started: {run_id}")
        error = record.get("error")
        if (
            not isinstance(error, str)
            or not error
            or len(error) > MAX_FAILURE_MESSAGE
            or any(not character.isprintable() for character in error)
            or error != " ".join(error.split())
        ):
            raise StateError(f"learning run has an invalid error: {run_id}")
    return record


def _validate_slug_list(
    value: Any,
    *,
    label: str,
    maximum: int,
) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > maximum
        or value != list(dict.fromkeys(value))
        or not all(
            isinstance(slug, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug)
            for slug in value
        )
    ):
        raise ValidationError(f"review cluster has invalid {label}")
    return value


def _validate_cluster_record(cluster: Any, run_id: str) -> dict[str, Any]:
    if not isinstance(cluster, dict):
        raise StateError(f"learning run is missing a review cluster: {run_id}")
    if cluster.get("artifact_kind") != "review_cluster":
        raise StateError(f"learning run has an invalid review cluster: {run_id}")
    if cluster.get("integration_mode") not in REVIEW_CLUSTER_MODES:
        raise StateError(f"learning run has an invalid review cluster: {run_id}")
    try:
        staged = _validate_slug_list(
            cluster.get("staged_slugs"), label="staged_slugs", maximum=3
        )
        members = _validate_slug_list(
            cluster.get("member_slugs"), label="member_slugs", maximum=5
        )
    except ValidationError as error:
        raise StateError(
            f"learning run has an invalid review cluster: {run_id}"
        ) from error
    triggers = cluster.get("trigger_source_ids")
    if (
        not isinstance(triggers, list)
        or len(triggers) != 1
        or not isinstance(triggers[0], str)
        or not SOURCE_ID_PATTERN.fullmatch(triggers[0])
        or not set(staged).issubset(members)
    ):
        raise StateError(f"learning run has an invalid review cluster: {run_id}")
    return cluster


def _runs_directory(root: Path) -> Path:
    directory = state_directory(root) / "runs"
    return secure_directory(directory)


def _run_path(root: Path, run_id: str) -> Path:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValidationError("invalid run ID")
    return safe_child(_runs_directory(root), f"{run_id}.json")


def _load_run(root: Path, run_id: str) -> dict[str, Any]:
    path = _run_path(root, run_id)
    if not path.is_file():
        raise StateError(f"learning run not found: {run_id}")
    record = read_json(path)
    return _validate_run_record(record, run_id)


def _records(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(_runs_directory(root).glob("*.json")):
        record = read_json(path)
        records.append(_validate_run_record(record, path.stem))
    return records


def _active_run(root: Path) -> dict[str, Any] | None:
    active = [record for record in _records(root) if record.get("status") == "active"]
    if len(active) > 1:
        raise StateError("workspace contains multiple active learning runs")
    return active[0] if active else None


def _planner_inputs(
    root: Path,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    manifest = read_json(state_file(root, "manifest.json"))
    learning_state = read_json(state_file(root, "learning-state.json"))
    sources = manifest.get("sources")
    lessons = learning_state.get("lessons")
    if not isinstance(sources, dict) or not isinstance(lessons, dict):
        raise StateError("workspace state has an unsupported schema")
    sources = project_current_watch_metadata(root, sources)
    for source_id, source in sources.items():
        validate_source_record(source_id, source)
    for slug, lesson in lessons.items():
        validate_learning_record(slug, lesson)
    return sources, lessons, list_drafts(root)


def _new_run_id(moment: datetime) -> str:
    prefix = moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ").lower()
    return f"{prefix}-{uuid4().hex[:12]}"


def _append_log(root: Path, timestamp: str, action: str, run_id: str) -> None:
    append_text(
        safe_child(root, "log.md"),
        f"- {timestamp} {action} run_id={run_id}\n",
    )


def _file_receipt(path: Path) -> dict[str, Any]:
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise StateError(f"cannot read managed file: {path.name}") from error
    return {"sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}


def _read_bounded_artifact(path: Path) -> bytes:
    chunks: list[bytes] = []
    size = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_ARTIFACT_BYTES:
                    raise ValidationError(
                        f"run artifact exceeds {MAX_ARTIFACT_BYTES} byte limit"
                    )
                chunks.append(chunk)
    except OSError as error:
        raise ValidationError("run artifact must be readable Markdown") from error
    return b"".join(chunks)


def _artifact_binding(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---\n"):
        raise ValidationError("run artifact must begin with binding frontmatter")
    try:
        frontmatter, body = content[4:].split("\n---\n", 1)
    except ValueError as error:
        raise ValidationError(
            "run artifact binding frontmatter is not terminated"
        ) from error
    binding: dict[str, Any] = {}
    for line in frontmatter.splitlines():
        if ": " not in line:
            raise ValidationError("run artifact has invalid binding frontmatter")
        key, raw_value = line.split(": ", 1)
        if key in binding:
            raise ValidationError("run artifact has duplicate binding frontmatter")
        try:
            binding[key] = json.loads(raw_value)
        except json.JSONDecodeError as error:
            raise ValidationError(
                "run artifact has invalid binding frontmatter"
            ) from error
    if not body.strip():
        raise ValidationError("run artifact must contain a human-readable result")
    return binding, body


def _review_count(root: Path, slug: str) -> int:
    path = safe_child(root, "reviews", f"{slug}.jsonl")
    if not path.exists():
        return 0
    if not path.is_file():
        raise StateError(f"review path is not a file: {slug}")
    count = 0
    try:
        with path.open("rb") as handle:
            for _ in handle:
                count += 1
    except OSError as error:
        raise StateError(f"cannot read review history: {slug}") from error
    return count


def _action_precondition(
    root: Path,
    action: dict[str, Any],
    sources: dict[str, Any],
    lessons: dict[str, Any],
) -> dict[str, Any]:
    action_type = action.get("type")
    action_id = action.get("id")
    if not isinstance(action_id, str):
        raise StateError("planner returned an invalid action ID")
    if action_type in {"source", "source_snapshot"}:
        source = sources.get(action_id)
        if not isinstance(source, dict):
            raise StateError(f"planner returned an unknown source action: {action_id}")
        lesson_slugs = source.get("lesson_slugs", [])
        if not isinstance(lesson_slugs, list) or not all(
            isinstance(slug, str) for slug in lesson_slugs
        ):
            raise StateError(f"source {action_id} has invalid lesson links")
        return {
            "snapshot": source.get("snapshot"),
            "coverage_status": source.get("coverage_status"),
            "lesson_slugs": sorted(lesson_slugs),
        }
    if action_type in {"review", "staged_lesson"}:
        lesson = lessons.get(action_id)
        if not isinstance(lesson, dict):
            raise StateError(f"planner returned an unknown lesson action: {action_id}")
        return {
            "last_reviewed": lesson.get("last_reviewed"),
            "review_count": _review_count(root, action_id),
        }
    raise StateError(f"planner returned an unsupported action type: {action_type}")


def _validate_action_completion(
    root: Path,
    record: dict[str, Any],
    *,
    timestamp: str,
    binding: dict[str, Any],
    artifact_body: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    action = record.get("next_action")
    precondition = record.get("precondition")
    if not isinstance(action, dict) or not isinstance(precondition, dict):
        raise StateError("learning run action receipt is invalid")
    action_type = action.get("type")
    action_id = action.get("id")
    manifest = read_json(state_file(root, "manifest.json"))
    learning_state = read_json(state_file(root, "learning-state.json"))
    sources = manifest.get("sources", {})
    lessons = learning_state.get("lessons", {})
    if action_type == "source_snapshot":
        source = sources.get(action_id)
        if (
            not isinstance(source, dict)
            or source.get("snapshot") is None
            or source.get("snapshot") == precondition.get("snapshot")
        ):
            raise StateError(
                f"snapshot action was not completed for source: {action_id}"
            )
        return None, None
    if action_type == "source":
        source = sources.get(action_id)
        before = set(precondition.get("lesson_slugs", []))
        after = (
            set(source.get("lesson_slugs", [])) if isinstance(source, dict) else set()
        )
        if not after - before:
            raise StateError(f"source action did not create a lesson: {action_id}")
        if not source.get("watch_paths"):
            return None, None
        cluster = _review_cluster_from_artifact(
            root,
            action_id=str(action_id),
            binding=binding,
            body=artifact_body,
            learning_state=learning_state,
            timestamp=timestamp,
        )
        return learning_state, cluster
    if action_type in {"review", "staged_lesson"}:
        lesson = lessons.get(action_id)
        if not isinstance(lesson, dict):
            raise StateError(f"lesson action no longer exists: {action_id}")
        review_count = precondition.get("review_count")
        if isinstance(review_count, int) and not isinstance(review_count, bool):
            learner_responded = _review_count(root, str(action_id)) > review_count
        else:
            learner_responded = lesson.get("last_reviewed") != precondition.get(
                "last_reviewed"
            )
        if learner_responded:
            return None, None
        lesson["awaiting_response"] = True
        lesson["prompted_at"] = timestamp
        return learning_state, None
    raise StateError(f"unsupported learning run action: {action_type}")


def _require_artifact_sections(body: str) -> None:
    for heading in REVIEW_CLUSTER_HEADINGS:
        match = re.search(
            rf"(?ms)^## {re.escape(heading)}\s*$\n+(.+?)(?=^## |\Z)",
            body,
        )
        if match is None or not match.group(1).strip():
            raise ValidationError(
                f"review cluster is missing a non-empty section: {heading}"
            )


def _review_cluster_from_artifact(
    root: Path,
    *,
    action_id: str,
    binding: dict[str, Any],
    body: str,
    learning_state: dict[str, Any],
    timestamp: str,
) -> dict[str, Any]:
    if binding.get("artifact_kind") != "review_cluster":
        raise ValidationError("watched source result must include a review cluster")
    integration_mode = binding.get("integration_mode")
    if integration_mode not in REVIEW_CLUSTER_MODES:
        raise ValidationError("review cluster has an invalid integration_mode")
    trigger_source_ids = binding.get("trigger_source_ids")
    if trigger_source_ids != [action_id]:
        raise ValidationError("review cluster must bind the watched source action")
    staged_slugs = _validate_slug_list(
        binding.get("staged_slugs"), label="staged_slugs", maximum=3
    )
    member_slugs = _validate_slug_list(
        binding.get("member_slugs"), label="member_slugs", maximum=5
    )
    if not set(staged_slugs).issubset(member_slugs):
        raise ValidationError("review cluster members must include all staged lessons")
    _require_artifact_sections(body)

    lessons = learning_state.get("lessons")
    if not isinstance(lessons, dict):
        raise StateError("learning-state.json has an unsupported schema")
    for slug in member_slugs:
        lesson = lessons.get(slug)
        if not isinstance(lesson, dict):
            raise ValidationError(f"review cluster cites an unknown lesson: {slug}")
        if lesson.get("awaiting_response", False):
            raise StateError(f"review cluster lesson already awaits a response: {slug}")
    for slug in staged_slugs:
        lesson = lessons[slug]
        staged = safe_child(root, "_staging", "lessons", f"{slug}.md")
        if not staged.is_file() or lesson.get("status") != "draft":
            raise ValidationError(f"review cluster result is not staged: {slug}")
        if action_id not in lesson.get("source_ids", []):
            raise ValidationError(
                f"review cluster staged lesson does not cite the watched source: {slug}"
            )
    for slug in member_slugs:
        lessons[slug]["awaiting_response"] = True
        lessons[slug]["prompted_at"] = timestamp
    return {
        "artifact_kind": "review_cluster",
        "integration_mode": integration_mode,
        "trigger_source_ids": trigger_source_ids,
        "staged_slugs": staged_slugs,
        "member_slugs": member_slugs,
    }


def start_run(
    vault: Path,
    period: str = "daily",
    now: datetime | None = None,
) -> dict[str, Any]:
    root = require_vault(vault)
    moment = _now(now)
    timestamp = _timestamp(moment)
    with workspace_lock(root):
        active = _active_run(root)
        if active is not None:
            raise StateError(f"active learning run already exists: {active['run_id']}")

        _sync_next_source_change_unlocked(root)
        sources, lessons, staged_lessons = _planner_inputs(root)
        plan = plan_digest(
            sources=sources,
            lessons=lessons,
            staged_lessons=staged_lessons,
            period=period,
            now=moment,
        )
        run_id = _new_run_id(moment)
        if not plan["items"]:
            record = {
                "run_id": run_id,
                "status": "no-op",
                "period": period,
                "started_at": timestamp,
                "completed_at": timestamp,
                "next_action": None,
            }
            write_json(_run_path(root, run_id), record)
            _append_log(root, timestamp, "RUN_NO_OP", run_id)
            return record

        agenda = _build_digest_unlocked(
            root,
            period=period,
            now=moment,
            filename=f"{run_id}-learning-agenda.md",
        )
        agenda_path = safe_child(root, agenda["path"])
        agenda_receipt = _file_receipt(agenda_path)
        artifact_path = f"_staging/digests/{run_id}-learning-session.md"
        next_action = agenda.get("next_action", plan["next_action"])
        record = {
            "run_id": run_id,
            "status": "active",
            "period": period,
            "started_at": timestamp,
            "agenda_path": agenda["path"],
            "agenda_sha256": agenda_receipt["sha256"],
            "agenda_size": agenda_receipt["size"],
            "artifact_path": artifact_path,
            "next_action": next_action,
            "precondition": _action_precondition(root, next_action, sources, lessons),
        }
        write_json(_run_path(root, run_id), record)
        _append_log(root, timestamp, "RUN_START", run_id)
        return {
            "run_id": run_id,
            "status": "started",
            "period": period,
            "agenda_path": record["agenda_path"],
            "artifact_path": artifact_path,
            "next_action": record["next_action"],
        }


def _require_active_run(root: Path, run_id: str) -> dict[str, Any]:
    record = _load_run(root, run_id)
    if record.get("status") != "active":
        raise StateError(f"learning run is not active: {run_id}")
    return record


def resume_run(vault: Path, run_id: str) -> dict[str, Any]:
    """Return the existing active run so a later Agent turn can continue it."""
    root = require_vault(vault)
    with workspace_lock(root):
        record = _require_active_run(root, run_id)
        agenda = safe_child(root, str(record.get("agenda_path", "")))
        if not agenda.is_file() or _file_receipt(agenda) != {
            "sha256": record.get("agenda_sha256"),
            "size": record.get("agenda_size"),
        }:
            raise StateError(f"learning run agenda is missing or changed: {run_id}")
        return {
            "run_id": run_id,
            "status": "resumed",
            "period": record["period"],
            "agenda_path": record["agenda_path"],
            "artifact_path": record["artifact_path"],
            "next_action": record["next_action"],
        }


def _staged_artifact(
    root: Path,
    run_id: str,
    record: dict[str, Any],
    artifact: Path,
) -> tuple[Path, str, dict[str, Any], dict[str, Any], str]:
    candidate = artifact.expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_symlink():
        raise ValidationError("run artifact must not be a symlink")
    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as error:
        raise ValidationError("run artifact must stay inside the workspace") from error
    if not relative.parts or relative.parts[0] != "_staging":
        raise ValidationError("run artifact must be inside _staging")
    if not resolved.is_file():
        raise ValidationError(f"run artifact does not exist: {relative.as_posix()}")
    if resolved.suffix.lower() != ".md":
        raise ValidationError("run artifact must be a Markdown file")
    payload = _read_bounded_artifact(resolved)
    try:
        content = payload.decode("utf-8")
    except UnicodeError as error:
        raise ValidationError("run artifact must be readable UTF-8 Markdown") from error
    if not content.strip():
        raise ValidationError("run artifact must not be empty")
    artifact_path = relative.as_posix()
    if artifact_path == record.get("agenda_path"):
        raise ValidationError("run artifact must not be the deterministic agenda")
    if artifact_path != record.get("artifact_path"):
        raise ValidationError(
            f"run artifact must use the run-specific path: {record.get('artifact_path')}"
        )
    action = record.get("next_action")
    if not isinstance(action, dict):
        raise StateError(f"learning run has an invalid next_action: {run_id}")
    binding, body = _artifact_binding(content)
    expected_binding = {
        "run_id": run_id,
        "action_type": action.get("type"),
        "action_id": action.get("id"),
    }
    if any(binding.get(key) != value for key, value in expected_binding.items()):
        raise ValidationError(
            "run artifact must identify its run_id, action_type, and action_id"
        )
    resolved.chmod(0o600)
    return (
        resolved,
        artifact_path,
        {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        },
        binding,
        body,
    )


def finish_run(
    vault: Path,
    run_id: str,
    artifact: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = require_vault(vault)
    moment = _now(now)
    timestamp = _timestamp(moment)
    with workspace_lock(root):
        record = _require_active_run(root, run_id)
        if moment.astimezone(UTC) < parse_utc_timestamp(record["started_at"]):
            raise ValidationError("run completion cannot precede run start")
        _, artifact_path, receipt, binding, artifact_body = _staged_artifact(
            root, run_id, record, artifact
        )
        learning_state_update, review_cluster = _validate_action_completion(
            root,
            record,
            timestamp=timestamp,
            binding=binding,
            artifact_body=artifact_body,
        )
        doctor = _inspect_vault_unlocked(root)
        if not doctor["ok"]:
            detail = "; ".join(doctor["errors"])
            raise StateError(f"cannot finish learning run; doctor failed: {detail}")
        if learning_state_update is not None:
            write_json(state_file(root, "learning-state.json"), learning_state_update)
        record.update(
            {
                "status": "completed",
                "completed_at": timestamp,
                "artifact": artifact_path,
                "artifact_sha256": receipt["sha256"],
                "artifact_size": receipt["size"],
            }
        )
        if review_cluster is not None:
            record["review_cluster"] = review_cluster
        write_json(_run_path(root, run_id), record)
        _append_log(root, timestamp, "RUN_FINISH", run_id)
        return record


def fail_run(
    vault: Path,
    run_id: str,
    message: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = require_vault(vault)
    moment = _now(now)
    timestamp = _timestamp(moment)
    normalized = " ".join(message.split())
    if not normalized:
        raise ValidationError("run failure message must not be empty")
    if len(normalized) > MAX_FAILURE_MESSAGE:
        raise ValidationError("run failure message is too long")
    if any(not character.isprintable() for character in normalized):
        raise ValidationError("run failure message contains control characters")
    with workspace_lock(root):
        record = _require_active_run(root, run_id)
        if moment.astimezone(UTC) < parse_utc_timestamp(record["started_at"]):
            raise ValidationError("run failure cannot precede run start")
        record.update(
            {
                "status": "failed",
                "failed_at": timestamp,
                "error": normalized,
            }
        )
        write_json(_run_path(root, run_id), record)
        _append_log(root, timestamp, "RUN_FAIL", run_id)
        return record


def workspace_status(vault: Path, now: datetime | None = None) -> dict[str, Any]:
    root = require_vault(vault)
    current = _now(now).astimezone(UTC)
    with workspace_lock(root):
        sources, lessons, staged_lessons = _planner_inputs(root)
        source_changes = scan_source_changes(root)
        records = _records(root)
        active = [record for record in records if record.get("status") == "active"]
        if len(active) > 1:
            raise StateError("workspace contains multiple active learning runs")
        failures = [record for record in records if record.get("status") == "failed"]
        failures.sort(key=lambda record: str(record.get("failed_at", "")))
        pending_sources = sum(
            1
            for source in sources.values()
            if source.get("snapshot")
            and source.get(
                "coverage_status",
                "pending" if source.get("status") == "new" else "partial",
            )
            != "complete"
        )
        unsnapshotted_urls = sum(
            1
            for source in sources.values()
            if source.get("kind") == "web" and source.get("snapshot") is None
        )
        awaiting_learner = sum(
            1 for lesson in lessons.values() if lesson.get("awaiting_response", False)
        )
        actionable_staged_lessons = sum(
            1
            for lesson in staged_lessons
            if not lessons.get(lesson["slug"], {}).get("awaiting_response", False)
        )
        due_reviews = sum(
            1
            for lesson in lessons.values()
            if isinstance(lesson, dict)
            and not lesson.get("awaiting_response", False)
            and isinstance(lesson.get("next_review"), str)
            and parse_utc_timestamp(lesson["next_review"]) <= current
        )
        staged_link_patches = len(list((root / "_staging/link-patches").glob("*.json")))
        return {
            "workspace": str(root),
            "pending_sources": pending_sources,
            "unsnapshotted_urls": unsnapshotted_urls,
            "staged_lessons": actionable_staged_lessons,
            "awaiting_learner": awaiting_learner,
            "due_reviews": due_reviews,
            "staged_link_patches": staged_link_patches,
            "source_changes": source_changes,
            "active_run": active[0] if active else None,
            "last_failure": failures[-1] if failures else None,
        }
