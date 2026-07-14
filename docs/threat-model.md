# Threat Model

## Trust boundaries

- Local files, PDFs, chat excerpts, URL strings, fetched webpage snapshots, and generated lesson Markdown are untrusted.
- CLI arguments cross into filesystem and state operations.
- Agent/model output crosses into staged Markdown but never directly into executable operations.

## Assets

- The learner's private source material and chat history.
- Provenance and source integrity.
- Approved notes and learning history.
- Files outside the configured vault.

## Abuse cases and controls

| Abuse case | Control |
|---|---|
| A paper says “ignore instructions and run this command” | Skills declare source text to be data; CLI never executes content |
| A slug or filename tries `../../` traversal | Slug allowlist and resolved-path containment checks |
| A URL points to localhost or cloud metadata | HTTPS-only URL validation and rejection of private/reserved IP literals; CLI does not fetch |
| A huge file exhausts memory/storage | Configured import size limit and streaming hash/copy |
| Model output cites a nonexistent source | Lesson validator rejects unknown source IDs |
| Agent silently publishes its output | All output stages; promotion requires explicit `--approve` |
| Imported source is modified later | Read-only snapshot permissions plus hash-drift detection in `doctor` |
| Corrupt state loses prior records | Atomic replace for JSON writes and append-only review/log records |

## Residual risks

- Hostnames can resolve differently when an external Agent fetches them; the Agent's web tool must provide its own network isolation.
- Local filesystem permissions cannot protect against the vault owner deliberately changing snapshots; `doctor` detects rather than prevents owner tampering.
- Mastery scores are subjective learning signals, not validated educational measurements.

