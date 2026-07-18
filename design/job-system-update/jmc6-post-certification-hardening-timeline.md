# JMC6 Post-Certification Hardening Timeline

Shared execution and cold-handoff record for JMC6D, JMC6E, and JMC6F. JMC6D is the current and
only active plan. No push, production activation, or JMC6E work is authorized.

## JMC6D Phase D0 — verification and failure-contract freeze

### Exact starting state

- Plan base is annotated `jmc6c-complete`, resolving to compact commit
  `5b831b1ca0a5cd27670284dcc3be82b7028fa5a6`, tree
  `177b18f3ef417fc36a686c96cd31a8e6ff9dc476`, with sole parent
  `a31cc75c3b648e8b948c49f20c05b4d4ffe8401d` (`jmc6b-complete`). `HEAD` and
  `job-manager` were exactly at that compact commit when D0 began.
- `origin/job-manager` is at `jmc6a-complete`; the local branch is ahead by exactly the compact
  JMC6B and JMC6C commits. No remote ref contains `jmc6c-complete`.
- Repository author is configured as Gautam Chaudhri <gautam.chaudhri@gmail.com>. All JMC6D
  commits must use only that configured identity.
- JMC6C recovery branch `recovery/jmc6c-20260717T002959Z`, annotated tag
  `recovery/jmc6c-pre-squash-20260717T002959Z`, and external bundle
  `/tmp/marquee-jmc6c-recovery-20260717T002959Z.bundle` exist. The branch/tag resolve to the
  certified tree, and `git bundle verify` reports complete SHA-1 history with the recorded
  pre-squash tip `a2beec2b0c9968ca589e02b8ad561c13d589f773`.
- The starting worktree was not clean. It contained only owner-authored post-certification plan
  input: modified `jmc6c-activation-audit.md`, `job-system-pgqueuer-migration.md`, and
  `design/timeline.md`; and untracked JMC6D, JMC6E, and JMC6F plan files. These files predate
  implementation, are preserved as user-owned input, and must not be mistaken for agent-created
  implementation changes.
- Required instructions and plans were read in full: `AGENTS.md`, `CLAUDE.md`, plan workflow,
  plan 04 section 0, JMC6D, JMC6C audit/certification, and both PgQueuer architecture/migration
  records. Serena and RTK are available. ByteRover was unavailable in-session and the owner
  explicitly authorized continuing without it; no further ByteRover installation or use is
  permitted for this task.

### Verified predecessor evidence

- Annotated `jmc6c-complete` metadata, compact commit/tree, sole-parent ancestry, configured
  author, local/unpushed state, recovery refs, and external bundle match the frozen JMC6C record.
- The frozen predecessor baseline is `1283 passed, 0 failed, 0 skipped, 0 xfail/xpass, 2 warnings
  in 93.21s` on owned disposable PostgreSQL 18.3 at `127.0.0.1:55450/marquee_test`. The two
  warnings are the recorded third-party UMAP `n_jobs` warnings.
- Frozen contracts pending fresh D0 reproduction: Marquee head `0006_jmc4c` fingerprint
  `9158c083cfe84e8975473bd681a67036bb5d5485ad41a42b108c0a89ebac9701`; PgQueuer 1.1.1
  durable fingerprint `19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a`;
  OpenAPI 202 paths and SHA-256 `d515a891f50d0afca25423b805d869ede3df02782f47397b4588582d9b6650fe`;
  generated TypeScript SHA-256 `0fa8e5b913236756e55246bf12b82b805a13e8744e16fbb9eb08260644f260d0`;
  61 definitions / 42 enabled leaves / 42 handlers / 19 ticketless or reserved definitions.

### Baseline gates and next steps

- **Current phase:** D0 — verify JMC6C and freeze failure contracts.
- **In progress:** verify the owner audit findings against current source and installed PgQueuer
  1.1.1 contracts; freeze worker/scheduler/runtime/recovery/Operations/schedule/webhook/readiness/
  Docker contracts; establish an owned disposable PostgreSQL 18 target; run the complete retained
  backend baseline before implementation and record exact counts, warnings, environment, schema,
  OpenAPI/client, definition/handler, frontend, Ruff, and diff gates.
- **Exact next steps:** inspect the installed PgQueuer delivery/heartbeat/retry/hold/cancellation/
  callback-exception source; use Serena to map current startup, delivery admission, attempt,
  reconciliation, schedule, webhook, readiness, and Operations symbols/references; provision and
  verify the disposable PostgreSQL target; execute the full retained baseline; add failing D0
  regressions for changed container identity, running-attempt redelivery, contradictory schedule
  state, unmounted webhooks, and external Operations health; run all D0 gates; commit D0 with the
  configured author; append the phase hash/results here; continue immediately to D1.
- **Deviations:** ByteRover is skipped only because the owner explicitly authorized continuation
  without it after the CLI/daemon proved unavailable. No architectural or verification gate is
  waived.
- **Pending operator actions:** do not push or activate; retain all JMC6C recovery material;
  owner-authorized push and GitHub-hosted CI remain pending after the complete D/E/F hardening
  sequence; optional witnessed Dolby Vision Profile 5/7 certification remains pending before that
  capability can be enabled.

### D0 verification and red-contract results

- The installed dependency is exactly PgQueuer 1.1.1. Source inspection confirmed that stale
  `picked` tickets are reclaimed solely by PgQueuer heartbeat timeout; `RetryRequested` is the
  public database-retry signal; `on_failure="hold"` requires an exception; and a callback that
  returns normally is logged `successful`. Therefore the current running-attempt no-op path can
  silently acknowledge its ticket and is release-blocking exactly as audited.
- Serena source/reference analysis reproduced all owner findings: mutable logical worker rows with
  no incarnation heartbeat; attempt ownership records only the node label; startup orphan scanning
  filters by the new process's node label; running-attempt delivery returns without execution or
  transport retry; scheduler has no durable runtime evidence; production predicates ignore the
  reported global-disabled constant; Operations depends on the embedded supervisor; webhook routes
  are mounted and Subgen is globally auth-exempt.
- Owned disposable database target is PostgreSQL 18.3 at
  `127.0.0.1:55450/marquee_test`, user `marquee`. The sandbox cannot see host loopback, so all
  database gates use the explicit target outside the network sandbox. Port 5432 was not targeted.
- Authoritative clean pre-implementation backend baseline:
  **1283 passed, 0 failed, 0 skipped, 0 xfail/xpass, 2 warnings in 95.80s**. The warnings are the
  same two third-party UMAP `n_jobs` warnings.
- Frozen baseline gates passed: Ruff over `marquee tests scripts`; Alembic reports no new upgrade
  operations; OpenAPI is current at 202 paths; API and generated-client hashes match the frozen
  JMC6C values; schema contract markers match `0006_jmc4c` and PgQueuer 1.1.1 durable fingerprints;
  definition manifest is 61/42/42/19; generated TypeScript has no drift; Svelte check is 0 errors/
  0 warnings; Prettier/ESLint, 108 unit tests, and production build pass; `git diff --check` passes.
  Playwright initially produced 10/11 with a visual mismatch only under reused-server mode; the
  focused test and complete suite pass under the certified `CI=1` fresh-server lifecycle, ending
  at **11 passed**. No snapshot was updated.
- Added six intentional red regressions covering the five required D0 failure contracts: active
  running-attempt redelivery must request transport retry; orphan selection must survive container/
  node-label change; the master gate must disable every production predicate; schedule reporting
  must expose truthful effective state and reasons; deferred webhook paths/auth exemption must be
  absent; and fresh external runtime evidence must drive Operations health.
- Focused red run: exactly **6 failed**. Complete red inventory:
  **1281 passed, 6 failed, 2 warnings in 92.47s**; failure membership is exactly those six tests.
  Ruff and `git diff --check` remain clean. The initial version of the duplicate-delivery red test
  allowed its first task to be cancelled when the expected assertion failed; the fixture was
  corrected to release/await it before asserting, without weakening the failure contract.
- **D0 status:** complete pending the configured-author phase commit. The red suite is intentional
  evidence required before behavior changes; it is not represented as a green phase gate.
- **Exact next steps:** commit D0 failure-contract freeze; append its hash; implement D1's forward
  runtime-instance model/migration, worker/scheduler incarnation lifecycle, heartbeat degradation,
  sanitized capability advertisement, and attempt incarnation link; run focused/schema/full/static/
  frontend/diff gates; commit D1 and continue immediately to D2.
- **Deviations:** no product/architecture deviation. The D0 phase commit intentionally contains red
  regressions because the authoritative plan requires failing tests before behavior changes. The
  exact clean baseline and exact red inventory are both preserved above.

### D0 phase commit

- **Completed work and commit:** `55b153e89b78076ae10bc017786e163fa83dfebd` (`freeze runtime
  recovery failures`) records the owner hardening plans, verified predecessor/base/recovery state,
  clean baseline, installed PgQueuer contract inspection, audit reproduction, and six intentional
  red regressions.
- **Verification:** configured author only; Ruff and `git diff --check` clean; clean pre-change
  baseline 1283 passed; intentional post-freeze inventory 1281 passed / exactly 6 audited failures;
  all schema/API/client/frontend baselines recorded above.
- **Current phase:** D1 — runtime-instance schema and lifecycle.
- **Exact next steps:** add forward `0007_jmc6d` data-preserving schema and ORM contract; implement
  generated immutable worker/scheduler incarnations, sanitized capability/entrypoint evidence,
  heartbeat/expiry/graceful-stop lifecycle and degraded telemetry reporting; link attempts to the
  incarnation; run focused migration/lifecycle/admission tests and every required gate.
- **Deviations and reasons:** none beyond the intentional red D0 checkpoint already recorded.
- **Pending operator actions:** unchanged; no push, activation, operator database/media access, or
  recovery-material deletion.

### D1 phase commit

- **Completed work and commit:** `c66f04cf0f10f8603366c670faddba125fdf3534` (`record runtime
  process incarnations`) adds forward revision `0007_jmc6d`, the durable worker/scheduler
  runtime-incarnation model and lifecycle, generated immutable IDs, bounded independent heartbeat
  writes, expiry and graceful-stop evidence, sanitized entrypoint/capability advertisement, and the
  exact runtime-incarnation link on admitted attempts. The worker now registers before startup
  reconciliation and advertises only configured locally supported execution classes; the scheduler
  records its own separate incarnation. The official migration head and retained schema-freeze
  contracts advance without rewriting the JMC1-JMC6 baseline.
- **Verification:** focused runtime lifecycle/admission tests pass (5/5); retained migration,
  contract-freeze, and backup certification tests pass (37/37); online upgrade reaches
  `0007_jmc6d`; offline SQL renders; `alembic check` reports no operations; Marquee's official
  migration service and PgQueuer verification pass and refresh the `0007_jmc6d` contract marker.
  The full phased inventory is **1286 passed, exactly 6 intentional D2-D4 failures, 2 unchanged
  UMAP warnings in 93.74s**. Ruff and `git diff --check` pass. OpenAPI remains deterministic at
  202 paths and generated TypeScript has no drift; Svelte check is 0 errors/0 warnings; Prettier/
  ESLint, all 108 frontend unit tests, and the production build pass. No frontend behavior changed,
  so the affected-frontend gate is satisfied without repeating Playwright in D1.
- **Current phase:** D2 — redelivery, takeover, and orphan reconciliation.
- **Exact next steps:** implement explicit live-attempt retry, globally bounded stale-instance
  selection, verified same-host death, policy-limited read-only/idempotent supersession, unsafe
  mutation quarantine/hold, and publication-aware recovery; certify graceful/hard death,
  container-label change, PostgreSQL loss/recovery, listener recovery, PID reuse, foreign
  containment, and stale-fence rejection; run all phase gates and commit D2.
- **Deviations and reasons:** no architecture deviation. One initial full-suite command omitted the
  explicit owned `DB_URL` and consequently reached the repository default database, producing
  unrelated missing-schema/role errors; it was discarded and rerun against the recorded owned
  PostgreSQL 18 target. The corrected result is the exact inventory above. D1 deliberately retains
  the six frozen failures assigned to later phases.
- **Pending operator actions:** unchanged; do not push or activate; retain all recovery material;
  no production migration, media access, or external coordination was performed.

### D2 phase commit

- **Completed work and commit:** `cdb3f85237ff400e64e7088eb6c8709ab5b124f0` (`harden stale
  attempt recovery`) makes duplicate delivery explicitly retry while a runtime incarnation is
  fresh; globally selects bounded unfinished attempts independent of container/node labels;
  verifies same-host boot/PID-start/process-group/cgroup identity before signalling; permits only
  policy-certified replay-safe remote supersession; quarantines unsafe or publication-uncertain
  mutation; and atomically closes a stale audit, advances its fence, creates the replacement
  attempt, and restores canonical execution in one row-locked transaction. Stale ownership cannot
  alter a newer fence, and an unsafe canonical result forces PgQueuer's hold path.
- **Verification:** focused delivery and real process-identity suites pass (49/49); retained
  definition/runtime manifests pass (15/15); retained schema/contract/backup set passes (23/23);
  `alembic check` reports no operations; OpenAPI remains current at 202 paths and the frozen API/
  client hashes remain unchanged. Full phased inventory is **1294 passed, exactly 4 intentional
  D3-D4 failures, 2 unchanged UMAP warnings in 94.72s**. Ruff, generated-client drift, Svelte
  zero-warning check, Prettier/ESLint, all 108 frontend unit tests, production build, and
  `git diff --check` pass. The process suite uses disposable subprocesses; container identity is a
  deterministic database simulation, not claimed as a Docker recreation smoke.
- **Current phase:** D3 — truthful schedule gating.
- **Exact next steps:** add the default-off restart-owned production master gate; make every
  production predicate and callback consume one effective-state calculator; expose registered,
  individually activated, configured, effective, and disabled-reason diagnostics; verify
  multi-scheduler uniqueness, gate-off/gate-on restart behavior, misfire/coalescing, and no backlog
  burst; run all required gates and commit D3.
- **Deviations and reasons:** no product/architecture deviation. One retained-schema command named a
  nonexistent historical backup test module and collected nothing; it was discarded and rerun with
  `tests/test_jmc3_certification.py`, producing the recorded 23/23 result. The local ByteRover CLI
  remains absent, while the required ByteRover MCP query is operational and was used.
- **Pending operator actions:** unchanged; do not push or activate schedules; retain recovery
  material; production restart/configuration and any real Docker recreation remain operator work.

### D3 phase commit

- **Completed work and commit:** `27555fe6611d36d9f24444325169eafe3d28af84` (`gate production
  schedules`) adds restart-owned `JOB_PRODUCTION_SCHEDULES_ENABLED`, default false; routes every
  production callback and readiness diagnostic through one effective-state calculation; preserves
  individual activation and configuration conditions; and reports registered, individually
  activated, configured, effective, and disabled-reason state per schedule. Fixed test scheduling
  remains outside the production master gate. Occurrence identity/coalescing prevents a gate toggle
  or scheduler restart from creating a catch-up burst.
- **Verification:** focused schedule/readiness/producer contracts pass (24/24), including gate-off,
  gate-on, disable/re-enable, two-caller reuse, restart reuse, misfire buckets, and no extra
  occurrence after a toggle. Full phased inventory is **1297 passed, exactly 2 intentional D4
  failures, 2 unchanged UMAP warnings in 94.60s**. Ruff, `alembic check`, deterministic OpenAPI at
  202 paths, generated-client drift, Svelte zero-warning check, Prettier/ESLint, all 108 frontend
  unit tests, production build, and `git diff --check` pass.
- **Current phase:** D4 — webhook removal and external Operations.
- **Exact next steps:** remove Radarr/Sonarr/Subgen webhook mounting, callback execution state, and
  Subgen auth exemption; make Operations use bounded fresh/stale/stopped runtime-instance and
  sanitized capability evidence with embedded-supervisor data supplemental only; expose scheduler
  and effective production schedule state; regenerate OpenAPI/types; add bounded-query/index and
  capability-mismatch tests; run all gates and commit D4.
- **Deviations and reasons:** no architecture deviation. The first full D3 run exposed two retained
  schedule tests whose old expected/default state bypassed the newly required gate; their product
  intent was preserved by making deliberate enabled fixtures explicit and updating the readiness
  golden to the truthful configured state, then the full suite was rerun.
- **Pending operator actions:** unchanged; the default remains off. Do not push, activate, or delete
  recovery material. A future operator enablement requires an explicit environment change and
  scheduler restart after the full hardening sequence.

### D4 phase commit

- **Completed work and commit:** `05636f24a668aa8630456850fd166e26d1ba428c` (`remove webhook
  surface and externalize operations`) deletes the mounted Radarr/Sonarr/Subgen webhook route module
  and its `webhook_state` singleton, removes the router mounting, the Subgen global-auth exemption,
  and the webhook entry in system status; makes Operations derive worker/scheduler health from
  bounded fresh/stale/stopped runtime instances with sanitized capability/entrypoint availability,
  active/stale/stopped counts, last heartbeat, scheduler presence, effective production-schedule
  master state, and enabled-definition capability mismatches, keeping the embedded supervisor as
  supplemental evidence only; and regenerates the OpenAPI (202→199 paths, no webhook paths) and
  generated TypeScript contracts. The two prior-plan guards that read the module by explicit path
  (jmc5a `INVENTORY_FILES`, jmc5c deferred-surfaces) and the jmc6a/jmc6b frozen path counts were
  updated for the intentional removal; the payload-free operations golden gained the `schedules`
  key; and bounded runtime-instance detail and capability-mismatch regressions were added.
- **Verification:** focused webhook-absence, operations-health, bounded-detail, capability-mismatch,
  and affected jmc5a/jmc5c/jmc6a/jmc6b contract tests pass (37/37). Full backend inventory is
  **1301 passed, 0 failed, 0 skipped, 0 xfail/xpass, 2 unchanged UMAP warnings in 94.58s**; the two
  previously-red D4 contracts are now green and the two new bounded/capability tests raise the
  total. Ruff over `marquee tests scripts` is clean, `export_openapi.py --check` is current at 199
  paths, and `git diff --check` passes. Frontend: generated-client shows no drift, `svelte-check`
  reports 0 errors/0 warnings, Prettier/ESLint pass, 108 unit tests pass, 11 Chromium Playwright/axe
  tests pass (including `operations.spec.ts`), and the production build passes. No Marquee model or
  migration changed, so the migration head stays `0007_jmc6d`, the retained migration/contract-
  freeze tests pass unchanged, and no schema rehearsal was required in D4.
- **Current phase:** D5 — integrated certification and compaction.
- **Exact next steps:** run §7 acceptance and the complete retained suites; run the migration
  upgrade/fresh-target and PgQueuer install/upgrade/verify rehearsals and record honest counts;
  confirm deterministic OpenAPI/TypeScript; record external worker/scheduler/Operations evidence
  honestly (unit/database simulation versus any real process kill); append the D4 and D5 timeline
  entries with phase hashes; then perform §8 final-only compaction (recovery branch/tag + verified
  external bundle, squash the JMC6D-only range to one configured-author commit `jmc6d: harden
  runtime recovery and activation safety`, prove pre/post tree identity, annotate `jmc6d-complete`).
- **Deviations and reasons:** no architecture deviation. The prior in-progress D4 edit had added the
  Operations `schedules` field without updating the exact-key-set golden and left the new
  `system_operations` imports mis-sorted; both were corrected. Deleting the deferred webhook module
  is the plan-mandated webhook removal (D18/§5 require no executable callback or state singleton to
  remain), not the broad legacy-module deletion deferred to JMC6F; the jmc4a/jmc4b legacy
  producer/bypass scans use dynamic `rglob` and assert an empty current graph, so they stayed green
  without fixture edits, and only the two guards reading the module by explicit path were updated.
  The embedded-Subgen completion helpers in the subtitle modules were intentionally left untouched
  (out of scope); their callback URL now targets a removed route, consistent with webhooks remaining
  deferred and uncertified.
- **Pending operator actions:** unchanged; do not push or activate; retain all recovery material;
  the production schedule master gate remains default-off; deferred public reset, browser
  authentication, Docker hardening, and webhook implementation remain operator and JMC6E-F scope.

### D5 phase certification and final pre-squash state

- **Integrated certification:** the complete retained backend suite is **1301 passed, 0 failed, 0
  skipped, 0 xfail/xpass, 2 unchanged UMAP warnings in 95.04s** on the owned disposable PostgreSQL
  18.3 target at `127.0.0.1:55450`. A fresh empty database migrated cleanly through Alembic
  `0001_jmc1`→`0007_jmc6d`; the migration service installed and verified PgQueuer 1.1.1 durable
  (catalog fingerprint `19377622f52c906a7a5cb6e68b4db6d3…`, identical to the frozen JMC6C value),
  created `runtime_instances` with its four indexes, added the `job_attempts` runtime-instance
  foreign key, and wrote the `marquee 0007_jmc6d` and `pgqueuer 1.1.1 durable` contract markers;
  `alembic check` reported no new operations (model/migration equivalence); and
  `alembic upgrade head --sql` rendered the full offline DDL (198 statements). The `0007_jmc6d`
  revision defines both upgrade and downgrade, retained as a documented non-destructive expectation
  rather than an operator downgrade. Ruff over `marquee tests scripts`, `export_openapi.py --check`
  (199 paths), and `git diff --check` pass. Frontend generated-client drift, `svelte-check` (0
  errors/0 warnings), Prettier/ESLint, 108 unit tests, 11 Chromium Playwright/axe tests (including
  `operations.spec.ts`), and the production build pass unchanged from D4.
- **§7 acceptance:** every acceptance item maps to green coverage — D2 duplicate-delivery,
  live-retry, global stale-instance selection, same-host process-identity, and stale-fence tests;
  D1 lifecycle plus D4 external-Operations health and capability-mismatch tests; D3 master-gate and
  effective-schedule tests; and the D4 webhook-absence contract. Runtime recovery/takeover is
  certified with disposable subprocesses and a deterministic database simulation of container-
  identity change; no real Docker recreation smoke is claimed.
- **Phase hashes (post-certification, pre-compaction):** D0
  `55b153e89b78076ae10bc017786e163fa83dfebd`, D1 `c66f04cf0f10f8603366c670faddba125fdf3534`, D2
  `cdb3f85237ff400e64e7088eb6c8709ab5b124f0`, D3 `27555fe6611d36d9f24444325169eafe3d28af84`, D4
  `05636f24a668aa8630456850fd166e26d1ba428c`, on base `jmc6c-complete`
  (`5b831b1ca0a5cd27670284dcc3be82b7028fa5a6`). The JMC6D-only range is linear, sole-parent,
  configured-author, and unpushed. This final pre-squash timeline state precedes §8 compaction; the
  exact pre-squash tip and certified tree are captured by the timestamped `recovery/jmc6d-*`
  branch/tag and the verified external Git bundle created immediately after this commit, and are
  recorded in the JMC6D handoff and the JMC6E cold-start record.
- **Compaction:** after this commit, create the recovery branch/tag plus the external bundle, then
  squash the JMC6D-only range to one configured-author commit `jmc6d: harden runtime recovery and
  activation safety` with a tree identical to the pre-squash tip and sole parent `jmc6c-complete`,
  and annotate `jmc6d-complete`. No push, force-push, recovery deletion, or agent/model attribution.
- **Pending operator actions:** do not push or activate; retain all JMC6C and JMC6D recovery
  material; the production schedule master gate remains default-off; owner-authorized push and
  GitHub-hosted CI, optional witnessed Dolby Vision Profile 5/7 certification, and JMC6E-F remain
  pending after the full hardening sequence.

## JMC6E Phase E0 — predecessor verification and seam manifest freeze

### Exact starting state

- JMC6E started at `2026-07-17T20:26:02Z` from annotated `jmc6d-complete`, resolving to compact
  commit `cbc976bae52ff62078d965cadf915fc5ec5d6a8f`, tree
  `9e8317b706481dc3a6b46c05e8837d3389d5616e`, with sole parent
  `jmc6c-complete` (`5b831b1ca0a5cd27670284dcc3be82b7028fa5a6`). `HEAD` and
  `job-manager` are exactly at that compact commit; the worktree is clean.
- `origin/job-manager` remains at `jmc6a-complete`; the local branch is ahead by exactly the compact
  JMC6B, JMC6C, and JMC6D commits. No push or activation was performed. Repository author remains
  Gautam Chaudhri <gautam.chaudhri@gmail.com> and is the only permitted JMC6E identity.
- Recovery branch `recovery/jmc6d-20260717T190525Z` and annotated tag
  `recovery/jmc6d-pre-squash-20260717T190525Z` both resolve to pre-squash tip
  `dabf415b582fb5faa1d0c680d5fcb0258c446a34` and the certified compact tree. External bundle
  `/tmp/marquee-jmc6d-recovery-20260717T190525Z.bundle` verifies as complete SHA-1 history with
  exactly those recovery refs. Timeline D0-D5 phase hashes and the certified tree match Git.
- Required instructions and plans were read in full, including JMC6E, JMC6D, the shared timeline,
  progress/Projection Room architecture, JMC6A/JMC6B, plan workflow, and the specified backend and
  frontend ground-rule sections. Serena, ByteRover MCP, and RTK are operational and in use.

### Verified predecessor and baseline evidence

- Owned disposable PostgreSQL 18.3 is at `127.0.0.1:55451/marquee_test`, user `marquee`, data root
  `/tmp/marquee-jmc6e-pg.AFr9Ug`. The official migration service installed and verified Marquee
  head `0007_jmc6d` and PgQueuer 1.1.1 durable. Contract fingerprints are Marquee
  `36754af6f0001c36d1d8c0102e6260855425405ac4bf2fa3fc5c219ead5be82b` and PgQueuer
  `19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a`.
- JMC6D runtime/recovery/activation focused suite is **78 passed in 11.98s**. The authoritative
  complete backend baseline is **1301 passed, 0 failed, 0 skipped, 0 xfail/xpass, 2 unchanged UMAP
  warnings in 96.32s**. Two discarded provisioning runs (1294/7 and 1300/1) proved the new empty
  database first lacked the public Alembic schema and then the PgQueuer catalog; after applying the
  official complete migration service, the focused failure and full suite passed without source
  changes.
- Ruff over `marquee tests scripts`, `alembic check`, current/head checks, deterministic OpenAPI,
  generated TypeScript drift, and `git diff --check` pass. OpenAPI is 3.1.0 with 199 paths and
  SHA-256 `f68d2e3b38e6c4124da05e965c2f6d3f38eb81ae874b42c527aa0c50e7b8ad1d`; generated TypeScript
  SHA-256 is `5e4b817e2e7050aa251fdae5c75e6222cf56851096ad94`.
- Frontend baseline: `svelte-check` 0 errors/0 warnings; Prettier/ESLint clean; 10 Vitest files and
  108 tests pass; production build passes; CI fresh-server Playwright/axe is 11/11. The build emits
  only the recorded large-chunk/plugin-timing notices. Generated-client regeneration is clean.
- Initial static seam audit reproduces every JMC6E target: single-file subtitle scan executes inline;
  taste GET accepts `recompute`; enrichment executes profile/map work via `asyncio.to_thread`;
  feedback apply/undo performs learned-head retraining inline; exemplar deletion rebuilds the map
  inline; legacy process-local taste rebuild state remains; and multiple subject-detail
  `FeatureActivityPanel` consumers query only feature-wide scope. There are 24 panel instances,
  including broad movie/series detail scopes, and the subtitle client still exposes handwritten
  `MediaJob`/`active_job` compatibility data.

### Current phase and exact next steps

- **Current phase:** E0 — verify JMC6D and freeze route/page manifest.
- **In progress:** freeze the complete initiating-action/definition/feature/subject/correlation/
  overlap/capability/terminal-refresh manifest and add intentional red contracts for inline-work
  absence, exact scope recovery beyond 20 unrelated jobs, overlap-policy coverage, refresh duplicate
  submission, and generated API drift.
- **Exact next steps:** inspect the canonical definition/submission/list/store contracts with Serena;
  add the E0 manifest fixture and failing backend/frontend/static tests; run the focused red inventory,
  complete backend comparison, Ruff, schema/generated/frontend gates, and diff check; commit E0 with
  the configured author; append its hash and continue immediately to E1.
- **Deviations:** none. The first two backend attempts were discarded environment-provisioning runs,
  not retained failures or code changes; the certified run uses the official migration service.
- **Pending operator actions:** do not push, activate, use operator media/database state, or delete
  JMC6C/JMC6D recovery material. Production schedules remain default-off. No Docker recreation or
  optional witnessed Dolby Vision certification is claimed.

### E0 phase commit

- **Completed work and commit:** `882fd5cd7670baacbdb5b4a55fb9ecee907716eb` (`freeze canonical
  seam failures`) records the verified compact JMC6D ancestry/tree/recovery material, complete
  backend/frontend/static/schema/generated baseline, initial canonical-seam audit, route/page
  manifest, and six intentional red contracts for the locked JMC6E gaps.
- **Verification:** focused E0 inventory is exactly 6 failed; complete inventory is **1301 passed,
  exactly 6 intentional E1-E4 failures, 2 unchanged UMAP warnings in 97.12s**. Ruff over
  `marquee tests scripts` and `git diff --check` pass. The clean predecessor frontend, schema,
  migration, OpenAPI, and generated-contract gates are recorded above and no corresponding source
  changed in E0.
- **Current phase:** E1 — overlap policy and exact list filters.
- **Exact next steps:** add typed definition-owned overlap policy, server-computed immutable scope,
  transactionally serialized equivalent/conflict resolution, typed submission dispositions and
  Activity links; add exact multi-type/subject-reference/root/correlation list filters with indexed
  query budgets; regenerate OpenAPI/types; certify concurrent submission and terminal scope release;
  run every phase gate and commit E1.
- **Deviations and reasons:** none. E0 intentionally commits red contracts as required by the plan;
  the failure membership is exact and the predecessor baseline remains separately certified.
- **Pending operator actions:** unchanged; no push, activation, operator media/database access, or
  recovery-material deletion.

### E1 phase completion

- **Phase commit:** `29f48c6a6a54e1ca752640e89b4dc03e3b8d32f2` (`close overlap and recovery
  contracts`).
- **Completed work:** added typed, definition-owned `coalesce_equivalent`, `reject_conflict`, and
  explicitly safe `allow` overlap policies; server-derived immutable overlap scope; transaction-
  serialized advisory locking and canonical active lookup; equivalent-job reuse with
  `idempotent=true`; typed unsafe conflict links; canonical Activity links; exact bounded multi-type
  and subject-reference filters alongside existing parent/root/correlation filters; and forward
  Alembic revision `0008_jmc6e` with the composite active-scope index. Submission plan evidence,
  batch/schedule paths, route responses, mocks, generated OpenAPI, and generated TypeScript were
  migrated to the expanded contract. Concurrent requests, conflicting unsafe requests, terminal
  scope release, and recovery beyond 20 unrelated jobs have dedicated PostgreSQL tests.
- **Verification before commit:** official migration service and Alembic head/check pass at
  `0008_jmc6e` with no model drift. Focused schema/definition/submission/list suite is **46 passed**;
  the complete non-future inventory is **1305 passed, 0 failed, 0 skipped/xfail/xpass, 2 unchanged
  UMAP warnings in 96.92s**. The literal complete suite is **1308 passed with exactly 3 intentional
  E2-E4 freeze failures and 2 unchanged warnings in 96.71s**; the E1 overlap, exact-filter, and
  response-contract freezes are green. Ruff over `marquee tests scripts`, deterministic OpenAPI
  check (199 paths), generated TypeScript drift, and `git diff --check` pass. OpenAPI SHA-256 is
  `990ad060e6a263a4ab54e21ddd23f5ea664e4b3778fca1c7ebec4061bb0dacee`; generated TypeScript
  SHA-256 is `e2b0c927803cffe292bf6ec7b1f208b48c0a491a5e4fcd7758484aee26ab43b1`.
- **Frontend verification:** generated-client regeneration is clean; Svelte check is 0 errors/0
  warnings; Prettier/ESLint pass; all 108 Vitest tests pass; production build passes with only the
  recorded chunk/plugin notices; Playwright/axe is 11/11.
- **Current phase:** E2 — canonical subtitle scan.
- **Exact next steps:** replace the inline single-file subtitle scan with canonical `subtitle_scan`
  submission using immutable file/subject/configuration evidence; migrate movie/episode/file clients
  to the standard response; refresh inventory only after terminal success/no-change; remove the
  handwritten `active_job`/`MediaJob` seam; certify duplicate-click reuse, cancellation, failure,
  no-change, and refresh recovery; run all gates and commit E2.
- **Deviations and reasons:** no product or architecture deviations. The initial combined Alembic/
  test command was denied local-loopback access by the sandbox and was rerun with approved access;
  it is environment evidence, not a retained test failure. The complete suite intentionally retains
  only the three future-phase E2-E4 red contracts established in E0.
- **Pending operator actions:** unchanged; do not push, activate, touch operator media/database
  state, or delete recovery material.

### JMC6E certified phase ledger and compaction handoff

- **Verified linear phase range after `jmc6d-complete`:** E0
  `882fd5cd7670baacbdb5b4a55fb9ecee907716eb`; E1
  `29f48c6a6a54e1ca752640e89b4dc03e3b8d32f2`; E2
  `a1990a5acfd5e33db2c5ede1c7ad5cb531c80497`; E3
  `8685cba7047f26eba418db452bc58d1e3f6d1d92`; E4
  `0f4e6223728656ab9e86ce8a2a2a8d7a1cb3caaf`; E5
  `ae63f7d23d9f4d4696cfa957172dc5df622c126e`.
- **Certified E5 product tree:** `eea3d12e41996344bc3b9dce7f2a0489eacb36aa`. The only subsequent
  pre-compaction content change is this timeline ledger, which corrects the previously mistyped E0
  hash and records the Git-derived handoff. The recovery refs, external bundle, and final compact
  commit must preserve the ledger-bearing pre-compaction tree exactly.
- **Ownership, ancestry, and publication:** the six phase commits are a single-parent linear range
  rooted directly at compact annotated `jmc6d-complete`; configured author and committer are Gautam
  Chaudhri `<gautam.chaudhri@gmail.com>`. `origin` is configured, but no remote-tracking ref contains
  the JMC6E tip; the range is local-only and no push was performed. The worktree was clean
  immediately after E5.
- **Current phase:** create the timestamped recovery branch and annotated recovery tag, create and
  verify an external bundle containing the complete pre-squash recovery ancestry, then perform the
  final-only exact-tree soft squash and create annotated `jmc6e-complete` without editing this
  timeline afterward.
- **Exact next steps:** commit this ledger; record its exact tree in the recovery ref/tag/bundle and
  terminal verification output; confirm no Git lock or competing worktree owns the branch; create
  recovery material; verify the bundle; re-check clean linear ancestry and configured ownership;
  squash; prove the compact commit has parent `jmc6d-complete` and the exact recovery tree; tag it;
  do not push and do not start JMC6F.
- **Deviations and reasons:** the E0 hash copied into the earlier timeline entry differed from Git by
  more than abbreviation and was corrected before recovery or rewrite. No commit, tree, test result,
  or implementation content was changed by that correction.
- **Pending operator actions:** preserve the recovery branch/tag and external bundle; no push,
  activation, operator-state mutation, or recovery deletion.

### E5 integrated certification

- **Phase commit:** pending this timeline-bearing certification commit; its exact hash will be
  appended before recovery refs or compaction.
- **Completed work:** certified the complete JMC6E acceptance inventory across overlap/coalescing,
  exact recovery, canonical single-file subtitle scans, read-only GET routes, canonical taste/map/
  enrichment/head jobs, exact feedback/undo/exemplar successor lineage, terminal-only refresh, and
  shared-store page authority. A separate empty `marquee_jmc6e_final` database was upgraded through
  every Alembic revision to `0008_jmc6e` by the official `marquee.db_migration` service, including
  PgQueuer 1.1.1 install/upgrade/durable/autovac/verify and contract-marker generation. Current,
  heads, and model drift all report `0008_jmc6e`; `alembic check` reports no new upgrade operations;
  a second complete migration-service run is idempotent.
- **Backend/static/schema/generated verification:** the literal full backend suite is **1314 passed,
  0 failed, 0 skipped/xfail/xpass in 87.92s** with the disposable database selected explicitly.
  Ruff lint over `marquee tests` passes. OpenAPI is current at 199 paths and SHA-256
  `b840054960e2770292a8438625f49883ae2fbb0ce2bf435f8681984dcf27c1b6`; regenerated TypeScript is
  byte-clean at SHA-256 `bd761262334fd7739a2f8966ea89a55428516587053b7c177a043186780517e6`.
  `git diff --check` passes.
- **Frontend/browser verification:** Svelte check is 0 errors/0 warnings; Prettier/ESLint pass; all
  111 Vitest tests pass; the production build passes with only the recorded chunk/plugin notices;
  generated-client regeneration produces no diff; a fresh complete Playwright/axe run is 11/11.
- **Current phase:** final-only recovery and compaction.
- **Exact next steps:** verify the worktree contains only this timeline update; commit E5 with the
  configured author; append that hash and the complete phase-hash ledger; prove the local range is
  linear, JMC6E-only, unpushed, and authored only by the configured identity; record the certified
  pre-squash tree and concurrency/ownership checks; create timestamped recovery branch and annotated
  recovery tag plus a verified external bundle; commit that final pre-compaction record; re-prove
  clean ownership/ancestry/tree identity; soft-squash exactly `jmc6d-complete..HEAD` to
  `jmc6e: close canonical job and refresh seams`; prove exact tree identity; create annotated
  `jmc6e-complete`; do not edit the timeline after compaction and do not push or begin JMC6F.
- **Deviations and reasons:** the first literal full backend invocation inherited an unrelated
  default database instead of the disposable JMC6E cluster: 1297 tests passed, six backup tests
  found no `schema_contracts` table, and eleven isolated-database setup cases found that other
  cluster's role lacked `CREATEDB`. The explicitly pinned disposable cluster reports `marquee` as
  superuser/`CREATEDB`; after its normal test database received the official migration service, the
  unchanged full suite passed 1314/1314. A repository-wide `ruff format --check` reports 215
  pre-existing files that the installed formatter would rewrite, so no broad mechanical formatting
  change was made; Ruff lint and the repository's required Prettier/ESLint gates pass. The first E5
  Playwright run passed 10/11 while the unchanged full-page Activity snapshot alternated between
  900px and 989px startup heights before stabilizing. Its fresh focused rerun passed, followed by a
  clean fresh 11/11 complete run; no snapshot or shell styling was changed.
- **Pending operator actions:** none beyond preserving the local unpushed result and the documented
  recovery material. No push, activation, operator media/database access, Docker change, public
  reset replacement, webhook/auth work, or recovery deletion is authorized.

### E4 phase completion

- **Phase commit:** `0f4e6223728656ab9e86ce8a2a2a8d7a1cb3caaf` (`derive exact feature
  activity state`).
- **Completed work:** every subject-detail `FeatureActivityPanel` consumer now sends the relevant
  canonical job types plus an exact immutable `subject_reference` set or run correlation identity;
  none relies on `subject_id` or feature-wide first-page recovery. The one shared Activity store now
  exposes server-derived active/conflicting state for an exact scope plus immediately returned job
  IDs. The shared panel binds that authority into each initiating page, and movie/series/file/HDR/
  letterbox/poster actions combine it with local flags that cover only the in-flight HTTP request.
  Overview panels retain the shared Activity handoff, while the store retains bounded cursor loading,
  last-good union semantics, one multiplexed SSE stream, snapshot repair, hidden-tab throttling, and
  storage-independent rediscovery. Added deterministic store fixtures for exact query encoding,
  active/terminal action authority, and two independent tabs recovering the same canonical job; the
  static freeze now requires exact scope, job types, and server-active binding for all nine detail
  consumers.
- **Verification before commit:** focused exact-list/overlap/detail-page suite is **25 passed** and
  the complete backend inventory is **1314 passed, 0 failed, 0 skipped/xfail/xpass in 90.47s**.
  Ruff over `marquee tests scripts`, deterministic OpenAPI check at 199 paths, and
  `git diff --check` pass; no schema or generated contract changed in E4. Frontend Svelte check is
  0 errors/0 warnings; Prettier/ESLint pass; all 111 Vitest tests pass; production build passes with
  only the recorded chunk/plugin notices; fresh-server Playwright/axe is 11/11.
- **Current phase:** E5 — integrated certification and final-only compaction.
- **Exact next steps:** run the complete JMC6E acceptance inventory,
  migration current/head/check and upgrade/fresh-target rehearsal, PgQueuer install/upgrade/verify,
  deterministic OpenAPI/TypeScript regeneration proof, full backend/frontend/static/schema/browser
  gates, linear local-only ancestry/author/tree checks, and final `git diff --check`; record the
  certified phase hashes and tree; create timestamped recovery branch/tag and verified external
  bundle; then, only if ownership/ancestry/concurrency/tree identity remain exact, squash the
  JMC6E-only range to `jmc6e: close canonical job and refresh seams`, prove tree identity, create
  annotated `jmc6e-complete`, and make no post-compaction timeline edit.
- **Deviations and reasons:** no product or architecture deviation. The first E4 Playwright run had
  one activity-card contrast failure while the other 10 tests passed; the unchanged fixture passed
  immediately in a fresh focused run and the subsequent complete fresh-server suite passed 11/11.
  No snapshot, palette, or accessibility expectation was changed. This is retained as a discarded
  environment/theme-resolution race, not represented as a clean result.
- **Pending operator actions:** unchanged; no push, activation, operator media/database access, or
  recovery deletion.

### E2 phase completion

- **Phase commit:** `a1990a5acfd5e33db2c5ede1c7ad5cb531c80497` (`canonicalize subtitle
  inventory scans`).
- **Completed work:** `POST /api/media-files/{id}/subtitles/scan` now resolves the live file only
  for bounded validation and submits the existing typed `subtitle_scan` definition with an immutable
  `media_file` subject; the route returns the standard canonical submission response and different
  request keys coalesce through E1 server policy. Persisted inventory GET/inspect reads no longer
  probe, scan, or publish and explicitly report missing/stale inventory. The movie client consumes
  the generated submission type, tracks the canonical ID immediately, recovers all relevant work by
  exact media-file subject and job types, and refreshes product data only for terminal succeeded or
  no-change outcomes. The permanently empty handwritten `active_job`, `MediaJob`, and unused
  `MediaJobSnapshot` compatibility seam is removed. Route inventory freezes now record
  `subtitle_scan` as a canonical route-constructed type.
- **Verification before commit:** focused canonical route/handler/read-purity suite is **9 passed**,
  covering equivalent duplicate requests, no-change, cancellation before I/O, probe-failure
  preservation of the last valid inventory, force behavior, and GET signature-drift reporting.
  Broader subtitle/mutation/E2 contract suite is **37 passed** apart from the two intentional E3/E4
  aggregate freezes. The full non-future suite is **1310 passed, 0 failed, 0 skipped/xfail/xpass, 2
  unchanged UMAP warnings in 96.30s**. A literal full run before the final route-inventory freeze
  update was 1313 passed with the two intentional E3/E4 failures plus that single corrected freeze;
  the corrected inventory/freeze set is subsequently 13/13 green. Ruff, Alembic head/check at
  `0008_jmc6e`, deterministic OpenAPI check (199 paths), generated TypeScript drift, and
  `git diff --check` pass. OpenAPI SHA-256 is
  `8c9828c5d80051af272e2a82d378c91d103804d87208d257bb924d25d545ddc8`; TypeScript SHA-256 is
  `b9b78f051a19a6393c90123cc1206f9977f220de1ca45c4fb0de182bccff8cb7`.
- **Frontend verification:** Svelte check is 0 errors/0 warnings; Prettier/ESLint pass after applying
  the repository formatter to the changed movie page; all 108 Vitest tests pass; production build
  passes with only recorded chunk/plugin notices; Playwright/axe is 11/11.
- **Current phase:** E3 — canonical taste, enrichment, and training successors.
- **Exact next steps:** make taste map GET pure; add a dedicated enabled typed `taste_enrich`
  definition/handler/presenter/progress path because enrichment remains exposed; route rebuild and
  enrichment only through canonical submissions; replace feedback/undo learned-head inline training
  and exemplar inline map rebuild with exact-revision successor jobs; remove process-local rebuild
  execution state; certify immutable publication/failure preservation/retry/progress/evidence/lineage;
  run all gates and commit E3.
- **Deviations and reasons:** no architecture deviation. The earlier combined frontend gate continued
  after Prettier reported one changed-file style issue, so the file was formatted and the lint gate
  rerun clean before certification. The two remaining intentional failures are exactly the E3 inline
  taste/training aggregate and E4 detail-panel scope freeze.
- **Pending operator actions:** unchanged; no push, activation, operator media/database access, or
  recovery deletion.

### E3 phase completion

- **Phase commit:** `8685cba7047f26eba418db452bc58d1e3f6d1d92` (`canonicalize taste
  model work`).
- **Completed work:** made every taste/profile/head GET path read-only; removed the map
  `recompute` contract and all route-owned multiprocessing, queue, monitor, cancellation, and
  process-local rebuild state; retained the exposed enrichment product as a dedicated enabled
  `taste_enrich` definition with strict request/result/progress, handler, presenter, labels, and
  immutable JMC4 publication semantics; and routed rebuild, map publication, enrichment, and manual
  learned-head training through standard canonical submissions. Feedback apply/undo now commits its
  bounded record independently and returns an optional `learned_head_train` successor linked to the
  exact feedback revision and mutation. Exemplar deletion durably updates the active profile and
  submits a typed `taste_map` successor with exact profile/trigger lineage and a deterministic safe
  idempotency key. The taste page records canonical IDs immediately and refreshes products only for
  terminal succeeded/no-change outcomes. Historical registry freezes and generated OpenAPI/client
  contracts were advanced for the new authoritative definition.
- **Verification before commit:** focused taste/feedback/definition/publication lineage suite is
  **99 passed with exactly the one intentional E4 detail-panel freeze remaining**; dedicated
  retained-contract correction suite is **21 passed**. The complete non-future backend inventory is
  **1313 passed, 0 failed, 0 skipped/xfail/xpass, 1 intentional E4 test deselected in 89.66s**.
  Ruff over `marquee tests scripts`, `alembic check` at `0008_jmc6e`, deterministic OpenAPI check
  (199 paths), the no-bypass static scan, and `git diff --check` pass. OpenAPI SHA-256 is
  `b840054960e2770292a8438625f49883ae2fbb0ce2bf435f8681984dcf27c1b6`; generated TypeScript
  SHA-256 is `bd761262334fd7739a2f8966ea89a55428516587053b7c177a043186780517e6`.
- **Frontend verification:** Svelte check is 0 errors/0 warnings; Prettier/ESLint pass; all 108
  Vitest tests pass; production build passes with only the recorded large-chunk/plugin notices;
  fresh-server Playwright/axe is 11/11.
- **Current phase:** E4 — exact feature-page recovery and action state.
- **Exact next steps:** update all eight remaining broad detail-panel
  consumers to exact immutable subject/correlation scope; expose exact derived active/conflicting
  state from the one shared Activity store; use local flags only for the initiating HTTP request;
  retain deterministic overview pagination or Activity handoff; certify recovery beyond 20 jobs,
  refresh/navigation with empty storage, snapshot repair, dropped SSE, hidden-tab behavior, rapid
  duplicate clicks, two tabs, and concurrent requests; run every phase gate and commit E4.
- **Deviations and reasons:** no product or architecture deviation. The first complete E3 run
  correctly exposed nine retained registry freezes that did not yet include the authoritative new
  definition and four obsolete tests whose sole purpose was exercising the forbidden process-local
  monitor. Those contracts were updated to the canonical registry and an explicit absence guard;
  the corrected focused and complete suites are green. The optional ByteRover CLI remains absent,
  while the required ByteRover MCP query is operational and was used.
- **Pending operator actions:** unchanged; do not push, activate, touch operator media/database
  state, or delete recovery material.

### Authoritative final JMC6E state

- **Timeline ordering:** the phase records above were appended around stable section anchors and are
  not physically chronological. Their authoritative order is E0, E1, E2, E3, E4, E5, then the
  Git-derived certified phase ledger; that ledger supersedes any earlier copied hash.
- **Current phase:** final-only recovery and exact-tree compaction; implementation and certification
  are complete.
- **Exact next steps:** commit the final pre-compaction record; create and verify timestamped
  recovery refs and an external bundle at that clean tip; squash only the linear JMC6E range; prove
  parent and tree identity; create annotated `jmc6e-complete`; make no later timeline edit.
- **Pending operator actions:** do not push, activate, delete recovery material, or start JMC6F.

## JMC6F Phase F0 — verify JMC6E and freeze retirement manifest

### Exact starting state

- JMC6F starts from annotated `jmc6e-complete`, resolving to compact commit
  `b46766bb4cca229db087e2dd35a389d952e6115e`, tree `5a13909b381c4f632bac29e7be0c773c938a64a3`,
  with sole parent `jmc6d-complete` (`cbc976bae52ff62078d965cadf915fc5ec5d6a8f`). `HEAD` and
  `job-manager` are exactly at that compact commit; the worktree was clean at start.
- `origin/job-manager` remains at `jmc6a-complete` (`a0355e07515c8cfee77ed7a2e31fc38d340eb445`); the
  local branch is ahead by exactly the compact JMC6B, JMC6C, JMC6D, and JMC6E commits. No remote ref
  contains `jmc6e-complete`. Repository author is Gautam Chaudhri
  `<gautam.chaudhri@gmail.com>` and is the only permitted JMC6F identity.
- Serena, ByteRover MCP, and RTK are operational and in use. Required instructions and plans were
  read in full: `AGENTS.md`, `CLAUDE.md`, plan workflow README, plan 04 §0 (backend ground rules),
  plan 05 §2 (frontend ground rules), JMC6F, JMC6E, JMC6D, this shared timeline, JMC5C, JMC6C
  zero-green certification, JMC6C activation audit, and the JMC6 former-test dispositions.

### Verified predecessor evidence (JMC6E complete, matches Git)

- Annotated `jmc6e-complete` is a tag object (tagger Gautam Chaudhri) → compact commit `b46766b`,
  tree `5a13909b`, parent `jmc6d-complete`. Configured author/committer only.
- Tree identity of the JMC6E squash holds: pre-squash tip `fb8b68197b667d4ca2af82e0319b68dc226be853`
  (`record jmc6e compaction handoff`) has tree `5a13909b`, byte-identical to the compact tree. The
  linear JMC6E-only range rooted at `jmc6d-complete` is E0 `882fd5c`, E1 `29f48c6`, E2 `a1990a5`,
  E3 `8685cba`, E4 `0f4e622`, E5 `ae63f7d`, ledger `fb8b681`.
- Recovery branch `recovery/jmc6e-20260717T215326Z` and annotated tag
  `recovery/jmc6e-pre-squash-20260717T215326Z` (→ tag object `941e850`) both resolve to the certified
  pre-squash tip. External bundle `/tmp/marquee-jmc6e-20260717T215326Z.bundle` verifies as complete
  SHA-1 history containing those recovery refs plus `jmc6d-complete`. Prior JMC6B/C/D bundles and the
  `~/backups/Marquee/` pre-squash bundles remain intact.
- Single Alembic head `0008_jmc6e`. The official `marquee.db_migration` service applied
  `0001_jmc1`→`0008_jmc6e` and installed/verified PgQueuer 1.1.1 durable on the owned disposable
  target.

### Baseline gates (complete green baseline)

- Owned disposable target is a fresh JMC6F PostgreSQL 18.3 cluster at `127.0.0.1:55452/marquee_test`,
  data root `/tmp/marquee-jmc6f-pg`, role `marquee` (superuser/CREATEDB). The sandbox cannot see host
  loopback, so all database gates run outside the network sandbox against this explicit target. Port
  5432 (the live operator database) was never targeted.
- Authoritative pre-change backend baseline after applying the official migration service:
  **1314 passed, 0 failed, 0 skipped, 0 xfail/xpass in 91.23s**. (A first run before the migration
  service showed the documented 7 provisioning failures — six `test_backup.py` and one
  `test_jmc1_readiness` case that read the base database rather than the ephemeral test schema; they
  pass once `marquee_test` receives the official migration service. That is environment provisioning,
  not a retained failure.)
- Ruff over `marquee tests scripts` passes. `export_openapi.py --check` is current at 199 paths;
  `design/api-schema.json` SHA-256 is `b840054960e2770292a8438625f49883ae2fbb0ce2bf435f8681984dcf27c1b6`
  (unchanged from JMC6E). `git diff --check` passes.
- Frontend: generated-client regeneration produces no diff; `svelte-check` 0 errors/0 warnings;
  Prettier/ESLint clean; 10 Vitest files / 111 tests pass. Production build and Playwright/axe were
  not re-run in F0 because no frontend source changed; they are certified in F4/F5.
- Definition manifest: 62 built-in definitions, 43 enabled types, 18 parent-only types; production
  schedule keys `audio_subs_deep_scan`, `library_sync`, `poster_heal`; webhook-reserved
  `radarr_upgrade`. (JMC6C's 61/42 plus JMC6E's added `taste_enrich`.)

### Retirement inventory (Serena reference/import analysis)

Confirmed against current source that the plan's retirement premises hold — the detached lifecycles
are already unreachable from production, so retirement removes dead runtime, not live capability:

- **RunManager (`marquee/pipeline/run_manager.py`, F03).** Lifecycle is dead in production: 0 prod
  refs to `run_manager.start`/`get_state`/`active_run_id`, `RunState`, `RunInProgressError`, and the
  `begin_rebuild`/`end_rebuild` flags (JMC6E E3 removed the inline taste-rebuild caller). Live
  helpers to **extract** into a neutral service: `load_archive` (8 prod call sites in
  `pipeline.py`/`taste.py`/`pipeline_tv.py`/`feedback.py`), `release_gpu_resources`, `gpu_busy`
  (`system.py`), `reset_extractor` (`feedback.py`), `_ensure_extractor`
  (`feedback.py`/`onboarding/build_taste_test.py`).
- **LetterboxManager (`marquee/media/letterbox_manager.py`, F02).** The detached batch lifecycle
  (`JobState`, `BatchInProgressError`, `start_batch`, `_execute_batch`, `active_job_id`, `get_state`,
  `_active_job_id`/`_jobs` state, `_emit_child_progress` bridge) has 0 prod and 0 test references.
  Canonical letterbox detection runs through `marquee.media.letterbox_detect` +
  `handlers_letterbox.py`, not this manager; the manager's `detect_*_and_store`/grouping methods have
  0 prod refs and are test-only (extract-vs-remove resolved in F1 against `letterbox_detect`
  coverage).
- **batch_runner (`marquee/pipeline/batch_runner.py`, F03).** Zero production importers (only a
  docstring mention in `runner.py:222` and 13 test refs). The canonical `execute_poster_pipeline`
  handler is a self-contained analysis stub that imports nothing from `marquee.pipeline`. Pure
  algorithms with valuable tests (`apply_official_pick`, ranking/OCR/detail helpers) are extracted to
  a neutral module in F1; the detached `run_batch`/`run_batch_assets` engine + `_BatchMovie`/
  `AssetSpec` wrappers are deleted.
- **taste rebuild (`marquee/api/routes/taste.py`, F04).** Already canonical after JMC6E E3:
  `_canonical_rebuild_status` queries the `Job` table; no multiprocessing/monitor/cancel-flag
  residue remains in the route. Remaining residue is the dead `run_manager` rebuild flags (removed
  with F1).
- **delivery executor (`marquee/core/jobs/delivery.py`, F05).** `LegacyNoopExecutor` injection is
  threaded (`executor` param) through `_execute_delivery`/`_deliver`, default `None`; no production
  caller supplies it. Deletion target.
- **frontend DTOs (F05).** The handwritten `MediaJob`/`active_job`/`MediaJobSnapshot` seam is already
  gone (JMC6E E2); the remaining `active_jobs` hits are an unrelated metrics count. `job-labels.ts`
  `displayMediaJobLabel` usage is re-checked in F2.
- **startup migrations (F10).** `main.py` lifespan calls `migrate_legacy_runtime_state` and
  `migrate_live_artifacts`; both are evaluated in F3 for obsolete-unreleased deletion vs bounded
  retention.
- **stale docs (F09).** Design/handoff docs referencing `/api/activity`, custom workers, inline media
  jobs, or `MediaJob` are updated/superseded in F2.

### F0 red contracts and gates

- Added `tests/test_jmc6f_retirement.py`: five intentional RED static-absence contracts (retired
  poster-run lifecycle, retired batch poster engine, retired letterbox batch lifecycle, no route
  importing a process-local pipeline lifecycle, retired delivery executor injection) plus two GREEN
  canonical-replacement guards (canonical letterbox detection on `letterbox_detect`; single canonical
  `execute_poster_pipeline`).
- Focused run: exactly 5 failed, 2 passed. Full backend inventory:
  **1316 passed, exactly 5 intentional F1-F3 absence failures in 88.13s** (1314 baseline + 2 green
  guards). Ruff over `marquee tests scripts` and `git diff --check` pass. The five failures are the
  static-absence contracts and are intentional evidence required before deletion, not a green gate.

### Current phase and next steps

- **Current phase:** F0 — verify JMC6E and freeze retirement manifest (complete pending the
  configured-author phase commit).
- **Exact next steps:** commit F0; append its hash; then F1 — create the neutral extractor/archive/GPU
  service and migrate its callers, extract the pure poster-ranking/official-pick and letterbox
  detection/grouping algorithms behind neutral modules, delete the RunManager/LetterboxManager batch
  lifecycles and `batch_runner`, rewrite/remove the lifecycle tests with named canonical replacements,
  and turn the F0 absence contracts green; run focused + full + Ruff + schema + generated + diff gates
  and commit F1.
- **Deviations:** none. F0 intentionally commits red static-absence contracts as the plan requires
  failing absence tests before deletion; the failure membership is exact and the predecessor baseline
  is separately certified at 1314.
- **Pending operator actions:** do not push, activate, touch operator media/database state, or delete
  any JMC6B/C/D/E recovery material. Production schedules remain default-off. Dolby Vision Profile 5/7
  remains readiness-disabled; browser auth, public reset replacement, Docker hardening, webhook
  implementation, and `radarr_upgrade` remain deferred owner work.

### F0 phase commit

- **Completed work and commit:** `862e4fe731c80ce56dc36afcdb1315b32af8dc25` (`freeze legacy
  retirement manifest`) records the JMC6F starting state, verified JMC6E predecessor evidence, the
  complete green baseline, the Serena-derived retirement inventory, and `tests/test_jmc6f_retirement.py`
  with five intentional RED static-absence contracts + two GREEN canonical-replacement guards.
- **Verification:** configured author only, no attribution trailer; focused 5 failed/2 passed; full
  inventory 1316 passed / exactly 5 intentional absence failures; Ruff and `git diff --check` clean.

### F1 phase completion — letterbox and poster lifecycle extraction/removal

- **Completed work:** extracted the still-live pipeline-runtime helpers out of the retired
  `RunManager` into a neutral `marquee/pipeline/extractor_runtime.py` (`extractor_runtime` singleton:
  `ensure_extractor`, `reset_extractor`, `release_gpu_resources`, `load_archive`) and migrated all
  eight production call sites (`pipeline.py`, `pipeline_tv.py`, `taste.py`, `feedback.py`,
  `system.py`, `handlers_poster_mutations.py`, `ocr_label_capture.py`, `onboarding/build_taste_test.py`).
  Deleted `marquee/pipeline/run_manager.py` (detached `start`/`_execute`/`_run_stages_blocking`/
  `_finalize_db`/`RunState`/`RunInProgressError`/active-run + `begin_rebuild`/`end_rebuild` GPU
  flags). The `/api/system/release-gpu` route drops the always-`None` in-process busy gate (canonical
  job resource reservations coordinate GPU work) and always releases.
- Extracted the pure TMDB official-pick poster-ranking algorithm (`apply_official_pick`, D8) into a
  neutral `marquee/pipeline/official_pick.py` and deleted the detached
  `marquee/pipeline/batch_runner.py` engine (`run_batch`/`run_batch_assets`/`_download_phase`/
  `_finalize`/`_persist_running`/`_BatchMovie`/`AssetSpec`) — it had zero production importers; the
  canonical `execute_poster_pipeline` handler is the sole poster-analysis path. Fixed the stale
  `batch_runner._ocr_batch` docstring reference in `runner.py`.
- Deleted the detached letterbox batch lifecycle from `marquee/media/letterbox_manager.py`
  (`JobState`, `BatchInProgressError`, `start_batch`, `_execute_batch`, `active_job_id`, `get_state`,
  `_active_job_id`/`_jobs` state, `_max_parallel`, `_SENTINEL`, unused `uuid4`/`contextlib`/`field`/
  `_get_session_factory` imports). Retained the pure detection/storage/grouping algorithms
  (`detect_*`, `group_episode_items_by_media_file`, `select_season_sample_episodes`,
  `EpisodeBatchItem`) and the no-op `_emit_child_progress` canonical seam. Canonical letterbox
  detection remains on `marquee.media.letterbox_detect` + `handlers_letterbox.py`.
- **Removed-test disposition (F06 — canonical replacement named):**
  - `tests/test_batch_runner.py` (removed): batch-engine isolation tests → canonical coverage in
    `test_jmc4a_batch_coordination.py` (batch parent/child), `test_jmc4c_poster_pipeline.py` /
    `test_jmc4c_poster_workspace.py` (canonical poster handler), and the stage-algorithm suites
    (OCR/scorer/gate/dedup) in `test_pipeline_revised.py`. The pure `apply_official_pick` algorithm
    and its seven tests are preserved in the new `tests/test_official_pick.py`.
  - `tests/test_poster_pipeline_backend.py`: removed `test_batch_ocr_fallback_is_isolated_per_movie`,
    `test_run_batch_finalizes_rows_when_exception_escapes`,
    `test_run_batch_assets_creates_tv_pipeline_runs_with_subject_fks` → canonical one-subject-per-job
    isolation + fenced failure handling (`test_jmc4c_poster_pipeline.py`, `test_jmc3a_*`) + subject
    snapshot tests. The OCR-token, results-grouping, cache-safety, and handler-registration tests are
    retained unchanged.
  - `tests/test_run_endpoints.py`: removed the three `RunState` buffer/replay/finish tests → canonical
    progress/SSE (`progress_service`, Projection Room, `test_pgqueuer_delivery.py`); removed
    `test_gpu_busy_reports_run_and_rebuild` and `test_release_gpu_endpoint_reports_busy` → canonical
    GPU coordination via job resource reservations. `test_release_gpu_resources_clears_cached_extractor`
    is migrated to `extractor_runtime` and a new `test_release_gpu_endpoint_releases_without_in_process_busy_gate`
    asserts the simplified route.
  - `tests/fixtures/jmc3b/b0_contract_freeze.json` + `test_jmc3b_contract_freeze.py`: the
    `run_manager.py` obsolete-helper disposition is marked executed (joins `progress_bridge.py` in the
    must-not-exist set); the `letterbox_manager.py` disposition notes the batch subscriber-queue
    removal with retained detection algorithms.
- **Verification before commit:** four of the five F0 absence contracts are now green (retired poster
  run lifecycle, retired batch engine, retired letterbox batch lifecycle, no route importing a
  process-local pipeline lifecycle). Full backend inventory: **1306 passed, exactly 1 intentional
  failure** (`test_legacy_delivery_executor_injection_is_retired`, assigned to F2) in 90.04s. Ruff
  over `marquee tests scripts` and `git diff --check` pass. OpenAPI is unchanged and current at 199
  paths, SHA-256 `b840054960e2770292a8438625f49883ae2fbb0ce2bf435f8681984dcf27c1b6`; migration head
  unchanged at `0008_jmc6e` (no model/migration change). No frontend source changed in F1, so the
  frontend baseline (svelte-check 0/0, 111 unit, generated-client no-drift) is unaffected; build/E2E
  are certified in F4/F5.
- **Current phase:** F2 — taste, delivery, frontend, and compatibility cleanup.
- **Exact next steps:** delete the legacy `LegacyNoopExecutor` delivery-executor injection from
  `marquee/core/jobs/delivery.py` and migrate any test suppliers (turns the last F0 absence contract
  green); remove residual process-local taste compatibility and any unused frontend `MediaJob`/label/
  SSE helpers; update/supersede stale `/api/activity`/custom-worker/inline-media-job design docs;
  regenerate OpenAPI/TypeScript if any contract shifts; run focused + full + Ruff + schema + frontend
  gates and commit F2.
- **Deviations:** none. The single remaining red is the intentional F2-assigned delivery-executor
  absence contract, consistent with the incremental red-to-green pattern used in D0-D4/E0-E4. No
  product/architecture deviation; the retired engines had zero production importers and the canonical
  poster/letterbox paths are unchanged.
- **Pending operator actions:** unchanged — no push, activation, operator media/database access, or
  recovery-material deletion.

### F2 phase completion — delivery, frontend, and compatibility cleanup

- **F1 phase commit:** `b4fb11b42013cefbb6f30a5bd822f0a73512901f` (`extract pipeline runtime and
  retire detached engines`).
- **Completed work:** removed the legacy `LegacyNoopExecutor` delivery-executor injection from
  `marquee/core/jobs/delivery.py` — `_execute_delivery(execution)` now always resolves the canonical
  `EXECUTION_HANDLERS` handler (no `executor`/`transport_context` params, no legacy result wrapper),
  and `deliver_job`/`deliver_control_job` drop the `executor` kwarg. The ten test suppliers of that
  seam (`test_pgqueuer_delivery.py` ×9 + `test_jmc6d_runtime_instances.py` ×1) are rewritten to swap
  the real handler via `monkeypatch.setitem(delivery._EXECUTION_HANDLERS, "system_noop", handler)` —
  a pure-test technique that exercises the identical production delivery path with no production
  test-seam. Every retry/hold/cancellation/fence/quarantine/duplicate-delivery assertion is preserved
  verbatim; the cancellation blocker uses `execution.cancellation` (the same object as the transport
  `context.cancellation`, delivery.py:805).
- Removed the dead handwritten `displayMediaJobLabel` export (last `MediaJob.operation` consumer) from
  `frontend/src/lib/job-labels.ts`; the `active_job`/`MediaJob`/`MediaJobSnapshot` DTO seam was already
  removed in JMC6E E2. Updated the active `design/poster-pipeline.md` architecture reference from the
  deleted `marquee/pipeline/run_manager.py` to `marquee/pipeline/extractor_runtime.py`. Historical
  timelines and executed plans (02/03/04) that mention the retired engines are left intact as
  point-in-time records (F09 permits historical documents). The only `/api/activity` reference in an
  active doc is the JMC6B decision to remove it, which is correct.
- Process-local taste rebuild machinery (F04) was already retired in JMC6E E3; the retained
  `test_taste_rebuild_has_no_process_local_execution_state` guard passes, and F1 removed the last dead
  `run_manager` rebuild flags.
- **Verification:** the safety-critical `test_pgqueuer_delivery.py` + `test_jmc6d_runtime_instances.py`
  focused suite is **49 passed** (matching the JMC6D D2 certification count). All five F0 static
  absence contracts are now green (7/7 in `test_jmc6f_retirement.py`). Full backend inventory:
  **1307 passed, 0 failed, 0 skipped/xfail/xpass in 88.63s**. Ruff over `marquee tests scripts`,
  `export_openapi.py --check` (199 paths, unchanged), generated-client no-drift, and
  `git diff --check` pass. Frontend: `svelte-check` 0 errors/0 warnings, Prettier/ESLint clean, 111
  Vitest tests, production build succeeds. Playwright/axe deferred to F4/F5 (only a dead unused label
  helper changed; no rendered behavior).
- **Current phase:** F3 — startup/schema/static retirement certification.
- **Exact next steps:** audit the `main.py` startup migration helpers (`migrate_legacy_runtime_state`,
  `migrate_live_artifacts`) for obsolete-unreleased deletion vs bounded/idempotent/tested retention
  (F10); prove fresh-schema and forward-upgrade equivalence from the `jmc6c-complete` schema, one
  executor per enabled definition, no legacy lifecycle imports/startup hooks; strengthen static
  absence coverage; run all gates and commit F3.
- **Deviations:** none. The delivery-test rewrite is a faithful canonical replacement (same
  `_execute_delivery` → handler path, same assertions); no safety contract was weakened.
- **Pending operator actions:** unchanged — no push, activation, operator media/database access, or
  recovery-material deletion.

### F3 phase completion — startup/schema/static retirement certification

- **F2 phase commit:** `7632c40a0350633180432cb0014ad9ef54fff930` (`retire delivery executor
  injection and stale compat`).
- **F10 startup-migration decision — RETAIN (bounded/idempotent/observable/tested):** both
  `main.py` startup helpers are kept because they satisfy every F10 retention criterion.
  `pipeline_config.migrate_legacy_runtime_state` moves pre-`data/` mutable state into the canonical
  `data/` layout over a fixed six-entry list, skips absent legacy paths, raises on a genuine
  path-conflict, and logs each move; `ml.migrate_artifacts.migrate_live_artifacts` upgrades live ML
  `.npz` artifacts to the safe codec format over a bounded target list. They are tested
  (`migrate_legacy_runtime_state` ×6 in `test_jmc1_readiness.py`; `migrate_live_artifacts`/
  `ensure_safe_artifact` ×9 across `test_artifact_codec.py`/`test_backup.py`). `migrate_live_artifacts`
  is a real first-release artifact-format-safety need; deleting either would strand a pre-existing
  operator layout with no benefit, so neither is an obsolete-unreleased deletion candidate.
- **Schema equivalence (JMC6F changed no model or migration):** `alembic check` reports
  `No new upgrade operations detected`; the sole head remains `0008_jmc6e`. A fresh database migrated
  `0001_jmc1`→`0008_jmc6e` and a forward-upgraded database (applied to the `jmc6c-complete` schema
  `0006_jmc4c`, then upgraded by the official migration service to `0008_jmc6e`) produce an identical
  schema: column fingerprint `5696b90d283f77e77a8239c59cdb13c4` and index fingerprint
  `5570599ca32f71ee49eabe65932b8845` match each other and the certification baseline (48 public
  tables). Temporary rehearsal databases were dropped.
- **One executor per enabled definition:** the registry reports 43 enabled leaves and exactly 43
  canonical `EXECUTION_HANDLERS` entries — `enabled − handlers = ∅` and `handlers − enabled = ∅`.
  `main.py`, `pgqueuer_worker.py`, `pgqueuer_scheduler.py`, and `supervisor.py` import no retired
  `run_manager`/`batch_runner`/`job_manager`/`media_job_manager`/`cancel_registry`/`builtin_handlers`
  symbol — startup is canonical-only.
- **Strengthened static absence (§4):** added
  `test_extracted_neutral_modules_have_no_lifecycle_coupling` — asserts `run_manager.py` is absent and
  that `extractor_runtime.py`/`official_pick.py` import no API route, PgQueuer transport, canonical
  `Job` mutation, delivery entry, `job_manager`, or `register_execution_handler`.
- **Verification:** full backend inventory **1308 passed, 0 failed, 0 skipped/xfail/xpass in 87.73s**
  (F2's 1307 plus the new static guard). Ruff over `marquee tests scripts`, `alembic check`, and
  `git diff --check` pass. OpenAPI unchanged at 199 paths; no schema/model/contract change in F3, so
  generated contracts are unaffected and the frontend baseline is unchanged.
- **Current phase:** F4 — production-like activation certification.
- **Exact next steps:** run the §5 activation matrix on owned disposable infrastructure — real
  worker-process recreation with a changed incarnation, schedule master gate off/on without a
  catch-up storm, external worker/scheduler Operations health and missing-entrypoint behavior,
  equivalent/conflicting concurrent submissions, exact feature-page recovery beyond 20 unrelated jobs
  with dropped SSE/snapshot repair, webhook-absence, and product/media evidence across the four
  primary feature areas; record every manual/tool/hardware exception honestly (Dolby Vision Profile
  5/7 stays readiness-disabled without a real `dovi_tool` smoke). Run all gates and commit F4.
- **Deviations:** the first fresh-vs-forward comparison used `pg_dump | grep`, which filtered to empty
  output and produced a spurious empty-string SHA match; it was discarded and redone with an
  `information_schema` column+index fingerprint that yields the real matching digests recorded above.
  No product, schema, or migration change resulted.
- **Pending operator actions:** unchanged — no push, activation, operator media/database access, or
  recovery-material deletion. Production schedule master gate remains default-off.

### F4 phase completion — production-like activation certification

- **F3 phase commit:** `b8c9a90818f697ef738c83de8a1b487015a0f411` (`certify startup schema
  and static retirement`).
- **Actual worker-process recreation:** on the owned PostgreSQL 18.3 target at
  `127.0.0.1:55452/marquee_test`, an external `pgqueuer_worker` process registered incarnation
  `2bbb5861-8f21-45c9-bd5e-76a5eb07d7ac`, became ready with all seven configured execution-class
  entrypoints, and stopped cleanly on `SIGINT`. Recreating the same logical node label produced
  distinct incarnation `ac8beefd-b92c-4490-b583-d599dd405ec3` with a different PID/start identity;
  the first row remained stopped and the second ready. The final updated-code smoke registered a
  third distinct worker incarnation `f4059961-687b-4da0-a217-aef18830d933`; all owned processes
  stopped cleanly after evidence capture.
- **External topology and schedule gate:** a separate real scheduler process registered
  `7740c0a9-78b1-4a4b-8005-0047045a25a2` (and final updated-code incarnation
  `e204e185-3732-44b6-b344-f2bdf845957b`). With `JOB_EMBEDDED_WORKERS=false`, the live Operations
  response reported no embedded supervisor, one fresh worker + one fresh scheduler, zero stale,
  scheduler present, no capability mismatch, zero picked/held/queued work, and 9 observed database
  connections within the configured 28 / maximum 32 budget. The real scheduler logged disabled
  `library-sync` and `poster-heal` occurrences for seven consecutive minutes with the production
  master gate false and created no transport row. Gate-on/off, disable/re-enable, two-scheduler
  reuse, occurrence coalescing, and no catch-up burst remain deterministically exercised by the
  green `test_jmc4a_schedules.py` suite; the real external scheduler was deliberately never started
  gate-on because this plan forbids activation and its production callbacks may contact configured
  integrations.
- **Activation defect found and corrected:** the first real `GET /api/system/operations` returned
  500 because `OperationsSchemaContract` incorrectly required integer schema versions and non-null
  durability while real `schema_contracts` rows contain string versions (`0008_jmc6e`, `1.1.1`)
  and nullable Marquee durability. The model now matches the canonical ORM/schema contract; a
  real-row regression was added; OpenAPI/TypeScript were regenerated; and the repeated live request
  returned 200 with both contract markers and external topology evidence.
- **Dolby Vision readiness:** the project-local binary exists as `dovi_tool 17ebb13`, but no
  owner-approved Profile 5/7 fixture smoke was available or performed. Added restart-owned
  `JOB_DOVI_CONVERSION_CERTIFIED=false`; the conversion route now returns typed 503
  `dovi_conversion_not_certified` before planning, and runtime Operations distinguishes binary
  availability (`dovi_tool=true`) from certified conversion readiness
  (`dovi_conversion_certified=false`). The live route returned 503. Existing deterministic mock /
  generated-fixture contracts remain green but do not certify Profile 5/7 conversion.
- **Overlap, refresh, recovery, webhook, and media matrix:** focused F4 suite is **223 passed in
  18.42s**, covering the F0-F3 retirement guards, JMC6E no-inline/page manifest, exact recovery
  beyond 20 unrelated jobs, concurrent equivalent coalescing and unsafe conflict, terminal scope
  release, schedule master behavior, external Operations/missing-entrypoint reporting, PgQueuer
  delivery/retry/hold/cancellation/fencing, canonical commands, webhook absence, poster,
  letterbox, audio/subtitle documents, and Dolby Vision readiness. The literal full backend suite
  is **1309 passed, 0 failed, 0 skipped/xfail/xpass in 87.25s**. Generated/confined media fixtures
  ran through the retained suite; no operator library, provider state, normal `DATA_DIR`, or
  owner media fixture was used, and no new live publish/restore/hardware encode smoke is claimed.
- **Schema/static/generated/frontend gates:** Ruff over `marquee tests scripts`; Alembic current,
  sole head, offline SQL, and model check; idempotent official migration/PgQueuer verification;
  static legacy/webhook/no-inline guards; and `git diff --check` pass. Schema remains 48 public
  tables at sole head `0008_jmc6e`; PgQueuer remains 1.1.1 durable. OpenAPI remains 199 paths at
  SHA-256 `4f70e9e7c5cbd90e5359fc8fadde37ad48272756e203a4a1f899b67459f9c7a4`; generated
  TypeScript SHA-256 is `96eedda857b02f9e54ae402bac3da5ea01c55438816e210e3eb8c4c73aa36d89`.
  Frontend: Svelte check 0 errors/0 warnings; Prettier/ESLint clean; 10 Vitest files / 111 tests;
  production build; and fresh-server Playwright/axe 11/11 pass. Local actionlint 1.7.7 passes from
  the retained JMC6C scratch binary; the ordinary PATH lacks `actionlint`. Build output retains only
  the recorded chunk-size/plugin-timing notices.
- **Definition/capability manifest:** 62 definitions / 43 enabled leaves / 19 ticketless or
  reserved types / exactly 43 canonical execution handlers. Production schedule keys remain
  `library-sync`, `poster-heal`, and `audio-subs-deep-scan`; `radarr_upgrade` remains reserved and
  all webhook routes remain absent. Runtime tools observed: Python 3.13.14, pytest 9.0.3, Ruff
  0.15.17, Node 22.22.2, npm 10.9.7, PostgreSQL 18.3, PgQueuer 1.1.1, and `dovi_tool 17ebb13`.
- **Current phase:** F5 — audit handoff, complete gates, and final-only compaction.
- **Exact next steps:** commit F4 with the configured author; append its hash; update the activation
  audit with corrected JMC6F authority, manifests, exceptions, and owner checklist; repeat the final
  complete backend/frontend/schema/PgQueuer/OpenAPI/client/static/actionlint/diff gates; record the
  full phase ledger, certified tree, intended tag, and operator work; then perform final-only
  recovery/bundle/squash/tag only if ownership, ancestry, concurrency, clean-tree, and tree-identity
  checks remain exact.
- **Deviations and reasons:** the first focused command named a nonexistent audio test module and
  collected nothing; it was discarded and rerun with `test_jmc5b_audio_subtitle_documents.py`.
  Sandboxed database attempts were blocked before test execution and were rerun with approved access
  to the owned loopback target. Ordinary `actionlint` PATH lookup failed; the verified 1.7.7 binary
  retained by JMC6C was located and passed. The Operations 500 and uncertified-DoVi readiness gap
  were real activation defects fixed without weakening any gate.
- **Pending operator actions:** do not push or activate. Preserve all JMC6B/C/D/E recovery material.
  Owner-approved Profile 5/7 fixture certification remains required before setting
  `JOB_DOVI_CONVERSION_CERTIFIED=true`. Browser authentication, public reset replacement, Docker
  hardening/version alignment, webhook implementation, `radarr_upgrade`, hosted CI, and final
  deployment/schedule/capability approval remain deferred.

### F5 integrated certification and activation handoff

- **F4 phase commit:** `5a7123fe98502d0e60654e51edaef5f987233795` (`recertify external
  activation topology`).
- **Completed work:** updated `jmc6c-activation-audit.md` with the corrected JMC6F activation
  candidate, exact definition/route/page/schema/generated-contract manifests, external topology,
  schedule/webhook disposition, capability exceptions, deferred risks, and owner checklist. Added
  the JMC6F legacy-retirement addendum to `jmc6-former-test-dispositions.md`, naming canonical
  replacement coverage for every removed or rewritten lifecycle test and recording the retained
  pure algorithm suites. No implementation, schema, or generated-contract content changed in F5.
- **Backend/static verification:** the literal full backend suite on the explicitly selected owned
  PostgreSQL 18.3 target at `127.0.0.1:55452/marquee_test` is **1309 passed, 0 failed, 0 skipped,
  0 xfail/xpass in 87.02s**. The focused JMC6F retirement + JMC6E seam freeze is **14 passed**.
  Ruff over `marquee tests scripts`, actionlint 1.7.7, static absence/no-inline/webhook contracts,
  and `git diff --check` pass. The manifest remains exactly 62 definitions / 43 enabled leaves / 19
  disabled parent-only or reserved types / 43 canonical delivery handlers.
- **Schema/PgQueuer/generated verification:** Alembic current and sole head are `0008_jmc6e`;
  offline SQL renders; `alembic check` reports no new upgrade operations; and the official migration
  service repeats idempotently while installing/upgrading/configuring/verifying PgQueuer 1.1.1
  durable and refreshing both schema-contract markers. The schema remains 48 public tables with
  column fingerprint `5696b90d283f77e77a8239c59cdb13c4`, index fingerprint
  `5570599ca32f71ee49eabe65932b8845`, and PgQueuer contract fingerprint
  `19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a`. OpenAPI is current at
  199 paths, SHA-256 `4f70e9e7c5cbd90e5359fc8fadde37ad48272756e203a4a1f899b67459f9c7a4`;
  regenerated TypeScript is byte-clean at SHA-256
  `96eedda857b02f9e54ae402bac3da5ea01c55438816e210e3eb8c4c73aa36d89`.
- **Frontend/browser verification:** generated-client drift passes; Svelte check is 0 errors / 0
  warnings; Prettier/ESLint pass; all 10 Vitest files / 111 tests pass; production build passes with
  only the previously recorded chunk-size/plugin-timing notices; and fresh-server Chromium
  Playwright/axe is 11/11. The sandbox-denied first Playwright server bind was discarded and the
  approved loopback run passed.
- **Capability and activation disposition:** the real F4 worker-recreation, external scheduler /
  Operations, exact refresh beyond 20 unrelated jobs, overlap-race, schedule-gate-off, and webhook
  absence evidence remains the activation authority. `JOB_PRODUCTION_SCHEDULES_ENABLED=false` and
  `JOB_DOVI_CONVERSION_CERTIFIED=false` remain the safe defaults. No operator media publish/restore,
  hardware encode, schedule activation, push, or Profile 5/7 `dovi_tool` certification occurred.
- **Current phase:** final-only recovery and exact-tree compaction.
- **Exact next steps:** commit F5 with the configured repository author; append the Git-derived
  phase ledger and certified F5 product tree; verify the clean, linear, JMC6F-only, unpushed range and
  absence of competing branch/worktree ownership; create timestamped recovery refs and a verified
  external bundle; re-check ownership/ancestry/tree identity; squash exactly the JMC6F range to
  `jmc6f: retire legacy runtimes and recertify activation`; prove sole-parent and exact pre/post tree
  identity; create annotated `jmc6f-complete`; make no later timeline edit and do not push or activate.
- **Deviations and reasons:** the first F5 backend invocation set the unconsumed variable
  `DATABASE_URL` instead of the repository-owned `DB_URL`, so settings inherited `.env` and the run
  reached the default PostgreSQL target. It stopped with 1292 passes, six backup failures from a
  missing contract table, and eleven migration setup errors from insufficient `CREATEDB`; the test
  harness uses isolated schemas and teardown completed, but this was an unintended operator-database
  access and no durable operator-state assertion is claimed. The result was discarded. The unchanged
  suite was immediately rerun with explicit `DB_URL` pointing at the owned `:55452` target and passed
  1309/1309. A first focused static command named two nonexistent test modules, then a second lacked
  approved loopback access; both collected no valid product result and were discarded before the
  correctly pinned 14/14 run. No code or contract was weakened in response.
- **Pending operator actions:** preserve all recovery material; do not push or activate. Require
  hosted CI, review the compact hash/tree/tag and audit, keep both schedule and Dolby Vision gates
  false until separately authorized/certified, and decide the deferred browser-auth, public reset,
  Docker, webhook, `radarr_upgrade`, and deployment risks.

### JMC6F certified phase ledger and compaction handoff

- **Verified linear phase range after `jmc6e-complete`:** F0
  `862e4fe731c80ce56dc36afcdb1315b32af8dc25`; F1
  `b4fb11b42013cefbb6f30a5bd822f0a73512901f`; F2
  `7632c40a0350633180432cb0014ad9ef54fff930`; F3
  `b8c9a90818f697ef738c83de8a1b487015a0f411`; F4
  `5a7123fe98502d0e60654e51edaef5f987233795`; F5
  `0fecf8512d4c433353b1fdfdaadc9b93b5285390`.
- **Certified F5 product tree:** `3176af23fd2f822e8e17b90a50bdd1ddae5cf19e`. The only subsequent
  pre-compaction content change is this Git-derived timeline ledger. Recovery refs, the external
  bundle, and the compact commit must preserve the ledger-bearing pre-compaction tree exactly; its
  hash is captured by terminal verification and reported to the owner.
- **Ownership, ancestry, concurrency, and publication:** the six phase commits form a single-parent
  linear range rooted directly at annotated compact `jmc6e-complete`; every author and committer is
  configured repository identity Gautam Chaudhri `<gautam.chaudhri@gmail.com>`. `job-manager` has
  exactly one worktree, no Git lock is present, the worktree was clean after F5, no remote-tracking
  ref contains the JMC6F tip, and `origin/job-manager` remains at `jmc6a-complete`. The range is
  local-only and unpushed.
- **Final manifests and dispositions:** 62 definitions / 43 enabled leaves / 19 disabled parent-only
  or reserved types / 43 canonical handlers; three default-off production schedule keys; five
  canonical seam route successors and the frozen 9-detail/14-overview page set; 199 OpenAPI paths;
  one Alembic head at `0008_jmc6e`; PgQueuer 1.1.1 durable. Deleted lifecycle wrappers and their
  tests have named canonical replacement coverage in the updated former-test ledger; retained pure
  letterbox, poster-ranking/OCR, archive, taste, and media-validation algorithms remain covered.
- **Intended compact identity:** exact message
  `jmc6f: retire legacy runtimes and recertify activation`, sole parent
  `b46766bb4cca229db087e2dd35a389d952e6115e`, and annotated tag `jmc6f-complete`. No push,
  activation, force-push, recovery deletion, or post-compaction timeline edit is authorized.
- **Current phase:** commit this final pre-compaction ledger, create and verify recovery material,
  then perform final-only exact-tree compaction and tagging.
- **Exact next steps:** record the ledger commit/tree in a timestamped recovery branch and annotated
  recovery tag; create and verify a complete external bundle; re-prove clean ownership, single-parent
  ancestry, one-worktree/no-lock concurrency, and unpushed status; soft-squash exactly
  `jmc6e-complete^{}..HEAD`; prove the compact tree is byte-identical to the recovery tip and the
  parent is exactly `jmc6e-complete^{}`; create annotated `jmc6f-complete`; make no further edit.
- **Pending operator actions:** preserve all JMC6 recovery material and review the final report.
  Only the owner may authorize the non-rewriting branch/tag push after hosted CI and then make the
  separate production activation/schedule/capability decision.
