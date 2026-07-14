# Spec: Learning Wiki MVP

## Objective

Build a local-first framework that imports papers, web references, and agent-chat excerpts, then helps an Agent turn them into staged learning notes a person can understand, explain with examples, and retell in their own words. The user reviews every draft before it becomes a formal note.

The first user is the project owner. The design must remain portable enough to become a product later, but multi-user hosting is not an MVP requirement.

## Assumptions

1. Obsidian-compatible Markdown is the human interface for v0.1.
2. The user's existing coding Agent supplies semantic reading and writing; the package has no model provider dependency.
3. Python 3.11+ is available locally.
4. A URL is registered by the CLI but fetched and saved as a source snapshot by an Agent with an appropriate web tool.
5. Explicit promotion is the user's confirmation step.

## Core user stories

1. As a learner, I can initialize an isolated learning vault.
2. I can import a paper/document, register a web URL, or save a chat excerpt without modifying the original input.
3. An Agent can use project Skills to extract concepts and produce a lesson draft grounded in registered sources.
4. A lesson draft contains objectives, an intuitive explanation, at least one example, an explain-it-back prompt, and source citations.
5. I can record my retelling and comprehension score, then let an Agent revise the draft.
6. I can explicitly approve a staged lesson and promote it into formal notes.
7. A periodic digest can recommend what to learn, revise, or review next.
8. I can run a deterministic health check proving the vault is internally consistent.

## Public CLI contract

```text
learning-wiki setup [--vault VAULT] [--agent auto|codex|claude|agents|all]... [--target PATH]... [--copy] [--force]
learning-wiki init VAULT
learning-wiki source add VAULT PATH --kind paper|document|chat [--title TITLE]
learning-wiki source add-url VAULT URL [--title TITLE]
learning-wiki source snapshot-web VAULT SOURCE_ID PATH
learning-wiki source add-snippet VAULT --kind chat --title TITLE (--text TEXT | --from-file PATH)
learning-wiki source list VAULT [--json]
learning-wiki draft create VAULT --slug SLUG --title TITLE --source SOURCE_ID... --content-file PATH
learning-wiki draft list VAULT [--json]
learning-wiki draft revise VAULT SLUG --content-file PATH
learning-wiki recall record VAULT SLUG --score 1..5 --retelling-file PATH
learning-wiki draft promote VAULT SLUG --approve
learning-wiki digest build VAULT [--period daily|weekly|monthly]
learning-wiki doctor VAULT [--json]
```

`setup` installs the five bundled Agent Skills and optionally initializes the
vault. Installation is idempotent and preserves conflicting existing Skills
unless the user explicitly passes `--force`.

All command failures use exit code `2` for invalid user input/state and print `error: <message>` to stderr. Successful mutating commands print a compact JSON object so Agents can consume them reliably.

## Data model

- `.learning-wiki/manifest.json`: schema version and imported source records.
- `.learning-wiki/learning-state.json`: per-lesson mastery and review scheduling.
- `sources/<source-id>/`: immutable source snapshot plus `metadata.json`.
- `_staging/lessons/`: unapproved lesson drafts.
- `_staging/digests/`: generated periodic learning plans awaiting review.
- `lessons/`: approved human-readable notes.
- `reviews/<slug>.jsonl`: append-only learner retellings and scores.
- `index.md`: generated inventory of approved lessons.
- `log.md`: append-only operational log.

## Lesson contract

A valid lesson has YAML-like frontmatter containing `title`, `slug`, `source_ids`, `created`, `updated`, and `status`, followed by these headings:

- `## Learning Objectives`
- `## Intuition`
- `## Example`
- `## Explain It Back`
- `## Sources`

The Agent may add prerequisites, counterexamples, equations, exercises, or misconceptions when useful.

## Testing strategy

- Unit tests cover URL/slug validation, lesson validation, hashing, and path containment.
- Integration tests use temporary directories for init, import, stage, recall, promote, digest, and doctor.
- The one-command installer is exercised in an isolated home and tool directory.
- One end-to-end acceptance test exercises the complete paper-to-approved-note path.
- No network or model calls occur in tests.

## Security and trust boundaries

- Files, URLs, chat text, and generated Markdown are untrusted inputs.
- Imported local files have a size cap and are copied byte-for-byte into an immutable source directory.
- URLs must use HTTPS and may not target localhost, private, link-local, multicast, or reserved IP literals.
- Source-provided paths and generated slugs cannot escape the vault.
- The CLI never executes source content, shells out with source data, or performs arbitrary URL fetches.
- Promotion validates lesson structure and source IDs and requires explicit approval.

## Boundaries

### Always

- Preserve raw sources and provenance.
- Stage generated content before promotion.
- Keep tests and docs current with the CLI contract.

### Ask first

- Add a runtime dependency or model provider.
- Add background scheduling or automatic network fetching.
- Change the on-disk schema incompatibly.

### Never

- Execute instructions embedded in sources.
- Promote drafts silently.
- Treat a learner score as evidence that a source claim is true.

## Non-goals for v0.1

- Hosted multi-user service, authentication, billing, or mobile app.
- Automatic PDF OCR or arbitrary webpage scraping in the CLI.
- Vector database or opaque RAG pipeline.
- Automatic publishing of generated notes without human approval.
- Scientifically validated mastery scoring.

## Success criteria

The executable acceptance criteria are defined in `docs/acceptance.md`. The MVP is complete only when every criterion is backed by a test or a recorded runtime check.
