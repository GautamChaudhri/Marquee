# JMC1 — PgQueuer Foundation

> **For every implementing agent:** Read `AGENTS.md`, `CLAUDE.md` when present,
> `design/plans/README.md`, the backend ground rules in
> `design/plans/04-television-backend.md` §0, and this document **in full** before writing
> code. Every decision below is locked. Do not redesign the architecture, add an outbox,
> copy PgQueuer SQL, create a replacement enqueue function, or restore the custom runtime.
> **Verify in code** means inspect the named source and relocate any stale anchors before
> editing.
>
> **Timeline protocol:** the one shared handoff log is
> `design/job-system-update/jmc1-pgqueuer-foundation-timeline.md`. The architect does not
> create it. The first implementing agent creates it before the first implementation commit.
> A later agent reads it in full, verifies every claimed commit against `git log` and the
> working tree, and resumes at the exact unfinished step. After every phase commit append:
> completed work and hash, verification, current work, exact next steps, deviations and why,
> and pending operator actions.
>
> **Git authorship is strict:** commit only as the repository's configured Git user. Never
> add yourself, a model, or an assistant as an author, co-author, contributor, or generator.
> Do not add `Co-Authored-By` trailers, “Generated with” footers, model names, or similar
> attribution to commits or files.

**Goal:** Establish the only target transport for Marquee jobs: PgQueuer 1.1.1 in durable
mode, installed independently from Marquee's clean Alembic baseline, with atomic canonical
enqueue, isolated worker and scheduler roles, truthful readiness, and a fully certified
`system_noop` path. JMC1 migrates no media or product job family.

**Plan shape:** one backend/infrastructure plan with six resumable phases and one timeline.
There is no frontend sibling.

**Prerequisites:** PostgreSQL is available for integration tests; the owner has approved
destruction of the unreleased development database. Current `DATA_DIR` contents are not part
of the JMC1 reset.

**Primary architecture:** [direct PgQueuer adoption](job-system-pgqueuer-direct-adoption.md)
and [six-chunk migration program](job-system-pgqueuer-migration.md).

## 1. Research record and corrected package contract

The 2026-07-12 planning inspection used the actual `pgqueuer-1.1.1` wheel in addition to
the [package release](https://pypi.org/project/pgqueuer/),
[release history](https://github.com/janbjorge/pgqueuer/releases), and
[official performance guidance](https://janbjorge.github.io/pgqueuer/guides/performance-tuning/).

The wheel does **not** install `fn_pgqueuer_enqueue`. The public supported path is:

```python
Queries.from_asyncpg_connection(connection).enqueue(...)
```

`Queries.enqueue()` executes the queue/log CTE on the supplied asyncpg connection. The
`AsyncpgDriver` does not commit or close a caller-owned connection. JMC1 therefore bridges
from the active SQLAlchemy `AsyncConnection` to its documented raw asyncpg driver connection
and proves transaction ownership with integration tests. This correction supersedes the
earlier installed-function wording; it does not change the no-outbox decision.

If the package installed by the implementing agent contradicts this verified 1.1.1 public
surface, stop and report the exact package metadata/source. Do not improvise around it.

## 2. Locked decisions

| ID | Decision |
|---|---|
| J1 | Pin `pgqueuer==1.1.1` exactly. Balanced and volatile durability are prohibited. |
| J2 | Alembic owns Marquee tables only. PgQueuer owns its objects through `pgq install`, `upgrade`, `durability durable`, `autovac`, and `verify`; never reproduce its DDL in Alembic. |
| J3 | Replace the unreleased Alembic chain with one clean JMC1 baseline. Chunk 2 may add forward migrations; squash again before first release. No old data, IDs, or migration compatibility is preserved. |
| J4 | Transactional enqueue uses public `Queries.enqueue()` through the active SQLAlchemy transaction's documented raw asyncpg connection. The SQLAlchemy caller is the sole commit/rollback authority. |
| J5 | No outbox, producer relay, Marquee enqueue SQL/function, copied PgQueuer query builder, second producer connection, or alternate runtime is allowed. |
| J6 | The queued payload is exactly UTF-8 JSON containing `job_id`, `payload_version`, and `dispatch_generation`. It contains no plan, media path, secret, or presentation document. |
| J7 | Transport dedupe is `marquee:{job_id}:{dispatch_generation}`. Canonical Marquee idempotency remains authoritative; PgQueuer dedupe is defense in depth. |
| J8 | Only primary entrypoint `control` and definition `system_noop` are enabled. All other definitions and legacy command writers fail closed as unmigrated. |
| J9 | No handler runs in Uvicorn. Queue manager and scheduler manager are separate roles, including embedded development child processes. Do not call `PgQueuer.run()` in both roles because it starts both managers. |
| J10 | Initial PgQueuer defaults: heartbeat timeout 60s, dequeue fallback 10s, small long-job-oriented batch, listener-failure shutdown enabled, `on_failure="hold"`, and explicit role connection budgets. |
| J11 | Add a guarded database-only development reset CLI. It does not change the public reset endpoint, delete `DATA_DIR`, or become a production upgrade path. |
| J12 | Reset requires `MARQUEE_ENVIRONMENT=development`, exact parsed database-name confirmation, a separate explicit data-loss flag, and proof that no other Marquee API/worker/scheduler/migration connection is active. Default environment is fail-safe, not development. |
| J13 | `/health/live` is process-only. `/health/ready` is dependency readiness. Existing `/health` aliases readiness temporarily. |
| J14 | A running API remains live during a later database outage and reports not-ready. Wrong/incomplete schema at startup terminates API, worker, and scheduler. |
| J15 | PgQueuer numeric IDs and tables are diagnostics, never product identifiers or public raw contracts. |
| J16 | Authentication, public reset replacement, Docker least-privilege work, webhooks, frontend/Projection Room work, non-noop definitions, and GitHub Actions modernization are out of scope. Functional process commands in Compose may change. |
| J17 | Existing failures are recorded exactly. No blanket `xfail`, quarantine, ignored test job, or assertion weakening is allowed. The retained failure set may shrink but may not grow. Obsolete tests are replaced in the same phase, never left failing. |
| J18 | Commit after each completed phase using the configured repository author only and a short lowercase imperative subject. Never use `ruff format`. |

## 3. Target boundaries

### 3.1 Schema ownership

The fresh deployment has two logical owners in one PostgreSQL database:

- **Marquee/Alembic:** canonical jobs, dispatch audit, semantic events, minimal attempt audit
  if delivery needs it, runtime schema-contract marker, and current non-obsolete domain data.
- **PgQueuer CLI:** `pgqueuer`, `pgqueuer_log`, `pgqueuer_statistics`,
  `pgqueuer_schedules`, its enum, indexes, notification function, and trigger.

The deployed JMC1 baseline excludes:

- `job_resources` and `job_resource_reservations`;
- the custom `job_schedules` and `job_workers` runtime tables;
- `media_batches`, lifecycle-bearing `media_jobs`, and `media_job_events`;
- any custom claim lease, reservation lease, schedule cursor, or recovery authority.

**Verify in code:** inspect `marquee/models/job.py`, `marquee/models/media_job.py`,
`marquee/models/__init__.py`, `alembic/env.py`, and every existing migration before deciding
which current non-job table belongs in the clean baseline.

Obsolete ORM/source may remain temporarily only when later migration chunks still reference
it. If retained, mark it through one explicit deployment-exclusion mechanism used by
Alembic and schema equivalence. Production startup may never call `Base.metadata.create_all`
or create excluded objects. Test-only creation must be clearly isolated and must not become a
runtime fallback. Chunk 5 removes the remaining source.

### 3.2 Forward-compatible JMC1 job core

JMC1 creates only the canonical fields needed to prove transport. Do not pull the full Chunk
2 presenter/progress/domain registry into this plan.

`jobs` must support:

- canonical string ID and type;
- versioned request payload;
- phase (`planned`, `queued`, `running`, `stopping`, `terminal`);
- nullable outcome (`succeeded`, `failed`, `cancelled`, `dead_letter`);
- desired state (`run`, `pause`, `cancel`);
- priority, eligible time, idempotency key;
- nullable current `pgq_job_id` and monotonically increasing `dispatch_generation`;
- result/error documents and lifecycle timestamps.

Transitional old columns may remain only when required to keep unmigrated source importable;
they have no claim/retry/schedule authority and are removed or reshaped by Chunk 2. Document
each such column in the timeline rather than silently treating it as target architecture.

`job_dispatches` must contain:

- canonical job ID;
- generation, unique with job ID;
- unique PgQueuer ticket ID;
- entrypoint, dedupe key, priority, and eligible time;
- created/end timestamps and bounded disposition.

`job_events` remains semantic history. JMC1 may add the minimal execution audit required to
prove no-op delivery, but must not recreate a heartbeat or claim lease.

### 3.3 Runtime schema contract

PgQueuer does not provide a database schema-version row. Add a small Marquee-owned contract
marker recording:

- component (`marquee` or `pgqueuer`);
- expected package/Alembic version;
- durable mode;
- deterministic catalog fingerprint;
- verification timestamp and verifier build.

The fingerprint is computed from catalog facts—required objects, columns, enum values,
indexes, trigger/function, and table persistence—not from copied DDL. Migration writes the
marker only after every verification succeeds. Startup and readiness compare installed
package version, marker, and live catalog.

## 4. Phase 0 — Contract verification and baseline

### 4.1 First actions

1. Create/read the JMC1 timeline per the header protocol.
2. Record `git status`, current branch, configured Git author, and recent commits. Do not
   change the configured author.
3. Read the plan and architecture package completely.
4. Use Serena to map current job creation, worker/scheduler startup, health, model, migration,
   and test references. Use ByteRover to query prior decisions; code and this plan win over
   stale context.
5. Inspect the installed 1.1.1 package source for `Queries`, `AsyncpgDriver`,
   `QueueManager`, `SchedulerManager`, retry/cancellation, CLI verification, durability, and
   metrics.

### 4.2 Baseline evidence

Run through RTK and record exact results:

- complete pytest suite against its isolated PostgreSQL schema;
- `ruff check marquee tests`;
- current Alembic heads and offline upgrade SQL;
- current frontend `npm run check` only as a non-blocking reference—JMC1 changes no frontend;
- current OpenAPI health paths and process startup commands.

Do not normalize the historical failure count from design docs; record what this checkout
actually produces.

### 4.3 Package contract tests

Before building the gateway, add PostgreSQL integration coverage proving that:

- public `Queries.from_asyncpg_connection()` exists in exactly 1.1.1;
- a scalar enqueue returns one numeric ID;
- enqueue is visible inside the caller transaction and invisible to another connection until
  commit;
- caller rollback removes both queue and queue-log writes;
- the PgQueuer adapter neither commits nor closes the supplied connection;
- the same SQLAlchemy connection remains usable after the public call.

Use a temporary schema/disposable database and let PgQueuer install its objects itself. Do
not point contract tests at the operator's ordinary schema.

### 4.4 Documentation correction commit

Confirm the direct-adoption and migration documents describe the public `Queries` bridge,
not `fn_pgqueuer_enqueue`. Commit Phase 0 separately after focused tests, full baseline
comparison, and Ruff.

## 5. Phase 1 — Clean baseline, migration service, and reset CLI

### 5.1 Clean baseline

Replace all unreleased revisions with one root revision. Generate/review it against the
deployment metadata rather than hand-copying the old chain. Its downgrade may drop the new
Marquee schema objects because there is no production data promise, but it must never drop
PgQueuer objects—those have a separate owner.

Required gates:

- one Alembic head and `down_revision=None`;
- deterministic offline SQL;
- empty database creates the same Marquee catalog twice;
- filtered deployment metadata equals Alembic head;
- excluded legacy tables are absent;
- no PgQueuer table/function/type appears in Alembic.

### 5.2 Migration service

Replace the migration container's raw `alembic upgrade head` command with one Marquee
migration entrypoint. **Verify in code:** inspect `docker/docker-compose.yml`, Dockerfile,
configuration URL conversion, and Alembic environment before choosing module names.

The entrypoint:

1. opens a migration/admin connection with a role-specific `application_name`;
2. acquires a fixed, documented PostgreSQL advisory lock;
3. runs Alembic to head;
4. detects whether PgQueuer objects are wholly absent or present;
5. runs `pgq install --durability durable` only when absent, otherwise
   `pgq upgrade --durability durable`;
6. runs `pgq durability durable`, `pgq autovac`, and `pgq verify --expect present`;
7. rejects partial installs rather than guessing install versus upgrade;
8. independently verifies live catalog shape/persistence and installed package 1.1.1;
9. writes both schema-contract markers in one final Marquee transaction;
10. releases the advisory lock and exits zero only after all checks pass.

Subprocess invocations must not expose database passwords in process listings. Prefer libpq
environment variables or a confined credential mechanism over a DSN argument.

### 5.3 Guarded reset CLI

Add a standard-library or existing-dependency CLI with a contract equivalent to:

```text
python -m <chosen-module> \
  --allow-data-loss \
  --confirm-database <exact parsed database name>
```

It must refuse unless:

- `MARQUEE_ENVIRONMENT=development` is explicit;
- the confirmation exactly matches the database parsed from configuration;
- the data-loss flag is present;
- the target is PostgreSQL;
- no other connection with a Marquee role application name is active;
- it can acquire the migration advisory lock without waiting indefinitely.

The command resets only the target database/owned schemas, reapplies the JMC1 baseline,
installs/verifies PgQueuer, initializes required reference rows, and executes the canonical
no-op smoke once Phase 3 exists. It never deletes `DATA_DIR`, media, backups, model files, or
artifacts. It never calls or modifies `POST /api/system/reset-db`.

Tests use an explicitly owned disposable database or unique schema. They must prove every
guard, repeated deterministic reset, partial failure behavior, and refusal when another
Marquee role connection exists.

### 5.4 Phase gate

- focused baseline/migration/reset tests pass;
- offline Alembic SQL inspected;
- full retained suite has no failure absent from the Phase 0 baseline;
- Ruff passes;
- timeline updated and one phase commit created.

## 6. Phase 2 — Canonical transactional enqueue gateway

### 6.1 Public Marquee contract

Implement one narrow gateway; names may follow local conventions, but behavior is fixed:

```python
async def enqueue(
    session: AsyncSession,
    *,
    job_id: str,
    entrypoint: str,
    payload_version: int,
    dispatch_generation: int,
    priority: int,
    execute_after: timedelta | None,
    dedupe_key: str,
) -> int: ...
```

Preconditions:

- PostgreSQL/asyncpg only;
- caller owns an active SQLAlchemy transaction;
- canonical job, dispatch audit, and initial event have been added;
- `entrypoint == "control"` in JMC1;
- `dispatch_generation >= 1` and matches the canonical row;
- priority and delay are validated/bounded;
- dedupe key equals `marquee:{job_id}:{dispatch_generation}`.

Algorithm:

1. Flush Marquee rows without committing.
2. Get the session's `AsyncConnection`.
3. Get its documented raw pooled connection and underlying driver connection.
4. Verify the driver is an open asyncpg connection already participating in the active
   SQLAlchemy transaction.
5. Serialize the exact payload with deterministic UTF-8 JSON.
6. Call `Queries.from_asyncpg_connection(driver_connection).enqueue(...)` once.
7. Require exactly one returned ID.
8. Set `jobs.pgq_job_id` and the dispatch ticket ID; flush again.
9. Return the numeric ID without commit, rollback, close, or notification work outside
   PgQueuer.

Prevent concurrent use of the same raw connection by gateway operations. Do not retain the
raw connection or `Queries` beyond the call. Do not access PgQueuer from routes, presenters,
or unrelated services.

### 6.2 Companion operations

The same gateway boundary owns:

- official queued cancellation through public PgQueuer queries;
- bounded status lookup for known internal ticket IDs;
- queue-size/statistics retrieval;
- schema/catalog health and package-version inspection;
- limited consistency checks between current canonical dispatches and transport rows.

Raw lookup methods require canonical IDs/known ticket IDs and fixed result limits. No method
returns arbitrary table rows to a public response.

### 6.3 Atomic command service

Add the smallest canonical service needed to create `system_noop`:

1. validate the no-op payload and canonical idempotency key;
2. return an existing matching canonical job on repetition;
3. create job, dispatch generation 1, and initial event;
4. invoke the gateway;
5. commit once at the outer service boundary.

Concurrent canonical idempotency conflict reloads the winning job in a fresh transaction;
it does not dispatch a second ticket. A PgQueuer dedupe conflict is treated as an invariant
diagnostic, not silently converted into a new ticket.

### 6.4 Required tests

- commit makes canonical job, dispatch, event, queue row, and queue-log row visible together;
- rollback at each pre/post-enqueue failure point leaves none visible;
- exact payload keys/value types and no secrets/paths;
- one numeric ticket linked uniquely in both Marquee rows;
- canonical idempotency race and transport dedupe;
- priority ordering and deferred eligibility;
- driver mismatch, closed connection, absent transaction, multiple returned IDs, and nested
  concurrent call fail clearly;
- caller connection remains usable and solely owns commit/rollback.

Finish with the standard phase gate and commit.

## 7. Phase 3 — No-op worker and scheduler roles

### 7.1 Registration and payload

Register exactly one PgQueuer entrypoint, `control`, with `on_failure="hold"` and the
configured global concurrency cap. Its strict transport payload:

```json
{
  "dispatch_generation": 1,
  "job_id": "<canonical string>",
  "payload_version": 1
}
```

Reject missing, extra, duplicate, malformed, non-UTF-8, or wrong-version data before domain
execution. Diagnostic retry/failure/blocking handlers exist only in tests and are never
registered in the shipped worker.

### 7.2 Delivery wrapper

For a valid delivery:

1. load the canonical job and current dispatch in a fresh SQLAlchemy transaction;
2. compare ticket ID, generation, phase, desired state, and definition;
3. return successfully without executing when terminal, cancelled, stale, duplicate, or
   superseded;
4. fail/hold unknown or unmigrated definitions without calling legacy code;
5. atomically transition the canonical no-op to running and append a semantic event;
6. execute `system_noop` outside the admission transaction;
7. commit result, terminal phase, succeeded outcome, dispatch disposition, and event before
   returning to PgQueuer;
8. on failure, persist bounded error/failed outcome first, then re-raise so PgQueuer holds
   the ticket;
9. on redelivery after canonical terminal commit, no-op without overwriting history.

JMC1 does not implement process groups, fences, logs, artifacts, or physical cancellation;
those belong to Chunk 3. The no-op must nevertheless prove terminal-before-ack and stale
dispatch rejection.

### 7.3 Separate runtime processes

Create separate entry modules:

- worker constructs PgQueuer, registers `control`, and runs `QueueManager` only;
- scheduler constructs PgQueuer, registers only JMC1 schedules, and runs
  `SchedulerManager` only.

Each owns a dedicated direct asyncpg manager/listener connection. Domain work uses the
role-budgeted SQLAlchemy pool. Both run startup schema verification before registering work,
set clear `application_name` values, respond to SIGTERM, close owned connections, and exit
nonzero on manager/listener failure.

**Verify in code:** replace current custom worker/scheduler commands and embedded supervisor
targets without letting Uvicorn call a handler. Old source may remain unreachable but no old
process or bootstrap hook starts.

### 7.4 Scheduling

PgQueuer schedule callbacks enqueue ordinary canonical jobs through the same command
service/gateway. Ship no product schedule in JMC1. Test-only schedules prove coordination;
do not seed legacy maintenance schedules or execute a callback inline.

### 7.5 Tests

- successful canonical no-op and result echo;
- terminal commit precedes transport acknowledgment;
- simultaneous and sequential duplicate delivery execute one effect;
- stale/superseded/terminal/cancelled delivery no-ops;
- malformed payload and unknown job type hold safely;
- queued/picked cancellation and cooperative cancellation of a test blocker;
- retry delay/attempt persistence through test-only `RetryRequested`;
- held final failure retains canonical error and transport evidence;
- worker SIGTERM/SIGKILL, heartbeat timeout, and stale redelivery;
- two scheduler managers produce one scheduled callback effect;
- notification loss still delivers after polling fallback;
- worker process contains no scheduler manager and scheduler contains no queue manager.

Finish with the standard phase gate and commit.

## 8. Phase 4 — Readiness, diagnostics, and connection budgets

### 8.1 Role budgets

Add explicit, documented pool settings per process role rather than multiplying the current
API defaults across every service. Count:

- API SQLAlchemy base/overflow;
- worker SQLAlchemy base/overflow plus one direct PgQueuer connection per process;
- scheduler SQLAlchemy base/overflow plus one direct PgQueuer connection;
- migration/admin connections;
- test-only or metrics connections.

Tag all with role-specific PostgreSQL `application_name`. Enforce a deployment-wide maximum
in configuration validation and document the initial arithmetic. Do not introduce PgBouncer
or Docker hardening.

### 8.2 Health API

| Route | Contract |
|---|---|
| `GET /health/live` | Always bounded and database-free. Returns 200 when the API event loop can serve. |
| `GET /health/ready` | Returns 200 only when every mandatory dependency is compatible; otherwise 503 with sanitized component results. |
| `GET /health` | Temporary alias of readiness for current health-check consumers. |

Readiness checks:

- PostgreSQL reachability within a short timeout;
- migration advisory lock not held;
- exact Alembic head/Marquee marker;
- installed PgQueuer package 1.1.1;
- PgQueuer marker and live catalog fingerprint;
- all PgQueuer tables logged/durable;
- required notification/event infrastructure;
- mandatory JMC1 configuration and connection budget.

Do not include exception strings, credentials, DSNs, SQL, payloads, or raw rows. A database
outage after successful startup must not make liveness depend on the failed pool. Startup
schema incompatibility is fatal; transient outage handling follows bounded startup retry and
supervisor restart.

### 8.3 Bounded diagnostics

Provide internal services/endpoints consistent with the existing authenticated system API
for:

- queue counts by entrypoint/status/priority;
- oldest eligible job age;
- picked and held-failed counts;
- listener health and last observed event time;
- package/schema fingerprint/durability;
- role connection counts against budget.

Never expose payload bytes or arbitrary PgQueuer rows. These are future Operations inputs,
not Projection Room product summaries.

### 8.4 Tests

- live endpoint performs no database access;
- ready status for healthy, unreachable, empty, partial, wrong-head, wrong-version,
  non-durable, missing-index/function/trigger, lock-held, and invalid-config states;
- readiness recovers after dependency repair without a false healthy interval;
- startup fails closed for incompatible schema in API/worker/scheduler;
- API remains live and returns ready=503 after a later database outage;
- sanitized response contains no secrets or internal rows;
- role connection counts remain within documented budget during worker restarts and no-op
  saturation.

Finish with the standard phase gate and commit.

## 9. Phase 5 — Integration and failure certification

Build a repeatable JMC1 certification harness using temporary schemas or a demonstrably
owned disposable database. It must never reset the operator's normal database.

### 9.1 Automated matrix

Certify:

- clean creation, repeated reset, guard refusals, and deterministic catalog;
- Alembic/deployment metadata equivalence;
- PgQueuer fresh install, repeated upgrade, durable persistence, autovacuum, and fingerprint;
- commit/rollback at every enqueue boundary;
- canonical/transport dedupe, priority, and deferral;
- payload secrecy and exact schema;
- queued/picked cancellation;
- successful completion and terminal-before-ack;
- held failure and persisted retry delay/attempt;
- duplicate/stale/terminal redelivery;
- worker kill and stale pickup;
- two-scheduler uniqueness;
- listener failure shutdown and polling fallback;
- API responsiveness while workers restart;
- readiness/liveness loss and recovery;
- schema mismatch/migration lock;
- bounded connections and diagnostics;
- static/runtime absence of legacy process startup, legacy claim/recovery calls, inline
  handlers, and non-noop registrations.

### 9.2 Operator smoke

Where the harness cannot prove ownership of PostgreSQL, leave these explicit and pending:

- real PostgreSQL restart before and after pickup;
- OS-level worker SIGKILL while picked;
- notification-channel disruption;
- migration service rerun against held/queued/deferred tickets.

Never restart or reset an operator database merely to complete a plan checkbox. Record the
exact commands and results when the operator performs them.

### 9.3 Final gate

- all focused JMC1 tests green;
- full retained pytest result compared test-for-test with Phase 0;
- no failure absent from the Phase 0 baseline;
- `ruff check marquee tests` green;
- clean offline Alembic SQL and schema comparison;
- static source checks green;
- timeline complete with hashes, deviations, and operator work;
- ByteRover curated with the implemented contract;
- honest statement of every manual smoke performed or still pending.

GitHub Actions remains untouched. Its rewrite occurs only after the full six-chunk local
baseline is green.

## 10. Public and internal contract summary

| Surface | JMC1 result |
|---|---|
| Canonical product ID | Existing Marquee string `job_id`; PgQueuer numeric ID remains internal |
| PgQueuer entrypoint | `control` only |
| Product definition | `system_noop` only |
| Queue payload | `{job_id, payload_version, dispatch_generation}` only |
| Enqueue integration | Public `Queries.enqueue()` on the active SQLAlchemy transaction's raw asyncpg connection |
| Cancellation | Gateway wraps official PgQueuer queued cancellation plus canonical desired state |
| Worker command | New PgQueuer queue-manager-only entry module |
| Scheduler command | New PgQueuer scheduler-manager-only entry module |
| Migration command | Marquee wrapper: Alembic then PgQueuer lifecycle/verification |
| Reset command | Guarded development database-only CLI |
| Health | `/health/live`, `/health/ready`, `/health` readiness alias |
| Diagnostics | Bounded schema/queue/listener/connection summaries; no raw rows |

## 11. Regression and stop rules

Stop and report rather than guessing when:

- installed PgQueuer is not exactly 1.1.1 or its public contract differs;
- SQLAlchemy/raw asyncpg transaction ownership cannot be proven;
- PgQueuer commits or closes the caller connection;
- clean baseline/reset would touch an unapproved database or `DATA_DIR`;
- an excluded legacy table is required by JMC1 runtime rather than only unmigrated source;
- a phase requires auth redesign, public reset work, Docker hardening, frontend work,
  non-noop migration, or CI modernization;
- a new full-suite failure cannot be classified and corrected inside the phase.

Do not respond to a stop condition with a compatibility layer, alternate backend, dual
execution, test skip, or undocumented deviation.

## 12. Out of scope

- all media, poster, HDR, audio/subtitle, letterbox, sync, ML, maintenance, and batch jobs;
- typed definition/presenter/progress registry beyond minimal `system_noop`;
- fences, process groups/cgroups, advisory media locks, staging, logs, artifacts, and SSE;
- Projection Room or feature-page frontend changes;
- browser auth/session/CSRF work;
- replacement/removal of the public reset endpoint;
- deletion of `DATA_DIR` during development reset;
- Docker least privilege, read-only roots, and capability hardening;
- webhooks;
- GitHub Actions modernization;
- production data migration or old-history retention.

## 13. Operator notes

- JMC1 deliberately makes the intermediate deployment useful only for `system_noop`.
  Unmigrated product command endpoints must fail closed; this is not a mixed-runtime release.
- Reset is authorized only because Marquee is unreleased. Before first release, freeze the
  final baseline and return to data-preserving migrations.
- PgQueuer `verify` checks object presence, not the package version by itself. The Marquee
  schema-contract marker plus live catalog fingerprint closes that gap.
- The public `Queries` bridge is intentionally narrow. A future PgQueuer upgrade must repeat
  the package-source inspection and the commit/rollback contract suite before changing the
  pin.
- Docker command changes needed to run migration/worker/scheduler roles are functional JMC1
  work, not authorization for the deferred Docker security phase.
