# Architecture

## Design thesis

Study Compiler separates reliable mechanics from semantic teaching judgment:

```text
watched papers / notes / chat exports
                |
                v
    delta scan + immutable source registry
                |
                v
     Agent Skills: extract and teach
                |
                v
         staged lesson drafts
                |
        read + explain it back
                |
                v
      explicit human approval gate
                |
                v
       approved learning notes
                |
                v
 staged typed-link patches + explicit approval
                |
                v
   periodic review and learning digest
```

## Layers

### 1. Source layer

The CLI scans configured, external read-only source roots and snapshots one
changed local input per run; low-level commands still record exceptional local
inputs and web references. Every source receives
a stable ID derived from its kind and content hash. The manifest stores
provenance, hash, media type, import time, snapshot path, operational `status`,
and learning `coverage_status`. Status (`new`, `staged`, `integrated`) records
where the source is in the workflow; coverage (`pending`, `partial`, `complete`)
records how much of it has been taught. Re-importing identical content is
idempotent, and `sources/<id>/metadata.json` must exactly match its manifest
record.

`.study-compiler/source-watch.json` maps canonical external paths to their
current and historical source IDs. SHA-256 is authoritative. Same-content rename
rekeys provenance, modification creates a new immutable version, and deletion is
report-only. Source roots may not overlap the compiled workspace and scanner
symlinks are ignored. The current path records, not historical manifest fields,
drive watched planning; a monotonic event sequence selects the latest event when
multiple paths share one content-addressed source. Unavailable roots stop
mutation and are excluded from deletion and rename matching.

### 2. Learning compiler layer

Markdown Skills tell an Agent how to:

- identify concepts and prerequisites;
- choose a teaching form based on the material;
- explain with examples and boundaries;
- ask the learner to retell the idea;
- search current staged and approved lessons before creating a new note;
- integrate unreviewed staged knowledge or create a staged synthesis and joint
  review prompt;
- diagnose gaps without confusing mastery with factual confidence.

Skills cannot directly promote a note. They invoke the deterministic CLI contract.

The `$learning-cross-link` Skill also owns semantic graph discovery. It builds a
metadata-first registry, scores plausible connections, and submits only bounded
structured proposals. It never edits relationships or applies a patch.

### 3. Review layer

All lessons begin in `_staging/lessons`. The CLI validates required sections,
registered source IDs, available snapshots, and that the `Sources` section
contains every cited ID. Learner retellings are append-only review records.
Explicit promotion moves a valid draft to `lessons/` and updates state, index,
and log. Source, lesson, digest, and run mutations use one fail-fast workspace
lock so concurrent Agents cannot interleave multi-file writes.

### 4. Knowledge graph layer

Lesson format v3 stores directed `{target, type}` relationships in lesson
frontmatter, making Markdown the graph source of truth. Approved-note changes
use hash-bound JSON patches under `_staging/link-patches/`; a specific `link
apply <patch-id> --approve` updates only one source lesson's relationship
projection and archives the patch for audit. Obsidian backlinks provide reverse
navigation without fabricating a reverse typed claim.

`graph check` parses canonical lesson paths, excludes code examples, validates
typed and body links, and reports unreferenced, dead-end, and isolated nodes
separately. `doctor` reuses the same validator. `graph export` produces stable,
directed JSON; file output is confined to `exports/` and written under the same
workspace lock as graph generation.

### 5. Scheduling layer

`digest build` creates a deterministic draft agenda from actionable sources,
staged lessons, weak recall, and upcoming reviews. Daily, weekly, and monthly
periods use 1-, 7-, and 30-day review horizons. A web reference without a
snapshot becomes a `source_snapshot` action rather than draftable material.

`run start` first scans watched roots and snapshots at most one actionable delta,
then records one active or no-op run in `.study-compiler/runs/` and
returns the highest-priority action. The semantic `$learning-run` Skill routes
that action through the appropriate leaf Skill. `run resume` lets a later Agent
continue an interrupted active record. The Agent writes a run-bound artifact at
the returned path; `run finish` verifies its leading run/action binding,
5 MiB limit, action-specific postcondition, hash, size, and workspace health.
Prompt-only retrieval transitions to `awaiting_response` so it does not starve
later sources, while a new recall record completes the review directly. `run
fail` records a bounded failure message. Neither path approves a lesson. A
missing configured source root fails before planning rather than producing a
misleading no-op or partial scan.

An external scheduler such as Codex automation or cron may wake an Agent to run
this protocol. The project contains no scheduler loop or background daemon. See
[ADR-001](decisions/001-recorded-agent-runs.md). A no-op learning run may route
one bounded graph-maintenance pass through `$learning-cross-link`; it can stage
but never apply a link patch. See
[ADR-003](decisions/003-staged-typed-cross-links.md).

## Module boundaries

```text
src/study_compiler/
  cli.py          command parsing and stable output contract
  config.py       saved default-workspace resolution
  errors.py       public validation/state errors
  storage.py      safe paths and atomic JSON persistence
  state.py        single-writer lock and source-record persistence
  sources.py      import, hashing, URL registration, coverage
  source_watch.py watched-root configuration, delta scan, version sync
  lessons.py      draft validation, recall, promotion
  graph.py        typed/body link parsing, health checks, stable export
  links.py        hash-bound cross-link patch staging, approval, recovery
  digest.py       deterministic work planning and agenda generation
  runs.py         recorded run lifecycle and workspace status
  doctor.py       whole-workspace invariant checks
  installer.py    seven-Skill discovery and installation
```

## Key decisions

- **Standard library only:** minimizes supply-chain risk and keeps installation simple.
- **Files over database:** Markdown stays readable in Obsidian and JSON stays inspectable.
- **No embedded LLM client:** Skills work with Codex, Claude, Pi, or another capable Agent.
- **No arbitrary fetch in CLI:** avoids SSRF and keeps web acquisition under an observable Agent tool.
- **Explicit promotion:** AI output remains draft data until the learner approves it.
- **Explicit graph mutation:** semantic links are staged and hash-bound; only a
  patch-specific approval may change an approved lesson.
- **Confined derived export:** graph JSON can only write under `exports/`, never
  into manifests, immutable sources, staged artifacts, or approved lessons.
- **Separate mastery from evidence:** `score` describes the learner; provenance describes the claim.
- **External scheduling:** schedulers trigger the Agent; they do not bypass the
  Skill protocol or become part of the deterministic state engine.
- **Recorded bounded runs:** one active run and one single-writer lock make
  retries, failures, and no-op outcomes visible without embedding an Agent loop.
- **Private managed storage:** managed path components reject symlinks; directories
  and generated files use owner-only permissions, and raw snapshots are copied,
  bounded, and hashed from the same open descriptor. Snapshot ownership and
  chronological append-only review history are reconciled by `doctor`;
  promotion independently rechecks source metadata and snapshot receipts, and
  file inputs are capped before full UTF-8 decoding.

## Evolution path

Future versions can add provider adapters, a review UI, scheduler integrations,
and multi-user storage behind the existing CLI/data contracts. A built-in
daemon would require a new decision because it changes ownership of retries,
credentials, lifecycle, and concurrency.
