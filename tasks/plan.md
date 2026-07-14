# Implementation Plan: Learning Wiki MVP

## Overview

Build one local vertical learning loop: initialize a vault, register immutable sources, stage a pedagogical lesson, record a retelling, explicitly promote the lesson, generate a periodic agenda, and validate every invariant.

## Dependency graph

```text
vault schema + safe storage
        |
        +--> source import and registry
        |          |
        |          v
        +--> lesson validation and staging
                   |
                   +--> recall and promotion
                   |
                   +--> digest agenda
                              |
                              v
                       whole-vault doctor

Skills depend on the stable CLI and file contracts but not on Python internals.
```

## Phases

### Phase 1: Contract and foundation

1. Define rules, spec, architecture, threat model, CLI, and acceptance criteria.
2. Implement vault initialization and safe JSON/filesystem storage with tests.

### Phase 2: Learning loop

3. Implement immutable source import/registration and deduplication with tests.
4. Implement lesson staging, recall recording, and approval-gated promotion with tests.
5. Implement deterministic digest agendas and whole-vault validation with tests.

### Phase 3: Agent behavior and handoff

6. Add Skills for ingest, lesson drafting, recall review, stage commit, and learning digest.
7. Run the complete acceptance flow, review security boundaries, and record evidence.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Agent produces polished but shallow summaries | High | Required examples, explain-it-back prompts, and recall loop |
| Prompt injection inside sources | High | Treat content as data; CLI never executes it; promotion validates structure |
| Source or model output escapes the vault | High | Strict slugs, resolved-path containment checks, CLI-controlled destinations |
| State files drift from Markdown | Medium | Single-writer CLI operations and `doctor` reconciliation |
| Product scope expands before learning value is proven | Medium | No UI, provider API, daemon, vector DB, or multi-user layer in v0.1 |

## Checkpoints

- Foundation: init and source tests pass; source files stay immutable.
- Learning loop: staged draft through approved lesson passes end to end.
- Complete: digest and doctor pass against a real temporary vault; docs match behavior.

