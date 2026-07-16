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
