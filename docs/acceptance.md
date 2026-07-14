# MVP Acceptance Criteria

## A. Independent project

- [x] The project exists at `~/Github/study-compiler` with its own Git history.
- [x] No Git remote, subtree, copied `.git`, or fork relationship points to `obsidian-wiki`.
- [x] All implementation and Skills are original files in this repository.

## B. Workspace initialization

- [x] `study-compiler init <temp-workspace>` creates every documented directory and valid state file.
- [x] Re-running `init` is safe and does not discard existing data.

## B2. Automatic installation

- [x] `./install.sh --workspace <path>` installs the CLI, all seven Skills, initializes the workspace, and saves it as the default for `status` and `run`.
- [x] Agent skill directories can be auto-detected or selected explicitly for Codex, Claude, shared Agents, or a custom target.
- [x] Interactive installation supports arrow-key navigation, space to toggle, and Enter to confirm Agent targets.
- [x] Human-readable output is the default while `--json` remains available for Agents and scripts.
- [x] Re-running installation is idempotent; conflicting existing Skills are preserved unless `--force` is explicit.
- [x] A built wheel contains all Skill instruction and Agent metadata files.

## C. Three source types

- [x] A local paper/document is copied into an immutable source snapshot and hashed.
- [x] An HTTPS web URL is registered without the CLI fetching it, then accepts an Agent-fetched immutable snapshot.
- [x] A chat excerpt is saved as an immutable source snapshot.
- [x] Duplicate content is detected and returns the existing source ID.
- [x] Unsafe URL schemes, private IP literals, oversized files, and escaping paths are rejected.
- [x] Source workflow status and learning coverage are independent; new sources start `pending`, drafting makes them `partial`, and explicit completion removes them from source work.
- [x] Manifest source records and `sources/<id>/metadata.json` remain identical.
- [x] Source bytes are bounded, copied, and hashed from one descriptor; reserved filenames cannot overwrite metadata.

## C2. Watched source folders

- [x] `setup --source-root` stores canonical, non-overlapping, external source roots.
- [x] `status` read-only scans supported files and reports new, modified, touched, unchanged, renamed, deleted, skipped, and unavailable-root states.
- [x] SHA-256 is authoritative; mtime-only changes do not create a new source.
- [x] `run start` snapshots at most one content delta without a manual source-import step and prioritizes it over due review work.
- [x] Modified files create a new source version while retaining the old snapshot and previous source ID.
- [x] A unique same-hash rename rekeys provenance; ambiguous moves remain new plus deleted.
- [x] Deletion is report-only, duplicate content reuses one snapshot, and hidden, symlinked, unsupported, or oversized files are not ingested.
- [x] Unavailable roots cannot create false deletion/rename events; scan-to-copy file or parent-symlink swaps and hash changes fail before registration.
- [x] Current watch records and monotonic event order select the latest multi-path change, while `A → B → A` remains a valid chronological version history.

## D. Learning draft

- [x] An Agent-facing Skill defines how to turn sources into a pedagogical lesson.
- [x] `draft create` rejects missing sources or missing required teaching sections.
- [x] `draft create` rejects unsnapshotted sources and requires the `Sources` section to contain every cited source ID.
- [x] A valid lesson is written only to `_staging/lessons`.
- [x] The staged lesson contains an explanation, example, explain-it-back prompt, and provenance.
- [x] `draft revise --add-source` can integrate new provenance into an unreviewed staged lesson and updates lesson state plus source links atomically under the workspace lock.
- [x] A reviewed or awaiting-response staged lesson cannot silently gain new source scope; the Skill creates a separate synthesis instead.

## E. Recall and approval

- [x] `recall record` appends the learner's retelling and a score from 1 to 5.
- [x] Learning state keeps mastery separate from source metadata.
- [x] Promotion without `--approve` fails.
- [x] Approved promotion moves the lesson to `lessons/` and updates index, log, and learning state.

## F. Periodic learning output

- [x] `digest build` produces a staged daily, weekly, or monthly learning agenda.
- [x] The agenda includes relevant staged work, unprocessed sources, and due/weak lessons.
- [x] Daily, weekly, and monthly periods use 1-, 7-, and 30-day upcoming-review horizons.
- [x] An unsnapshotted web URL is emitted as `source_snapshot`, not as draftable source content.
- [x] A Skill defines how the Agent converts the agenda into human-readable learning guidance.

## F2. Recorded Agent runs

- [x] `status` uses the configured workspace by default and reports actionable sources, unsnapshotted URLs, staged lessons, due reviews, an active run, and the last failure.
- [x] `run start` records one active or no-op run under `.study-compiler/runs/` and rejects a second active run.
- [x] `run resume` idempotently returns an interrupted active run without creating another record.
- [x] `$learning-run` requires the exact run-bound artifact; `run finish` validates leading run/action frontmatter, a 5 MiB cap, action completion, receipts, and a healthy workspace.
- [x] Prompt-only review work becomes `awaiting_response`, while a recall recorded after run start satisfies the review without asking twice.
- [x] `run fail` records a bounded failure message that remains visible to later status checks.
- [x] Run agendas and result artifacts are bound to their exact run ID; terminal timestamps cannot precede start, and persisted failure text cannot contain terminal controls.
- [x] Completing a run never approves a lesson; promotion remains a separate explicit action.
- [x] External schedulers trigger the Agent protocol; Study Compiler installs no background daemon.
- [x] A watched-source run requires a validated review-cluster artifact with one staged result, at most five total members, an integrated example, and a joint explain-it-back prompt.
- [x] Cluster members become awaiting-response, while mastery remains per lesson rather than one aggregate score.

## F3. Durable lesson relationships

- [x] Lesson format v3 stores directed relationships using the seven `obsidian-wiki` relationship types and canonical `[[lessons/<slug>]]` targets.
- [x] `$learning-cross-link` discovers candidates metadata-first, scores them, skips weak proposals, and stages bounded structured patches without editing approved notes.
- [x] `link stage` is additive and idempotent; it rejects missing, unapproved, duplicate, self, malformed, or ambiguous targets.
- [x] One source can have at most one typed relationship to a target; changing its type requires a future explicit replacement workflow rather than a second additive edge.
- [x] `link apply <patch-id> --approve` binds approval to one exact source hash, preserves teaching content, provenance, learner state, and the target lesson, and archives an audit record.
- [x] Draft promotion rejects manually injected relationships, preserving the independent link-patch approval boundary.
- [x] Interrupted link application can be retried safely; a stale or conflicting source fails closed.
- [x] Obsidian navigation is written as a controlled Related Lessons block, while reverse navigation relies on backlinks rather than fabricated reverse relationship types.
- [x] `graph check` ignores code examples, preserves direction and canonical paths, rejects broken links, and reports unreferenced, dead-end, and isolated lessons separately.
- [x] `graph export` emits deterministic directed JSON, writes only under `exports/` while holding the workspace lock, and cannot overwrite manifests, sources, lessons, or patches; `doctor` reuses the graph and patch validators.
- [x] Scheduled runs may stage a bounded link patch on no-op or after related source work, but cannot apply or approve it.

## G. Integrity and quality

- [x] `doctor` detects malformed JSON/UTF-8, missing or cross-source snapshots, hash, metadata, and watch-path drift, missing review history, invalid drafts or learning state, unknown source IDs, invalid run/cluster records, stale index entries, broken relationship graphs, invalid link patches, and path escapes.
- [x] A fail-fast single-writer lock prevents concurrent Study Compiler mutations from interleaving.
- [x] Every managed path component rejects symlinks, orphan raw snapshots are never overwritten, and private workspace/config/source permissions are enforced.
- [x] File-based snippets, lesson bodies, retellings, and run artifacts are bounded before full decoding or processing.
- [x] Proposal and frontmatter JSON reject duplicate keys, non-finite values, pathological numbers/nesting, null relationship lists, and ambiguous schema-number types without tracebacks.
- [x] Promotion revalidates mirrored source metadata and current snapshot hash/size before writing; legacy lessons require explicit revision first.
- [x] Persisted timestamps are canonical UTC, recall history cannot move backwards, and corrupt enum/boolean/path types fail as user-facing state errors.
- [x] `doctor --json` returns stable machine-readable results.
- [x] All tests pass with no skipped tests.
- [x] An end-to-end temporary-workspace run passes from initialization through approved note and clean doctor report.
- [x] Documentation covers architecture, commands, trust boundaries, and the human approval workflow.

## Verification commands

```bash
./install.sh --help
python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m study_compiler --help
PYTHONPATH=src python3 -m study_compiler status --help
PYTHONPATH=src python3 -m study_compiler run --help
PYTHONPATH=src python3 -m study_compiler link --help
PYTHONPATH=src python3 -m study_compiler graph --help
PYTHONPATH=src python3 -m study_compiler doctor <acceptance-workspace> --json
git status --short
git remote -v
```
