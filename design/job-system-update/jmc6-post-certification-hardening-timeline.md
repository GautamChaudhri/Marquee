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
