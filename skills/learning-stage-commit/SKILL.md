---
name: learning-stage-commit
description: Review staged Study Compiler lesson drafts or typed-link patches and apply only the exact artifact the user explicitly approves. Use when the user asks to review, approve, accept, commit, promote a generated lesson, or apply a proposed note connection.
---

# Learning Stage Commit

Protect the boundary between generated drafts and the learner's formal notes.

First determine whether the requested artifact is a lesson slug or a link patch
ID. Approval of one never authorizes the other.

## Inventory and preview

Run:

```bash
study-compiler draft list <workspace> --json
```

For each selected draft, show:

- title and slug;
- learning objectives;
- source IDs;
- explanation and example summary;
- latest recall score, when present;
- unresolved uncertainty or missing prerequisite.

Check that the draft teaches rather than merely summarizes. Confirm that every material claim is grounded or labeled as inference and that the explain-it-back prompt is meaningful.

For a synthesis draft, also show the related lesson slugs, why they were grouped,
which claims are cross-source inference, and whether any source contradicts an
approved lesson. Approval of the synthesis never authorizes rewriting or
deleting its input lessons.

For a typed-link patch, run:

```bash
study-compiler link show <workspace> <patch-id> --json
```

Show the source lesson, each target, relationship type, confidence, score,
rationale, and base hash. State that applying it changes only relationship
metadata plus the generated Related Lessons block; it preserves sources,
teaching content, approval time, mastery, and review history. A stale patch must
be regenerated, never rebased silently.

## Require explicit approval

Ask the user to approve the specific slug. A request to inspect, summarize, or improve a draft is not approval. Never infer approval from silence, a scheduled run, or a positive recall score.

After explicit approval, run:

```bash
study-compiler draft promote <workspace> <slug> --approve
study-compiler doctor <workspace> --json
```

For an explicitly approved link patch ID, run instead:

```bash
study-compiler link apply <workspace> <patch-id> --approve
study-compiler doctor <workspace> --json
```

If `doctor` fails, report the exact error and do not claim promotion is healthy. Do not delete rejected drafts; leave them staged unless the user asks for a separate rejection workflow.

## Finish

Report the approved note or applied patch path, its exact scope, and doctor
result. Never promote multiple drafts or apply multiple patches when the user
approved only one. After promoting a lesson, offer `$learning-cross-link` as a
separate staged maintenance action; promotion itself is not approval of any
new relationship.
