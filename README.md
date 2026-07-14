# Study Compiler

English | [简体中文](README.zh-CN.md)

Study Compiler is a local-first learning workflow for folders of papers, notes,
webpages, and Agent conversations. It combines deterministic delta tracking with
Agent Skills so a scheduled run can notice changed material, connect it to prior
knowledge, and prepare teachable lessons and recall practice. It is not a
general-purpose wiki.

The goal is not to accumulate summaries. The goal is to produce notes a person can understand, explain with examples, and retell in their own words.

## How it works

```text
watched source folders
    → content-hash delta scan
    → immutable, versioned source registry
    → integrate a staged lesson or create a synthesis/new draft
    → connected review cluster
    → learner explain-it-back attempt
    → Agent diagnosis and revision
    → explicit human approval
    → formal Obsidian-compatible note
    → staged typed cross-links → explicit patch approval
    → periodic agenda
```

Generated lessons and proposed cross-links never enter the formal notebook
automatically. They stay in `_staging/` until the learner explicitly approves
the exact lesson slug or patch ID.

## Install

Study Compiler requires Python 3.11 or newer and has no runtime dependencies.

From a cloned checkout, one command installs the CLI, discovers your local Agent,
installs all seven bundled Skills, and initializes a study workspace:

```bash
cd ~/Github/study-compiler
./install.sh \
  --workspace ~/Documents/MyLearning \
  --source-root ~/Documents/Notes \
  --source-root ~/Documents/ChatExports
```

The installer uses `uv` when available and otherwise creates an isolated Python
virtual environment. In an interactive terminal it opens a multi-select menu:

```text
Select where to install Study Compiler skills
↑/↓ Move  Space Toggle  Enter Confirm

 › ● Codex
   ○ Claude Code
   ○ Shared agents (OpenCode, Aider, and others)
```

Detected Agents start selected. Restart the selected Agent after installation
so it reloads the Skills.

Useful variants:

```bash
./install.sh --agent all --workspace ~/Documents/MyLearning
./install.sh --agent claude --workspace ~/Documents/MyLearning
./install.sh --target ~/.my-agent/skills --copy
```

Existing same-name Skills are never overwritten silently. Re-running the
installer is safe when the installed content is unchanged; use `--force` only
when you intentionally want to replace conflicting Study Compiler skills.

If the CLI is already installed, the equivalent setup command is:

```bash
study-compiler setup \
  --agent auto \
  --workspace ~/Documents/MyLearning \
  --source-root ~/Documents/Notes \
  --source-root ~/Documents/ChatExports
```

`setup` prints a concise human-readable summary by default. Automation can keep
the stable machine-readable response with `study-compiler setup ... --json`.
When `--workspace` is supplied, setup also saves its resolved path in
`~/.study-compiler/config.json`. The Agent-facing `status` and `run` commands use
that default unless `--workspace` is passed explicitly.

## Quick start

Put papers, Markdown fragments, text notes, or conversation exports into one of
the configured source roots. Then ask the Agent to run:

```text
Use $learning-run to check my source changes, integrate one with my existing
learning notes, and prepare a joint review.
```

The Agent invokes `status` and `run start`. New or changed local files are
detected by SHA-256 and snapshotted automatically; no per-file import command is
part of the normal workflow. A modified file becomes a new immutable version,
a same-content rename only updates path provenance, and deletion is reported
without deleting snapshots or lessons.

If `status` reports any `missing_roots`, the Agent reports every unavailable
path and stops before starting a learning run. An incomplete source scan is a
configuration failure, not a normal `no-op`.

If a changed source matches an unreviewed staged concept, the Agent integrates
it there. If it relates to approved, reviewed, awaiting, distinct, or
contradictory knowledge, it creates a separate staged synthesis. Otherwise it
creates one focused lesson. The result groups up to five related lessons around
an integrated example and joint explain-it-back prompt. An awaiting-response
lesson may inform a synthesis, but it is not added to the new review cluster or
prompted again until its existing learner response is recorded.

The low-level source commands remain available for exceptional inputs. For
example, register a webpage, let an authorized Agent fetch and clean it, then
attach the immutable snapshot:

```bash
study-compiler source add-url ~/Documents/MyLearning \
  https://example.com/article \
  --title "Example article"

study-compiler source snapshot-web ~/Documents/MyLearning \
  <returned-source-id> /tmp/example-article.md
```

Or save only a selected in-conversation fragment that does not exist in a
watched export:

```bash
study-compiler source add-snippet ~/Documents/MyLearning \
  --kind chat \
  --title "Attention explanation" \
  --from-file /tmp/attention-chat.md
```

Review and approve remain explicit:

```text
Use $recall-review to check my explanation.
Use $learning-stage-commit to review the draft before promotion.
```

The normal learning loop is: place material in a watched folder, let
`$learning-run` detect and integrate the delta, explain the connected cluster
back with `$recall-review`, and only then use `$learning-stage-commit` to approve
the formal note.

After lessons are approved, ask for a durable graph pass:

```text
Use $learning-cross-link to find missing connections in my approved lessons and
stage typed-link patches for review.
```

The Skill uses the same seven relationship types as `obsidian-wiki`, but it
does not edit approved Markdown. Review a patch with `$learning-stage-commit`;
only `link apply <patch-id> --approve` writes typed frontmatter and a generated
Related Lessons block. The target remains unchanged and Obsidian provides its
reverse navigation as a backlink.

Drafting marks a source `partial`. When the Agent has intentionally compiled
all in-scope concepts from that source, it closes the deterministic queue item:

```bash
study-compiler source coverage ~/Documents/MyLearning \
  <source-id> --status complete
```

## Periodic learning

Inspect the configured workspace and start one bounded, recorded Agent run:

```bash
study-compiler status --json
study-compiler run start --period weekly
```

`status` reports exact watched-file deltas. `run start` scans under the workspace
lock, snapshots at most one content change, records its state under
`.study-compiler/runs/`, and returns one `next_action`. A watched source action
is prioritized over due review work so the new knowledge can be connected first.
If a configured source root is unavailable, `$learning-run` reports the missing
path and treats the scan as incomplete instead of reporting a normal `no-op`.
An unsnapshotted web URL appears as `source_snapshot`; the Agent
routes that action through `$learning-ingest` before drafting from the source.
Daily, weekly, and monthly plans include reviews within 1, 7, and 30 days
respectively, while weak recall remains actionable at every cadence.

If a prior Agent was interrupted, resume its recorded work instead of starting
a parallel run:

```bash
study-compiler run resume <run-id>
```

The `$learning-run` Skill handles exactly one action and creates the exact
run-specific Markdown `artifact_path` returned by the CLI. Its frontmatter
must be the first block and binds the artifact to `run_id`, `action_type`, and
`action_id`; the artifact is capped at 5 MiB. Source and web snapshot actions
must also produce their recorded state change. Watched source artifacts include
a validated review-cluster binding with the changed source, staged result, and
one to five member lesson slugs. It then records either outcome:

```bash
study-compiler run finish <run-id> \
  --artifact ~/Documents/MyLearning/_staging/digests/<run-id>-learning-session.md
study-compiler run fail <run-id> --message "concise failure"
```

The deterministic agenda itself cannot be reused as the finished artifact.
Finishing stores agenda/artifact hashes and sizes. A prompt-only retrieval marks
the lesson as awaiting the learner so later schedules can continue to other
work instead of repeating the same prompt; a new recorded recall satisfies the
review instead. Completion never approves or promotes a lesson; approval
remains a separate, explicit `$learning-stage-commit` action.

When a run has no source or review work, `$learning-run` checks graph health and
may route one bounded pass through `$learning-cross-link`. A completed source
run may likewise stage a strong missing relation between approved cluster
members. Scheduled passes can only create hash-bound patches; they cannot apply
them.

Codex automation, cron, or another external scheduler may trigger an Agent to
invoke `$learning-run`. Study Compiler does not install or run a background
daemon. `digest build` remains available when only a deterministic agenda is
needed.

## Skills

- `learning-ingest`: handle exceptional/manual sources and safely snapshot webpages.
- `learning-cross-link`: discover and stage typed relationships across approved lessons.
- `learning-run`: detect one delta, integrate prior knowledge, and prepare a resumable joint review.
- `lesson-draft`: integrate or synthesize sources into a lesson with intuition, examples, and retrieval practice.
- `recall-review`: score each relevant lesson in a connected retelling, diagnose gaps, and revise staged teaching.
- `learning-stage-commit`: preview and explicitly promote one draft or apply one typed-link patch.
- `learning-digest`: turn due work into a focused human-readable learning session.

## Workspace layout

```text
MyLearning/
├── .study-compiler/
│   ├── manifest.json
│   ├── learning-state.json
│   ├── source-watch.json
│   ├── workspace.lock
│   ├── runs/
│   └── link-patches/       # applied patch audit records
├── sources/
├── _staging/
│   ├── lessons/
│   ├── digests/
│   └── link-patches/       # proposals awaiting approval
├── exports/                # derived graph JSON only
├── lessons/
├── reviews/
├── index.md
└── log.md
```

Source provenance and learner mastery deliberately live in different state
files. Source workflow `status` (`new`, `staged`, or `integrated`) is also
separate from learning `coverage_status` (`pending`, `partial`, or `complete`).
Drafting makes cited sources partially covered; promotion does not pretend the
whole source is complete. A high recall score does not make a claim more
trustworthy, and a weak recall score does not discredit its source.

All multi-file source, lesson, digest, and run mutations share a fail-fast
single-writer workspace lock. `doctor` detects manifest/metadata drift, invalid
lesson or run state, and files that no longer match their recorded state.
Promotion also rechecks each cited source's mirrored metadata, owned snapshot,
byte size, and SHA-256 receipt immediately before writing an approved lesson.
Every managed path component rejects symlinks, workspace/config directories are
private (`0700`), generated state and notes are `0600`, and immutable raw
snapshots are `0400` by default. File-based snippet, lesson, retelling, and run
inputs are bounded before full decoding; lesson and run artifacts are capped at
5 MiB. Persisted timestamps use canonical UTC and review/run history may not
move backwards.

Lesson format v3 stores directed typed relationships in Obsidian-compatible
frontmatter. `graph check` rejects missing targets, self-links, duplicates, and
broken body links, while reporting `unreferenced`, `dead_ends`, and `isolated`
separately. `graph export` emits stable directed JSON; `--output graph.json`
writes only to `exports/graph.json`, never over managed state or source files.
Untyped wikilinks export with `typed: false` and `type: null`. Link proposal JSON
is bounded and duplicate-key checked; application fails if the approved source
lesson no longer matches the patch's SHA-256. A source lesson may have at most
one typed relationship to a given target.

## Validate

```bash
python3 -m unittest discover -s tests -v
study-compiler doctor ~/Documents/MyLearning --json
study-compiler graph check ~/Documents/MyLearning --json
```

`doctor` checks source hashes, snapshot ownership, metadata parity, path
containment, lesson schemas, source references, bidirectional review/state
history, run records, typed relationships, staged/applied link patches, state
JSON/UTF-8, and the generated lesson index.

See [architecture](docs/architecture.md), [specification](docs/spec.md), [threat
model](docs/threat-model.md), [acceptance criteria](docs/acceptance.md), and
[ADR-001](docs/decisions/001-recorded-agent-runs.md) and
[ADR-003](docs/decisions/003-staged-typed-cross-links.md) for the full contract.

## MVP boundary

Version 0.4 intentionally has no hosted service, model-provider dependency,
vector database, built-in scheduler/daemon, or automatic publishing. Those can
be added after the core learning loop proves useful.
