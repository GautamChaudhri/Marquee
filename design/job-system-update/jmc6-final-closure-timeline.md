# JMC6 Final Closure Timeline

Shared execution and cold-handoff record for JMC6G and JMC6H. JMC6G is the current and only
active implementation plan. No push, activation, JMC6H implementation, or operator-state mutation
is authorized.

## JMC6G Phase G0 — predecessor verification and closure-manifest freeze

### Exact starting state

- JMC6G implementation starts from owner-authored planning commit
  `d2dd72a4e01ca93883ae04b2af12f94c7be29f3a` (`chunk 6g-h planned`), tree
  `faa67230ca5857ab81d2d6bb464d000f93028255`. That commit and `origin/job-manager` are identical.
  It is immutable, already-pushed plan input and is excluded from the future JMC6G implementation
  range and history rewrite.
- The exact predecessor remains annotated `jmc6f-complete`, resolving to configured-author compact
  commit `d8214fd39f59de2f0857346995ef77910078deb8`, tree
  `009dfc71d6cec429bbfbcfec4e95813a9c2bc26e`, with sole parent
  `jmc6e-complete` (`b46766bb4cca229db087e2dd35a389d952e6115e`). The compact tree is
  byte-identical to recovery tip `637666c0a21d2bd3537c405869b7dc15363c3172`.
- JMC6F recovery branch `recovery/jmc6f-20260718T004241Z`, annotated tag
  `recovery/jmc6f-pre-squash-20260718T004241Z`, and external bundle
  `/tmp/marquee-jmc6f-20260718T004241Z.bundle` exist. `git bundle verify` reports complete SHA-1
  history and the recovery refs resolve to the certified tree. Earlier recovery material is
  retained. No recovery ref was changed or deleted.
- The branch is `job-manager`, tracks `origin/job-manager`, and began clean at the planning commit.
  Repository author and committer configuration is Gautam Chaudhri
  `<gautam.chaudhri@gmail.com>`; that configured identity is the only permitted commit identity.
  There is one worktree and no implementation commit exists yet.
- Required instructions and plans were read in full: `AGENTS.md`, `CLAUDE.md`, plan workflow README,
  JMC6G, JMC6F, the complete JMC6D–F shared timeline, direct PgQueuer adoption, clean-slate
  migration, semantic progress, and Projection Room experience documents.
- Serena MCP, ByteRover MCP, and RTK are operational. The optional/local `brv` CLI is not installed
  (`command not found`), so CLI-only swarm query is unavailable; the required ByteRover MCP query
  path is operational and was used. RTK is 0.42.4.

### Owned baseline environment and tools

- Owned disposable PostgreSQL 18.3 cluster:
  `127.0.0.1:55453/marquee_test`, role `marquee`, data root
  `/tmp/marquee-jmc6g-pg.mt3VtY`, Unix socket root `/tmp`. Port 5432 and the prior JMC6F cluster
  were not targeted. The official migration service applied `0001_jmc1` through `0008_jmc6e` and
  installed/upgraded/verified PgQueuer 1.1.1 durable.
- Owned disposable execution/media fixture root:
  `/tmp/marquee-jmc6g-data.jCy7Wd`. The complete legacy baseline itself retains the repository's
  required default `DATA_DIR` contract because `test_pipeline_run_root_is_inside_data` deliberately
  requires `<project>/data/runs/work`; synthetic JMC6G execution/media fixtures use the owned root.
- Tools: Python 3.13.14, pytest 9.0.3, Ruff 0.15.17, Alembic 1.18.4, PgQueuer 1.1.1,
  PostgreSQL 18.3, Node 22.22.2, npm 10.9.7, openapi-typescript 7.13.0, Playwright 1.61.1.

### Verified predecessor and zero-green baseline

- Authoritative complete backend baseline against the owned database:
  **1309 passed, 0 failed, 0 skipped, 0 xfail/xpass, 0 warnings in 90.77s**.
- A discarded environment-contract run with global `DATA_DIR` redirected to the owned fixture root
  produced 1308 passes and only `test_pipeline_run_root_is_inside_data` failed because the test
  intentionally freezes the repository-local pipeline root. No code/test changed; the corrected
  baseline above preserves that contract and passed completely.
- Ruff over `marquee tests scripts` passes. Alembic current/head are both `0008_jmc6e`;
  `alembic check` reports no upgrade operations; repeated official migration/PgQueuer verification
  is idempotent. Schema contract fingerprints are Marquee
  `7af29195f6966074ebd295c34b35e9bd4f092e39ecfa55648a9e9c69e24ab528` and PgQueuer
  `19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a`.
- OpenAPI is current at 199 paths, SHA-256
  `4f70e9e7c5cbd90e5359fc8fadde37ad48272756e203a4a1f899b67459f9c7a4`; generated TypeScript
  SHA-256 is `96eedda857b02f9e54ae402bac3da5ea01c55438816e210e3eb8c4c73aa36d89` and regeneration is
  byte-clean.
- Frontend: Svelte check 0 errors/0 warnings; Prettier/ESLint clean; 10 Vitest files / 111 tests;
  production build passes with only the previously recorded large-chunk/plugin-timing notices;
  fresh-server Chromium Playwright/axe 11/11. `git diff --check` was clean before timeline creation.
- Registry baseline is 62 definitions / 43 enabled leaves / 43 canonical delivery handlers / 19
  parent-only or reserved definitions. No enabled definition is omitted from the G0 audit.

### Installed PgQueuer contract inspection

- Installed source confirms `RetryRequested(delay, reason)` requeues the same transport job,
  increments transport attempts, and defers `execute_after`; PgQueuer imposes no Marquee definition
  attempt budget or delay cap.
- A callback that returns normally is logged `successful`; an exception becomes `failed` only when
  the entrypoint uses `on_failure="hold"` (otherwise `exception`). Cancellation records `canceled`.
  Therefore Marquee must not return after an unsafe/nonterminal canonical conflict, and it must
  raise only after durable canonical retry/hold evidence.
- PgQueuer heartbeat owns stale picked-ticket redelivery. Its completion watcher treats canceled,
  deleted, exception, and successful as terminal; held `failed` work remains operator-visible rather
  than normal completion. Installed source is the authority for G1/G2 transport acknowledgement.

### Reproduced JMC6G audit defects

- `FencedWriter.succeed` special-cases only `MutationResultV1`; every other validated result is
  forced to canonical/attempt/dispatch success. Mutation no-change/partial values are collapsed to
  transport-style success while unsafe/failed strings can be copied into incompatible vocabularies.
- `deliver_job` seals the attempt log as `succeeded` before the fenced canonical terminal
  transaction. A terminal conflict can therefore leave a success-claiming log while the canonical
  job is nonterminal; stale non-conflict completion can also return normally and be acknowledged.
- Definition retry policies exist but delivery accepts handler-chosen `RetryRequested` delay without
  applying the classifier, current durable attempt count, maximum attempts, or bounded delay list.
  Letterbox/DoVi handlers hard-code their own retry delay.
- Definition timeout limits safety-gate admission only. `_execute_delivery` awaits handlers without
  a handler execution timeout.
- Every enabled definition currently declares an empty configuration-key set; the sole nonempty
  branch is for disabled `audio_subs_deep_scan`. Enabled handlers still contain mutable settings
  reads requiring G2 inventory and migration to `ExecutionContext.configuration`.
- Progress policies exist structurally, but handler reachability shows enabled long-running paths
  without JMC3 progress writes/current-subject updates, native progress adapters named without
  live-stream wiring, provider/copy/hash/backup waits not surfaced, and single-stage generic
  mutation policies that do not prove subject-aware semantic execution.
- Enabled product paths still contain blocking/untracked subprocess or large-file work; physical
  retention metadata exists but `job_retention_purge` deletes canonical rows without first invoking
  confined physical log/artifact expiration. Subtitle/audio mutations do not uniformly persist the
  authoritative post-operation inventory/bindings/artifacts.
- Startup orphan selection, Operations listener health, and list queue-rank logic retain the three
  bounded diagnostic defects named by G26; exact behavioral freezes are added in G0.

### Current phase and exact next steps

- **Current phase:** G0 — freeze the complete enabled-definition closure manifest and intentional
  red behavioral contracts.
- **Exact next steps:** finish Serena reference/reachability audit for all 43 enabled handlers and
  their submission, delivery, persistence, process/I/O, projection, artifact, presenter, and batch
  consumers; add a checked-in closure manifest with no omitted enabled definition; add intentional
  red tests that prove terminal mapping/order, retry/timeout/configuration, progress/process/I/O,
  projection/retention, and bounded diagnostics defects through canonical submission and delivery;
  run focused red inventory plus complete comparison, Ruff, schema/generated/frontend/browser/diff
  gates; commit G0 as configured author; append its hash/results and continue immediately to G1.
- **Deviation and justification:** the owner-authored pushed `chunk 6g-h planned` commit follows
  `jmc6f-complete`. It is immutable plan input, not implementation. Final compaction will rewrite
  only the local JMC6G range rooted at this planning commit and will prove ancestry through exact
  `jmc6f-complete`; if §9's wording cannot safely permit that separation, compaction stops rather
  than rewriting the pushed planning commit.
- **Operator/live-smoke state:** no operator database, media, schedule, push, activation, or
  external coordination was used. Only the owned PostgreSQL cluster, owned test servers, and
  synthetic frontend fixtures ran. Live tool/media smoke remains pending for the applicable G3–G6
  gates.
- **Tree and ancestry:** pre-edit implementation base `d2dd72a`, tree `faa67230`; branch/upstream
  identical and clean. This timeline is the first JMC6G worktree change.

## Phase G0 checkpoint — closure freeze and intentional-red inventory

- Added `tests/fixtures/jmc6g/enabled_definition_closure.json`, a checked-in 43-entry manifest that
  names every enabled definition and freezes its handler, result/outcome contract, retry policy,
  timeout, configuration ownership, progress stages, process and large-I/O reachability, projection,
  artifacts, presenter, and required test coverage. `test_jmc6g_closure_manifest.py` proves exact
  enabled-registry and handler-set equality plus required per-leaf fields.
- Added four canonical submission/delivery/persistence/consumer contracts in
  `test_pgqueuer_delivery.py`. The focused run is exactly **1 passed / 4 intentional failures**:
  non-mutation `no_change` is forced to job `succeeded`; attempt-log success is sealed before the
  terminal transaction; a handler-selected 999-second retry bypasses definition delays 5/30; and a
  one-second definition timeout does not wrap handler execution.
- Complete comparison is exactly **1310 passed / 4 intentional failures in 92.22s**. These are the
  same four focused failures and no baseline regression. Ruff initially found import ordering in
  the two touched tests; repository Ruff fixed those imports, after which the phase files are ready
  for the clean lint/diff checkpoint. The terminal-order red test also exposes the expected pending
  log-sink task caused by monkeypatching the seal at the currently incorrect boundary; G1 moves the
  boundary and removes that symptom.
- **Current phase:** G0 checkpoint ready for configured-author commit; G1 begins immediately after
  the commit and makes the four terminal/retry/timeout contracts green through one typed terminal
  authority and definition-owned execution policy.
- **Exact next steps:** run post-format Ruff and `git diff --check`; commit the timeline, manifest,
  manifest contract, and four intentional-red contracts; record the commit hash/tree; implement the
  typed terminal decision and make canonical durability precede truthful log sealing; then apply
  retry classification, bounded attempts/delays, and execution timeout without acknowledging kernel
  uncertainty.
- **Deviation and justification:** G0 is intentionally red because the authoritative plan requires
  the behavioral failures to be frozen before implementation. The original 1309-test baseline is
  wholly green; only the four named contracts are red, and later phase gates must return the complete
  suite to zero-green.
- **Operator/live-smoke state:** unchanged; no operator or external state touched. Live smoke remains
  pending for G3–G6.
- **Tree and ancestry:** worktree is based on pushed plan commit `d2dd72a`; the G0 commit hash and
  resulting tree are recorded immediately below after the configured-author commit.

### G0 committed state

- Configured-author phase commit: `04b06bb72fe3181140e21894f0437278c049aa8b`
  (`freeze execution closure failures`).
- Commit tree: `2599e74d5b4cbac781bc8e3ad54d101d579e8e60`; sole parent is pushed plan commit
  `d2dd72a4e01ca93883ae04b2af12f94c7be29f3a`.
- Author is the configured repository identity, Gautam Chaudhri
  `<gautam.chaudhri@gmail.com>`. Worktree was clean immediately after commit.
- **Current phase:** G1 — typed terminal decision and truthful finalization is in progress.
- **Exact next steps:** add the typed terminal-decision authority and route result validation,
  canonical job/attempt/dispatch disposition, transport acknowledgement, log summary, attention,
  and workspace disposition through it; enforce definition-owned retry/timeout; make the four G0
  contracts green; add the complete terminal mapping matrix and uncertainty/hold tests; run every G1
  gate, commit, append evidence, and continue to G2.

## Phase G1 checkpoint — terminal truth and finalization ordering

- Added the typed `TerminalDecisionPolicy`/`TerminalDecision` authority. Every enabled definition
  owns an exact closed mapping from its current result contract to canonical job outcome, attempt
  outcome, dispatch disposition, acknowledgement safety, attention, bounded log summary, and
  workspace cleanup/quarantine. `BuiltInResultV1` is now closed; poster `review_required` is the
  sole explicit alias and maps to canonical `partially_succeeded`. Registry startup rejects missing,
  open, extra, or mismatched outcome mappings.
- Removed the `MutationResultV1` special case and generic forced-success path. Returned domain
  failure/unsafe completes the attempt and transport delivery while preserving the domain job
  outcome; returned cancellation and supersession use their distinct attempt/dispatch vocabularies.
  Attention is persisted by the decision rather than reconstructed by the presenter.
- Reordered finalization to stop descendants, derive the typed decision, commit the fenced terminal
  transaction and batch projection, seal the log with both semantic and attempt outcomes, register
  canonical evidence, then clean/quarantine the workspace and acknowledge. Stale ownership,
  invalid mapping, and terminal-write uncertainty seal only interrupted/unsafe evidence, quarantine,
  and raise `DeliveryRejectedError` so PgQueuer holds rather than acknowledges.
- Canonical artifact/log evidence failure after terminal durability no longer reruns product work.
  It appends bounded `artifact.failed`/`log.truncated` degradation evidence. Virtual artifact
  registration is idempotent, and `repair_terminal_virtual_artifacts(limit<=200)` is invoked during
  worker startup to restore missing result/error evidence without re-execution.
- Focused terminal/registry/presenter/batch tests are green: 38/38 for the primary mapping/order
  set, 20/20 for frozen registry/mutation contracts, 9/9 for returned outcomes/conflict/order, and
  4/4 for closure/evidence repair. The first complete comparison exposed two unrelated expected
  freeze-order updates; both were corrected. The final comparison is **1322 passed / 2 intentional
  pre-frozen G2 failures in 101.34s**. The only red contracts are definition-bounded retry delay and
  handler execution timeout; no G1 regression remains. They are resolved immediately in G2.
- Ruff over `marquee tests scripts` and `git diff --check` pass. Alembic remains `0008_jmc6e` head
  and `alembic check` reports no operations. OpenAPI remains 199 paths with SHA-256
  `4f70e9e7c5cbd90e5359fc8fadde37ad48272756e203a4a1f899b67459f9c7a4`; generated TypeScript
  remains `96eedda857b02f9e54ae402bac3da5ea01c55438816e210e3eb8c4c73aa36d89` and both drift checks
  pass. Frontend check is 0/0, lint clean, unit 111/111, build passes with the preexisting plugin/chunk
  notices, and Chromium Playwright/axe is 11/11 with the preexisting color-variable notices.
- **Deviation and justification:** the complete suite cannot be zero-green between G1 and G2
  because G0 intentionally froze the two G2 contracts before implementation, as required by the
  authoritative phase sequence. They are not G1 regressions and execution continues directly into
  G2; the G2 gate must restore the complete suite to zero-green.
- **Operator/live-smoke state:** no operator state or real media/tool execution; deterministic
  database, API, presenter, batch, evidence-outage, and browser fixtures only. Applicable live smoke
  remains pending.
- **Current phase:** G1 checkpoint ready for configured-author commit; G2 begins immediately after
  commit.
- **Exact next steps:** enforce the definition classifier/budget/delay against durable attempt
  audit, wrap handler execution in the definition timeout with owned-process shutdown and unsafe
  publication reconciliation, replace handler-selected delay loops, complete all 43 configuration
  snapshots and remove mutable handler reads, then run the complete zero-green G2 gate.

### G1 committed state

- Configured-author phase commit: `89b755e42e7a6cb149c0e55ea410e810ee1897ae`
  (`enforce terminal execution truth`).
- Commit tree: `764b80db0a442f6c48dd53348d8cf29873ab8523`; sole parent is G0 commit
  `04b06bb72fe3181140e21894f0437278c049aa8b`.
- Author is the configured repository identity, Gautam Chaudhri
  `<gautam.chaudhri@gmail.com>`. Worktree was clean immediately after commit.
- **Current phase:** G2 — bounded retry, execution timeout, and configuration authority is in
  progress.

## Phase G2 checkpoint — definition-owned execution policy and immutable configuration

- Every enabled definition now carries a required failure classifier, bounded retry policy,
  handler execution timeout, configuration-key set, and explicit `snapshot` or `audited_empty`
  configuration audit. Delivery applies the classifier to all handler exceptions, derives delays
  solely from the durable attempt number and definition policy, terminalizes exhausted/permanent
  failures, and holds/quarantines unsafe kernel uncertainty. Handler timeouts wrap the actual
  execution body and cannot bypass owned-process shutdown/reconciliation.
- Letterbox and Dolby Vision probes now return a typed transient execution error rather than
  choosing a PgQueuer delay. Canonical delivery tests prove handler delay hints are ignored,
  maximum attempts are enforced, timeout becomes a bounded definition retry, and kernel
  uncertainty yields job `unsafe`, interrupted attempt, failed dispatch, hold, and quarantine.
  Returned domain failure/unsafe remains a normal typed terminal result under the G1 authority.
- The closure registry now snapshots the exact public, database-owned execution catalogs for every
  enabled pipeline/subtitle definition and explicitly audits all other enabled definitions empty.
  Subgen path/profile/model/mode/naming behavior consumes `ExecutionContext.configuration`; the
  only remaining global reads are restart-owned connection/timeout capabilities. An AST closure
  guard rejects live `configuration_provider` access and database-owned setting attributes in all
  enabled handler modules.
- Canonical submission/delivery proves an enqueue-time configuration value survives a provider
  change and retry unchanged in both attempts and the durable job snapshot. The frozen 43-leaf
  manifest resolves its named catalog sets back to exact registry keys and is now marked
  `g2_policy_configuration_closed`.
- Correctly targeted focused verification is **72 passed**. The mandatory complete comparison is
  **1327 passed, 0 failed, 0 skipped, 0 xfail/xpass, 0 warnings in 95.91s**. Ruff over
  `marquee tests scripts` and `git diff --check` pass. Alembic current/head are `0008_jmc6e` and
  `alembic check` reports no operations. Schema fingerprints remain Marquee
  `7af29195f6966074ebd295c34b35e9bd4f092e39ecfa55648a9e9c69e24ab528` and PgQueuer
  `19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a`.
- OpenAPI remains 199 paths at
  `4f70e9e7c5cbd90e5359fc8fadde37ad48272756e203a4a1f899b67459f9c7a4`; generated TypeScript
  remains `96eedda857b02f9e54ae402bac3da5ea01c55438816e210e3eb8c4c73aa36d89`, with both drift checks
  clean. Frontend check is 0/0, lint clean, unit 111/111, build passes with the inherited
  plugin/chunk notices, and Chromium Playwright/axe is 11/11 with inherited color-variable notices.
- **Deviation and recovery:** initial focused/full invocations exported `DATABASE_URL`, but Marquee
  reads `DB_URL`; those results are excluded. They reached the configured `.env` PostgreSQL on
  port 5432, created and removed only pytest's UUID-named isolated schemas, and read backup schema
  markers; migration database creation was denied. They did not alter public application rows,
  media, schedules, or operator configuration. A second mistake exported the synthetic `DATA_DIR`
  during that excluded full run. The timeline contract exposed both errors. All recorded evidence
  above was rerun with `DB_URL=postgresql+asyncpg://marquee@localhost:55453/marquee_test`, the owned
  cluster's superuser/CREATEDB and schema markers were read back, and default repository `DATA_DIR`
  was preserved. No excluded result is used as certification evidence.
- **Operator/live-smoke state:** beyond the isolated-schema deviation above, no operator state or
  real media/tool execution occurred. G2 uses owned PostgreSQL and synthetic process/configuration
  fixtures. Applicable native-tool/media smoke remains pending for G3–G6.
- **Current phase:** G2 checkpoint ready for configured-author commit; G3 begins immediately after
  commit.
- **Exact next steps:** complete subject-aware semantic progress for all enabled long-running
  definitions; stream native FFmpeg/mkvmerge progress during execution; route every enabled product
  subprocess through tracked containment; replace blocking large-file copy/hash/backup work with
  cancellation-aware chunked execution I/O; prove each path through canonical delivery and
  presenter/batch consumers; run the full G3 gate and continue.

### G2 committed state

- Configured-author phase commit: `27d4d6fa08e15ab4fb98c5a20ffa57344f32014b`
  (`bound retries and execution configuration`).
- Commit tree: `842e6ff35afbf654c4b8775bfdf7920fdb9e98e0`; sole parent is G1 commit
  `89b755e42e7a6cb149c0e55ea410e810ee1897ae`.
- Author is the configured repository identity, Gautam Chaudhri
  `<gautam.chaudhri@gmail.com>`. Worktree was clean immediately after commit.
- **Current phase:** G3 — semantic progress, tracked processes, and cancellation-aware execution
  I/O is in progress.

## Phase G3 checkpoint — semantic progress, tracked processes, and bounded execution I/O

- Every admitted attempt now owns one typed `ExecutionProgress` authority with stable overall and
  current scopes plus its frozen subject snapshot. Delivery emits definition-owned first/final
  stages, enabled long-running handlers emit semantic intermediate stages, and the presenter/API
  contract now carries native progress metrics. Projection Room renders server-reported byte
  progress without estimating missing values.
- FFmpeg and mkvmerge stdout is consumed while the tracked process is running. FFmpeg jobs request
  `-progress pipe:1 -nostats`; the closed adapters tolerate partial/native packets and emit only
  truthful samples. `pg_dump` is now in the closed launcher catalog with an allowlisted
  `PGPASSFILE` environment, so canonical backup no longer escapes attempt containment.
- Added `ExecutionIO`, which checks cancellation and durable fence ownership between 1 MiB chunks,
  yields the event loop, reports byte metrics, and removes partial destinations on cancellation or
  fence loss. Enabled media staging, hashing, backup, restore, sidecar, generation, poster, Dolby
  Vision, and letterbox paths use this authority. Publication performs full async preflight hashes
  and metadata identity checks at the atomic boundary instead of rehashing large files while the
  fenced publication transaction is held.
- Managed-data archive reads now check cancellation between tar reads and clean their temporary
  backup root. Canonical backup routes `pg_dump` through the tracked launcher; legacy offline
  backup/restore entry points retain their deliberately synchronous operator implementation.
- The frozen 43-definition manifest is now `g3_progress_process_io_closed`. Its static reachability
  guard rejects raw subprocess, `read_bytes`, `write_bytes`, and `FilesystemBoundary.copy_file`
  shortcuts from enabled handler modules. A canonical submit/deliver/API test proves fenced I/O
  byte metrics and the frozen subject reach the typed presenter, catching and correcting an
  initially invalid sessionless fence callback that direct handler tests could not detect.
- Focused verification is green: 59 media/publication/I/O tests, 37 backup/progress tests, 136
  closure/delivery/presenter/batch/process tests, the 40-test presenter contract set, and the final
  12-test static/canonical closure comparison. The mandatory complete suite is **1338 passed, 0
  failed, 0 skipped, 0 xfail/xpass, 0 warnings in 95.51s**. Its first comparison found three stale
  frozen/direct-harness expectations (the two new execution-context authorities, the additional
  semantic progress event, and a maintenance progress stub); all were updated and the complete
  suite rerun from zero.
- Ruff over `marquee tests scripts` and `git diff --check` pass. Alembic current/head remain
  `0008_jmc6e`; `alembic check` reports no operations. Database schema fingerprints remain Marquee
  `7af29195f6966074ebd295c34b35e9bd4f092e39ecfa55648a9e9c69e24ab528` and PgQueuer
  `19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a`.
- OpenAPI remains 199 paths and intentionally changes for typed `CompactProgress.metrics`, with
  SHA-256 `312b5952addc871fcc55c6311a627bafa594cce8258bed92e381a194e5654e12`.
  Generated TypeScript is deterministic at
  `f4f3c4c334190d8c676c365d771ecf473ad77d512d4f372671b35079fb93482b`; export and generated-type
  drift checks pass. Frontend check is 0/0, lint clean, unit **112/112**, build passes with inherited
  plugin/chunk notices, and Chromium Playwright/axe is **11/11** with inherited color-variable
  notices.
- **Deviation and recovery:** sandboxed PostgreSQL socket access failed during the first focused
  invocation even though the owned server remained healthy; the required checks were rerun with
  explicit approval against only port 55453. One Vitest invocation used unsupported Jest option
  `--runInBand`; it ran no tests and was replaced by the canonical command. The pre-commit
  generated-type check initially observed the intentional uncommitted generated diff; after staging
  that generated file, the same deterministic generation check passed. None is certification
  evidence.
- **Operator/live-smoke state:** FFmpeg 8.1.2 generated a one-second FFV1 Matroska fixture while
  emitting native `out_time_us`/`progress=end`; mkvmerge 99.0 remuxed it while emitting 89% and 100%
  GUI progress; ffprobe verified duration 1.000000 and size 11196 bytes. `pg_dump` is 18.3 and its
  tracked canonical fixture proves the allowlisted password-file lifecycle. Smoke files are confined
  to `/tmp/marquee-jmc6g-g3-smoke-*`; no operator media or product state was changed.
- **Current phase:** G3 checkpoint ready for configured-author commit; G4 begins immediately after
  commit.
- **Exact next steps:** persist post-mutation audio/subtitle inventory and managed-sidecar bindings;
  register user-relevant sidecars, backups, reports, and validation evidence as `JobArtifact` rows;
  implement physical log/artifact retention before canonical row deletion with schedule/CLI/API
  visibility; close the G4 manifest and run its complete gate.

### G3 committed state

- Configured-author phase commit: `feb740f2fb4aa39858762866855413e5e2d6134b`
  (`stream fenced execution progress`).
- Commit tree: `a0c72d8457592d49579ce8fab6002724909abdef`; sole parent is G2 commit
  `27d4d6fa08e15ab4fb98c5a20ffa57344f32014b`.
- Author and committer are the configured repository identity, Gautam Chaudhri
  `<gautam.chaudhri@gmail.com>`. Worktree was clean immediately after commit.
- **Current phase:** G4 — durable mutation inventory, artifact evidence, and physical retention is
  in progress.

## Phase G4 checkpoint — durable domain evidence and physical retention

- Audio/subtitle mutation probes now launch both ffprobe and mkvmerge through the attempt-owned
  tracked launcher. Successful track, policy, embed, and generation publication persists the actual
  post-mutation `SubtitleInventory`/`SubtitleTrack` view only after a current-attempt fence check;
  inventory rows carry job, attempt, and fence provenance. Request planning consumes that durable
  inventory and refuses missing or stale scans instead of spawning untracked API-side probes.
- Generated and extracted managed sidecars now create subject-aware
  `ManagedSubtitleBinding` rows with job/attempt/fence provenance. Binding evidence survives media
  retirement by nulling the media reference. Generation truth is target-granular: a published
  sidecar followed by a failed requested embed is `partially_succeeded`, with the sidecar target
  succeeded and embed target failed.
- Canonical terminal delivery recursively registers typed backup evidence and retained copies of
  managed sidecars as physical `JobArtifact` rows, plus a materializable validation artifact that
  points to the canonical result. Sidecar evidence is copied into immutable artifact storage so its
  retention expiry cannot remove the still-bound product sidecar; product backups remain the
  physical recovery objects. Evidence-registration degradation is durable and repairable without
  rerunning product work.
- Artifact and log expiry now claims `expiring`, deletes and verifies the confined physical object,
  and only then marks the row `expired` with an explicit disposition. Storage failures return the
  row to an available/sealed retryable state. Full job retention refuses row deletion while physical
  evidence remains; the production-gated evidence-retention schedule and the
  `expire-job-evidence` operator command perform evidence-only expiry. Operations exposes bounded
  overdue artifact/log counts and oldest timestamps.
- Migration `0009_jmc6g` adds inventory/binding provenance and the transient evidence state. Alembic
  current/head are the sole `0009_jmc6g` head and autogenerate reports no operations. Schema
  fingerprints are Marquee
  `9b6747b73e92cc7621df81aef7391f883aafea0df6b40daa8c62e17871a7c90d` and PgQueuer
  `19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a`.
- The executable 43-definition closure manifest is now
  `g4_domain_evidence_and_retention_closed`; it rejects any enabled entry that still describes a
  missing or incomplete projection or artifact path. Serena reachability confirms native mutation
  probes are reachable only from tracked execution handlers (plus their focused test), while API
  planning consumers reach only durable inventory reconstruction.
- Focused gates are green: canonical mutation routes **4/4**; artifact/evidence/delivery **64/64**;
  JMC5 mutation/maintenance/freeze compatibility **118/118**; closure manifest **5/5**; earlier G4
  domain, retention, schedule, migration, readiness, JMC1–4, and JMC5C/JMC6 groups are all green.
  The mandatory complete suite is **1346 passed, 0 failed, 0 skipped, 0 xfail/xpass, 0 warnings in
  98.25s**.
- Ruff over `marquee tests scripts`, `git diff --check`, API export, and generated-type drift checks
  pass. OpenAPI remains 199 paths at
  `240709d6c592586a973bba09f562352526868e60bb3781726859c27824291aa4`; generated TypeScript is
  deterministic at `088cda349ecaff9a6deaa3d26f689551a489ef00dde379149fb3a05c98e38fc3`.
  Frontend check is 0/0, lint clean, unit **112/112**, build passes with inherited plugin/chunk
  notices, and the final Chromium Playwright/axe gate is **11/11**.
- **Deviation and recovery:** one complete backend invocation accidentally omitted the recorded
  `DB_URL` and hit the unrelated default PostgreSQL service; its 1327 passes, eight failures, and 11
  setup errors are discarded, and the exact owned-database command then passed from zero. The first
  browser invocation was blocked from binding localhost in the sandbox; after explicit local-bind
  approval, one activity-card axe scan transiently observed dim inherited colors. Its isolated
  2/2 rerun and the subsequent complete 11/11 rerun passed without a source change; only the final
  complete run is certification evidence.
- **Operator/live-smoke state:** against only the owned database and
  `/tmp/marquee-jmc6g-data.jCy7Wd`, `python -m marquee.maintenance expire-job-evidence --limit 1`
  completed successfully and truthfully reported zero claims/deletions for both artifacts and logs.
  No operator database, media, evidence, schedule, or product state was changed.
- **Current phase:** G4 checkpoint ready for configured-author commit; G5 begins immediately after
  commit.
- **Exact next steps:** correct stale orphan candidate selection, listener-health reporting, and
  bounded class-local queue rank through canonical diagnostics/API consumers; close the G5 manifest
  and run the complete phase gate.

### G4 committed state

- Configured-author phase commit: `cd63729c90f4b811433c98e434fc603eb13ce597`
  (`persist mutation evidence and retention`).
- Commit tree: `1cd567599d0cf51a17ffd98eb0bb8050b0d100dd`; sole parent is G3 commit
  `feb740f2fb4aa39858762866855413e5e2d6134b`.
- Author and committer are the configured repository identity, Gautam Chaudhri
  `<gautam.chaudhri@gmail.com>`. Worktree was clean immediately after commit.
- **Current phase:** G5 — bounded diagnostics and operational truth is in progress.

## Phase G5 checkpoint — bounded recovery and diagnostics truth

- Startup orphan selection now applies runtime freshness before its 1..500 bound: only absent,
  stopped, or heartbeat-expired runtime ownership enters the ordered candidate batch. Healthy recent
  attempts therefore cannot crowd an older stale attempt out, and a fresh attempt is never touched
  merely to increment an `active` counter.
- Operations listener health now requires a fresh, ready `worker` runtime and reports
  `runtime_instances.worker` as its source with that worker heartbeat. A scheduler-only topology is
  still truthfully active and scheduler-present, but it no longer fabricates worker/listener health.
- Queue rows now receive exact execution-class-local rank from a separately bounded active queued
  window ordered by eligibility/creation/ID. Rank continues across cursor pages, ignores unrelated
  classes, and is omitted beyond the 1,000-row class window instead of restarting at one on every
  page. The maximum-page API budget increases by exactly one bounded query per represented class;
  no history document or raw transport identity is exposed.
- Focused recovery/read/API/Operations verification is **74 passed**, including fresh rows ahead of
  a stale candidate with `limit=1`, second-page ranks 11..20, a 1,002-row omission boundary, fresh
  worker health, and scheduler-only rejection. The executable closure manifest is now
  `g5_bounded_diagnostics_closed` and its **5/5** contract tests pass.
- The final mandatory complete suite after the manifest update is **1350 passed, 0 failed, 0
  skipped, 0 xfail/xpass, 0 warnings in 101.04s**. Ruff over `marquee tests scripts` and
  `git diff --check` pass.
- Alembic current/head remain the sole `0009_jmc6g` head and autogenerate reports no operations.
  Schema fingerprints remain Marquee
  `9b6747b73e92cc7621df81aef7391f883aafea0df6b40daa8c62e17871a7c90d` and PgQueuer
  `19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a`.
- OpenAPI remains current at 199 paths and
  `240709d6c592586a973bba09f562352526868e60bb3781726859c27824291aa4`; generated TypeScript is
  deterministic at `088cda349ecaff9a6deaa3d26f689551a489ef00dde379149fb3a05c98e38fc3`.
  Frontend check is 0/0, lint clean, unit **112/112**, build passes with inherited plugin/chunk
  notices, and Chromium Playwright/axe is **11/11**.
- **Deviation and recovery:** the first focused comparison expected the old behavior of counting a
  healthy row inside the orphan candidate batch and reused one process identity for four synthetic
  runtimes. The fixture was corrected to distinct process identities and the assertion now freezes
  the locked pre-filter behavior; the complete focused set then passed. No product behavior was
  weakened to retain the stale expectation.
- **Operator/live-smoke state:** G5 has no external tool or product-state smoke. Its topology,
  multi-page, and large-history checks ran against only synthetic rows in the owned disposable
  database; pytest cleanup removed them. No operator runtime, schedule, queue, or media was changed.
- **Current phase:** G5 checkpoint ready for configured-author commit; G6 integrated certification
  begins immediately after commit.
- **Exact next steps:** run the complete enabled-definition execution/evidence certification,
  fresh-target schema/model equivalence, PgQueuer verification, static reachability and generated
  contracts; curate ByteRover; complete the timeline; then perform the final-only recovery and
  compaction protocol if every stop gate remains clear.

### G5 committed state

- Configured-author phase commit: `df813631cf9d4121d3003b507d74190c6a11b458`
  (`bound recovery diagnostics`).
- Commit tree: `fcd41ee6035dfcc315228269abc35a86fe4be351`; sole parent is G4 commit
  `cd63729c90f4b811433c98e434fc603eb13ce597`.
- Author and committer are the configured repository identity, Gautam Chaudhri
  `<gautam.chaudhri@gmail.com>`. Worktree was clean immediately after commit.
- **Current phase:** G6 — integrated execution/evidence certification and final-only compaction is
  in progress.

## Phase G6 checkpoint — integrated execution and evidence certification

- The executable closure document is frozen at
  `g6_integrated_execution_evidence_certified`: all **43 enabled definitions** have one registered
  handler and explicit result model/outcomes, retry authority, timeout, immutable configuration
  keys, subject-aware progress stages, tracked external-tool coverage, cancellation-aware large-file
  I/O coverage, durable projection/evidence behavior, presenter, and canonical-path tests. The other
  **19 registered types** remain deliberately disabled, parent-only, or reserved; no poster/ML
  implementation work assigned to JMC6H was pulled forward.
- One typed `TerminalDecision` authority now controls canonical job and attempt outcome, dispatch
  disposition, transport acknowledgement, log summary, operator attention, and workspace
  disposition. Returned domain failure/unsafe outcomes remain durable domain truth; exceptions,
  cancellation uncertainty, lost fences, and persistence uncertainty hold or quarantine instead of
  fabricating success. Canonical terminal persistence precedes the terminal log write.
- Retry and timeout ownership is definition-local and frozen: **29 mutation/maintenance definitions**
  permit one attempt with no retry delay; **14 read/analysis definitions** permit three attempts with
  delays of 5 and 30 seconds. `system_noop` has a 30-second handler timeout and the other **42**
  definitions have a 86,400-second bound. Delivery enforces these values rather than reading mutable
  global settings or result-model-specific terminal rules.
- Immutable execution configuration is complete: `pipeline_execution_v1` belongs to the three taste
  jobs, learned-head training, and poster analysis; `subtitle_execution_v1` belongs to subtitle scan
  and policy audit plus every enabled audio/subtitle mutation. Every other enabled definition is
  explicitly audited as configuration-independent. Handlers consume the submission snapshot only.
- Every enabled long-running definition has semantic subject-aware progress. Native FFmpeg
  `-progress` and mkvmerge progress stream while the child runs; every product subprocess is launched
  through attempt-owned tracked containment. Backup, copy, publication, hashing, and archive paths use
  cancellation-aware chunked `ExecutionIO` rather than event-loop-blocking large-file operations.
- Serena final reachability found `TerminalDecision` consumers only in delivery, fenced persistence,
  and terminal policy; `ExecutionIO` is injected by delivery and reaches backup/publication paths;
  physical evidence expiry is reachable only through the bounded maintenance handler/CLI and its
  tests. The integrated canonical submission/delivery/persistence/evidence/batch/presenter gate is
  **202 passed in 18.04s**.
- Fresh-install and forward-upgrade rehearsals used disposable databases
  `jmc6g_fresh_190103` and `jmc6g_forward_190103`. Both reached Marquee `0009_jmc6g`, PgQueuer 1.1.1,
  48 tables, column signature `bbab95721a2b9941575c4fbe16c3f39f`, index signature
  `ed8a0d58550cd1c2b465f0a3a9ee00c1`, and constraint signature
  `b2ddcaf92899b49599247bc5005dbc75`; both rehearsal databases were then dropped. Migration tests are
  **14/14**, Alembic current/head are the sole `0009_jmc6g` head, and autogenerate reports no
  operations.
- Final schema fingerprints are Marquee
  `9b6747b73e92cc7621df81aef7391f883aafea0df6b40daa8c62e17871a7c90d` and PgQueuer
  `19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a`. OpenAPI remains 199 paths at
  `240709d6c592586a973bba09f562352526868e60bb3781726859c27824291aa4`; generated TypeScript remains
  deterministic at `088cda349ecaff9a6deaa3d26f689551a489ef00dde379149fb3a05c98e38fc3`.
- The final mandatory complete suite is **1350 passed, 0 failed, 0 skipped, 0 xfail/xpass, 0 warnings
  in 102.56s**. Ruff over `marquee tests scripts`, `git diff --check`, deterministic API/type checks,
  and model/migration checks pass. Frontend check is 0/0, lint clean, unit **112/112**, build passes
  with only inherited plugin/chunk notices, and Chromium Playwright/axe is **11/11 in 20.3s**.
- **Deviation and recovery:** the fresh/forward rehearsal uses explicit disposable database names
  because the owned main test database must remain available for the complete gate. Both targets were
  schema-signature equivalent and removed after verification. No locked decision, warning, skip,
  contract drift, or unresolved failure remains.
- **Operator/live-smoke state:** FFmpeg 8.1.2 streamed native progress for a one-second FFV1 encode;
  mkvmerge 99.0 streamed native progress; ffprobe verified the result; and pg_dump 18.3 ran under
  tracked containment. Evidence-retention CLI smoke truthfully claimed/deleted zero rows in the owned
  disposable environment. No operator database, media, runtime, schedule, or product state changed.
- **Current phase:** G6 checkpoint ready for its configured-author commit. The only remaining work is
  the locked final-only history protocol.
- **Exact next steps:** commit this certified G6 tree; append and commit the terminal timeline ledger;
  verify clean linear configured-author unpushed ownership and ancestry; create and verify timestamped
  recovery branch/tag plus repository-external bundle; soft-reset to the exact plan base; create the
  single compact commit and prove tree identity; then create annotated local tag `jmc6g-complete`.

### G6 committed state

- Configured-author phase commit: `cae7c6d80c217cc7a6a2851ee7078f378859820b`
  (`certify execution evidence closure`).
- Certified product tree: `a1ce428a5ea938ccea72bd27ec03d22aa0536964`; sole parent is G5 commit
  `df813631cf9d4121d3003b507d74190c6a11b458`.
- Author and committer are the configured repository identity, Gautam Chaudhri
  `<gautam.chaudhri@gmail.com>`. Worktree was clean immediately after commit.
- All G0 through G6 implementation and certification gates are complete. This terminal ledger is
  the final allowed content change before compaction; after its configured-author commit the
  timeline is immutable.

## Terminal pre-compaction ledger

- Predecessor certification is annotated tag `jmc6f-complete`, dereferencing to
  `d8214fd39f59de2f0857346995ef77910078deb8` with tree
  `009dfc1783c004ec3b2b374e29ae55d3a92933b0`. The immutable pushed JMC6G plan base is its child
  `d2dd72a4e01ca93883ae04b2af12f94c7be29f3a` on `origin/job-manager`.
- The configured-author, first-parent JMC6G phase range after the plan base is, in order:
  `04b06bb72fe3181140e21894f0437278c049aa8b`,
  `89b755e42e7a6cb149c0e55ea410e810ee1897ae`,
  `27d4d6fa08e15ab4fb98c5a20ffa57344f32014b`,
  `feb740f2fb4aa39858762866855413e5e2d6134b`,
  `cd63729c90f4b811433c98e434fc603eb13ce597`,
  `df813631cf9d4121d3003b507d74190c6a11b458`, and
  `cae7c6d80c217cc7a6a2851ee7078f378859820b`, followed only by the timeline-only ledger commit
  containing this section. No phase commit has a merge parent or a non-configured author/committer.
- Complete certification evidence is the G6 checkpoint above: backend **1350/1350**, integrated
  **202/202**, migration **14/14**, frontend unit **112/112**, browser/axe **11/11**, Ruff,
  Alembic/model, OpenAPI/type, build, and whitespace gates all green without a new failure, skip,
  xfail, warning, or contract drift.
- ByteRover final curation was queued as task `0476ab75-362f-4337-95a5-ac79115f0a87`; Serena
  reachability is recorded in the G6 checkpoint. No required tool is unavailable.
- **Authorized terminal operation:** after this ledger is committed, resolve its exact tip and tree;
  re-prove ownership, linear ancestry, single-worktree safety, absence of Git locks, and that no
  remote contains the tip. Create a timestamped recovery branch and annotated recovery tag at that
  exact tip, plus a verified bundle outside the repository. Only then soft-reset to exact plan base
  `d2dd72a4e01ca93883ae04b2af12f94c7be29f3a`, commit the unchanged tree once as
  `jmc6g: close execution truth and evidence`, prove tree/parent/ref/bundle identity and a clean
  worktree, and create annotated local tag `jmc6g-complete`. Do not push or edit this timeline after
  compaction.

## JMC6H Phase H0 — predecessor verification and real-product-contract freeze

JMC6H is now the active implementation plan. It replaces the placeholder poster and ML product
behavior with the real Marquee pipeline and native ML artifacts, converges artifact/consumer
authority, removes remaining obsolete lifecycle code, and certifies every enabled definition from
user action to visible product effect. No push, activation, operator-state mutation, or final
compaction is authorized until H6 certifies.

### Verified predecessor state

- Annotated `jmc6g-complete` resolves to compact commit `93a695b5843ffaaf26eae98e67669538c32b1a56`
  (`jmc6g: close execution truth and evidence`), tree `7f6283146f7a25b3463806e421020e98e5baab93`,
  sole parent pushed plan base `d2dd72a4e01ca93883ae04b2af12f94c7be29f3a`. Author and committer are
  the configured identity Gautam Chaudhri `<gautam.chaudhri@gmail.com>`. `git bundle verify` on
  `/tmp/marquee-jmc6g-20260718T192421Z.bundle` reports a complete SHA-1 history; the recovery branch
  `recovery/jmc6g-20260718T192421Z` (f3a1cc2) and annotated tag
  `recovery/jmc6g-pre-squash-20260718T192421Z` (2a09d25) resolve to tree
  `7f6283146f7a25b3463806e421020e98e5baab93`, byte-identical to the compact HEAD tree. All earlier
  recovery refs/bundles (jmc4b–jmc6f) are retained and unchanged.
- Branch `job-manager` tracks `origin/job-manager` and is exactly one commit ahead (the unpushed
  jmc6g compaction). One worktree; clean before the first H0 change. This is the exact JMC6H plan
  base for the final-only compaction range.
- JMC6H is the immediate successor named in the JMC6G doc §10 handoff. This is the first JMC6H
  worktree change; no prior H phase work exists.

### Owned baseline environment and tools

- Owned disposable PostgreSQL 18.3 cluster: `initdb -U marquee --auth=trust`, data root
  `/tmp/marquee-jmc6h-pg.l3z68M` (path saved to `/tmp/jmc6h-pgdata-path.txt`), listening
  `127.0.0.1:55460`, database `marquee_test`. Port 5432 (the operator database) and the retired
  JMC6G cluster were not targeted. `DB_URL=postgresql+asyncpg://marquee@127.0.0.1:55460/marquee_test`
  is the only certification database.
- `python -m marquee.db_migration` applied Alembic `0001_jmc1` → `0009_jmc6g` and installed/verified
  PgQueuer 1.1.1 durable into `public`; `schema_contracts`, `job_events`, `jobs`, and the four
  `pgqueuer*` tables are present. DATA_DIR is left at the repository default; `conftest.py` redirects
  it per session, preserving the `test_pipeline_run_root_is_inside_data` contract.
- Tools: venv Python 3.13.14, pytest, Ruff 0.15.17, Alembic 1.18.4, PgQueuer 1.1.1, PostgreSQL 18.3,
  FFmpeg/ffprobe 8.1.2, mkvmerge 99.0, pg_dump 18.3. Hardware: NVIDIA RTX 3070 8 GiB (driver 595.80).
  Local ML models present under `marquee/ml/models/`: `clip-vit-b-32.onnx`, `dinov2-vits14.onnx`,
  `sa_0_4_vit_b_32_linear.{npz,pth}` (aesthetic), `scrfd_500m_bnkps.onnx` (face), `yolo11n.onnx`
  (person). PaddleOCR/TMDB live capability remains to be smoke-checked in H2/H6.
- Serena MCP, ByteRover MCP, and RTK are operational. The local `brv` CLI is not installed; the
  required ByteRover MCP query path was used.

### Verified zero-green baseline (with recovery)

- **Deviation and recovery:** the first full run against a freshly `initdb`'d cluster that had *not*
  had `db_migration` applied produced **9 failed, 1341 passed**. All nine failed identically with
  `asyncpg UndefinedTableError: relation "schema_contracts"/"job_events" does not exist`
  (`test_backup.py` ×8, `test_jmc1_readiness.py` ×1): those tests read the `public` schema through
  `pg_dump`/fresh admin connections, not the per-test `test_<uuid>` schema that `conftest.py`
  `create_all`s into. Root cause was the missing public-schema migration on the disposable database,
  not a source regression on unmodified `jmc6g-complete`. After `python -m marquee.db_migration`, the
  same 31 backup/readiness tests pass and the complete suite is **1350 passed, 0 failed, 0 skipped,
  0 xfail/xpass in 95.03s**, matching the JMC6G certified baseline exactly. Only migrated-cluster
  results are certification evidence.

### Reproduced placeholder product defects (JMC6H targets)

- `poster_pipeline.execute_poster_pipeline` fabricates candidates from `hashlib.sha256(...)` with a
  fixed `score=0.5` and `decision="accepted"`, emits synthetic stage progress, and registers only a
  JSON `poster-analysis-report.json`. It writes **no** `PipelineRun` row and never invokes the real
  engine. `pipeline/runner.py::run_sync_stages` (the real fetch/dedupe/gate/OCR/feature/scorer flow)
  has **zero callers** in `marquee/` — it is orphaned by the placeholder.
- `handlers_ml._execute_publication` writes a descriptive JSON document (`application/json`,
  fabricated `coverage`/`epochs`/`validation` evidence) for `taste_rebuild`, `taste_map`,
  `taste_enrich`, and `learned_head_train`, then activates it through `activate_immutable_artifact`.
  The real trainers exist and are unused: `ml/taste_trainer.py::rebuild_profile`, `ml/taste_map.py`,
  `ml/profile_enrich.py`, `ml/head_trainer.py`; the production loaders are `ml/taste_store.py`
  (`TasteStore`/`NumpyTasteStore`) and `ml/learned_head.py`.
- `PipelineRun` (`models/pipeline_run.py`) has no `job_id`/`attempt_id` linkage to the canonical job;
  H2 must add it via a forward migration (H10). `MlActivePublication`
  (`models/ml_publication.py`) is the single fenced active pointer that H16 converges every reader on.
- The tracked launcher `process_launcher.py::TOOL_CATALOG`/`ProcessLauncher` is the JMC6G process
  boundary that H1 extends with the fixed internal-runner capability (H03).

### H0 deliverables (frozen)

- `tests/fixtures/jmc6h/enabled_definition_closure.json`: the initial behavioral closure manifest
  (H23), 43 enabled definitions, `audit_state="h0_frozen"`, `plan_base` = jmc6g-complete. Each entry
  carries the frozen JMC6G execution fields plus `feature_area`, `execution_class`, `product_state`,
  `producer`, `consumer`, `real_product_effect`, and `certification_test`. Five leaves are flagged
  `placeholder_pending_h2`/`placeholder_pending_h4`: `poster_pipeline`, `taste_rebuild`, `taste_map`,
  `taste_enrich`, `learned_head_train`.
- `tests/test_jmc6h_closure_manifest.py`: three green contracts proving the manifest covers exactly
  `JOB_DEFINITION_REGISTRY.enabled_types` == `EXECUTION_HANDLERS`, carries the required behavioral
  fields matching the registry, and flags exactly the five placeholder leaves with cert tests.
- `tests/test_jmc6h_product_effect.py`: two **intentional-red** contracts through the real handlers.
  `test_poster_pipeline_writes_canonical_pipeline_run_projection` fails `assert 0 >= 1` (no
  `PipelineRun`); `test_taste_rebuild_publishes_native_loadable_profile_artifact` fails on
  `content_type == "application/json"` / not numpy-loadable. Both fail on the asserted product effect,
  not on harness error, and are made green in H2 and H4 respectively.

### Current phase and exact next steps

- **Current phase:** H0 checkpoint — freeze verified; ready for the configured-author phase commit.
- **Exact next steps:** commit H0 (short lowercase, configured author), record hash/tree, then begin
  H1 — extend the tracked launcher with the fixed allowlisted internal-runner operation/manifest/
  progress/result protocol kept in the attempt process group, and certify containment, descendant
  death, malformed-frame safety, timeout, cancellation, output confinement, and stale fence before
  enabling any new poster/ML behavior.
- **Deviation and justification:** H0 is intentionally red for exactly the two placeholder
  product-effect contracts, as the plan requires the gap to be frozen before implementation. The
  original 1350-test suite remains green; only the two named contracts are red and later phases must
  return the full suite to zero-green.
- **Operator/live-smoke state:** no operator database, media, schedule, push, activation, or external
  coordination used. Only the owned cluster and synthetic fixtures ran. Live TMDB download and
  PaddleOCR/CLIP/DINO/GPU model smokes remain pending for H2/H4/H6.
- **Tree and ancestry:** pre-edit base `93a695b` (jmc6g-complete), tree `7f628314`; branch/upstream
  one commit ahead and clean before H0. H0 commit hash/tree recorded below after the commit.

### H0 committed state

- Configured-author phase commit: `1d8627cca20cbadbb0f1016f7fe93e3bba9e0480`
  (`freeze real product contracts`).
- Commit tree: `0a76fad454f0cfe266d922a390732b1453c8ca08`; sole parent is jmc6g-complete
  `93a695b5843ffaaf26eae98e67669538c32b1a56`.
- Author and committer are the configured repository identity, Gautam Chaudhri
  `<gautam.chaudhri@gmail.com>`. Worktree was clean immediately after commit.
- Complete H0 gate on the owned migrated cluster: **2 failed, 1353 passed** — the 1350 preserved
  baseline plus three green manifest contracts, with exactly the two intentional-red product-effect
  contracts. Ruff over `marquee tests scripts` passes; `git diff --check` clean. This is the
  intentional-red H0 state; H2/H4 restore the full suite to zero-green.
- **Current phase:** H1 — contained internal runner and native protocol. H1 is the next phase; its
  design is captured below. **No H1 implementation code is written yet**; the worktree is clean at
  the H0 checkpoint. This session concludes at the clean, committed, verified H0 boundary because H1
  is a safety-critical contained-subprocess subsystem whose containment/descendant-death/timeout/
  cancellation/stale-fence matrix must not be left partially built or unverified; a resumer continues
  from exactly here.

### H1 design blueprint (derived from source; not yet implemented)

- **Child template:** `core/jobs/process_canary.py` is the exact model. The internal runner is a new
  closed child module, e.g. `python -m marquee.core.jobs.internal_runner <operation>`, where
  `<operation>` is validated against a closed enum (`poster_single`, `poster_batch`, `taste_profile`,
  `taste_map`, `enrichment`, `learned_head`, plus a `noop` used only to certify transport). It emits
  the `MARQUEE_CANARY_READY`-style start barrier and gates on a stdin byte, exactly like the canary.
- **Control channel:** the versioned bounded manifest (in) and progress/result frames (out) travel on
  a dedicated fd (e.g. fd 3 via `asyncio.create_subprocess_exec(..., pass_fds=(3,))`), length-prefixed
  JSON, so the child's real stdout/stderr (OCR/model chatter) still tee to the redacted attempt log
  per H05. Malformed/oversized frames must fail safely. Large candidate/model data moves through
  confined workspace/artifact keys, never the JSON pipe.
- **Coordinator:** extend `ProcessLauncher` with a `launch_internal_runner(...)` method mirroring
  `launch`/`launch_canary` — `start_new_session=True` (process-group leader), `create_attempt_cgroup`
  containment (so OCR/model multiprocessing descendants stay in the attempt cgroup per H04),
  `capture_process_identity` + `record_identity` fence, `_minimal_environment()` plus only an
  allowlisted env, `cwd=self._cwd()` confinement. No executable/module/path/code comes from a request.
  Add the runner module name to a code-owned allowlist (not `TOOL_CATALOG`, which is media tools).
- **Coordinator validation:** validate every produced file/checksum/size/media-model-format/expected
  subject/fence before artifact registration or projection/activation (§4). A runner cannot write the
  canonical database or publish a library destination.
- **Certification matrix (H1):** process-group/cgroup ownership; descendant death on cancel/timeout/
  worker-kill (reuse `TrackedProcess.cancel` SIGINT→TERM→KILL/cgroup + `_confirm_tree_dead`);
  malformed/oversized frame safety; stdout/stderr log capture; progress freshness; timeout wrapping
  the handler (JMC6G `TimeoutPolicy`); output confinement; stale-fence rejection. Model these on the
  existing JMC3A launcher tests (`tests/test_jmc3a_*`, canary behaviors) plus new runner tests.
- **Then (still H1):** adapt `pipeline/runner.py::run_sync_stages` and the ML trainers
  (`ml/taste_trainer.rebuild_profile`, `ml/taste_map`, `ml/profile_enrich`, `ml/head_trainer`) to
  accept explicit workspace input/output locations and a progress callback, **without enabling new
  behavior** until each family gate passes (that enablement is H2/H4). The two intentional-red
  contracts in `test_jmc6h_product_effect.py` go green in H2 (poster `PipelineRun`) and H4 (native
  taste artifact) respectively.
- **Resume command:** owned cluster is disposable and may need re-creating; re-run
  `python -m marquee.db_migration` against a fresh `marquee_test` before the suite, and always set
  `DB_URL=postgresql+asyncpg://marquee@127.0.0.1:<port>/marquee_test` (never port 5432). Gate every
  phase with the full suite (baseline 1350 green + only the sanctioned intentional-reds until H2/H4).

## JMC6H Phase H1 checkpoint — contained internal-runner transport (certified)

The fixed, code-owned internal-runner transport is implemented and certified. It is the H03/H04/H05
process boundary that H2/H4 use to run heavy poster/ML Python; only the transport is enabled now
(`noop`), and the real poster/ML operations remain deliberately not-enabled until their family gates
pass.

- **New modules.** `core/jobs/runner_protocol.py` — dependency-free, versioned, length-prefixed
  frame protocol (`PROTOCOL_VERSION=1`, 64 KiB frame/manifest bounds, 100k frame ceiling), the
  closed `RunnerOperation` enum (`noop`, `poster_single`, `poster_batch`, `taste_profile`,
  `taste_map`, `enrichment`, `learned_head`), a child `ControlWriter`, and stdin manifest reader.
  `core/jobs/internal_runner.py` — the light child entrypoint
  (`python -m marquee.core.jobs.internal_runner <operation>`); emits a ready barrier, reads the
  stdin manifest, dispatches through an operation registry (only `NOOP` wired; other members raise
  `RunnerOperationNotEnabledError`), writes confined outputs to the workspace cwd, and returns a
  result frame; confines produced-file names to safe basenames. `core/jobs/internal_runner_host.py`
  — the coordinator `run_internal_operation(...)`: drives the protocol via a reader task + queue so
  cancellation/timeout never corrupt a partial frame, proves tree death on interruption, validates
  every produced file (existence/size/sha256), and returns a typed `RunnerOutcome`
  (succeeded/failed/cancelled/timeout/protocol_error) that never reports success over a lost fence,
  nonzero exit, missing result, or the coordinator's own cancellation/timeout.
- **Launcher extension.** `ProcessLauncher.launch_internal_runner(operation, *, manifest)` mirrors
  `launch`/`launch_canary`: `start_new_session=True` process-group leadership, cgroup containment
  (`create_attempt_cgroup`), identity capture + `record_identity` fence, `_minimal_environment()`
  plus a single code-owned control-fd env key, confined `cwd`. The control pipe is passed via
  `pass_fds` and returned to the coordinator as an `asyncio.StreamReader`; the manifest is written to
  the child's stdin; stdout/stderr still tee to the redacted attempt log. No module/path/command/code
  comes from any caller — only the closed operation and a bounded manifest.
- **Certification.** `tests/test_jmc6h_internal_runner.py` — 20 tests, all green: ready/progress/
  result/file round-trip with checksum-validated confined output; identity recorded as a
  process-group leader; not-enabled operation reports failure (not success); unknown operation and
  oversized manifest rejected at launch; cooperative cancellation confirms tree death; timeout
  terminates and reports timeout; signal-ignoring runner escalates to SIGKILL; fence loss never
  reports success; output-confinement rejects traversal; checksum-mismatch detection; frame bound/
  version/malformed/EOF/truncation/oversize safety; and finalization honesty (missing result →
  failed, late fence loss → cancelled, nonzero exit → failed, protocol error surfaced).
- **Deviation (justified).** `tests/fixtures/jmc3b/b0_contract_freeze.json` `process_launcher_methods`
  gains `launch_internal_runner` — a legitimate, intended launcher-surface extension for JMC6H, so
  the JMC3A extension-point freeze is updated to match rather than weakened.
- **Gate.** Full suite on the owned cluster: **1373 passed, 2 failed** — the 1350 baseline + 3
  manifest contracts + 20 runner contracts, with only the two sanctioned poster/ML intentional-reds
  still red (they go green in H2/H4). Ruff over `marquee tests scripts` clean; `git diff --check`
  clean.
- **Remaining H1 → H2 bridge.** The poster/ML *operations* are not yet wired: adapting
  `pipeline/runner.py::run_sync_stages` and the ML trainers to explicit workspace input/output plus a
  runner progress callback, and registering their runner operations, is done as each family is
  enabled (poster in H2, ML in H4). The transport, containment, and safety guarantees above are the
  fixed substrate they build on.
- **Next:** H2 — run the real single-subject poster pipeline through a `poster_single` runner
  operation, register real candidate artifacts, write the canonical `PipelineRun` projection linked
  to job/attempt/subject (forward migration for the linkage), migrate review/run/metrics/feedback/
  deployment consumers to canonical identity, and flip
  `test_jmc6h_product_effect.py::test_poster_pipeline_writes_canonical_pipeline_run_projection` green.

### H1 committed state

- Configured-author phase commit: `5aeeed78f07fa515be3f87045e4b20a84d5ef693`
  (`add contained internal runner`).
- Commit tree: `e9e79418e98de5b63c2f1f1404c775dd97fead61`; sole parent is the H0-record commit
  `94919f7ba13f4ae4c1926a3d9b6665b2c9c5a062`.
- Author and committer are the configured identity, Gautam Chaudhri `<gautam.chaudhri@gmail.com>`.
  Worktree clean immediately after commit. Full suite **1373 passed, 2 failed** (only the two
  sanctioned poster/ML intentional-reds); Ruff and `git diff --check` clean.

## JMC6H H2 groundwork — environment findings and session checkpoint

Before writing H2 code I inventoried the real pipeline and the box's actual ML capability. The
findings below are load-bearing for H2/H4 and were not obvious from the plan.

- **The real pipeline stage functions are orphaned.** `pipeline/runner.py::run_sync_stages`,
  `fetch_and_download`, `place_outputs`, and `build_run_payload` have **no callers** in `marquee/`
  (only `extractor_runtime.py` constructs a `FeatureExtractor`, for archived-run rescoring). JMC4C's
  placeholder replaced the whole orchestration. H2 must **compose the existing real stage functions**
  inside the `poster_single` runner operation (reuse, not a second pipeline, per H02) — there is no
  current composed orchestrator to call. `run_sync_stages` already takes explicit
  `out_dir`/`records`/`candidate_map`/`all_files`/`resolution_by_name`/`FeatureExtractor`/
  `OcrGateContext`, so it is workspace-friendly; `fetch_and_download` is the async TMDB fetch that a
  fixture path must bypass with owned candidate images.
- **ML artifacts are present** (so H2's real pipeline can run here): `data/ml/taste_profile.clip-vit-b-32.npz`
  (movie), `data/ml/taste_profile.tv.clip-vit-b-32.npz` (TV), `data/ml/taste_map.clip-vit-b-32.npz`,
  `data/ml/zeroshot_axes.clip-vit-b-32.npz`, and `marquee/ml/models/` CLIP/DINO/aesthetic/face/person
  ONNX. `TASTE_PROFILE_PATH` resolves to `data/ml/taste_profile.{AI_MODEL}.npz`.
- **PaddleOCR 3.7.0 is installed and importable.**
- **onnxruntime is the CPU build here:** `ort.get_available_providers()` == `['AzureExecutionProvider',
  'CPUExecutionProvider']` — **no `CUDAExecutionProvider`** despite the RTX 3070 being present via
  `nvidia-smi`. So CLIP/DINO/face/person inference runs on **CPU** (functional but slow). H6's poster
  "GPU smoke" is therefore, on this box, a **CPU** ONNX smoke; a real CUDA smoke is **unavailable** and
  must be recorded as such with this exact readiness reason unless onnxruntime-gpu is installed.
- **H4 training-data gap:** `TRAINING_DATA_DIR` (`data/taste_seeding/movies`) is **empty (0 files)**.
  A from-scratch real `taste_rebuild` needs exemplar posters; the existing `data/ml/taste_profile*.npz`
  were built earlier (the training set is not checked in). H4 must either locate the operator exemplar
  set or record the rebuild live-smoke as unavailable with this reason while still certifying the
  publication/activation/consumer path deterministically.
- **First H2 step (do this first, model-free):** forward migration `0010_jmc6h` adding nullable,
  indexed `job_id` (FK `jobs.id`) and `attempt_id` (FK `job_attempts.id`) to `pipeline_runs` (H10),
  plus a fenced-provenance marker, with an offline `alembic upgrade head --sql` + model-equivalence
  test. Then compose the `poster_single` runner operation over the real stages with a fixture
  candidate-injection path, write the linked `PipelineRun`, register candidate artifacts, and flip the
  poster intentional-red green.

### Session checkpoint (H1 complete; H2 not started)

This session delivered **H0** (`1d8627c`, `94919f7`) and **H1** (`5aeeed7`) — both certified, green
except the two sanctioned intentional-reds, configured author, no attribution, unpushed, clean
worktree. It stops at the H1 boundary rather than begin H2's large, model-heavy pipeline
composition, because that phase cannot be completed to genuine green certification within this
session and rushing unverified model-integration code would violate the plan's truthfulness bar.
The H2 blueprint and the environment findings above make the next session resume immediately. No
push, activation, operator-state mutation, or history compaction was performed. The owned disposable
cluster (`127.0.0.1:55460/marquee_test`, migrated to `0009_jmc6g`) is left running for reuse; recreate
per the resume command above if absent.

## JMC6H Phase H2 — real single-subject poster pipeline (implemented; poster red green)

Resumed on operator instruction. The placeholder poster handler is deleted and replaced with the
real pipeline run inside the contained internal runner; the poster intentional-red is now green and
`poster_pipeline` is a live-smoked real capability.

- **Migration `0010_jmc6h`** (committed `87306e4b`, refreshed to head `0010_jmc6h`, `alembic check`
  no-op): `pipeline_runs` gains nullable, indexed `job_id`→`jobs`, `attempt_id`→`job_attempts`,
  `fence_token`, and `selected_artifact_id`→`job_artifacts`, all `ON DELETE SET NULL` (H10). Schema
  fingerprint changed from the JMC6G value; `db_migration.ALEMBIC_HEAD` and the migration test's head
  parent were updated. (The disposable cluster's `schema_contracts` marker must be refreshed with
  `python -m marquee.db_migration` after a manual `alembic upgrade`, else offline-restore
  certification fails — an environment step, not a defect.)
- **Reusable real-pipeline orchestrator** `marquee/pipeline/orchestrator.py`: composes the *existing*
  stage functions (`fetch_and_download` or a confined fixture source → `run_sync_stages` →
  `place_outputs` → `build_run_payload`) for one subject inside the workspace. Not a second pipeline
  (H02) — it reuses the real stages behind one confined call and returns status/counts/recommendation
  plus the run payload.
- **Contained `poster_single` operation** (`internal_runner.py::_run_poster_single`, registered in the
  closed vocabulary): lazily imports the pipeline/ML stack, builds a real `FeatureExtractor`
  (preflight), runs the orchestrator (`asyncio.run`), streams pipeline stages as progress frames,
  writes `run.json` + the selected candidate into the workspace, and announces them as
  coordinator-validated produced files.
- **Real handler** `core/jobs/poster_pipeline.py` (placeholder deleted, H01): builds the bounded
  manifest from the immutable request, runs `run_internal_operation(POSTER_SINGLE, …)` through the
  attempt-owned launcher, re-checks the fence, registers the run archive (`command_report`) and
  selected candidate (`evidence_image`) as JMC3 artifacts, and writes the canonical `PipelineRun`
  linked to job/attempt/fence/subject with real counts/status/scorer/selected artifact. Analysis
  only — it never deploys, resets, or restores artwork, and imports no `marquee.pipeline` directly
  (the pipeline runs in the subprocess). The JMC4C C1 workspace freeze test was updated from the
  placeholder's `workspace.staging_file` to the real `run_internal_operation` delegation.
- **Certification.** Deterministic:
  `test_jmc6h_product_effect.py::test_poster_pipeline_writes_canonical_pipeline_run_projection` now
  drives the real handler with a controlled runner outcome (avoiding a multi-minute model load in the
  suite) and asserts the linked `PipelineRun` (job/attempt/fence/movie/status/auto-pick/scorer) — the
  poster intentional-red is **green**. Live capability: `test_jmc6h_poster_live_smoke.py`
  (`MARQUEE_LIVE_SMOKE=1`) ran the **real** `poster_single` subprocess end to end — real
  `FeatureExtractor` (CLIP/DINO/aesthetic, CPU ORT), PaddleOCR 3.7.0, the taste profile, gates, and
  scorer — over two fixture posters in **16.08s**, producing a checksum-validated `run.json` and
  processing both candidates (`posters_found == 2`). This is real H25 evidence that the enabled poster
  capability executes on this host (CPU inference; a CUDA smoke stays unavailable per the H2 findings).
- **Consumers.** Review-queue, run listing, and metrics already read `PipelineRun` and remain green
  (40 poster-family tests pass); the H10 linkage columns are additive. Full round-trip review→deploy
  and the legacy-archive→artifact read migration continue in H3/H11.
- **Manifest.** `poster_pipeline` moves from `placeholder_pending_h2` to `real`; the closure-manifest
  contract and its placeholder-leaf set shrink to the four ML families. The ML intentional-red stays
  red until H4.
- **Next:** H3 (real movie/TV poster batches + the review→feedback→canonical-deploy/reset round trip,
  correcting review-reset semantics) and H4 (native taste/map/enrichment/head publication, flipping
  the ML intentional-red).

## JMC6H Phase H4 (partial) — native taste-profile publication (ML red green)

`taste_rebuild` is converted from a descriptive-JSON placeholder to a real native taste-profile
publication; the ML intentional-red is now green and the full suite is zero-green (only the two
opt-in live smokes skip).

- **Native artifact policy.** `ARTIFACT_POLICIES["taste_profile"]` changes from `.json`/
  `application/json`/1 MiB to `.npz`/`application/octet-stream`/256 MiB. Only `taste_rebuild`
  registers the `taste_profile` kind, so the blast radius is one handler.
- **Contained `taste_profile` operation** (`internal_runner.py::_run_taste_profile`, registered in the
  closed vocabulary): lazily imports the ML stack and runs `taste_trainer.rebuild_profile` with an
  explicit workspace `output=profile.npz`, so it writes **only** to the workspace and never the
  configured live taste-profile path (verified: `rebuild_profile` uses `output or ns.profile_path`).
  `source.mode=fixture` trains from posters staged in `training/`; `library` uses the configured
  exemplar dir. It announces `profile.npz` as a coordinator-validated produced file.
- **Real handler** `handlers_ml.py::execute_taste_rebuild` (placeholder path removed for this family):
  runs `run_internal_operation(TASTE_PROFILE, …)` through the attempt-owned launcher, gates the result
  with the **production loader** (`NumpyTasteStore._ensure_loaded`, H18), re-checks the fence, registers
  the `.npz` as a native `octet-stream` `taste_profile` artifact, and fenced-CAS activates
  `MlActivePublication` `taste_profile:{library}` via the existing `activate_immutable_artifact`
  (failure/superseded/stale preserves the prior active version).
- **Certification.** Deterministic:
  `test_jmc6h_product_effect.py::test_taste_rebuild_publishes_native_loadable_profile_artifact` drives
  the real handler with a controlled runner outcome that stages a valid native `.npz`, and asserts the
  activated artifact is `application/octet-stream` and numpy-loadable — the ML intentional-red is
  **green**. Live capability: `test_jmc6h_taste_live_smoke.py` (`MARQUEE_LIVE_SMOKE=1`) ran the **real**
  `rebuild_profile` (real CLIP embeddings, CPU) over three fixture posters in **2.88s**, producing a
  native `profile.npz` (3 exemplars) that `NumpyTasteStore` loads — real H25 evidence.
- **Manifest.** `taste_rebuild` moves to `real`; the placeholder-leaf set shrinks to
  `{taste_map, taste_enrich, learned_head_train}`.
- **Deferred within H4 (honest readiness).** The remaining three ML families are **not yet real**:
  `taste_map.build_map`, `head_trainer.train_from_labels(save=…)`, and `profile_enrich.enrich` write
  to configured live paths and need the same explicit-output refactor + runner ops before they can be
  workspace-confined and activated natively. They keep the descriptive-JSON `_execute_publication` path
  and stay flagged placeholder. Their live inputs (empty `TRAINING_DATA_DIR`, no accumulated feedback
  labels on this box) mean their eventual live smokes may be unavailable-with-reason per H25.
- **Next:** finish H4 for the three remaining ML families, then H3 (batches + review round trip),
  H5 (legacy removal + static retirement), and H6 (integrated certification + final compaction).

### JMC6H committed range so far (all configured author, unpushed)

- `1d8627c` freeze real product contracts (H0)
- `94919f7` record h0 state and h1 blueprint (H0)
- `5aeeed7` add contained internal runner (H1)
- `d848f88` record h2 groundwork and checkpoint (H2 groundwork)
- `87306e4` link pipeline run to canonical job (H2 migration `0010_jmc6h`)
- `d79f934` run real poster pipeline (H2)
- `5e85c2d` publish native taste profile (H4 taste_rebuild)

**Milestone:** the full backend suite is **zero-green (1375 passed, 0 failed, 2 opt-in live-smoke
skips)** on the owned migrated cluster — both H0 intentional-reds are flipped by real,
live-smoked implementations. Schema head `0010_jmc6h`. Ruff, `git diff --check`, OpenAPI/type export
all clean. Remaining before H6 certification + final compaction: the three other ML families
(taste_map/taste_enrich/learned_head — builder output-confinement refactors), H3 (poster batches +
review→feedback→deploy/reset round trip + review-reset semantics H19), H5 (legacy removal + static
retirement H20–H22), and the full H6 behavioral-closure + performance/saturation certification. These
remain genuinely incomplete; no certification or compaction is claimed.

## JMC6H Phase H3 (partial) — review-reset semantics (H19)

- **`api/routes/pipeline.py::reset_review_queue` corrected (H19).** It previously nulled `Movie`
  poster DB state (`poster_path`, `poster_deployed_filename`, `poster_ai_selected`, …) for
  review-queue movies while deliberately leaving the deployed poster file on disk — exactly the
  DB/filesystem disagreement H19 forbids. It now changes **review disposition only** (marks the
  review-queue `PipelineRun`s reviewed so they leave the Review tab and records the `review_reset`
  `ArtworkEvent`); clearing or removing deployed artwork remains the separately authorized canonical
  `poster_reset` job. The response contract is unchanged (`posters_reset` is now always `0`); no
  OpenAPI/type drift.
- **Certification.** `test_jmc6h_review_reset.py` proves the run's disposition changes while every
  deployed poster DB field is untouched. Full suite **1376 passed, 0 failed, 2 opt-in skips**; ruff
  and diff clean.
- **Still open in H3:** poster batch parents already dispatch real `poster_pipeline` children (H2), but
  the H06 single-process batch runner, sealed-child overall progress, and the full
  analysis→review→feedback→canonical-deploy/reset refresh round trip are not yet certified.

## JMC6H Phase H5 (partial) — retire automatic clean-slate startup migrations (H21/H22)

- **`marquee/main.py` no longer runs the clean-slate legacy migrations at API startup.** The lifespan
  previously invoked `migrate_legacy_runtime_state()` and `migrate_live_artifacts()` on every boot —
  automatic clean-slate migrations that can rewrite operator artifacts. Both calls and their imports
  are removed. The functions remain as **explicit offline utilities**
  (`marquee.core.pipeline_config.migrate_legacy_runtime_state`, still directly tested, and
  `python -m marquee.ml.migrate_artifacts` via its `__main__` guard), so nothing rewrites artifacts on
  a normal startup. `test_jmc1_readiness` no longer needs to monkeypatch them.
- **Static retirement check (H22).** `test_jmc6h_retirement.py` proves the entrypoint imports/invokes
  neither migration and that the offline artifact-migration command still exists.
- **Still open in H5:** the broader H20 removals (dead manager singletons, detached warm tasks, dead
  heal/TV-scope/service/cache modules, retired re-encode stubs, stale lifecycle comments/tests) and the
  full static-reachability retirement sweep remain, after extracting any still-used pure helpers.

## JMC6H Phase H4 (partial) — native taste-map publication and active consumer

- **Native map builder.** `ml.taste_map.build_map` now accepts explicit profile input and output
  paths while preserving the configured-path behavior for legacy/offline callers. Explicit output
  never archives or overwrites the configured map and can suppress live thumbnail writes. The fixed
  `taste_map` internal-runner operation consumes only workspace `profile.npz`, invokes the existing
  real projection/clustering builder, emits bounded progress, and announces native `map.npz`.
- **One active authority.** `resolve_active_publication` is a typed `MlActivePublication` resolver
  that verifies pointer/artifact family, status, checksum, and physical evidence. The canonical map
  handler resolves `taste_profile:{library}` through it, cancellation-aware copies that exact
  generation into the attempt workspace, validates runner output with `ml.taste_map.load_map`,
  registers native octet-stream evidence, and fenced-CAS activates `taste_map:{library}`. The GET
  map API now resolves and loads that active publication rather than the configured cache path.
- **Certification.** The deterministic product-effect test proves active profile → confined copy →
  native map → production loader → artifact → activation, and the contained-runner test executes the
  real builder. The opt-in live smoke copied the installed real movie profile read-only into an owned
  workspace and built/loaded its native map through containment: **1 passed in 11.00s**. Serena
  reference analysis found `build_map` consumers only in its own load/project paths and focused tests;
  the default-compatible signature preserves them while the job path uses explicit inputs/outputs.
- **Complete gates.** Focused H4 set: **43 passed** before the stable-focus correction, then the
  corrected manifest/consumer set passed. Final backend: **1380 passed, 0 failed, 3 opt-in live-smoke
  skips in 104.81s**; the new taste-map smoke passes separately above. Ruff over `marquee tests
  scripts`, `git diff --check`, frontend check 0/0, lint, unit **112/112**, build, and Chromium
  Playwright/axe **11/11** pass. Alembic current/head remain sole `0010_jmc6h`; autogenerate reports
  no operations. OpenAPI remains 199 paths at
  `61d9649efc8723e23d56471ee426adfb8fa4fcfb17159cb4672662d647ba3358`; generated TypeScript is
  `b9ac20b83f2e22bcc408c05adf069d3a3647f83c1a635b82c3a90162bf9d962c`.
- **Deviation and recovery.** The first focused test run was blocked before collection by sandbox
  socket denial and is excluded; all recorded database tests used only the owned port-55460 cluster.
  Small four-point and eight-dimensional runner fixtures exposed real artifact/UMAP constraints;
  the fixture was corrected to a valid eight-by-512 production-format profile. The deterministic
  contract check also found inherited review-reset docstring drift from `8a2eaca`; OpenAPI and types
  were regenerated and rechecked. Two mistyped npm script invocations ran no generator and are not
  evidence. No operator library, configured live artifact, schedule, push, or activation changed.
- **Current phase and exact next steps.** H4 remains active. `taste_map` is now `real`; the remaining
  placeholder set is exactly `{taste_enrich, learned_head_train}`. Next, refactor enrichment to
  consume/emit explicit native profile paths with no cache/live mutation, then refactor learned-head
  training to explicit feedback/input and output artifacts; wire both runner operations, production
  loaders, active consumers, invalidation/repair, preservation tests, live or unavailable readiness,
  and complete gates. H3 batch/review round-trip, broad H5 retirement, and H6 remain pending.
- **Tree and ancestry.** Based on configured-author commit `9460cdd3080b06d041308d9e6ce3c478b778ca5d`;
  branch remains linear and unpushed after `jmc6g-complete`. The taste-map phase commit/hash/tree are
  recorded immediately below after commit.

### Native taste-map committed state

- Configured-author phase commit: `4ba9393dc4b17e5b996ad10f40b9b3cd6b5d578c`
  (`publish native taste map`), tree `5af06a4d0399ce4358c5635a050e74eb87ff774f`, sole parent
  `9460cdd3080b06d041308d9e6ce3c478b778ca5d`. Author and committer are Gautam Chaudhri
  `<gautam.chaudhri@gmail.com>`; the worktree was clean immediately after commit and the branch was
  12 unpushed commits ahead of `origin/job-manager`.
- **Current phase:** H4 native enrichment and learned-head publication is in progress. Exact next
  step is explicit-input/output enrichment over the active taste profile, followed by the same
  contained/validated/fenced/consumed closure for learned-head training.

## JMC6H Phase H4 complete — native enrichment, learned head, and active scoring inputs

- **Enrichment is a profile successor, not a second authority.** `ml.profile_enrich.enrich` accepts
  explicit profile/output/cache paths. The contained `enrichment` operation consumes staged
  `source-profile.npz`, invokes the real enricher, and emits native `profile.npz` without modifying
  its input or configured live files. The coordinator resolves and checksum-copies the active
  `taste_profile:{library}`, validates the successor with `NumpyTasteStore`, then registers and
  fenced-CAS advances that same active family. The dead `taste_enrichment:{library}` authority and
  result-family vocabulary are removed from routes, submission snapshots, and result documents.
- **Learned-head publication is native and truthful.** `head_trainer.train_from_labels` now accepts
  a frozen row set and explicit output path. The fixed `learned_head` runner parses a bounded
  coordinator-staged feedback snapshot, runs the real pairwise/pointwise trainer, returns truthful
  `no_change` when thresholds are unmet, or emits `head.npz`. The coordinator prechecks expected
  generation, snapshots at most 64 MiB/100,000 rows, validates with `LogisticHead.load`, rechecks the
  fence, registers the native octet-stream artifact, and activates `learned_head:{library}` by CAS.
  The old descriptive-JSON publication implementation is deleted; every H4 family now invokes its
  real implementation.
- **Sole active consumer path.** Canonical poster analysis resolves verified active
  `taste_profile` and `learned_head` publications, copies the exact generation/checksum into the
  attempt workspace, and the contained runner constructs `NumpyTasteStore(profile.npz)` and selects
  the scorer from `head.npz`. The product-effect test loads and scores through the actual
  `select_scorer` consumer. Configured live paths remain only backward-compatible/offline defaults;
  the canonical job path uses the typed active-publication authority.
- **Behavioral closure.** `taste_enrich` and `learned_head_train` move to `real`; the JMC6H
  placeholder set is empty. Their checked-in closure entries name native artifacts, active
  projections, actual consumers, and exact product-effect tests. Runner tests execute both real
  implementations over bounded valid fixtures; learned-head absence/insufficient feedback is a
  truthful no-change readiness state rather than synthetic success.
- **Certification.** Focused H4/closure sets passed (60, 38, and 13-test cuts); final backend is
  **1383 passed, 0 failed, 3 opt-in live-smoke skips in 106.64s**. Ruff passes over `marquee tests`;
  `git diff --check` passes; Alembic reports no new operations at sole head `0010_jmc6h`; OpenAPI is
  current at 199 paths and generated TypeScript has no drift. Frontend `svelte-check` is 0/0,
  Prettier/ESLint pass, unit is **112/112**, production build succeeds with the known chunk warning,
  and Chromium Playwright/axe is **11/11**.
- **Excluded non-evidence.** One test command named two nonexistent test modules and therefore ran
  no tests; a later corrected 60-test set passed. One API-client drift command was launched from the
  repository root and failed before execution because no root `package.json` exists; the same
  command from `frontend/` passed. Neither failed invocation is certification evidence.
- **Committed state.** Configured-author phase commit
  `b69221ea3ff9c9aee76f00c01340302054b58e73` (`complete native ml publication`), tree
  `e1166540ebc3a0419d820fa1436c1562bc283921`, sole parent
  `ecc5b3a79ef44144b2c4945731d26d10084c3e79`. Worktree was clean immediately after commit; branch
  was 14 local commits ahead of `origin/job-manager` and remained unpushed.
- **Current phase and exact next steps.** H4 is complete. Resume H3 with the missing H06 sealed
  single-process poster batch runner and full analysis → review → feedback → canonical deploy/reset
  refresh proof; then complete H5 H20/H22 reachability-based retirement, H6 definition/live/
  unavailable/saturation certification, recovery bundle, mandatory final-only squash, and local
  annotated completion tag. Do not push or activate production schedules.

## JMC6H Phase H3 complete — immutable review evidence and canonical deploy refresh

- **Review candidates and archives are canonical evidence.** Each bounded poster candidate is copied
  into the contained attempt workspace, announced by the fixed runner protocol, registered as a
  checksum-addressed `evidence_image`, and embedded back into the immutable run document by artifact
  identity. `PipelineRun.archive_artifact_id` (migration `0011_jmc6h`) links the authoritative archive;
  all production archive readers now verify artifact ownership, family, status, size, checksum, and
  physical storage before parsing it. Candidate serving likewise reads the verified artifact rather
  than an extractor workspace path.
- **Feedback → deploy → refresh closes over the same bytes.** Candidate selection carries its canonical
  artifact id and checksum. The deploy handler verifies job/run/reference ownership and checksum, then
  stages that exact immutable artifact into its own attempt workspace before invoking the real deployer.
  The product-effect test executes analysis publication, archive loading, approval feedback, canonical
  deploy, DB/file mutation, workspace-candidate removal, refreshed library state, and successful image
  serving from retained evidence. It also exposed and fixed feedback's invalid reuse of an untyped root
  idempotency key; deploy children now use the canonical typed child key.
- **H06 batch decision.** Existing poster batch parents already seal and dispatch independently fenced
  `poster_pipeline` children, each executing the fixed contained `POSTER_SINGLE` operation. A shared
  single-process runner is optional in H06 and would weaken those per-child containment/fencing
  boundaries without changing product effect, so it was not introduced. The unused, unimplemented
  `POSTER_BATCH` runner-operation declaration was removed; no dead protocol path remains.
- **Certification.** Full backend: **1383 passed, 0 failed, 3 opt-in live-smoke skips in 102.30s**.
  Ruff passes over `marquee tests` and migration `0011`; `git diff --check` passes. Alembic current and
  sole head are `0011_jmc6h`, and autogenerate reports no operations. OpenAPI remains current at 199
  paths; generated TypeScript has no drift. Frontend `svelte-check` is 0/0, Prettier/ESLint pass, unit
  is **112/112**, production build succeeds with the known chunk warning, and Chromium Playwright/axe
  is **11/11**.
- **Excluded non-evidence.** One focused zsh invocation contained a nonexistent glob and therefore ran
  no tests; the corrected exact-file cut passed. The first full suite after adding migration `0011`
  reported ten failures caused by the still-`0010` schema-contract constant; updating the constant and
  its direct head assertion resolved the shared cause, after which focused migration tests and the full
  suite passed. A broad Ruff invocation over historical Alembic files reported 13 pre-existing style
  findings; the repository's canonical `marquee tests` scope plus the new migration pass cleanly.
- **Committed state.** Configured-author phase commit
  `79057e5cd9b7f0b770fe3329d617f092b70a0f25` (`close poster review lifecycle`), tree
  `71dfae5758516a41d991d20f2bbbf2bc4cf847bf`, sole parent
  `69bbb5a26d33c7b151b2c36d4e920ca0136bbb1d`. Author and committer are Gautam Chaudhri
  `<gautam.chaudhri@gmail.com>`; the branch remains local and unpushed.
- **Current phase and exact next steps.** H3 is complete. Continue immediately with H5 H20/H22:
  use semantic-reference and static-reachability scans to remove dead manager singletons, detached warm
  tasks, heal/TV-scope/service/cache modules, retired re-encode stubs, and stale lifecycle surfaces while
  preserving any still-reached pure helper. Then perform H6 integrated closure, recovery evidence,
  final-only squash, and the local annotated completion tag. Do not push or activate schedules.

## JMC6H Phase H5 complete — product convergence and clean-slate closure

- **Legacy runtime retirement (H20-H22).** Deleted the letterbox manager singleton, dead heal,
  TV-scope, service, pipeline-cache, poster-service, extractor-runtime, profile-updater, parallel ML
  artifact registry/snapshot models, and retired re-encode process stubs. Still-reached algorithms now
  live in purpose-named `letterbox_eligibility`, `letterbox_scope`, `letterbox_transcode`,
  `poster_files`, `poster_summary`, and `publication_catalog` modules. The checked AST/OpenAPI
  retirement contract proves deleted-module imports, dynamic executable strings, manual ML activation,
  GPU-release, configured-path exemplar image, and unconsumed candidate-overlay routes are unreachable.
  Historical contracts and current design references were regenerated for the converged surface.
- **One canonical projection/publication authority.** Migration `0012_jmc6h` removes the artifact
  snapshot tables and makes every surviving `PipelineRun` require its canonical job, attempt/fence,
  immutable archive artifact, and correlation identity while dropping physical workspace columns.
  Review, detail, OCR labels, feedback, metrics, mutation submission, and movie/TV readers now seed or
  consume canonical projections. Taste profile/head listing, detail, exemplar metadata, status, and
  neighbor lookup read verified `MlActivePublication` artifacts; filesystem mutation/manual activation
  APIs and the unused overlay path are gone. Native product-effect tests exercise the mounted consumers.
- **Startup and database evidence.** Removed the unused runtime-state migration implementation and all
  startup hooks; only the explicit offline `python -m marquee.ml.migrate_artifacts` converter remains
  (7 codec/import tests pass). The owned database completed `0012 -> 0011 -> 0012`, Alembic head/current
  are solely `0012_jmc6h`, autogenerate reports no operations, and `marquee.db_migration` verifies it.
  A new empty owned database (`marquee_jmc6h_h5_20260719`) migrated through `0001`-`0012` and passed all
  26 JMC1 migration/readiness tests, proving fresh clean-slate startup without hidden lifecycle work.
- **Certification.** Focused convergence cuts passed (publication/catalog 5, retirement 11, consumer/
  backup/contracts 37, fresh migration/readiness 26, offline conversion 7). Final backend is **1310
  passed, 0 failed, 3 H6 opt-in capability/live-fixture skips in 101.92s**. Ruff passes over `marquee
  tests scripts` plus the executable gauntlet, and `git diff --check` passes. OpenAPI is deterministic
  at 191 paths, SHA-256 `f115de222a8c031f08d9c38825809afa96c22e11c9b10530b614b2f5a4e42091`;
  generated TypeScript SHA-256 is
  `0769bd0a09d6c505d0702b1b7ea7e98b353d3153fc9dc96a3d57e7639cfc1567` and its post-commit
  regenerate check passes. Frontend check is 0 errors/0 warnings, lint passes, unit is **112/112**,
  production build passes with the intentional lazy Plotly vendor limit documented, and Chromium
  Playwright/axe is **11/11**. The closure-manifest SHA-256 entering H6 is
  `b7e7397db7ccc3de83d38f1218c0b3479d19b1eac0a25ff3ea3a9b1097de22da`.
- **Tool/capability evidence and deviations.** Serena was temporarily nonresponsive during the first
  cleanup pass, so AST contracts and repository search preserved progress; it recovered before this
  checkpoint and symbolically confirmed mounted publication-catalog consumers plus the absence of
  retired production references. ByteRover MCP remained available while its local `brv` CLI was not.
  The three opt-in skips are not accepted as final evidence: H6 must execute their available live
  smokes or record exact unavailable readiness and remove skip-based ambiguity. No operator library,
  configured active artifact, schedule, remote, or production service was mutated.
- **Committed state.** Configured-author phase commit
  `c921f9cf51932e4aa1a1f66a9fd5626e52123b9b` (`retire legacy product runtime`), tree
  `25eed4f64051d05828a8a17f773b84d58bfd8b55`, sole parent
  `c13c53872d8289d50fec2f54a7fde666fe85c978`. Author and committer are Gautam Chaudhri
  `<gautam.chaudhri@gmail.com>`; the worktree was clean after commit and the branch was 18 local,
  unpushed commits ahead of `origin/job-manager`.
- **Current phase and exact next steps.** H5 is complete. H6 starts now: validate every enabled
  manifest entry and actual refreshed consumer, run the representative terminal/retry/cancellation/
  failure/no-change/recovery/resource journeys, execute live model/tool/GPU smokes or mark exact
  unavailability, perform the independent final static reachability inspection, then close the
  timeline and perform final-only recovery/bundle/squash/tag operations. Do not push or activate.

## JMC6H Phase H6 complete — integrated certification and final-compaction checkpoint

- **Behavioral closure and real consumers.** The checked-in manifest is now explicitly
  `h6_certified`, covers all **43** enabled registry/delivery definitions exactly, names concrete
  existing certification modules for every entry, and records the converged
  `MlActivePublication taste_profile namespace` projection. Its final SHA-256 is
  `bb9e8fbb75b6a696447ad55fa669db3d392267ce08b7af6d6cf47a0e8eef6655`.
  Manifest/execution closure is 25 passed; the integrated lifecycle/resource/product matrix is
  136 passed; representative audio/subtitle/DoVi/letterbox/poster mutation coverage is 120 passed;
  and the focused finalized manifest gate is 3 passed. Product-effect tests prove canonical
  `PipelineRun` projection and the production loaders/scorer/API consumers of taste profile, map,
  enrichment, and learned-head publications after refresh.
- **Live capability evidence.** The real contained poster operation executed provider enumeration,
  fixture decode/deduplication, PaddleOCR/gates, CLIP, DINO, aesthetic inference, taste scoring,
  ranking, and checksum-validated `run.json`/candidate output against the installed movie profile.
  The final complete run finished this smoke in 330.74s under the unchanged 600s containment
  deadline. Real taste-profile training and taste-map construction/loading also passed. The host is
  an NVIDIA RTX 3070 8GB with driver 595.80; installed artifacts include CLIP (336MB), DINO (85MB),
  aesthetic, SCRFD, YOLO, and movie/TV taste profiles. Available tool versions were exercised or
  inventoried: FFmpeg/ffprobe 8.1.2, mkvmerge/mkvpropedit 99.0, dovi_tool `17ebb13`, and ImageMagick
  7.1.2-13. Read-only live probes reported Radarr 6.2.1.10461, Sonarr 4.0.19.2979, and a successful
  TMDB movie-details request. No remote mutation was attempted.
- **Capability matrix.** Enabled and live-certified: contained poster analysis; taste profile/map;
  local CLIP/DINO/aesthetic/OCR; FFmpeg/ffprobe; mkvmerge/mkvpropedit; dovi_tool; ImageMagick; and
  read-only Radarr/Sonarr/TMDB connectivity. Embedded subtitle generation remains configuration-
  dependent; external Subgen is truthfully unavailable because `SUBGEN_URL` is not configured.
  Authentication, a public reset replacement, Docker hardening, webhooks, `radarr_upgrade`, and TV
  Dolby Vision conversion remain the plan-locked deferred set and were not implemented.
- **Complete zero-green gates.** The decisive backend command with `MARQUEE_LIVE_SMOKE=1` is
  **1313 passed, 0 failed, 0 skipped, 0 xfail/xpass, 0 pytest warnings in 451.91s**. Ruff passes over
  `marquee tests scripts experiments/gauntlet_runner.py`; `git diff --check` passes. Frontend Svelte
  check is 0 errors/0 warnings, lint passes, unit is **112/112**, production build passes, and the
  stabilized warning-free Chromium Playwright/axe suite is **11/11 in 21.9s**. The visual shell test
  now waits for the synthetic queue's terminal empty state before full-page capture, eliminating a
  loading-layout race found during the final rerun. OpenAPI remains deterministic at 191 paths,
  SHA-256 `f115de222a8c031f08d9c38825809afa96c22e11c9b10530b614b2f5a4e42091`;
  generated TypeScript remains
  `0769bd0a09d6c505d0702b1b7ea7e98b353d3153fc9dc96a3d57e7639cfc1567`.
- **Database and clean-slate equivalence.** Alembic head/current are solely `0012_jmc6h`,
  autogenerate reports no operations, and the runtime migration verifier passes. The owned primary
  database and the fresh-install rehearsal database `marquee_jmc6h_h5_20260719` independently
  produce identical fingerprints: Marquee
  `18f206d4d74d1afb7f4d21b5b1efa1712c3dcc0715079be8fc8613499003f52a` and PgQueuer
  `19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a`. The fresh database had
  already migrated from empty through `0001`-`0012` and passed the 26 migration/readiness checks;
  the primary database also passed the `0012 -> 0011 -> 0012` reversal rehearsal.
- **Independent static reachability.** Serena inspected the mounted routers, lifecycle, delivery
  handler authority, publication catalog references, startup tasks, process creation, and dynamic
  execution patterns after all tests. Retired manager/service/registry/snapshot/extractor modules
  have no production references (the surviving `pipeline_cache_clear` text is the canonical enabled
  maintenance job, not the retired cache singleton). Startup performs no schema/artifact migration.
  Mounted taste endpoints, feedback submission, ML handlers, and poster scoring all resolve the sole
  `MlActivePublication` authority. Product child processes remain behind the closed tracked launcher;
  no shell/eval execution surface or parallel active-artifact authority was found.
- **Deviations and recovery.** Two early complete live runs exposed extreme cold CUDA/ONNX/OCR
  first-use variance: poster exceeded 600s and conservative cancellation refused to claim tree death
  inside its two-second proof window. No process or GPU holder survived either failed pytest process;
  the JMC6H-only combined suite then passed 37/37, a pre-H6 bisect passed 253/253 with poster at 523s,
  and the final unchanged 600s gate passed at 331s. Containment was not weakened and no inflated
  timeout was retained. A Playwright color-environment notice was removed for the final run, which
  then exposed and drove the loading-state screenshot stabilization above. All discovered in-scope
  failures were fixed or truthfully recertified within JMC6H.
- **Operator state.** No operator library, deployed poster, configured active artifact, schedule,
  webhook, remote service, or production database was mutated; no push or schedule activation was
  performed. External product calls were read-only. External Subgen configuration remains an owner
  decision, not hidden completion work.
- **Committed state and final-only compaction input.** Configured-author H6 commit
  `2a6c6153b67ef514312c651b092ba20e6ee76026` (`certify product convergence`) has tree
  `02ef80e444467c242db27ed70289a3b3f2e2298c`, sole parent
  `65440e0920fd3f6e9723206796175477cd228d69`, and a clean worktree. The range after exact compact
  base/tag `jmc6g-complete` (`93a695b5843ffaaf26eae98e67669538c32b1a56`) is linear, merge-free,
  configured-author-only, JMC6H-only, and 19 local unpushed commits before this timeline commit.
  Existing JMC6G recovery branch/tag and verified complete external bundle
  `/tmp/marquee-jmc6g-20260718T192421Z.bundle` remain intact.
- **Exact next steps.** Commit this final timeline without further product changes; create recovery
  branch `recovery/jmc6h-20260720T005731Z`, annotated tag
  `recovery/jmc6h-pre-squash-20260720T005731Z`, and verified external bundle
  `/tmp/marquee-jmc6h-20260720T005731Z.bundle` at that pre-squash tip; record the resulting certified
  tree; soft-reset through RTK to exact base `93a695b5843ffaaf26eae98e67669538c32b1a56`; create exactly
  `jmc6h: complete product convergence and certification`; prove tree/parent/ref/bundle/worktree
  identity; create annotated local tag `jmc6h-complete`; and do not edit this timeline afterward.
