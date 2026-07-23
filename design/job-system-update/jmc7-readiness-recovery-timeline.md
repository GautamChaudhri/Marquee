# JMC7 Readiness Recovery Timeline

Shared execution record for the three-part release-readiness recovery program. The plans execute
strictly JMC7A → JMC7B → JMC7C. This file is created with the architecture package so every
implementer starts from the same independent-audit evidence and appends to one ledger.

No plan authorizes remote push, schedule activation, operator database/library/media mutation, or
production service changes. All implementation and certification use disposable PostgreSQL,
temporary `DATA_DIR`, deterministic fixtures, and explicitly accounted live capabilities.

## Program documents

1. [JMC7A runtime control and execution safety](jmc7a-runtime-control-and-execution-safety.md)
2. [JMC7B onboarding, publication, and learning integrity](jmc7b-onboarding-publication-and-learning-integrity.md)
3. [JMC7C Activity, contracts, and release certification](jmc7c-activity-contracts-and-release-certification.md)

## Locked sequence and handoffs

- JMC7A starts only from exact clean `jmc6k-complete` and produces `jmc7a-complete`.
- JMC7B starts only after independently verifying `jmc7a-complete` and produces
  `jmc7b-complete`.
- JMC7C starts only after independently verifying `jmc7b-complete` and produces
  `jmc7c-complete`.
- Each plan runs continuously through its internal phases, retains phase commits until every gate
  succeeds, creates verified repository-external recovery material, then performs final-only
  tree-identical compaction of its own range.
- No implementer pushes or activates. After `jmc7c-complete`, an independent audit must return
  Outcome B before browser/operator acceptance begins.

## Architect baseline — 2026-07-22

### Repository identity

- Branch: `job-manager`, one commit ahead of `origin/job-manager` before the JMC7 planning edits.
- Exact base/tag: `jmc6k-complete` → `9a9ac1a80eb0d4fbc11d7de6f37c1a039af6083c`.
- Parent: `704e93dff0626dfa087427a0a38e18b4c48fd1a6` (`jmc6j-complete`).
- Certified tree: `5a6c8bbaccfdd4ab5c8ea329bec9fda6f219b8cd`.
- Worktree was clean before these architecture documents were added.
- The prior `/tmp` repository-external JMC6K recovery bundle is no longer present. Local recovery
  refs remain. JMC7 must preserve this as an honest gap and create newly verified external bundles;
  it must not claim the missing bytes were recovered.

### Independent readiness-audit gates

- Disposable PostgreSQL migrations reached sole head/current `0014_jmc6k`; `alembic check` was
  clean. The temporary server was stopped after verification.
- Complete backend: **1373 passed, 3 skipped**, with one Paddle/CUDA capability warning.
- Focused JMC6K/runtime/process: **111 passed**.
- Explicit opt-in live CPU smokes: **3 passed**.
- Ruff: clean.
- Deterministic OpenAPI: current, **191 paths**.
- Generated TypeScript drift: clean.
- Svelte check: **0 errors, 0 warnings**.
- Vitest: **112 passed**; frontend lint and production build passed.
- Build emitted a large Plotly chunk of approximately 4.6 MB.
- Chromium Playwright/axe: **13 passed, 1 failed**. The Activity shell screenshot differed by
  12,460 pixels (ratio 0.02); a focused rerun failed identically. The three onboarding tests passed
  but used synthetic/stateless setup rather than a real lifecycle.
- No GPU capability was available. CPU paths were exercised; GPU was not reported green.
- Registry inventory: **43 enabled definitions and 43 execution handlers**, with no missing/extra
  handlers and presenters present for all definitions.

### Release-blocking findings and ownership

| Audit finding | Plan owner |
|---|---|
| Picked cancel fences out its own terminal writer | JMC7A |
| Safety-gate timeout/connection failure can strand held linked work | JMC7A |
| Retry is advertised broadly but rejected for most definitions | JMC7A |
| Inline letterbox preview executor bypasses canonical runtime | JMC7A |
| Onboarding review includes rejected candidates and loses neutral ordering | JMC7B |
| Failed/cancelled/no-change/crash onboarding effects are not recoverable | JMC7B |
| Automatic residual scheduling and replacement evaluation use inconsistent evidence/math | JMC7B |
| Publisher fabricates reload acknowledgement; readiness can disagree with runtime | JMC7B |
| Negative taste evidence is dropped by production profile construction | JMC7B |
| Activity drops/races records and accumulates unrelated SSE work | JMC7C |
| Onboarding contracts/browser certification are handwritten, incomplete, or declarative | JMC7C |
| Stale docs, dead scheduler, eager Plotly, missing old bundle, visual drift | JMC7C |

### Source anchors from the audit

- Cancellation/control: `marquee/core/jobs/control.py`, `delivery.py`, `pgqueuer_gateway.py`, and
  `tests/test_pgqueuer_delivery.py`.
- Admission/recovery: `safety_gates.py`, `delivery.py`, `pgqueuer_worker.py`, and
  `transport_intent_monitor.py`.
- Retry/actions: `control.py`, `presenters/base.py`, definition manifest/policies, and generated job
  action consumers.
- Preview: `marquee/api/routes/letterbox.py`, `marquee/core/letterbox_preview.py`, and binary/process
  helpers.
- Survivor review: poster runner payload construction, `internal_runner.py`, and
  `onboarding_review.py`.
- Onboarding recovery: `api/routes/onboarding.py`, job submission, onboarding review/decision,
  poster mutation handlers, exemplars, and readiness.
- Residual/profile/publication: `feedback.py`, `handlers_ml.py`, `residual.py`,
  `taste_preferences.py`, `ml_publication.py`, and poster pipeline scorer resolution.
- Activity/contracts: `FeatureActivityPanel.svelte`, shared jobs store, onboarding page/client,
  OpenAPI generation, closure manifests, and Playwright specs.

### Planning decision

Three plans are the smallest safe split:

1. JMC7A repairs the shared runtime substrate used by every later job.
2. JMC7B repairs the connected onboarding/profile/residual/publication aggregate on that substrate.
3. JMC7C repairs the client/contracts and performs real stateful, whole-system certification.

One plan would be too broad to retain meaningful stop gates and handoffs. Two plans would either mix
runtime ownership with personalization semantics or ask the same session to implement product
contracts and independently certify them. More than three would create artificial boundaries
inside the same transactional/domain invariants.

### Architecture-tool status

- Required ByteRover CLI commands `brv query` and `brv swarm query` were attempted and failed with
  `brv: command not found`. The available ByteRover MCP query returned the existing JMC planning
  conventions and predecessor decisions.
- Serena MCP was available, its manual was read, the Marquee project was activated, and its symbol/
  pattern tools were used to verify the plan ownership surfaces.
- Shell and Git inspection used the required RTK proxy.

### Current status

- **Completed:** independent audit, three-plan decomposition, locked JMC7A/JMC7B/JMC7C architecture,
  shared timeline creation, and root design-timeline update.
- **In progress:** architecture-document verification only.
- **Next:** review the JMC7 document diff and links. The first implementation session then starts
  JMC7A Phase 7A0 from exact `jmc6k-complete` plus the architecture commit selected by the owner.
- **Implementation changes:** none.
- **Pending operator actions:** none. Do not run implementation, browser/operator acceptance, push,
  or activation as part of this planning checkpoint.

## Implementer append protocol

After every phase commit append, tersely and factually:

- plan/phase and configured-author commit/tree/parent;
- completed behavior and exact production paths changed;
- commands with exact pass/fail/skip/warning counts;
- migration head and schema/generated-contract fingerprints when applicable;
- current phase and exact next actions;
- deviations and why they preserve locked decisions;
- disposable environment and capability accounting;
- repository status, recovery material, and pending operator actions.

Never rewrite earlier checkpoints except to correct a factual typo with an explicit correction
entry. A resumed implementer reads this file in full, verifies its claims against Git and the
worktree, and continues from the recorded phase.

## JMC7A implementation stop — 2026-07-23

- **Current state:** JMC7A Phase 7A0 remains incomplete and Phase 7A1 has one uncommitted
  cancellation fix. Do not treat `cc888e6` as a completed-phase handoff.
- **Verified base:** clean `jmc6k-complete` commit
  `9a9ac1a80eb0d4fbc11d7de6f37c1a039af6083c`, tree
  `5a6c8bbaccfdd4ab5c8ea329bec9fda6f219b8cd`, parent
  `704e93dff0626dfa087427a0a38e18b4c48fd1a6`; committed planning baseline
  `065e1672fdf9705ba5a41c22ce95529cbab34f09` is its direct child and is authored by the
  configured repository user `Gautam Chaudhri <gautam.chaudhri@gmail.com>`. The worktree was
  clean before implementation edits; no unrelated work was present.
- **Baseline evidence:** PgQueuer is installed at version 1.1.1. `alembic heads`, disposable
  PostgreSQL `alembic current`, and `alembic check` each reported sole/current `0014_jmc6k` and
  no pending upgrade operations. OpenAPI check reported 191 paths; SHA-256 fingerprints were
  `35c3c15e2fc95a71117845ff4a08cf8d3fe58d5d87f7ad7ec69d876f7499d423`
  (`design/api-schema.json`) and
  `2ab6f9c156e01f4ef179ca75e1f5e68c8b8f421e94d9197f3efdea73edc7aef`
  (generated TypeScript); generated-contract drift was clean. Full backend against the disposable
  PostgreSQL schema and fixture-owned temporary DATA_DIR: **1373 passed, 3 skipped, 1 Paddle CUDA
  CPU-fallback warning**. Ruff, Svelte check (0 errors/0 warnings), lint, 112 Vitest tests, and
  production build passed. This Chromium Playwright run passed 14/14; it did not reproduce the
  historically recorded Activity screenshot mismatch, and no snapshot was changed or re-recorded.
- **PgQueuer 1.1.1 source audit:** `core/qm.py::_dispatch` shields cancellation status buffering;
  `Queries.mark_job_as_cancelled()` logs cancellation and emits the cancellation notification;
  `QueryQueueBuilder.build_retry_job_query()` atomically returns a held/picked row to `queued`
  with a delayed `execute_after`; dequeue claims with `FOR UPDATE SKIP LOCKED` and writes
  `picked` atomically. These are the supported cancellation/retry mechanics to use; no behavior
  was inferred from mocks.
- **Committed work:** `cc888e6` (`freeze runtime defects`, configured author) adds
  `test_public_cancel_keeps_active_delivery_fence_until_terminalization`. It drives the public
  `control.cancel` service through the real PgQueuer callback and fenced delivery. It fails on the
  exact baseline with `stopping` and a bumped fence rather than a terminal `cancelled` attempt.
- **Uncommitted owned change:** `marquee/core/jobs/control.py` removes the picked-job control-path
  fence advance, leaving terminal ownership with the active delivery. The new focused test then
  passes, and `tests/test_pgqueuer_delivery.py tests/test_process_launcher.py
  tests/test_cooperative_cancellation.py -q` reports **69 passed**. This is intentionally not
  certified or committed because Phase 7A0 has not completed its required ByteRover workflow and
  the remaining 7A0 defect tests have not been added.
- **Blocking evidence:** ByteRover swarm configuration is absent. `rtk brv query "JMC7A runtime
  control execution safety ..."` repeatedly lost its daemon connection; `rtk brv status` timed out
  waiting for the daemon. The documented `rtk brv restart` stopped all ByteRover processes, but a
  subsequent query exhausted restart attempts 2–10 and ended with `Failed to start daemon: timed
  out waiting for daemon to become ready`. Its update check also reports inaccessible
  `/home/cptbandit/.config`. Serena remains available and its manual was read; all shell/Git/test
  commands used RTK.
- **Unblock condition:** restore a functioning ByteRover daemon/provider (the CLI’s required
  `brv query` and `brv swarm query` must complete; swarm currently also needs configuration), then
  re-read this ledger, verify `cc888e6`, the dirty owned diff, and the clean ancestor before
  continuing Phase 7A0. Do not discard, amend, or claim certification for the uncommitted
  cancellation change. No recovery material, external bundle, compaction, push, schedule
  activation, operator database/library/media access, or agent attribution was created.

## JMC7A Phase 7A1 — cancellation ownership — 2026-07-23

- **Authorization/deviation:** the owner explicitly authorized continuation without ByteRover after
  the preceding documented daemon failure. The failure remains recorded above; no ByteRover result
  is claimed.
- **Commit:** `6e4864a` (`fix cancel ownership`, configured repository author), parent
  `cc888e6`. It removes the non-batch public-control fence increment before
  `PgQueuerGateway.cancel_known_ticket()`. A picked delivery therefore retains its active fence
  through descendant shutdown, cancellation terminalization, evidence sealing, and transport
  acknowledgement. Queued cancellation remains gateway-owned terminalization; batch-parent
  projection behavior is unchanged.
- **Executable proof:** the committed `cc888e6` public-control test fails on the predecessor with
  `stopping` and a bumped stale fence. With `6e4864a`,
  `tests/test_pgqueuer_delivery.py::test_public_cancel_keeps_active_delivery_fence_until_terminalization -q`
  passes. The focused delivery/process/cancellation gate
  `tests/test_pgqueuer_delivery.py tests/test_process_launcher.py tests/test_cooperative_cancellation.py -q`
  reports **69 passed**; `ruff check marquee tests` passes.
- **Next:** complete the remaining Phase 7A0 executable defect cases while implementing Phase 7A2
  admission deferral, then run its real PostgreSQL contention/recovery gates. The timeline remains
  intentionally dirty until the next phase commit. No migration, generated contract, operator data,
  external recovery material, push, or activation has occurred.

## JMC7A Phase 7A2 checkpoint — admission deferral — 2026-07-23

- **Commit:** `0bbd733` (`defer admission safely`, configured repository author), parent
  `6e4864a`. This checkpoint includes the preceding Phase 7A1 ledger entry and the Phase 7A2
  production changes.
- **Behavior:** `SafetyGateTimeoutError` and allowlisted gate-connection loss are now intercepted
  before `_admit_delivery` can create a `JobAttempt`. The canonical job records a bounded pending
  deferral event/attention state and PgQueuer receives `RetryRequested`, which uses its installed
  1.1.1 delayed requeue primitive. The bound is 1s, 5s, 30s; exhaustion writes an explicit
  canonical failed outcome and leaves PgQueuer held. The monitor now examines linked queued work,
  observes a committed retry, and can requeue a held/picked pending admission ticket using
  `Queries.retry_job()` through the enlisted gateway connection. New typed `AdmissionDeferral`
  carries the job, dispatch, ticket, cause, count, and next eligibility at that gateway boundary.
- **Proof:** a real PostgreSQL advisory maintenance lock is held past a 50ms admission deadline in
  `test_real_gate_contention_defers_without_attempt_then_executes_once`. It observes zero attempts,
  one durable pending deferral, releases the lock, and proves exactly one successful attempt.
  Focused `tests/test_pgqueuer_delivery.py tests/test_safety_gates.py
  tests/test_jmc3a_staging_and_monitor.py -q` reports **70 passed**; targeted Ruff is clean.
- **Remaining before Phase 7A2 certification:** add the required transport-absence recovery,
  callback-interruption, transient-connection, hard-error, exhaustion, and cancellation-race
  cases; do not represent this checkpoint as the completed 7A2 phase. Next implementation work is
  the definition-owned retry resolver in Phase 7A3.

## JMC7A Phase 7A3 checkpoint — retry truth — 2026-07-23

- **Commit:** `f5082c1` (`resolve retry capability`, configured repository author), parent
  `0bbd733`. It includes the preceding Phase 7A2 ledger entry.
- **Behavior:** all definitions receive an explicit retry mode at registry construction:
  `generic` for `system_noop`, `domain_coordinated` for `taste_rebuild` and canonical parents, and
  `unsupported` otherwise. `resolve_retry_capability()` is now the sole retry truth used by the
  presenter and by the command transaction; terminal state, outcome, enabled state, action policy,
  mode, and bounded request validation yield its stable reason. The retry command no longer checks
  a separate allowlist before resolving that capability. Queued cancellation only advances a fence
  after terminal proof; picked cancellation retains the active writer's fence.
- **Proof:** `tests/test_job_commands.py tests/test_job_presenters.py
  tests/test_job_definition_manifest.py tests/test_job_presentation_contract.py -q` reports
  **43 passed**. The queued/picked cancellation regression pair reports **2 passed** and targeted
  Ruff is clean.
- **Remaining before Phase 7A3 certification:** execute the required 43-definition public-command
  matrix (including stale, unsafe, missing subject, duplicate and domain successor cases) rather
  than treating resolver-unit coverage as that matrix. Next: canonical letterbox preview in Phase
  7A4.

## JMC7A Phase 7A4 checkpoint — canonical letterbox preview — 2026-07-23

- **Commit:** `14d1db5` (`queue letterbox previews`, configured repository author), parent
  `f5082c1`. It includes the preceding Phase 7A3 ledger entry.
- **Behavior:** an explicit `letterbox_preview` MEDIA_READ definition and bounded typed request now
  model movie/episode preview rendering. Both former GET byte-generation routes are POST
  submission/reuse routes returning the canonical job response; the GET path is retired. The
  handler resolves the server-side MediaFile, renders only in the attempt workspace through
  `ExecutionContext.process_launcher`, registers a bounded `evidence_image` artifact, and returns
  typed result evidence. The registry/inventory count is now 63 total definitions (the predecessor
  43 enabled-definition audit remains historical, not rewritten).
- **Proof:** letterbox API/unit tests report **97 passed** and the manifest contract tests report
  **8 passed**; targeted Ruff is clean. OpenAPI was regenerated (still 191 paths) and TypeScript
  generation completed; those generated files and this ledger entry are uncommitted pending the
  next checkpoint.
- **Remaining before Phase 7A4 certification:** add real preview delivery/cancellation/timeout,
  artifact retrieval/confinement, reuse, movie/TV SSE discovery, and static-retirement tests;
  update frontend preview callers to the generated POST contract. Do not treat this checkpoint as
  complete 7A4 or JMC7A certification.

## JMC7A Phase 7A1 completion — cancellation ownership — 2026-07-23

- **Commit/tree/parent:** `267f235` (`finalize cancellation recovery`), configured author
  `Gautam Chaudhri <gautam.chaudhri@gmail.com>`, tree
  `ec686bcaaa2dd6a22fb0076729b8b047d08b4fc7`, parent
  `14d1db5f759689830c606d1431922076e3ee38a5`.
- **Completed behavior:** public queued cancellation remains gateway-owned; a picked delivery
  retains its fence and confirms durable cancellation intent before it publishes a terminal
  success. PgQueuer status reads now inspect the live queue relation rather than its historical
  log. If the active writer is gone, orphan reconciliation proves process liveness first and then
  takes exactly one recovery fence to terminalize the cancelling attempt. The public control,
  PgQueuer gateway, active delivery, fenced writer, process ownership, and recovery paths are all
  covered without substituting a direct gateway call for the public cancel action.
- **Evidence:** the focused public cancellation/recovery gate
  `tests/test_pgqueuer_delivery.py::test_picked_cancellation_reaches_test_blocker`,
  `::test_public_cancel_keeps_active_delivery_fence_until_terminalization`, and
  `::test_expired_cancelled_attempt_is_terminalized_by_one_recovery_fence` exited successfully
  against disposable PostgreSQL at `localhost:55455/marquee_test`. The combined runtime,
  definition, and preview gate reported **207 passed in 41.40s**; `ruff check marquee tests` and
  `git diff --check` were clean. The real preview cancellation and timeout paths are retained for
  Phase 7A4 certification, not counted as a replacement for this phase's ownership gate.
- **Migration/contracts:** migration head remains sole/current `0014_jmc6k`; this phase changes
  neither migration nor generated contracts.
- **Status/next:** Phase 7A1 is complete. Proceed directly with Phase 7A2's transport-absence,
  transient/hard gate-failure, exhaustion, and public cancellation-during-admission proofs.
- **Environment/recovery:** all tests used the disposable PostgreSQL cluster and fixture-owned
  temporary data only. The worktree contains only owned JMC7A changes plus this append. No push,
  schedule activation, operator data/media access, or agent attribution occurred. The inherited
  external JMC6K recovery bundle remains unavailable; it was not recreated or represented as
  verified recovery material.

## JMC7A Phase 7A2 completion — admission and recovery — 2026-07-23

- **Commit/tree/parent:** `779aba7` (`complete admission recovery`), configured author
  `Gautam Chaudhri <gautam.chaudhri@gmail.com>`, tree
  `7e207e299793961bf5589958ceb0c5e4110a2b6a`, parent
  `267f23580ab415f1447c9b89a7ff89ad3d2b6493`.
- **Completed behavior:** the canonical pre-admission deferral is durable before PgQueuer retry,
  updates both job and dispatch eligibility, and cannot create an attempt. The gateway reads the
  live PgQueuer relation, repairs an absent deferred transport ticket on the same dispatch, and
  retains the exact bounded lineage. The monitor resolves pending cancellation before any generic
  transport recovery. Only timeout and an allowlisted connection-loss class defer; hard connection
  errors remain held and the bounded deferral budget terminates without a phantom attempt.
- **Evidence:** against disposable `localhost:55455/marquee_test`, the real advisory-lock
  contention case plus public cancel-during-gate-wait, transport-absence recovery,
  transient-connection, hard-error, and exhaustion cases, together with
  `tests/test_safety_gates.py` and `tests/test_jmc3a_staging_and_monitor.py`, reported
  **20 passed in 3.54s**. `ruff check marquee tests` reported `All checks passed!`; staged
  `git diff --check` was clean. The immediately preceding Phase 7A1 gate reported
  **3 passed in 1.32s** through the same public PgQueuer/disposable PostgreSQL path.
- **Migration/contracts:** migration head remains sole/current `0014_jmc6k`; this phase changes
  neither migration nor generated contracts.
- **Status/next:** Phase 7A2 is complete. Proceed directly to Phase 7A3's definition-owned
  retry matrix and public successor-action proofs.
- **Deviation/environment/recovery:** initial restricted-sandbox test attempts could not reach
  the disposable local PostgreSQL listener and timed out before fixture setup; the approved RTK
  escalated path was then used and produced the evidence above. No behavior or gate was weakened.
  No operator resource, push, schedule activation, or agent attribution occurred. The inherited
  external JMC6K recovery bundle is still unavailable and unrepresented as verified material.

## JMC7A cancellation correction — 2026-07-23

- **Commit/tree/parent:** `3b7e0bc` (`preserve terminal cancellations`), configured author
  `Gautam Chaudhri <gautam.chaudhri@gmail.com>`, tree
  `a1180417c54a9a4d9f48f6e1fe90e470d262a49c`, parent
  `779aba7c45199c7487be7687c2fa92c169fa2bd7`.
- **Behavior/evidence:** an already terminal cancellation now returns its canonical snapshot
  idempotently without re-signalling transport, reopening work, or requiring a stale historical
  fence. The public terminal-cancel regression plus the active-delivery ownership regression
  reported **2 passed in 0.99s** against disposable PostgreSQL; focused Ruff and staged diff checks
  were clean. This closes an owned 7A1 control-surface condition discovered during the 7A3 source
  audit; it preserves the locked active-writer and queued-gateway ownership split.
- **Status:** no migration/generated-contract change, no operator resource or external recovery
  material. Continue directly with Phase 7A3.

## JMC7A Phase 7A3 completion — definition-owned retry truth — 2026-07-23

- **Commit/tree/parent:** `4112ab3` (`certify retry actions`), configured author
  `Gautam Chaudhri <gautam.chaudhri@gmail.com>`, tree
  `0c933931997d465ae74ad8347b7554646eb712cc`, parent
  `3b7e0bcf2e745b904c58eb69c420322cc4fcbce4`.
- **Completed behavior:** registry construction rejects unsafe generic retry declarations and
  enforces retry policy/mode consistency. The command resolver now uses definition effect safety,
  subject kind/reference, and typed request validity as its one generic-retry truth. A retry
  successor always uses its owning definition's entrypoint rather than an implicit control-path
  compatibility route. The profile-coordinated retry travels through the public command endpoint
  and preserves its domain-owned successor evidence.
- **Evidence:** the public all-definition retry-capability matrix plus command, presenter,
  manifest, presentation-contract, and profile-coordination suites reported **52 passed in 2.54s**
  against disposable `localhost:55455/marquee_test`. It includes the 63-definition unsafe-terminal
  public-action denial scan, stale replay, active/missing-subject rejection, generic successor,
  and domain successor cases. Focused Ruff reported `All checks passed!`; staged diff check was
  clean.
- **Migration/contracts:** migration head remains sole/current `0014_jmc6k`; this phase changes
  neither migration nor generated contracts.
- **Status/next:** Phase 7A3 is complete. Proceed directly with Phase 7A4's canonical POST
  preview delivery, process containment, artifact, movie/TV, and static-retirement certification.
- **Environment/recovery:** all executable proof used disposable PostgreSQL and temporary test
  data only. No operator resource, push, activation, or agent attribution occurred. The missing
  inherited JMC6K external recovery bundle remains an explicitly unavailable historical item.

## JMC7A Phase 7A4 completion — canonical letterbox preview — 2026-07-23

- **Commit/tree/parent:** `9ac59cc` (`complete preview execution`), configured author
  `Gautam Chaudhri <gautam.chaudhri@gmail.com>`, tree
  `2f6ff8e595d0915ab0794468f161ddf4d3180f65`, parent
  `4112ab3205c40a3a0633737fe849b8de33423ef9`.
- **Completed behavior:** movie and TV preview requests are canonical POST submission/reuse
  actions for `letterbox_preview`, dispatch to the `media_read` PgQueuer entrypoint, and expose
  only shared job/artifact discovery. The registered handler resolves the server-side media file,
  launches fixed ffmpeg arguments through `ExecutionContext.process_launcher`, polls cancellation
  while waiting, confirms terminal process evidence before delivery writes, and registers one
  bounded `preview_image` artifact. Former helper/route execution is retired to a non-executing
  compatibility-free surface; frontend callers consume the generated POST contract and artifact
  result instead of raw preview bytes or media paths.
- **Evidence:** the integrated runtime/definition/preview gate reported **208 passed in 40.76s**
  against disposable `localhost:55455/marquee_test`; it includes real movie delivery, real ffmpeg
  cancellation and timeout process-death proof, public artifact retrieval/reuse, TV submission,
  fences, recovery, and the public controls. Ruff reported `All checks passed!`; staged diff check
  was clean. Static route/helper checks found no `run_in_executor` or `create_subprocess`
  reachability; the sole `subprocess` match is a route docstring stating the retirement rule.
- **Contracts/frontend:** deterministic OpenAPI export is current at **191 paths** and generated
  TypeScript drift is clean. Svelte check reported **0 errors, 0 warnings**; lint passed after the
  repository formatter; Vitest reported **10 files / 112 tests passed**; production build passed.
- **Migration/status:** migration head remains sole/current `0014_jmc6k`; no migration was needed.
  Phase 7A4 is complete. Next is Phase 7A5 full certification, capability accounting, recovery
  verification, tree-identical final compaction, local annotated tag, and the exact JMC7B handoff.
- **Environment/recovery:** all execution used disposable PostgreSQL, fixture temporary paths, and
  generated test media only. No operator media/data, push, schedule activation, or agent
  attribution occurred. The inherited external JMC6K bundle remains unavailable and was not
  recreated or claimed as verified.

## JMC7A preview inventory correction — 2026-07-23

- **Commit/tree/parent:** `c4b97c8` (`cover preview handler inventory`), configured author
  `Gautam Chaudhri <gautam.chaudhri@gmail.com>`, tree
  `75486cd788ad3ec0585c0a92245b5d84e0bb7bd2`, parent
  `9ac59cc4fcee0bb4d7b195c39f41c95a718a4298`.
- **Evidence:** full certification first exposed the exact enabled-definition/handler inventory
  omission for the new `letterbox_preview` definition. Both enumerations now include it;
  `tests/test_backup.py::test_only_system_noop_is_dispatch_enabled_and_executable` reports
  **1 passed in 0.61s** and focused Ruff is clean. This is a test-contract correction only; no
  runtime behavior, policy, compatibility path, or lock decision changed.
- **Status:** resume Phase 7A5 from a fresh full-suite run. No migration, operator resource, push,
  activation, or recovery-material claim occurred.

## JMC7A artifact transaction-ownership correction — 2026-07-23

- **Commit/tree/parent:** `17ba253` (`avoid artifact self deadlock`), configured author
  `Gautam Chaudhri <gautam.chaudhri@gmail.com>`, tree
  `0b307ec25d0a86fd96c6a5654fc8c87476a3999e`, parent
  `c4b97c81e89b9ff568b5f2e5de3353069f6f9976`.
- **Diagnosis and completed behavior:** fresh full certification exposed the public feedback
  negative-exemplar path waiting on itself. The exact `jmc6k-complete` predecessor passes that
  test in **0.94s**; a temporary statement timeout on disposable PostgreSQL identified the new
  `Job` `FOR UPDATE` fence in `register_physical_artifact` as a second transaction waiting on the
  feedback transaction that already owns the same row. The fence, attempt lock, preview active-kind
  count, and artifact policy remain unchanged. Caller-owned feedback and onboarding transactions
  now perform reserve, publication, and failure recording through the same canonical artifact
  boundary; independent delivery paths retain their transaction-owned reservation/publication flow.
- **Evidence:** the public feedback regression reports **1 passed in 0.83s**. The feedback,
  taste-authority, and physical-artifact suites report **40 passed in 5.62s** against disposable
  `localhost:55455/marquee_test`; focused Ruff and staged diff checks are clean. The temporary
  database statement timeout was reset before this proof.
- **Status/next:** resume Phase 7A5 from a fresh full suite, then complete migrations/contracts,
  capability/accounting, external recovery material, exact tree-identical compaction, annotated
  local tag, and the JMC7B handoff. No operator resource, push, schedule activation, or agent
  attribution occurred.

## JMC7A complete preview inventory correction — 2026-07-23

- **Commit/tree/parent:** `d9c4b9f` (`complete preview inventory`), configured author
  `Gautam Chaudhri <gautam.chaudhri@gmail.com>`, tree
  `59fc032d4dafa809b59f10d7fc710fa109ee236e`, parent
  `17ba253e6b9a35689d9a8e177dc41b3083b623f3`.
- **Completed behavior:** `letterbox_preview` now has the required dedicated `LetterboxPresenter`
  and its honest render headline. The JMC3–JMC5 exact enabled-definition, execution-handler,
  route-constructed, and presenter inventories now declare the canonical preview leaf and the
  63-definition cardinality.
  The historical freeze checks record exactly the two JMC7A recovery-surface additions:
  `FencedWriter.recover_cancelled` and `PgQueuerGateway.recover_admission_deferral`; no production
  recovery or preview behavior was changed to satisfy a stale assertion.
- **Evidence:** focused Ruff is clean. Registry/fence/gateway/handler/presenter certification reports
  **63 passed in 3.11s**, and the real-app JMC3 enabled-registry check reports **3 passed in 0.67s**.
  Both run without operator data or a database write. The earlier full pass had reached **273 passed**
  before exposing its first stale static inventory assertion.
- **Status/next:** resume the Phase 7A5 full suite against disposable PostgreSQL from a fresh process.
  One database-backed focused rerun request was rejected before execution by the automatic approval
  service, so it is deliberately not represented as a test result; the target remains only
  `localhost:55455/marquee_test` and `/tmp/marquee-jmc7a-data.wutha4`. No operator resource, push,
  schedule activation, or agent attribution occurred.

## JMC7A preview closure correction — 2026-07-23

- **Commit/tree/parent:** `b1a69b6` (`record preview closure`), configured author
  `Gautam Chaudhri <gautam.chaudhri@gmail.com>`, tree
  `9e4960d2c045784764d788431267c05ecdf251f8`, parent
  `d9c4b9fd5eab51ff9867aa932005a51c19216ea2`.
- **Completed behavior:** the authoritative JMC6H enabled-definition closure now includes
  `letterbox_preview` with its actual handler, built-in result contract, bounded preview-frame I/O,
  ffmpeg-only tool fact, retry/progress policy, canonical POST producer, JobArtifact consumer, and
  JMC7A certification references. The source-inventory cardinality is corrected to 63 built-ins.
  This is closure evidence only; it introduces no execution, compatibility, or route behavior.
- **Evidence:** manifest, registry, inventory, document, JMC6G/JMC6H closure, JMC6K freeze, and
  presentation-contract checks report **50 passed in 1.88s**; focused Ruff and diff checks are
  clean. The CPU real taste-profile capability smoke also reports **1 passed in 1.47s** with
  `MARQUEE_LIVE_SMOKE=1`, isolated `XDG_CACHE_HOME`, and temporary JMC7A data only. GPU inspection
  reported no usable device and remains honestly unavailable.
- **Status/next:** resume the complete backend suite against disposable PostgreSQL, then execute
  Alembic current/check and the remaining Phase 7A5 certification/recovery/compaction protocol.
  No operator resource, push, schedule activation, or agent attribution occurred.

## JMC7A configured data-root certification correction — 2026-07-23

- **Commit/tree/parent:** `afafa62` (`honor configured data roots`), configured author
  `Gautam Chaudhri <gautam.chaudhri@gmail.com>`, tree
  `a1d916053ab3712fda5dbee69f4f88ae07ae6b82`, parent
  `b1a69b6cdbbeb9ccad49277134a56da6b943e0d1`.
- **Diagnosis and completed behavior:** the first captured full suite reported **1 failed,
  1387 passed, 3 skipped, 1 warning in 144.86s**. The only failure hard-coded
  repository `data/runs/work` while certification correctly supplied temporary `DATA_DIR`. The
  exact `jmc6k-complete` predecessor fails the same assertion under the same disposable-data
  configuration in **0.29s**, proving it inherited rather than JMC7A runtime behavior. The test
  now compares the runner's import-bound root with a fresh `Settings()` process configuration;
  this stays valid after the session fixture temporarily redirects the mutable singleton.
- **Evidence:** `tests/test_pipeline_revised.py::test_pipeline_run_root_is_inside_data` reports
  **1 passed in 0.34s** against disposable PostgreSQL and temporary data; focused Ruff and diff
  checks are clean. No production runner, gateway, delivery, fence, artifact, recovery, generated
  contract, Activity screenshot, or schedule behavior changed.
- **Status/next:** restart the complete backend suite from a fresh process, then run Alembic
  current/check and the remaining Phase 7A5 certification/recovery/compaction protocol. No
  operator resource, push, schedule activation, or agent attribution occurred.

## JMC7A Phase 7A5 certification — 2026-07-23

- **Certification scope:** all JMC7A runtime-control, PgQueuer gateway, delivery, fenced-writer,
  artifact, process, preview, retry, and recovery paths were exercised through their public/canonical
  surfaces on owned disposable PostgreSQL `localhost:55455/marquee_test` and temporary data only.
  The final full backend result is **1388 passed, 3 skipped, 1 CPU-fallback warning in 149.25s**.
  The skipped cases are opt-in capability tests; they are not counted as green live evidence.
- **Focused executable evidence:** cancellation/recovery **3 passed in 1.32s**; admission/recovery
  **20 passed in 3.54s**; terminal cancellation ownership **2 passed in 0.99s**; definition-owned
  retry/control/manifest matrix **52 passed in 2.54s**; canonical preview runtime/process/artifact
  gate **208 passed in 40.76s**; feedback/taste/artifact self-deadlock regression **40 passed in
  5.62s**; registry/fence/gateway/handler/presenter inventory **63 passed in 3.11s**; and the
  final closure/inventory/document/presentation group **50 passed in 1.88s**. These proofs use
  the real public control and PgQueuer/delivery/fence/artifact/recovery paths, not mock bypasses.
- **Schema/contracts/static:** full Ruff reports `All checks passed!`; sole Alembic head/current is
  `0014_jmc6k` and `alembic check` reports `No new upgrade operations detected`. OpenAPI is current
  at **191 paths**; SHA-256 values are `design/api-schema.json`
  `2cec76614f6951ce4b94f5de750eef00c95654b639e590627143fca0c8f874e6` and generated TypeScript
  `b0d427107096ea5b5a82a24aa8d692a96dd0a35e1c3a9c5dba0e0d916ed57497`; generation is byte-clean.
  Static preview retirement finds no route/helper `run_in_executor` or `create_subprocess`; the only
  `subprocess` string is the retirement-rule docstring. `git diff --check` is clean.
- **Frontend/browser/capability evidence:** Svelte check reports **0 errors, 0 warnings**; Prettier
  and ESLint pass; Vitest reports **10 files / 112 tests passed**; production build passes; and the
  fresh Chromium Playwright/axe run reports **14 passed**. No Activity snapshot was re-recorded or
  changed: the inherited JMC7C-owned Activity screenshot disposition remains untouched. The real
  CPU taste-profile smoke reports **1 passed in 1.47s** with `MARQUEE_LIVE_SMOKE=1`, isolated cache,
  and temporary data. `nvidia-smi` reports no usable device, so GPU remains honestly unavailable;
  the backend warning records its CPU fallback and is not treated as GPU certification.
- **Environment/deviations:** the diagnostic database statement timeout was reset and verified as
  `0`. The initial full run exposed only stale JMC7A inventory/closure expectations and the inherited
  mutable-data-root assertion; all were corrected with canonical behavior preserved, and the exact
  `jmc6k-complete` predecessor reproduces the latter under a temporary data root. No operator data
  or media, push, schedule activation, or agent attribution occurred.
- **Current phase/next:** commit this certification record with the configured author; append a
  Git-derived pre-compaction ledger; then create and verify timestamped recovery branch/tag plus a
  repository-external bundle, prove clean single-worktree linear ancestry, soft-squash exactly from
  `jmc6k-complete`, prove tree identity and sole parent, annotate `jmc7a-complete`, and make no
  subsequent timeline edit.

## JMC7A certified phase ledger and compaction handoff — 2026-07-23

- **Verified predecessor:** annotated `jmc6k-complete` resolves to
  `9a9ac1a80eb0d4fbc11d7de6f37c1a039af6083c`, tree
  `5a6c8bbaccfdd4ab5c8ea329bec9fda6f219b8cd`, sole parent
  `704e93dff0626dfa087427a0a38e18b4c48fd1a6`. The JMC7A range is single-parent,
  linear, configured-author-only, and local-only; `job-manager` is ahead of
  `origin/job-manager` and no push occurred.
- **Certified phase tip:** `f96afc7711479aeac8f81e7711ecdf0b95cb1d05`
  (`record jmc7a certification`) has configured author/committer Gautam Chaudhri
  `<gautam.chaudhri@gmail.com>`, parent `afafa62d58fd25ee83cf1661cfd3ea7e150e60ce`,
  and certified product tree `de7808f7e63e334f06ca5192c1b3edaaa66dfe1b`. Its exact
  linear phase history is planning baseline `065e167`, 7A0 `cc888e6`, cancellation
  `6e4864a`/`267f235`/`3b7e0bc`, admission `0bbd733`/`779aba7`, retry
  `f5082c1`/`4112ab3`, preview `14d1db5`/`9ac59cc`, inventory/closure
  `c4b97c8`/`d9c4b9f`/`b1a69b6`, and certification corrections `17ba253`/`afafa62`.
- **Pre-compaction invariant:** the only content after the certified phase tip is this final
  ledger. Commit it with the configured author; the recovery branch/tag and external bundle must
  capture that ledger-bearing pre-squash tip. The compact commit must have exact message
  `jmc7a: close runtime control and execution safety`, sole parent
  `jmc6k-complete^{}`, and a tree byte-identical to the captured recovery tip. Create the local
  annotated tag `jmc7a-complete`; do not edit this timeline afterward, push, activate schedules,
  touch operator data/media, or delete recovery material.
- **Recovery protocol:** use timestamp `20260723T212814Z` to create
  `recovery/jmc7a-20260723T212814Z`, annotated tag
  `recovery/jmc7a-pre-squash-20260723T212814Z`, and repository-external bundle
  `/tmp/marquee-jmc7a-20260723T212814Z.bundle`. Verify the bundle before rewrite and re-prove one
  primary worktree, clean status, exact base ancestry, configured author, no merge commits, and
  unpushed local range.
- **Exact JMC7B handoff:** begin only from verified annotated `jmc7a-complete`; re-check its compact
  tree/sole parent, the recovery refs and external bundle, Alembic `0014_jmc6k`, OpenAPI 191-path
  hashes recorded above, and the default-off schedule/GPU dispositions. JMC7A guarantees canonical
  cancellation, durable admission/recovery, definition-owned retry, and bounded canonical preview
  execution—JMC7B must consume those shared surfaces without special retry/process paths. JMC7B owns
  only survivor truth/neutral order, recoverable onboarding/deployment lineage, polarity-complete
  profiles, residual evidence/replacement evaluation, and consumer acknowledgement/readiness
  (7B-C1–C4 and 7B-H1). Activity races, generated contract closure, and final browser/screenshot
  work remain JMC7C; no JMC7A Activity snapshot was changed.
