---
name: learning-stage-commit
description: Review staged Study Compiler lesson drafts and promote only those the user explicitly approves. Use when the user asks to review, approve, accept, commit, or move a generated learning draft into formal notes.
---

# Learning Stage Commit

Protect the boundary between generated drafts and the learner's formal notes.

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

## Require explicit approval

Ask the user to approve the specific slug. A request to inspect, summarize, or improve a draft is not approval. Never infer approval from silence, a scheduled run, or a positive recall score.

After explicit approval, run:

```bash
study-compiler draft promote <workspace> <slug> --approve
study-compiler doctor <workspace> --json
```

If `doctor` fails, report the exact error and do not claim promotion is healthy. Do not delete rejected drafts; leave them staged unless the user asks for a separate rejection workflow.

## Finish

Report the approved note path, source IDs, current mastery signal, and doctor result. Never promote multiple drafts when the user approved only one.
