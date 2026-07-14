# Implementation Plan: Study Compiler MVP

## Overview

Build one local vertical learning loop: initialize a workspace, detect immutable
source deltas, stage a pedagogical lesson, record a retelling, explicitly
promote the lesson, stage and approve durable typed relationships, generate a
periodic agenda, and validate every invariant.

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

6. Add Skills for ingest, lesson drafting, recall review, stage commit, learning digest, and recorded runs.
7. Run the complete acceptance flow, review security boundaries, and record evidence.

### Phase 4: Durable knowledge graph

8. Add lesson format v3 typed relationships, graph health checks, and stable export.
9. Add hash-bound structured link patches with explicit approval and crash recovery.
10. Add `$learning-cross-link` and route bounded graph maintenance from scheduled runs.
11. Re-run the complete acceptance, packaging, Skill, and clean-install audits.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Agent produces polished but shallow summaries | High | Required examples, explain-it-back prompts, and recall loop |
| Prompt injection inside sources | High | Treat content as data; CLI never executes it; promotion validates structure |
| Source or model output escapes the vault | High | Strict slugs, resolved-path containment checks, CLI-controlled destinations |
| State files drift from Markdown | Medium | Single-writer CLI operations and `doctor` reconciliation |
| Semantic links silently rewrite approved knowledge | High | Structured additive patches, exact base hashes, and patch-specific approval |
| Link semantics are inferred from navigation alone | Medium | Keep untyped body links distinct from explicit typed relationships |
| Product scope expands before learning value is proven | Medium | No UI, provider API, daemon, vector DB, or multi-user layer in v0.1 |

## Checkpoints

- Foundation: init and source tests pass; source files stay immutable.
- Learning loop: staged draft through approved lesson passes end to end.
- Complete: digest and doctor pass against a real temporary vault; docs match behavior.
- Graph: staged relationships require explicit approval; graph check/export and doctor agree.
