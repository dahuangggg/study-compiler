# Study Compiler

[中文](#中文) | [English](#english)

---

## 中文

**Study Compiler**：本地优先、由 Agent 引导的学习编译器，把论文、笔记、网页和 Agent 对话整理成可复述、经人工批准的 Obsidian 兼容学习笔记。

### 项目状态

早期项目（v0.4.0），不再维护。

### 功能

- 监控来源目录，基于 SHA-256 做增量扫描，原始来源以不可变快照保存
- 生成的学习笔记先进入 `_staging/`，需显式 `--approve` 才会成为正式笔记
- 复述（explain-it-back）记录与掌握度跟踪，来源可信度与学习掌握度分开管理
- 带类型的交叉链接补丁（Lesson format v3）、知识图谱检查与 JSON 导出
- 每日/每周/每月学习计划（digest）和可恢复的单次学习运行（`run`）
- `doctor` 校验工作区一致性；单写者工作区锁
- 内置 7 个 Agent Skills（`skills/`），可安装到 Codex、Claude Code 等

### 技术栈

- Python 3.11+，仅标准库（无运行时依赖），setuptools 打包
- Markdown（Obsidian 兼容）+ JSON 状态文件
- Bash 安装脚本 `install.sh`；Markdown Agent Skills

### 本地运行

```bash
# 安装 CLI 和 Skills，并初始化工作区（有 uv 时用 uv，否则创建独立 venv）
./install.sh --workspace ~/Documents/MyLearning --source-root ~/Documents/Notes

# 或直接从源码运行
PYTHONPATH=src python3 -m study_compiler --help
PYTHONPATH=src python3 -m study_compiler init /tmp/ws
PYTHONPATH=src python3 -m study_compiler doctor /tmp/ws

# 运行测试
python3 -m unittest discover -s tests -v
```

完整的中文使用说明见 [README.zh-CN.md](README.zh-CN.md)。

### 目录结构

```text
src/study_compiler/   CLI 与核心逻辑（来源、笔记、链接、复述、digest、doctor、run）
skills/               内置 Agent Skills（SKILL.md）
tests/                unittest 测试
docs/                 架构、规格、威胁模型、验收标准、ADR
examples/             示例笔记与复述
tasks/                计划与待办
install.sh            一键安装脚本
```

### 许可证

[MIT](LICENSE)

---

## English

**Study Compiler**: a local-first, agent-guided learning compiler that turns papers, notes, web pages, and agent conversations into explicitly approved, explain-it-back-ready, Obsidian-compatible learning notes.

### Status

Early project (v0.4.0), no longer actively maintained.

### Features

- Watches source folders with SHA-256 delta scans; raw sources are stored as immutable snapshots
- Generated lessons land in `_staging/` and only become formal notes with an explicit `--approve`
- Explain-it-back recall logging and mastery tracking, kept separate from source confidence
- Typed cross-link patches (lesson format v3), graph checks, and JSON graph export
- Daily/weekly/monthly learning agendas (`digest`) and resumable single learning runs (`run`)
- `doctor` validates workspace integrity; single-writer workspace lock
- Seven bundled Agent Skills (`skills/`) installable for Codex, Claude Code, and others

### Tech stack

- Python 3.11+, standard library only (no runtime dependencies), packaged with setuptools
- Obsidian-compatible Markdown + JSON state files
- Bash installer `install.sh`; Markdown Agent Skills

### Getting started

```bash
# Install the CLI and Skills and initialize a workspace (uses uv if available, otherwise an isolated venv)
./install.sh --workspace ~/Documents/MyLearning --source-root ~/Documents/Notes

# Or run straight from source
PYTHONPATH=src python3 -m study_compiler --help
PYTHONPATH=src python3 -m study_compiler init /tmp/ws
PYTHONPATH=src python3 -m study_compiler doctor /tmp/ws

# Run tests
python3 -m unittest discover -s tests -v
```

See [docs/architecture.md](docs/architecture.md) and [docs/spec.md](docs/spec.md) for the full contract.

### Directory structure

```text
src/study_compiler/   CLI and core logic (sources, lessons, links, recall, digest, doctor, run)
skills/               Bundled Agent Skills (SKILL.md)
tests/                unittest suite
docs/                 Architecture, spec, threat model, acceptance criteria, ADRs
examples/             Example lesson and retelling
tasks/                Plan and todo
install.sh            One-command installer
```

### License

[MIT](LICENSE)
