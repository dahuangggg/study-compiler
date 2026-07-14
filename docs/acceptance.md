# MVP Acceptance Criteria

## A. Independent project

- [x] The project exists at `~/Github/learning-wiki` with its own Git history.
- [x] No Git remote, subtree, copied `.git`, or fork relationship points to `obsidian-wiki`.
- [x] All implementation and Skills are original files in this repository.

## B. Vault initialization

- [x] `learning-wiki init <temp-vault>` creates every documented directory and valid state file.
- [x] Re-running `init` is safe and does not discard existing data.

## B2. Automatic installation

- [x] `./install.sh --vault <path>` installs the CLI, all five Skills, and initializes the vault.
- [x] Agent skill directories can be auto-detected or selected explicitly for Codex, Claude, shared Agents, or a custom target.
- [x] Re-running installation is idempotent; conflicting existing Skills are preserved unless `--force` is explicit.
- [x] A built wheel contains all Skill instruction and Agent metadata files.

## C. Three source types

- [x] A local paper/document is copied into an immutable source snapshot and hashed.
- [x] An HTTPS web URL is registered without the CLI fetching it, then accepts an Agent-fetched immutable snapshot.
- [x] A chat excerpt is saved as an immutable source snapshot.
- [x] Duplicate content is detected and returns the existing source ID.
- [x] Unsafe URL schemes, private IP literals, oversized files, and escaping paths are rejected.

## D. Learning draft

- [x] An Agent-facing Skill defines how to turn sources into a pedagogical lesson.
- [x] `draft create` rejects missing sources or missing required teaching sections.
- [x] A valid lesson is written only to `_staging/lessons`.
- [x] The staged lesson contains an explanation, example, explain-it-back prompt, and provenance.

## E. Recall and approval

- [x] `recall record` appends the learner's retelling and a score from 1 to 5.
- [x] Learning state keeps mastery separate from source metadata.
- [x] Promotion without `--approve` fails.
- [x] Approved promotion moves the lesson to `lessons/` and updates index, log, and learning state.

## F. Periodic learning output

- [x] `digest build` produces a staged daily, weekly, or monthly learning agenda.
- [x] The agenda includes relevant staged work, unprocessed sources, and due/weak lessons.
- [x] A Skill defines how the Agent converts the agenda into human-readable learning guidance.

## G. Integrity and quality

- [x] `doctor` detects malformed JSON, missing source snapshots, hash drift, invalid drafts, unknown source IDs, stale index entries, and path escapes.
- [x] `doctor --json` returns stable machine-readable results.
- [x] All tests pass with no skipped tests.
- [x] An end-to-end temporary-vault run passes from initialization through approved note and clean doctor report.
- [x] Documentation covers architecture, commands, trust boundaries, and the human approval workflow.

## Verification commands

```bash
./install.sh --help
python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m learning_wiki --help
PYTHONPATH=src python3 -m learning_wiki doctor <acceptance-vault> --json
git status --short
git remote -v
```
