# Study Compiler

[English](README.md) | 简体中文

Study Compiler 是一个面向论文、笔记、网页和 Agent 对话目录的本地优先学习工作流。
它将确定性的增量跟踪与 Agent Skills 结合起来，让定时运行能够发现材料变化、
关联既有知识，并准备便于理解的学习笔记和回忆练习。它不是一个通用 Wiki。

目标不是堆积摘要，而是产出真正能让人理解、能用例子解释，并能用自己的话复述的
学习笔记。

## 工作原理

```text
受监控的来源目录
    → 基于内容哈希的增量扫描
    → 不可变、可版本化的来源登记表
    → 整合到暂存学习笔记，或创建综合笔记／新草稿
    → 关联复习组
    → 学习者复述讲解
    → Agent 诊断与修订
    → 人工明确批准
    → Obsidian 兼容的正式笔记
    → 暂存带类型的交叉链接 → 明确批准链接补丁
    → 定期学习计划
```

生成的学习笔记和交叉链接提案绝不会自动进入正式笔记库。它们会保留在
`_staging/` 中，直到学习者明确批准对应的学习笔记 slug 或链接补丁 ID。

## 安装

Study Compiler 需要 Python 3.11 或更高版本，并且没有运行时依赖。

克隆仓库后，只需一条命令即可安装 CLI、发现本地 Agent、安装全部 7 个内置
Skills，并初始化学习工作区：

```bash
cd ~/Github/study-compiler
./install.sh \
  --workspace ~/Documents/MyLearning \
  --source-root ~/Documents/Notes \
  --source-root ~/Documents/ChatExports
```

安装器会优先使用 `uv`；如果不可用，则创建隔离的 Python 虚拟环境。在交互式
终端中，它会显示多选菜单。下面的代码块是程序的真实英文输出：

```text
Select where to install Study Compiler skills
↑/↓ Move  Space Toggle  Enter Confirm

 › ● Codex
   ○ Claude Code
   ○ Shared agents (OpenCode, Aider, and others)
```

检测到的 Agent 默认处于选中状态。安装完成后请重启所选 Agent，使其重新加载
Skills。

常用安装方式：

```bash
./install.sh --agent all --workspace ~/Documents/MyLearning
./install.sh --agent claude --workspace ~/Documents/MyLearning
./install.sh --target ~/.my-agent/skills --copy
```

安装器不会静默覆盖同名 Skill。已安装内容未变化时，重复运行安装器是安全的；
只有在你明确希望替换冲突的 Study Compiler Skills 时才使用 `--force`。

如果 CLI 已经安装，等价的设置命令是：

```bash
study-compiler setup \
  --agent auto \
  --workspace ~/Documents/MyLearning \
  --source-root ~/Documents/Notes \
  --source-root ~/Documents/ChatExports
```

`setup` 默认输出简洁、便于阅读的摘要。自动化场景可以通过
`study-compiler setup ... --json` 获得稳定的机器可读结果。提供 `--workspace`
时，setup 还会把解析后的路径保存到 `~/.study-compiler/config.json`。除非显式
传入 `--workspace`，面向 Agent 的 `status` 和 `run` 命令都会使用这个默认路径。

## 快速开始

将论文、Markdown 碎片、文本笔记或对话导出文件放进任一已配置的来源目录，
然后让 Agent 运行：

```text
使用 $learning-run 检查我的来源变化，把其中一项与现有学习笔记整合，
并准备一次联合复习。
```

Agent 会调用 `status` 和 `run start`。新的或发生变化的本地文件会通过 SHA-256
自动检测并生成快照；正常工作流不需要逐文件执行导入命令。文件内容修改后会形成
新的不可变版本；内容相同的重命名只更新路径溯源；删除文件只会被报告，不会删除
已有快照或学习笔记。

如果 `status` 报告任何 `missing_roots`，Agent 会列出所有不可用路径，并在开始
学习运行前停止。来源扫描不完整属于配置失败，而不是正常的 `no-op`。

如果变化的来源与尚未复习的暂存概念匹配，Agent 会把新材料整合进该草稿。如果它
与已批准、已复习、等待回答、概念不同或存在矛盾的知识相关，Agent 会创建一篇独立
的暂存综合笔记。否则，它会创建一篇聚焦单一主题的学习笔记。生成结果会围绕一个
综合示例和联合复述问题，组织最多 5 篇相关笔记。正在等待学习者回答的笔记可以为
综合笔记提供背景，但在现有回答被记录之前，不会再次加入新的复习组或重复提问。

底层来源命令仍保留给特殊输入。例如，先登记一个网页，由获得授权的 Agent 获取并
清理内容，再附加不可变快照：

```bash
study-compiler source add-url ~/Documents/MyLearning \
  https://example.com/article \
  --title "Example article"

study-compiler source snapshot-web ~/Documents/MyLearning \
  <returned-source-id> /tmp/example-article.md
```

也可以仅保存当前对话中、尚未存在于受监控导出文件里的选定片段：

```bash
study-compiler source add-snippet ~/Documents/MyLearning \
  --kind chat \
  --title "Attention explanation" \
  --from-file /tmp/attention-chat.md
```

复习和批准始终是显式操作：

```text
使用 $recall-review 检查我的复述。
使用 $learning-stage-commit 在正式化之前审阅草稿。
```

正常学习循环是：把材料放进受监控目录，让 `$learning-run` 检测并整合增量，
使用 `$recall-review` 复述关联复习组，最后才通过 `$learning-stage-commit`
批准正式笔记。

学习笔记批准后，可以让 Agent 执行一次持久的知识图谱关联：

```text
使用 $learning-cross-link 查找已批准学习笔记中缺失的关联，
并暂存带类型的链接补丁供我审阅。
```

该 Skill 使用与 `obsidian-wiki` 相同的 7 种关系类型，但不会直接编辑已批准的
Markdown：`extends`、`implements`、`contradicts`、`derived_from`、`uses`、
`replaces` 和 `related_to`。使用 `$learning-stage-commit` 审阅补丁；只有
`study-compiler link apply <workspace> <patch-id> --approve` 才会写入带类型的
frontmatter 和由程序生成的 Related Lessons 区块。目标笔记保持不变，反向导航由
Obsidian backlink 提供。

创建草稿会把来源标记为 `partial`。当 Agent 确认该来源范围内的概念都已完成整理
后，可以关闭对应的确定性队列项：

```bash
study-compiler source coverage ~/Documents/MyLearning \
  <source-id> --status complete
```

## 定期学习

检查已配置的工作区，并开始一次有边界、有记录的 Agent 运行：

```bash
study-compiler status --json
study-compiler run start --period weekly
```

`status` 会报告精确的受监控文件增量。`run start` 在工作区锁内进行扫描，一次最多
为一项内容变化创建快照，把状态记录在 `.study-compiler/runs/` 中，并返回一个
`next_action`。受监控来源动作的优先级高于到期复习，以便先关联新知识。如果某个
已配置的来源目录不可用，`$learning-run` 会报告缺失路径，并把扫描视为不完整，
而不是返回正常的 `no-op`。尚未生成快照的网页 URL 会显示为 `source_snapshot`；
Agent 会先通过 `$learning-ingest` 处理该动作，再根据来源创建草稿。每日、每周和
每月计划分别包含未来 1、7、30 天内的复习任务；薄弱的回忆结果在任何周期中都保持
可操作状态。

如果之前的 Agent 被中断，应恢复已有运行，而不是并行启动一个新运行：

```bash
study-compiler run resume <run-id>
```

`$learning-run` Skill 每次只处理一个动作，并创建 CLI 返回的、与本次运行精确对应
的 Markdown `artifact_path`。frontmatter 必须位于文件开头，并通过 `run_id`、
`action_type` 和 `action_id` 将产物绑定到本次运行；产物大小上限为 5 MiB。来源
动作和网页快照动作还必须产生对应的已记录状态变化。受监控来源产物包含经过验证的
复习组绑定信息：变化来源、暂存结果，以及 1 至 5 个成员学习笔记 slug。随后记录
以下两种结果之一：

```bash
study-compiler run finish <run-id> \
  --artifact ~/Documents/MyLearning/_staging/digests/<run-id>-learning-session.md
study-compiler run fail <run-id> --message "concise failure"
```

确定性生成的 agenda 不能直接充当最终产物。完成运行时会保存 agenda 和 artifact
的哈希及大小。只包含问题的检索会把学习笔记标记为等待学习者回答，使后续定时运行
可以继续处理其他工作，而不是重复同一个问题；新的已记录回忆则会完成这次复习。
完成运行绝不会批准或正式化学习笔记；批准仍是独立、显式的
`$learning-stage-commit` 操作。

当一次运行没有来源或复习任务时，`$learning-run` 会检查知识图谱健康状态，并可
能把一次有边界的处理交给 `$learning-cross-link`。已完成的来源运行也可以在已批准
的复习组成员之间暂存一条证据充分、尚缺失的关系。定时处理只能创建与内容哈希绑定
的补丁，不能应用补丁。

Codex automation、cron 或其他外部调度器可以触发 Agent 调用 `$learning-run`。
Study Compiler 不会安装或运行后台守护进程。只需要确定性学习计划时，仍可使用
`digest build`。

## Skills

- `learning-ingest`：处理特殊／手动来源，并安全地为网页创建快照。
- `learning-cross-link`：在已批准学习笔记之间发现并暂存带类型的关系。
- `learning-run`：检测一项增量、整合既有知识，并准备可恢复的联合复习。
- `lesson-draft`：把来源整合或综合为包含直觉、示例和检索练习的学习笔记。
- `recall-review`：在关联复述中评估每篇相关笔记、诊断缺口并修订暂存教学内容。
- `learning-stage-commit`：预览并明确正式化一篇草稿，或应用一个带类型的链接补丁。
- `learning-digest`：把到期任务整理成聚焦、易读的学习过程。

## 工作区结构

```text
MyLearning/
├── .study-compiler/
│   ├── manifest.json
│   ├── learning-state.json
│   ├── source-watch.json
│   ├── workspace.lock
│   ├── runs/
│   └── link-patches/       # 已应用补丁的审计记录
├── sources/
├── _staging/
│   ├── lessons/
│   ├── digests/
│   └── link-patches/       # 等待批准的提案
├── exports/                # 仅保存派生的知识图谱 JSON
├── lessons/
├── reviews/
├── index.md
└── log.md
```

来源溯源和学习者掌握度有意保存在不同的状态文件中。来源工作流 `status`（`new`、
`staged` 或 `integrated`）也独立于学习 `coverage_status`（`pending`、`partial`
或 `complete`）。创建草稿会让被引用的来源变为部分覆盖；正式化笔记并不代表整个
来源已被完整整理。高回忆分数不会提高某项陈述的可信度，低回忆分数也不会削弱其
来源可信度。

所有涉及多个文件的来源、学习笔记、digest 和运行状态变更都共享一个快速失败的
单写入者工作区锁。`doctor` 会检测 manifest／metadata 漂移、无效的学习笔记或运行
状态，以及内容不再符合记录状态的文件。正式化前还会重新检查每个引用来源的镜像
metadata、归属快照、字节大小和 SHA-256 收据。所有受管理路径组件都拒绝符号链接；
工作区和配置目录默认权限为 `0700`，生成状态和笔记为 `0600`，不可变原始快照为
`0400`。基于文件的对话片段、学习笔记、复述和运行输入会在完整解码前检查大小；
学习笔记和运行产物上限为 5 MiB。持久化时间戳使用规范 UTC，复习和运行历史不得
倒退。

学习笔记格式 v3 在 Obsidian 兼容的 frontmatter 中保存有方向、带类型的关系。
`graph check` 会拒绝缺失目标、自链接、重复关系和正文断链，并分别报告：
`unreferenced`（没有入链）、`dead_ends`（没有出链）和 `isolated`（既没有入链也
没有出链）。`graph export` 会输出稳定的有向 JSON；`--output graph.json` 只能
写入 `exports/graph.json`，绝不会覆盖受管理状态或来源文件。无类型 wikilink 导出
为 `typed: false` 和 `type: null`。链接提案 JSON 有大小限制并检查重复键；如果已
批准的来源学习笔记不再匹配补丁中的 SHA-256，应用操作会失败。一个来源学习笔记对
同一目标最多只能有一种带类型的关系。

## 验证

```bash
python3 -m unittest discover -s tests -v
study-compiler doctor ~/Documents/MyLearning --json
study-compiler graph check ~/Documents/MyLearning --json
```

`doctor` 会检查来源哈希、快照归属、metadata 一致性、路径范围、学习笔记 schema、
来源引用、复习／状态历史的双向一致性、运行记录、带类型的关系、暂存／已应用链接
补丁、状态 JSON／UTF-8，以及生成的学习笔记索引。

完整约定请参阅[架构](docs/architecture.md)、[规格说明](docs/spec.md)、
[威胁模型](docs/threat-model.md)、[验收标准](docs/acceptance.md)，以及
[ADR-001](docs/decisions/001-recorded-agent-runs.md) 和
[ADR-003](docs/decisions/003-staged-typed-cross-links.md)。

## MVP 边界

0.4 版本有意不包含托管服务、模型提供商依赖、向量数据库、内置调度器／守护进程
或自动发布。这些能力可以在核心学习循环证明有效后再添加。
