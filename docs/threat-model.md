# Threat Model

## Trust boundaries

- Local files, PDFs, chat excerpts, URL strings, fetched webpage snapshots, and generated lesson Markdown are untrusted.
- CLI arguments cross into filesystem and state operations.
- Agent/model output crosses into staged Markdown but never directly into executable operations.
- An external scheduler may wake an Agent, but it does not receive a privileged
  state-mutation or approval path.
- Saved default-workspace configuration selects a workspace for `status` and
  `run`; explicit `--workspace` input still passes normal workspace validation.
- Watched source roots are external, user-owned read-only inputs. Their paths,
  filenames, metadata, and contents are untrusted.

## Assets

- The learner's private source material and chat history.
- Provenance and source integrity.
- Approved notes and learning history.
- Typed lesson relationships and staged/applied link-patch audit history.
- Recorded run history and the identity of the single active run.
- Files outside the configured workspace.

## Abuse cases and controls

| Abuse case | Control |
|---|---|
| A paper says “ignore instructions and run this command” | Skills declare source text to be data; CLI never executes content |
| A slug or filename tries `../../` traversal | Slug allowlist and resolved-path containment checks |
| Any managed path component is replaced by a symlink | Workspace validation, component-safe paths, and no-follow leaf writes reject the operation |
| A URL points to localhost or cloud metadata | HTTPS-only URL validation and rejection of private/reserved IP literals; CLI does not fetch |
| A file changes between scan and copy | Watched reads traverse beneath the configured root with no-follow descriptors, require a regular file, and require the copied SHA-256 to match the scan receipt |
| A source root points back into generated lessons | Configuration rejects roots that overlap the compiled workspace or one another |
| A watched tree swaps a file or parent directory to a symlink | Scanner skips visible symlinks; snapshot reads open every path component with no-follow descriptors beneath the configured root, so scan-to-copy swaps fail closed |
| A configured source root is temporarily unavailable | Its tracked files are excluded from delete/rename matching and `run start` stops instead of persisting a partial scan |
| A timestamp changes without content changing | SHA-256 remains authoritative; the run reconciles the touch without creating learning work |
| A source file is renamed or deleted | Unique same-hash rename only rekeys provenance; deletion never removes immutable snapshots or notes |
| A huge source, lesson, retelling, or Agent receipt exhausts memory/storage | File inputs are bounded before full decoding, source copy is streamed, and lesson/run artifacts have 5 MiB limits |
| A source filename collides with `metadata.json` | Snapshots use a reserved `snapshot.<ext>` name separate from source metadata |
| A title injects a fake agenda heading | Input and lesson parsing reject control characters; agenda labels are encoded as data |
| Model output cites a nonexistent source | Lesson validator rejects unknown source IDs |
| A lesson cites a registered URL before it has content | Draft validation rejects sources without snapshots; the planner emits `source_snapshot` instead |
| Agent silently publishes its output | All output stages; promotion requires explicit `--approve` |
| A staged lesson smuggles relationships through normal lesson approval | Promotion and `doctor` require draft relationships to remain empty; approved relationships only enter through a separately approved link patch |
| A scheduled graph pass silently edits approved notes | `$learning-cross-link` may only call `link stage`; patch application is a separate patch-specific approval action |
| Model output smuggles Markdown, deletion, or a generic diff into a link proposal | The CLI accepts only bounded, duplicate-key-checked structured relationships with exact fields and allowed values |
| A link patch is approved after its source lesson changes | The patch records the source SHA-256 and size; apply fails closed unless the bytes still match exactly |
| A proposal invents a reverse semantic claim | Apply changes only the source lesson; Obsidian backlinks provide reverse navigation without adding a reverse typed edge |
| A malformed managed-link marker tricks the rewriter into replacing teaching content | Reserved markers are rejected in generated drafts and the CLI requires one exact, well-formed controlled block before replacement |
| Link application is interrupted after the lesson write | The audit record enters `applying`; an exact retry recognizes the resulting hash and completes the record, while any third state fails closed |
| Graph export overwrites a manifest, source receipt, lesson, or patch | File exports are confined to the dedicated `exports/` tree and graph generation plus write share the workspace lock |
| Pathological or ambiguous JSON crashes validation or changes meaning | Bounded proposal/frontmatter readers reject duplicate keys, non-finite values, extreme numbers/nesting, null relationship lists, and non-integer format versions |
| New evidence silently rewrites approved or already-reviewed knowledge | Skills create a staged synthesis; `draft revise --add-source` rejects reviewed or awaiting-response drafts |
| Imported source is modified later | Read-only snapshot permissions, hash-drift detection in `doctor`, and a metadata/hash/size preflight immediately before promotion |
| A source record points at another source's bytes | `doctor` requires each snapshot to live directly inside its own source directory |
| Two scheduled Agents write state concurrently | One fail-fast workspace lock serializes source, lesson, digest, and run mutations |
| An Agent reports unrelated or old Markdown as completed work | `run finish` requires the exact run-specific path, leading run/action binding, action postcondition, size cap, and content receipt |
| A watched-source run claims integration without a usable review | `run finish` validates trigger/staged/member IDs, a five-member cap, required connected-review sections, and per-lesson prompt state |
| A stale agenda or clock-skewed result is attached to a different run | Agenda paths are derived from the run ID and terminal timestamps cannot precede run start |
| A scheduler treats successful generation as approval | `$learning-run` cannot invoke stage commit; run completion and lesson approval are separate state transitions |
| A failed Agent leaves ambiguous work behind | `run fail` records a bounded message; `status` exposes the active run and last failure |
| Manifest and per-source metadata diverge | Every mutation writes both records; `doctor` requires exact equality |
| Append-only learner review history is deleted or reordered | Runtime rejects backwards recall timestamps; `doctor` enforces chronological history and reconciles learning-state fields to the latest review record |
| Corrupt state loses prior records | Atomic replace for JSON writes, recorded run state, and append-only review/log records |
| Another local account reads private learning data | Managed directories are `0700`, mutable files `0600`, and immutable snapshots `0400` |

## Residual risks

- Hostnames can resolve differently when an external Agent fetches them; the Agent's web tool must provide its own network isolation.
- Local filesystem permissions cannot protect against the workspace owner deliberately changing snapshots; `doctor` detects rather than prevents owner tampering.
- The single-writer lock coordinates cooperating Study Compiler processes; it
  cannot stop an unrelated program from editing files directly.
- Multi-file mutations are serialized but are not a crash-atomic database
  transaction. `doctor` reconciles source links, reviews, files, receipts, and
  mirrored metadata so interrupted writes remain visible for repair. Link-patch
  application has an operation-specific applying/result receipt for safe retry.
- An external scheduler controls wake-up and retry policy. Misconfigured
  schedulers may repeatedly invoke the Agent, but the active-run check prevents
  parallel recorded runs and no built-in daemon expands credential lifetime.
- Scanning hashes supported files and can be expensive for large trees. The
  implementation caps file count and size, but scheduler frequency still needs
  to match the learner's source volume.
- Mastery scores are subjective learning signals, not validated educational measurements.
