# Plans — the architect / implementer workflow

This folder contains **numbered, authoritative implementation plans**. Unlike
the exploratory docs at `design/` root, each plan here is a complete,
decision-locked instruction set that an implementing agent executes verbatim.
Features ship as backend + frontend **doc pairs**; single-doc plans are fine
for refinement passes. Once an agent starts a plan, a sibling
`NN-<name>-timeline.md` appears next to it — the live progress/handoff log.

**Split-plan convention (13+):** when a feature splits into backend + frontend
docs, both docs share the **same number** — `NN-<name>-backend.md` +
`NN-<name>-frontend.md` — and share **one unified** `NN-<name>-timeline.md`:
the backend agent creates it; the frontend agent reads it, verifies its claims
against `git log`, and appends to the same file. Never create a second
timeline for the pair. (Historical pairs 04/05, 06/07, 08/09, 10/11 predate
this and keep their separate numbers/timelines.)

## The workflow

Two tiers of model, two jobs:

1. **Architect session** (strongest available model — plans, never
   implements). It explores the real code before proposing anything, verifies
   external APIs against actual docs/source (never from memory), discusses
   options with recommendations, and asks clarifying questions **as numbered
   lists in chat** (answer by number). Once decisions are settled it writes
   the plan doc(s), updates `design/timeline.md`, and produces **one kickoff
   prompt per doc**.
2. **Implementer sessions** (small/mid model — Sonnet-class has the best
   track record here). One session per plan doc, driven by the kickoff
   prompt. Frontends only start after their backend plan is complete.

### Bootstrap prompt for a new architect session

> You are the architect for this project. I want to add <feature>. Your job
> is the research, design discussion, and plan writing — smaller agents do
> the implementation from your docs. Explore the codebase and verify every
> fact (including external API docs/source) before designing; ask me
> clarifying questions as numbered lists in chat, never via question tools.
> When we've settled the decisions, write
> `design/plans/NN-<feature>-backend.md` and `NN-<feature>-frontend.md`
> (continue the folder's numbering), following the structure of plans 06–11:
> a header note for the implementing agent, a locked-decisions table, phased
> steps with "verify in code" markers, an API contract table, tests, out of
> scope, and operator notes. Reference the shared ground rules (plan 04 §0
> for backend, plan 05 §2 for frontend) instead of repeating them. Update
> `design/timeline.md`. Then give me one strong, focused kickoff prompt per
> doc, following the anatomy in `design/plans/README.md`.

## Plan-doc conventions

- **Locked-decisions table** (D/G/A/L-style ids) — every settled choice, so
  implementers never re-litigate.
- **"Verify in code" markers** — anything the architect couldn't fully pin
  down; the implementer must read that module first. Code beats doc, always.
- **Shared ground rules by reference**: plan 04 §0 (backend: ruff/Alembic/
  test-baseline/commit rules), plan 05 §2 (frontend: trackJob poll-first,
  null guards, job labels, reuse over forking).
- Regression protection is explicit: snapshot tests before touching shared
  code, literal routes before parameterized ones, `media_type` guards.

## Kickoff-prompt anatomy

Every kickoff prompt must include:

1. Read `CLAUDE.md`, the ground-rules sections, and the plan doc **in full**
   before any code. Decisions are final — no redesigning, no questions.
2. **Frontend prompts only**: verify the backend actually shipped (named
   routes/modules exist) — STOP and report if not. Route code is the source
   of truth over the plan's contract tables.
3. **Timeline-doc protocol**: FIRST check whether
   `design/plans/NN-<name>-timeline.md` exists. Exists → read it, verify its
   claims against `git log` and the working tree, and RESUME from where it
   leaves off. Missing → create it before the first commit. After every
   commit append: completed (with hash), in progress, exact next steps,
   deviations from the plan and why, pending operator actions. Terse and
   factual — it exists so a different agent can take over cold. For split
   plans (same-number backend/frontend pair) both agents use the ONE shared
   timeline: the backend agent creates it, the frontend agent appends.
4. **Git authorship**: commit as the repository's configured git user ONLY —
   never add yourself as an author or co-author (no `Co-Authored-By:
   Claude …` trailers, no "Generated with Claude Code" footers).
5. Phase-by-phase commits, short lowercase imperative messages.
6. Gates: backend = full pytest baseline first (report baseline vs new) +
   `ruff check marquee tests`; frontend = `npm run check` / `lint` / `build`
   green per commit.
7. Finish with operator notes and an honest statement of whether the manual
   smoke test was performed.

## Environment notes for implementer sessions

- **pytest needs the local PostgreSQL** (127.0.0.1:5432): `tests/conftest.py`
  provisions an ephemeral `test_<uuid>` schema and drops it — approve an
  unsandboxed test run when the sandbox blocks the socket; it cannot touch
  live data.
- **The session needs git write access.** Some surfaces/thread policies make
  `.git` read-only; run implementer sessions in the same harness that
  committed successfully on earlier plans, or the agent will stall at its
  first commit.
- `ruff check` only — **never** `ruff format`.
- Alembic migrations are verified offline (`alembic upgrade head --sql`) and
  applied to the live DB **by the operator only**.
