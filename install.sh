#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${LEARNING_WIKI_BIN_DIR:-$HOME/.local/bin}"
TOOL_DIR="${LEARNING_WIKI_TOOL_DIR:-$HOME/.local/share/learning-wiki/tools}"

usage() {
  cat <<'EOF'
Install Learning Wiki, its Agent Skills, and optionally a learning vault.

Usage:
  ./install.sh --vault ~/Documents/MyLearning
  ./install.sh --agent all --vault ~/Documents/MyLearning
  ./install.sh --target ~/.my-agent/skills --copy

Setup options passed through to learning-wiki:
  --vault PATH       Initialize a learning vault at PATH.
  --agent NAME       Install for auto, codex, claude, agents, or all.
  --target PATH      Install into a custom skill directory (repeatable).
  --copy             Copy Skills instead of creating symlinks.
  --force            Replace only conflicting Learning Wiki Skills.

Environment overrides for managed installation paths:
  LEARNING_WIKI_BIN_DIR
  LEARNING_WIKI_TOOL_DIR
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

mkdir -p "$BIN_DIR" "$TOOL_DIR"

printf '\nLearning Wiki 安装\n\n'

if command -v uv >/dev/null 2>&1; then
  export UV_TOOL_BIN_DIR="$BIN_DIR"
  export UV_TOOL_DIR="$TOOL_DIR"
  printf '  正在安装命令行工具... '
  uv tool install --quiet --force --reinstall "$ROOT"
  printf '完成\n'
  CLI="$BIN_DIR/learning-wiki"
else
  VENV="$TOOL_DIR/venv"
  printf '  正在创建独立 Python 环境... '
  python3 -m venv "$VENV"
  "$VENV/bin/python" -m pip install \
    --quiet --no-deps --force-reinstall --no-cache-dir "$ROOT"
  printf '完成\n'
  CLI="$VENV/bin/learning-wiki"
  LINK="$BIN_DIR/learning-wiki"
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
  echo "Learning Wiki executable was not installed at $CLI" >&2
  exit 2
fi

printf '  正在配置 Agent Skills...\n'
"$CLI" setup "$@"
