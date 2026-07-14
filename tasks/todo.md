# Tasks

## Task 1: Vault foundation

- [x] Create package entrypoint, error contract, vault layout, and safe persistence.
- Acceptance: init is idempotent; state schemas are valid; writes cannot escape the vault.
- Verify: targeted unit and integration tests.
- Dependencies: none.

## Task 2: Source registry

- [x] Import files and snippets, register URLs, hash content, and deduplicate.
- Acceptance: all three source families work; unsafe/oversized inputs fail closed.
- Verify: source tests plus CLI smoke checks.
- Dependencies: Task 1.

## Task 3: Lesson lifecycle

- [x] Validate and stage lessons, append recall records, and approval-gate promotion.
- Acceptance: required pedagogy and provenance enforced; state/index/log updated.
- Verify: lesson integration tests.
- Dependencies: Tasks 1–2.

## Task 4: Digest and doctor

- [ ] Generate periodic learning agendas and validate the entire vault.
- Acceptance: due/weak/pending items appear; corruption is reported deterministically.
- Verify: digest/doctor tests and JSON CLI output.
- Dependencies: Tasks 1–3.

## Task 5: Agent Skills

- [ ] Add five Skills that follow the CLI contract and trust boundary.
- Acceptance: source-to-lesson, recall revision, promotion, and periodic guidance are fully instructed.
- Verify: structural Skill checks and manual contract review.
- Dependencies: Tasks 1–4.

## Task 6: Acceptance audit

- [ ] Exercise the complete flow and check every item in `docs/acceptance.md`.
- Acceptance: all criteria have current evidence; no remote/fork relationship exists.
- Verify: full test suite, runtime fixture, doctor, Git inspection.
- Dependencies: all prior tasks.
