# Superpowers Workflow

The Perspicacité project uses a structured multi-agent design and execution workflow
for non-trivial feature work. This document describes how that workflow operates and
where to find its artifacts.

---

## The workflow

Feature development follows four stages:

1. **Brainstorm** — open-ended exploration of the problem space, constraints, and
   possible approaches. Produces a short written summary of options considered and
   the chosen direction.

2. **Spec** — a design document (`docs/superpowers/specs/`) that records the accepted
   approach in enough detail to be implemented without further clarification. The spec
   is the contract between the person who designed the feature and the agent(s) who
   will implement it. Specs follow the naming convention
   `YYYY-MM-DD-<feature-name>-design.md`.

3. **Plan** — an implementation plan (`docs/superpowers/plans/`) that breaks the spec
   into concrete, sequentially-executable tasks. Each task specifies which files to
   touch, what to change, and what test to write to confirm it is done. Plans follow
   the naming convention `YYYY-MM-DD-<feature-name>.md`.

4. **Subagent execution** — the plan is handed to a Claude Code subagent (or executed
   by the agent currently in session) as a series of tasks. Each task is committed
   separately to `main` with a descriptive commit message. The agent verifies each
   task against its test criterion before moving on.

---

## Where the artifacts live

```
docs/superpowers/
  specs/          # accepted design documents (YYYY-MM-DD-feature-design.md)
  plans/          # implementation plans (YYYY-MM-DD-feature.md)
```

Active specs and plans as of 2026-05-15 include:

- `plans/2026-05-14-budget-caps.md` — budget caps and checkpoint/resume for long synthesis
- `plans/2026-05-13-capsule-cycle-a-core.md` — capsule extraction (figures, references)
- `plans/2026-05-13-multi-kb-zotero-local-docs.md` — multi-KB routing, Zotero, local docs
- `specs/2026-05-14-embedding-cache-design.md` — embedding cache to avoid recomputing unchanged chunks
- `specs/2026-05-14-claude-code-sampling-integration-design.md` — MCP sampling integration

---

## Commit discipline

- One commit per logical deliverable
- Commit directly to `main` (no long-lived feature branches by default)
- Commit messages follow Conventional Commits: `feat(module): short description`
- Each commit is independently meaningful — do not bundle a bug fix with a feature

---

## When to write a spec vs. just coding

Write a spec when:
- The change touches more than 2-3 files
- The change introduces a new abstraction or changes an existing public interface
- The correct approach is not immediately obvious (there are real trade-offs)
- The change will be executed by a subagent who was not part of the brainstorm

Skip the spec when:
- The change is a straightforward bug fix with an obvious correct approach
- The change is purely documentation
- The change is a small config default adjustment

---

## Related topics

- `docs/superpowers/plans/` — active implementation plans
- `docs/superpowers/specs/` — accepted design documents
- [development/contributing.md](contributing.md) — general contributor workflow
- [development/architecture.md](architecture.md) — where to look when writing a spec
  that touches the core pipeline
