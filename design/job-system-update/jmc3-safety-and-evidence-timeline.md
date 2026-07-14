# JMC3 Safety and Evidence Timeline

Shared implementer log for JMC3A → JMC3B → JMC3C. Append after every phase commit.

## Initial state — 2026-07-13 (JMC3A Phase A0)

- Branch/HEAD: `job-manager` at `067c16b0c96558d221d8c57bdedf544f4720c24c`
  (`chunk 2 complete, chunk 3 ready`), tracking `origin/job-manager`.
- Working tree ownership: clean before A0; no overlapping agent or operator changes were
  present. JMC3A owns the new shared timeline and A0 contract-freeze fixture/test.
- Configured Git author: `Gautam Chaudhri <gautam.chaudhri@gmail.com>`; unchanged.
- Tooling: Serena instructions read and project activated; symbol/reference/caller/path and
  process inventories completed. ByteRover queried before implementation. RTK is active,
  but its normal Python-tool resolver cannot locate the repository virtualenv; development
  commands use the documented `rtk proxy` form with explicit `.venv/bin` executables.

## Verified JMC2 completion

- JMC2A commits `10cb470`, `3659626`, `6cc38dc`, `f2662b6`, `e49bd07`, `2d6f678`;
  JMC2B commits `32eb5a8`, `ac60f8d`, `7f900a4`, `86daa96`, `c0bca7c`; JMC2C commits
  `eed20dc`, `c5b913c`, `1a098a5`, `f5ac084`, `7d90b9b`, `56cd341`, `54c99d2`.
  All are ancestors of the initial HEAD.
- The definition registry contains 48 covered built-ins and rejects any enabled definition
  other than `control/system_noop`; the manifest and readiness check expose exactly that one
  dispatch-enabled type. No production canary or arbitrary executable definition exists.
- Sole Alembic head: `0002_jmc2a`. Recorded reset fingerprints remain Marquee
  `651ba3f0efe8fffb6a262b95962d9a99b9568609b2bd52936cf34bfac04aba89` and PgQueuer
  `19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a` (1.1.1,
  durable). No live migration or destructive reset was performed for A0.
- OpenAPI drift check passes at 193 paths. Static TypeScript regeneration is clean with
  `openapi-typescript` 7.13.0 and TypeScript 5.9.3.

## A0 baseline and capabilities

- `DEBUG=true pytest -q`: **881 passed, 21 failed, 11 errors, 2 warnings** in 62.62s.
  The 21 failures and 11 `CREATEDB` privilege errors match JMC2C certification exactly.
- `ruff check marquee tests`: passed (via `rtk proxy .venv/bin/ruff`).
- Frontend: generated API drift passed; `npm run check` passed with 0 errors and the same
  16 warnings in 8 files; lint and production build passed.
- Runtime: Python 3.13.14, asyncpg 0.31.0, SQLAlchemy 2.0.50, PgQueuer 1.1.1,
  PostgreSQL client/server family 18.3, Linux 7.1.3 x86_64.
- Process identity inputs are readable: host boot ID and `/proc/<pid>/stat` start time.
  POSIX session/process-group support is available.
- cgroup v2 is mounted with `cpuset cpu io memory hugetlb pids rdma misc dmem`, but the
  session has no writable delegated subtree. A0 therefore records process-group fallback;
  it does not claim cgroup containment.
- Direct process-launch and filesystem-operation/security-prefix inventories are frozen in
  `tests/fixtures/jmc3a/a0_contract_freeze.json`. Existing direct launches remain
  unreachable from production delivery except the role supervisor and JMC1 migration
  administration; later phases centralize execution without enabling feature handlers.

## Current phase and next steps

- Phase A0 — verify JMC2C and freeze safety contracts: **complete** (phase commit
  `b312cc5`).
- Focused A0 contract freeze: **3 passed**. Full suite: **884 passed, 21 failed, 11 errors,
  2 warnings** in 62.13s; the three new passes are the only delta from the 881-pass baseline,
  and the retained failure/error set is identical. Ruff, OpenAPI drift, static TypeScript
  regeneration, frontend check/lint/build, and `git diff --check` passed.
- Current phase: A0 complete; A1 next.
- Exact next steps: implement the confined root/key model and descriptor-safe filesystem
  operations; migrate security-sensitive serving and deletion first; remove raw poster
  deletion and string-prefix confinement; add traversal/symlink/path-swap/archive fixtures.
- Deviations: none from JMC3A. RTK raw proxy use is required because normal RTK executable
  resolution is broken for the local virtualenv; all development and Git commands still
  pass through RTK.

## Pending operator work

- Inherited JMC1/JMC2: real-deployment SIGKILL while picked, PostgreSQL restart after
  pickup, notification-channel disruption, and migration rerun with held/queued/deferred
  tickets; apply `0002_jmc2a` or use the sanctioned development reset on the live
  development database.
- Rerun the 11 isolated JMC1 migration tests with a PostgreSQL test role allowed to create
  databases. Evaluate four low-severity npm advisories separately.
- JMC3A cgroup, process-kill, filesystem-race, and PostgreSQL advisory-lock manual smokes
  have not yet been performed.

## Phase A1 — filesystem boundary and unsafe-path removal

- Phase commit: `24675b83564c99cb63c840797cda3d8afba537c3`
  (`confine filesystem operations`), authored solely by the configured repository user.
- Added the classified root/key authority with resolved `Path.is_relative_to` containment,
  no-follow descriptor-relative read/create/delete, descriptor-backed streaming responses,
  confined copy and temporary files, same-directory atomic replacement with an explicit
  `EXDEV` stop, cleanup, and link/device/traversal-rejecting tar extraction.
- Migrated every backend `FileResponse`, security-sensitive string-prefix decision, route
  mutation, pipeline/preview/subtitle/taste serving path, and poster reset to the authority.
  The raw poster unlink fallback was removed: rejected deletion records
  `deploy_reset_error`, leaves the stored reference intact, and performs no file mutation.
  The subtitle cross-device copy-and-replace fallback was also removed.
- Focused certification: **43 passed** across the confinement, static inventory,
  translation, poster serving/deletion, poisoned stored-path, symlink, descriptor swap,
  archive traversal, and `EXDEV` cases. A broader route selection produced 227 passes plus
  the pre-existing cross-module database-isolation failure in the TV reset test; that test
  passes in its owning module and remains in the retained full-suite set.
- Full retained suite: **902 passed, 21 failed, 11 errors, 2 warnings** in 62.71s. The
  failure/error set is identical to A0/JMC2C; the 18 new A1 tests are the only pass-count
  increase. `ruff check marquee tests`, `git diff --check`, OpenAPI drift (193 paths), and
  static TypeScript regeneration all passed. No schema, reset fingerprint, API contract,
  frontend source, skip, or xfail changed.
- Current phase: A1 complete; A2 next.
- Exact next steps: implement domain-separated signed 64-bit lock keys, validated global
  requirement ordering, dedicated direct-asyncpg session lock handles, cancellation-aware
  partial acquisition/release, shared/exclusive maintenance exclusion, and honest
  readiness/connection-budget diagnostics without exposing raw keys.
- Deviations: none from the locked filesystem contracts. Existing internal tool-owned
  cleanup remains visible in the static inventory; public serving and route mutation no
  longer bypass the filesystem authority.
- Pending operator work: real concurrent symlink/path-swap and archive fixture smokes on the
  deployment filesystem remain pending. The inherited operator work above is unchanged.

## Phase A2 — advisory safety gates and maintenance barrier

- Phase commit: `ee4127f` (`add advisory safety gates`), authored solely by the configured
  repository user.
- Added definition-owned safety policy, domain-separated BLAKE2b v1 signed-`bigint` keys,
  validated media-file → media-write permit → GPU permit → shared-maintenance ordering, and
  exclusive-maintenance isolation. Permit numbers derive deterministically from a
  server-owned allocation identity and snapshotted capacity; clients never supply gates or
  see raw keys.
- The lock service holds session advisory locks on a dedicated direct asyncpg connection
  named `marquee:worker:safety-gate`, polls without blocking the event loop, publishes only
  friendly wait reasons, observes cancellation/deadline, releases partial sets in reverse
  order, explicitly unlocks normally, and closes the connection as the final fail-safe.
- Readiness now reports sanitized safety-gate availability. Connection arithmetic reserves
  four gate sessions per worker: API 15 + worker 8 + scheduler 3 + migration 1 = **27**
  configured against the 32-connection deployment limit.
- Focused certification: **8 passed** for stable/domain-separated keys, contradictory and
  duplicate requirements, policy/capacity resolution, real PostgreSQL same-file contention,
  numbered permits, shared/exclusive maintenance, cancellation after partial acquisition,
  connection-close release, and updated budget enforcement.
- Full retained suite: **909 passed, 21 failed, 11 errors, 2 warnings** in 64.19s. The seven
  new A2 tests are the only delta from A1; the retained failure/error set is identical.
  `ruff check marquee tests`, `git diff --check`, and OpenAPI drift (193 paths) passed. No
  schema, generated contract, frontend source, skip, or xfail changed.
- Current phase: A2 complete; A3 next.
- Exact next steps: generalize delivery through the registry, introduce immutable delivery
  and attempt contexts, acquire A2 gates before admission, row-lock and recheck canonical
  state, atomically advance attempt/fence ownership, and route every terminal/result/error
  mutation through one compare-and-set writer.
- Deviations: none. PostgreSQL fairness is bounded by cancellation-aware polling rather than
  claimed strict FIFO behavior; measured contention latency remained inside the configured
  focused-test deadlines.
- Pending operator work: multiprocess contention and database-connection-loss smokes on the
  deployment PostgreSQL instance remain pending. Existing filesystem/cgroup/process and
  inherited operator work remains unchanged.

## Phase A3 — fenced admission and canonical writer

- Phase commit: `515ee36` (`fence job execution`), authored solely by the configured
  repository user.
- Replaced the hard-coded no-op lifecycle wrapper with a registry-resolved execution kernel.
  Strict transport parsing now builds immutable delivery/preflight context, validates the
  stored JMC2 request, computes definition-owned gates, waits before audit admission, then
  row-locks and rechecks dispatch/generation/definition/intent before atomically incrementing
  the fence and inserting the current attempt. Only `system_noop` has a production handler.
- Production handlers receive immutable request/configuration/subject snapshots and narrow
  cancellation, gate, and writer capabilities; they do not receive ORM sessions, canonical
  rows, PgQueuer rows, client execution policy, or raw paths. The optional two-argument
  executor remains test-only for the fixed no-op matrix.
- Added one compare-and-set writer for success, retry, stopping, failure, and cancellation.
  Every job predicate includes canonical ID, current attempt ID, fence, dispatch generation,
  expected phase, desired state, and nonterminal outcome. Attempt/dispatch/event updates
  share the transaction. Zero-row writes return explicit `stale` or `conflict`; JMC2 result
  and safe-error documents are validated before mutation.
- Focused certification: **47 passed** across delivery, commands, registry, and evidence;
  the delivery module alone passed **27** cases including safety wait with zero attempts,
  pause/cancel before admission, simultaneous duplicate suppression, serial terminal
  redelivery, disabled definitions, stale fence ownership, retry-before-signal, and
  terminal-before-PgQueuer-return.
- Full retained suite: **914 passed, 21 failed, 11 errors, 2 warnings** in 65.64s. The five
  new A3 cases are the only delta from A2; the retained failure/error set is identical.
  `ruff check marquee tests`, `git diff --check`, and OpenAPI drift (193 paths) passed. No
  schema, generated contract, frontend source, skip, or xfail changed.
- Current phase: A3 complete; A4 next.
- Exact next steps: add the fixed-tool tracked launcher, durable boot/PID/start identity,
  mandatory POSIX process groups, honest optional-cgroup capability, concurrent pipe
  draining, cooperative/TERM/KILL cancellation with positive death confirmation, and
  identity-safe startup orphan reconciliation using fixed canaries only.
- Deviations: system-noop canonical result/error storage now conforms to the already-locked
  JMC2 `BuiltInResultV1`/`SafeJobErrorV1` models instead of retaining the invalid pre-A3 echo
  and exception dictionaries. No public schema changed because canonical documents remain
  typed JSON at that boundary.
- Pending operator work: real picked-ticket loss/restart and concurrent multiworker admission
  smokes remain pending. Existing lock/filesystem/cgroup/process and inherited operator work
  remains unchanged.

## Phase A4 — process containment, cancellation, and orphan reconciliation

- Phase commit: `b9460d5` (`contain job processes`), authored solely by the configured
  repository user.
- Added a closed-catalog exec launcher with no shell or arbitrary executable/argument/env/
  cwd surface. It reclassifies the confined cwd, creates a new POSIX session/process group,
  persists node + boot ID + PID + process-group + `/proc` start ticks before opening a fixed
  start barrier, drains both pipes concurrently beyond bounded capture, and records bounded
  exit evidence. Fixed canaries cover clean, high-output, cooperative, KILL-only tree,
  staged-file, and failure behavior.
- Cancellation verifies complete durable identity before every group signal, escalates
  cooperative → TERM → KILL, reaps the leader, and positively confirms the group is gone
  before returning. Identity mismatch/unknown states never signal. Startup reconciliation
  scans at most 50 current attempts for the stable configured node, safely interrupts only
  processless or positively dead attempts, and terminalizes partial/mismatched/unknown
  identity as `unsafe` without speculative signaling.
- Worker-node telemetry now records stable node/build/boot identity and honest containment
  capability. The tested environment reports `process_group`: cgroup v2 remains mounted but
  not delegated, so cgroup containment is not claimed. Readiness exposes the sanitized tier;
  pure `system_noop` remains available.
- Focused certification: **53 passed** across process launching, dual 1 MiB pipe drainage,
  event-loop responsiveness, cooperative and forced whole-tree cancellation, identity
  mismatch refusal, delivery duplicate behavior, readiness, and static runtime checks.
  Full retained suite: **924 passed, 21 failed, 11 errors, 2 warnings** in 66.28s. The ten
  new A4 tests are the only delta from A3; the retained failure/error set is identical.
  `ruff check marquee tests`, `git diff --check`, and OpenAPI drift (193 paths) passed.
- Current phase: A4 complete; A5 next.
- Exact next steps: restore the already-focused attempt-workspace integration, add durable
  publish intent plus fence-locked same-directory replacement/evidence, quarantine ambiguous
  crash state, run the bounded public-gateway transport-intent monitor, then execute the full
  JMC3A certification matrix.
- Deviations: no cgroup smoke was possible because the host exposes no writable delegated
  subtree. The process-group fallback was exercised with a real forked descendant. The old
  delivery-time queue-manager-ID supersession test was replaced because it violated E9/E13;
  delivery now never guesses that a running attempt is abandoned.
- Pending operator work: delegated-cgroup `cgroup.kill`/`populated=0`, deployment-host
  process-kill, and restart-orphan smokes remain pending. Existing PostgreSQL, filesystem,
  and inherited operator work remains unchanged.

## Phase A5 — staging, atomic publish, consistency monitor, and certification

- Phase commit: `2e1197f` (`stage and publish attempts`), authored solely by the configured
  repository user. JMC3A implementation commits are A0 `b312cc5`, A1 `24675b8`, A2
  `ee4127f`, A3 `515ee36`, A4 `b9460d5`, and A5 `2e1197f`; each preceding phase also has
  its adjacent shared-timeline commit.
- Every admitted attempt now receives an exclusive confined `DATA_DIR` workspace keyed by
  canonical job/attempt/fence identity and a launcher bound to that cwd. The coordinator
  shuts down and positively confirms all launched process trees before sealing state,
  cleaning/quarantining staging, or releasing advisory gates. Fixed staged-file behavior is
  compiled in; no HTTP/queue contract accepts executable, arguments, env, cwd, destination,
  or raw path.
- The publish coordinator requires a regular single-link bounded file staged in the exact
  destination directory/root. It validates SHA-256/device/inode/size/mtime signatures,
  rejects changed destinations/stale fences/oversize/cross-root or cross-directory work,
  fsyncs data, and uses the A1 no-`EXDEV` atomic replace. Durable publish intent commits
  before mutation; the final fence/desired-state row lock spans only the replace/evidence
  boundary. A post-replace commit ambiguity therefore retains a deterministic intent/hash
  for quarantine rather than duplicate publication.
- Startup workspace reconciliation scans at most 100 confined identity-shaped directories.
  It deletes only exact finished attempts with no ambiguous publish intent, leaves active
  attempts untouched, and writes bounded quarantine evidence for missing/mismatched or
  intent-without-publication state. Quarantined workspaces cannot be auto-deleted.
- The scheduler now runs a low-frequency, bounded (`25`, maximum `100`) canonical intent
  monitor using `FOR UPDATE SKIP LOCKED`. It calls only public `PgQueuerGateway` status and
  known-ticket cancellation methods. It cannot enqueue, inspect raw PgQueuer tables, recover
  heartbeats, create attempts, or infer handler success; missing/mismatched/paused linkage is
  surfaced as canonical attention.
- Optional delegated cgroup v2 support creates a per-runner cgroup before the start barrier,
  records its path, uses `cgroup.kill` for final escalation, and requires
  `cgroup.events populated=0` before cleanup. Recovery validates the confined compiled cgroup
  name and membership before use. The certified host remained on the real process-group
  fallback because delegation is unavailable.
- Focused A5/integrated certification: **59 passed**. Full retained suite: **930 passed,
  21 failed, 11 errors, 2 warnings** in 65.84s. The six new A5 tests are the only pass-count
  delta from A4; the exact 21 retained failures and 11 `CREATEDB` privilege errors remain
  unchanged from JMC2C/A0. No skips or xfails were introduced.
- Final static/contracts/schema/frontend certification: `ruff check marquee tests` passed;
  `git diff --check` passed; sole Alembic head remains `0002_jmc2a`; OpenAPI remains current
  at 193 paths; frontend check passed with the retained 16 warnings/0 errors in 8 files;
  frontend lint and production build passed. No migration, schema fingerprint, OpenAPI,
  TypeScript contract, or frontend source changed.
- Performed real smokes: local PostgreSQL advisory lock contention/connection release from
  A2; real POSIX sessions/groups, a forked TERM-ignoring descendant, cooperative/TERM/KILL
  escalation, dual 1 MiB pipe drain, and death confirmation; confined traversal/symlink/
  descriptor-swap/archive/`EXDEV` fixtures from A1; real same-filesystem fsync/atomic replace,
  changed-destination/stale-fence rejection, and synthetic post-replace commit ambiguity.
  No public development canary or real feature handler was enabled.
- Current phase: **JMC3A complete and certified**. Exact JMC3B starting point: extend the
  immutable `ExecutionContext`, `ProcessLauncher` summaries, `FencedWriter`, and attempt
  workspace with physical bounded log/artifact/progress/event sinks without changing
  PgQueuer transport ownership, process/publication fencing, or enabling feature handlers.
- Deviations: no locked contract was weakened. The cgroup-supported path is implemented and
  unit/static inspected but cannot be live-smoked on this non-delegated host. The optional
  public development CLI was intentionally not added; fixed canaries remain test-only.

## Final operator handoff

- Still required on the deployment host: delegated-cgroup `cgroup.kill` plus
  `populated=0` smoke when delegation is configured; SIGKILL/restart while a real ticket is
  picked; startup orphan reconciliation across worker restart; PostgreSQL restart and
  notification disruption; multiprocess gate contention/connection-loss; concurrent
  symlink/path-swap on the deployment filesystem; and migration rerun with held/queued/
  deferred tickets.
- Rerun the 11 isolated migration tests with a PostgreSQL role allowed to create databases.
  Apply `0002_jmc2a` or the sanctioned development reset to the live development database as
  inherited from JMC2. Evaluate the four low-severity npm advisories separately.
- Production dispatch remains exactly `control/system_noop`. JMC3B must not treat
  `JobAttempt`, `WorkerNode`, workspace state, or monitor selection as a lease/claim/queue;
  PgQueuer remains the sole delivery, heartbeat recovery, retry timing, transport
  cancellation, and concurrency authority.

## JMC3B Phase B0 — contract freeze complete (2026-07-13)

- Branch/HEAD: `job-manager` at `da9f009c7eefa2dcf63ad38d9d10edcc4ab55876`
  (`complete jmc3a timeline`), tracking `origin/job-manager`. The tracked working tree was
  clean and no overlapping agent/operator work was present. Configured Git author remains
  `Gautam Chaudhri <gautam.chaudhri@gmail.com>`.
- Verified every JMC3A implementation commit is an ancestor of HEAD: A0 `b312cc5`, A1
  `24675b8`, A2 `ee4127f`, A3 `515ee36`, A4 `b9460d5`, and A5 `2e1197f`, with the recorded
  timeline commits between phases. Serena symbol/reference analysis reconfirmed fence
  predicates include job/attempt/fence/generation/state, publication rechecks under that
  fence, process shutdown proves tree death before gate release, and only
  `control/system_noop` is production-enabled. Fixed canaries remain closed-enum/test-only.
- Focused prerequisite certification: **70 passed** across the JMC3A contract freeze,
  filesystem confinement/static inventory, advisory gates, delivery/fence behavior, process
  containment/cancellation, staging/publication, and the bounded transport-intent monitor.
  The first sandboxed attempt reached no tests because local sockets were denied; the same
  suite passed with the repository-approved isolated PostgreSQL socket permission.
- Baseline on the ordinary test role: **930 passed, 21 retained failures, 11 known
  `CREATEDB` privilege errors, 2 warnings**. Baseline on an owned disposable PostgreSQL 18.3
  UTF-8 cluster at `/tmp/marquee-jmc3b-pg.mwB8uw`, port 55442: **941 passed, 21 retained
  failures, 2 warnings**; all 11 migration cases executed and passed. The 21 failures are
  exactly the JMC3A/JMC2C retained application set; no failure, error, skip, or xfail was
  added.
- Schema: sole Alembic head `0002_jmc2a`; offline SQL generated successfully (1,332 lines);
  guarded reset and `alembic check` passed on the owned cluster. Contract fingerprints match
  JMC3A: Marquee `651ba3f0efe8fffb6a262b95962d9a99b9568609b2bd52936cf34bfac04aba89`;
  PgQueuer `19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a`
  (1.1.1, durable). The operator database still reports the inherited obsolete revision
  `b1c2d3e4f5a6`; applying `0002_jmc2a` remains explicit pending operator work, not a target
  schema regression.
- Generated/backend/frontend baseline: `ruff check marquee tests` passed; OpenAPI drift
  passed at 193 paths; static TypeScript regeneration with `openapi-typescript` 7.13.0 was
  clean. Frontend check passed with 0 errors and the same 16 warnings in 8 files; lint and
  production build passed. `git diff --check` is required again at the phase gate.
- Disposable evidence roots: database cluster above; physical evidence root
  `/tmp/marquee-jmc3b-evidence.Y3eBfY` on a 12,260,768 KiB tmpfs with 11,166,260 KiB free at
  baseline. Tests continue to receive isolated per-session `DATA_DIR` roots under `/tmp`;
  no operator `DATA_DIR`, media, backup, model, or artifact path was modified.
- Resource budgets: connection arithmetic remains API 15 + one worker 8 (including four
  safety-gate sessions) + scheduler 3 + migration 1 = **27 configured / 32 maximum**. JMC3B's
  one event-listener connection per API instance must fit explicitly before B1 readiness
  changes. The host soft file-descriptor limit is **524,288**; JMC3B has no existing separate
  application FD budget, so B0 freezes bounded listener/client/log-handle requirements before
  physical capture is admitted.
- Phase commit: `400f724` (`freeze jmc3b evidence contracts`), authored solely by the
  configured repository user. The machine-readable freeze preserves the JMC2 presentation,
  progress, and read-API test inputs; exact `JobEvent`/`JobLog`/`JobArtifact` columns; progress
  model fields; immutable JMC3A execution context and writer/launcher extension points; all
  13 direct `JobEvent` constructors; current jobs GET routes; logging, subprocess, filesystem,
  and artifact surfaces; and an explicit removal/replacement disposition for every obsolete
  process-local helper.
- The B2 redaction corpus now covers configured exact values, Bearer and API-key headers, URL
  userinfo/query secrets, split and joined secret arguments, full database-URL arguments,
  bounded cross-chunk matching, and unchanged safe text. It contains synthetic values only
  and locks `[REDACTED]` as the replacement marker without claiming that redaction is already
  implemented.
- Focused B0 certification: **10 passed**; the focused B0 plus inherited JMC2 contracts are
  **30 passed**. Final full owned-database suite: **951 passed, 21 retained failures,
  2 warnings** in 49.66s. The ten new B0 tests are the only pass-count delta from the JMC3A
  owned-role baseline; the exact 21 failures and warnings are unchanged.
- Final B0 static/schema/generated/frontend gate: `ruff check marquee tests`, Serena file
  diagnostics, `git diff --check`, owned-cluster `alembic check`, deterministic OpenAPI and
  TypeScript regeneration, frontend check/lint/build all passed. Frontend diagnostics remain
  the inherited 16 warnings and 0 errors in 8 files. No production source, database schema,
  OpenAPI document, generated TypeScript, frontend source, or enabled job type changed.
- Current phase: **B0 complete; B1 next**. Exact next steps: introduce the sole durable event
  writer with post-commit notification, replace all direct event construction, add one
  reconnecting tailer/listener per API instance, expose only `GET /api/jobs/events/stream`
  with bounded replay/reset/client queues, and certify lifecycle/command ordering plus
  readiness/health without enabling any additional handler.
- Deviations: RTK raw-proxy form remains required for repository virtualenv tools. The first
  disposable cluster was initialized as SQL-ASCII and was stopped after psycopg rejected its
  byte-valued server version; the UTF-8 owned cluster above replaced it. No repository or
  operator state was changed by that environment correction.
- Pending operator work is unchanged from JMC3A: deployment-host cgroup, picked-ticket
  SIGKILL/restart/orphan, PostgreSQL restart/notification disruption, multiprocess lock and
  connection-loss, concurrent filesystem race, held/queued/deferred migration rerun, and
  live development migration/reset smokes remain unperformed.

## JMC3B Phase B1 — durable events and multiplexed SSE complete (2026-07-13)

- Phase commit: `e6a451e` (`stream durable job events`), authored solely by the configured
  repository user.
- B1 began from clean timeline commit `9899c19` immediately after B0 commit `400f724`;
  branch, configured author, owned UTF-8 PostgreSQL cluster, isolated evidence roots, and
  sole enabled `control/system_noop` registry were unchanged. No overlapping work appeared.
- The B0 inventory's 13 direct constructors have been removed. `JobEventWriter.append` is
  now the sole production `JobEvent(...)` constructor; it validates the finite semantic
  vocabulary and bounded detail, inserts inside the caller's state transaction, flushes the
  one global `job_events.id` cursor, and issues exactly one transactional `pg_notify` on
  `marquee_job_events_v1` containing only the decimal cursor. Caller commit/rollback remains
  authoritative, and rollback/commit notification behavior is exercised against PostgreSQL.
- One `JobEventTailer` per API instance owns one direct asyncpg listener connection, starts
  before readiness succeeds, repairs missed notifications periodically in bounded 256-row
  ranges, reconnects after degradation, and fans each cursor range through per-client
  bounded 128-frame queues. The deployment connection arithmetic is now API pool 15 + API
  listener 1 + worker 8 + scheduler 3 + migration 1 = **28 configured / 32 maximum**.
- `GET /api/jobs/events/stream` is registered before dynamic job routes and is the sole live
  job-event stream. Native `Last-Event-ID` is authoritative; a matching `after` query is
  allowed. Frames expose the durable cursor, committed canonical fence version, bounded
  allowlisted delta, and snapshot/presentation reconciliation links. Keepalives have no ID.
  Invalid/future/expired/over-limit cursors and slow-client overflow emit one
  `stream.reset_required` frame and close that client without blocking the shared tailer.
- Performed B1 smokes include real commit-only PostgreSQL notification, rollback silence,
  listener wakeup and once-only range tailing, repair wakeup, retained replay, retention-gap
  reset, invalid-cursor SSE reset/close, header/query disagreement, slow-client isolation,
  idempotent one-listener startup, canonical-version ordering, detail rejection, and public
  result/internal-metadata omission.
- Focused B1/inherited certification: **90 passed**. Final full owned-database suite:
  **964 passed, 21 retained failures, 2 warnings** in 52.51s. The 13 new B1 tests are the
  only pass-count delta from B0; the exact retained failures/warnings remain unchanged.
  `ruff check marquee tests`, `git diff --check`, owned-cluster `alembic check`, OpenAPI
  export/check, TypeScript generation, frontend check/lint/build all passed. OpenAPI is now
  current at **194 paths**; frontend diagnostics remain 16 warnings/0 errors in 8 files.
- Current phase: **B1 complete; B2 next**. Exact next steps are B2's central
  configured/structural/cross-chunk redactor, confined attempt
  JSONL sink, scoped Python logging and launcher pipe integration, 100 MiB cap with reserved
  single truncation record while pipes continue draining, atomic seal/gzip/recovery, and
  bounded cursor/SSE/download APIs without enabling another handler.
- Deviation: the B1 phase heading was appended at the certification gate instead of before
  the first source edit. The timeline still records the exact clean starting hash and all
  pre-edit B0 facts; no gate, ownership check, or scope decision was skipped. No schema
  migration was needed because the committed canonical version is stored as bounded internal
  event metadata and stripped from legacy detail responses while exposed as a typed field.

## JMC3B Phase B2 — attempt log capture complete (2026-07-13)

- B2 starts from clean timeline commit `bd9ebcd`, immediately after B1 implementation commit
  `e6a451e`. Branch/author/owned UTF-8 database and evidence roots are unchanged, no
  overlapping work is present, and only `control/system_noop` remains enabled.
- Retained starting gate: **964 passed, 21 inherited failures, 2 warnings**; focused B1 gate
  **90 passed**; backend/schema/OpenAPI/TypeScript/frontend gates passed at 194 paths. B2 may
  add passes or shrink the retained failure set but may not add a failure, error, skip, or
  xfail.
- B2 resource contract before edits: attempt-log cap **100 MiB persisted sanitized UTF-8**
  across segments including the reserved truncation record; one confined active handle per
  running attempt; launcher stdout/stderr remain concurrently drained after truncation; API
  log reads use transient pooled sessions and lazy file streaming, not per-client database
  polling. Default terminal retention is 30 days; active attempts do not expire.
- Exact next steps: implement the B0 redaction corpus with configured and structural rules;
  add stable bounded JSONL line/cursor contracts; attach scoped Python records and launcher
  pipe sinks through immutable execution context; reserve and emit exactly one truncation
  record; atomically seal/checksum/gzip on every terminal path; recover only after proven
  process death; expose bounded cursor list/SSE/final download routes; and certify high-output
  no-deadlock, cross-chunk secrecy, terminal/retry/cancel sealing, and safe lazy download.
- Pending operator work remains B1/JMC3A's deployment-host restart/notification/cgroup/
  filesystem/migration matrix. Deployment-host long-running capture and storage-capacity
  smokes are added to that list; no operator data was modified and no real handler enabled.
- Phase commit: `542df2e` (`capture bounded attempt logs`), authored solely by the configured
  repository user. One `JobLog` row owns each attempt's confined segment-0 JSONL file and
  records stable cursors, sanitized byte/line counts, truncation, compression, stored size,
  SHA-256, seal/recovery state, failure code, and 30-day terminal expiry. Sole Alembic head is
  now `0003_jmc3b`; owned-cluster upgrade, all 14 migration tests, offline SQL (1,352 lines),
  and `alembic check` passed.
- The central redactor covers the frozen configured/structural/argv/URL/header corpus and
  keeps an overlap across arbitrary stdout/stderr chunks. Scoped `ContextVar` Python capture
  and launcher pipe capture feed one bounded queue; a same-loop ordering race found during
  certification was fixed so records cannot land behind the seal sentinel. The fixed 100 MiB
  sanitized cap reserves and emits exactly one typed truncation record, then discards while
  both pipes continue draining.
- Success, retry, cancellation, and failure paths seal only after process-tree death. Seal is
  fsync + confined same-directory gzip replacement + checksum; startup recovery claims only
  finished attempts whose recorded process identity is positively dead or absent, recognizes
  an already-gzipped ambiguity, and never double-compresses. A current fence emits exactly one
  committed `log.available`; stale attempts retain evidence without publishing canonical
  semantics. Capture/seal failure is isolated from the media effect and reported as degraded
  evidence.
- Added bounded cursor list/filter, file-polled bounded SSE, and sealed download APIs. SSE
  closes its transient database session before streaming and never polls the database;
  download verifies checksum, stored size, and physical compression and distinguishes active,
  expired, unavailable, missing, and corrupt evidence. Readiness now probes the confined log
  root. OpenAPI and generated TypeScript are current at **197 paths**.
- Focused B2 contract tests: **16 passed**; delivery/launcher/readiness/log integration:
  **65 passed**; migration suite: **14 passed**. Final exact retained suite with the recorded
  `DEBUG=true` baseline setting: **970 passed, 21 retained failures, 2 warnings** in 53.89s.
  The six new B2 tests are the only pass-count delta from B1; the exact failure/warning set is
  unchanged and no skip or xfail was added. An earlier `DEBUG=false` run was discarded because
  it changes development-route registration and is not the frozen baseline.
- `ruff check marquee tests`, `git diff --check`, Serena structural diagnostics (apart from its
  known virtualenv import-resolution noise), OpenAPI export, TypeScript generation, frontend
  check (the inherited 16 warnings/0 errors), lint, and production build passed. Production
  dispatch remains exactly `control/system_noop`; no raw executable, argument, environment,
  cwd, destination, or path surface was introduced.
- Current phase: **B2 complete; B3 next**. Exact next steps: implement the physical/virtual
  artifact registry over the existing `JobArtifact` table, validate attempt/fence provenance,
  confine immutable physical bytes and lazy downloads, expose bounded inventory/download
  contracts, and add startup reconciliation without duplicating pipeline execution.
- Deviations: the first focused commands used the ineffective `DATABASE_URL` name rather than
  this repository's `DB_URL`; those fixtures created and dropped only isolated schemas and
  migration setup stopped before database creation. All final schema/integration/full-suite
  evidence uses the owned port-55442 cluster. ByteRover's local `brv` CLI remained absent, so
  the required swarm helper could not run; normal ByteRover context tools remain available.

## JMC3B Phase B3 — physical and virtual artifacts complete (2026-07-13)

- B3 starts from clean timeline commit `33c36ba`, immediately after B2 implementation commit
  `542df2e`. The owned database/evidence roots, configured author, 28/32 connection budget,
  and sole enabled `control/system_noop` definition remain unchanged.
- Retained starting gate: **970 passed, 21 inherited failures, 2 warnings**; focused B2 gate
  **65 passed**; sole Alembic head `0003_jmc3b`; OpenAPI/TypeScript current at 197 paths.
- Exact next steps: add policy-owned physical artifact staging into an immutable confined
  root, bounded deterministic virtual canonical documents, attempt/fence provenance, typed
  unavailable/failed/expired metadata, checksum-verified lazy download, bounded cleanup and
  reconciliation alerts, and bounded artifact/log availability links in JMC2 snapshots and
  presentations. No caller-supplied existing path or production handler will be enabled.
- Phase commit: `138c866` (`store confined job artifacts`). Physical artifacts accept only a
  JMC3A-classified source and a policy-owned kind/name/content-type/retention/metadata set.
  They stream into a confined per-job/per-artifact temporary object with a kind-specific cap,
  SHA-256 and byte count, fsync, mode `0400`, and same-directory atomic publication before the
  database row becomes available. Failed staging leaves bounded failure evidence and a
  current-fence `artifact.failed` event; no caller path can be published as available.
- Virtual artifacts are restricted to canonical request, plan, result, error, and bounded
  event documents. Encoding is deterministic, recursively bounded and centrally redacted;
  event artifacts freeze a through-cursor rather than representing a moving query. Terminal
  delivery registers canonical result/error evidence without changing the durable terminal
  outcome if evidence registration itself degrades.
- Artifact inventory hides storage keys and exposes availability plus a job-bound download
  URL. Downloads materialize virtual documents deterministically and verify their recorded
  checksum/size; physical downloads verify the confined object before lazy response. Expired,
  unavailable, missing, corrupt, and wrong-job requests are distinct, with safe attachment
  disposition and `nosniff` headers. Presentations derive bounded log/artifact availability
  in their existing query budget.
- Expiration uses bounded `SKIP LOCKED` claims, marks rows expired, emits current-attempt
  `artifact.expired`, and performs idempotent confined deletion. Startup reconciliation is
  alert-only for missing tracked objects and untracked managed files; it never adopts or
  deletes ambiguous data. Readiness now probes the artifact root alongside attempt logs.
- Focused artifact/API/freeze contracts: **30 passed**; delivery/readiness/log/event
  integration: **61 passed**. Final exact retained suite with `DEBUG=true`: **975 passed,
  21 retained failures, 2 warnings** in 55.50s. The five new B3 tests are the only pass-count
  delta from B2; the exact failure/warning set is unchanged and no skip or xfail was added.
- Sole Alembic head/current remains `0003_jmc3b` and `alembic check` reports no drift. OpenAPI
  and generated TypeScript are current at **198 paths**. `ruff check marquee tests`,
  `git diff --check`, frontend check (the inherited 16 warnings/0 errors), lint, and production
  build passed. Production dispatch remains exactly `control/system_noop`.
- Current phase: **B3 complete; B4 next**. Exact next steps: implement the sole fenced progress
  writer, persist bounded canonical progress documents, add strategy-owned adapters and
  parent projection, and connect durable progress transitions to the existing snapshot/event
  delivery surfaces without enabling another production handler.
- Deviations: the first Alembic current command inherited the repository's invalid
  `DEBUG=release` environment value and stopped during settings validation; the immediate
  `DEBUG=true` rerun passed current and drift checks against the owned cluster. ByteRover's
  local `brv` CLI remains absent, so the required swarm helper could not run; normal ByteRover
  context tools remain available.

## JMC3B Phase B4 — progress writer, adapters, and parent projection complete (2026-07-13)

- B4 starts from clean timeline commit `caffa9a`, immediately after B3 implementation commit
  `138c866`. The owned database/evidence roots, configured author, resource budgets, and sole
  enabled `control/system_noop` definition remain unchanged.
- Retained starting gate: **975 passed, 21 inherited failures, 2 warnings**; focused B3 gates
  **30 passed** and **61 passed**; sole Alembic head `0003_jmc3b`; OpenAPI/TypeScript current
  at 198 paths; backend/frontend/schema gates passed.
- Exact next steps: implement the sole transactional fenced progress writer with server-owned
  sequence/percent/credible ETA authority; reject stale, duplicate, incompatible, regressive,
  and wrong-fence observations; add bounded per-attempt coalescing and forced flush/failure
  isolation; implement pure partial-record FFmpeg, mkvmerge, and indeterminate adapters; add
  bounded synthetic sealed-child parent projection; and route the fixed no-op canary through
  this service without enabling any other production definition.
- Phase commit: `c0959dc` (`persist fenced job progress`). One transactional writer locks and
  validates the canonical job/attempt/fence, resolves the registered definition policy,
  materializes percentages and credible rate-backed ETA server-side, allocates the next
  durable sequence, stores the typed snapshot/current projections, and appends
  `progress.updated` in the same transaction. Stale producer ordinals, wrong ownership,
  disallowed stages/subjects/units, incompatible scope transitions, shrinking totals,
  regressions, malformed stored state, and terminal replacement are rejected.
- A latest-only per-attempt coalescer bounds pending state to one observation, flushes stage,
  subject, scope, mode, wait, warning, failure, meaningful-delta, and maximum-staleness
  transitions, retries isolated persistence failures at bounded cadence, and performs a
  bounded shutdown flush. Progress failure is operationally logged and cannot change the
  handler's media effect or erase the last good durable snapshot.
- Pure fixture-driven FFmpeg and mkvmerge adapters accept only their machine-readable
  progress protocols, preserve partial records, bound malformed buffers, reject regression,
  and degrade invalid/missing durations to indeterminate work. The opaque adapter emits only
  liveness. None owns process launch, cancellation, logs, lifecycle, or publication.
- Synthetic parent projection reads at most 500 ordered registered children, requires the
  policy-owned sealed denominator before determinate progress, distinguishes terminal outcome
  classes through bounded warning/failure summaries, and projects one primary plus at most
  eight concurrent subjects through the parent fence. No production batch handler was
  enabled.
- The fixed `system_noop` canary publishes a policy-valid observation through the same service.
  Fenced terminalization advances the sequence, preserves failure measurements, completes
  credible success/no-change measurements, marks freshness terminal, and emits the final
  progress event inside the terminal transaction. A synthetic progress database failure was
  proven not to alter the successful canary effect.
- Focused progress/lifecycle gate: **41 passed**; broader event/evidence/presentation gate:
  **77 passed**. Final exact retained suite with `DEBUG=true`: **982 passed, 21 retained
  failures, 2 warnings** in 54.76s. Seven new B4 tests are the only pass-count delta from B3;
  the exact retained failure/warning set is unchanged and no skip or xfail was added.
- Sole Alembic head/current remains `0003_jmc3b` and `alembic check` reports no drift. OpenAPI
  and generated TypeScript remain deterministic at **198 paths**. `ruff check marquee tests`,
  `git diff --check`, frontend check (the inherited 16 warnings/0 errors), lint, and production
  build passed. Serena diagnostics found only its known virtualenv import-resolution noise;
  the pure adapter module had no diagnostics.
- Current phase: **B4 complete; B5 next**. Exact next steps: run the integrated canary across
  process identity/death, cancellation, progress, logs, artifacts, event replay/overflow,
  restart repair, expiration, and filesystem boundaries; certify all generated contracts and
  retained gates; record JMC3C evidence-sealing/backup integration points and operator smokes;
  and curate the final architecture if ByteRover becomes available.
- Deviations: B4's first database-backed verification was delayed when the execution approval
  quota was exhausted; no alternative database or bypass was used, and the required owned
  cluster gate passed immediately after work resumed. ByteRover remains unavailable because
  the required local `brv` executable is absent; both query modes were retried and failed with
  exit 127. Deployment-host restart/notification/cgroup/filesystem/retention-capacity smokes
  remain operator work; no operator data was changed.

## JMC3B Phase B5 — integrated canary and contract certification complete (2026-07-13)

- B5 starts from clean timeline commit `5c4e9bd`, immediately after B4 implementation commit
  `c0959dc`. The owned PostgreSQL/evidence roots, configured author, resource budgets, and sole
  enabled `control/system_noop` definition remain unchanged.
- Retained starting gate: **982 passed, 21 inherited failures, 2 warnings**; focused B4 gates
  **41 passed** and **77 passed**; sole Alembic head `0003_jmc3b`; OpenAPI/TypeScript current
  at 198 paths; backend/frontend/schema gates passed.
- Exact next steps: add an integrated fixed-canary proof spanning fenced execution, progress,
  redacted/sealed logs, canonical artifacts, ordered semantic events, bounded replay/overflow,
  restart recovery, expiration, and confined downloads; rerun every JMC3B/backend/frontend/
  schema/generated-contract gate; record performed and pending operator smokes plus the exact
  JMC3C evidence-sealing/backup integration points; and attempt final ByteRover curation.
- Phase commit: `be65864` (`certify integrated job evidence`). The fixed `system_noop` canary
  now proves one PgQueuer delivery reaches fenced terminal success with attempt provenance,
  server-sequenced terminal progress, a checksummed sealed gzip log, an allowlisted virtual
  result artifact, globally ordered semantic events, and bounded snapshot, presentation,
  log, artifact, download, and event APIs. Production enablement remains only
  `control/system_noop`.
- The integrated canary passed alone, and the combined process identity/death, cooperative
  cancellation, staging monitor, filesystem boundary/static safety, PgQueuer delivery,
  readiness, progress, log sealing/recovery/retention, artifact expiration/reconciliation,
  event replay/repair/retention-gap/slow-client overflow, and frozen-contract matrix passed
  **138 tests**. These automated restart, repair, overflow, expiry, download, and process-death
  proofs preserve the JMC3A rule that evidence degradation cannot change the media outcome.
- Final exact retained suite with `DEBUG=true`: **983 passed, 21 retained failures, 2
  warnings** in 58.08s, with 1,004 tests collected. The single new B5 test is the only pass
  delta from B4; the inherited failure/warning set is exact and no skip or `xfail` was added.
  Ruff, `git diff --check`, sole Alembic head/current `0003_jmc3b`, and `alembic check` passed.
- OpenAPI and generated TypeScript are deterministic at **198 paths**. Frontend check passed
  with the same 16 warnings/0 errors in 8 files; lint and production build passed. Serena
  diagnostics on the new test reported only the established virtualenv import-resolution
  noise.
- Final event handoff: `job_events.id` is the global durable cursor; transactional notifications
  use `marquee_job_events_v1` only as wakeup hints. One listener per API instance tails at most
  256 rows per range and fans out to bounded 128-frame client queues; replay is bounded to 128.
  Invalid, future, expired, disagreement, restart-gap, and slow-client overflow paths require
  snapshot reconciliation. The configured deployment budget remains **28/32 connections**.
- Final evidence handoff: semantic state is in canonical `jobs`, `job_attempts`, `job_events`,
  `job_logs`, and `job_artifacts`; physical evidence is confined below
  `DATA_DIR/jmc3/evidence/logs` and `DATA_DIR/jmc3/evidence/artifacts`. Logs are sanitized UTF-8
  JSONL capped at exactly 100 MiB per attempt, with one reserved truncation record, 30-day
  retention, atomic gzip sealing, stored size, and SHA-256. Redaction combines configured
  secrets with structural argv, URL, header, mapping, and chunk-overlap rules before any sink.
  Virtual artifacts are capped at 1 MiB and event documents at 1,000 rows.
- Final progress handoff: determinate policies persist at 2-second cadence, 1-percent meaningful
  delta, and 10-second maximum staleness; indeterminate policies use 5/15 seconds. Stage,
  subject, scope, wait, warning, failure, and terminal transitions force persistence. FFmpeg,
  mkvmerge, and opaque adapters are pure fixture-tested parsers; synthetic parents read at
  most 500 ordered children and expose one primary plus at most eight subjects.
- JMC3C must coordinate a consistent PostgreSQL/evidence snapshot: quiesce or otherwise bind
  the canonical semantic tables to the confined log and artifact objects; define sealing
  manifests, checksums, retention metadata, and backup inclusion/exclusion; run reconciliation
  before backup and after restore so no object lacks metadata and no available metadata lacks
  its object; and implement request-size enforcement plus backup/restore certification.
  PgQueuer transport tables are operational transport, not canonical product evidence, while
  jobs, attempts, events, logs, and artifacts are mandatory backup state.
- Deployment-host operator work remains: real API/worker restart and LISTEN/NOTIFY disruption;
  cgroup-v2 descendant cancellation; actual mount, cross-device, symlink, ownership, and
  permission smokes; long-running 100-MiB/high-output/capacity behavior; and 30-day retention,
  cleanup, backup, and restore against real storage. No operator data or service was changed.
- JMC3B is complete; JMC3C may begin from implementation commits `400f724`, `e6a451e`,
  `542df2e`, `138c866`, `c0959dc`, and `be65864`. ByteRover context query, swarm query, and
  final curate were each retried through RTK and failed with exit 127 because the local `brv`
  executable is absent; no context-tree change was claimed or made.
