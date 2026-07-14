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
