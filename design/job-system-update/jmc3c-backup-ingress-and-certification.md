# JMC3C — Backup, Request Ingress, and Certification

**Previous plan:** [JMC3B progress, logs, artifacts, and events](jmc3b-progress-logs-artifacts-and-events.md)  
**Filesystem foundation:** [JMC3A execution kernel and filesystem safety](jmc3a-execution-kernel-and-filesystem-safety.md)  
**Companion findings:** [reset-window miscellaneous fixes](job-system-miscellaneous-reset-window-fixes.md)  
**Program:** [clean-slate PgQueuer migration](job-system-pgqueuer-migration.md)
**Next plan:** [JMC4A producers, batches, and schedules](jmc4a-producers-batches-and-schedules.md)

> **For the implementing agent:** Read `AGENTS.md`, `CLAUDE.md` when present,
> `design/plans/README.md`, `design/plans/04-television-backend.md` §0, the complete JMC1,
> JMC2, JMC3A, and JMC3B plans/timelines, and this document **in full** before changing code.
> Decisions below are final. **Verify in code** means inspect current symbols and callers
> before editing; completed JMC2/JMC3 source and the shared timeline outrank stale anchors.
>
> **Shared JMC3 timeline:**
> `design/job-system-update/jmc3-safety-and-evidence-timeline.md`. Read it, verify every
> completed hash and gate against Git and the working tree, and append after every phase
> commit. Never create a JMC3C-specific timeline.
>
> **Git authorship:** use only the repository's configured Git user. Never add yourself, a
> model, or an assistant as author, co-author, contributor, or generator. No
> `Co-Authored-By`, “Generated with,” model-name, or assistant-name attribution is allowed.

**Goal:** Complete Chunk 3 by producing a consistent, verifiable PostgreSQL-plus-`DATA_DIR`
backup/restore contract, enforcing streaming request-body limits at both public ingress
layers, and certifying the complete JMC3 safety/evidence foundation under failure.

**Ordering:** final JMC3 plan. JMC3A/B must be complete and verified. This plan does not
enable `backup_create` or another production feature definition; Chunk 5 migrates the
canonical backup/destructive-maintenance jobs.

## 1. Preconditions and stop gates

Before implementation:

1. Verify every JMC3A/B phase hash and gate: fence/CAS behavior, safety locks, maintenance
   barrier, containment, cancellation, filesystem roots, staging, events, progress, logs,
   artifacts, retention, generated contracts, exact retained failures, and operator smokes.
2. Re-run representative JMC3A/B process-death, path, stale-fence, event-replay, log-cap,
   artifact-download, progress-coalescing, and evidence-recovery tests.
3. Inventory the current backup service, API routes, scheduler/definition entries, restore
   placeholders, pg_dump command construction, DATA_DIR inclusion rules, frontend callers,
   and tests. Prove no current route can still execute a backup inline in Uvicorn.
4. Inventory request-body handling in the ASGI app, endpoint dependencies, SvelteKit proxy,
   reverse-proxy/deployment files, tests, OpenAPI error models, streaming requests, SSE, and
   downloads. Record which routes legitimately accept bodies and their current maximums.
5. Verify installed PostgreSQL client/server compatibility and the exact `pg_dump`/
   `pg_restore` flags available. Verify the current SvelteKit/Node fetch streaming contract
   from installed types/runtime rather than assuming browser-only behavior.
6. Record branch/HEAD, working-tree ownership, Git author, complete backend/frontend
   baselines, schema/reset fingerprints, PgQueuer fingerprint, generated-contract checks,
   disposable database/server ownership, and disposable backup/data roots.

Stop if JMC3B has unsealed or unconfined writers, the exclusive maintenance barrier cannot
block new attempt admission, tests cannot prove ownership of the disposable database/server,
or another agent owns overlapping files. Never run restore/reset against the operator's
ordinary database or `DATA_DIR`.

## 2. Locked decisions

| ID | Decision |
|---|---|
| C1 | A backup is one logical pair: a PostgreSQL custom-format archive, a confined `DATA_DIR` archive/file set, and a checksummed versioned manifest. No component is independently advertised as a valid complete backup. |
| C2 | Online backup acquires JMC3A's exclusive maintenance barrier before the consistency point and holds it through database dump, DATA_DIR capture, manifest fsync, and atomic publication. |
| C3 | Every ordinary attempt/evidence writer holds the shared maintenance barrier until processes are dead and logs/artifacts are sealed. Exclusive acquisition therefore proves no admitted file writer is active. |
| C4 | PgQueuer may continue transport housekeeping, but no Marquee attempt, schedule callback with product effects, evidence retention writer, or filesystem mutation may cross the exclusive barrier. |
| C5 | Database credentials never appear in argv, logs, manifests, artifacts, or exceptions. Use a confined mode-0600 temporary pgpass file or an equally supported descriptor mechanism and delete it through the filesystem service. |
| C6 | The manifest includes exact Marquee/PgQueuer/configuration/format identity and a checksum/size for every backup component. Unknown format/schema versions fail verification. |
| C7 | Backup publication is temp-then-fsync-then-atomic-rename inside the backup root. An interrupted backup remains explicitly incomplete and is never listed as restorable. |
| C8 | Restore is an offline operator CLI that materializes into a fresh database and fresh empty target data directory. It never overwrites the running deployment or implements the deferred public reset endpoint. |
| C9 | Restore verifies the complete pair and safe archive member set before creating/mutating targets. The target database and data directory must be explicitly named, absent/empty, and development/test/operator-confirmed. |
| C10 | `pg_restore` uses fail-fast behavior and a transaction where supported by the selected mode. After restore, Marquee schema, PgQueuer durable schema/version, configuration, evidence files, and database/file linkage are verified before success. |
| C11 | No production `backup_create` definition is enabled. Remaining inline create/restore/delete routes are removed or fail closed; read-only listing/verification may remain only through confined bounded contracts. |
| C12 | Backend request limits count received bytes in a pure ASGI receive wrapper. `Content-Length` is an early validation hint, never the byte-count authority. |
| C13 | The first public SvelteKit ingress also streams and counts request bytes. It must not call `arrayBuffer()`, clone/buffer the full body, or rely on the backend to protect frontend memory. |
| C14 | Malformed/negative/conflicting declared lengths fail deterministically; over-limit bodies return the canonical 413 response. Omitted/chunked/slow bodies are subject to the same actual-byte limit. |
| C15 | Limits are method/path allowlisted from a code-owned catalog with one global default and smaller endpoint-specific values. Clients cannot request a larger cap. |
| C16 | Request-body enforcement does not modify response streaming. SSE and bounded artifact/log downloads are exempt only because they have no request body, not through a generic path bypass. |
| C17 | JMC3 certification uses disposable PostgreSQL/data roots and fixed canaries. A real PostgreSQL restart, real deployment cgroup, and operator filesystem smoke are recorded honestly when not automated. |
| C18 | Existing failures may shrink but the retained set may not grow. GitHub Actions and the eventual zero-failure stabilization remain after Chunk 6. Never run `ruff format`. |

The database tools follow PostgreSQL's supported archive contracts: [pg_dump](https://www.postgresql.org/docs/18/app-pgdump.html)
creates a transactionally consistent database export, and
[pg_restore](https://www.postgresql.org/docs/18/app-pgrestore.html) restores custom-format
archives. `pg_dump` consistency does not by itself synchronize external files, which is why
the JMC3 maintenance barrier remains mandatory.

## 3. Maintenance barrier and writer sealing

### 3.1 Participants

The JMC3A advisory service owns the barrier. These actors acquire shared maintenance before
mutating state/files and retain it until their durable/file work is sealed:

- admitted execution attempts;
- JMC3B log/artifact writers and retention/reconciliation;
- model/profile/artifact publication helpers migrated to the filesystem service;
- any remaining non-job maintenance writer that cannot yet be removed.

Backup acquires the exclusive form on its dedicated connection. New shared acquisitions wait
behind it and expose a friendly maintenance wait state without creating an attempt. Exclusive
acquisition has a bounded cancellation/timeout policy and records blocker diagnostics without
raw advisory keys or other jobs' private payloads.

### 3.2 Consistency point

After exclusive acquisition:

1. verify no current attempt/process/evidence writer contradicts the barrier;
2. flush/seal any allowed checkpointable service state;
3. capture the current configuration version, Alembic head, schema fingerprints, PgQueuer
   package/mode/fingerprint, and database identity;
4. run `pg_dump` while no Marquee file writer can change database/file linkage;
5. capture the allowlisted DATA_DIR file set;
6. compute component and member checksums/sizes;
7. write/fsync the manifest and backup directory;
8. atomically publish the complete backup;
9. release the exclusive barrier.

An error before publication deletes or marks only the positively identified incomplete
workspace. It never rotates a prior valid backup. Retention/rotation runs as a separate
confined operation after success and may not delete the newly created or last-known-good
backup accidentally.

## 4. Backup format and operator CLI

### 4.1 CLI surface

Provide one operator entrypoint such as `python -m marquee.maintenance` with fixed
subcommands:

```text
backup
  --output-root <configured/confined root override>

verify-backup
  --backup-id <safe id>

restore-backup
  --backup-id <safe id>
  --target-database <exact new database name>
  --confirm-database-name <same exact name>
  --target-data-dir <new empty path under approved operator root>
  --allow-data-loss-or-create-target
```

Use the repository's established CLI conventions, but keep equivalent explicit guards. The
restore flag name may be normalized to current CLI style during implementation; its safety
semantics may not be weakened.

`backup` may operate against the running database because it uses the exclusive barrier.
`verify-backup` is read-only. `restore-backup` requires an explicit maintenance/offline
environment, exact database-name confirmation, an explicit acknowledgement flag, no active
Marquee service connections to the target, and a new/empty target data directory.

The product API does not run restore. If it retains a validation endpoint, it returns
`restorable`, reasons, and `offline_restore_required=true`; it never returns or implies
`restored=true`.

### 4.2 Versioned manifest

Use a deterministic JSON manifest with at least:

```text
backup_format_version
backup_id / created_at / completed_at
application_build
database
  server/client major versions
  source identity
  archive name/format/size/checksum
marquee
  alembic head
  schema fingerprint
pgqueuer
  package version
  durable mode
  schema fingerprint
configuration_version
data
  archive/file-set format
  included root classes
  explicit exclusions
  member key, type, size, checksum
totals
```

Do not store physical source paths, credentials, environment dumps, arbitrary symlink
targets, queue payloads, or secrets. Member keys are confined POSIX keys. Sort members and
JSON keys deterministically for checksum/reproducibility.

### 4.3 DATA_DIR capture

- Derive inclusion from the completed JMC3 filesystem root/catalog, not scattered glob lists.
- Include canonical logs, artifacts, model/profile state, and other declared durable data.
- Exclude caches, temporary work, staging, incomplete backups, credential files, sockets,
  and rebuildable ephemeral data explicitly in the manifest.
- Refuse unsafe symlinks, special devices, FIFOs, sockets, traversal, hardlink surprises, or
  files that change identity/size while read despite the barrier.
- Stream/archive without loading a complete file into memory.

### 4.4 PostgreSQL command safety

Construct argv as discrete fields with host, port, user, and database options; never embed a
password-bearing URL. Create a confined `PGPASSFILE` with owner-only permissions, pass only
its path in the child environment, redact it from logs, and delete it in a `finally` path.
Use JMC3A's tracked launcher where compatible without making backup success depend on
attempt-only state.

Capture and sanitize bounded stderr. Verify client/server major compatibility before dump and
restore. Do not use `--no-sync` for an advertised backup.

## 5. Offline fresh-target restore

The restore sequence is fixed:

1. confine/verify the backup ID and complete manifest;
2. verify every component checksum and safe member before creating targets;
3. reject unknown/newer format, incompatible PostgreSQL/PgQueuer/Marquee schema, unsafe
   archive entries, incomplete publication, or extra unmanifested durable members;
4. prove the target database name confirmation and absence of active Marquee connections;
5. require the target database not to exist, or create an explicitly confirmed new database
   from `template0`; never overwrite the source/running database;
6. require the target data directory to be absent or empty and stage extraction in its
   parent filesystem;
7. restore PostgreSQL with fail-fast semantics and a single transaction where compatible;
8. extract DATA_DIR through JMC3A's archive confinement into a staged fresh directory;
9. verify all restored file sizes/checksums;
10. run Marquee Alembic/schema-contract, PgQueuer durable/version/fingerprint,
    configuration, job/evidence storage-key, log/artifact checksum, and model/profile linkage
    checks against the restored targets;
11. publish/rename the fresh data directory only after verification;
12. report success plus the operator's separate cutover/restart steps.

On failure, leave the original deployment untouched, clean only owned incomplete targets, and
return bounded diagnostics. A dump is treated as trusted only after its self-produced
manifest/checksums and safe member policies pass; PostgreSQL's warning that restores execute
archive-provided database code must be noted in operator output/documentation.

## 6. Product-route boundary

Inventory the completed JMC2 API rather than assuming current route names. Enforce:

- no API route or scheduler callback runs backup, restore, bulk delete, or archive extraction
  inline;
- `backup_create` remains defined but dispatch-disabled until Chunk 5;
- any create/restore/delete product mutation that still bypasses canonical jobs is removed
  from routing/OpenAPI and its frontend caller is changed to an unavailable/offline notice;
- read-only list/verify/download may remain only if bounded and served through confined keys;
- generated OpenAPI/TypeScript artifacts are updated in the same phase as route changes.

Do not create a temporary compatibility endpoint or misreport validation as restoration.

## 7. Streaming request-size enforcement

### 7.1 Limit catalog and errors

Create a code-owned catalog keyed by normalized HTTP method plus literal/route-template
identity. It supplies:

- a conservative global request-body default;
- smaller explicit limits for JSON commands/settings/plans where useful;
- a bounded larger limit only for an endpoint that intentionally accepts it;
- no client-selected override.

Methods without bodies use a zero/none policy as appropriate. Multipart form parsing retains
its own file/field/part bounds under the outer byte limit. Route matching errors do not permit
a larger fallback.

Define the canonical error envelope and behavior:

- malformed/negative/non-decimal/ambiguous `Content-Length`: 400;
- declared or actual body over the selected limit: 413;
- unsupported transfer framing handled by the server/proxy according to HTTP runtime policy;
- no handler invocation or partial command after rejection.

### 7.2 Backend pure-ASGI middleware

Replace the header-only FastAPI middleware with a pure ASGI wrapper around `receive`:

- validate declared length early when present;
- count actual `http.request` body bytes across chunks;
- stop forwarding once the limit would be exceeded;
- send one canonical 413 response without allowing the route to commit work;
- handle disconnect and duplicate/end messages correctly;
- avoid `BaseHTTPMiddleware`, `request.body()`, or a full-body buffer;
- preserve cancellation/backpressure and leave response streaming untouched.

The wrapper must not attempt to consume an unlimited remainder into memory. Verify the
Uvicorn/ASGI behavior for rejected unread body data and connection reuse in integration tests.
Starlette exposes request bodies as streaming chunks rather than requiring buffering; see
[Starlette requests](https://www.starlette.io/requests/).

### 7.3 SvelteKit public proxy

The same-origin proxy is the first public application ingress and must:

- validate the incoming declared length against the same or stricter catalog;
- pass `request.body` as a stream through a counting transform;
- abort upstream immediately at the cap or client disconnect;
- never call `arrayBuffer()`, `text()`, `json()`, `clone()`, or otherwise materialize the
  complete body;
- set the installed Node/SvelteKit fetch duplex option only when its actual runtime/types
  require it;
- remove/recompute hop-by-hop and length headers safely;
- return the same 400/413 envelope and avoid a second response after upstream start;
- retain existing bounded timeout behavior for non-SSE responses and unlimited-lifetime SSE
  response streaming.

Do not exempt a request merely because its path later returns a stream. SSE/log/artifact
downloads normally use GET without a request body and therefore need no special body bypass.

## 8. Implementation phases

Each phase ends with focused tests, complete retained pytest comparison,
`ruff check marquee tests`, applicable schema/reset/readiness and deterministic contract
checks, frontend format/check/lint/build where affected, `git diff --check`, one short
lowercase commit, and a shared-timeline update.

### Phase C0 — verify JMC3B and freeze backup/ingress contracts

- Perform all prerequisites and baseline.
- Freeze backup route/service/manifest and ingress/proxy behavior.
- Verify installed PostgreSQL and Node/SvelteKit streaming contracts.
- Allocate disposable server/database/data/backup roots and prove ownership.

### Phase C1 — consistency barrier, backup pair, and verification

- Integrate exclusive maintenance with all shared writers.
- Implement safe pg_dump credentials/launch, DATA_DIR capture, deterministic manifest,
  checksums, atomic publication, verification, and rotation safety.
- Remove/fail-close unsafe inline backup mutations without enabling `backup_create`.

### Phase C2 — guarded offline fresh-target restore

- Implement CLI guards, pre-verification, fresh database/data restoration, post-restore
  schema/PgQueuer/config/evidence/linkage checks, failure cleanup, and operator output.
- Certify a complete restore into targets owned by the test harness.

### Phase C3 — backend and frontend streaming request limits

- Implement the limit catalog and pure-ASGI receive wrapper.
- Stream/count/abort in the SvelteKit proxy and remove complete-body buffering.
- Update generated contracts/error types and run malformed/chunked/slow/mismatch/over-limit
  integration tests at both layers.

### Phase C4 — complete JMC3 certification

- Run the full combined fence/process/lock/path/staging/progress/log/artifact/event/backup/
  restore/ingress matrix under saturation and injected failure.
- Run complete backend/frontend/generated/schema gates and compare retained failures.
- Perform or clearly defer the operator-only cgroup, PostgreSQL restart, worker SIGKILL,
  proxy, and restore smokes.
- Record the exact Chunk 4 starting point without enabling a real handler.

## 9. Acceptance matrix

JMC3C is incomplete until automated evidence proves:

- exclusive maintenance prevents new admission and waits for every active shared writer;
- active logs/artifacts are sealed before the backup consistency point;
- interrupted dump/archive/manifest/fsync/publication never appears as a complete backup;
- credentials and physical source paths do not appear in argv, logs, events, artifacts,
  manifests, or exceptions;
- manifest/member order, checksums, versions, includes/excludes, and totals are deterministic;
- symlink/special-file/archive traversal/path-swap/unmanifested-member attacks fail closed;
- prior valid backups survive failed creation and rotation retains required recovery points;
- restore refuses active/existing/unconfirmed targets and verifies all inputs before mutation;
- fresh-target restore recreates exact Marquee/PgQueuer/configuration/job/event/log/artifact/
  model linkage and detects a corrupted database or any file/member checksum;
- the API never claims a restore occurred and no inline backup/restore/delete bypass remains;
- `backup_create` and all other non-noop production definitions remain disabled;
- declared-length early rejection and actual streamed-byte enforcement agree;
- omitted, chunked, mismatched, negative, malformed, slow, exactly-at-limit, one-byte-over,
  disconnect, and concurrent uploads remain bounded at backend and proxy;
- rejected bodies invoke no handler/transaction and do not exhaust memory or connection
  slots;
- the SvelteKit proxy contains no full-body buffering and preserves response SSE/download
  streaming;
- JMC1 readiness, PgQueuer delivery, JMC2 contracts, and all JMC3 evidence behavior remain
  correct under worker/API/listener/database restarts;
- connection/file-descriptor/event/log/storage/request rates remain in documented budgets;
- no new failure, skip, or `xfail` appears; Ruff, generated contracts, schema, and affected
  frontend gates pass.

## 10. Complete JMC3 fault and saturation matrix

Run fixed canaries while saturating advisory permits, event clients, live log tails, artifact
downloads, request uploads, and database pools. Inject failure at:

- delivery validation, lock wait, attempt admission, process identity persistence;
- child launch, high-output drain, progress flush, cooperative/TERM/KILL cancellation;
- stage creation, validation, fsync, atomic replace, terminal product commit, PgQueuer ack;
- event insert/notify/tail/replay, log write/truncate/seal/compress, artifact publish/download;
- backup barrier acquisition, pg_dump, file capture, checksum, manifest, rename, rotation;
- restore verification, database creation/restore, extraction, linkage verification;
- proxy read, upstream forwarding, backend receive, handler boundary, client disconnect.

Required proofs are no stale write/publish, no leaked owned process, no unrelated process
signal, no conflicting file effect, no unvalidated destination, no false cancellation or
restore, no missing terminal evidence linkage, no secret/path exposure, and bounded recovery
after service restart.

## 11. Out of scope

- production activation of backup, maintenance, or any other real job definition;
- restoring over the running/ordinary database or implementing `POST /api/system/reset-db`;
- browser authentication/authorization/CSRF redesign;
- Docker/cgroup delegation hardening or least-privilege deployment;
- webhooks;
- Projection Room/shared feature-page visual rebuild;
- migration of real read-only/destructive handlers (Chunks 4–5);
- legacy data/API compatibility;
- final zero-failure cleanup and GitHub Actions modernization (after Chunk 6 local green).

## 12. Operator handoff

The final shared timeline must identify all JMC3 hashes, exact retained failures, schema and
generated-contract versions, containment/lock/connection budgets, log/artifact/event/retention
settings, backup format and CLI commands, verified include/exclude roots, disposable restore
results, ingress limits, performed manual smokes, every pending operator action, and the exact
JMC4 starting condition that still has only `system_noop` production-enabled.
