# MVP Acceptance Criteria

## A. Independent project

- [ ] The project exists at `~/Github/learning-wiki` with its own Git history.
- [ ] No Git remote, subtree, copied `.git`, or fork relationship points to `obsidian-wiki`.
- [ ] All implementation and Skills are original files in this repository.

## B. Vault initialization

- [ ] `learning-wiki init <temp-vault>` creates every documented directory and valid state file.
- [ ] Re-running `init` is safe and does not discard existing data.

## C. Three source types

- [ ] A local paper/document is copied into an immutable source snapshot and hashed.
- [ ] An HTTPS web URL is registered without the CLI fetching it, then accepts an Agent-fetched immutable snapshot.
- [ ] A chat excerpt is saved as an immutable source snapshot.
- [ ] Duplicate content is detected and returns the existing source ID.
- [ ] Unsafe URL schemes, private IP literals, oversized files, and escaping paths are rejected.

## D. Learning draft

- [ ] An Agent-facing Skill defines how to turn sources into a pedagogical lesson.
- [ ] `draft create` rejects missing sources or missing required teaching sections.
- [ ] A valid lesson is written only to `_staging/lessons`.
- [ ] The staged lesson contains an explanation, example, explain-it-back prompt, and provenance.

## E. Recall and approval

- [ ] `recall record` appends the learner's retelling and a score from 1 to 5.
- [ ] Learning state keeps mastery separate from source metadata.
- [ ] Promotion without `--approve` fails.
- [ ] Approved promotion moves the lesson to `lessons/` and updates index, log, and learning state.

## F. Periodic learning output

- [ ] `digest build` produces a staged daily, weekly, or monthly learning agenda.
- [ ] The agenda includes relevant staged work, unprocessed sources, and due/weak lessons.
- [ ] A Skill defines how the Agent converts the agenda into human-readable learning guidance.

## G. Integrity and quality

- [ ] `doctor` detects malformed JSON, missing source snapshots, hash drift, invalid drafts, unknown source IDs, stale index entries, and path escapes.
- [ ] `doctor --json` returns stable machine-readable results.
- [ ] All tests pass with no skipped tests.
- [ ] An end-to-end temporary-vault run passes from initialization through approved note and clean doctor report.
- [ ] Documentation covers architecture, commands, trust boundaries, and the human approval workflow.

## Verification commands

```bash
python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m learning_wiki --help
PYTHONPATH=src python3 -m learning_wiki doctor <acceptance-vault> --json
git status --short
git remote -v
```
