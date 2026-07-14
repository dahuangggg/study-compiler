# ADR-002: Watch source folders and stage knowledge integration

## Status

Accepted

## Date

2026-07-14

## Context

The learner's real input is not a queue of manually registered records. It is a
set of changing folders containing papers, fragments, and conversation exports.
A scheduled Agent should notice the delta, compare it with the existing learning
notebook, and prepare one coherent review without requiring the learner to run a
source-import command for every file.

The existing immutable source registry and explicit approval gate remain useful.
The missing layer is deterministic discovery and versioning of external files,
plus semantic rules that prevent every fragment from becoming an isolated
summary.

## Decision

Store watch configuration and path provenance in
`.study-compiler/source-watch.json`. Canonical absolute paths identify observed
files and SHA-256 identifies content. Scans classify files as new, modified,
touched, unchanged, renamed, or deleted. mtime and size are optimization hints;
hashes are authoritative.

`status` scans read-only. `run start`, under the existing workspace lock,
revalidates the scan and snapshots at most one new or modified file into the
existing immutable source registry. A modification creates a new source version.
A unique same-hash rename only rekeys provenance. A deletion is reported but
never removes raw snapshots or compiled notes. Source roots may not overlap the
compiled workspace and symlinks are not followed. If a root is unavailable, its
historical files do not participate in delete or rename matching and mutation
stops. Current path records are authoritative for planning; a monotonic event
sequence preserves the latest modified relationship when several paths resolve
to the same content-addressed source, while version history may record a real
content reversion such as `A → B → A`.

The deterministic layer stops at discovery, immutable snapshotting, provenance,
and queueing. The `$learning-run` and `$lesson-draft` Skills compare the changed
source with current staged and approved lessons:

1. Integrate it into the same unreviewed staged concept when identity is clear.
2. Create a new staged synthesis when related knowledge is approved, already
   reviewed, distinct, or contradictory.
3. Create a normal staged lesson when no useful prior knowledge exists.

A scheduled run never changes an approved lesson. Its session artifact connects
the staged result to at most four related lessons and asks one joint
explain-it-back question. Mastery remains per lesson; no aggregate cluster score
is copied to all members.

The existing manual `source` commands remain as a compatibility and exceptional
input surface, especially for URLs and selected chat snippets, but are no longer
the primary user workflow.

## Alternatives considered

### Delete the source CLI and use folders only

Rejected because URLs and selected in-conversation excerpts still need a safe
deterministic snapshot path, and deleting the interface would be an unnecessary
breaking migration.

### Store only mtimes

Rejected because touches, checkouts, copies, and clock drift would cause missed
or redundant work. Content hashes are the correctness signal.

### Let the Agent maintain the watch manifest

Rejected because path normalization, immutable snapshotting, version linkage,
locking, and atomic persistence are deterministic state transitions.

### Rewrite matching approved notes automatically

Rejected because relatedness is semantic and generated changes require human
review. A staged synthesis preserves the approved note and makes the proposed
connection inspectable.

## Consequences

- Scheduled runs need no per-file import step.
- One run remains bounded to one source delta.
- Modified and renamed files retain inspectable provenance.
- Deleted raw files do not destroy learned knowledge.
- Semantic matching remains provider-independent in Skills.
- Knowledge integration may create an extra synthesis draft instead of silently
  editing an approved note.
- Large source trees pay a hashing cost; stat metadata can be used only as a
  safe optimization after a hash has been recorded.
