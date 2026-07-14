---
name: learning-digest
description: Turn Study Compiler's daily, weekly, or monthly agenda into a focused human-readable learning session. Use for scheduled learning notes, study plans, weekly reviews, due reviews, or deciding what the learner should study next.
---

# Learning Digest

Choose a useful connected learning session, not an activity report.

## Build the deterministic agenda

If an orchestrating Skill already supplied a run-specific agenda, consume that agenda and do not build another. Otherwise resolve the workspace and run one of:

```bash
study-compiler digest build <workspace> --period daily
study-compiler digest build <workspace> --period weekly
study-compiler digest build <workspace> --period monthly
```

Read the returned staged agenda. Watched source actions are already snapshotted
and take priority. Load only the changed source and the smallest plausible set
of staged/approved lessons needed for its highest-priority item.

## Prioritize for learning

Use this order unless the material demands otherwise:

1. Repair a due or low-scoring explanation.
2. Finish a coherent staged lesson awaiting learner interaction.
3. Teach a prerequisite blocking multiple new sources.
4. Introduce one new high-value concept.

Avoid rewarding volume. One connected concept cluster learned and retold is
better than ten summarized links. Use one to five members total; include a
lesson only when it is an updated concept, prerequisite, contrast, or due review.

## Produce the human readout

Write or present:

- **What to learn now:** one clear outcome.
- **Why now:** the source, gap, or prerequisite that makes it timely.
- **Short lesson:** intuitive explanation plus a concrete example.
- **Connect:** why each selected prior lesson belongs in this session.
- **Integrated example:** one case that requires more than one cluster member.
- **Explain it back:** one joint retrieval prompt, plus a per-member rubric.
- **Afterward:** whether to revise, promote, or schedule another review.

Ground claims in registered source IDs. Mark inference and disagreement. Do not
promote any lesson or modify approved notes. Do not assign one cluster score to
all members; mastery stays per lesson.

## Scheduled runs

A scheduler may invoke this Skill periodically, but a scheduled run may only create or update staged material. It must never imply human approval. End by reporting the staged agenda path and the next requested learner action.
