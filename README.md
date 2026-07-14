# Study Compiler

Study Compiler is a local-first learning workflow for papers, webpages, and Agent conversations. It combines a deterministic CLI with Agent Skills so source handling stays reliable while teaching style can adapt to the material and learner. It is not a general-purpose wiki: it compiles source material into teachable lessons, recall practice, and reviewed notes.

The goal is not to accumulate summaries. The goal is to produce notes a person can understand, explain with examples, and retell in their own words.

## How it works

```text
source material
    → immutable source registry
    → Agent-written lesson draft
    → learner explain-it-back attempt
    → Agent diagnosis and revision
    → explicit human approval
    → formal Obsidian-compatible note
    → periodic review agenda
```

Generated lessons never enter the formal notebook automatically. They stay in `_staging/` until the learner explicitly approves them.

## Install

Study Compiler requires Python 3.11 or newer and has no runtime dependencies.

From a cloned checkout, one command installs the CLI, discovers your local Agent,
installs all five bundled Skills, and initializes a study workspace:

```bash
cd ~/Github/study-compiler
./install.sh --workspace ~/Documents/MyLearning
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
study-compiler setup --agent auto --workspace ~/Documents/MyLearning
```

`setup` prints a concise human-readable summary by default. Automation can keep
the stable machine-readable response with `study-compiler setup ... --json`.

## Quick start

Point the shell at the workspace created during installation:

```bash
export STUDY_COMPILER_WORKSPACE=~/Documents/MyLearning
```

Import a paper:

```bash
study-compiler source add "$STUDY_COMPILER_WORKSPACE" paper.pdf \
  --kind paper \
  --title "Attention Is All You Need"
```

Register a webpage. The deterministic CLI does not fetch arbitrary URLs; let an Agent fetch and clean it, then attach the immutable snapshot:

```bash
study-compiler source add-url "$STUDY_COMPILER_WORKSPACE" \
  https://example.com/article \
  --title "Example article"

study-compiler source snapshot-web "$STUDY_COMPILER_WORKSPACE" \
  <returned-source-id> /tmp/example-article.md
```

Save a selected Agent chat fragment:

```bash
study-compiler source add-snippet "$STUDY_COMPILER_WORKSPACE" \
  --kind chat \
  --title "Attention explanation" \
  --from-file /tmp/attention-chat.md
```

Then ask an Agent to use the Skills:

```text
Use $lesson-draft to teach me the registered attention sources.
Use $recall-review to check my explanation.
Use $learning-stage-commit to review the draft before promotion.
```

The normal learning loop is: import material with `$learning-ingest`, ask
`$lesson-draft` to teach it, explain it back with `$recall-review`, and only
then use `$learning-stage-commit` to approve the formal note.

## Periodic learning

Build a deterministic agenda at any cadence:

```bash
study-compiler digest build "$STUDY_COMPILER_WORKSPACE" --period weekly
```

Then invoke `$learning-digest` from a Codex automation, cron-triggered Agent session, or manually. Scheduled runs may create staged guidance but cannot approve notes.

The agenda prioritizes weak or due recall, staged lessons, blocking prerequisites, and finally new material. This prevents a growing source inbox from becoming an endless summary feed.

## Skills

- `learning-ingest`: register immutable sources and safely snapshot webpages.
- `lesson-draft`: turn sources into a lesson with intuition, examples, and retrieval practice.
- `recall-review`: score a retelling, diagnose gaps, and revise a staged lesson.
- `learning-stage-commit`: preview and explicitly promote one approved draft.
- `learning-digest`: turn due work into a focused human-readable learning session.

## Workspace layout

```text
MyLearning/
├── .study-compiler/
│   ├── manifest.json
│   └── learning-state.json
├── sources/
├── _staging/
│   ├── lessons/
│   └── digests/
├── lessons/
├── reviews/
├── index.md
└── log.md
```

Source provenance and learner mastery deliberately live in different state files. A high recall score does not make a claim more trustworthy, and a weak recall score does not discredit its source.

## Validate

```bash
python3 -m unittest discover -s tests -v
study-compiler doctor "$STUDY_COMPILER_WORKSPACE" --json
```

`doctor` checks source hashes, path containment, lesson schemas, source references, review records, state JSON, and the generated lesson index.

See [architecture](docs/architecture.md), [specification](docs/spec.md), [threat model](docs/threat-model.md), and [acceptance criteria](docs/acceptance.md) for the full contract.

## MVP boundary

Version 0.2 intentionally has no hosted service, model-provider dependency, vector database, or automatic publishing. Those can be added after the core learning loop proves useful.
