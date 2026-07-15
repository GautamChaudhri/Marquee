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
