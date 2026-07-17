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
