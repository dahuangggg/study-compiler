---
name: recall-review
description: Evaluate a learner's explain-it-back attempt against a staged or approved Study Compiler lesson, record mastery, diagnose misconceptions, and revise the staged lesson. Use when the user retells a concept, asks whether they understood it, or wants a learning check.
---

# Recall Review

Use retrieval practice to find understanding gaps, not to reward wording similarity.

## Collect an unaided retelling

Ask the learner to explain the concept in their own words and give an example. Do not show the model answer immediately before asking. Accept uncertainty; it is diagnostic signal.

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

## Revise a staged lesson

If the lesson is still staged and the failure reveals a teaching problem, revise only the body. Preserve its sources and status. Keep all required headings, then run:

```bash
study-compiler draft revise <workspace> <slug> --content-file <revised-body-file>
```

Do not rewrite an approved lesson silently. Propose a new staged revision in a future version or ask the user how to proceed.

## Finish

Give the learner one concise correction and another explain-it-back prompt. Do not promote the lesson; approval belongs to `learning-stage-commit`.
