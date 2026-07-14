---
name: learning-digest
description: Turn Study Compiler's daily, weekly, or monthly agenda into a focused human-readable learning session. Use for scheduled learning notes, study plans, weekly reviews, due reviews, or deciding what the learner should study next.
---

# Learning Digest

Choose a useful learning session, not an activity report.

## Build the deterministic agenda

Resolve the workspace and run one of:

```bash
study-compiler digest build <workspace> --period daily
study-compiler digest build <workspace> --period weekly
study-compiler digest build <workspace> --period monthly
```

Read the returned staged agenda. Load only the lessons and registered sources needed for its highest-priority items.

## Prioritize for learning

Use this order unless the material demands otherwise:

1. Repair a due or low-scoring explanation.
2. Finish a coherent staged lesson awaiting learner interaction.
3. Teach a prerequisite blocking multiple new sources.
4. Introduce one new high-value concept.

Avoid rewarding volume. One concept learned and retold is better than ten summarized links.

## Produce the human readout

Write or present:

- **What to learn now:** one clear outcome.
- **Why now:** the source, gap, or prerequisite that makes it timely.
- **Short lesson:** intuitive explanation plus a concrete example.
- **Connect:** one link to prior learning.
- **Explain it back:** one retrieval prompt.
- **Afterward:** whether to revise, promote, or schedule another review.

Ground claims in registered source IDs. Mark inference and disagreement. Do not promote any lesson or modify approved notes.

## Scheduled runs

A scheduler may invoke this Skill periodically, but a scheduled run may only create or update staged material. It must never imply human approval. End by reporting the staged agenda path and the next requested learner action.
