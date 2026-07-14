from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .errors import StateError, StudyCompilerError, ValidationError
from .graph import _graph_report_unlocked
from .lessons import LESSON_FORMAT_VERSION, parse_lesson, render_index
from .links import inspect_link_patches_unlocked
from .schemas import validate_learning_record, validate_source_record
from .source_watch import validate_source_watch
from .state import workspace_lock
from .storage import (
    read_json,
    parse_utc_timestamp,
    require_vault,
    safe_child,
    state_directory,
    state_file,
)


RUN_ID_PATTERN = re.compile(r"[a-z0-9-]{1,128}")
RUN_STATUSES = {"active", "completed", "failed", "no-op"}
RUN_PERIODS = {"daily", "weekly", "monthly"}


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise StateError(f"cannot read managed file: {path.name}") from error
    return digest.hexdigest()


def _read_state(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        return read_json(path)
    except StudyCompilerError as error:
        errors.append(str(error))
        return None


def _check_source(
    root: Path,
    source_id: str,
    source: Any,
    errors: list[str],
    warnings: list[str],
) -> None:
    try:
        validate_source_record(source_id, source)
    except StudyCompilerError as error:
        errors.append(str(error))
        return
    raw_source_directory = root / "sources" / source_id
    if raw_source_directory.is_symlink():
        errors.append(f"source {source_id} directory must not be a symlink")
        return
    raw_metadata_path = raw_source_directory / "metadata.json"
    if raw_metadata_path.is_symlink():
        errors.append(f"source {source_id} metadata must not be a symlink")
        return
    try:
        metadata_path = safe_child(root, "sources", source_id, "metadata.json")
    except ValidationError:
        errors.append(f"source {source_id} directory escapes the workspace")
        return
    except StudyCompilerError as error:
        errors.append(f"source {source_id} has an invalid metadata path: {error}")
        return
    if not metadata_path.is_file():
        errors.append(f"source {source_id} is missing metadata.json")
    else:
        metadata = _read_state(metadata_path, errors)
        if metadata is not None and metadata.get("id") != source_id:
            errors.append(f"source metadata ID mismatch: {source_id}")
        if metadata is not None and metadata != source:
            legacy_source = dict(source)
            legacy_metadata = dict(metadata)
            both_legacy = (
                "coverage_status" not in legacy_source
                and "coverage_status" not in legacy_metadata
            )
            for field in ("status", "lesson_slugs"):
                legacy_source.pop(field, None)
                legacy_metadata.pop(field, None)
            if both_legacy and legacy_source == legacy_metadata:
                warnings.append(
                    f"source {source_id} has legacy mutable metadata drift; "
                    "the next source update will reconcile it"
                )
            else:
                errors.append(f"source {source_id} metadata drift from manifest")

    source_status = source.get("status")
    if not isinstance(source_status, str) or source_status not in {
        "new",
        "staged",
        "integrated",
    }:
        errors.append(f"source {source_id} has an invalid status")
    coverage_status = source.get("coverage_status", "pending")
    if not isinstance(coverage_status, str) or coverage_status not in {
        "pending",
        "partial",
        "complete",
    }:
        errors.append(f"source {source_id} has an invalid coverage_status")

    snapshot = source.get("snapshot")
    if snapshot is None:
        if source.get("kind") != "web":
            errors.append(f"source {source_id} is missing a snapshot")
        return
    if not isinstance(snapshot, str):
        errors.append(f"source {source_id} has an invalid snapshot path")
        return
    try:
        raw_snapshot_path = root / snapshot
        if raw_snapshot_path.is_symlink():
            errors.append(f"source {source_id} snapshot must not be a symlink")
            return
        snapshot_path = safe_child(root, snapshot)
    except ValidationError:
        errors.append(f"source {source_id} snapshot escapes the workspace")
        return
    except StudyCompilerError as error:
        errors.append(f"source {source_id} has an invalid snapshot path: {error}")
        return
    expected_source_directory = safe_child(root, "sources", source_id)
    if snapshot_path.parent != expected_source_directory:
        errors.append(f"source {source_id} snapshot is outside its source directory")
        return
    if not snapshot_path.is_file():
        errors.append(f"source {source_id} snapshot is missing: {snapshot}")
        return
    expected_hash = source.get("sha256")
    try:
        actual_hash = _hash_file(snapshot_path)
    except StateError as error:
        errors.append(f"source {source_id} snapshot is unreadable: {error}")
        return
    if not isinstance(expected_hash, str) or actual_hash != expected_hash:
        errors.append(f"source {source_id} snapshot hash drift")
    if snapshot_path.stat().st_mode & 0o222:
        warnings.append(f"source {source_id} snapshot is writable")


def _check_lesson(
    path: Path,
    expected_status: str,
    source_ids: set[str],
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any] | None:
    if path.is_symlink():
        errors.append(f"invalid lesson {path.name}: symlinks are not allowed")
        return None
    try:
        metadata, _ = parse_lesson(path.read_text(encoding="utf-8"))
    except (StudyCompilerError, OSError, UnicodeError, TypeError) as error:
        errors.append(f"invalid lesson {path.name}: {error}")
        return None
    if metadata.get("slug") != path.stem:
        errors.append(
            f"lesson {path.name} slug does not match its file name: "
            f"{metadata.get('slug')}"
        )
    if metadata.get("status") != expected_status:
        errors.append(
            f"lesson {path.name} has status {metadata.get('status')}, expected {expected_status}"
        )
    if metadata.get("format_version", 1) < LESSON_FORMAT_VERSION:
        warnings.append(
            f"lesson {path.name} uses legacy format; revise it before promotion"
        )
    if expected_status == "draft" and metadata.get("relationships"):
        errors.append(
            f"lesson {path.name} has relationships that require a separate link patch"
        )
    for source_id in metadata["source_ids"]:
        if source_id not in source_ids:
            errors.append(f"lesson {path.name} cites unknown source ID: {source_id}")
    return metadata


def _check_learning_state(
    learning_state: dict[str, Any],
    lesson_files: dict[str, tuple[str, dict[str, Any]]],
    known_source_ids: set[str],
    errors: list[str],
) -> None:
    lessons = learning_state["lessons"]
    for slug, lesson in lessons.items():
        label = f"learning-state lesson {slug}"
        try:
            validate_learning_record(slug, lesson)
        except StateError as error:
            errors.append(str(error))
        if not isinstance(slug, str) or not isinstance(lesson, dict):
            errors.append(f"{label} must be an object")
            continue

        status = lesson.get("status")
        valid_status = isinstance(status, str) and status in {"draft", "approved"}
        if not valid_status:
            errors.append(f"{label} has an invalid status")

        source_ids = lesson.get("source_ids")
        valid_source_ids = (
            isinstance(source_ids, list)
            and bool(source_ids)
            and all(
                isinstance(source_id, str) and bool(source_id)
                for source_id in source_ids
            )
        )
        if not valid_source_ids:
            errors.append(f"{label} has invalid source_ids")
        else:
            for source_id in source_ids:
                if source_id not in known_source_ids:
                    errors.append(f"{label} cites unknown source ID: {source_id}")

        mastery_score = lesson.get("mastery_score")
        valid_mastery_score = mastery_score is None or (
            not isinstance(mastery_score, bool)
            and isinstance(mastery_score, int)
            and mastery_score in range(1, 6)
        )
        if not valid_mastery_score:
            errors.append(f"{label} has invalid mastery_score")
        can_explain = lesson.get("can_explain")
        if not isinstance(can_explain, bool):
            errors.append(f"{label} has invalid can_explain")
        elif valid_mastery_score and can_explain != (
            isinstance(mastery_score, int)
            and not isinstance(mastery_score, bool)
            and mastery_score >= 4
        ):
            errors.append(f"{label} can_explain does not match mastery_score")

        for field in ("last_reviewed", "next_review"):
            value = lesson.get(field)
            if value is not None and not _valid_timestamp(value):
                errors.append(f"{label} has invalid {field}")
        if (lesson.get("last_reviewed") is None) != (lesson.get("next_review") is None):
            errors.append(f"{label} has an incomplete review schedule")
        last_reviewed_value = lesson.get("last_reviewed")
        next_review_value = lesson.get("next_review")
        if _valid_timestamp(last_reviewed_value) and _valid_timestamp(
            next_review_value
        ):
            last_reviewed = parse_utc_timestamp(last_reviewed_value)
            next_review = parse_utc_timestamp(next_review_value)
            if next_review < last_reviewed:
                errors.append(f"{label} next_review precedes last_reviewed")

        approved_at = lesson.get("approved_at")
        if approved_at is not None and not _valid_timestamp(approved_at):
            errors.append(f"{label} has invalid approved_at")
        awaiting_response = lesson.get("awaiting_response", False)
        if not isinstance(awaiting_response, bool):
            errors.append(f"{label} has invalid awaiting_response")
        prompted_at = lesson.get("prompted_at")
        if awaiting_response is True and not _valid_timestamp(prompted_at):
            errors.append(f"{label} has invalid prompted_at")
        elif awaiting_response is False and prompted_at is not None:
            errors.append(f"{label} has prompted_at while not awaiting a response")

        file_record = lesson_files.get(slug)
        if file_record is None:
            errors.append(f"ghost lesson in learning-state has no file: {slug}")
            continue
        file_status, metadata = file_record
        if valid_status and status != file_status:
            errors.append(
                f"{label} status {status} does not match {file_status} lesson file"
            )
        if valid_source_ids and source_ids != metadata["source_ids"]:
            errors.append(f"{label} source_ids do not match lesson file")

    for slug in sorted(lesson_files.keys() - lessons.keys()):
        errors.append(f"lesson file has no learning-state entry: {slug}")


def _valid_timestamp(value: Any) -> bool:
    try:
        parse_utc_timestamp(value)
    except ValueError:
        return False
    return True


def _check_run_path(
    root: Path,
    run_id: str,
    field: str,
    value: Any,
    errors: list[str],
    *,
    prefix: tuple[str, ...],
    require_file: bool,
) -> Path | None:
    label = f"learning run {run_id} {field}"
    if not isinstance(value, str) or not value:
        errors.append(f"{label} is invalid")
        return None
    try:
        raw_path = root / str(value)
        if raw_path.is_symlink():
            errors.append(f"{label} must not be a symlink")
            return None
        resolved = safe_child(root, value)
        relative = resolved.relative_to(root)
    except ValidationError:
        errors.append(f"{label} escapes the workspace")
        return None
    except StudyCompilerError as error:
        errors.append(f"{label} is invalid: {error}")
        return None
    if relative.parts[: len(prefix)] != prefix or resolved.suffix.lower() != ".md":
        errors.append(f"{label} is outside {'/'.join(prefix)}")
        return None
    if require_file and not resolved.is_file():
        errors.append(f"{label} is missing: {value}")
        return None
    return resolved


def _check_run_receipt(
    run_id: str,
    label: str,
    path: Path | None,
    record: dict[str, Any],
    errors: list[str],
) -> None:
    expected_hash = record.get(f"{label}_sha256")
    expected_size = record.get(f"{label}_size")
    if (
        not isinstance(expected_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size < 0
    ):
        errors.append(f"learning run {run_id} has an invalid {label} receipt")
        return
    if path is not None and path.is_file():
        try:
            actual_size = path.stat().st_size
            actual_hash = _hash_file(path)
        except (OSError, StateError) as error:
            errors.append(f"learning run {run_id} {label} is unreadable: {error}")
            return
        if actual_size != expected_size or actual_hash != expected_hash:
            errors.append(f"learning run {run_id} {label} receipt drift")


def _check_run_action(
    run_id: str,
    action: Any,
    precondition: Any,
    errors: list[str],
) -> None:
    if not isinstance(action, dict):
        errors.append(f"learning run {run_id} has an invalid next_action")
        return
    action_type = action.get("type")
    action_id = action.get("id")
    if not isinstance(action_type, str) or action_type not in {
        "source",
        "source_snapshot",
        "review",
        "staged_lesson",
    }:
        errors.append(f"learning run {run_id} has an invalid next_action type")
        return
    if not isinstance(action_id, str) or not action_id:
        errors.append(f"learning run {run_id} has an invalid next_action ID")
    watched = action.get("watched")
    if watched is not None:
        previous_source_id = action.get("previous_source_id")
        if (
            watched is not True
            or action_type != "source"
            or action.get("change") not in {"new", "modified"}
            or not isinstance(action.get("path"), str)
            or not Path(action["path"]).is_absolute()
            or (
                previous_source_id is not None
                and (
                    not isinstance(previous_source_id, str)
                    or not re.fullmatch(
                        r"(?:paper|document|chat|web)-[0-9a-f]{16}",
                        previous_source_id,
                    )
                )
            )
        ):
            errors.append(f"learning run {run_id} has invalid watched-source action")
    if not isinstance(precondition, dict):
        errors.append(f"learning run {run_id} has an invalid precondition")
        return
    if action_type in {"source", "source_snapshot"}:
        lesson_slugs = precondition.get("lesson_slugs")
        if not isinstance(lesson_slugs, list) or not all(
            isinstance(slug, str) for slug in lesson_slugs
        ):
            errors.append(f"learning run {run_id} has an invalid precondition")
    elif action_type in {"review", "staged_lesson"}:
        if "last_reviewed" not in precondition:
            errors.append(f"learning run {run_id} has an invalid precondition")
        last_reviewed = precondition.get("last_reviewed")
        if last_reviewed is not None and not _valid_timestamp(last_reviewed):
            errors.append(f"learning run {run_id} has an invalid precondition")
        review_count = precondition.get("review_count")
        if review_count is not None and (
            isinstance(review_count, bool)
            or not isinstance(review_count, int)
            or review_count < 0
        ):
            errors.append(f"learning run {run_id} has an invalid precondition")


def _valid_run_slug_list(value: Any, maximum: int) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and len(value) <= maximum
        and value == list(dict.fromkeys(value))
        and all(
            isinstance(slug, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug)
            for slug in value
        )
    )


def _check_completed_review_cluster(
    run_id: str,
    record: dict[str, Any],
    action: dict[str, Any],
    learning_lessons: dict[str, Any],
    lesson_files: dict[str, tuple[str, dict[str, Any]]],
    errors: list[str],
) -> list[str]:
    cluster = record.get("review_cluster")
    staged = cluster.get("staged_slugs") if isinstance(cluster, dict) else None
    members = cluster.get("member_slugs") if isinstance(cluster, dict) else None
    triggers = cluster.get("trigger_source_ids") if isinstance(cluster, dict) else None
    if (
        not isinstance(cluster, dict)
        or cluster.get("artifact_kind") != "review_cluster"
        or cluster.get("integration_mode")
        not in {"create", "integrate_staged", "synthesize"}
        or triggers != [action.get("id")]
        or not _valid_run_slug_list(staged, 3)
        or not _valid_run_slug_list(members, 5)
        or not set(staged).issubset(members)
    ):
        errors.append(f"learning run {run_id} has an invalid review cluster")
        return []

    completed_at = record.get("completed_at")
    for slug in members:
        if not isinstance(learning_lessons.get(slug), dict):
            errors.append(
                f"learning run {run_id} review cluster has unknown member: {slug}"
            )

    trigger_source_id = action.get("id")
    for slug in staged:
        file_record = lesson_files.get(slug)
        if file_record is None:
            errors.append(
                f"learning run {run_id} review cluster staged lesson is missing: {slug}"
            )
            continue
        file_status, metadata = file_record
        lesson = learning_lessons.get(slug)
        if file_status == "approved":
            approved_at = (
                lesson.get("approved_at") if isinstance(lesson, dict) else None
            )
            if (
                not _valid_timestamp(completed_at)
                or not _valid_timestamp(approved_at)
                or parse_utc_timestamp(approved_at) < parse_utc_timestamp(completed_at)
            ):
                errors.append(
                    f"learning run {run_id} review cluster result was not staged "
                    f"at completion: {slug}"
                )
        elif file_status != "draft":
            errors.append(
                f"learning run {run_id} review cluster result is not a draft: {slug}"
            )
        if trigger_source_id not in metadata.get("source_ids", []):
            errors.append(
                f"learning run {run_id} review cluster staged lesson does not cite "
                f"the trigger source: {slug}"
            )
    return members


def _check_latest_cluster_prompts(
    latest_cluster_prompts: dict[str, tuple[str, str]],
    prompt_events: dict[str, set[str]],
    learning_lessons: dict[str, Any],
    errors: list[str],
) -> None:
    for slug, (completed_at, run_id) in latest_cluster_prompts.items():
        lesson = learning_lessons.get(slug)
        if not isinstance(lesson, dict):
            continue
        awaiting_response = lesson.get("awaiting_response")
        if awaiting_response is True:
            prompted_at = lesson.get("prompted_at")
            superseded_by_later_prompt = (
                _valid_timestamp(prompted_at)
                and parse_utc_timestamp(prompted_at) > parse_utc_timestamp(completed_at)
                and prompted_at in prompt_events.get(slug, set())
            )
            if prompted_at != completed_at and not superseded_by_later_prompt:
                errors.append(
                    f"learning run {run_id} review cluster member {slug} has "
                    "prompted_at inconsistent with its latest recorded prompt"
                )
        elif awaiting_response is False:
            last_reviewed = lesson.get("last_reviewed")
            recalled_after_prompt = _valid_timestamp(
                last_reviewed
            ) and parse_utc_timestamp(last_reviewed) >= parse_utc_timestamp(
                completed_at
            )
            if not recalled_after_prompt:
                errors.append(
                    f"learning run {run_id} review cluster member {slug} is neither "
                    "awaiting a response nor recalled after the cluster completed"
                )


def _check_runs(
    root: Path,
    errors: list[str],
    *,
    learning_lessons: dict[str, Any],
    lesson_files: dict[str, tuple[str, dict[str, Any]]],
) -> int:
    runs_directory = state_directory(root) / "runs"
    if not runs_directory.is_dir():
        errors.append("missing learning run directory")
        return 0
    paths = sorted(runs_directory.glob("*.json"))
    active_runs: list[str] = []
    latest_cluster_prompts: dict[str, tuple[str, str]] = {}
    prompt_events: dict[str, set[str]] = {}
    for path in paths:
        record = _read_state(path, errors)
        if record is None:
            continue
        run_id = record.get("run_id")
        if (
            not isinstance(run_id, str)
            or not RUN_ID_PATTERN.fullmatch(run_id)
            or run_id != path.stem
        ):
            errors.append(f"learning run ID mismatch: {path.name}")
            run_id = path.stem
        status = record.get("status")
        if not isinstance(status, str) or status not in RUN_STATUSES:
            errors.append(f"learning run {run_id} has an invalid status")
            continue
        period = record.get("period")
        if not isinstance(period, str) or period not in RUN_PERIODS:
            errors.append(f"learning run {run_id} has an invalid period")
        if not _valid_timestamp(record.get("started_at")):
            errors.append(f"learning run {run_id} has an invalid started_at")

        agenda_path: Path | None = None
        if status != "no-op":
            _check_run_action(
                run_id,
                record.get("next_action"),
                record.get("precondition"),
                errors,
            )
            expected_agenda = f"_staging/digests/{run_id}-learning-agenda.md"
            if record.get("agenda_path") != expected_agenda:
                errors.append(f"learning run {run_id} has an invalid agenda_path")
                agenda_value: Any = None
            else:
                agenda_value = record.get("agenda_path")
            agenda_path = _check_run_path(
                root,
                run_id,
                "agenda_path",
                agenda_value,
                errors,
                prefix=("_staging", "digests"),
                require_file=True,
            )
            _check_run_receipt(run_id, "agenda", agenda_path, record, errors)
            expected_artifact = f"_staging/digests/{run_id}-learning-session.md"
            if record.get("artifact_path") != expected_artifact:
                errors.append(f"learning run {run_id} has an invalid artifact_path")

        if status == "active":
            active_runs.append(run_id)
        elif status == "completed":
            if not _valid_timestamp(record.get("completed_at")):
                errors.append(f"learning run {run_id} has an invalid completed_at")
            elif _valid_timestamp(record.get("started_at")) and parse_utc_timestamp(
                record["completed_at"]
            ) < parse_utc_timestamp(record["started_at"]):
                errors.append(f"learning run {run_id} completed before it started")
            expected_artifact = f"_staging/digests/{run_id}-learning-session.md"
            if record.get("artifact") != expected_artifact:
                errors.append(f"learning run {run_id} has an invalid artifact")
                artifact_value: Any = None
            else:
                artifact_value = record.get("artifact")
            artifact_path = _check_run_path(
                root,
                run_id,
                "artifact",
                artifact_value,
                errors,
                prefix=("_staging", "digests"),
                require_file=True,
            )
            _check_run_receipt(run_id, "artifact", artifact_path, record, errors)
            action = record.get("next_action")
            if isinstance(action, dict) and action.get("watched"):
                members = _check_completed_review_cluster(
                    run_id,
                    record,
                    action,
                    learning_lessons,
                    lesson_files,
                    errors,
                )
                completed_at = record.get("completed_at")
                if _valid_timestamp(completed_at):
                    for slug in members:
                        prompt_events.setdefault(slug, set()).add(completed_at)
                        previous = latest_cluster_prompts.get(slug)
                        if previous is None or parse_utc_timestamp(
                            completed_at
                        ) >= parse_utc_timestamp(previous[0]):
                            latest_cluster_prompts[slug] = (completed_at, run_id)
            elif (
                isinstance(action, dict)
                and action.get("type") in {"review", "staged_lesson"}
                and isinstance(action.get("id"), str)
                and _valid_timestamp(record.get("completed_at"))
            ):
                prompt_events.setdefault(action["id"], set()).add(
                    record["completed_at"]
                )
        elif status == "failed":
            error = record.get("error")
            if not _valid_timestamp(record.get("failed_at")):
                errors.append(f"learning run {run_id} has an invalid failed_at")
            elif _valid_timestamp(record.get("started_at")) and parse_utc_timestamp(
                record["failed_at"]
            ) < parse_utc_timestamp(record["started_at"]):
                errors.append(f"learning run {run_id} failed before it started")
            if (
                not isinstance(error, str)
                or not error
                or len(error) > 2000
                or any(not character.isprintable() for character in error)
                or error != " ".join(error.split())
            ):
                errors.append(f"learning run {run_id} has an invalid error")
        elif status == "no-op":
            if not _valid_timestamp(record.get("completed_at")):
                errors.append(f"learning run {run_id} has an invalid completed_at")
            elif _valid_timestamp(record.get("started_at")) and parse_utc_timestamp(
                record["completed_at"]
            ) < parse_utc_timestamp(record["started_at"]):
                errors.append(f"learning run {run_id} completed before it started")
            if record.get("next_action") is not None:
                errors.append(f"learning run {run_id} has an invalid next_action")

    if len(active_runs) > 1:
        errors.append("workspace contains multiple active learning runs")
    _check_latest_cluster_prompts(
        latest_cluster_prompts, prompt_events, learning_lessons, errors
    )
    return len(paths)


def _inspect_vault_unlocked(vault: Path) -> dict[str, Any]:
    root = require_vault(vault)
    errors: list[str] = []
    warnings: list[str] = []
    manifest = _read_state(state_file(root, "manifest.json"), errors)
    learning_state = _read_state(state_file(root, "learning-state.json"), errors)
    source_watch_path = state_file(root, "source-watch.json")
    if source_watch_path.exists() or source_watch_path.is_symlink():
        source_watch = _read_state(source_watch_path, errors)
    else:
        source_watch = {"schema_version": 1, "roots": [], "files": {}}

    sources: dict[str, Any] = {}
    if manifest is not None:
        if manifest.get("schema_version") != 1 or not isinstance(
            manifest.get("sources"), dict
        ):
            errors.append("manifest.json has an unsupported schema")
        else:
            sources = manifest["sources"]
            for source_id, source in sources.items():
                _check_source(root, source_id, source, errors, warnings)
            sources_directory = root / "sources"
            if not sources_directory.is_dir():
                errors.append("missing managed directory: sources")
                known_dirs: set[str] = set()
            else:
                known_dirs = {
                    path.name for path in sources_directory.iterdir() if path.is_dir()
                }
            for orphan in sorted(known_dirs - sources.keys()):
                errors.append(f"orphan source directory: {orphan}")

    watched_file_count = 0
    if source_watch is not None:
        try:
            validate_source_watch(source_watch, workspace=root)
        except StateError as error:
            errors.append(str(error))
        else:
            watched_file_count = len(source_watch["files"])
            current_watch_paths: dict[str, set[str]] = {}
            for watched_path, record in source_watch["files"].items():
                source_id = record["source_id"]
                if source_id not in sources:
                    errors.append(
                        f"source-watch file cites unknown source ID: {watched_path}"
                    )
                for version in record["versions"]:
                    if version not in sources:
                        errors.append(
                            "source-watch file cites unknown source version: "
                            f"{watched_path}"
                        )
                if any(
                    Path(watched_path).is_relative_to(Path(configured_root))
                    for configured_root in source_watch["roots"]
                ):
                    current_watch_paths.setdefault(source_id, set()).add(watched_path)
            for source_id, source in sources.items():
                if not isinstance(source, dict):
                    continue
                actual_paths = source.get("watch_paths", [])
                expected_paths = sorted(current_watch_paths.get(source_id, set()))
                if actual_paths != expected_paths:
                    errors.append(
                        f"source {source_id} watch paths do not match source-watch state"
                    )
            for configured_root in source_watch["roots"]:
                candidate = Path(configured_root)
                if candidate.is_symlink() or not candidate.is_dir():
                    warnings.append(
                        f"source-watch root is unavailable: {configured_root}"
                    )

    staged_paths = sorted((root / "_staging/lessons").glob("*.md"))
    approved_paths = sorted((root / "lessons").glob("*.md"))
    known_source_ids = set(sources)
    lesson_files: dict[str, tuple[str, dict[str, Any]]] = {}
    for path in staged_paths:
        metadata = _check_lesson(path, "draft", known_source_ids, errors, warnings)
        if metadata is not None:
            lesson_files[path.stem] = ("draft", metadata)
    for path in approved_paths:
        metadata = _check_lesson(path, "approved", known_source_ids, errors, warnings)
        if metadata is not None:
            if path.stem in lesson_files:
                errors.append(f"lesson exists in staging and approved: {path.stem}")
            lesson_files[path.stem] = ("approved", metadata)

    expected_source_links: dict[str, set[str]] = {
        source_id: set() for source_id in known_source_ids
    }
    for slug, (_, metadata) in lesson_files.items():
        for source_id in metadata["source_ids"]:
            expected_source_links.setdefault(source_id, set()).add(slug)
    for source_id, source in sources.items():
        if not isinstance(source, dict) or "coverage_status" not in source:
            continue
        actual_links = source.get("lesson_slugs", [])
        if not isinstance(actual_links, list) or not all(
            isinstance(slug, str) for slug in actual_links
        ):
            errors.append(f"source {source_id} has invalid lesson links")
        elif set(actual_links) != expected_source_links.get(source_id, set()):
            errors.append(f"source {source_id} lesson links do not match lesson files")

    if learning_state is not None:
        if learning_state.get("schema_version") != 1 or not isinstance(
            learning_state.get("lessons"), dict
        ):
            errors.append("learning-state.json has an unsupported schema")
        else:
            _check_learning_state(
                learning_state, lesson_files, known_source_ids, errors
            )

    try:
        expected_index = render_index(root)
        actual_index = (root / "index.md").read_text(encoding="utf-8")
        if actual_index != expected_index:
            errors.append("index.md is stale")
    except (StudyCompilerError, OSError, UnicodeError, TypeError) as error:
        errors.append(f"cannot validate index.md: {error}")

    reviews_directory = root / "reviews"
    if not reviews_directory.is_dir():
        errors.append("missing managed directory: reviews")
        review_paths: list[Path] = []
    else:
        review_paths = sorted(reviews_directory.glob("*.jsonl"))
    latest_reviews: dict[str, dict[str, Any]] = {}
    for review_path in review_paths:
        if review_path.is_symlink():
            errors.append(
                f"invalid review {review_path.name}: symlinks are not allowed"
            )
            continue
        try:
            review_lines = review_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as error:
            errors.append(f"invalid review {review_path.name}: {error}")
            continue
        previous_reviewed_at = None
        for line_number, line in enumerate(review_lines, start=1):
            try:
                record = json.loads(line)
                score = record.get("score") if isinstance(record, dict) else None
                if (
                    not isinstance(record, dict)
                    or record.get("slug") != review_path.stem
                    or isinstance(score, bool)
                    or score not in range(1, 6)
                    or not isinstance(record.get("retelling"), str)
                    or not record["retelling"].strip()
                    or not _valid_timestamp(record.get("reviewed_at"))
                    or not _valid_timestamp(record.get("next_review"))
                ):
                    raise ValueError("invalid review record")
                reviewed_at = parse_utc_timestamp(record["reviewed_at"])
                next_review = parse_utc_timestamp(record["next_review"])
                if next_review <= reviewed_at:
                    raise ValueError("next_review must follow reviewed_at")
                if (
                    previous_reviewed_at is not None
                    and reviewed_at < previous_reviewed_at
                ):
                    raise ValueError("review timestamps are not chronological")
                previous_reviewed_at = reviewed_at
                latest_reviews[review_path.stem] = record
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                errors.append(
                    f"invalid review {review_path.name}:{line_number}: {error}"
                )

    if learning_state is not None and isinstance(learning_state.get("lessons"), dict):
        state_lessons = learning_state["lessons"]
        for slug, lesson in state_lessons.items():
            if (
                isinstance(lesson, dict)
                and any(
                    lesson.get(field) is not None
                    for field in ("mastery_score", "last_reviewed", "next_review")
                )
                and slug not in latest_reviews
            ):
                errors.append(f"review history is missing for lesson: {slug}")
        for slug, review in latest_reviews.items():
            lesson = state_lessons.get(slug)
            if not isinstance(lesson, dict):
                errors.append(f"review {slug} has no learning-state lesson")
                continue
            expected = {
                "mastery_score": review["score"],
                "can_explain": review["score"] >= 4,
                "last_reviewed": review["reviewed_at"],
                "next_review": review["next_review"],
            }
            for field, value in expected.items():
                if lesson.get(field) != value:
                    errors.append(
                        f"review {slug} does not match learning-state {field}"
                    )

    run_count = _check_runs(
        root,
        errors,
        learning_lessons=(
            learning_state["lessons"]
            if isinstance(learning_state, dict)
            and isinstance(learning_state.get("lessons"), dict)
            else {}
        ),
        lesson_files=lesson_files,
    )

    graph = _graph_report_unlocked(root)
    errors.extend(f"lesson graph: {error}" for error in graph["errors"])
    if graph["isolated"]:
        warnings.append(
            "lesson graph has isolated lessons: " + ", ".join(graph["isolated"])
        )

    link_patches = inspect_link_patches_unlocked(root)
    errors.extend(link_patches["errors"])
    warnings.extend(link_patches["warnings"])

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "sources": len(sources),
            "staged_lessons": len(staged_paths),
            "approved_lessons": len(approved_paths),
            "reviews": len(review_paths),
            "learning_runs": run_count,
            "watched_files": watched_file_count,
            "typed_relationships": graph["counts"]["typed_edges"],
            "isolated_lessons": len(graph["isolated"]),
            "staged_link_patches": link_patches["staged"],
            "applied_link_patches": link_patches["applied"],
        },
    }


def inspect_vault(vault: Path) -> dict[str, Any]:
    """Inspect one consistent workspace snapshot under the writer lock."""
    root = require_vault(vault)
    with workspace_lock(root):
        return _inspect_vault_unlocked(root)
