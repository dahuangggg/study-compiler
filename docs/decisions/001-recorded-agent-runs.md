# ADR-001: Record Agent runs while keeping scheduling external

## Status

Accepted

## Date

2026-07-14

## Context

Study Compiler needs periodic note generation without weakening its core trust
boundary: deterministic state changes belong to the CLI, semantic teaching
belongs to Agent Skills, and generated work remains staged until a learner
approves it. A scheduled invocation also needs enough durable state to explain
whether work started, completed, failed, or had nothing to do.

Embedding a scheduler or model client would make the package responsible for a
long-running process, credentials, retries, provider behavior, and process
lifecycle across operating systems. Leaving all orchestration to a prompt,
however, would make concurrent runs and partial failures invisible.

## Decision

Use three explicit layers:

1. The deterministic CLI owns validation and state transitions. `status`
   reports actionable work; `run start`, `run resume`, `run finish`, and
   `run fail` record the run lifecycle under `.study-compiler/runs/`. One
   fail-fast workspace lock prevents cooperating writers from interleaving
   multi-file mutations.
2. The semantic `$learning-run` Skill handles one returned action and delegates
   teaching work to the five leaf Skills. It must create the run-bound artifact
   under `_staging/`, bind it in leading frontmatter, stay within the artifact
   size limit, satisfy the recorded action postcondition, may not submit the
   deterministic agenda as completed work, and never invokes approval.
3. An external scheduler, such as Codex automation or cron, only wakes the
   Agent. Study Compiler does not install a daemon, retry loop, or hosted model
   process.

`setup --workspace` saves a default workspace so scheduled Agent sessions can
use `status` and `run` without reconstructing environment variables. An
explicit `--workspace` always takes precedence.

Run completion and lesson approval are deliberately separate. A completed run
means that its exact staged artifact and planned action are verified, content
receipts are recorded, and workspace invariants pass; only a later explicit
stage-commit action may promote a lesson. An interrupted active run is resumed
from its recorded agenda instead of being duplicated.

## Alternatives considered

### Built-in background daemon

- Pros: owns timing, retries, and continuous execution in one package.
- Cons: requires service installation, credential lifetime management,
  cross-platform process supervision, shutdown semantics, and a larger attack
  surface.
- Rejected: these costs are not needed to validate the local learning loop, and
  users already have Agent-capable schedulers.

### Scheduler invokes deterministic CLI steps directly

- Pros: simple shell automation with no semantic orchestration Skill.
- Cons: a deterministic command cannot choose an appropriate teaching form or
  safely route `source_snapshot`, drafting, and recall actions. The scheduler
  would absorb product logic and likely bypass staging conventions.
- Rejected: semantic judgment belongs to the Agent and its Skills.

### Skill-only orchestration with no run records

- Pros: no additional on-disk state or lifecycle commands.
- Cons: concurrent invocations, abandoned work, no-op cycles, and failures are
  ambiguous. Later Agents cannot distinguish an active run from an interrupted
  prompt.
- Rejected: small deterministic run records provide recovery and diagnosis
  without embedding an Agent loop.

## Consequences

- External automation remains replaceable and provider-independent.
- The CLI has a stable, inspectable run state machine and `doctor` can validate
  its records.
- Only one recorded run may be active in a workspace, and concurrent mutations
  fail fast instead of waiting indefinitely.
- Schedulers own wake-up frequency and retry policy; Study Compiler owns neither.
- Agents must explicitly finish or fail a started run; interruptions leave an
  active record that the next Agent can resume and diagnose.
- Successful scheduling never implies lesson approval, preserving the existing
  human gate.
- Multi-file persistence is coordinated but not a crash-atomic transaction;
  `doctor` detects state drift for repair rather than hiding it.
