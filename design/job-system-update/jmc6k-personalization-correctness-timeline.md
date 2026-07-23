# JMC6K Personalization and Onboarding Correctness Timeline

Shared execution record for the final JMC6K corrective plan. JMC6K is authoritative over
JMC6J wherever the documents differ. No push, schedule activation, operator database/media
mutation, or browser-agent acceptance is authorized by this work.

## K0 — compact-base verification and failing-contract freeze

### Exact starting state

- Implementation base: annotated `jmc6j-complete` at
  `704e93dff0626dfa087427a0a38e18b4c48fd1a6`, tree
  `4bdf7accfb706e2d69c9151a068221902698fc69`, configured-author commit
  `jmc6j: replace onboarding and add residual learning`. The recovery branch
  `recovery/jmc6j-20260723T004019Z` and annotated tag
  `recovery/jmc6j-pre-squash-20260723T004019Z` both resolve to pre-squash
  commit `5fa60cbe08aa147542d544683fb740b962dfd763`, whose tree is byte-identical
  to the compact base.
- The current planning-only successor is `10dc33d77979f5bcb82641700457559046767a33`
  (`chunk 6k planned`), tree `942f1cf13b6a36151d42f50b1bd41d9113c90f25`. Its
  three-file diff adds the JMC6K plan, its JMC6J correction note, and the design-timeline
  entry; it contains no application, test, migration, or frontend implementation change.
  JMC6K final-only compaction resets to the exact `jmc6j-complete` base, retaining this
  planning input in the tree-identical final result.
- Repository author and committer are configured as Gautam Chaudhri
  `<gautam.chaudhri@gmail.com>`; no other attribution is permitted. `job-manager` is one
  commit ahead of `origin/job-manager` at the planning commit, and the initial worktree was
  clean. No remote branch contains `HEAD`.
- The predecessor bundle formerly under `/tmp` had expired. It was reconstructed directly from
  the immutable JMC6J recovery refs at
  `/tmp/marquee-jmc6j-20260723T004019Z.bundle`; `git bundle verify` reports both recovery refs,
  complete SHA-1 history, and an okay bundle. This is a verified predecessor recovery repair,
  not a product change.

### Required source and tooling audit

- Read in full before edits: `AGENTS.md`, `CLAUDE.md`, `design/plans/README.md`, the JMC6K and
  JMC6J plans, the complete JMC6 runtime/personalization timeline, job-progress and Projection
  Room design documents, `design/poster-pipeline.md`, and `design/timeline.md`.
- Serena is operational and activated for `/forge/Marquee`; semantic analysis is required for
  source changes. RTK is operational and used for development and Git commands.
- ByteRover CLI is unavailable for this session: `brv query` repeatedly lost its daemon
  connection and `brv swarm query` reports no `.brv/swarm/config.yaml`; no ByteRover MCP
  fallback is exposed. This capability deviation is recorded here and does not alter locked
  design decisions. Serena project memories supplied the only available historical context.

### Owned verification environment

- Isolated PostgreSQL 18.3 cluster: data root `/tmp/marquee-jmc6k-pg.cxRux9`, listener
  `127.0.0.1:55472`, database `marquee_test`, owner `marquee`. It is the only database targeted
  by JMC6K verification. Pytest additionally creates/drops its own temporary schema and redirects
  `DATA_DIR` to a session-owned temporary root; no operator database, data directory, library,
  schedule, or media path was touched.
- `python -m marquee.db_migration` on that owned database upgraded cleanly through sole Alembic
  head `0013_jmc6j`; static `alembic heads` agrees. The first baseline attempt additionally set
  `DATA_DIR=/tmp/marquee-jmc6k-data` and correctly failed only
  `test_pipeline_run_root_is_inside_data`, whose contract intentionally asserts the repository
  default before pytest redirects it. The corrected baseline leaves `DATA_DIR` to the fixture and
  is the authoritative result below; no product source was changed.

### Current contracts and baseline fingerprints

- Active job definitions are `marquee.core.jobs.manifest.ENABLED_JOB_TYPES` (43):
  `audio_remove`, `audio_reorder`, `backup_create`, `dovi_analyze`, `dovi_convert`,
  `dovi_discard`, `dovi_publish`, `dovi_restore`, `job_retention_purge`, `letterbox_apply`,
  `letterbox_detect`, `letterbox_detect_episode`, `letterbox_detect_tv_scope`,
  `letterbox_reencode`, `letterbox_reencode_discard`, `letterbox_reencode_publish`,
  `letterbox_reencode_restore`, `letterbox_remove`, `library_sync`, `pipeline_cache_clear`,
  `poster_backup_subject`, `poster_deploy`, `poster_maintenance`, `poster_pipeline`,
  `poster_rescan`, `poster_reset`, `poster_restore`, `ranking_residual_train`, `subtitle_embed`,
  `subtitle_extract`, `subtitle_generate`, `subtitle_metadata`, `subtitle_policy`,
  `subtitle_policy_audit`, `subtitle_remove`, `subtitle_restore`, `subtitle_scan`,
  `system_metrics_purge`, `system_noop`, `taste_enrich`, `taste_map`, `taste_rebuild`, and
  `track_remove`.
- Existing onboarding source exposes only `status`, `start`, `choose`, and `complete`; its
  choose request still contains client presentation evidence. Taste preference scheduling still
  has single revision-level build state. The residual scorer/runtime and trainer expose distinct
  `ResidualScorer`, `select_scorer`, `ResidualArtifact.compatible`, and residual training paths.
  These are source-audited K0 targets, not closure evidence.
- Deterministic OpenAPI is current at 189 paths, SHA-256
  `9cce3b3dc8fe36c10dc17c00e20862c580e30e9a30e329483be55440e102fffe`; generated TypeScript is
  SHA-256 `d6624d4b17fc427bb7d83c7f6651ed17661510a8a4dcd56e7e2c3c2e85eb91ff`.
- Retained backend baseline on the owned database: **1353 passed, 3 skipped, 1 inherited Paddle
  GPU-device warning** in 113.57s. The three skips are exactly the default-disabled opt-in
  `MARQUEE_LIVE_SMOKE=1` poster/taste nodes; they are not execution evidence. `ruff check marquee
  tests` passes and `git diff --check`/worktree are clean.
- Generated-contract checks pass (`api:check`, `api:generate:check`). Frontend baseline passes:
  Svelte check 0 errors/0 warnings; Prettier/ESLint clean; Vitest **112/112**; production build
  green (only inherited plugin-timing notice); hermetic Chromium Playwright/axe **11/11**. Vitest
  prints its pre-existing Svelte-config discovery notice; Playwright prints inherited Node
  `NO_COLOR`/`FORCE_COLOR` and plugin-timing warnings.
- Live capability behavior at K0: defaults skip the one poster and two taste smokes. JMC6K must
  later invoke all three with `MARQUEE_LIVE_SMOKE=1` against owned fixtures and certify CPU OCR;
  GPU availability remains a separately truthful capability result.

### K0 status

- **Current phase:** K0 in progress.
- **Exact next actions:** re-read and semantically inventory the canonical `PipelineRun` archive,
  candidate artifact registration, onboarding choose/deploy activation, taste revision/publication
  and retry lineage, readiness projection, residual runtime/evaluation calls, and frontend caller
  graph; then add production-path intentional-red contracts for K-A1 through K-T1. Verify that
  archived evidence can reconstruct opaque candidate membership/order before implementation. Freeze
  deterministic candidate/features and continue only if the K0 stop gates remain satisfied.
- **Deviations:** predecessor external bundle reconstructed after `/tmp` expiry; ByteRover CLI/MCP
  capability unavailable; test baseline uses pytest's owned data root rather than a global
  `DATA_DIR` override because the existing isolation fixture requires that contract. None changes
  product architecture or safety rules.
- **Pending operator actions:** none. Browser-agent acceptance, real-library/media work, remote
  push, and activation remain explicitly out of scope.

### K0 committed checkpoint

- Completed and committed as `f1161ac` (`capture personalization closure regressions`). The commit
  adds this continuity ledger and nine intentional-red production-contract tests covering K-A1/A2/A3,
  K-P1/P3, K-R1/R2, K-L1/L2, and K-T1. Candidate evidence was verified sufficient: an immutable
  `PipelineRun` points to a bounded verified archive and review-candidate `JobArtifact` identities
  are frozen into the archive before it is registered.
- Focused result: **9 intentional failures**, each matching its asserted gap. Full result:
  **1353 passed, 9 intentional failures, 3 default-disabled live skips, 1 inherited Paddle warning**
  in 111.66s. Ruff and `git diff --check` pass. No unrelated failure was introduced.
- **Current phase:** K1 — canonical onboarding review and decisions.
- **Exact next actions:** replace the choose request with intent-only review-bound selection; add a
  bounded review resource derived from the verified archive; reconstruct candidate identity/order
  server-side; implement idempotent hate without deployment; create the atomic selection/deploy/event
  projection; correct recovery links; regenerate OpenAPI/types and migrate the frontend helper.

### K1 committed checkpoint

- Completed and committed as `107d8ed` (`bind onboarding decisions to canonical review evidence`).
  `GET /api/onboarding/runs/{run_id}/review` now returns a neutral, bounded projection rebuilt from
  the verified archived `PipelineRun`; every displayed candidate re-validates its registered artifact
  identity, immutable checksum/storage key, metadata, and physical bytes. `choose` and `hate` accept
  only opaque candidate intent plus the review revision and idempotency key. The server locks the run,
  reconstructs exposure/order/evidence, rejects stale or replay-conflicting decisions, and records
  the canonical event. Choose remains a normal fenced `poster_deploy` projection; hate retains a
  pinned active negative exemplar with no deployment job or invented deployment result.
- The onboarding Activity link is now the canonical Projection Room queue location. The frontend
  helper and generated OpenAPI contract expose review/choose/hate while leaving K4 visual work for
  the later phase. OpenAPI is now 191 paths, SHA-256
  `7c6cfb854b66e38dc22c34f09d7fe9646e8ee1bdc3205fd6fc67ad52115385a6`; generated TypeScript is
  SHA-256 `133420fe30c23e517bf43686d4c4bc7ecaf61ff3afe29b31778c5e26fde0fa2d`.
- Focused K1 backend coverage: the K1 review/hate, prior cold-start, and taste-authority tests pass
  (16 passed); this includes physical-artifact verification and idempotent hate replay. Retained full
  backend result: **1356 passed, 7 expected later-phase contract failures, 3 default-disabled live
  skips, 1 inherited Paddle warning** in 111.07s. The remaining seven failures are exactly K2/K3/K4:
  explicit profile generation, durable per-library lineage/readiness, residual runtime/evaluation/
  partition contracts, and the future browser journey. Ruff and `git diff --check` pass.
- Generated contract checks pass. Frontend K1 gates: Svelte check 0 errors/0 warnings; Prettier/
  ESLint clean; Vitest **112/112**; build green; Chromium Playwright/axe **11/11**. The same
  inherited config/plugin/Node notices from K0 remain; no new warnings, skips, or xfails were added.
- **Current phase:** K2 — profile-build coordination and readiness.
- **Exact next actions:** add the per-library durable coordinator/build lineage and migration;
  serialize movie and TV builds independently with database-enforced in-flight uniqueness, explicit
  generations, retry-as-successor, coalescing, publication/reload observation, and readiness facts;
  migrate all callers and certify migration/model parity.
- **Deviations:** none beyond the recorded ByteRover and owned-fixture capability deviations.
- **Pending operator actions:** none.

### K5 committed checkpoint

- Completed and committed as `3f43ada` (`certify personalization and onboarding closure`). The
  active closure fixtures now identify the native, loader-validated bounded residual as the
  personalization artifact and `POST /api/taste/residual/retrain` as its producer. The retained
  JMC6H/JMC6I inventory files are explicitly historical/superseded rather than current
  personalization authority; their former learned-head/rank-test language was removed from active
  source and is guarded by the JMC6K contract freeze.
- Current-contract focused coverage passes (**32 passed**), including the retirement freeze,
  historical-manifest wording, and executable certification checks. The three opt-in CPU live
  capabilities—one poster and two taste smokes—pass with `MARQUEE_LIVE_SMOKE=1` against the
  JMC6K-owned PostgreSQL fixture (**3 passed**). The initial unprivileged probe was blocked by the
  sandbox socket policy and was immediately rerun with the approved isolated-cluster capability;
  it was not a product failure or capability exception.
- Final backend certification: **1373 passed, 3 default-disabled live skips, 1 inherited Paddle
  GPU-device warning** in 113.51s. `ruff check marquee tests`, `git diff --check`, `alembic
  check`, and `alembic heads/current` pass; the sole/current head remains `0014_jmc6k`. Frontend
  certification: Svelte check 0 errors/0 warnings; Prettier/ESLint clean; Vitest **112/112**;
  production build; API check at **191 paths**; generated-contract check; Chromium
  Playwright/axe **14/14**. Current deterministic fingerprints are OpenAPI
  `35c3c15e2fc95a71117845ff4a08cf8d3fe58d5d87f7ad7ec69d876f7499d423` and generated TypeScript
  `2ab6f9c156e01f4ef179ca75e1f5e68c8b8f421e94d9197f3efdea73edc7aef2`.
- ByteRover MCP queued the K5 audit (`cur-1784780921591`). The owned-cluster deviation remains
  deliberate: the test role cannot create disposable databases, so only the JMC6K-owned cluster
  and pytest-owned temporary data root were used. No operator database, library, media path,
  schedule, activation, remote push, or live browser-agent session was touched.
- **Current phase:** final history certification and compaction only.
- **Exact next actions:** verify the linear configured-author range from `jmc6j-complete`, record
  the pre-squash recovery metadata, create a timestamped local recovery branch/tag and verified
  repository-external bundle, soft-reset only to the recorded base, make the sole closure commit,
  verify an identical tree/sole parent/clean worktree, and create local annotated
  `jmc6k-complete`. No timeline edit follows the tag.
- **Pending operator actions:** none. Browser-agent acceptance against a real library, operator
  media/GPU work, schedule activation, and remote push remain out of scope.

### Final pre-squash certification record

- **Recorded base:** `jmc6j-complete` = `704e93dff0626dfa087427a0a38e18b4c48f...`.
  The unpushed range is linear with no merge commits and has one configured author only, Gautam
  Chaudhri `<gautam.chaudhri@gmail.com>`. Its JMC6K plan/phase commits are `10dc33d`, K0
  `f1161ac`, K1 `107d8ed`, K2 `ce1680a`, K3 `01dbf29`, K4 `e9bda88`, and K5 `3f43ada`; no
  unrelated or concurrent commit is present.
- **Certified lifecycle evidence:** canonical archive-bound onboarding review, intent-only choose
  and idempotent hate, database-coordinated movie/TV profile builds, generation-aware residual
  runtime/evaluation, and recoverable bounded browser onboarding are all covered by the phase
  contracts. Active historical fixtures describe the residual authority rather than a retired
  learned head. The final matrix is the K5 backend/frontend/migration/live-smoke evidence above;
  generated fingerprints and sole migration head are also recorded above.
- **Safety/accounting:** all checks used the JMC6K-owned PostgreSQL cluster and pytest-owned data
  roots. The three opt-in live capabilities passed on CPU; no green result was excused. The sole
  inherited Paddle warning and known test-role disposable-database limitation are recorded above.
  No operator action is pending; no production service, user data, media, schedule, activation,
  remote, or external system was changed.
- **Compaction record:** immediately after this commit, its exact pre-squash tip and tree are
  preserved in a timestamped local recovery branch, annotated recovery tag, and verified external
  Git bundle before the authorized soft reset. The certified tree must be reproduced exactly by
  the sole-parent configured-author commit `jmc6k: close personalization and onboarding
  correctness`; the intended immutable local tag is `jmc6k-complete`. This is the final timeline
  edit.

### K4 committed checkpoint

- Completed and committed as `e9bda88` (`complete recoverable taste onboarding`). The onboarding
  status snapshot now carries the latest unreviewed, terminal movie `PipelineRun` initiated by the
  canonical onboarding route. Start, choose/hate, and profile-build responses return that same full
  recovery snapshot, so refresh and navigation return to bounded candidate review rather than using
  generic job detail as the happy path.
- `/onboarding` now owns the complete neutral review journey: it keeps analysis/deployment/build
  progress in the shared Activity store, loads review cards from the server-owned archive, sends
  only decision intent, renders source/OCR/eligibility facts without rank/score/recommendation
  affordances, records choose or hate outcomes explicitly, and exposes direct canonical Activity,
  detail, and build-remediation links. Movie and TV publication rows remain separate; the client
  cancels obsolete refreshes and discards stale responses.
- Hermetic Chromium coverage adds keyboard choose intent-body verification, hate-without-deployment,
  review-only axe scanning, and narrow mobile controls. The synthetic backend contains only bounded
  canonical response shapes and never contacts an operator service, media path, or library.
- K4 focused backend contract: **14 passed**. Final full backend: **1372 passed, 3
  default-disabled live skips, 1 inherited Paddle GPU-device warning** in 121.06s. Full frontend:
  Svelte check 0 errors/0 warnings; Prettier/ESLint clean; Vitest **112/112**; production build;
  API and generated-contract checks; Chromium Playwright/axe **14/14**. Full `ruff check marquee
  tests`, `alembic check`, and `git diff --check` pass; Alembic remains sole head `0014_jmc6k`.
- ByteRover MCP recorded the recovery/UI architecture (`cur-1784780186625`); Serena remained active
  for code analysis and every shell/Git command used RTK.
- **Current phase:** K5 — final retirement and certification.
- **Exact next actions:** audit and remove stale learned-head/rank-test assumptions and unreachable
  onboarding aliases; run the complete current-contract/lifecycle matrix from the owned fixture;
  update the closure manifest and timeline; then perform only the prescribed final history
  compaction and local `jmc6k-complete` tag after a clean certified tree.
- **Pending operator actions:** none. Remote push, browser-agent acceptance against a real library,
  operator media/GPU work, and schedule activation remain out of scope.

### K2 committed checkpoint

- Completed and committed as `ce1680a` (`coordinate taste profile revisions`). Migration
  `0014_jmc6k_profile_build_coordination` adds `taste_profile_builds` and
  `taste_profile_coordinators`, including a database-enforced one-in-flight build constraint per
  `movies`/`tv` library. Coordinator submissions freeze canonical active evidence, revision,
  profile-build identity, and expected active-publication generation under a transaction advisory
  lock. New evidence coalesces while a build is in flight; terminal failure, cancellation,
  supersession, retry, and stale-generation handling preserve bounded successor lineage rather
  than duplicating or reviving old work.
- `POST /api/taste/retrain` now accepts only the canonical-revision source and delegates to the
  same coordinator as initial, continued, repair, and retry flows. Legacy filesystem-sourced
  manual training is no longer an authority. The status/readiness projection exposes the desired
  revision, in-flight or terminal build, active generation, and update-attention facts for each
  library. OpenAPI and generated frontend types were refreshed; historical inventory freezes now
  correctly distinguish coordinator-produced `taste_rebuild` work from directly constructed route
  jobs.
- Executable certification fixtures now use physically retained, checksum- and size-validated
  canonical taste evidence, proving that the manual route and downstream map/enrichment consumers
  use the production authority path. Migration upgrade on the owned PostgreSQL cluster at
  `/tmp/marquee-jmc6k-pg.5mYRCX` (`127.0.0.1:55473`) reaches sole head `0014_jmc6k`; `alembic
  check` reports no model drift. The shared test role could not create disposable databases, so
  this cluster is JMC6K-owned test infrastructure only; no operator database or media path was
  touched.
- K2 focused migration, inventory, coordinator, route, and executable-certification suites pass
  (**46 passed** before the final inventory-freeze correction; **40 passed** in the final focused
  contract run). `ruff check marquee tests`, `git diff --check`, `api:check`, and
  `api:generate:check` pass. Frontend K2 gates are green: Svelte check 0 errors/0 warnings;
  Prettier/ESLint clean; Vitest **112/112**; production build; Chromium Playwright/axe
  **11/11**. The inherited Svelte-config, plugin-timing, and Node color notices remain non-failing.
- Final K2 full backend baseline: **1364 passed, 4 intentional K3/K4 contract failures, 3
  default-disabled live skips, 1 inherited Paddle GPU-device warning** in 122.25s. The four
  remaining contracts are exactly residual runtime context, shared residual candidate scoring,
  three-way residual partitions, and the future onboarding browser journey; no K2 regression
  remains.
- ByteRover CLI remains unavailable, but the ByteRover MCP fallback was available at K2 and the
  coordinator architecture was queued for curation (`cur-1784778193992`). Serena remained active
  for semantic analysis and every shell/Git command used RTK.
- **Current phase:** K3 — residual-runtime correctness.
- **Exact next actions:** remove the global/profile-implicit residual scorer path; introduce the
  explicit runtime profile identity and shared per-candidate primitive; make trainer evaluation
  use that same primitive with deterministic group-aware train/validation/test partitions;
  regenerate contracts and certify evaluation does not mutate active publication state.
- **Pending operator actions:** none. Browser-agent acceptance, real-library/media work, remote
  push, schedule activation, and production residual publication remain explicitly out of scope.

### K3 committed checkpoint

- Completed and committed as `01dbf29` (`align residual evaluation with deployed scoring`). Active
  residual scoring now requires an explicit runtime context: library, weighted-baseline signature,
  profile checksum and generation, and staged artifact identity. The runtime verifies staged bytes
  against the manifest before loading. Auto mode records a typed incompatibility and uses the
  bit-identical weighted scorer; forced-residual mode returns a typed compatibility failure.
- `score_residual_candidate` is the sole deployed and held-out evaluation primitive: baseline
  probability is clamped before logit conversion, the linear residual plus bias is clamped once per
  candidate, alpha is applied in logit space, and pair outcomes compare final candidate logits.
  Training uses deterministic subject-disjoint train/validation/test partitions, gates activation
  from validation only, and persists separate partition metrics. Residual artifact/profile lineage
  now includes profile generation in both immutable artifact and publication metadata.
- Readiness exposes per-library residual compatibility/dormancy. A profile change makes an
  mismatched residual dormant without deleting or mutating its immutable artifact; regular
  evidence-thresholded feedback successor scheduling can subsequently create a compatible
  replacement.
- K3 focused residual/runtime/publication/readiness suite: **40 passed, 1 K4 browser sentinel
  deselected**; `ruff check` and `git diff --check` pass. `alembic check` reports no model drift;
  the owned test cluster remains at sole head `0014_jmc6k`.
- Full backend: **1370 passed, 1 intentional K4 browser-contract failure, 3 default-disabled live
  skips, 1 inherited Paddle GPU-device warning** in 117.03s. No K3 failure remains. Frontend gates:
  Svelte check 0 errors/0 warnings; Prettier/ESLint clean; Vitest **112/112**; production build;
  API check and generated contract check; Chromium Playwright/axe **11/11**.
- ByteRover MCP recorded the K3 architecture (`cur-1784779375917`); the unavailable CLI remains
  documented. Serena remained active for code analysis and every shell/Git command used RTK.
- **Current phase:** K4 — bounded onboarding browser journey.
- **Exact next actions:** implement the real onboarding route over the review/choose/hate API,
  including neutral evidence presentation, keyboard/axe/mobile coverage, Activity/job outcome
  navigation, and network stubbing that proves no hidden retrieval or direct detail mutation.
- **Pending operator actions:** none.
