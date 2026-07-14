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
   prompt. Frontends only start after their backend plan is complete. An
   implementer executes its assigned plan continuously through all internal
   phases; a phase boundary is a verification/checkpoint boundary, not a
   reason to yield or ask whether it should continue.

### Implementer continuity and stop conditions

Once an implementer starts a plan, it must continue autonomously until the
**entire assigned plan** is complete. At the end of each internal phase it
runs the phase gates, commits, updates the shared timeline, and immediately
begins the next phase in the same session. Routine phase completion, a large
diff, elapsed time, context compaction, a successful commit, or a desire for
confirmation are not valid reasons to stop.

An implementer stops early only when at least one of these conditions is
true:

- a plan-defined stop gate is reached;
- a required test, safety, schema, contract, ancestry, or verification gate
  fails and cannot be safely resolved within the plan;
- required infrastructure, credentials, permissions, tooling, or an owned
  disposable test environment is unavailable after the documented recovery
  path is exhausted;
- the code or an installed dependency materially contradicts a locked plan
  decision, so continuing would require redesign;
- unrelated/concurrent work makes the next edit, commit, or history rewrite
  unsafe;
- a required user decision or additional authority would materially change
  scope or behavior;
- the whole assigned plan is complete, including its final certification and
  any mandatory final-only history procedure.

When stopping early, the agent records the exact evidence, commands/results,
current tree and commit state, unfinished phase, and precise unblock condition
in the shared timeline, then reports it. It must not manufacture a blocker,
weaken a gate, skip verification, or treat pending manual/operator work that
the plan explicitly allows as a reason to abandon otherwise executable work.

For multi-doc programs, this continuity rule applies within one assigned plan
doc. A deliberate external plan boundary (for example backend → frontend or
JMC4A → JMC4B) remains a handoff when the plans or kickoff prompts assign them
to separate sessions.

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
5. **Continuous execution**: internal phase boundaries are checkpoints, not
   stopping points. After a successful phase gate, commit and update the
   timeline, then continue immediately into the next phase. Stop only for a
   condition listed under **Implementer continuity and stop conditions** or
   after the full assigned plan is complete.
6. Phase-by-phase commits, short lowercase imperative messages.
7. Gates: backend = full pytest baseline first (report baseline vs new) +
   `ruff check marquee tests`; frontend = `npm run check` / `lint` / `build`
   green per commit.
8. Finish with operator notes and an honest statement of whether the manual
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
