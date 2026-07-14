# ADR-003: Stage typed cross-links before changing approved lessons

## Status

Accepted

## Date

2026-07-14

## Context

Source-driven runs already compare changed material with prior lessons and form
temporary review clusters. Those clusters disappear into run history, however,
so the approved notebook still lacks a durable, inspectable knowledge graph.
The desired behavior matches the useful part of `obsidian-wiki`: an Agent
discovers semantic connections, while deterministic tooling validates typed
relationships, broken targets, and graph health.

Study Compiler has a stronger publication boundary than a general wiki. A
scheduled Agent may not silently rewrite an approved lesson, and semantic model
output must not be accepted as an arbitrary diff or replacement document.

## Decision

Lesson format version 3 adds an optional `relationships` frontmatter value. It
is a JSON list of directed `{target, type}` objects so it remains compatible
with the existing line-oriented parser while using the same seven relationship
types as `obsidian-wiki`: `extends`, `implements`, `contradicts`,
`derived_from`, `uses`, `replaces`, and `related_to`.
Drafts keep this list empty: lesson promotion is not relationship approval.
Each source-target pair has at most one typed relationship.

The `$learning-cross-link` Skill owns semantic discovery. It builds a cheap
lesson registry, reads only plausible candidates, scores each proposed
connection, skips ambiguous scores, and submits structured data to the CLI. The
CLI never guesses a relationship type.

Cross-links to approved lessons use a separate patch lifecycle:

1. `link stage` validates one source lesson and one or more additive typed
   relationships, records the current lesson SHA-256, and writes a bounded JSON
   patch under `_staging/link-patches/`.
2. Scheduled runs may create patches but may not apply them.
3. `link apply <patch-id> --approve` rechecks the exact base hash and every
   target, then changes only the source lesson's relationship metadata and a
   CLI-owned `Related Lessons` block. The target gets an ordinary Obsidian
   backlink without an invented reverse semantic edge.
4. Applied patches move to `.study-compiler/link-patches/` as audit history.

Patches are additive only. A stale patch fails closed rather than rebasing.
Changes to explanations, examples, objectives, provenance, or learner state are
not link patches; they remain staged lesson or synthesis work.

`graph check` and `doctor` share one deterministic graph validator. It preserves
direction, rejects invalid types, missing targets, self-links, and duplicate
edges, and reports three distinct health signals:

- `unreferenced`: no incoming lesson links;
- `dead_ends`: no outgoing lesson links;
- `isolated`: neither incoming nor outgoing lesson links.

`graph export` emits stable, directed JSON for external inspection. Untyped body
links remain distinguishable as `typed: false`, `type: null`. File output is
confined to `exports/` and cannot overwrite managed state. It does not claim to
infer communities or semantics.

## Alternatives considered

### Let the scheduled Agent edit approved Markdown directly

Rejected because it collapses semantic discovery and publication into one
unreviewable step and makes prompt-injected or stale model output authoritative.

### Store relations only in a separate JSON graph

Rejected because Obsidian should be able to navigate the compiled Markdown
without a proprietary sidecar, and duplicated graph state would drift from the
lesson files.

### Automatically write reverse typed edges

Rejected because most types are directional. A link from source to target
already creates an Obsidian backlink; a reverse semantic claim requires its own
evidence and proposal.

### Treat every wikilink as a typed `related_to` edge

Rejected because navigation links and semantic claims are different. Untyped
body links still count for graph connectivity, but only explicit frontmatter
entries appear as typed relationships.

## Consequences

- The notebook gains durable typed relationships and graph health checks.
- Cross-link discovery remains provider-independent and improves with the
  Agent Skill without destabilizing the CLI.
- Approved notes can evolve safely, but every structural change requires a
  specific patch approval.
- Stale patches require regeneration after any intervening lesson edit.
- Obsidian backlinks provide reverse navigation without fabricating reverse
  relationship types.
