# JMC5 Destructive Media Timeline

Shared implementer log for JMC5A → JMC5B → JMC5C. Append after every phase commit.

## Initial state and prerequisite stop — 2026-07-14 (JMC5A Phase A0)

- Exact JMC5A plan base: `cadfdb423b09a5d1e3f9b2270ca147bb080c2723`
  (`chunk 5 planned`), tree `dfffb157cdb75babfc5563124a333f2c08f24832`, sole parent
  `0eded400fceb01438641baf4da37ae436e998921` (`jmc4c-complete`). Branch is
  `job-manager`, tracking `origin/job-manager`. The working tree was clean before this timeline
  was created.
- Configured repository author is
  `Gautam Chaudhri <gautam.chaudhri@gmail.com>`; the plan-base, compact JMC4C commit, and tags use
  that configured author only.
- Required Serena MCP is available, its instructions were read, and project `Marquee` is active.
  Required ByteRover MCP is available and was queried. RTK proxying is available and was used for
  development and Git commands. The optional local `brv` CLI/swarm helper is not installed
  (`No such file or directory`); this does not constitute a ByteRover MCP outage.

## Proven JMC4C predecessor state

- Annotated tag `jmc4c-complete` peels to compact commit
  `0eded400fceb01438641baf4da37ae436e998921`
  (`jmc4c: complete nonmutating job migration`), tree
  `0017f68aed4488b5f7c9dea1184239fe0b0bf15e`, with sole parent
  `0dd6b9ad4e5a26cc73513c9fcacc53472ec5352f` (`jmc4b-complete`). Commit, tagger, author, and
  committer are the configured repository user.
- Recovery branch `recovery/jmc4c-20260714T232433Z` and annotated tag
  `recovery/jmc4c-pre-squash-20260714T232433Z` resolve to pre-squash commit
  `2ec9cf986425242069fe33e5e7c4086d65b7e520`; its tree and the compact tree are identical at
  `0017f68aed4488b5f7c9dea1184239fe0b0bf15e`.
- External bundle
  `/home/quartermaster/backups/Marquee/marquee-jmc4c-pre-squash-20260714T232433Z.bundle`
  exists and `git bundle verify` reports complete history with the JMC4C pre-squash branch and
  recovery refs.
- Enabled leaf/executor manifest is exactly `dovi_analyze`, `learned_head_train`,
  `letterbox_detect`, `letterbox_detect_episode`, `letterbox_detect_tv_scope`, `library_sync`,
  `poster_pipeline`, `poster_rescan`, `subtitle_policy_audit`, `subtitle_scan`, `system_noop`,
  `taste_map`, and `taste_rebuild`. Activated schedule keys are exactly `library-sync` and
  `audio-subs-deep-scan`.
- Deferred JMC4C mutation/operator set is exactly `audio_remove`, `audio_reorder`,
  `backup_create`, `dovi_convert`, `job_retention_purge`, `letterbox_apply`,
  `letterbox_apply_tv_scope`, `letterbox_heal`, `letterbox_reencode`, `letterbox_remove`,
  `letterbox_revert_tv_scope`, `pipeline_cache_clear`, `poster_backup_all`,
  `poster_deploy_reset`, `poster_heal`, `poster_maintenance`, `radarr_upgrade`,
  `subtitle_embed`, `subtitle_extract`, `subtitle_generate`, `subtitle_metadata`,
  `subtitle_policy`, `subtitle_remove`, `subtitle_restore`, `system_metrics_purge`, and
  `track_remove`. Source certification still proves these definitions are dispatch-disabled and
  no enabled definition uses `media_write`.

## Stop gate

- **JMC5A Phase A0 is blocked before implementation.** The required JMC4C post-rewrite focused
  smoke cannot be proven from the mandated JMC4 timeline. Its final entry is explicitly the
  pre-squash certification and says the timeline will not be edited after compaction. The file
  ends by stating that post-rewrite smoke must be verified, but it records no command, result,
  timestamp, or pass/fail evidence after the rewrite. The `jmc4c-complete` annotation contains
  only `JMC4C complete nonmutating job migration`; no Git note or later verification record exists.
- Per the JMC5A prerequisite gate and kickoff instruction, no runtime behavior, tests, schemas,
  generated contracts, frontend files, or mutation-writer inventories were changed or baselined.
  No JMC5A implementation commit was created.
- Exact unblock condition: provide an authoritative existing record that proves the JMC4C
  post-rewrite focused smoke, or explicitly authorize a documented prerequisite repair procedure
  that does not falsify or rewrite the locked JMC4 timeline. After that proof is available,
  re-verify the clean initial state and resume Phase A0 with tool versions, owned disposable
  PostgreSQL target, schema/OpenAPI/frontend/full-suite baselines, mutation-writer inventory,
  contract-freeze tests, and the exact next phase steps.

## Authorized prerequisite repair and unblock — 2026-07-14 (JMC5A Phase A0)

- The user explicitly authorized a documented prerequisite-repair procedure that preserves the
  locked JMC4 timeline. No JMC4 commit, tag annotation, tree, recovery ref, or bundle was changed.
  The repair uses the default Git notes ref plus this JMC5 timeline, so the original stop and its
  resolution remain auditable without falsifying pre-squash chronology.
- A detached worktree was created at the exact annotated `jmc4c-complete` commit
  `0eded400fceb01438641baf4da37ae436e998921`; `HEAD^{tree}` remained
  `0017f68aed4488b5f7c9dea1184239fe0b0bf15e`. At `2026-07-15T04:22:25Z`, the owned disposable
  PostgreSQL command
  `DB_URL=postgresql+asyncpg://marquee@127.0.0.1:55446/marquee_test
  /forge/Marquee/.venv/bin/pytest -q tests/test_jmc4c_certification.py
  tests/test_jmc4c_ml_rescan.py tests/test_jmc4c_poster_pipeline.py
  tests/test_jmc4c_poster_workspace.py` completed with **21 passed in 2.10s**, exit status 0.
- A preceding setup attempt omitted `DB_URL`, reached no test bodies, and produced 21 fixture
  setup errors while resolving the unavailable Docker hostname `postgres`; it is not counted as a
  product or certification result. Supplying the already-owned JMC4C disposable target resolved
  the environment error without a code, schema, fixture, or database-role change.
- The successful exact-tree command, target commit/tree, UTC timestamp, and result are attached to
  compact commit `0eded400fceb01438641baf4da37ae436e998921` under `refs/notes/commits` as
  `JMC4C post-rewrite focused smoke verification`. `git notes show` reproduces the evidence.
- The missing durable post-rewrite proof is therefore repaired and the prerequisite stop is
  cleared. JMC5A Phase A0 may resume from the recorded plan base after its remaining baseline,
  disposable-environment, inventory, and contract-freeze gates run. No JMC5 runtime implementation
  was performed as part of this repair.

## Pending operator work

- All operator/live-model/native-tool items from the JMC4 final handoff remain pending, including
  controlled DOVI deep-analysis and OCR/CLIP/DINO smokes. No operator database, normal `DATA_DIR`,
  media library, real backup set, external service, or remote ref was touched.

## Phase A0 complete — 2026-07-14

- Re-verified the prerequisite repair commit as
  `932532fa28cf857027dc8693d194c7529af82b1c` with sole parent
  `cadfdb423b09a5d1e3f9b2270ca147bb080c2723`. The default Git note on compact JMC4C is blob
  `a8a8e23a78c7f0262abac6f25a0f196eca9a6334`; compact commit, tree, parent, annotated tag,
  recovery refs, and verified external bundle remain exactly as recorded above.
- Owned disposable PostgreSQL is `/tmp/marquee-jmc5a-pg/data`, listening only for this work on
  `127.0.0.1:55447`, role `marquee`, database `marquee_test`. Guarded reset upgraded through sole
  Alembic head `0006_jmc4c`. No operator database, media root, normal `DATA_DIR`, or backup set was
  opened or mutated.
- Tool baseline: Python `3.13.14`, Node `22.22.2`, pytest `9.0.3`, Ruff `0.15.17`, Alembic head
  `0006_jmc4c`, ffmpeg/ffprobe `8.1.2`, Git `2.55.0`, and RTK `0.42.4`. `pg_config` is not on the
  configured path; PostgreSQL utilities used for the owned instance are the system binaries under
  `/usr/bin`. Required Serena and ByteRover MCP remained available. The optional local ByteRover
  CLI/swarm helper remains unavailable (`No such file or directory`).
- Baseline before the A0 freeze was **1124 passed, 21 failed, 2 warnings in 64.30s**. The failures
  are the exact retained JMC4C set: nine development OCR-label cases, TV development reset,
  effective OCR hardware policy, four run endpoints, two sync reset cases, system metrics,
  two taste-artifact cases, and the Whisper catalog verdict. Ruff passed. Alembic reported no new
  upgrade operations. OpenAPI was current at 197 paths and generated TypeScript was unchanged.
  Frontend check reported 0 errors and the inherited 16 warnings in 8 files; lint and build passed.
- Frozen mutation authority includes exact enabled/executor, registered-handler,
  route-constructed, and schedule-produced manifests; typed state for all twelve JMC5A targets and
  seventeen deferred JMC5B/JMC5C types; direct poster/backup/deletion and legacy lifecycle call
  sites across eleven source files; and the nine existing backup/heal/maintenance/poster test
  modules. The four new A0 tests are in `tests/test_jmc5a_contract_freeze.py` with their fixture at
  `tests/fixtures/jmc5a/a0_contract_freeze.json`. No runtime behavior changed.
- Phase commit: `058fa508965f8e91568cac8ce227b250af60ecff` (`freeze jmc5a mutation inventory`), authored
  by the configured repository user. Focused result: **4 passed in 0.53s**. Complete retained result:
  **1128 passed, 21 failed, 2 warnings in 65.48s**, exactly the baseline plus four passing freeze
  tests. Full Ruff, Alembic/model drift, deterministic OpenAPI/TypeScript, frontend check/lint/build,
  and `git diff --check` gates passed; the same frontend 16 warnings remain.
- Current phase is **A1 — common mutation documents and coordinator protocol**. Exact next work:
  add the shared typed mutation vocabulary and family adapters; validate canonical
  `MediaOperationDetail` evidence on persistence and presentation; introduce coordinator-facing
  target/snapshot/validation/backup/publication helpers; prove no-change, partial-group,
  all-or-nothing, stale-fence, and uncertain-publication semantics; then update the A0 freeze in
  the same phase commit. No A2 route or artwork writer migration starts before A1 passes.
- Deviation: the initial unqualified focused pytest invocation could not open a sandbox socket;
  it reached no test body. The qualified rerun used only the owned disposable target and passed.
  There are no safety, contract, schema, or runtime deviations.

## Pending operator work after A0

- All JMC4 live-model/native-tool smokes remain pending. JMC5A live artwork, backup, and scheduler
  smokes are also pending until their corresponding phases exist and synthetic certification is
  complete. No push or remote mutation is authorized.

## Phase A1 complete — 2026-07-14

- Added the shared path-free `MutationTargetV1`, target outcome, snapshot, validation, atomicity,
  backup, publish, result, error, and evidence documents. Target status is restricted to
  `succeeded`, `failed`, `skipped`, or `not_applied`; `no_change` is a reasoned job outcome.
  Model invariants reject changed unsuccessful targets, incomplete target accounting,
  non-specific no-change, applied targets after an all-or-nothing failure, and any uncertain
  publication not represented as an `unsafe` outcome.
- Added the coordinator-facing ordered protocol plus the final fence/cancellation/source/
  destination precondition guard and crash-evidence reconciliation classifier. A durable publish
  intent without sealed publication evidence is classified `required` reconciliation, never
  retry-safe success or failure.
- Added the sole typed `MediaOperationDetail` persistence/read boundary. It writes the seven
  canonical JSON documents together and strictly revalidates them on presenter/API read. The job
  presentation query now loads optional 1:1 mutation evidence; valid evidence yields bounded
  validation/atomicity/publication facts, while malformed evidence is omitted with a safe warning.
- Updated definition validation for Chunk 5: an enabled unsafe leaf must use family-specific
  request/result documents, a safety-valid retry policy, and either the media-write gate or the
  exclusive maintenance barrier. Read-only definitions remain forbidden from claiming
  `media_write`. No mutation definition was enabled in A1.
- Phase commit: `fafb3ff75d0c62810794f17ccebec930714a5cde` (`add canonical mutation contracts`),
  authored by the configured repository user. Focused mutation/publication/presenter/read API/
  freeze/definition result: **56 passed in 1.35s**. Complete retained result: **1135 passed,
  21 failed, 2 warnings in 62.19s**; the 21 failures are the exact recorded JMC4C set and the
  increase is only seven new passing A1 tests. Full Ruff, Alembic/model drift, deterministic
  197-path OpenAPI/TypeScript, frontend check/lint/build, and `git diff --check` gates passed.
  Frontend remains at 0 errors and the inherited 16 warnings in 8 files.
- Current phase is **A2 — poster deploy, restore, and reset**. Exact next work: add typed leaf
  request/result adapters and certified definitions/handlers; resolve only server-owned
  JMC4 artifact/storage keys; stage and decode-validate images under confined synthetic roots;
  create recoverable evidence before replacement/deletion; publish through the JMC3 coordinator;
  persist artwork/event/live-state/evidence coherently; then convert feedback, television, and
  single-subject reset routes to canonical asynchronous submission. Webhook restore remains
  disabled and is not migrated.
- Deviations: the first expanded focused command named a nonexistent test module and collected no
  tests; the corrected command passed. Serena diagnostics use a host interpreter that cannot
  resolve the project virtualenv packages, so its import-resolution warnings are not product
  diagnostics; Serena symbol/reference analysis and the executable Ruff/pytest gates remained
  authoritative. No architecture or safety deviation exists.

## Pending operator work after A1

- The prior JMC4 operator smokes remain pending. Poster mutation live smokes are deferred until A2
  synthetic movie/series/season certification passes. No operator content or remote ref was
  touched.

## Phase A2 complete — 2026-07-14

- Added family-specific poster deploy, restore, and reset requests/results/errors plus the
  server-owned candidate and recorded-artwork selection snapshots. Enabled exactly those three
  `media_write` leaves, registered their PgQueuer execution handlers and configured concurrency,
  and retained `poster_backup_subject` as the typed disabled A3 child definition.
- Added the confined poster mutation coordinator. It independently classifies candidate, backup,
  staging, live destination, and recorded restore sources; decodes and checksums images; creates a
  recoverable backup before replacement/deletion; performs the final fence, cancellation,
  signature, filename, and destination checks; publishes only through the JMC3 coordinator; and
  coherently records live artwork projection, `ArtworkEvent`, typed `MediaOperationDetail`, and
  canonical result evidence. Movie, series, and season success/no-change/reset/restore plus
  poisoned stored paths are covered using synthetic roots only.
- Converted feedback deploy, movie/TV poster deletion, and television use-show-poster writes to
  canonical asynchronous leaf submission. Bulk movie/TV auto-approval requires an explicit root
  `Idempotency-Key`, derives bounded per-run keys, returns 202 when deployment is requested, and
  exposes canonical deployment summaries without leaking raw exceptions. Frontend calls and the
  deterministic OpenAPI/TypeScript contracts were updated together.
- Hardened the shared fenced writer so a durable mutation publish intent without sealed
  publication evidence terminates `unsafe` with typed uncertain atomicity. Retry is refused and
  quarantined whenever mutation publication cannot be proven absent. Focused tests exercise both
  failure and requested-retry crash paths.
- Phase commit: `3a3002e65f91681565f2d7fd68d913b1420d684a`
  (`add-canonical-poster-mutations`), authored by the configured repository user. Final focused
  poster/quarantine/route/freeze result: **16 passed in 1.80s**; the broader A2 route and mutation
  gates also passed. Complete retained result: **1145 passed, 21 failed, 2 warnings in 65.53s**;
  the failures are the exact recorded JMC4C set and all A2 additions pass. Full Ruff,
  Alembic/model drift, deterministic 197-path OpenAPI/TypeScript, frontend check (0 errors and the
  inherited 16 warnings in 8 files), lint, build, and `git diff --check` gates passed.
- Current phase is **A3 — poster parents, healing, and schedule**. Exact next work: implement sealed
  reset, backup, and heal parent discovery; enable and certify `poster_backup_subject`; aggregate
  isolated child results; add code-owned heal scheduling only after uniqueness/coalescing and
  overlap proofs; migrate the remaining poster bulk routes; and verify zero-child, cancellation,
  retirement, partial-failure, pagination, and restart behavior.
- Deviations: no architecture or safety deviation exists. RTK's argument proxy stripped quoted
  commit-message whitespace, so the phase subject uses hyphens while remaining a short lowercase
  configured-author summary. Serena's host interpreter still cannot resolve virtualenv imports;
  its actionable optional-value diagnostics were fixed and executable Ruff/pytest remained the
  authoritative gates.

## Pending operator work after A2

- Controlled live artwork deploy/restore/reset remains pending for operator-owned content; A2
  certification used only the owned disposable PostgreSQL database and confined synthetic roots.
  All prior JMC4 native-model smokes remain pending. No remote ref was touched.

## Phase A3 complete — 2026-07-14

- Converted `poster_deploy_reset`, `poster_backup_all`, and `poster_heal` into fixed, sealed,
  ticketless control parents. Bounded discovery snapshots movie, series, and season subjects before
  submission; reset, backup, and heal create isolated `poster_reset`, `poster_backup_subject`, and
  `poster_restore` children respectively. Empty scopes terminate `no_change`, while normal batch
  projection itemizes partial failure, cancellation, and unsupported or unchanged heal subjects.
- Enabled exactly the typed `poster_backup_subject` media-write leaf. It confines the canonical
  destination and recoverable storage, copies through the fenced mutation coordinator, records
  source/backup checksums and byte counts, and returns a truthful no-change result when the source
  is absent or an identical backup already exists. Removed the three legacy monolithic parent
  handlers and the inline heal scan.
- Migrated `/api/pipeline/backup-all`, `/api/pipeline/posters/reset`, and `/api/system/heal` to
  require `Idempotency-Key`, submit the canonical fixed parent, and return 202 canonical job links.
  Frontend consumers and deterministic OpenAPI/TypeScript contracts were updated together.
- Activated the code-owned poster-heal schedule with its own versioned enable/interval inputs.
  The callback serializes active-parent discovery with a transaction advisory lock, reuses an
  exact occurrence before rediscovery, coalesces overlapping buckets, and only constructs the
  sealed parent. Concurrent different-bucket, restart, disable/re-enable, and interval tests pass.
- Phase implementation commit: `edcafc26ec5f98cb2056e312634af8dba0a15698`
  (`add-sealed-poster-parent-workflows`), tree
  `b6825427909c2a4f609e8e682c936b51b18e1315`, sole parent
  `4fac94351d944e7f79f9f903c93099a38e999a74`, authored by the configured repository user.
  Final focused A3 gate: **46 passed**. Complete retained result: **1153 passed, 21 failed,
  2 warnings in 67.82s**; the failures are the exact recorded JMC4C set. Full Ruff,
  Alembic/model drift, deterministic 197-path OpenAPI/TypeScript, frontend check (0 errors and the
  inherited 16 warnings in 8 files), lint, build, and `git diff --check` gates passed.
- Current phase is **A4 — backup creation and destructive maintenance**. Exact next work: move
  `backup_create` behind canonical maintenance execution and exclusive maintenance safety;
  implement typed, bounded `poster_maintenance`, `pipeline_cache_clear`, `job_retention_purge`,
  and `system_metrics_purge` handlers; convert their public routes to asynchronous canonical
  submission; preserve offline-only restore; and prove dry-run plan equality, reference
  preservation, confinement, bounded transactions, idempotency, and cancellation between batches.
- Deviations: no architecture or safety deviation exists. Serena remained available for symbol
  analysis; its host interpreter reports only environment-level unresolved virtualenv imports.
  ByteRover A3 curation was queued as `cur-1784095695006`.

## Pending operator work after A3

- Controlled live poster backup/heal/reset smokes remain pending for operator-owned content; all
  A3 verification used only the owned disposable PostgreSQL database and confined synthetic media
  roots. Prior JMC4 native-model smokes remain pending. No remote ref was touched.

## Phase A4 complete — 2026-07-14

- Enabled exactly the five typed exclusive-maintenance leaves: `backup_create`,
  `poster_maintenance`, `pipeline_cache_clear`, `job_retention_purge`, and
  `system_metrics_purge`. Each uses the maintenance entrypoint, a single-attempt retry policy,
  durable typed result/error documents, determinate batch progress, and the process-wide exclusive
  maintenance safety gate. Their legacy built-in handlers and inventory entries were removed.
- Backup creation now runs behind canonical submission without recursively acquiring the service
  maintenance gate. It still holds the backup operation lock, creates the same verified backup, and
  registers the confined manifest as an extended-retention `backup_manifest` artifact. Restore
  remains offline-only; no online restore or destructive backup API was introduced.
- Poster and pipeline-cache maintenance enumerate only bounded, server-owned confined roots, reject
  symlinks and path escapes, preserve live movie/series/season and active pipeline references, and
  delete through classified `FilesystemBoundary` descriptors. Job retention preserves the current
  job, nonterminal dependencies, and unexpired log/artifact evidence. Metrics and job deletion use
  bounded transactions with current-attempt ownership and cancellation checks between batches.
- Every destructive maintenance request defaults to dry-run. A dry-run seals a stable checksum and
  category counts without mutation; applying the plan requires the exact checksum and recomputes the
  bounded scope before any write. Results report planned, processed, deleted, per-category counts,
  cancellation, and truthful no-change/partial/success outcomes without exposing raw paths.
- `POST /api/system/backup`, `POST /api/pipeline/maintenance`, and
  `POST /api/pipeline/cache/clear` now require `Idempotency-Key`, submit canonical jobs, and return
  202 job links. Deterministic OpenAPI and generated TypeScript were updated together. Maintenance
  presentation now prefers the typed result's dry-run flag and exposes planned/processed/deleted
  metrics while retaining historical summary compatibility.
- Phase implementation commit:
  `3eb477153463af5cb0f99d962522cf21cd5cc819`
  (`add-canonical-maintenance-workflows`), tree
  `4c0584e715278aebd8bc8304f9bcf72c35753d9d`, sole parent
  `2b501dcfea8ac084e0191feee9772979e61bf198`, authored by Gautam Chaudhri.
- Final focused A4 gate: **67 passed**. Complete retained result: **1156 passed, 21 failed,
  2 warnings in 67.90s**; the failures are the exact recorded JMC4C set. Ruff passed, Alembic
  reported no new upgrade operations, deterministic OpenAPI remained at 197 paths, generated
  TypeScript was current, frontend check reported 0 errors and the inherited 16 warnings in 8
  files, frontend lint/build passed, and `git diff --check` passed.
- Required Serena symbol/edit tooling remained available. One reference lookup over the former
  unusually large poster-maintenance legacy symbol raised Serena's internal `IndexError`; bounded
  Serena symbol replacement/deletion and executable gates remained authoritative. ByteRover MCP
  remained available and A4 curation was queued as `cur-1784098693643`. The optional local
  `brv` CLI/swarm helper remained unavailable (`brv: command not found`); this did not affect the
  required MCP query/curation workflow.
- Current phase is **A5 — final certification, recovery artifacts, squash, and immutable handoff**.
  Exact next work: re-run the full locked certification matrix from the A4 implementation tree;
  verify the JMC4 compact commit/tag/recovery/bundle invariants again; create JMC5A recovery refs and
  an external bundle; squash only the authorized JMC5A range onto the documented base; verify the
  compact tree against the pre-squash implementation tree; tag the compact commit; run the exact
  compact-tree smoke; attach durable verification evidence; and record the final hashes without
  pushing any remote ref.

## Pending operator work after A4

- Controlled live backup creation and destructive maintenance smokes remain pending for
  operator-owned data. A4 verification used only the owned disposable PostgreSQL database and
  confined synthetic media roots. All prior JMC4 native-model and live-media smokes remain pending.
  No operator content or remote ref was touched.

## JMC5A final pre-squash certification — 2026-07-15

- Exact plan base: `cadfdb423b09a5d1e3f9b2270ca147bb080c2723` (`chunk 5 planned`),
  whose sole parent is compact `jmc4c-complete`
  `0eded400fceb01438641baf4da37ae436e998921`. The post-base range is linear, contains no merge,
  exists only on local `job-manager` beyond `origin/job-manager`, and every commit has configured
  author Gautam Chaudhri <gautam.chaudhri@gmail.com>. The ordered record is prerequisite repair
  `932532fa`; A0 `058fa508`, `004843c0`; A1 `fafb3ff7`, `6ad41684`; A2
  `3a3002e6`, `4fac9435`; A3 `edcafc26`, `2b501dcf`; A4 `3eb47715`,
  `504806dc`; and this final certification entry.
- The complete acceptance and retained-suite matrix is certified on the owned disposable
  PostgreSQL 18.3 database at `127.0.0.1:55447/marquee_test` and confined synthetic roots.
  Focused JMC5A mutation/artwork/parent/maintenance/route/freeze gates passed (**67 passed**).
  The authoritative full result is **1156 passed, 21 retained failures, 2 warnings in 67.90s**.
  The unchanged failures are the recorded nine dev-OCR cases, TV development reset, effective OCR
  hardware policy, four run endpoints, two sync resets, system metrics, two taste-artifact cases,
  and the Whisper catalog verdict. No failure, error, skip, or xfail was added.
- Shared gates pass: `ruff check marquee tests`; Alembic/model drift with no new upgrade
  operations at head `0006_jmc4c`; deterministic OpenAPI export/check at 197 paths;
  openapi-typescript 7.13.0 regeneration; frontend check with 0 errors and the inherited 16 warnings
  in 8 files; frontend lint/build; staged and working `git diff --check`. Tool state remains
  Python 3.13.14, pytest 9.0.3, Ruff 0.15.17, Node 22.22.2, RTK 0.42.4, PostgreSQL 18.3, and the
  previously certified model/native-tool inventory. Required Serena and ByteRover MCP operated;
  ByteRover A4 curation is `cur-1784098693643`.
- Enabled executable definitions are exactly `backup_create`, `dovi_analyze`,
  `job_retention_purge`, `learned_head_train`, `letterbox_detect`,
  `letterbox_detect_episode`, `letterbox_detect_tv_scope`, `library_sync`,
  `pipeline_cache_clear`, `poster_backup_subject`, `poster_deploy`,
  `poster_maintenance`, `poster_pipeline`, `poster_rescan`, `poster_reset`,
  `poster_restore`, `subtitle_policy_audit`, `subtitle_scan`,
  `system_metrics_purge`, `system_noop`, `taste_map`, and `taste_rebuild`.
  Exactly the four poster leaves use `media_write`; the five A4 leaves use the serialized
  `maintenance` entrypoint.
- Canonical ticketless/parent-only definitions remain `audio_subs_deep_scan`,
  `dovi_analyze_batch`, `letterbox_apply_batch`, `letterbox_detect_batch`,
  `letterbox_detect_tv_batch`, `letterbox_reencode_tv_batch`, `poster_backup_all`,
  `poster_deploy_reset`, `poster_heal`, `poster_pipeline_batch`,
  `poster_pipeline_tv_batch`, `subtitle_generate_batch`, and `subtitle_scan_all`.
  Deferred disabled leaves are `audio_remove`, `audio_reorder`, `dovi_convert`,
  `letterbox_apply`, `letterbox_apply_tv_scope`, `letterbox_heal`,
  `letterbox_reencode`, `letterbox_remove`, `letterbox_revert_tv_scope`,
  `radarr_upgrade`, `subtitle_embed`, `subtitle_extract`, `subtitle_generate`,
  `subtitle_metadata`, `subtitle_policy`, `subtitle_remove`, `subtitle_restore`, and
  `track_remove`. Webhook mutation and online restore remain unavailable.
- Schedule catalog state is exact: `library-sync` produces `library_sync`, `poster-heal`
  produces the sealed `poster_heal` parent, and `audio-subs-deep-scan` produces its ticketless
  parent. Existing uniqueness/coalescing and explicit activation policy remain authoritative.
  The operating envelope remains four worker tasks and 28 of 32 PostgreSQL connections with
  bounded APIs, batches, events, logs, artifacts, workspaces, and immutable storage.
- Deviations are bounded and recorded: the optional local `brv` CLI/swarm helper is unavailable
  while required ByteRover MCP works; Serena raised one internal `IndexError` on the retired
  oversized legacy poster-maintenance symbol while bounded Serena edits and executable gates
  succeeded. No architecture, safety, ancestry, schema, or acceptance assertion was weakened.
- Operator smokes deferred: controlled live poster deploy/restore/reset/backup/heal, backup creation
  against an operator-approved target, bounded destructive maintenance against operator-approved
  data, OCR/CLIP/DINO publication, DOVI deep analysis, and prior JMC4 native/model checks. Retain all
  recovery material and never bypass canonical fencing, exclusive maintenance, or offline-only
  restore. No operator media, normal data root, backup set, remote ref, or external service was
  mutated during JMC5A.
- **Pre-squash tip:** `HEAD` at this final pre-squash timeline commit, resolved immediately before
  recovery creation. It will be protected as local branch
  `recovery/jmc5a-20260715T070101Z`, annotated tag
  `recovery/jmc5a-pre-squash-20260715T070101Z`, and verified complete external bundle
  `/home/quartermaster/backups/Marquee/marquee-jmc5a-pre-squash-20260715T070101Z.bundle`.
  The intended compact-tree resolver is local annotated tag `jmc5a-complete`; this timeline is not
  edited after compaction.
- JMC5A certifies canonical poster artwork publication, recoverable poster backup/healing,
  consistent backup creation, and bounded maintenance writes. It does not certify source-media
  remuxing/transcoding. JMC5B may start only from verified compact `jmc5a-complete` after its
  commit/tree/base, recovery refs/bundle, and exact compact-tree smoke are proven; this task does
  not start JMC5B.

## JMC5B Phase B0 — verified JMC5A and froze audio/subtitle contracts — 2026-07-15

### Proven JMC5A predecessor state and exact plan base

- **Exact JMC5B plan base:** annotated tag `jmc5a-complete` peels to compact commit
  `a1973e016302feba61285e20c79e48576499f9c6` (`jmc5a: establish canonical mutation workflows`),
  tree `3592cffb502aa7755642344148220021c4fe91e4`, sole parent
  `cadfdb423b09a5d1e3f9b2270ca147bb080c2723` (`chunk 5 planned`). Tag object type is `tag`
  (annotated); tagger, author, and committer are the configured repository user
  `Gautam Chaudhri <gautam.chaudhri@gmail.com>`. The commit message carries no agent/model
  attribution trailer.
- **Tree identity:** pre-squash tip `1d1b96c16601303ddef4aa213cc5593100e05c2c` and compact
  `a1973e0` have byte-identical trees (`3592cffb…`); `git diff` between them is empty.
- **Recovery material verified:** branch `recovery/jmc5a-20260715T070101Z` and annotated tag
  `recovery/jmc5a-pre-squash-20260715T070101Z` both resolve to `1d1b96c…`. External bundle
  `/home/quartermaster/backups/Marquee/marquee-jmc5a-pre-squash-20260715T070101Z.bundle`
  reports `The bundle records a complete history`, and its sha256
  `fa2332e65bf575d26e6b9840e19bf2018b53fb8ed307bc79aca851f10adcd286` matches the recorded value
  exactly.
- **Post-squash smoke proven:** the default Git note on `a1973e0` (blob
  `0172233edba6ea7a615d985c16ed01036ddd5343`) records `JMC5A post-compaction verification` with
  `smoke=67 passed in 4.37s`, `tree_identity=verified`, `sole_parent=verified`,
  `recovery_refs=verified`, `bundle=complete and verified`, and `worktree=clean`. The JMC5A
  timeline states it is not edited after compaction, so this note is the authoritative
  post-squash record. No JMC5A claim was left unproven; the JMC5B prerequisite gate passes.
- **Clean state:** working tree was clean at Phase B0 start (`git status --porcelain` empty);
  branch is `job-manager`; configured author is the repository user only.

### Recorded state change since the JMC5A certification (not a JMC5B action)

- The JMC5A certification recorded its range as existing "only on local `job-manager` beyond
  `origin/job-manager`". That claim was true when written: the `origin/job-manager` reflog shows
  the tracking ref stood at `cadfdb4` at that time. It now stands at `a1973e0` via
  `refs/remotes/origin/job-manager@{0}: update by push`, matching the operator's established
  per-chunk pattern (`490bc1d`, `0eded40`, `cadfdb4`, `a1973e0` were each pushed after
  compaction). **JMC5B performed no push and will not push.** The pushed base is safe for the
  §10 rewrite because JMC5B soft-resets *to* `a1973e0` and never rewrites it; only the JMC5B
  phase range (which does not exist on any remote) is compacted.

### Owned disposable environment and tool/provider baseline

- Owned disposable PostgreSQL **18.3** cluster created for this work only at
  `/tmp/marquee-jmc5b-pg/data`, listening on `127.0.0.1:55448` with its own socket directory,
  role `marquee`, database `marquee_test`. No operator database, normal `DATA_DIR`, media
  library, or backup set was opened or mutated. The operator PostgreSQL on `127.0.0.1:5432` was
  not used.
- **Provisioning correction (deviation, environment only):** the disposable database must be
  provisioned with the application's own migration command
  `python -m marquee.db_migration`, which applies Alembic **and** installs the PgQueuer durable
  schema **and** writes the `schema_contracts` marker rows. Provisioning with raw
  `alembic upgrade head` alone reproduces only 1149 passed / 28 failed: six `tests/test_backup.py`
  cases plus `tests/test_jmc1_readiness.py::test_api_startup_fails_closed_before_serving` fail
  environmentally on the missing markers/PgQueuer catalog (`relation "schema_contracts" does not
  exist`, then `restored database failed schema or PgQueuer certification`). This is an
  environment-provisioning fact, not a product defect: no source file was changed while
  diagnosing it, and the certified baseline is reproduced exactly once the command is used.
  Recorded so a cold agent does not misread these seven as regressions.
- Alembic sole head remains `0006_jmc4c`. `schema_contracts` markers are `marquee|0006_jmc4c` and
  `pgqueuer|1.1.1|durable`.
- **Tool baseline:** Python `3.13.14`, pytest `9.0.3`, Ruff `0.15.17`, Alembic `1.18.4`,
  Node `22.22.2`, Git `2.55.0`, RTK `0.42.4`, PostgreSQL `18.3`, pgqueuer `1.1.1` (pinned),
  openapi-typescript `7.13.0`.
- **Media/provider capability (JMC5B-critical):** ffmpeg `8.1.2`, ffprobe `8.1.2`,
  mkvmerge/mkvextract/mkvpropedit `v99.0 ('Buka') 64-bit`. `mkvmerge --gui-mode` and FFmpeg
  `-progress` are therefore both available for B09. Provider stack: faster-whisper `1.2.1`,
  stable-ts-whisperless `2.19.1`, torch `2.7.1+cu126`.
- **Live provider boundary:** `.env` configures a real Subgen endpoint
  `SUBGEN_URL=http://localhost:9000` (plus a redacted callback token) and pydantic-settings reads
  `.env`, so tests inherit it. Nothing is listening on port 9000 at B0. Per the JMC5B safety
  boundary no test may contact a live provider; B3 follows the existing repository convention of
  monkeypatching `subtitle_settings.SUBGEN_URL` to a fake host (`http://subgen.local`) with a
  stubbed transport, as already used by `tests/test_subgen_generator.py` and
  `tests/test_frontend_gap_routes.py`. No live provider was contacted during B0.
- No committed MKV/MP4/sidecar fixtures exist; JMC5B generates small confined fixtures per §9.
- Required Serena MCP is available, its manual was read, and project `Marquee` is active.
  Required ByteRover MCP is available and was queried (its subtitle/schema entries predate the
  PostgreSQL cutover and were treated as stale where code disagreed; code beats doc). RTK
  proxying is available and was used.

### Complete retained baseline at the B0 freeze

- Authoritative full result on the owned disposable target: **1156 passed, 21 failed, 2 warnings
  in 66.67s** — reproducing the JMC5A certification exactly, both in counts and in membership.
  The 21 retained failures are the exact recorded JMC5A/JMC4C set: nine development OCR-label
  cases, TV development reset, effective OCR hardware policy, four run endpoints, two sync
  resets, system metrics, two taste-artifact cases, and the Whisper catalog verdict. No failure,
  error, skip, or xfail was added.
- Shared gates all pass: `ruff check marquee tests` (`All checks passed!`); Alembic offline
  `upgrade head --sql` with no new operations at head `0006_jmc4c`; deterministic OpenAPI
  `api:check` current at **197 paths**; `api:generate:check` clean under openapi-typescript
  7.13.0; frontend `check` at **495 files, 0 errors, 16 warnings, 8 files with problems** (the
  exact inherited set); frontend `lint` and `build` pass; `git diff --check` clean.

### Frozen JMC5B authority inventory (B0, no runtime behavior changed)

- New freeze `tests/test_jmc5b_contract_freeze.py` (7 tests) with fixture
  `tests/fixtures/jmc5b/b0_contract_freeze.json`. It pins the registry surfaces, the twelve
  JMC5B definition states, legacy authority, direct launches, the audio/subtitle route surface,
  and the existing audio/subtitle test inventory.
- **Definition state:** all ten B04 leaves (`audio_remove`, `track_remove`, `subtitle_remove`,
  `subtitle_embed`, `subtitle_metadata`, `audio_reorder`, `subtitle_extract`,
  `subtitle_generate`, `subtitle_policy`, `subtitle_restore`) are present, **dispatch-disabled**,
  `media_write`, `unsafe_mutation`, and still carry the generic `BuiltInIntentV1`/
  `BuiltInResultV1` documents that A02 forbids for enabled mutating definitions — B1/B2 must give
  them family-specific typed documents before enabling. Nine already declare the
  `mkvmerge_gui` tool adapter; `subtitle_generate` declares none (correct: provider-based).
  `subtitle_generate_batch` exists parent-only and disabled. **`subtitle_policy_batch` does not
  exist yet** and must be added by B05/B4.
- **Legacy `MediaJob`/`MediaBackup`/`cancel_registry` inventory (exact, 7 sites):**
  `marquee/api/routes/subtitle_generators.py:generate_for_media_file` and `:generate_for_movie`
  call `media_job_manager`; `marquee/core/subtitles/backup.py:_backup_for_job`,
  `marquee/core/subtitles/mutation.py:_make_backup`, and
  `marquee/core/subtitles/mutation.py:_remove_external_sidecars` use `MediaBackup`;
  `marquee/core/subtitles/generation.py:run_generation_job` and
  `marquee/core/subtitles/mutation.py:_raise_if_cancel_requested` use `cancel_registry`.
  The `MediaJob`/`MediaJobEvent`/`MediaBatch` lifecycle is already removed — `media_jobs/manager.py`
  is a fail-closed facade and the `marquee/api/routes/media_jobs.py` route module is already
  absent. `MediaBackup` (`marquee/models/media_backup.py`, exported from `marquee/models/__init__.py`)
  is therefore the only remaining legacy authority with real power, and is B12's removal target.
- **Direct subprocess launches inside JMC5B scope (exact, 5 calls / 3 scopes):**
  `marquee/core/subtitles/generation.py:_extract_audio_for_asr` (1),
  `marquee/core/subtitles/mutation.py:execute_job` (1), and
  `marquee/core/subtitles/mutation.py:link_or_copy` (3). All must move behind the JMC3 tracked
  launcher per B10. Launches in `letterbox_reencode.py` and `dovi_conversion.py` are JMC5C scope
  and are deliberately untouched; `process_launcher.py`, `supervisor.py`, `backup.py`,
  `db_migration.py`, `media/binaries.py`, and the vendored Subgen are out of this inventory.
- **Route surface frozen (32 audio/subtitle routes):** `audio_subs.py` (8),
  `subtitles.py` (9), `subtitle_policies.py` (7), `subtitle_generators.py` (8).

### Phase B0 result and exact next steps

- Phase commit: see below. Focused freeze result: **7 passed**. B0 changed no runtime behavior:
  it adds only a test module and its fixture.
- Current phase is **B1 — planned/confirm flow, selectors, and canonical backup evidence**.
  Exact next work: implement `plan_mutation(...)`/`confirm_mutation(...)` on the canonical
  submission service with a real `planned` phase and no PgQueuer ticket; store immutable
  request/before-snapshot/expected-target/source-signature/confirmation-requirements/expiry in
  `MediaOperationDetail` without rewriting the requested operation on confirm; add typed
  `MutationTargetV1`-based track selectors (kind, source, language, codec, channels, title,
  dispositions, managed key, inventory signature, original index as hint only) and typed
  before/actual inventories; add canonical checksummed job-linked backup artifacts; prove
  stale-plan, expired-plan, mismatched-confirmation, retired-subject, changed-source, and
  already-dispatched conflicts plus exactly-once confirmation dispatch; delete `MediaBackup`
  authority from target metadata; then update this B0 freeze in the same phase commit.
- No deviation from the plan's architecture or safety rules exists. The only recorded deviations
  are environmental: the disposable-database provisioning correction above, and the stale
  ByteRover subtitle/schema entries.

### Pending operator work after B0

- All prior JMC4/JMC5A operator smokes remain pending. JMC5B live smokes — real Subgen provider
  generation, representative-hardware remux, and operator-library mutation — are pending and must
  not be implied from mocks. No operator media, normal `DATA_DIR`, backup set, remote ref, or
  external provider was touched during B0.

## JMC5B Phase B1 checkpoint — planned/confirmed flow landed — 2026-07-15

- Phase B0 commit: `c85c5d28641d03ea8c069088d52593216fa106c5`
  (`freeze jmc5b audio and subtitle contracts`), sole parent `a1973e0` (the recorded plan base),
  configured author. Focused freeze: **7 passed**. Full retained: **1163 passed, 21 failed**
  (= certified 1156 baseline + 7 freeze tests, identical failure membership).
- B1 checkpoint commit: `1f0e37261d6900c5cf5ef9accca273cca4dee900`
  (`add canonical planned and confirmed mutation flow`), sole parent `c85c5d2`, configured author.
  Focused: **9 passed**. Full retained: **1172 passed, 21 failed, 2 warnings in 69.71s**
  (= 1163 + 9 new B1 tests; the 21 retained failures are unchanged in count and membership).
  Ruff and `git diff --check` pass.
- Landed in B1 so far (B02/B03): new `marquee/core/jobs/mutation_planning.py` providing
  `plan_mutation(...)` and `confirm_mutation(...)`. A plan creates a real canonical job in
  `phase='planned'` with `dispatch_generation = 0`, **no** `JobDispatch` row and **no** PgQueuer
  ticket, plus the strict 1:1 `MediaOperationDetail` carrying the immutable request, before
  snapshot, requested/expected targets, source signature, confirmation requirements, and expiry.
  `confirm_mutation` row-locks job + detail, validates plan version (a sha256 fingerprint over the
  immutable plan documents), expiry, source signature, desired state, and optional expected
  configuration version, records confirmation provenance, then creates dispatch generation 1 and
  enqueues **exactly once**. Confirmation never rewrites the requested operation — the stored
  request is replayed verbatim (asserted).
- Proven by `tests/test_jmc5b_mutation_planning.py` (9 tests): transport-free planned job with
  immutable documents; required targets/signature/bounded TTL; idempotent repeated plan;
  exactly-once confirmation dispatch with provenance and idempotent confirm replay; and typed
  conflicts for stale plan version, changed source signature, expired plan, cancelled plan, and
  missing plan — each proving `dispatch_generation` stays 0 so nothing can publish.
- Registry change: `job.planned` added to `SEMANTIC_EVENT_KEYS` in
  `marquee/core/jobs/event_service.py`. No frozen contract test regressed.
- **B1 is NOT complete.** Remaining B1 work, in order: typed audio/subtitle track selectors and
  before/actual inventories per §5 (track kind, embedded/external source, language, codec,
  channels, title, default/forced/HI dispositions, managed key, inventory signature, original
  index as hint only, with stale/ambiguous resolution failing before mutation); canonical
  checksummed job-linked backup artifacts (B12); deletion of `MediaBackup` authority from target
  metadata (`marquee/core/subtitles/backup.py`, `marquee/core/subtitles/mutation.py:_make_backup`
  and `:_remove_external_sidecars`, `marquee/models/media_backup.py`, and the
  `marquee/models/__init__.py` export); and the B0 freeze update in the same phase commit.
  B2–B5 are untouched. `subtitle_policy_batch` still does not exist. No JMC5B leaf is enabled yet
  and every one still carries the generic `BuiltInIntentV1`/`BuiltInResultV1` that A02 forbids for
  enabled mutating definitions.
- Environment for resumption: owned disposable PostgreSQL 18.3 at `127.0.0.1:55448`
  (`/tmp/marquee-jmc5b-pg/data`, role `marquee`, db `marquee_test`). It is in `/tmp` and will not
  survive a reboot; recreate with `initdb`, then provision with
  `DB_URL=postgresql+asyncpg://marquee@127.0.0.1:55448/marquee_test python -m marquee.db_migration`
  (**not** raw `alembic upgrade head` — see the B0 provisioning note, or seven backup/readiness
  tests fail environmentally). Run tests with that same `DB_URL`.
- No push, force-push, or recovery-material deletion occurred. No operator media, `DATA_DIR`,
  backup set, or live Subgen provider was touched.

## JMC5B Phase B1 complete — planned/confirm, selectors, canonical backups — 2026-07-15

- B1 commits: `1f0e37261d6900c5cf5ef9accca273cca4dee900`
  (`add canonical planned and confirmed mutation flow`), `dc3918b` (checkpoint record), and
  `eb24963` (`add durable track selectors and canonical media backups`). All configured author,
  linear, sole-parent.
- Full retained result: **1188 passed, 21 failed, 2 warnings in 66.88s** — the 21 retained
  failures are exactly the certified JMC5A/JMC4C set, unchanged in count and membership. B1 added
  25 passing tests (9 planning + 9 selectors + 7 backups). Ruff and `git diff --check` pass.
- **Durable selectors (§5/B07)** — `marquee/core/jobs/track_selectors.py`. Verified in code that
  neither obvious identity is durable: `SubtitleTrack.id` is regenerated as a fresh `uuid4` on
  every rescan (`marquee/core/subtitles/service.py:68,92`), and a raw container stream index
  shifts whenever an earlier track is removed or reordered. The selector therefore carries a
  **derived** key `kind:fact_fingerprint:ordinal` built from immutable facts (kind, embedded/
  external source, language, codec, channels, title, default/forced/hearing-impaired, managed key)
  plus an occurrence ordinal that keeps the §9 duplicate-metadata fixture individually
  addressable, together with the inventory signature and the original stream/tool identity kept
  strictly as `*_hint` diagnostics. `resolve_selector` fails closed with typed
  `signature_changed` / `stale` / `ambiguous` reasons before any mutation; `resolve_all` also
  refuses the same track twice in one request. Proven to survive stream-index drift.
- **Canonical checksummed backups (B12)** — `marquee/core/jobs/media_backups.py`. A backup is a
  confined, content-addressed artifact at `data:jmc5/media-backups/{subject}/{sha256}.{ext}`
  copied through `FilesystemBoundary.copy_file` (not the 32 MB-bounded publication coordinator,
  which cannot carry source media), fsynced, re-hashed, and returned as the A1
  `MutationBackupV1` document (artifact key + checksum + size + source signature + retention +
  restore eligibility). It is idempotent by content, fails closed when a recorded key's bytes
  disagree, and `verify_media_backup` refuses missing/tampered/ineligible artifacts before any
  restore consumes them. Backup keys reject traversal and unsafe suffixes; `file_signature`'s
  existing `st_nlink != 1` rule refuses hardlinked sources.
- **`MediaBackup` authority in target metadata is gone**: the typed path records only
  `MutationBackupV1` storage keys, never a `MediaBackup` row or physical path. The remaining
  legacy `MediaBackup` consumers (`marquee/core/subtitles/backup.py`,
  `marquee/core/subtitles/mutation.py:_make_backup` and `:_remove_external_sidecars`) sit on the
  **unreachable** legacy executor chain — `mutation.execute_job` is only reached from
  `generation.py:306` and `restore.py:127`, both of which require legacy job rows that
  `media_job_manager` refuses to create (fail-closed facade). The model/table/service deletion and
  its migration therefore land with B2–B4 as each module is replaced, per B12.
- Freeze maintenance: `tests/fixtures/jmc5a/a0_contract_freeze.json` `test_inventory` gained
  `tests/test_jmc5b_media_backups.py` (added only; nothing removed). This was the JMC5A A0 freeze
  correctly catching a new backup-named test module, and is recorded rather than suppressed —
  no assertion was weakened. The JMC5B B0 freeze passes unchanged because B1 altered no route,
  definition, legacy-authority, or direct-launch surface.
- Registry change from B1: `job.planned` added to `SEMANTIC_EVENT_KEYS`.
- Current phase is **B2 — removals, reorder, and metadata**. Exact next work: add family-specific
  typed request/result/error documents for `audio_remove`, `track_remove`, `subtitle_remove`,
  `audio_reorder`, and `subtitle_metadata` (replacing the generic `BuiltInIntentV1`/
  `BuiltInResultV1` that A02 forbids for enabled mutating definitions); build mkvmerge command
  arrays from the frozen plan rather than client arguments; parse `mkvmerge --gui-mode` progress
  with truthful indeterminate fallback; stage on the destination filesystem, validate, and
  publish only through the JMC3 coordinator; run an authoritative post-operation ffprobe/
  MKVToolNix rescan (B13); attribute per-target outcomes with failing stage; enable exactly those
  five leaves; migrate their routes to planned/confirm submission; regenerate OpenAPI/TypeScript;
  and update both freezes in the same phase commit. Generated confined MKV/MP4 fixtures are still
  to be built (none are committed).

## JMC5B Phase B2 in progress — plan building proven against real media — 2026-07-15

- B2 commits so far: `c77b06c` (`add generated media fixtures and typed track inventories`),
  `3c1c4f8` (`add typed audio and subtitle family documents`), and `0896912`
  (`build mkvmerge remux plans from frozen inventories`). All configured author, linear.
- Full retained result: **1206 passed, 21 failed, 2 warnings in 71.60s**. The 21 retained failures
  are exactly the certified set — verified by set-difference against the recorded membership, not
  by count alone. Ruff and `git diff --check` pass.
- **Generated fixtures (§9)** — `tests/support/media_fixtures.py`. Nothing binary is committed:
  small MKVs are synthesised per test with ffmpeg/mkvmerge into `tmp_path`, covering multiple
  audio/subtitle codecs (ac3/aac/subrip), languages, channel counts (6/2), titles,
  default/forced dispositions, attachments, chapters, and a duplicate-metadata pair.
  `require_media_tools()` skips cleanly when native tools are absent.
- **Typed inventories (§5/B13)** — `marquee/core/jobs/track_inventory_adapter.py` builds one typed
  inventory from real `probe_container` output for both before and actual state. Verified against
  real media: the probe **normalizes** language tags to ISO 639-1 (`fr`), even though mkvmerge was
  given `fra` and ffprobe reports `fre` — so the derived selector is stable across that naming
  difference. Duplicate real tracks stay individually addressable, selectors survive stream-index
  drift, a replaced file fails on `signature_changed`, and removing one track leaves only that
  selector `stale`.
- **Family documents (B04/A02)** — `marquee/core/jobs/audio_subtitle_documents.py`:
  `TrackRemoveRequestV1`, `AudioReorderRequestV1`, `SubtitleMetadataRequestV1`/
  `SubtitleMetadataEditV1`, and `MediaTrackMutationResultV1`. Requests accept durable selectors
  only — no container, tool arguments, paths, stream indexes, or execution policy — and reject
  duplicate/empty targets, partial reorders, cross-kind targets, and no-op edits. The result
  enforces B13: a target that changed bytes cannot be reported without the authoritative actual
  inventory.
- **Plan building (§6.1)** — `marquee/core/jobs/mkvmerge_plan.py` builds argument arrays from the
  frozen resolved inventory, never from client arguments. Proven by executing **real mkvmerge**
  and re-probing: removal drops only the target while preserving non-target streams, surviving
  track identity/title/default flag, chapters, and attachments; removing all audio emits an
  explicit `--no-audio`; reorder produces the requested order; a plan may not empty the file.
- **B09 reuses JMC3, not a second parser:** `MkvmergeProgressAdapter`
  (`marquee/core/jobs/progress_adapters.py:100`) already parses `#GUI#progress`, `#GUI#error`,
  `#GUI#warning`, and `#GUI#exit` with a monotonic guard and a bounded 64 KB buffer (the §9
  stderr-flood case). Verified it parses real `mkvmerge --gui-mode` output to 100%. Confirmed by
  experiment that mkvmerge emits `#GUI#progress N%` on stdout.
- **Verified tool fact:** mkvpropedit's `track:@N` selects by Matroska `TrackNumber`, which is
  *not* mkvmerge's 0-based track id (`@4` targeted mkvmerge id=3). The builder therefore uses the
  documented type-relative selector (`track:s1`) derived from the authoritative inventory instead
  of relying on an `id + 1` coincidence. Metadata edits use mkvpropedit in place and are proven
  not to rewrite stream payloads.
- **B2 remaining (exact next work):** the remux coordinator wiring these plans to the JMC3 tracked
  launcher (B10) with destination-filesystem staging, candidate validation, fence/signature
  recheck, coordinator-only publication (B11), canonical backup before replacement (B12), and the
  authoritative post-operation rescan (B13); per-target outcome attribution with failing stage;
  registering execution handlers and enabling exactly `audio_remove`, `track_remove`,
  `subtitle_remove`, `audio_reorder`, `subtitle_metadata` with their typed documents wired into
  `manifest.py` (`_REQUEST_MODELS`/`_RESULT_MODELS` and `ENABLED_JOB_TYPES`); migrating their
  routes to planned/confirm submission; regenerating OpenAPI/TypeScript; presenter fixtures; and
  updating both freezes in the same phase commit. Enabling a leaf without a registered handler
  breaks definition-coverage, so those land together.
- B3–B5 untouched. `subtitle_policy_batch` still absent. `MediaBackup` model/table still present
  behind the unreachable legacy executor chain.

## JMC5B Phase B2 — five track-mutation leaves enabled and proven — 2026-07-15

- B2 commits: `c77b06c`, `3c1c4f8`, `0896912`, `2b2e790`
  (`enable canonical track removal reorder and metadata leaves`), and `027d097`
  (`prove track mutation handlers against real media`). All configured author, linear.
- Full retained result: **1213 passed, 21 failed, 2 warnings**. The 21 are the exact certified
  set, verified by **set-difference in both directions** (no new failure; none disappeared).
  Ruff, Alembic offline, deterministic OpenAPI (197 paths), and `git diff --check` pass.
- **Enabled exactly five `media_write` leaves**: `audio_remove`, `track_remove`,
  `subtitle_remove`, `audio_reorder`, `subtitle_metadata`, each with family-specific typed
  request/result documents (A02's ban on generic `BuiltInIntentV1`/`BuiltInResultV1` for enabled
  mutating definitions is now satisfied for them), registered handlers in
  `marquee/core/jobs/handlers_track_mutations.py`, and the `mkvmerge_gui` progress adapter.
- **B10 reuses the JMC3 launcher**: `ProcessLauncher.launch(tool, args)` already exists and its
  `TOOL_CATALOG` already permits `mkvmerge`/`mkvpropedit`. No new exec surface was created and no
  direct `create_subprocess_exec` was added.
- **End-to-end proof over real media** (`tests/test_jmc5b_track_mutation_handlers.py`, 7 tests):
  a real `ProcessLauncher` runs real mkvmerge/mkvpropedit over generated fixtures. Proven:
  removal publishes and is confirmed by the authoritative rescan while preserving chapters,
  attachments, and non-target streams; a canonical checksummed backup exists under the confined
  data root before replacement (B12); multi-target removal reports every target; reorder
  publishes the requested order; an already-satisfied metadata edit is a reasoned `no_change`
  that writes nothing; and a metadata edit publishes and rescans. Two fail-closed proofs assert
  the source bytes are **byte-identical** afterwards: a stale selector fails at `resolve`, and a
  changed source signature fails with `signature_changed` — both with every target `not_applied`
  and `published=false`.
- **Presenter migrated to typed evidence (§7)**: `presenters/audio_subs.py` now derives the
  headline, the track table, and the before/after inventory from the typed result's
  `requested_targets`/`target_outcomes`/`before_inventory`/`actual_inventory` instead of the
  legacy `summary` dict. **Defect found and fixed while doing so:** the before/after row computed
  `changed = before != after`, which reported *changed* for a failed remux because `after` was
  unknown (`None`). It now requires a known, differing count, so a failed remux correctly reports
  nothing changed.
- **Freeze/certification maintenance (no assertion weakened):** enabling five leaves legitimately
  moved them out of every prior chunk's "still deferred" enumeration. Updated
  `test_jmc3_certification`, `test_jmc3a/3b/4a/4b/4c_contract_freeze`,
  `test_jmc4a_worker_certification`, `test_jmc4c_certification`, `test_backup`,
  `test_job_definition_manifest`, and both JMC5A/JMC5B freeze fixtures — using the same
  named-exemption pattern JMC5A used for `A4_MAINTENANCE` (now `B2_TRACK_MUTATIONS`). The safety
  invariant is intact: every type still deferred must raise `DisabledJobDefinitionError`, and the
  enabled `media_write` set is asserted to be exactly the four poster leaves plus these five.
- **Verified tool facts:** mkvmerge emits `#GUI#progress N%` on stdout and JMC3's
  `MkvmergeProgressAdapter` parses it to 100% (B09 needs no new parser). mkvpropedit's `track:@N`
  selects by Matroska `TrackNumber`, not mkvmerge's 0-based id, so the builder uses the
  documented type-relative selector derived from the authoritative inventory.
- Current phase is **B3 — embed, extract, and generation**. Exact next work: migrate
  `subtitle_embed`, `subtitle_extract`, and `subtitle_generate`; remove the direct
  `create_subprocess_exec` calls and `cancel_registry` from `marquee/core/subtitles/generation.py`
  and `mutation.py`; certify provider polling/late output/bounded waits with a stubbed provider
  (never the live `SUBGEN_URL=http://localhost:9000`); prove the external/embedded atomic-group
  split (B08/B15) and registered-artifact returns (B17). Then B4 (policy, restore, batches,
  `subtitle_policy_batch`) and B5 (certification + §10 squash). `MediaBackup` model/table still
  present behind the unreachable legacy executor chain; it is removed with its last consumer.

## JMC5B Phase B3 in progress — extract and embed enabled — 2026-07-15

- B3 commit so far: `1237146` (`add canonical subtitle extract and embed leaves`), configured
  author, sole parent `fc99888`.
- Full retained result: **1220 passed, 21 failed, 2 warnings**. The 21 are the exact certified set
  (verified by set-difference both directions). Ruff, deterministic OpenAPI (197 paths), and
  `git diff --check` pass. Seven JMC5B leaves are now enabled: the five B2 track mutations plus
  `subtitle_extract` and `subtitle_embed`.
- **`mkvextract` is deliberately NOT used.** It is absent from the JMC3 launcher's `TOOL_CATALOG`
  (`ffprobe`, `ffmpeg`, `mkvmerge`, `mkvpropedit`, `convert`, `dovi_tool`). Rather than widen the
  exec surface, extraction uses the already-allowlisted `ffmpeg` (`-map 0:s:N -c:s copy`), which
  was verified by experiment to extract correctly and to emit `-progress` records.
- **Extraction (B17)**: stages ffmpeg output, validates it (non-empty, valid UTF-8, contains cue
  timing), then publishes a **content-addressed managed sidecar** at
  `data:jmc5/managed-subtitles/{sha256}.srt` and registers a `ManagedSubtitleAsset` row whose
  public handle is the key, never a path. Proven over real media: the published artifact matches
  its checksum and contains the expected track, the **source is byte-identical afterwards**,
  repeated extraction is idempotent by content (one file, one asset row), and a stale selector
  fails at `resolve` writing nothing.
- **Embedding (§6.2/B13)**: consumes only a validated managed asset key — never a caller path —
  verifies the sidecar's checksum before use, remuxes through the tracked launcher, creates a
  canonical backup, publishes atomically, and proves the exact new track by rescan. Proven:
  extract→embed round-trip lands a second track with the requested language/title/forced flag; an
  unknown asset fails at `preflight` (`asset_missing`); a tampered sidecar fails at `validate`
  (`asset_invalid`). Both failures leave the source byte-identical and `published=false`.
- **Refactor forced by a real defect:** `handlers_sidecars` initially imported private helpers from
  `handlers_track_mutations`, which produced a circular import at collection. The shared
  confinement/probe/resolution helpers now live in
  `marquee/core/jobs/media_mutation_support.py` and both handler modules depend on it. Test
  fixtures likewise moved to `tests/support/jmc5b_harness.py`, registered via `pytest_plugins` in
  `tests/conftest.py`, so fixtures resolve by name without cross-importing test modules.
- Freeze/certification maintenance extended to the two new leaves using the same named-exemption
  pattern; the safety invariant (still-deferred types must raise `DisabledJobDefinitionError`,
  and the enabled `media_write` set is exact) remains intact.
- **B3 remaining:** `subtitle_generate` (B14/B15/B16) — provider submission/wait/download/
  reconciliation as distinct stages, bounded cancellable polling, late-output-after-cancellation
  safety, the generation/embed atomic-group split proving "generated but not embedded", and
  removal of the direct `create_subprocess_exec` + `cancel_registry` from
  `marquee/core/subtitles/generation.py` and `mutation.py`. Tests must stub the provider
  (`SUBGEN_URL` monkeypatched to a fake host); the live `http://localhost:9000` is never contacted.
  Then B4 (policy, restore, batches, `subtitle_policy_batch`) and B5 (certification + §10 squash).

## JMC5B Phase B3 — extract, embed, and generation enabled — 2026-07-15

- B3 commits: `1237146` (extract/embed) and `d34891e`
  (`add canonical subtitle generation with bounded provider polling`). Configured author, linear.
- Full retained result: **1229 passed, 21 failed, 2 warnings**. The 21 are the exact certified set
  (set-difference verified both directions). Ruff, deterministic OpenAPI (197 paths), and
  `git diff --check` pass. **Eight of the ten B04 leaves are now enabled**: `audio_remove`,
  `track_remove`, `subtitle_remove`, `audio_reorder`, `subtitle_metadata`, `subtitle_extract`,
  `subtitle_embed`, `subtitle_generate`. Remaining: `subtitle_policy`, `subtitle_restore` (B4).
- **Generation (§6.3/B14/B15/B16)** — `marquee/core/jobs/handlers_generation.py`. Submission,
  waiting, reconciliation, download, validation, and publication are distinct stages.
  `wait_for_provider` polls `reconcile` with a bounded deadline and checks cooperative
  cancellation **before every poll and every sleep**, so a cancelled job stops waiting without the
  provider responding. Webhook completion stays deferred (B16): completion is discovered only by
  reconciliation polling. B14 is asserted — the job never rewrites its type or request; the
  publish target is fixed at request time.
- **B15 evidence split proven**: `SubtitleGenerationResultV1` carries `generated` and `embedded`
  separately. Tests prove a rejected submission is `generated=false` with nothing published;
  invalid provider output is **`generated=true`, `sidecar=None`** ("generated but nothing
  published"); and late output arriving after cancellation is `generated=true` but refuses to
  publish, leaving the managed store empty. Bounded-deadline and provider failed/timeout states
  are named honestly (`provider_timeout`, `provider_failed`).
- **Live provider never contacted.** `.env` sets `SUBGEN_URL=http://localhost:9000`; every
  generation test uses a local `StubProvider` double injected by monkeypatching `get_generator`,
  and performs no network I/O. Real-provider generation remains an un-run operator smoke.
- **Presenter migrated for generation (§7):** the headline's language and the Provider fact now
  come from the typed result (`requested_targets[].selector_facts.language_tag`, `provider`), and
  the typed result carries a safe `source_track` label (never a path or raw index). The JMC2C
  golden `subtitle_generate_detail.json` was regenerated and **reviewed**: the headline is
  unchanged ("Generate English subtitles from audio stream 3"), Provider now reads from the typed
  document, and the presentation gained `track_table` + `change_list` sections — richer, as §7
  requires. The legacy `Model`/`Output file` facts came from the removed summary; §7 requires
  provider/task/language and the produced artifact, all of which are present (the artifact via the
  sidecar/track table).
- **Two of my own fixtures were wrong and the models caught them** — recorded rather than worked
  around: the golden claimed a published sidecar while recording no artifact, which
  `SubtitleSidecarResultV1`'s invariant correctly rejected; and a stubbed cancellation was
  off-by-one against the handler's real pre-publish check. Both were fixed in the tests, not by
  loosening the models.
- Current phase is **B4 — policy, restore, and batches**. Exact next work: migrate
  `subtitle_policy` and `subtitle_restore`; add the parent-only `subtitle_policy_batch` (B05) and
  keep `subtitle_generate_batch` parent-only; freeze the evaluated policy/version and one
  immutable child plan per file before creating children (B18, no mid-batch policy adoption);
  prove restore validates the canonical backup checksum, original source identity, and current
  destination, never deletes its only verified backup, and refuses to overwrite a newly changed
  file without a fresh plan; prove batch aggregation, zero-child/no-change, mixed outcomes, and
  cancellation. Then B5 (full certification + §10 squash + `jmc5b-complete`). The direct
  `create_subprocess_exec` and `cancel_registry` in `marquee/core/subtitles/generation.py` and
  `mutation.py` still exist on the unreachable legacy chain and are removed with `MediaBackup`
  when B4 retires their last consumers.

## JMC5B Phase B4 — policy, restore, and sealed batches — 2026-07-15

- B4 commit: `9998ad9` (`add canonical subtitle policy restore and sealed batches`), configured
  author, sole parent `3e27d07`.
- Full retained result: **1239 passed, 21 failed, 2 warnings**. The 21 are the exact certified set
  (set-difference verified both directions). Ruff, deterministic OpenAPI (197 paths), and
  `git diff --check` pass.
- **All ten B04 leaves are now enabled**: `audio_remove`, `track_remove`, `subtitle_remove`,
  `subtitle_embed`, `subtitle_metadata`, `audio_reorder`, `subtitle_extract`, `subtitle_generate`,
  `subtitle_policy`, `subtitle_restore`. JMC5C's set remains dispatch-disabled and verified:
  `dovi_convert`, `letterbox_apply`, `letterbox_heal`, `letterbox_reencode`, `letterbox_remove`,
  `radarr_upgrade`.
- **`subtitle_policy_batch` added (B05)** as a ticketless parent-only aggregate (registry 53 → 54).
  `marquee/core/jobs/subtitle_parents.py` evaluates the policy once and **freezes** the per-file
  decision into each child's immutable request (B18). Proven: the sealed parent carries no PgQueuer
  ticket, each child carries the frozen `remove_selectors` and the evaluated `policy_revision`, a
  file the policy selected nothing for becomes a child with an empty frozen plan, an empty scope
  seals zero children, duplicate files and an unbounded scope are refused, and child idempotency
  keys are deterministic and path-free. No child ever re-reads live policy, so a policy edited
  after sealing cannot be adopted mid-batch.
- **`subtitle_policy` leaf** executes only its frozen plan; a plan that selected nothing is a
  reasoned `no_change` (`policy_selected_nothing`) that writes nothing.
- **`subtitle_restore` leaf (B12)** consumes only a confined artifact key + checksum + recorded
  source lineage. Proven over real media: it refuses a destination that changed since planning
  (`destination_changed`), refuses a tampered backup artifact at `validate`, reports
  `already_restored` when the destination already matches, and on a real round-trip (remove a
  track, then restore) **republishes the original bytes exactly**, proves both audio tracks
  returned by rescan, and **leaves its backup intact** — the backup is copied and the copy is
  published, so a restore never consumes its only verified backup.
- **Defect found and fixed in my own handler:** the restore no-change check originally compared
  `resolved_file.signature` (from `compute_signature`) against the backup's `source_signature`
  (from `signature_text`). Those are different schemes and can never compare equal, so an
  already-restored file would have been needlessly republished instead of reporting `no_change`.
  It now compares the destination's actual content digest against the backup checksum.
- **Registry-growth gates satisfied, not suppressed:** adding a definition tripped
  `test_job_definition_inventory`, `test_job_presenters_supporting`,
  `test_job_definition_manifest`, and the jmc3a/4a/4b freezes. `subtitle_policy_batch` was given a
  real parent presenter, headline, child noun, and label so the "every definition has a dedicated
  presenter" coverage gate passes on merit; the count assertions were updated to 54 with the
  reason recorded inline.
- Current phase is **B5 — JMC5B certification and history compaction**. Exact next work: run the
  complete §9 acceptance matrix and every shared gate from the B4 tree; add the static legacy scan
  proving migrated modules contain no `MediaJob`/`MediaBackup`/`media_job_manager`/
  `cancel_registry`/direct child launch; retire the now-unreachable legacy
  `marquee/core/subtitles/mutation.py`, `generation.py`, `restore.py`, `backup.py` consumers and
  the `MediaBackup` model/table (with its Alembic migration); migrate the audio/subtitle routes to
  canonical planned/confirm submission and regenerate OpenAPI/TypeScript; then perform §10
  (recovery refs + verified bundle + tree-identical squash + `jmc5b-complete`).

## JMC5B Phase B5 — canonical route cutover and legacy retirement — 2026-07-15

- B5 implementation commit: `37df97db0105e224a0d8c67f72d2097f6bc05d48`
  (`=finish-jmc5b-mutation-migration`), configured repository author, sole parent
  `90a80cfdc3fb75020e00cabd4ec77078e7c9ebac`. The unusual leading `=` is an RTK argument-rendering
  artifact on this temporary phase commit only; §10 replaces the entire phase range with the exact
  required compact subject.
- **Canonical public flow:** audio/subtitle mutation routes now create typed planned jobs and
  dispatch only through exactly-once confirmation with matching plan version, source signature,
  and configuration version. Planning creates no PgQueuer ticket. A changed source is rejected
  before dispatch. Movie and TV generation and policy application create ticketless sealed parents
  with immutable typed children. Generation never rewrites its job type/request and bounded provider
  reconciliation is independent of the deferred webhook.
- **Legacy authority retired:** the process-local media-job facade, legacy mutation/backup/restore
  modules, physical-path backup model/export, and `media_backups` table are deleted. Alembic sole
  head is `0007_jmc5b`; its one-way migration drops the legacy table. Canonical restore consumes a
  confined checksummed job artifact and never consumes its only verified backup. Static scans of
  the migrated route/handler/generation modules return no legacy lifecycle/model/manager/cancel or
  direct-subprocess symbol.
- **Publication and validation:** every source-changing leaf stages on the destination filesystem,
  uses `media_write`, per-file exclusion, one automatic domain attempt, the tracked launcher, and
  fenced coordinator publication. Remove/reorder/embed/metadata/policy/restore publication is
  coordinator-only; metadata edits a candidate rather than the source in place. Post-publication
  inventory uses the actual recomputed signature. External selectors are refused for source-changing
  remux operations. Sidecar generation records an explicit generation atomic group and, for embed,
  a separate embed group.
- **Focused media/contract result:** all JMC5B modules **88 passed**; the final backup/restore/freeze
  resmoke after the legacy-name cleanup **24 passed**. Generated confined MKV/sidecar fixtures cover
  selector stability, removals/reorder/metadata, embed/extract/generation, backup/restore, policy
  batches, stale confirmation, coordinator fences, tool failures, cancellation, and atomic-group
  outcomes. No operator media or live provider was contacted.
- **Complete retained result:** **1233 passed, 21 failed, 2 warnings in 87.20s** on owned disposable
  PostgreSQL 18.3 at `127.0.0.1:55449/marquee_test`. Failure membership is exactly the certified
  retained set in both directions: nine development OCR-label cases, TV development reset,
  effective OCR hardware policy, four run endpoints, two sync resets, system metrics, two
  taste-artifact cases, and the Whisper catalog verdict. There is no new failure, error, skip,
  xfail, quarantine, or weakened assertion.
- **Shared gates:** Ruff passes; `git diff --check` passes; offline Alembic upgrade SQL reaches
  `0007_jmc5b`; focused schema/Alembic/ORM/audio-subtitle schema result is **55 passed**; deterministic
  OpenAPI is current at **198 paths**; openapi-typescript `7.13.0` regeneration is clean; frontend
  check is **0 errors, 16 inherited warnings in 8 files**; frontend lint and production build pass.
- **Tool/provider/fixture envelope:** Python `3.13.14`, pytest `9.0.3`, Ruff `0.15.17`, Alembic
  `1.18.4`, Node `22.22.2`, Git `2.55.0`, RTK `0.42.4`, PostgreSQL `18.3`, pgqueuer `1.1.1`, ffmpeg/
  ffprobe `8.1.2`, MKVToolNix `v99.0 ('Buka') 64-bit`, faster-whisper `1.2.1`,
  stable-ts-whisperless `2.19.1`, and torch `2.7.1+cu126`. Tests used only generated confined
  fixtures and stubbed `http://subgen.local` transport; the configured localhost provider was not
  contacted.
- **Enabled/deferred manifest:** enabled leaves are `audio_remove`, `track_remove`,
  `subtitle_remove`, `subtitle_embed`, `subtitle_metadata`, `audio_reorder`, `subtitle_extract`,
  `subtitle_generate`, `subtitle_policy`, and `subtitle_restore`; enabled parent-only aggregates are
  `subtitle_policy_batch` and `subtitle_generate_batch`. Deferred/dispatch-disabled for JMC5C remain
  `dovi_convert`, `letterbox_apply`, `letterbox_apply_tv_scope`, `letterbox_heal`,
  `letterbox_reencode`, `letterbox_remove`, and `radarr_upgrade`; Subgen webhook completion is also
  deferred.
- Current phase remains **B5 certification and §10 history compaction**. Exact next steps: commit
  this timeline checkpoint; prove the post-base range is owned, linear, sole-parent, and unpushed;
  record the pre-squash tip/tree and intended tag; create timestamped recovery branch/tag and a
  verified complete external bundle; soft-reset through RTK to exact base
  `a1973e016302feba61285e20c79e48576499f9c6`; create the exact configured-author compact commit;
  prove tree identity/sole parent/clean state; create annotated `jmc5b-complete`; run the prescribed
  post-squash smoke. Stop on any ownership, ancestry, backup, or tree mismatch.
- **Pending operator actions:** real Subgen generation, representative-hardware remux, and any
  operator-library mutation remain deliberately unrun, as do inherited JMC4/JMC5A manual smokes.
  No push, force-push, recovery deletion, operator database/library access, or JMC5C work occurred.

## JMC5B final pre-squash certification — 2026-07-15

- Exact plan base is annotated `jmc5a-complete`, resolving to
  `a1973e016302feba61285e20c79e48576499f9c6` with tree
  `3592cffb502aa7755642344148220021c4fe91e4`. The certified pre-entry JMC5B range is 20 commits;
  this final timeline entry makes the protected range 21 commits. It has no merge,
  every commit has exactly one parent forming an unbroken chain from that base, and every commit
  is authored by configured repository author Gautam Chaudhri <gautam.chaudhri@gmail.com>.
  `origin/job-manager` is exactly the plan base; local `job-manager` is 20 ahead, zero behind, and
  no remote ref contains the pre-squash tip. The worktree is clean.
- Certification is the B5 record immediately above: 88 JMC5B-focused passes, 55 schema/Alembic/
  ORM passes, authoritative full **1233 passed / 21 retained failures / 2 warnings**, Ruff,
  deterministic 198-path OpenAPI and generated TypeScript, frontend check/lint/build, static
  legacy scans, offline migration SQL, and `git diff --check`. Enabled/deferred types, exact
  failure membership, versions, deviations, and pending operator actions are unchanged.
- **Pre-squash tip:** `HEAD` at this final timeline commit, resolved immediately before recovery
  creation. It will be protected by local branch `recovery/jmc5b-20260715T224135Z`, annotated tag
  `recovery/jmc5b-pre-squash-20260715T224135Z`, and verified complete external bundle
  `/home/quartermaster/backups/Marquee/marquee-jmc5b-pre-squash-20260715T224135Z.bundle`.
  The intended compact resolver is local annotated tag `jmc5b-complete`; the required compact
  subject is `jmc5b: migrate audio and subtitle mutations`. This timeline is not edited after
  compaction.
- JMC5B certifies only the audio/subtitle mutation scope described above. It does not start or
  certify JMC5C. JMC5C may begin only after the compact commit/tree/base, sole-parent ancestry,
  recovery refs/bundle, clean worktree, annotated completion tag, and post-squash smoke are all
  proven.
