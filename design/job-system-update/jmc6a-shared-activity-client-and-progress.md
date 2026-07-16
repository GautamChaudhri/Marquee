# JMC6A — Shared Activity Client and Progress Experience

> **Implementer:** Read this document in full before changing code. Decisions are locked.
> Execute every internal phase continuously; phase boundaries are verification checkpoints, not
> stopping points. Stop only for a condition in
> [`design/plans/README.md`](../plans/README.md#implementer-continuity-and-stop-conditions)
> or after the complete plan, certification, and final-only history compaction succeed.

**Program:** [clean-slate PgQueuer migration](job-system-pgqueuer-migration.md)
**Product design:** [Projection Room redesign](projection-room-job-experience-redesign.md)
**Progress design:** [job progress and loading experience](job-progress-and-loading-experience.md)
**Predecessor:** [JMC5C destructive media and runtime retirement](jmc5c-letterbox-hdr-and-runtime-retirement.md)
**Successor:** [JMC6B Projection Room and feature pages](jmc6b-projection-room-and-feature-pages.md)
**Shared timeline:** `design/job-system-update/jmc6-activity-and-certification-timeline.md`
**Recommended model tier:** **God**

## 1. Objective

Create the single generated, runtime-validated client boundary and single server-backed job store
used by Projection Room and every feature page. Replace the legacy polling adapter and establish
the reusable progress/evidence components before rebuilding any large page.

JMC6A is not a visual Projection Room rewrite. It supplies the concurrency, reconciliation,
typing, testing, and rendering primitives that make the JMC6B page work reliable. At its exit,
the old pages may still render their existing layouts, but the new store/card must be fully tested
and ready for migration.

## 2. Verified starting state and stop gates

The architect verified `jmc5c-complete` at compact commit `30cc8c1`, a clean tree, and the final
JMC5 baseline of **1249 passed, 17 retained failures, 2 warnings**. JMC5 recorded frontend check
at zero errors and 16 warnings in eight files, deterministic OpenAPI 3.1.0 with 201 paths, and no
legacy runtime/schema writer.

Current source still contains the exact client problems assigned here:

- `frontend/src/lib/jobs.ts` accepts loose progress dictionaries; its `eventsUrl` is ignored and
  it periodically polls one job at a time;
- `frontend/src/lib/api/jobs.ts` converts generated canonical responses back into handwritten
  legacy DTOs and its detail helper eagerly fans out across presentation, attempts, events,
  children, and four raw documents;
- `frontend/src/routes/projection-room/+page.svelte` uses local storage as an active-job registry,
  starts per-job trackers, and refreshes product plus host data together;
- initiating pages retain multiple local-storage IDs, private stage maps, and page-local bars;
- the frontend has no unit/component/E2E test command or harness.

Before edits, verify the full JMC5C completion tag, tree, timeline, schema/OpenAPI fingerprints,
enabled/reserved definition manifest, configured Git author, branch ancestry, and recovery bundle.
Create the shared JMC6 timeline and record exact backend, Ruff, frontend check/lint/build, generated
contract, and test-warning baselines.

Stop rather than weakening the plan if:

- the compact JMC5C tree or its final executor manifest cannot be reproduced;
- the multiplexed event cursor or compact snapshots cannot be reconciled without another
  page-owned source of truth;
- the browser would need a secret or an unbounded response to open the job event stream;
- a generated contract materially contradicts the current canonical route implementation;
- concurrent/unrelated commits make the plan-owned range or final rewrite unsafe.

## 3. Locked decisions

| ID | Decision |
|---|---|
| A01 | The canonical application Activity URL remains `/projection-room`. The separate `/activity` page is not a second job experience. JMC6B removes or redirects that unused UI route after verifying navigation consumers. |
| A02 | Generated OpenAPI `paths` and `components` are the only wire-type authority. Handwritten job/progress/presentation DTOs and canonical-to-legacy adapters are deleted as consumers migrate. |
| A03 | Keep Marquee's existing fetch runtime. Add narrow runtime validators for destructive command responses, job-creation responses, terminal success/outcome data, cursor envelopes, and SSE event envelopes; do not generate or adopt a second runtime SDK. |
| A04 | One browser-session `JobProgressStore` owns Queue discovery, snapshots, multiplexed SSE, event/progress cursors, connection freshness, repair polling, and local view state. It is created through a Svelte context/layout boundary so SSR requests never share mutable state. |
| A05 | The server remains authoritative. Local storage may hold column/density preferences and optional recent canonical IDs, but never determines whether an active job exists or has failed. |
| A06 | The store opens one same-origin `/api/jobs/events/stream` EventSource. API-key handling stays in the existing server/proxy boundary; no credential appears in browser query parameters, storage, logs, or event payloads. |
| A07 | A stream error changes connection state to reconnecting/stale and preserves the last good data. It never marks a job failed, removes a card, or invokes a page-specific error callback. |
| A08 | Queue discovery runs on first use and route/subject changes through bounded `view=queue` filters. Snapshot repair is low-frequency, abortable, hidden-tab aware, backoff bounded, and guarded against overlapping requests. |
| A09 | Global event IDs and per-job progress sequences reject duplicate/late updates. A retention-gap/reset frame triggers bounded Queue/snapshot reconciliation rather than replay assumptions. |
| A10 | Store records are canonical compact rows/snapshots plus connection metadata. Full presentations, logs, artifacts, raw documents, and child pages are lazy resources and never embedded in the global active map. |
| A11 | `JobProgressCard` has compact and expanded variants over the same typed props. It renders separate overall/current scopes, determinate/indeterminate/none modes, most-specific subject hierarchy, plain-language stage/wait state, freshness, credible metrics, bounded concurrent children, and server-authorized actions. |
| A12 | Clients never calculate a job percentage from arbitrary keys, humanize a raw status/stage key, invent an ETA, or reuse one bar across overall and current scope. |
| A13 | Terminal failure/cancellation preserves the last progress measurement. Success and no-change receive coherent terminal presentation; the client does not force 100 percent independently. |
| A14 | Add Vitest plus Testing Library for stores/components and Playwright Chromium plus axe for user flows/accessibility. Pin dependencies and add deterministic `test:unit` and `test:e2e` scripts. |
| A15 | JMC6A may add only narrow backend/OpenAPI fixes needed to uphold already-designed list/snapshot/SSE contracts. It does not invent compatibility endpoints or restore per-job polling SSE. |
| A16 | No feature page is allowed to fork the store/card API. JMC6B replaces page-local consumers; JMC6A freezes a migration inventory and adds static guards against new imports of the legacy tracker. |
| A17 | Internal phases continue autonomously. Final history compaction occurs only after all frontend/backend/generated-contract gates pass. |

The testing choices follow current official guidance: Svelte recommends Vitest for Vite/SvelteKit
unit and component work and documents Playwright for E2E; Testing Library provides resilient
role/user-oriented component assertions. Playwright's axe integration provides useful automated
accessibility coverage, supplemented by manual keyboard/screen-reader checks.
[Svelte testing](https://svelte.dev/docs/svelte/testing),
[Playwright accessibility testing](https://playwright.dev/docs/accessibility-testing)

## 4. Generated client and boundary validation

Refactor the frontend API layer around generated path operations:

- type route, parameters, request bodies, success responses, and documented error envelopes from
  generated `paths` rather than duplicating shapes;
- centralize URL/query/body serialization, `AbortSignal`, content type, API error parsing, and
  bounded response handling;
- preserve exact distinctions among omitted, `null`, zero, false, and empty arrays;
- validate commands and success-reporting boundaries before changing UI state;
- surface schema/version mismatch as a visible stale/incompatible client condition, not a
  successful toast;
- regenerate OpenAPI/TypeScript deterministically and fail on drift.

Replace `getJobDetail()` fan-out with focused methods for list, snapshot, presentation, attempts,
events, children, logs, artifacts, and raw documents. Callers choose the resource they need and
pass cursors/limits explicitly. No helper silently retrieves every diagnostic collection.

## 5. Shared store and reconciliation

Implement the store as a testable state machine with dependency-injected fetch/EventSource,
timers, visibility, and clock. It owns:

- one keyed Queue/list scope per active feature/subject/filter consumer with reference counting;
- one canonical compact record per job and one optional latest snapshot;
- global event cursor, per-job progress sequence/fence, connection state, and last-success time;
- initial Queue discovery before local hints;
- multiplexed event application followed by debounced snapshot repair when a delta lacks enough
  information;
- terminal movement/removal only after authoritative state;
- cancellation of old subject/filter requests on navigation;
- bounded exponential backoff with jitter, one in-flight request per scope/job, and hidden-tab
  cadence reduction;
- teardown when no consumers remain, without discarding durable last-good state prematurely.

Test explicit state transitions for initial/loading/live/reconnecting/stale/incompatible/stopped.
Offline or failed requests retain the prior card with a freshness label. Retry and redelivery use
canonical lineage/fence data and cannot accept the prior attempt's late progress.

## 6. Progress and evidence component family

Build small typed primitives rather than a monolith:

- subject artwork/hierarchy header;
- friendly action/status/attention line;
- overall scope progress;
- current-work scope progress or indeterminate activity;
- elapsed/ETA/speed/FPS/throughput metrics only when present and credible;
- wait/retry/hold/cancelling/freshness callouts;
- bounded concurrent-subject summary;
- links/actions for Activity, detail, cancel, logs, and artifacts.

The compact variant fits Activity rows and feature-page strips. The expanded variant preserves the
same information hierarchy on detail pages. Announce meaningful subject/stage/status transitions
through a restrained live region; do not announce high-frequency ticks.

Golden fixtures must include movie, series, season, episode, media file, track, poster, model,
maintenance, parent batch, concurrent children, deleted subject, success, no-change, failure,
cancellation, retry, determinate, indeterminate, hybrid, immediate, stale, and reconnecting states.

## 7. Implementation phases

### Phase A0 — verify JMC5C and freeze client/consumer contracts

Create the shared timeline, record all baselines, verify completion/recovery/schema/API manifests,
inventory every tracker/bar/stage map/local-storage job ID/generated/manual DTO/API consumer, and
add source/contract freeze tests. Do not change product behavior.

### Phase A1 — establish frontend test infrastructure

Pin Vitest, jsdom, Testing Library, Playwright Chromium, and axe; add deterministic unit/component
and E2E configuration/scripts, representative smoke fixtures, failure artifacts, and documented
local commands. Tests use synthetic API fixtures or an owned disposable backend, never operator
media/database.

### Phase A2 — replace the handwritten job client boundary

Implement generated-path request helpers and runtime validators, split bounded diagnostic
methods, remove legacy adapters where no longer referenced, regenerate contracts, and prove every
job/configuration/feature API wrapper matches a real route.

### Phase A3 — implement the shared store and event reconciliation

Build Queue discovery, EventSource, cursor/sequence application, repair snapshots, abort/backoff,
visibility behavior, lifecycle partition movement, and teardown. Test dropped/duplicate/late/reset
events, request overlap, navigation races, API/PostgreSQL restart simulations, retry/redelivery,
and cleared local storage.

### Phase A4 — implement shared progress/evidence components

Build compact/expanded cards and primitives, complete all progress/subject/terminal/freshness
goldens, add keyboard/live-region/mobile component tests, and statically prevent new raw stage or
arbitrary-percent rendering.

### Phase A5 — JMC6A certification and history compaction

Run all focused tests, complete retained backend comparison, Ruff, schema/Alembic checks,
OpenAPI/type drift, frontend unit/check/lint/build/E2E gates, and `git diff --check`. Record the
exact JMC6B consumer inventory, then perform section 9 only if every gate succeeds.

After every phase, commit, append the shared timeline, and immediately continue to the next phase.
A completed phase is not a reason to stop.

## 8. Acceptance matrix

- generated request/response/error typing and runtime invalid-response rejection;
- bounded URLs, cursors, maximums, aborts, and error envelopes;
- first-load discovery with empty/corrupt local storage;
- one EventSource across many jobs/pages and no per-job stream/poller creation;
- missed notification, Last-Event-ID replay, retention reset, duplicate/late event, and wrong
  progress sequence/fence;
- overlapping refresh suppression, route change abort, bounded backoff, hidden tab, reconnect,
  API loss/recovery, and stale freshness;
- authoritative terminal movement and last-progress preservation;
- monotonic overall plus legitimate current `scope_id` reset;
- honest determinate/indeterminate/hybrid/none rendering and credible metrics only;
- all subject variants, concurrent children, missing artwork/deleted subjects, wait/retry/hold,
  warning/error/attention, and allowed-action combinations;
- keyboard/focus/live-region behavior, narrow viewport, axe scan, and no color-only meaning;
- no secret/raw PgQueuer ID/path/raw stage key in client state or rendered fixtures;
- no new backend failure, skip, `xfail`, frontend warning, lint exception, or generated drift.

JMC6A does not need to eliminate JMC5C's 17 retained backend failures or inherited 16 frontend
warnings; those numbers may only decrease. JMC6B/C own the complete zero-warning/zero-failure gate.

## 9. Mandatory final-only history compaction

Perform only after A5 succeeds:

1. Verify a clean, linear, configured-author, JMC6A-only, unpushed range after exact
   `jmc5c-complete`. Stop for merges, unrelated/concurrent commits, uncertain ownership, or pushed
   phase history.
2. Commit the final pre-squash timeline entry with phase hashes, complete verification,
   deviations, pending operator work, pre-squash tip, and intended tag `jmc6a-complete`.
3. Create a timestamped recovery branch and annotated tag plus a verified repository-external Git
   bundle, preferably under `/home/quartermaster/backups/Marquee/`.
4. Record the certified tree hash; through RTK soft-reset to the plan base and create one
   configured-author commit: `jmc6a: establish shared activity client`.
5. Prove tree identity, sole-parent ancestry, clean tree, recovery refs, and bundle; create local
   annotated tag `jmc6a-complete`.
6. Do not edit the timeline after compaction, push, force-push, delete recovery material, or start
   JMC6B.

No agent/model attribution is permitted in commits, tags, files, bundles, or the timeline.

## 10. Out of scope

- the complete Projection Room Queue/History/detail/Operations page rebuild (JMC6B);
- migration of every initiating feature page (JMC6B);
- final legacy-test cleanup, zero-failure certification, GitHub Actions rewrite (JMC6C);
- authentication, public reset replacement, Docker hardening, and webhooks;
- remote push or activation.

## 11. Handoff

Report compact base/tag/hash/tree and recovery material; exact generated schema/client versions;
test dependency versions; store/event/repair budgets; fixtures covered; remaining legacy consumers,
warnings, and backend failures; manual browser checks performed; and exact JMC6B starting state.
