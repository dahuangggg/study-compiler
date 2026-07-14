# Architecture

## Design thesis

Learning Wiki separates reliable mechanics from semantic teaching judgment:

```text
papers / web snapshots / chat excerpts
                |
                v
      deterministic source registry
                |
                v
     Agent Skills: extract and teach
                |
                v
         staged lesson drafts
                |
        read + explain it back
                |
                v
      explicit human approval gate
                |
                v
       approved learning notes
                |
                v
   periodic review and learning digest
```

## Layers

### 1. Source layer

The CLI snapshots local inputs and records web references. Every source receives a stable ID derived from its kind and content hash. The manifest stores provenance, hash, media type, import time, and snapshot path. Re-importing identical content is idempotent.

### 2. Learning compiler layer

Markdown Skills tell an Agent how to:

- identify concepts and prerequisites;
- choose a teaching form based on the material;
- explain with examples and boundaries;
- ask the learner to retell the idea;
- diagnose gaps without confusing mastery with factual confidence.

Skills cannot directly promote a note. They invoke the deterministic CLI contract.

### 3. Review layer

All lessons begin in `_staging/lessons`. The CLI validates required sections and registered source IDs. Learner retellings are append-only review records. Explicit promotion moves a valid draft to `lessons/` and updates state, index, and log atomically enough for a local single-process MVP.

### 4. Scheduling layer

`digest build` creates a deterministic draft agenda from unprocessed sources, staged lessons, low-scoring lessons, and due reviews. The `learning-digest` Skill turns that agenda into a human-readable learning note. External schedulers may invoke the Agent later; the MVP documents the hook but does not install a daemon.

## Module boundaries

```text
src/learning_wiki/
  cli.py          command parsing and stable output contract
  errors.py       public validation/state errors
  models.py       source and learning-state schemas
  storage.py      safe paths, JSON persistence, index/log writes
  sources.py      import, hashing, URL registration, deduplication
  lessons.py      draft validation, recall, promotion
  digest.py       deterministic learning agenda generation
  doctor.py       whole-vault invariant checks
```

## Key decisions

- **Standard library only:** minimizes supply-chain risk and keeps installation simple.
- **Files over database:** Markdown stays readable in Obsidian and JSON stays inspectable.
- **No embedded LLM client:** Skills work with Codex, Claude, Pi, or another capable Agent.
- **No arbitrary fetch in CLI:** avoids SSRF and keeps web acquisition under an observable Agent tool.
- **Explicit promotion:** AI output remains draft data until the learner approves it.
- **Separate mastery from evidence:** `score` describes the learner; provenance describes the claim.

## Evolution path

Future versions can add provider adapters, a review UI, background automation, and multi-user storage behind the existing CLI/data contracts. Those are extensions, not prerequisites for validating the learning loop.

