# Spec: Study Compiler MVP

## Objective

Build a local-first framework that watches the learner's existing folders of
papers, notes, and conversation records, then helps an Agent compile only the
changed material into staged learning notes a person can understand, connect to
prior knowledge, explain with examples, and retell in their own words. The user
reviews every draft before it becomes a formal note.

The first user is the project owner. The design must remain portable enough to become a product later, but multi-user hosting is not an MVP requirement.

## Assumptions

1. Obsidian-compatible Markdown is the human interface for the MVP.
2. The user's existing coding Agent supplies semantic reading and writing; the package has no model provider dependency.
3. Python 3.11+ is available locally.
4. A URL is registered by the CLI but fetched and saved as a source snapshot by an Agent with an appropriate web tool.
5. Explicit promotion is the user's confirmation step.
6. Periodic execution is triggered by an external scheduler that wakes an
   Agent; Study Compiler does not own a background daemon.
7. Watched source directories are user-owned and read-only to Study Compiler.
   Their files may change or move, but the compiler never rewrites, moves, or
   deletes them.

## Core user stories

1. As a learner, I can initialize an isolated study workspace.
2. I can configure folders containing fragmented learning material once; a
   scheduled run detects new, changed, renamed, and deleted files without a
   manual import step.
3. New and changed files are copied into immutable, versioned snapshots without
   modifying the original input.
4. An Agent can compare changed material with staged and approved lessons, then
   integrate an unreviewed staged lesson, create a synthesis lesson, or create a
   new lesson grounded in registered sources.
5. A lesson draft contains objectives, an intuitive explanation, at least one example, an explain-it-back prompt, and source citations.
6. A source-driven run groups the new lesson with a bounded set of related prior
   lessons and asks one joint explain-it-back question.
7. I can record my retelling and comprehension score, then let an Agent revise the draft.
8. I can explicitly approve a staged lesson and promote it into formal notes.
9. A periodic digest can recommend what to learn, revise, or review next.
10. I can run a deterministic health check proving the workspace is internally consistent.
11. I can inspect actionable work and record one resumable Agent run as started,
   completed, failed, or a no-op.
12. An Agent can discover missing connections across approved lessons and stage
    additive, typed cross-link patches without changing formal notes.
13. I can approve one exact cross-link patch, navigate its Obsidian backlink,
    and inspect broken, unreferenced, dead-end, and isolated graph nodes.

## Public CLI contract

```text
study-compiler setup [--workspace WORKSPACE] [--source-root PATH]... [--agent auto|codex|claude|agents|all]... [--target PATH]... [--copy] [--force] [--json]
study-compiler init WORKSPACE [--source-root PATH]...
study-compiler source add WORKSPACE PATH --kind paper|document|chat [--title TITLE]
study-compiler source add-url WORKSPACE URL [--title TITLE]
study-compiler source snapshot-web WORKSPACE SOURCE_ID PATH
study-compiler source add-snippet WORKSPACE --kind chat --title TITLE (--text TEXT | --from-file PATH)
study-compiler source list WORKSPACE [--json]
study-compiler source coverage WORKSPACE SOURCE_ID --status pending|partial|complete
study-compiler draft create WORKSPACE --slug SLUG --title TITLE --source SOURCE_ID... --content-file PATH
study-compiler draft list WORKSPACE [--json]
study-compiler draft revise WORKSPACE SLUG --content-file PATH [--add-source SOURCE_ID...]
study-compiler recall record WORKSPACE SLUG --score 1..5 --retelling-file PATH
study-compiler draft promote WORKSPACE SLUG --approve
study-compiler link stage WORKSPACE SOURCE_SLUG --proposal-file PATH
study-compiler link list WORKSPACE [--json]
study-compiler link show WORKSPACE PATCH_ID [--json]
study-compiler link apply WORKSPACE PATCH_ID --approve
study-compiler graph check WORKSPACE [--json] [--strict]
study-compiler graph export WORKSPACE [--output EXPORT_NAME]
study-compiler digest build WORKSPACE [--period daily|weekly|monthly]
study-compiler doctor WORKSPACE [--json]
study-compiler status [--workspace WORKSPACE] [--json]
study-compiler run start [--workspace WORKSPACE] [--period daily|weekly|monthly]
study-compiler run resume RUN_ID [--workspace WORKSPACE]
study-compiler run finish RUN_ID --artifact PATH [--workspace WORKSPACE]
study-compiler run fail RUN_ID --message MESSAGE [--workspace WORKSPACE]
```

`setup` installs the seven bundled Agent Skills and optionally initializes the
workspace. When a workspace is supplied, setup saves its resolved path in
`~/.study-compiler/config.json`; `status` and `run` use this default unless an
explicit `--workspace` takes precedence. Installation is idempotent and
preserves conflicting existing Skills unless the user explicitly passes
`--force`. When no target flag is supplied in an interactive terminal, setup
presents an arrow-key multi-select. Human-readable output is the default;
`--json` preserves the machine-readable contract where supported.

`--source-root` configures the complete set of watched directories for that
workspace. Omitting it preserves existing watch configuration. The `source`
subcommands remain a low-level compatibility interface for URLs, selected chat
fragments, and exceptional imports; they are not part of the normal scheduled
workflow.

Invalid user input or state uses exit code `2` and prints `error: <message>` to
stderr. A completed `doctor` check returns `1` when it finds invariant failures.
Successful mutating commands print a compact JSON object so Agents can consume
them reliably.

## Data model

- `.study-compiler/manifest.json`: schema version and imported source records.
- `.study-compiler/source-watch.json`: watched roots and canonical path-to-source
  version history.
- `.study-compiler/learning-state.json`: per-lesson mastery and review scheduling.
- `.study-compiler/runs/<run-id>.json`: active, completed, failed, and no-op Agent runs.
- `.study-compiler/link-patches/`: applied typed-link patch audit records.
- `.study-compiler/workspace.lock`: fail-fast single-writer coordination.
- `sources/<source-id>/`: immutable source snapshot plus `metadata.json`.
- `_staging/lessons/`: unapproved lesson drafts.
- `_staging/digests/`: generated periodic learning plans awaiting review.
- `_staging/link-patches/`: structured cross-link patches awaiting approval.
- `exports/`: derived graph JSON; no other command or managed state writes here.
- `lessons/`: approved human-readable notes.
- `reviews/<slug>.jsonl`: append-only learner retellings and scores.
- `index.md`: generated inventory of approved lessons.
- `log.md`: append-only operational log.

Every new source starts with `coverage_status: pending`. Drafting from a source
sets it to `partial`; promotion leaves coverage partial until coverage is
explicitly marked `complete`. This is independent of source workflow `status`
(`new`, `staged`, or `integrated`). The per-source `metadata.json` record must
remain exactly equal to the corresponding manifest record.

Watched files are keyed by canonical absolute path and compared by SHA-256.
mtime and size may avoid unnecessary work but never override the content hash.
An edit creates a new immutable source version and retains the previous source
ID. A unique same-content rename rekeys path provenance without creating a new
source. Deletion is report-only: it never removes snapshots, lessons, or
citations. Ambiguous rename candidates remain a `new` plus `deleted` pair. The
watch record's current source ID and latest event sequence are authoritative for
planning, including content reversion and duplicate content at multiple paths.
An unavailable root is an incomplete scan: its files cannot become deletion or
rename candidates, and `run start` fails closed until the root is available.

Learning state also records `awaiting_response` after a scheduled retrieval
prompt; a new `recall record` clears it.

## Lesson contract

A valid new lesson has YAML-like frontmatter containing `title`, `slug`,
`source_ids`, `created`, `updated`, `status`, `format_version: 3`, and an optional
JSON `relationships` list, followed by
these headings:

- `## Learning Objectives`
- `## Intuition`
- `## Example`
- `## Explain It Back`
- `## Sources`

The `Sources` section must contain every `source_id` declared in frontmatter.
Every cited source must be registered and have a snapshot; a registered URL
without a snapshot cannot ground a lesson. The Agent may add prerequisites,
counterexamples, equations, exercises, or misconceptions when useful.

Staged lesson drafts must have an empty `relationships` list. Promotion cannot
publish relationships injected into a draft; only a separately approved
`link apply` operation may add them to an approved lesson. Custom
Obsidian-compatible JSON frontmatter fields are preserved when that operation
rewrites the managed fields.

Each relationship is a directed `{target, type}` edge whose target is a
canonical `[[lessons/<slug>]]` wikilink. Allowed types are `extends`,
`implements`, `contradicts`, `derived_from`, `uses`, `replaces`, and
`related_to`. The deterministic layer rejects missing targets, self-links,
duplicates, multiple types from one source to the same target, and malformed
values. Untyped body links still provide navigation
and count for graph connectivity, but do not become typed claims implicitly.

## Cross-link patch and graph contract

The `$learning-cross-link` Skill owns candidate discovery and semantic scoring.
It first builds a registry from lesson metadata, then reads only plausible
candidates. Exact or strongly evidenced connections are marked `extracted`;
weaker semantic connections are marked `inferred`; ambiguous candidates are
skipped. Model output enters the CLI only as bounded structured proposal data.

`link stage` creates one additive patch for one approved source lesson. The
patch records the exact base SHA-256, normalized proposed relations, confidence,
score, short rationale, and creation time. It cannot carry Markdown, a generic
diff, deletions, source changes, or learner-state changes. Re-staging the same
proposal against the same bytes is idempotent.

`link apply` requires the exact patch ID and `--approve`. It fails when the
source lesson changed, a target disappeared, a relationship already exists, or
the patch no longer validates. On success it updates only relationship
frontmatter, the CLI-owned related-lessons block, and the lesson `updated`
timestamp; it preserves teaching sections, title, slug, sources, creation time,
approval time, mastery, and review schedule. The applied patch is retained as
audit history.

`graph check` parses canonical lesson links without collapsing paths to
basenames or treating code-fence examples as real edges. It returns typed-edge
errors, broken body links, and separate `unreferenced`, `dead_ends`, and
`isolated` lists. `--strict` makes those health findings fail the command;
`doctor` always fails on invalid/broken edges and reports isolation as warnings.
`graph export` emits deterministic directed JSON. Untyped navigation edges use
`typed: false` and `type: null`; consumers must not treat them as typed
`related_to` claims. File output is confined to the dedicated `exports/`
directory and generation plus write occur under one workspace lock.

## Planning and recorded-run contract

Before planning, `run start` scans configured source roots and automatically
snapshots at most one new or modified file under the workspace lock. Watched
source changes take priority so a scheduled run can compile them without a
manual `source add`. The deterministic planner otherwise emits additive work
items in priority order. A web
URL without a snapshot is a `source_snapshot` action. Snapshotted sources remain
actionable until their coverage is `complete`. Weak recall is always
actionable; upcoming reviews enter daily, weekly, and monthly plans within 1,
7, and 30 days respectively.

`run start` allows at most one active run, writes its record under
`.study-compiler/runs/`, and creates a deterministic agenda when work exists. A
no-work result is recorded as `no-op`. The `$learning-run` Skill handles one
returned action and must create a new, non-empty UTF-8 Markdown artifact under
`_staging/`; it may not overwrite or submit the deterministic agenda as that
artifact. The artifact is limited to 5 MiB. `run finish` requires that staged
artifact and a healthy `doctor` report. `run fail` records a concise failure so
the next Agent can diagnose it.
Finishing a run never approves or promotes a lesson; approval remains an
independent explicit action.

`run resume` is idempotent for the one active record. Every work-producing run
returns an exact `_staging/digests/<run-id>-learning-session.md` artifact path.
The artifact must begin with frontmatter that exactly identifies its `run_id`,
`action_type`, and `action_id`.
Completion requires the corresponding source lesson or web snapshot when those
are the planned actions, and stores SHA-256/size receipts for both agenda and
artifact. A prompt-only review marks the lesson as awaiting a learner response;
a recall record added after the run started satisfies the review directly.

For a watched `source` action, the Agent must search the current lesson catalog
before writing. It may add the new source to an unreviewed staged lesson, create
a staged synthesis that links related approved/reviewed lessons, or create a
new staged lesson. Approved lessons are never edited by a scheduled run. The
run artifact records the staged result and a bounded review cluster (the changed
lesson plus at most four relevant prerequisite, contrast, or due-review
lessons), with one integrated example and one joint explain-it-back prompt.

The bundled Skills are `learning-ingest`, `lesson-draft`, `recall-review`,
`learning-stage-commit`, `learning-digest`, `learning-run`, and
`learning-cross-link`.

## Testing strategy

- Unit tests cover URL/slug validation, lesson validation, hashing, and path containment.
- Source-watch tests cover new, modified, touched, unchanged, renamed, deleted,
  duplicate-content, symlink, overlap, and file-size behavior.
- Integration tests use temporary directories for init, import, stage, recall, promote, digest, and doctor.
- The one-command installer is exercised in an isolated home and tool directory.
- One end-to-end acceptance test exercises the complete paper-to-approved-note path.
- No network or model calls occur in tests.

## Security and trust boundaries

- Files, URLs, chat text, and generated Markdown are untrusted inputs.
- Watched roots must be real directories, may not overlap the compiled
  workspace, and are scanned without following symlinks or hidden directories.
- Imported local files have a size cap and are copied byte-for-byte into an immutable source directory.
- File-based snippets, lesson bodies, and retellings are bounded before full
  UTF-8 decoding; lesson bodies are limited to 5 MiB.
- URLs must use HTTPS and may not target localhost, private, link-local, multicast, or reserved IP literals.
- Source-provided paths and generated slugs cannot escape the workspace.
- The CLI never executes source content, shells out with source data, or performs arbitrary URL fetches.
- Scanner state is updated only after the exact snapshotted bytes are hashed;
  source changes during a run cannot overwrite an older immutable version.
- Promotion validates lesson structure and source IDs, rechecks current source
  metadata plus snapshot SHA-256/size receipts, rejects legacy format until it
  is revised, and requires explicit approval.
- Multi-file source, lesson, digest, and run mutations share a non-blocking
  single-writer workspace lock.
- Run completion accepts only bounded staged Markdown with exact leading
  binding frontmatter, rejects the deterministic agenda, and cannot invoke
  approval.
- Link proposals are bounded structured JSON, never executable diffs; applying
  one requires explicit approval and an unchanged approved-lesson hash.
- Proposal and lesson-frontmatter JSON reject duplicate keys, non-finite values,
  pathological nesting/numbers, null relationship lists, and ambiguous numeric
  schema versions with stable user-facing errors.
- The CLI owns the generated Related Lessons block and refuses malformed or
  duplicate block markers instead of guessing how to rewrite them.
- Managed path components reject symlinks. Workspace and
  config directories use `0700`, mutable private files use `0600`, and immutable
  raw snapshots use `0400`.
- Every snapshot path must stay directly inside its own source directory, and
  canonical UTC review timestamps in learning state must reconcile to
  chronological append-only history.

## Boundaries

### Always

- Preserve raw sources and provenance.
- Stage generated content before promotion.
- Keep tests and docs current with the CLI contract.

### Ask first

- Add a runtime dependency or model provider.
- Add a built-in scheduler/daemon or automatic network fetching.
- Change the on-disk schema incompatibly.

### Never

- Execute instructions embedded in sources.
- Promote drafts silently.
- Treat a learner score as evidence that a source claim is true.

## Non-goals for the MVP

- Hosted multi-user service, authentication, billing, or mobile app.
- Automatic PDF OCR or arbitrary webpage scraping in the CLI.
- Vector database or opaque RAG pipeline.
- Automatic publishing of generated notes without human approval.
- Automatic application or deletion of cross-links without patch-specific
  human approval.
- A resident scheduler, background daemon, or implicit run retry loop.
- Scientifically validated mastery scoring.

## Success criteria

The executable acceptance criteria are defined in `docs/acceptance.md`. The MVP is complete only when every criterion is backed by a test or a recorded runtime check.
