# JMC6 — Activity and Certification Timeline

Shared implementer timeline for Chunk 6 (JMC6A → JMC6B → JMC6C). The JMC6A
implementer creates this file; JMC6B and JMC6C read it, verify its claims against
`git log`, tags, and the tree, and append. Terse and factual so a different agent
can take over cold. No agent/model attribution appears here or in any commit.

Authoritative plans:
[JMC6A](jmc6a-shared-activity-client-and-progress.md) ·
[JMC6B](jmc6b-projection-room-and-feature-pages.md) ·
[JMC6C](jmc6c-zero-green-certification-and-ci.md).

---

## JMC6A — plan base, environment, and baselines (Phase A0)

### Plan base and ownership

- **Plan base:** `c8413aa` (`chunk 6 planned`), tree
  `556735fa38b92e88cc541afdb6dc22f454be1cca`, sole parent `30cc8c1` (`jmc5c-complete`). `c8413aa` adds only the JMC6A/B/C plan docs and
  doc cross-references (10 files, +873/-1); it is the first-plan-of-chunk base, exactly
  as `jmc5a`/`a1973e0` was based on `chunk 5 planned`/`cadfdb4`. JMC6A phase commits build
  on `c8413aa`; the final-only squash soft-resets to `c8413aa`.
- **Branch:** `job-manager` (also `origin/job-manager`, which is exactly at `c8413aa`).
- **Author:** `Gautam Chaudhri <gautam.chaudhri@gmail.com>` (repository-configured; the
  only permitted author).
- **Tooling:** Serena (Python symbol/reference analysis), ByteRover (`brv`, project
  context — note its job-client entries predate JMC5C and were disregarded in favour of
  code + design docs), RTK proxy (all dev + Git commands). All three verified available at
  A0 start.

### JMC5C prerequisite verification (all proven)

- Annotated tag `jmc5c-complete` (`git cat-file -t` → `tag`) resolves to compact commit
  `30cc8c1` with tree `1508003d95220a1dc2694771373e238ec52f450d`; tagger is the configured
  author.
- Ancestry `jmc5c-complete..HEAD` is linear (no merges); `c8413aa` has sole parent
  `30cc8c1`. `c8413aa` (current HEAD) touches only design docs, so the live code tree is
  identical to `jmc5c-complete`.
- Recovery material present: branch `recovery/jmc5c-20260716T042308Z`, annotated tag
  `recovery/jmc5c-pre-squash-20260716T042308Z`, and external bundle
  `/home/quartermaster/backups/Marquee/marquee-jmc5c-pre-squash-20260716T042308Z.bundle`.
- Schema/OpenAPI/executor fingerprints (from the JMC5C final certification, re-confirmed
  live at A0): Alembic sole head `0006_jmc4c`; Marquee contract
  `0006_jmc4c|9158c083cfe84e8975473bd681a67036bb5d5485ad41a42b108c0a89ebac9701`; PgQueuer
  `1.1.1|durable|19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a`;
  deterministic OpenAPI 3.1.0 with 201 paths; 61 definitions / 42 enabled leaves = 42
  canonical handlers / 19 reserved. Working tree clean.

### Environment

- Python 3.13.14 (`.venv`; pytest/ruff) — system Python 3.14.6. Node v22.22.2, npm 10.9.7.
- Ruff 0.15.x. Svelte 5.56 / SvelteKit 2.63 / Vite 8 / TypeScript 5.9.3 /
  `openapi-typescript` 7.13.0. No frontend test framework yet (A1 adds it).
- PostgreSQL: **owned disposable** instance at `127.0.0.1:55450/marquee_test`
  (`/tmp/marquee-jmc5c-pg/data`, owned by `quartermaster`) — the JMC5C certification
  target. The `.env` `DB_URL` points at the live `127.0.0.1:5432/marquee`; every test run
  overrides `DB_URL=postgresql+asyncpg://marquee:***@127.0.0.1:55450/marquee_test` so no
  live database or media root is touched (plan boundary: owned disposable only).

### A0 baselines (recorded for the A5 comparison)

- **Backend:** `1249 passed, 17 failed, 2 warnings` in ~91 s on `:55450/marquee_test` —
  byte-identical to the JMC5C-certified baseline. The 17 are the known-retained OCR
  snapshot/labels, OCR-workers hardware-policy (env-specific on this box), run-endpoint,
  taste-artifact, and Whisper-verdict cases reserved for Chunk 6. Exact identities frozen
  in `tests/fixtures/jmc6a/retained_backend_failures.txt`. (Running the default `.env`
  `:5432` instance instead produces 23 failed + 11 `test_jmc1_migration` errors — an
  environment mismatch, not a code baseline; `:55450` is authoritative.)
- **Ruff:** `ruff check marquee tests` → clean.
- **OpenAPI:** `scripts/export_openapi.py --check` → current, 3.1.0, 201 paths.
- **Frontend:** `npm run check` → 0 errors, 16 warnings in 8 files; `npm run lint`
  (prettier + eslint) → clean; `npm run api:generate:check` → no type drift; `npm run
  build` (adapter-node) → success.

### A0 client/consumer inventory (frozen)

The exact client problems JMC6A replaces, and the migration surface JMC6B inherits:

- **Legacy tracker `lib/jobs.ts`** (`trackJob`/`jitterMs`/`JobProgressDetail`): polls one
  job at a time via `setInterval`; `eventsUrl` is ignored (`void opts.eventsUrl`); accepts
  loose progress dicts. **24 importer files.**
- **Handwritten client `lib/api/jobs.ts`**: converts generated canonical responses back
  into legacy DTOs (`JobSnapshot`/`JobListItem`/`JobDetail` with retired
  `resource_request`/`media_job_id`/`resources[]`/`workers[]`); `progressFromCanonical`
  reinterprets typed progress client-side (A12 violation); `getJobDetail` fans out 9
  parallel requests (snapshot+presentation+attempts+events+children+4 raw docs).
  **24 importer files.**
- **Media-jobs adapter `lib/api/media-jobs.ts`**: `getMediaJob`/`confirmJob`/`cancelJob`
  over the canonical snapshot. **7 importer files.**
- **`lib/sse.ts`**: poll-only shim ("until Chunk 3 supplies the multiplexed durable event
  stream"); opens no EventSource. Superseded by the JMC6A store.
- **localStorage active-job/batch registries** (server-authority violations, A05):
  `projection-room/+page.svelte` (`ACTIVE_KEY` job-id array), plus per-page batch/job ids in
  `pipeline/movies`, `pipeline/tv`, `letterbox/+`, `letterbox/movies`,
  `hdr/movies/[id]`, `audio-subs/movies/[id]`. (Legit prefs — `theme.ts`,
  `RunResultsView` `stackView` — are out of scope.)
- **Runtime to keep (A03):** `lib/api/client.ts` typed fetch runtime (`apiGet`/`apiSend`
  over generated `paths`, `ApiError`, AbortController timeout).

Backend contracts JMC6A depends on already exist (built in JMC2C/JMC3B): the multiplexed
SSE stream `GET /api/jobs/events/stream` (frames `id: <cursor>` / `event: <event_key>` /
`data: <JobEventFrame>`, keepalive comments, a `stream.reset_required` frame for retention
gaps, 503 when the tailer is unhealthy, `Last-Event-ID`/`after` cursor), plus
`GET /api/jobs?view=queue|history`, `/snapshot`, `/presentation`, bounded
`/attempts|/events|/children|/artifacts|/raw/{kind}`, and command endpoints. No new
multiplexed stream or per-job SSE is needed — the JMC6A stop gate on that point does not
trigger.

### A0 deliverables (freeze, no behaviour change)

- `tests/test_jmc6a_contract_freeze.py` (5 tests, static, no DB): freezes the backend job
  wire contract (openapi version; 22 job/activity paths ⊇ frozen methods; 23 core schema
  shapes ⊇ frozen props/required/enums; one multiplexed stream and no per-job event
  stream) and the frontend legacy-consumer inventory (can only shrink — a new import of
  the legacy tracker/client/media-jobs fails; A16 guard).
- `tests/fixtures/jmc6a/client_contract_freeze.json`,
  `tests/fixtures/jmc6a/legacy_client_consumers.json`,
  `tests/fixtures/jmc6a/retained_backend_failures.txt`.

### A0 status

- **Completed:** JMC5C verification; baselines recorded; inventory frozen; freeze
  tests/fixtures added and green (`5 passed`, ruff clean); this timeline created.
- **Commit:** `8bf1cd5` (`jmc6a a0: verify jmc5c and freeze client and consumer contracts`).
- **Current phase after A0:** A1 — establish frontend test infrastructure.
- **Exact next steps (A1):** pin Vitest + jsdom + `@testing-library/svelte`/`jest-dom` +
  `@playwright/test` (Chromium) + `@axe-core/playwright`; add `vitest.config.ts` (jsdom,
  deterministic), `playwright.config.ts` (Chromium only, failure artifacts), and `test:unit`
  / `test:e2e` scripts; add a smoke unit test and a smoke E2E against a synthetic fixture
  or owned disposable backend (never operator media/database); document local commands.
  Keep `npm run check`/`lint`/`build` green and the retained backend baseline at 1249/17/2.
- **Deviations:** none.
- **Pending operator actions:** none (all gates run against owned disposable resources).

---

## JMC6A — frontend test infrastructure (Phase A1)

### Pinned test dependencies (exact, no ranges; `engine-strict` honoured)

`vitest@4.1.10` (peer `vite ^6||^7||^8` — supports the installed Vite 8.0.16),
`@testing-library/svelte@5.4.2` (Svelte 5), `@testing-library/jest-dom@6.9.1`,
`@testing-library/user-event@14.6.1`, `jsdom@29.1.1`, `@playwright/test@1.61.1`,
`@axe-core/playwright@4.12.1`. Chromium browser installed via
`npx playwright install chromium`.

### Configuration and scripts

- `vitest.config.ts` — two deterministic projects under one `sveltekit()` root:
  `client` (jsdom, `*.svelte.test.ts`, `svelteTesting()` plugin, `clearMocks`,
  `vitest-setup-client.ts`) and `server` (node, `*.test.ts`). `test:unit` runs
  `vitest run` (no watch).
- `vitest-setup-client.ts` — registers `@testing-library/jest-dom/vitest`
  matchers and Testing Library auto-cleanup at runtime.
- `src/lib/testing/jest-dom.d.ts` — surfaces jest-dom matcher types to
  `svelte-check` for every `*.svelte.test.ts` (the root setup file is outside the
  SvelteKit tsconfig program, so its module augmentation is re-declared here).
- `playwright.config.ts` — Chromium only, `retries:0`, `workers:1`,
  `fullyParallel:false`; failure artifacts (`trace: retain-on-failure`,
  `screenshot: only-on-failure`) under git-ignored `test-results/`; two managed
  hermetic `webServer`s (synthetic backend + built adapter-node app with its API
  proxy pointed at the synthetic backend via `MARQUEE_API_URL`). `test:e2e` runs
  `playwright test`. Aggregate `test` script runs unit then E2E.
- `e2e/support/synthetic-backend.mjs` — tiny Node HTTP server returning
  well-shaped empty canonical job/activity/system JSON; touches no external
  service, media, or database.
- `frontend/.gitignore` — ignores `test-results/`, `playwright-report/`,
  `playwright/.cache/`, `coverage/`.

### Smoke fixtures/tests (all hermetic)

- `src/lib/sort-title.test.ts` (server) — 3 pure-logic tests.
- `src/lib/testing/Smoke.svelte` + `smoke.svelte.test.ts` (client) — 2 render
  tests proving jsdom + Testing Library + jest-dom.
- `e2e/smoke.spec.ts` — 2 Chromium tests: app-shell render against the synthetic
  backend, and an axe scan that proves the accessibility harness (A1 does not
  assert legacy-page a11y debt; JMC6A's own components gate on zero violations in
  A4).

### A1 gates (green)

`npm run test:unit` → 5 passed (2 files). `npm run test:e2e` → 2 passed.
`npm run check` → 0 errors, 16 warnings in 8 files. `npm run lint` → clean.
`npm run build` → success. `git diff --check` → clean. `test_jmc6a_contract_freeze`
→ 5 passed (frontend additions import no legacy module; inventory unchanged).
Backend untouched by A1, so the 1249/17/2 retained baseline stands.

### A1 status

- **Completed:** test dependencies pinned/installed; Vitest client/server projects,
  Playwright Chromium + axe, and hermetic synthetic backend configured; smoke
  unit + E2E fixtures added and green; local commands documented (`npm run
  test:unit`, `test:e2e`, `test`).
- **Commit:** `deb6db5` (`jmc6a a1: add frontend test infrastructure`). Carries
  the A0 timeline hash-fill.
- **Deviation (benign):** `vitest`/`build` print a "No Svelte config file found"
  info line because the project intentionally inlines its SvelteKit config in
  `vite.config.ts` (no `svelte.config.js`). It is stdout info, not a
  `svelte-check`/lint warning, and does not affect the 0-error/16-warning gate;
  runes components auto-detect runes mode, so test compilation matches
  production. `vite.config.ts` was left unmodified to protect the certified build.
- **Current phase after A1:** A2 — replace the handwritten job client boundary.
  (Done — see below.)
- **Exact next steps (A2):** implement generated-path request helpers and narrow
  runtime validators (destructive commands, job-creation, terminal success/outcome,
  cursor envelopes, SSE event envelopes) on top of the kept `lib/api/client.ts`;
  split `getJobDetail()`'s 9-way fan-out into bounded per-resource methods (list,
  snapshot, presentation, attempts, events, children, artifacts, raw, logs) with
  explicit cursors/limits; delete legacy DTO adapters where unreferenced;
  regenerate OpenAPI/TS deterministically and prove every wrapper matches a real
  route. Keep all A1 gates green.
- **Pending operator actions:** none.

---

## JMC6A — shared job client boundary (Phase A2)

New module `frontend/src/lib/activity/` — the single generated-path job client the
store (A3) and cards (A4) consume. Built on the **kept** fetch runtime
(`lib/api/client.ts`, A03); it does not adopt a second runtime SDK.

- `types.ts` — canonical wire aliases straight from generated `components`/`paths`
  (the generated schema is the only wire authority, A02). `ListJobsQuery` and
  `RawDocumentKind` are derived from the generated path operations.
- `validators.ts` — narrow runtime guards for the critical boundaries (A03):
  `parseListEnvelope`, `parseSnapshot` (terminal success/outcome), `parseCommandResponse`
  (destructive commands), `parseJobSubmission` (job creation), `parseJobEventFrame`
  (SSE envelope, with a `version === 1` schema-version guard) plus `jobApiErrorCode`
  for stale-command conflict detection. A malformed/unknown-version response throws
  `IncompatibleResponseError` so the UI shows a stale/incompatible condition rather
  than treating bad data as success (§4). Guards never coerce, so omitted/`null`/`0`/
  `false`/`[]` stay distinct.
- `client.ts` — focused methods replacing the legacy `getJobDetail()` 9-way fan-out:
  `listJobs`, `getSnapshot`, `getPresentation`, `getBatchSummary`, `listAttempts`,
  `listEvents`, `listChildren`, `listArtifacts`, `listAttemptLogs`, `getRawDocument`;
  commands `cancelJob`/`pauseJob`/`resumeJob`/`retryJob`/`setJobPriority`/`bulkJobActions`
  (each validated); and the single multiplexed SSE endpoint `JOB_EVENT_STREAM_URL`
  (`/api/jobs/events/stream`) + `jobEventStreamUrl(after?)`. Every caller passes
  cursors/limits explicitly; no helper retrieves every diagnostic collection at once.
- Bounded-list endpoints use `cursor` (not `after`; `after` is SSE-stream-only) —
  svelte-check caught a test that used the wrong key, confirming the generated types
  enforce route correctness.

**Route-reality proof:** `apiGet`/`apiSend` type their path against the generated
`paths`, so a wrapper cannot name a non-existent route — `npm run check` (green) is the
compile-time proof; `client.test.ts` additionally records verb/URL/body/validation at
runtime. **A16 static guard:** the A0 `test_jmc6a_contract_freeze` can-only-shrink
inventory is the guard against new legacy-tracker imports; a blanket eslint
`no-restricted-imports` is deferred to JMC6B (it would flag all 24 current importers
before their migration). **Legacy removal** is deferred: every legacy adapter in
`lib/api/jobs.ts`/`media-jobs.ts`/`lib/jobs.ts` still has feature-page consumers that
JMC6B migrates; A2 is purely additive and imports no legacy module.

- **No backend/OpenAPI change** was required (all list/snapshot/SSE contracts already
  exist and are correct), so the retained backend baseline 1249/17/2 stands untouched.
- **A2 gates (green):** `test:unit` → 31 passed (4 files; 26 new activity tests +
  5 smoke); `check` → 0 errors, 16 warnings; `lint` → clean; `api:generate:check` →
  no drift; `build` → success; `test_jmc6a_contract_freeze` → 5 passed (inventory
  unchanged); `git diff --check` → clean.
- **Commit:** `bf99b41` (`jmc6a a2: add generated-path job client and validators`).
- **Deviations:** none (legacy-removal and eslint import-guard intentionally deferred
  to JMC6B as above).
- **Current phase after A2:** A3 — shared store and event reconciliation.
- **Exact next steps (A3):** build the browser-session `JobProgressStore` as a DI state
  machine (fetch/EventSource/timers/visibility/clock injected) using the A2 client:
  keyed Queue/list scopes with reference counting; one canonical compact record + optional
  snapshot per job; global event cursor + per-job progress sequence/fence; one multiplexed
  EventSource with `parseJobEventFrame`; debounced abortable hidden-tab-aware snapshot
  repair with bounded jittered backoff and one in-flight request per scope/job; retention
  reset → bounded reconciliation; terminal movement only after authoritative state; teardown
  without dropping durable last-good state. Test initial/loading/live/reconnecting/stale/
  incompatible/stopped transitions and dropped/duplicate/late/reset events, overlap,
  navigation races, API/PostgreSQL restart simulations, retry/redelivery, and cleared storage.
- **Pending operator actions:** none.

---

## JMC6A — shared store and event reconciliation (Phase A3)

- Added the browser-session `JobProgressStore` as a dependency-injected Svelte state machine.
  Keyed Queue scopes are reference-counted; compact rows/snapshots are held once per canonical
  job; presentations, children, logs, artifacts, and raw documents remain lazy. The root layout
  creates and provides one request-local store through Svelte context, so SSR requests do not
  share mutable state.
- One multiplexed same-origin EventSource carries the durable global cursor. Duplicate/late global
  cursors, progress sequences, canonical fences, and snapshots are rejected. Retention reset
  frames run bounded scope discovery plus compact snapshot repair; absence from a bounded first
  Queue page is never treated as proof of terminal state.
- Snapshot repair is debounced, abortable, reference-aware, hidden-tab-aware, and limited to one
  in-flight request per scope/job. Retry uses bounded jittered exponential backoff and every
  debounce/retry/visibility/periodic timer is cancelled on teardown. Stream/API loss retains the
  last good card and changes only connection/freshness state; only an authoritative snapshot moves
  a job to History. Snapshot schema version 1 is now validated before applying state.
- Deterministic store coverage includes initial discovery, one stream across scopes, ref-counted
  teardown, navigation abort, hidden/visible cadence, bounded-page absence, progress/lifecycle
  repair, duplicate/late/reset/malformed events, overlap coalescing, terminal preservation,
  same/new/old fences, reconnect/stale/incompatible/stopped states, API/PostgreSQL restart,
  retry cancellation, cleared local storage, and cursor-resumed recreation.
- **Commit:** `f7506237a21ee68f4421eff4934334f145fefbe0`
  (`jmc6a a3: add shared progress store and reconciliation`), sole parent `bf99b41`, configured
  repository author.
- **Verification:** `npm run test:unit` → 59 passed; `npm run test:e2e` → 2 Chromium tests passed
  including axe; `npm run check` → 0 errors and the inherited 16 warnings in 8 files; frontend
  lint/build and deterministic generated-type drift passed; JMC6A freeze → 5 passed; Ruff passed;
  `git diff --check` passed. Complete retained backend comparison on the owned disposable
  PostgreSQL target is **1254 passed, 17 retained failures, 2 warnings in 93.19s** — exactly the
  JMC5C 1249-pass baseline plus the five A0 freeze tests, with unchanged failure membership.
- **Current phase:** A4 — shared progress/evidence components.
- **Exact next steps:** implement typed subject, action/status, overall/current progress,
  metrics, wait/freshness, concurrent-subject, action/link, and restrained live-region primitives;
  compose compact/expanded `JobProgressCard` variants; add the complete subject/progress/terminal/
  freshness golden matrix plus keyboard, live-region, mobile, and axe coverage; add static guards
  against raw-stage humanization and arbitrary client percentages; then run all frontend,
  retained-backend, contract, generated-type, Ruff, and diff gates.
- **Deviation:** the resumed worktree contained an uncommitted task-aligned A3 draft and the A2
  timeline hash fill before this session began. It was preserved, audited, completed, and gated;
  no unrelated file or commit was present. The first sandboxed pytest/E2E attempts were blocked by
  local socket permissions and were rerun successfully against only the owned disposable database
  and hermetic local web servers with the required permission.
- **Pending operator actions:** none. No operator database/media root, external service, remote ref,
  or non-disposable storage was touched.

## JMC6A — shared progress and evidence components (Phase A4)

- Added generated-contract-driven primitives for subject hierarchy/artwork fallback, friendly
  action/status/attention, determinate/indeterminate overall and current scopes, server-provided
  metric cards, wait/retry/hold/cancelling/freshness notices, and bounded concurrent subjects.
  `JobProgressCard` composes compact and expanded variants with Activity/detail/log/artifact links
  and fence-token cancel submission. It renders the server percentage and `stage_label` directly;
  it neither calculates a percentage nor exposes/humanizes `stage_key`.
- Golden fixtures cover movie, series, season, episode, media file, track, poster, model,
  maintenance, parent batch, concurrent children, deleted subjects, success, no-change, failure,
  cancellation, retry/wait/hold/error attention, determinate, indeterminate, hybrid, immediate,
  stale, and reconnecting states. Restrained live-region text excludes percentage ticks. Native
  links/buttons, keyboard focus, 360 px bounds, missing artwork, terminal progress preservation,
  and allowed-action/fence combinations are asserted.
- Added source-contract tests preventing raw-stage humanization, client percent arithmetic,
  legacy job imports, and local/session storage in the shared components. A runtime-locked E2E
  fixture route is available only with `MARQUEE_E2E_FIXTURES=1`; real Chromium reports zero axe
  violations for the expanded component and proves mobile containment. Normal runtime requests to
  the fixture route receive 404.
- **Commit:** `21509194b161b1a34e842a78df68ed51c7ed9028`
  (`jmc6a a4: add shared progress cards`), sole parent `70dca13`, configured repository author.
- **Verification:** `npm run test:unit` → **95 passed** (7 files); `npm run test:e2e` → **4
  Chromium tests passed** (component keyboard/zero-axe/mobile plus retained shell smoke); `npm run
  check` → 0 errors and the inherited 16 warnings in 8 files; lint/build and deterministic
  OpenAPI/type drift passed. JMC6A+JMC5C contract freezes → **12 passed**; Ruff and `git diff
  --check` passed. Complete retained comparison on the owned disposable PostgreSQL target is
  **1254 passed, 17 retained failures, 2 warnings in 93.69s**, with the exact frozen failure
  membership and no skip/xfail/error.
- **Current phase:** A5 — final JMC6A certification and history compaction.
- **Exact next steps:** record the exact JMC6B consumer inventory; rerun the complete frontend,
  backend, contract, Ruff, Alembic/schema, OpenAPI/type-drift, E2E, and diff gates from the A4 tree;
  verify the phase range is linear, JMC6A-only, unpushed, and based on the plan commit whose sole
  parent is `jmc5c-complete`; curate final ByteRover context; append the final certification entry;
  create timestamped recovery branch/tag and verified external bundle; record the certified tree;
  squash to the one required commit, prove tree identity, and create annotated `jmc6a-complete`.
- **Deviations:** no product/design deviation. Serena structural/reference search remained
  available, but its diagnostics endpoint misclassified TypeScript/Svelte as Python; authoritative
  `svelte-check` and ESLint diagnostics were used for frontend files. Socket-restricted sandbox
  attempts were rerun with permission against only the owned database and hermetic web servers.
- **Pending operator actions:** none. No operator database/media root, external service, remote ref,
  or non-disposable storage was touched.

---

## JMC6A — final certification and JMC6B handoff (Phase A5)

### Certified phase history and tree

- Phase commits after plan base `c8413aa`: A0 `8bf1cd5`; A1 `deb6db5`; A2 `bf99b41`;
  A3 `f750623` plus timeline `70dca13`; A4 `2150919` plus timeline `3b730a2`; this A5
  timeline entry is the final pre-squash commit. The range has seven commits before this entry,
  no merge, only the configured repository author, no remote containment, and no unrelated path.
  `origin/job-manager` and the merge base remain exactly `c8413aa`; its sole parent is the compact
  commit peeled from annotated `jmc5c-complete` (`30cc8c13d69ae3337bebf8ec909e50895ac30d0e`).
- Certified product tree before this final timeline-only entry:
  `e9e0a3a77bbe88634de02f6959eaf3b532a7bba9`. The final certified/pre-squash tree is the tree of
  the commit containing this entry and is captured before reset for byte-identical comparison.
- Intended recovery material: branch `recovery/jmc6a-20260716T183418Z`, annotated tag
  `recovery/jmc6a-pre-squash-20260716T183418Z`, and complete external bundle
  `/home/quartermaster/backups/Marquee/marquee-jmc6a-pre-squash-20260716T183418Z.bundle`.
  Intended compact tag: annotated `jmc6a-complete`.

### Final certification

- Complete retained backend comparison on the owned disposable PostgreSQL 18.3 target at
  `127.0.0.1:55450/marquee_test`: **1254 passed, 17 retained failures, 2 warnings in 90.13s**.
  Failure membership exactly matches `tests/fixtures/jmc6a/retained_backend_failures.txt`; there
  is no new failure, error, skip, xfail, warning, quarantine, or suppression. Combined JMC6A/JMC5C
  freezes are **12 passed**; Ruff is clean.
- Alembic sole head/current is `0006_jmc4c`; `alembic check` reports no new upgrade operations;
  the owned-cluster migration/schema-contract suite is **14 passed**. The unchanged Marquee and
  PgQueuer contract fingerprints remain the A0 values. Deterministic OpenAPI remains 3.1.0 with
  201 paths and generated TypeScript has no drift.
- Frontend: **95 unit tests passed**; `svelte-check` is 0 errors with the inherited 16 warnings in
  8 files; Prettier/ESLint, adapter-node build, and generated-type drift are green. **4 Chromium
  E2E tests passed**, including keyboard operation, zero axe violations on the expanded shared
  card, 360 px containment, and retained shell smoke. No separate manual operator browser session
  was performed; the real built application was exercised by managed Chromium against the
  hermetic synthetic backend. `git diff --check` passes and the worktree is clean.

### Exact JMC6B consumer inventory

- `tests/fixtures/jmc6a/legacy_client_consumers.json` is the exact path-level handoff: **24**
  importers of handwritten `lib/api/jobs.ts`, **24** importers of per-job `lib/jobs.ts`, and **7**
  importers of `lib/api/media-jobs.ts`. The A0 can-only-shrink freeze proves no importer was added.
- Seven feature routes still use local storage to remember active work and must move to server
  discovery in JMC6B: `projection-room/+page.svelte`, `pipeline/tv/+page.svelte`,
  `pipeline/movies/+page.svelte`, `letterbox/+page.svelte`, `letterbox/movies/+page.svelte`,
  `hdr/movies/[id]/+page.svelte`, and `audio-subs/movies/[id]/+page.svelte`. Theme and pipeline
  stack-view preferences are legitimate non-job preferences and remain out of scope.
- JMC6B starts from the generated-path `lib/activity/client.ts`, root-provided session store,
  compact/expanded cards, 95-unit/4-E2E baseline, and this inventory. It owns Projection Room and
  feature-page migration; JMC6A did not begin that work.

### Status

- **Completed:** A0 through A5, all certification gates, JMC6B inventory, and final ByteRover
  project-context curation request. No product/design deviation.
- **Pre-squash tip:** the commit containing this entry, resolved immediately before recovery
  creation. The final-only procedure must preserve it with the intended recovery material, record
  its tree, soft-reset to `c8413aa`, create exactly `jmc6a: establish shared activity client`,
  prove tree identity and sole-parent ancestry, then create annotated `jmc6a-complete`.
- **Deviations:** Serena diagnostics misclassified frontend TypeScript/Svelte as Python, while
  Serena source/reference analysis remained available; repository `svelte-check` and ESLint were
  the frontend diagnostic authorities. Sandboxed socket attempts were rerun with permission only
  against disposable localhost services. No implementation or scope deviation.
- **Pending operator actions:** none for JMC6A. Never push, delete recovery material, or begin
  JMC6B as part of this phase.

---

## JMC6B — plan base, prerequisite gate, and inventories (Phase B0)

### Base and JMC6A verification

- **Exact base:** annotated `jmc6a-complete` peels to compact commit
  `a0355e07515c8cfee77ed7a2e31fc38d340eb445`, tree
  `7849db9ab8558d8007d23909c44d6fb6f261e2f7`, with sole parent
  `c8413aacc2552301e963084bdcad953b7dc1b78a` (`chunk 6 planned`). The configured and tag authors
  are `Gautam Chaudhri <gautam.chaudhri@gmail.com>`. `job-manager` and
  `origin/job-manager` were exactly at the compact base before B0; the worktree was clean.
- Recovery branch `recovery/jmc6a-20260716T183418Z`, annotated recovery tag
  `recovery/jmc6a-pre-squash-20260716T183418Z`, and external bundle
  `/home/quartermaster/backups/Marquee/marquee-jmc6a-pre-squash-20260716T183418Z.bundle` all
  exist. `git bundle verify` reports complete history and a valid SHA-1 bundle; the recovery ref
  preserves pre-squash commit `b1a2a9483b926c29af9add4ab11f67deae1dbd6f`.
- The generated-path client, runtime validators, request-local root `JobProgressStore`, one
  multiplexed EventSource, compact/expanded `JobProgressCard`, frontend test harness, and JMC6A
  static contract guards are present. The exact handoff remains 24 handwritten job-client
  importers, 24 per-job tracker importers, and 7 media-job adapter importers; no consumer was
  added after JMC6A.

### Reproduced B0 baselines

- Environment: Python 3.13.14, Ruff 0.15.17, Node 22.22.2, npm 10.9.7, Playwright 1.61.1,
  Chromium 149.0.7827.55. OpenAPI is deterministic 3.1.0 with 201 paths.
- Complete retained backend comparison on the owned disposable PostgreSQL target
  `127.0.0.1:55450/marquee_test`: **1254 passed, 17 retained failures, 2 warnings in 90.72s**.
  Failure membership matches the JMC6A freeze exactly; no new failure, error, skip, or xfail.
  Ruff is clean. The B0/JMC6A/JMC5C static contract set is **17 passed**.
- Frontend baseline: **95 unit tests passed**; `svelte-check` reports 0 errors and the inherited
  **16 warnings in 8 files**; Prettier/ESLint, generated-client drift, and production build are
  green. The JMC6A browser baseline is 4 Chromium tests; B0 adds one deterministic full-page
  visual contract, producing **5 Chromium tests passed** with retained axe and 360 px coverage.
  `git diff --check` passes.

### Frozen B0 surface and API gaps

- `tests/fixtures/jmc6b/activity_surface_inventory.json` records the exact compact base,
  generated contract, ten fixture subject kinds, 16-warning file membership, seven local-storage
  job-authority routes, six Projection Room legacy components, duplicate Activity consumers, and
  19 inherited broad file-level ESLint suppressions. `tests/test_jmc6b_contract_freeze.py` permits
  removal/migration inventories only to shrink and freezes every bounded canonical job resource.
- `/activity`, the Sidebar/command-palette duplicate link, and `/api/activity` are still present
  at B0 and are assigned to B1 removal. Projection Room still owns local-storage discovery,
  per-job tracking, client-side filters, and eager host/system polling.
- Canonical list/snapshot/presentation/action/attempt/event/child/log/artifact/raw/SSE resources
  are present and bounded. Two narrow route-contract deltas are frozen: the current priority
  command is `PATCH` while JMC6B specifies `POST`, and Operations requires client fan-out across
  four untyped responses (`/api/system/status`, `/job-transport`, `/metrics`, `/metrics/history`).
  B2 aligns the priority method; B4 adds one typed bounded `/api/system/operations` snapshot while
  retaining separately bounded history. Neither change alters job semantics.

### Status

- **Completed:** JMC6A prerequisite proof; exact page/component/client/warning/API inventory;
  retained backend and frontend/browser baselines; static Activity/Operations contract freeze;
  deterministic Projection Room visual baseline. B0 phase commit is created immediately after
  this entry; its hash is appended in the post-commit checkpoint.
- **Current phase:** B1 — Activity shell, Queue, History, and navigation.
- **Exact next steps:** build generated-contract Queue/History list state with abortable URL-backed
  search/filter/sort and stable cursors; add attention strip, versioned density/column preferences,
  subject-first responsive rows/cards and bounded batch expansion; preserve last-good data through
  transient failure; update Sidebar/TopBar/command palette to one Activity destination; remove the
  `/activity` page and `/api/activity` route/tests; update the visual contract and run B1 gates.
- **Deviations:** no product or scope deviation. Browser fixture servers and the disposable
  PostgreSQL socket required the documented sandbox permission. The base priority verb and
  untyped Operations fan-out are recorded implementation gaps resolved in their assigned phases.
- **Pending operator actions:** none. No operator database, media root, external service, or remote
  ref was mutated.

### B0 post-commit checkpoint

- **Phase commit:** `d0636ba` (`jmc6b b0: verify jmc6a and freeze activity surfaces`), sole
  parent `a0355e0`, configured repository author, no unrelated path.
- **Verification:** retained backend **1254 passed / 17 expected failures / 2 warnings**; combined
  B0/JMC6A/JMC5C static contracts **17 passed**; Ruff clean; frontend **95 unit / 5 Chromium E2E**;
  check 0 errors/16 inherited warnings; lint, generated drift, build, and diff check green.
- **Current phase:** B1. **Next:** replace the legacy Projection Room shell with canonical
  Queue/History views and one lazy Operations entry point, then remove duplicate Activity UI/API.
- **Deviations/operator work:** unchanged; none.

## JMC6B Phase B1 — canonical Activity shell, Queue, History, and navigation

### Completed work

- `/projection-room` is the sole Activity destination. Sidebar, TopBar, and command palette now
  expose one Activity entry; the duplicate `/activity` page and obsolete `/api/activity`
  cross-domain aggregate are removed with their dedicated test. OpenAPI remains 3.1.0 with 201
  paths: bounded `GET /api/jobs/attention` replaces the obsolete aggregate and generated
  TypeScript is current.
- Queue and History use the JMC6A session store/card. The shared store now publishes bounded
  scope views, preserves last-good rows and cursors on transient failure, and appends stable
  cursor pages without introducing another stream or polling authority. URL-owned filters cover
  search, feature, type, subject, lifecycle/outcome, attention, trigger, batch/root,
  correlation, worker, execution class, and time; server allowlisted sorting remains canonical.
- The Activity surface includes the bounded attention strip/navigation badge, subject-first
  responsive rows/cards, versioned density/column preferences, explanatory loading/empty/stale/
  incompatible states, and abortable bounded batch expansion. Operations is secondary and its
  component is dynamically imported only when selected; the typed Operations contract remains
  assigned to B4.
- Query-state navigation uses URL history as authority with an explicit popstate projection.
  Filter replacement avoids history spam, view changes create navigable entries, old scopes are
  released/aborted, and browser back restores the prior view and filter values. Removing the
  layout's child key eliminated duplicate same-path page bodies during query navigation.
- The narrow list contract now supports `worker_id` and `execution_class`; worker filtering uses
  bounded attempt existence and class filtering uses the canonical execution policy. Historical
  JMC3B/JMC4A route freezes record the intentional bounded attention addition.

### Verification and status

- Retained full backend comparison: **1260 passed, the same 17 retained failures, 2 warnings in
  92.16s** on the owned disposable PostgreSQL target. Focused B1/JMC6A/historical route contracts
  are **18 passed**; Ruff is clean. No new failure, error, skip, xfail, warning, quarantine, or
  ignored test was added.
- Frontend: **103 unit tests passed**; `svelte-check` remains 0 errors with the inherited 16
  warnings in 8 files; Prettier/ESLint, deterministic schema, production build, and diff check
  are green. **7 Chromium tests passed**, covering the updated visual contract, keyboard card,
  390 px responsive Activity controls, URL back navigation, and zero axe violations on both the
  shared card and full Activity shell.
- **Current phase:** B2 — actions, batch drill-down, and lineage. **Exact next steps:** align the
  priority action verb, expose only server-returned row/bulk capabilities, implement bounded
  selection and partial-result/stale-conflict feedback, make batch drill-down failed-first and
  cursor-bounded, show retry successor/predecessor lineage, and certify commands beyond the first
  page.
- **Deviations:** no product/design deviation. The planned attention endpoint required updating
  two earlier exact-route freeze fixtures; their semantics remain frozen and focused gates pass.
  Sandboxed localhost browser/database work used only the documented disposable fixtures.
- **Pending operator actions:** none. No remote ref, operator database/media, or external service
  was mutated. The B1 phase hash is appended immediately after its configured-author commit.

### B1 post-commit checkpoint

- **Phase commit:** `f5629fa` (`jmc6b b1: rebuild activity queue and history`), sole parent
  `d0636ba`, configured repository author, linear and unpushed.
- **Verification:** retained backend **1260 passed / same 17 retained failures / 2 warnings**;
  focused route/read contracts **18 passed**; Ruff clean; frontend **103 unit / 7 Chromium E2E**;
  check 0 errors/16 inherited warnings; lint, schema, generated output, build, axe, responsive,
  navigation, visual, and diff checks green.
- **Current phase:** B2. **Next:** capability-driven single/bulk commands, failed-first bounded batch
  drill-down, stale-conflict/partial feedback, priority verb alignment, and retry lineage.
- **Deviations/operator work:** unchanged; none.

---

## JMC6B Phase B2 — capability actions, batch drill-down, and lineage

### Completed work

- Queue and History rows now render the canonical server-returned `allowed_actions` only. Single
  lifecycle commands carry the current fence token; priority uses the canonical `POST` route and
  displays the backend execution class; logs, artifacts, and detail links are direct canonical
  resources. Retry success exposes the returned successor rather than inferring lineage locally.
- Bounded bulk selection is capped at 100 jobs. Mixed execution classes disable bulk priority,
  result feedback separates successes from stale/conflict failures, successful rows are cleared,
  and failed selections remain available for review and retry. The shared store remains the only
  client authority and now carries canonical row fence tokens into commands.
- Batch expansion is abortable, cursor-bounded to 20 children per request, failed/adverse outcomes
  sort first by default, and the stable cursor includes outcome rank, creation time, and job ID.
  Backend tests prove failed-first traversal across 105 children and action discovery beyond the
  first page.
- Canonical row/snapshot presentation now includes generated `fence_token` and
  `execution_class`; historical route/client freezes and poster presentation goldens record the
  intentional contract growth. Scope acquisition uses Svelte `untrack`, preventing reactive map
  mutations from repeatedly releasing and aborting the same request.

### Verification and status

- Retained full backend comparison: **1262 passed, the same 17 retained failures, 2 warnings in
  94.39s** on the owned disposable PostgreSQL target. Focused read/command/presenter/freeze gates
  are **44 passed**, including stable failed-first cursors and canonical priority method coverage;
  Ruff is clean. No new failure, error, skip, xfail, warning, quarantine, or ignored test was added.
- Frontend: **105 unit tests passed**; `svelte-check` remains 0 errors with the inherited 16
  warnings in 8 files; Prettier/ESLint, deterministic schema, generated canonical types,
  production build, and diff check are green. **8 Chromium tests passed**, including the inspected
  Activity visual golden, 390 px responsiveness, navigation restoration, reconnect state,
  capability gating, retry successor lineage, and zero axe violations.
- **Current phase:** B3 — canonical job detail and diagnostics. **Exact next steps:** replace the
  legacy detail page with typed Overview/Timeline/Logs/Artifacts/Raw Data/Execution panels; consume
  backend presentation sections without inspecting handler payloads; make diagnostic resources
  tab-visible, lazy, abortable, cursor-bounded, attempt-aware, and virtualized; certify retention,
  truncation, redaction, deleted-subject, lineage, download, and presenter-family behavior.
- **Deviations:** no product/design deviation. The visual fixture now explicitly waits for its
  synthetic reconnect state so the accepted screenshot is deterministic. Localhost browser and
  database gates used only the documented owned disposable fixtures.
- **Pending operator actions:** none. No remote ref, operator database/media, or external service
  was mutated. The B2 phase hash is appended immediately after its configured-author commit.

### B2 post-commit checkpoint

- **Phase commit:** `fb4326f` (`jmc6b b2: add activity actions and lineage`), sole parent
  `f5629fa`, configured repository author, linear and unpushed.
- **Verification:** retained backend **1262 passed / same 17 retained failures / 2 warnings**;
  focused read/command/presenter/freeze contracts **44 passed**; Ruff clean; frontend **105 unit /
  8 Chromium E2E**; check 0 errors/16 inherited warnings; lint, schema, generated output, build,
  axe, responsive, navigation, reconnect, capability, retry-lineage, visual, and diff gates green.
- **Current phase:** B3. **Next:** canonical typed job detail with lazy bounded diagnostics,
  attempt selection, evidence retention/redaction states, virtualization, and presenter goldens.
- **Deviations/operator work:** unchanged; none.

---

## JMC6B Phase B3 — canonical job detail and bounded diagnostics

### Completed work

- The job route now loads only `JobPresentation` through the generated-path Activity client.
  Legacy `getJobDetail`, per-job `trackJob`, eager child/attempt/event fan-out, handler payload
  interpretation, and local request/result semantics are removed from the page.
- Overview renders every backend presentation-section discriminator with generated canonical
  types, retained subject snapshots, deleted-subject notice, status/attention, warnings,
  failures, steps, facts, metrics, before/after, change, track, artifact, child, and notice data.
- Timeline, Logs, Artifacts, Raw Data, and Execution mount only while their URL-backed tab is
  visible. Each resource is cursor/limit bounded, propagates an AbortSignal through the shared
  fetch runtime, and tears down on tab change. Timeline/log/artifact/attempt rows use browser
  content virtualization; logs select an attempt, expose download, freshness, seal, compression,
  expiry, and truncation; artifacts expose server-returned availability/download; raw documents
  are safely rendered as explicitly diagnostic redacted data with canonical downloads.
- A synthetic detail backend/browser fixture covers lazy request budgets, missing live subjects,
  redaction, truncation, retention, attempt selection, downloads, responsive navigation, and axe.
  Static proof rejects legacy detail imports/trackers and requires abortable virtualized panels.
  The Activity visual fixture now owns a deterministic connected EventSource, and the active-view
  subtitle contrast is WCAG-correct.

### Verification and status

- Retained full backend comparison: **1263 passed, the same 17 retained failures, 2 warnings in
  94.28s**. Presenter-family/read-detail contracts are **48 passed**; the complete static B3 proof
  is included in the retained run; Ruff is clean.
- Frontend: **106 unit tests passed**, including external abort propagation; `svelte-check` is 0
  errors with the inherited 16 warnings in 8 files; Prettier/ESLint, schema, production build,
  and diff check are green. **10 Chromium tests passed**, including all prior Activity coverage
  plus lazy detail diagnostics, mobile layout, deterministic visual state, and zero axe violations.
- **Current phase:** B4 — lazy typed Operations. **Exact next steps:** add one generated, bounded
  `/api/system/operations` snapshot (with separately bounded history where required); replace the
  four-response untyped fan-out; dynamically load Operations only when selected; abort hidden
  work; certify node/worker/transport/database/event/storage data and query/poll/event/connection
  budgets without returning infrastructure to Queue or History.
- **Deviations:** no product/design deviation. The pre-existing Activity subtitle contrast missed
  axe by 0.06 and was corrected without suppression; its inspected visual golden was refreshed.
- **Pending operator actions:** none. Only synthetic owned fixtures and the disposable test
  database/browser servers were used. The B3 phase hash follows after the configured-author commit.

### B3 post-commit checkpoint

- **Phase commit:** `b7a2145` (`jmc6b b3: rebuild canonical job detail`), sole parent `fb4326f`,
  configured repository author, linear and unpushed.
- **Verification:** retained backend **1263 passed / same 17 retained failures / 2 warnings**;
  presenter-family/read-detail **48 passed**; Ruff clean; frontend **106 unit / 10 Chromium E2E**;
  check 0 errors/16 inherited warnings; lint, schema, build, visual, lazy-budget, abort, download,
  redaction, truncation, responsive, axe, and diff gates green.
- **Current phase:** B4. **Next:** one generated bounded Operations snapshot, lazy hidden-panel
  teardown, separately bounded history, and infrastructure budget certification.
- **Deviations/operator work:** unchanged; none.

## JMC6B Phase B4 — Lazy typed Operations

### Completed work

- Added one versioned generated `GET /api/system/operations` presentation contract covering
  node, workers, transport, database connection budgets, the durable event listener, storage,
  and schema contracts. The existing metrics-history resource is now a separately typed,
  explicitly windowed/downsampled contract with a 200-job overlay ceiling and truncation signal.
- Replaced the Operations placeholder with a secondary dynamic import. The view performs one
  snapshot request only after selection, loads history only after an explicit bounded action,
  propagates `AbortSignal` through the shared Activity client, aborts hidden/unmounted work, uses
  content virtualization for history rows, and never leaks infrastructure data into Queue or
  History.
- Regenerated the canonical OpenAPI schema/client and expanded the frozen inventory, static
  contract proof, typed backend tests, Activity-client tests, and owned synthetic Playwright
  fixture. The mobile browser gate proves request budgets, URL bounds, hidden-view teardown, and
  zero axe violations.

### Verification and status

- Retained full backend comparison on `127.0.0.1:55450/marquee_test`: **1266 passed, the same 17
  retained failures, 2 warnings in 96.39s**; failure membership exactly matches the frozen JMC6A
  inventory. Focused B4 contracts are **13 passed** and Ruff is clean.
- Frontend: **108 unit tests passed**; `svelte-check` is 0 errors with the inherited 16 warnings in
  8 feature-page files; Prettier/ESLint, deterministic 202-path schema, generated types,
  production build, and diff check are green. **11 Chromium tests passed**, including responsive,
  navigation, lazy/abort budgets, axe, and the retained visual fixture.
- **Current phase:** B5 — poster and HDR initiation surfaces. **Exact next steps:** inventory every
  poster-pipeline and HDR initiation/result surface; replace legacy trackers, local job authority,
  per-page stage maps, per-job streams, and polling with the one JMC6A store/card and canonical
  generated types; preserve feature-specific results outside Activity and certify navigation,
  reconnect, responsive, and accessibility behavior.
- **Deviations:** no product/design deviation. One initial full run intentionally reproduced the
  timeline's documented wrong-target `:5432` signature; the authoritative owned-target rerun is
  the comparison recorded above. The 16 inherited warnings remain assigned to their B6 consumer
  migration and must reach zero before B7.
- **Pending operator actions:** none. Only the owned disposable PostgreSQL target and synthetic
  browser fixture were used.

### B4 post-commit checkpoint

- **Phase commit:** `d25b2b8` (`jmc6b b4: add typed lazy operations`), sole parent `b7a2145`,
  configured repository author, linear and unpushed.
- **Verification:** retained backend **1266 passed / same 17 retained failures / 2 warnings**;
  focused contracts **13 passed**; Ruff clean; frontend **108 unit / 11 Chromium E2E**; check 0
  errors/16 inherited warnings; lint, schema, generated contract, production build, lazy/abort,
  bounded history, mobile, navigation, axe, and diff gates green.
- **Current phase:** B5. **Next:** migrate every poster and HDR initiation surface to the one
  shared Activity store/card and generated canonical types, then delete their legacy tracking,
  stage maps, polling, streams, and local authority after the last consumer.
- **Deviations/operator work:** unchanged; none.

## JMC6B Phase B5 — Poster, taste, onboarding, and HDR initiating pages

### Completed work

- Added one reusable `FeatureActivityPanel` over the JMC6A contextual store and card. Feature
  scopes perform bounded Queue discovery, bind every returned canonical job ID immediately, keep
  one multiplexed stream, render server capabilities through `JobProgressCard`, and report
  authoritative terminal snapshots back to feature-specific refresh logic. No storage-backed job
  ID, page tracker, per-job EventSource, polling loop, stage map, or duplicate action policy was
  introduced.
- Migrated the poster overview, movie and TV batch/single pages, run result view, taste training,
  and onboarding completion. Returned single/batch/training IDs appear in the same shared card;
  reload discovery is server-owned, while domain review, navigation, filters, and result state
  remain feature-owned. The legacy loader-side job-list reattachment adapters were removed.
- Migrated HDR overview, movie list, movie detail conversion/publication, and TV show/season
  analysis. Local-storage IDs, progress streams, arbitrary stage text, polling, and custom progress
  bars were removed. The movie detail API now validates the built-in `DoviConvertResultV1` on the
  backend and returns a bounded domain `conversion_candidate`; the feature page never reads raw
  handler result/error JSON.
- Added a generated `PlannedJobSubmissionResponse` for confirmable HDR mutations and converted
  HDR/onboarding initiators to generated canonical submission types. Active HDR job aggregates
  were removed from feature read models because the shared Activity scope is now authoritative.
  Static B5 proofs require the shared panel across all migrated consumers and reject their legacy
  trackers, streams, polling, and job-document decoding.

### Verification and status

- Retained full backend comparison on the owned PostgreSQL target: **1268 passed, the exact same
  17 retained failures, 2 warnings in 93.49s**. The two additional passes are B5 static contract
  proofs. Focused B5 contract/conversion gates are **13 passed**; Ruff is clean; no new failure,
  skip, xfail, warning, quarantine, or ignored test was added.
- Frontend: **108 unit tests passed**; `svelte-check` is 0 errors with the same 16 warnings confined
  to the not-yet-migrated B6 audio/subtitle and letterbox consumers. Prettier/ESLint, the
  deterministic 202-path schema, generated types, production build, and diff check are green.
  **11 Chromium Playwright/axe tests passed**, including responsive cards, URL navigation,
  reconnect/capability behavior, lazy diagnostics/Operations budgets, and accessibility.
- **Current phase:** B6 — audio/subtitle, letterbox, and supporting initiation surfaces. **Exact
  next steps:** inventory and migrate every remaining media mutation, scan, batch, generation,
  restore, reset, library, backup, and maintenance initiator; preserve typed mutation planning and
  feature result state; then delete the final legacy job/media-job trackers, components, adapters,
  local authority, and broad suppressions with static absence proof.
- **Deviations:** no product/design deviation. The generic result-view `localStorage` preference
  remains UI-only (flat versus sectioned display), never stores job identity or lifecycle state.
  A sandboxed Playwright bind was rerun with the approved localhost fixture permission. The
  retained full comparison membership and count are unchanged.
- **Pending operator actions:** none. Only synthetic fixtures and owned disposable infrastructure
  were used. The B5 phase hash is appended immediately after its configured-author commit.

### B5 post-commit checkpoint

- **Phase commit:** `31453fa` (`jmc6b b5: migrate poster and hdr activity`), sole parent
  `d25b2b8`, configured repository author, linear and unpushed.
- **Verification:** retained backend **1268 passed / same 17 retained failures / 2 warnings**;
  focused B5 contracts **13 passed**; Ruff clean; frontend **108 unit / 11 Chromium E2E**; check
  0 errors/16 B6-only warnings; lint, schema, generated contract, build, responsive, navigation,
  reconnect, capability, axe, and diff gates green.
- **Current phase:** B6. **Next:** migrate audio/subtitle, letterbox, and all supporting initiation
  surfaces, then remove the final legacy trackers, adapters, components, and job authority with
  static absence proof.
- **Deviations/operator work:** unchanged; none.

## JMC6B Phase B6 — Remaining initiation surfaces and legacy tracker removal

### Completed work

- Migrated every audio/subtitle overview, movie inventory/detail, TV detail, generation, deep-scan,
  policy audit/apply, and settings-reset initiator to `FeatureActivityPanel`. Confirmable subtitle
  and re-encode plans now use the canonical generated mutation-confirmation contract, and returned
  job IDs bind immediately to the single JMC6A store/card.
- Migrated letterbox overview, movie board/detail, TV library/show/season/episode, detection,
  apply/revert, re-encode, publication/restore/discard, and batch surfaces. Removed custom progress
  bars, stage decoding, per-job streams, polling reconciliation, direct cancel paths, and local
  batch/job storage. Feature payloads no longer aggregate active job IDs or detection snapshots;
  the canonical Activity query is the only lifecycle authority.
- Retained domain forms, plans, candidates, artifacts, and post-terminal refresh behavior while
  removing raw handler-result/error inspection. Policy audit was corrected to the canonical
  asynchronous contract and policy apply now exposes its generated canonical submission type.
- Deleted the last legacy job/media-job clients and tracker/component stack after source searches
  proved no consumers. Removed all broad file-level lint suppressions and added static B6 proofs
  covering every remaining initiation surface, deleted files, legacy imports, storage authority,
  streams, tracker calls, and feature job aggregates.

### Verification and status

- Retained full backend comparison on the owned PostgreSQL target: **1270 passed, the exact same
  17 retained failures, 2 warnings in 95.82s**. The two additional passes are B6 static contract
  proofs; failure membership is unchanged. Focused B6 contract/letterbox/policy gates are **32
  passed** and Ruff is clean.
- Frontend: **108 unit tests passed**; `svelte-check` is **0 errors / 0 warnings**; Prettier/ESLint,
  deterministic 202-path schema, generated client, production build, and diff check are green.
  **11 Chromium Playwright/axe tests passed**, covering keyboard, responsive/mobile, navigation,
  server capabilities, reconnect fixtures, lazy diagnostics/Operations, and accessibility.
- **Current phase:** B7 — final accessibility, browser, contract, and compaction certification.
  **Exact next steps:** run the complete final gate matrix and static inventory; finalize the shared
  timeline; verify the JMC6B range is linear, JMC6B-only, unpushed, and exactly based on
  `jmc6a-complete`; create timestamped recovery branch/tag and verified external bundle; record
  the certified tree; squash to the mandated subject, prove tree identity, and annotate
  `jmc6b-complete`.
- **Deviations:** no product/design deviation. A sandboxed focused pytest attempt could not open a
  socket and was immediately rerun against the approved owned database; Playwright likewise used
  the approved localhost fixture permission. The build retains its pre-existing large-chunk
  advisory; it is not a Svelte diagnostic and no lint/test suppression was added.
- **Pending operator actions:** none. Only synthetic fixtures and owned disposable infrastructure
  were used. The B6 phase hash is appended immediately after its configured-author commit.

### B6 post-commit checkpoint

- **Phase commit:** `0dcc7f1` (`jmc6b b6: migrate remaining activity consumers`), sole parent
  `31453fa`, configured repository author, linear and unpushed.
- **Verification:** retained backend **1270 passed / same 17 retained failures / 2 warnings**;
  focused B6 contracts **32 passed**; Ruff clean; frontend **108 unit / 11 Chromium E2E**; check
  **0 errors / 0 warnings**; lint, deterministic 202-path schema, generated contract, production
  build, keyboard, responsive/mobile, navigation, reconnect/capability, axe, and diff gates green.
- **Current phase:** B7. **Next:** repeat the complete final certification matrix, finalize this
  timeline, create and verify recovery material, then perform the final-only squash and annotated
  completion tag without pushing or starting JMC6C.
- **Deviations/operator work:** unchanged; none.

## JMC6B Phase B7 — Final UI and contract certification

### Completed work and verification

- Re-ran the complete retained backend comparison against the owned PostgreSQL target: **1270
  passed / the exact same 17 retained failures / 2 warnings in 95.82s**. Focused B6/B7 contract,
  letterbox, and policy coverage is **32 passed**; Ruff is clean; schema export is deterministic at
  202 paths and the generated TypeScript client is current.
- Re-ran frontend certification: **108 unit tests**, zero-error/zero-warning `svelte-check`, clean
  Prettier/ESLint, production build, and **11 Chromium Playwright/axe tests** covering keyboard,
  responsive/mobile, navigation and URL restoration, server capabilities/retry lineage, reconnect
  fixtures, lazy diagnostics, bounded Operations teardown, and accessibility.
- Re-ran static absence and diff gates. No legacy tracker/client/component import, feature-local
  job storage authority, per-job stream/poll adapter, feature active-job aggregate, broad
  file-level lint suppression, skip, xfail, quarantine, or ignored test remains or was added.

### Finalization status

- **Current phase:** B7 final-only history compaction. **Exact next steps:** verify the range and
  remote state; curate ByteRover project context; create timestamped recovery branch/tag and an
  external verified bundle; record the certified pre-squash tree; squash exactly to `jmc6b:
  rebuild projection room activity`; prove tree identity; create annotated `jmc6b-complete`; stop
  without pushing, deleting recovery material, or beginning JMC6C.
- **Deviations:** no product/design deviation. Localhost/database sandbox approvals and the
  pre-existing Vite chunk advisory are recorded in B6; no operator work is required.

### B7 post-commit and recovery checkpoint

- **Phase commit:** `ec8d940` (`jmc6b b7: certify activity integration`), sole parent `0dcc7f1`,
  configured repository author. The eight-commit JMC6B range is linear, JMC6B-only, exactly based
  on `jmc6a-complete` (`a0355e0`), and the read-only remote query proves `origin/job-manager`
  remains at that base; the JMC6B range is unpushed.
- **Certified pre-squash tree:** `82552aedda89bd85a6cc8af22fe5971db4814731` at `ec8d940`.
- **Recovery branch/tag:** `recovery/jmc6b-20260716T214327Z` and annotated
  `recovery-jmc6b-20260716T214327Z`, both preserving the certified pre-squash checkpoint.
- **External bundle:** `/tmp/marquee-jmc6b-recovery-20260716T214327Z.bundle`; `git bundle verify`
  reports three refs, complete history, and SHA-1 integrity.
- **Final step:** commit this recovery record, squash the entire range to the mandated subject,
  prove exact final tree identity, annotate `jmc6b-complete`, and stop. No push, force-push,
  recovery deletion, or JMC6C work is authorized.
