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
