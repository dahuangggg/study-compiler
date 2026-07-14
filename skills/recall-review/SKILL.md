---
name: recall-review
description: Evaluate a learner's explain-it-back attempt against a staged or approved Study Compiler lesson, record mastery, diagnose misconceptions, and revise the staged lesson. Use when the user retells a concept, asks whether they understood it, or wants a learning check.
---

# Recall Review

Use retrieval practice to find understanding gaps, not to reward wording similarity.

## Collect an unaided retelling

Ask the learner to explain the concept in their own words and give an example. Do not show the model answer immediately before asking. Accept uncertainty; it is diagnostic signal.

When the prompt came from a review cluster, ask for one connected explanation,
then evaluate each member lesson separately. A response may demonstrate one
member but not another; never copy one aggregate score across the cluster.

## Evaluate against sources

Read the relevant lesson and its registered sources. Score the retelling:

- `1`: materially wrong or no usable model.
- `2`: isolated facts with a major misconception.
- `3`: correct gist but weak mechanism, example, or boundary.
- `4`: accurate explanation with a valid example; minor omissions only.
- `5`: accurate explanation, example, boundary, and transfer to a new case.

State what was correct, the smallest important gap, and one targeted follow-up question. Never change source confidence because of the learner's score.

## Record the attempt

Save the learner's exact retelling to a temporary UTF-8 file and run:

```bash
study-compiler recall record <workspace> <slug> --score <1-5> --retelling-file <file>
```

For a cluster response, run this separately only for members that can be scored
from the learner's answer. Leave unsupported members awaiting a response and ask
one targeted follow-up. Use the same exact learner retelling as evidence for
each scored member; do not invent member-specific answers.

## Revise a staged lesson

If the lesson is still staged and the failure reveals a teaching problem, revise only the body. Preserve its sources and status. Keep all required headings, then run:

```bash
study-compiler draft revise <workspace> <slug> --content-file <revised-body-file>
```

Do not add new source scope while repairing a reviewed lesson. Do not rewrite an
approved lesson silently; create a separate staged synthesis/comparison when new
evidence needs to be connected.

## Finish

Give the learner one concise correction and another explain-it-back prompt. Do not promote the lesson; approval belongs to `learning-stage-commit`.
