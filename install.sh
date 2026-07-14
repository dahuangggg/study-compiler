#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${STUDY_COMPILER_BIN_DIR:-$HOME/.local/bin}"
TOOL_DIR="${STUDY_COMPILER_TOOL_DIR:-$HOME/.local/share/study-compiler/tools}"
LEGACY_TOOL_DIR="$HOME/.local/share/learning-wiki/tools"

usage() {
  cat <<'EOF'
Install Study Compiler, its agent skills, and optionally a study workspace.

Usage:
  ./install.sh --workspace ~/Documents/MyLearning --source-root ~/Documents/Notes
  ./install.sh --agent all --workspace ~/Documents/MyLearning
  ./install.sh --target ~/.my-agent/skills --copy

Setup options passed through to study-compiler:
  --workspace PATH   Initialize a study workspace at PATH.
  --source-root PATH Watch an external learning-source directory (repeatable).
  --agent NAME       Install for auto, codex, claude, agents, or all.
  --target PATH      Install into a custom skill directory (repeatable).
  --copy             Copy Skills instead of creating symlinks.
  --force            Replace only conflicting Study Compiler skills.

Environment overrides for managed installation paths:
  STUDY_COMPILER_BIN_DIR
  STUDY_COMPILER_TOOL_DIR
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

mkdir -p "$BIN_DIR" "$TOOL_DIR"

printf '\nStudy Compiler Setup\n\n'

if command -v uv >/dev/null 2>&1; then
  export UV_TOOL_BIN_DIR="$BIN_DIR"
  export UV_TOOL_DIR="$TOOL_DIR"
  printf '  Installing the CLI... '
  uv tool install --quiet --force --reinstall "$ROOT"
  printf 'done\n'
  CLI="$BIN_DIR/study-compiler"
else
  VENV="$TOOL_DIR/venv"
  printf '  Creating an isolated Python environment... '
  python3 -m venv "$VENV"
  "$VENV/bin/python" -m pip install \
    --quiet --no-deps --force-reinstall --no-cache-dir "$ROOT"
  printf 'done\n'
  CLI="$VENV/bin/study-compiler"
  LINK="$BIN_DIR/study-compiler"
  if [[ -e "$LINK" || -L "$LINK" ]]; then
    if [[ ! -L "$LINK" || "$(readlink "$LINK")" != "$CLI" ]]; then
      echo "Refusing to replace existing executable: $LINK" >&2
      exit 2
    fi
  else
    ln -s "$CLI" "$LINK"
  fi
fi

if [[ ! -x "$CLI" ]]; then
  echo "Study Compiler executable was not installed at $CLI" >&2
  exit 2
fi

printf '  Configuring agent skills...\n'
"$CLI" setup "$@"

if command -v uv >/dev/null 2>&1 && [[ -d "$LEGACY_TOOL_DIR/learning-wiki" ]]; then
  printf '  Removing the previous Learning Wiki CLI... '
  UV_TOOL_DIR="$LEGACY_TOOL_DIR" UV_TOOL_BIN_DIR="$BIN_DIR" \
    uv tool uninstall --quiet learning-wiki || true
  printf 'done\n'
fi
