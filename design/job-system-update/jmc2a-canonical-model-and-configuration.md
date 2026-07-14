# JMC2A — Canonical Model and Versioned Configuration

**Program:** [clean-slate PgQueuer migration](job-system-pgqueuer-migration.md)  
**Architecture:** [direct PgQueuer adoption](job-system-pgqueuer-direct-adoption.md)  
**Next plan:** [JMC2B definition registry and policies](jmc2b-definition-registry-and-policies.md)

> **For the implementing agent:** Read `AGENTS.md`, `CLAUDE.md` when present,
> `design/plans/README.md`, `design/plans/04-television-backend.md` §0, the complete JMC1
> plan and timeline, and this document **in full** before changing code. Decisions below are
> final. **Verify in code** means inspect the named source and relocate drift before editing.
>
> **Shared JMC2 timeline:**
> `design/job-system-update/jmc2-canonical-product-timeline.md`. JMC2A creates it before its
> first implementation commit if absent. If it exists, read it, verify every claim against
> `git log` and the working tree, and resume rather than repeating work. After every phase
> commit append the hash, verification, current work, exact next steps, deviations and why,
> and pending operator actions. JMC2B and JMC2C append to this same file.
>
> **Git authorship:** use only the repository's configured Git user. Never add yourself, a
> model, or an assistant as author, co-author, contributor, or generator. No
> `Co-Authored-By`, “Generated with,” model-name, or assistant-name attribution is allowed.

**Goal:** Replace JMC1's intentionally transitional product schema with the final clean-slate
canonical job/evidence model, make historical evidence independent of live library rows, and
replace process-local JSON setting overrides with one versioned cross-process configuration
authority.

**Ordering:** first of three JMC2 plans. JMC2B and JMC2C must not start until this plan is
complete and its claims are verified through the shared timeline.

**No compatibility migration:** the database remains disposable. Do not backfill old rows,
preserve old identifiers, or retain legacy job/media lifecycle columns or views.

## 1. Preconditions and stop gates

Before implementation:

1. Verify JMC1 Phase 5 is complete in
   `jmc1-pgqueuer-foundation-timeline.md`, including final commit hashes, retained test
   failure set, schema/reset checks, and performed-versus-pending operator smokes.
2. Verify JMC1 still exposes only `control/system_noop`, its transactional gateway tests pass,
   and the clean reset produces the recorded JMC1 catalog.
3. Record the JMC2A baseline in the shared timeline: branch/HEAD, clean/dirty paths,
   configured Git author, full pytest result and exact failures, Ruff, `alembic check`, offline
   SQL, reset/catalog fingerprint, and affected frontend check/lint/build results.
4. Preserve unrelated working-tree changes. Stop if JMC1 is incomplete, its final schema is
   drifting, or the current branch contains uncommitted implementation work owned by another
   agent.

Do not interpret a pending operator-only PostgreSQL restart as a schema blocker if JMC1 marked
it honestly and all automated JMC1 gates passed. Record it as inherited operator work.

## 2. Locked decisions

| ID | Decision |
|---|---|
| M1 | Build the final target schema directly. No backfill, compatibility view, legacy serializer, old-ID resolver, or dual lifecycle. |
| M2 | Add one forward Alembic revision after JMC1's root during implementation. Do not resquash JMC1 while other agents may depend on its recorded head; the pre-release program performs the final squash later. |
| M3 | Remove every transitional JMC1 `Job`/`JobAttempt` column and every deployment-excluded legacy job/media runtime model from target metadata. Unmigrated source may remain importable only if it no longer maps or writes a legacy table. |
| M4 | `JobAttempt` is evidence only. PgQueuer remains the sole claim, heartbeat, stale-redelivery, retry-timing, schedule, and concurrency authority. |
| M5 | Canonical history never has a required foreign key to a live Movie, Series, Season, Episode, or MediaFile projection. Immutable subject snapshots remain readable after retirement/deletion. |
| M6 | Live integration projections are retired first (`is_present=false`, `retired_at`) and are not physically deleted by ordinary synchronization. Hard purge is a later maintenance concern. |
| M7 | Use one strict 1:1 `MediaOperationDetail` keyed by canonical `job_id`; other families use registry-validated request/plan/result documents rather than sparse lifecycle tables. |
| M8 | Create log/artifact metadata now, but physical capture, streaming, compression, and cleanup remain Chunk 3. |
| M9 | All currently UI-writable non-secret settings move to immutable database revisions. Secrets, database/process settings, model paths, and restart-only machine configuration remain environment-owned. |
| M10 | Configuration updates use optimistic concurrency. No last-write-wins merge and no shared JSON override file. |
| M11 | PostgreSQL notification is an invalidation hint, not the source of truth. Every role periodically repairs against the current database version and retains its last valid revision on load/validation failure. |
| M12 | Each job stores the configuration version plus only the bounded execution-relevant values selected by its future `JobDefinition`; never snapshot secrets or the entire settings document. |
| M13 | Existing settings/config route paths may remain, but they must read/write the new service. Mutation requires `expected_version`; stale requests return 409 with current version metadata. |
| M14 | Remove UI mutation of secrets, including Subgen callback tokens. GET surfaces only configured/redacted booleans. |
| M15 | Only `system_noop` remains dispatch-enabled. Schema/config work must not enable another handler or restore legacy writers. |
| M16 | Existing failures may shrink but the retained set may not grow. Obsolete tests are replaced in the same phase, not skipped. |
| M17 | Backend phases run full pytest plus `ruff check marquee tests`; affected frontend phases run check, lint, and build. Never run `ruff format`. |

## 3. Final canonical schema

### 3.1 `jobs`

Create one canonical row with these semantic groups:

- **Identity/versioning:** string `id`, `type`, `payload_version`, `result_version`,
  `error_version`.
- **Documents:** validated `request`, optional `plan`, optional `result`, optional `error`.
- **Lifecycle:** `phase`, nullable `outcome`, `desired_state`, monotonic `fence_token`, nullable
  `current_attempt_id`, priority and eligibility.
- **Transport:** nullable current `pgq_job_id`, monotonic `dispatch_generation`; dispatch
  history remains in `job_dispatches`.
- **Policy snapshots:** retry policy, timeout/effect policy identifier, configuration version,
  bounded execution configuration snapshot.
- **Hierarchy:** nullable self-FK `parent_id`, stable string `root_id` and `correlation_id`,
  nullable self-FK `retry_of_job_id`.
- **Provenance:** trigger kind, bounded initiator document, feature area, presentation family.
- **Subject:** subject kind, stable display/reference ID, immutable versioned
  `subject_snapshot`.
- **Semantic state:** typed progress document, progress sequence/freshness, stable current
  stage/current-subject summary, attention document.
- **Time:** created, planned, queued, eligible, started, stopping, terminal, and updated.

Allowed values are final product vocabulary:

- phase: `planned`, `queued`, `running`, `stopping`, `terminal`;
- outcome: `succeeded`, `partially_succeeded`, `no_change`, `failed`, `cancelled`,
  `superseded`, `dead_letter`, or `unsafe`;
- desired state: `run`, `pause`, or `cancel`.

`phase=terminal` requires an outcome and terminal timestamp. Non-terminal rows have no
outcome. Queued work requires an active current dispatch/ticket; a planned unconfirmed job
does not. Enforce what is safely enforceable with database checks and cover cross-row rules in
the canonical service.

Delete JMC1 transitional authority: `status`, checkpoint, resource request, custom attempt
count/max attempts, cancel/pause booleans, custom scheduled/claimed timestamps, legacy
heartbeat/claim fields, and duplicated lifecycle timestamps. Update `system_noop` and JMC1
tests to use only final fields.

### 3.2 `job_dispatches`

Retain JMC1's `(job_id, generation)` audit and unique PgQueuer ticket, but finalize:

- disposition vocabulary includes active, succeeded, failed, cancelled, stale, superseded,
  and dead-lettered;
- store creation, eligibility, picked-observed, and end times only when Marquee legitimately
  observes them;
- never make it a claim/retry scheduler;
- deleting a canonical job intentionally cascades its dispatch audit, while deleting a live
  media subject cannot affect it.

### 3.3 `job_attempts`

Replace the transitional attempt model with immutable/admission evidence:

- canonical job, PgQueuer job ID and transport attempt number;
- Marquee attempt number and fence token, unique within the job;
- worker-node/build identity;
- attempt phase and semantic outcome/failure classification;
- admitted, started, stopping, and finished timestamps;
- process group/cgroup, host boot ID, PID and process-start identity placeholders;
- exit code/signal and bounded metrics;
- no heartbeat, lease expiry, reservation, claim status, or recovery authority.

JMC1 no-op delivery must create/update this audit without weakening terminal-before-ack or
duplicate-delivery behavior. Chunk 3 later fills process/log fields.

### 3.4 Semantic events, logs, artifacts, and nodes

- `job_events`: one append-only semantic stream with job, optional attempt, stable event key,
  state/stage, human-safe message, bounded detail, global durable cursor, and timestamp.
- `job_logs`: attempt-scoped segment metadata—confined storage key, format/encoding,
  compression, byte/line counts, redacted/truncated flags, open/closed times, retention and
  expiry. No arbitrary filesystem path.
- `job_artifacts`: job/optional-attempt, artifact kind/name/status, physical confined key or
  virtual-document source, content type, size/checksum, bounded metadata, retention/expiry.
- `worker_nodes`: node/build/boot identity, capabilities, readiness/drain state, last seen and
  bounded telemetry. It never claims jobs and has no lease authority.

Chunk 3 implements capture and lifecycle. JMC2A only supplies constraints, safe empty APIs for
later plans, and fixtures.

### 3.5 `media_operation_details`

One row per applicable canonical job:

- `job_id` primary key/FK;
- operation kind;
- nullable live `media_file_id` with `ON DELETE SET NULL`;
- immutable media/target snapshot and input signature;
- plan expiry/confirmation metadata where applicable;
- requested/expected/actual target outcome documents;
- validation, atomicity, backup and publish summaries.

Lifecycle, cancellation, attempts, progress, result/error, batch parentage, and events remain
canonical `Job` concerns. Do not restore `MediaJob`, `MediaBatch`, or `MediaJobEvent` under new
names.

### 3.6 Hierarchy and deletion behavior

- Parent and retry self-FKs use `SET NULL`; stable root/correlation/retry IDs remain in
  snapshots/documents for historical rendering.
- Job evidence cascades only when the canonical job itself is deliberately purged.
- `MediaOperationDetail.media_file_id` uses `SET NULL`.
- Job artifacts/logs never require live library FKs.
- Current projections such as subtitle inventory, current letterbox state, and current DoVi
  state may cascade with their owning live projection; their canonical job evidence does not.

**Verify in code:** audit `PipelineRun`, `ArtworkEvent`, `LetterboxEvent`, re-encode/backup
evidence and every live-library cascade. Historical/audit rows use nullable `SET NULL` plus
their own display snapshot. Current state may cascade only when loss cannot erase job history.

## 4. Library retirement

Add consistent projection-retirement fields to Movie, Series, Season, Episode, and MediaFile:

- `is_present` default true, indexed where active queries need it;
- nullable `retired_at`;
- `last_seen_at` or equivalent synchronization observation timestamp.

Update Radarr/Sonarr/media synchronization semantics:

1. mark all entities observed in the completed synchronization present;
2. retire previously present entities absent from a successfully completed authoritative
   response;
3. do not retire on partial/integration failure;
4. reactivate the same stable external identity if it returns;
5. exclude retired rows from ordinary active-library queries unless history explicitly asks
   for them.

Physical purge and retention are out of scope. Tests must remove/retire every subject type
and prove canonical job presentation inputs, retry lineage, attempts, events, logs and
artifacts remain readable from snapshots.

## 5. Versioned configuration model

### 5.1 Storage

Use two Marquee-owned structures:

- immutable `configuration_revisions`: monotonic version, canonical JSON values, checksum,
  schema version, actor/trigger, creation time;
- singleton `configuration_current`: current version FK and update time.

The current pointer changes in the same transaction that inserts a valid revision. Published
notification is transactional. Never update or delete a revision in place.

### 5.2 Key catalog and ownership

Create one code-defined catalog containing key, Pydantic validation owner, public metadata,
scope, sensitivity and apply mode.

Database-owned UI-mutable non-secrets include:

- hot pipeline scoring/gating/tuning values currently accepted by `/api/config/pipeline`;
- subtitle policy, preferred-language, scan/mutation/generation and deep-scan values currently
  accepted by `/api/settings`;
- non-secret provider URL/profile/mode/path-mapping labels that can safely take effect for the
  next job;
- poster naming/restore method and healing enable/interval values.

Environment/restart-owned values include:

- every secret/token/API key;
- database, auth, host/port, process-role and pool settings;
- model/artifact filesystem paths and execution-provider identity;
- embedded-service process/device/model/concurrency settings that bind at process startup;
- any setting whose current implementation cannot safely change without process recreation.

Some restart-owned values may be displayed as redacted/configured metadata, but API mutation
is rejected with its apply mode. Subgen callback token becomes environment-only immediately.

### 5.3 Effective configuration and precedence

Precedence is fixed:

1. code defaults;
2. environment/restart-owned values;
3. current database revision for catalogued database-owned keys.

Unknown database keys, invalid types, secret-like keys, or values that fail the owning
cross-field validators invalidate the revision. They do not partially apply.

Remove reads/writes of `pipeline_overrides.json`, `subtitle_overrides.json`, and app override
JSON for migrated keys. Do not import their data because the database is reset. Delete dead
helpers/tests once no caller remains.

### 5.4 Optimistic updates

Configuration mutation accepts an explicit `expected_version`:

- equal to current: validate merged effective configuration, insert one revision, move pointer,
  notify, return new version/ETag;
- stale: return HTTP 409 with current version/ETag and no values from another user's draft;
- invalid: return 422/400 using the established validation envelope and create no revision;
- empty/no-effective-change update: return current version without creating churn.

Concurrent updates never silently merge. UI reloads the current document and lets the user
reapply their change.

### 5.5 Cross-process cache

API, worker, and scheduler use the same provider:

- load/validate current revision at startup;
- listen on a fixed PostgreSQL channel whose payload is only the decimal version;
- invalidate and reload on a newer version;
- repair against `configuration_current` at most every 30 seconds while active;
- reject duplicate/older notifications;
- retain the last valid revision if database/revision validation fails;
- surface stale/invalid configuration in readiness/Operations without crashing an already
  running media operation.

Startup with no valid initial revision fails closed after migration/seed should have created
one. Cache code must not mutate imported Pydantic settings singletons.

### 5.6 Job snapshots

Expose `snapshot_for(keys)`/equivalent:

- accepts only catalogued non-secret execution keys;
- returns current version plus deterministic bounded JSON;
- rejects secret/restart/process keys;
- is called inside canonical enqueue transaction;
- stores values on `Job` before PgQueuer enqueue.

JMC2B definitions declare their required key sets. JMC2A supports `system_noop` with an empty
execution snapshot and tests synthetic key sets.

## 6. Settings APIs and narrow frontend continuity

Keep current route families to avoid an unrelated settings-page rewrite, but make their
contract version-aware:

| Route | JMC2A behavior |
|---|---|
| `GET /api/settings` | Existing redacted sections plus `configuration_version`, `etag`, value ownership/apply metadata and stale/health state |
| `PUT /api/settings` | Existing grouped changes plus required `expected_version`; database revision transaction; 409 on conflict |
| `GET /api/config/pipeline` | Current/default/meta data from effective versioned provider plus version/ETag; no JSON override file |
| `PUT /api/config/pipeline` | Required `expected_version` and validated values; restart/secret keys rejected |

Update only the settings/config frontend clients and forms necessary to send the version,
handle 409 by reloading with an explanatory message, and remove secret-edit affordances.
Do not start Projection Room, generated OpenAPI adoption, or general frontend state cleanup.

## 7. Implementation phases

### Phase A0 — verify JMC1 and freeze contracts

- perform the preconditions and baseline;
- snapshot current JMC1 job, dispatch, attempt, event, reset, readiness and no-op behavior;
- inventory deployment-excluded models and all JSON override readers/writers;
- commit tests/contract fixtures only when the phase gate is green.

### Phase A1 — canonical schema and migration

- implement final models/constraints and forward Alembic revision;
- remove transitional/deployment-excluded target metadata;
- update reset/schema fingerprints and JMC1 no-op/gateway to final columns;
- prove offline SQL, upgrade, reset, metadata equality, and no PgQueuer DDL ownership drift.

### Phase A2 — durable evidence and subject retirement

- add attempt/event/log/artifact/worker/media-detail structures;
- correct historical FK deletion behavior;
- implement retirement/reactivation semantics in synchronization;
- add deletion/retirement history regressions.

### Phase A3 — configuration storage and service

- implement catalog, immutable revisions/current pointer, validation and optimistic updates;
- seed revision 1 during migration/reset;
- remove JSON override persistence and singleton mutation;
- add concurrency/validation/secret tests.

### Phase A4 — cross-process cache and job snapshots

- add notification listener, 30-second repair and last-valid fallback;
- integrate API/worker/scheduler startup/readiness;
- snapshot system-noop configuration in its canonical transaction;
- test missed/late notifications, invalid revision, restart and outage behavior.

### Phase A5 — settings API/frontend continuity and certification

- switch existing settings/config routes and narrow frontend callers;
- remove secret editing;
- run complete schema/config/history/API/frontend matrix;
- produce final JMC2A timeline handoff for JMC2B.

Each phase ends with focused tests, full retained pytest comparison, Ruff, applicable
Alembic/reset checks, applicable frontend check/lint/build, one lowercase commit, and a
timeline update.

## 8. Acceptance matrix

- fresh reset and forward upgrade produce exact final target metadata;
- no transitional job column, excluded legacy table/model, compatibility view, or old writer;
- JMC1 transactional no-op, cancellation, duplicate delivery and readiness remain correct;
- phase/outcome/timestamp, hierarchy, retry and dispatch invariants reject invalid rows;
- attempt audit cannot claim, heartbeat, reserve or recover;
- logs/artifacts accept confined metadata only;
- media detail is strict 1:1 and survives live media deletion through snapshot/SET NULL;
- retirement/reactivation is deterministic and partial sync never retires;
- all history remains renderable after each live subject is retired/deleted;
- revision checksums and current pointer are deterministic;
- simultaneous same-version writers yield one success and one 409;
- notifications, missed-notification repair, stale notification rejection and last-valid
  fallback work across API/worker/scheduler;
- no secret or restart-owned key enters a revision or job snapshot;
- no JSON override write/read remains for migrated values;
- settings pages remain functional and explain stale conflicts/restart ownership;
- retained backend failure set does not grow; affected frontend gates are green.

## 9. Out of scope

- enabling any non-noop definition;
- full JobDefinition/ProgressPolicy/SubjectSnapshot implementation (JMC2B);
- presenters and canonical jobs API replacement (JMC2C);
- physical logs/artifacts/process safety/events broadcaster (Chunk 3);
- frontend Activity/Projection Room redesign (Chunk 6);
- auth, webhook implementation, public reset replacement, Docker hardening or CI rewrite;
- old database data/history migration;
- physical purge of retired library projections.

## 10. Operator handoff

JMC2A is complete only when the shared timeline identifies:

- final commits and schema head;
- exact retained failure set;
- configuration keys by owner/apply mode;
- any inherited JMC1 operator smokes;
- exact JMC2B starting point;
- an honest statement of manual settings/sync/reset smoke performed or pending.
