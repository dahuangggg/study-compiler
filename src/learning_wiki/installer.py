from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Sequence

from .errors import StateError

SKILL_NAMES = (
    "learning-digest",
    "learning-ingest",
    "learning-stage-commit",
    "lesson-draft",
    "recall-review",
)

AGENT_SKILL_DIRECTORIES = {
    "codex": ".codex/skills",
    "claude": ".claude/skills",
    "agents": ".agents/skills",
}


def locate_bundled_skills() -> Path:
    """Find skills in a source checkout or an installed Python environment."""
    candidates = (
        Path(__file__).resolve().parents[2] / "skills",
        Path(sys.prefix) / "share/learning-wiki/skills",
    )
    for candidate in candidates:
        if all((candidate / name / "SKILL.md").is_file() for name in SKILL_NAMES):
            return candidate.resolve()
    searched = ", ".join(str(path) for path in candidates)
    raise StateError(f"bundled Skills are missing; searched: {searched}")


def resolve_skill_targets(
    agents: Sequence[str],
    custom_targets: Sequence[Path],
    *,
    home: Path | None = None,
) -> list[Path]:
    """Resolve explicit targets or auto-detect locally configured Agents."""
    resolved_home = (home or Path.home()).expanduser().resolve()
    names: list[str] = []
    requested = list(agents)
    if "all" in requested:
        requested = list(AGENT_SKILL_DIRECTORIES)
    if "auto" in requested or (not requested and not custom_targets):
        names.extend(
            name
            for name, relative in AGENT_SKILL_DIRECTORIES.items()
            if (resolved_home / Path(relative).parent).is_dir()
        )
        if not names:
            names.append("codex")
    names.extend(name for name in requested if name not in {"all", "auto"})

    targets = [path.expanduser().resolve() for path in custom_targets]
    targets.extend(
        (resolved_home / AGENT_SKILL_DIRECTORIES[name]).resolve() for name in names
    )
    return list(dict.fromkeys(targets))


def _directories_match(source: Path, destination: Path) -> bool:
    if not destination.is_dir() or destination.is_symlink():
        return False
    source_files = sorted(
        path.relative_to(source) for path in source.rglob("*") if path.is_file()
    )
    destination_files = sorted(
        path.relative_to(destination)
        for path in destination.rglob("*")
        if path.is_file()
    )
    if source_files != destination_files:
        return False
    return all(
        (source / relative).read_bytes() == (destination / relative).read_bytes()
        for relative in source_files
    )


def _is_unchanged(source: Path, destination: Path, *, copy: bool) -> bool:
    if copy:
        return _directories_match(source, destination)
    return destination.is_symlink() and destination.resolve() == source.resolve()


def install_skills(
    skill_root: Path,
    targets: Sequence[Path],
    *,
    copy: bool = False,
    force: bool = False,
) -> list[dict[str, object]]:
    """Install bundled Skills without silently replacing user-owned content."""
    source_root = skill_root.expanduser().resolve()
    missing = [
        name for name in SKILL_NAMES if not (source_root / name / "SKILL.md").is_file()
    ]
    if missing:
        raise StateError(
            f"invalid bundled Skills directory; missing: {', '.join(missing)}"
        )

    resolved_targets = [target.expanduser().resolve() for target in targets]
    if not resolved_targets:
        raise StateError("no Agent skill directory selected")

    actions: list[tuple[Path, Path, str]] = []
    for target in resolved_targets:
        for name in SKILL_NAMES:
            source = source_root / name
            destination = target / name
            if _is_unchanged(source, destination, copy=copy):
                actions.append((source, destination, "unchanged"))
            elif destination.exists() or destination.is_symlink():
                if not force:
                    raise StateError(
                        f"Skill already exists and was preserved: {destination}; "
                        "rerun with --force to replace it"
                    )
                actions.append((source, destination, "replace"))
            else:
                actions.append((source, destination, "install"))

    reports: list[dict[str, object]] = []
    for target in resolved_targets:
        target.mkdir(parents=True, exist_ok=True)
        installed: list[str] = []
        unchanged: list[str] = []
        for source, destination, action in actions:
            if destination.parent != target:
                continue
            if action == "unchanged":
                unchanged.append(destination.name)
                continue
            if action == "replace":
                if destination.is_symlink() or destination.is_file():
                    destination.unlink()
                else:
                    shutil.rmtree(destination)
            if copy:
                shutil.copytree(source, destination)
            else:
                destination.symlink_to(source, target_is_directory=True)
            installed.append(destination.name)
        reports.append(
            {
                "path": str(target),
                "mode": "copy" if copy else "symlink",
                "installed": installed,
                "unchanged": unchanged,
            }
        )
    return reports
