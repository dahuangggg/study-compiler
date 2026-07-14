from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Iterator, Sequence, TextIO

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

AGENT_LABELS = {
    "codex": "Codex",
    "claude": "Claude Code",
    "agents": "Shared agents (OpenCode, Aider, and others)",
}


def detect_agent_names(*, home: Path | None = None) -> list[str]:
    resolved_home = (home or Path.home()).expanduser().resolve()
    detected = [
        name
        for name, relative in AGENT_SKILL_DIRECTORIES.items()
        if (resolved_home / Path(relative).parent).is_dir()
    ]
    return detected or ["codex"]


def _read_terminal_key() -> str:
    if os.name == "nt":  # pragma: no cover - exercised on Windows
        import msvcrt

        key = msvcrt.getwch()
        if key in {"\x00", "\xe0"}:
            return {"H": "up", "P": "down"}.get(msvcrt.getwch(), "other")
    else:
        import termios
        import tty

        descriptor = sys.stdin.fileno()
        previous = termios.tcgetattr(descriptor)
        try:
            tty.setraw(descriptor)
            key = sys.stdin.read(1)
            if key == "\x1b":
                sequence = sys.stdin.read(2)
                return {"[A": "up", "[B": "down"}.get(sequence, "other")
        finally:
            termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)
    if key in {"\r", "\n"}:
        return "enter"
    if key == " ":
        return "space"
    if key == "\x03":
        raise KeyboardInterrupt
    return "other"


def _selector_lines(cursor: int, selected: set[str]) -> list[str]:
    lines = [
        "Select where to install Study Compiler skills",
        "↑/↓ Move  Space Toggle  Enter Confirm",
        "",
    ]
    for index, name in enumerate(AGENT_SKILL_DIRECTORIES):
        pointer = "›" if index == cursor else " "
        checkbox = "●" if name in selected else "○"
        lines.append(f" {pointer} {checkbox} {AGENT_LABELS[name]}")
    return lines


def choose_agent_names(
    detected: Sequence[str],
    *,
    keys: Iterator[str] | None = None,
    output: TextIO | None = None,
) -> list[str]:
    """Run a dependency-free terminal multi-select and return Agent names."""
    stream = output or sys.stdout
    selected = set(detected) & set(AGENT_SKILL_DIRECTORIES)
    if not selected:
        selected.add("codex")
    cursor = 0
    key_source = keys
    rendered_lines = 0

    while True:
        lines = _selector_lines(cursor, selected)
        if rendered_lines:
            stream.write(f"\x1b[{rendered_lines}A")
        for line in lines:
            stream.write(f"\r\x1b[2K{line}\n")
        stream.flush()
        rendered_lines = len(lines)

        try:
            key = next(key_source) if key_source is not None else _read_terminal_key()
        except StopIteration as error:
            raise StateError("Agent selection ended before confirmation") from error
        if key == "up":
            cursor = (cursor - 1) % len(AGENT_SKILL_DIRECTORIES)
        elif key == "down":
            cursor = (cursor + 1) % len(AGENT_SKILL_DIRECTORIES)
        elif key == "space":
            name = tuple(AGENT_SKILL_DIRECTORIES)[cursor]
            selected.symmetric_difference_update({name})
        elif key == "enter" and selected:
            stream.write("\n")
            stream.flush()
            return [name for name in AGENT_SKILL_DIRECTORIES if name in selected]


def locate_bundled_skills() -> Path:
    """Find skills in a source checkout or an installed Python environment."""
    candidates = (
        Path(__file__).resolve().parents[2] / "skills",
        Path(sys.prefix) / "share/study-compiler/skills",
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
        names.extend(detect_agent_names(home=resolved_home))
    names.extend(name for name in requested if name not in {"all", "auto"})

    targets = [path.expanduser().resolve() for path in custom_targets]
    targets.extend(
        (resolved_home / AGENT_SKILL_DIRECTORIES[name]).resolve() for name in names
    )
    return list(dict.fromkeys(targets))


def _display_path(path: str) -> str:
    resolved = Path(path)
    try:
        return f"~/{resolved.relative_to(Path.home()).as_posix()}"
    except ValueError:
        return str(resolved)


def _target_label(path: str) -> str:
    normalized = Path(path).as_posix()
    for name, relative in AGENT_SKILL_DIRECTORIES.items():
        if normalized.endswith(f"/{relative}"):
            return AGENT_LABELS[name]
    return "Custom agent"


def format_setup_summary(result: dict[str, object]) -> str:
    lines = ["", "✓ Study Compiler is ready", ""]
    workspace = result.get("workspace")
    if isinstance(workspace, dict):
        if workspace.get("migrated"):
            state = "migrated from Learning Wiki"
        else:
            state = "created" if workspace.get("created") else "already exists"
        lines.append(
            f"  Workspace  {_display_path(str(workspace['workspace']))} ({state})"
        )
    else:
        lines.append("  Workspace  not created (run study-compiler init later)")
    lines.extend(["  Skills     5 skills ready", "", "  Installed for"])
    targets = result.get("targets", [])
    if isinstance(targets, list):
        for report in targets:
            if not isinstance(report, dict):
                continue
            path = str(report["path"])
            lines.append(f"  - {_target_label(path)}  {_display_path(path)}")
    lines.extend(
        [
            "",
            "Next: restart the selected agent, then ask:",
            "  Use $learning-ingest to import this paper or webpage.",
        ]
    )
    return "\n".join(lines)


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


def _is_legacy_managed_link(destination: Path) -> bool:
    if not destination.is_symlink():
        return False
    return "/learning-wiki/share/learning-wiki/skills/" in (
        destination.resolve().as_posix()
    )


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
                if _is_legacy_managed_link(destination):
                    actions.append((source, destination, "replace"))
                elif not force:
                    raise StateError(
                        f"Skill already exists and was preserved: {destination}; "
                        "rerun with --force to replace it"
                    )
                else:
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
