# Learning Wiki

Learning Wiki is a local-first learning compiler for papers, webpages, and Agent conversations. It combines a deterministic CLI with Agent Skills so source handling stays reliable while teaching style can adapt to the material and learner.

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

Learning Wiki requires Python 3.11 or newer and has no runtime dependencies.

```bash
cd ~/Github/learning-wiki
python3 -m pip install -e .
learning-wiki --help
```

To expose the bundled Skills to Codex, symlink the ones you want into its skill directory:

```bash
mkdir -p ~/.codex/skills
for skill in "$PWD"/skills/*; do
  ln -sfn "$skill" "$HOME/.codex/skills/$(basename "$skill")"
done
```

Other Agents can point their own skill-discovery directory at the same canonical `skills/` folders.

## Quick start

Initialize a learning vault:

```bash
learning-wiki init ~/Documents/MyLearning
export LEARNING_WIKI_VAULT=~/Documents/MyLearning
```

Import a paper:

```bash
learning-wiki source add "$LEARNING_WIKI_VAULT" paper.pdf \
  --kind paper \
  --title "Attention Is All You Need"
```

Register a webpage. The deterministic CLI does not fetch arbitrary URLs; let an Agent fetch and clean it, then attach the immutable snapshot:

```bash
learning-wiki source add-url "$LEARNING_WIKI_VAULT" \
  https://example.com/article \
  --title "Example article"

learning-wiki source snapshot-web "$LEARNING_WIKI_VAULT" \
  <returned-source-id> /tmp/example-article.md
```

Save a selected Agent chat fragment:

```bash
learning-wiki source add-snippet "$LEARNING_WIKI_VAULT" \
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

## Periodic learning

Build a deterministic agenda at any cadence:

```bash
learning-wiki digest build "$LEARNING_WIKI_VAULT" --period weekly
```

Then invoke `$learning-digest` from a Codex automation, cron-triggered Agent session, or manually. Scheduled runs may create staged guidance but cannot approve notes.

The agenda prioritizes weak or due recall, staged lessons, blocking prerequisites, and finally new material. This prevents a growing source inbox from becoming an endless summary feed.

## Skills

- `learning-ingest`: register immutable sources and safely snapshot webpages.
- `lesson-draft`: turn sources into a lesson with intuition, examples, and retrieval practice.
- `recall-review`: score a retelling, diagnose gaps, and revise a staged lesson.
- `learning-stage-commit`: preview and explicitly promote one approved draft.
- `learning-digest`: turn due work into a focused human-readable learning session.

## Vault layout

```text
MyLearning/
├── .learning-wiki/
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
learning-wiki doctor "$LEARNING_WIKI_VAULT" --json
```

`doctor` checks source hashes, path containment, lesson schemas, source references, review records, state JSON, and the generated lesson index.

See [architecture](docs/architecture.md), [specification](docs/spec.md), [threat model](docs/threat-model.md), and [acceptance criteria](docs/acceptance.md) for the full contract.

## MVP boundary

Version 0.1 intentionally has no hosted service, model-provider dependency, vector database, or automatic publishing. Those can be added after the core learning loop proves useful.

