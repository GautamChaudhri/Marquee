# JMC4A — Canonical Producers, Batches, and Schedules

**Program:** [clean-slate PgQueuer migration](job-system-pgqueuer-migration.md)  
**Architecture:** [direct PgQueuer adoption](job-system-pgqueuer-direct-adoption.md)  
**Previous plan:** [JMC3C backup, ingress, and certification](jmc3c-backup-ingress-and-certification.md)  
**Next plan:** [JMC4B library, scans, and media analysis](jmc4b-library-scans-and-media-analysis.md)

> **For the implementing agent:** Read `AGENTS.md`, `CLAUDE.md` when present,
> `design/plans/README.md`, `design/plans/04-television-backend.md` §0, all JMC1–JMC3
> plans and timelines, and this document **in full** before changing code. Decisions below
> are final. **Verify in code** means inspect the current symbol and all references first;
> implementation locations may drift after this plan is authored.
>
> **Shared JMC4 timeline:**
> `design/job-system-update/jmc4-nonmutating-jobs-timeline.md`. JMC4A creates it before
> its first implementation commit if absent. If it exists, read it, verify its claims
> against Git and the working tree, and resume exactly where it stops. Append completed
> work, phase hash, verification, current phase, exact next steps, deviations and why, and
> pending operator actions after every phase commit. JMC4B and JMC4C use the same file.
>
> **Git authorship:** use only the repository's configured Git user. Never add yourself, a
> model, or an assistant as author, co-author, contributor, or generator. No
> `Co-Authored-By`, “Generated with,” model-name, or assistant-name attribution is allowed.

**Goal:** Replace the fail-closed legacy producer surface with one typed canonical submission
service, implement transport-free batch parents and durable aggregation, register safe worker
entrypoints, and make PgQueuer schedule callbacks enqueue ordinary canonical jobs.

**Ordering:** first of three JMC4 plans. JMC3 must be complete and certified. JMC4B and JMC4C
must not start until JMC4A is complete, compacted to one verified commit, and tagged
`jmc4a-complete`.

**Dispatch boundary:** `system_noop` remains the only production-enabled definition throughout
JMC4A. Test-only fixed definitions may exercise producers, batches, schedules, and entrypoints,
but no real feature handler becomes reachable.

## 1. Preconditions and stop gates

Before implementation:

1. Verify the compact JMC3A/B/C commits and the complete JMC3 timeline against Git. Record
   the actual JMC3 tree, Alembic head, schema markers, PgQueuer 1.1.1 durable fingerprint,
   OpenAPI count, generated-type state, retained test failures, and operator-only smokes.
2. Recreate an owned disposable PostgreSQL database and prove readiness exposes exactly
   `control/system_noop` as dispatch enabled. No backup, legacy media, or other handler may run.
3. Inventory and freeze the canonical `Job`, `JobDispatch`, `JobAttempt`, `JobEvent`,
   definition registry, PgQueuer gateway, command service, delivery kernel, parent-progress
   prototype, job routes, and every `job_manager`/`media_job_manager` producer call.
4. Inspect the installed `pgqueuer==1.1.1` source. Prove public `Queries.enqueue()` supports
   ordered list input and ordered numeric IDs in the caller-owned SQLAlchemy transaction.
   Verify the installed schedule callback, schedule context, concurrency, cancellation, and
   duplicate-key behavior rather than relying on prose documentation.
5. Record branch/HEAD, the exact plan base, clean-tree ownership, configured Git author,
   full pytest/Ruff baseline, Alembic/schema checks, deterministic OpenAPI/type checks, and
   affected frontend gates in the new shared timeline.
6. Verify no JMC4 timeline or completion tag already exists unexpectedly. Stop on an
   unexplained ref rather than overwriting it.

Stop if JMC3 certification cannot be reproduced, a non-noop handler is executable, the working
tree contains unrelated work, PgQueuer's installed public contract contradicts this plan, or
same-transaction ordered bulk enqueue cannot be proven. Carry accepted host-only smokes forward
honestly; do not use them to conceal a new automated regression.

## 2. Locked decisions

| ID | Decision |
|---|---|
| A1 | PgQueuer remains the sole queue, claim, retry-time, heartbeat, schedule-row, and transport-cancellation authority. JMC4A adds no outbox, claim loop, schedule table, or queue lease. |
| A2 | One canonical submission service is the only product-job creation authority. Legacy `JobManager` and `MediaJobManager` creation methods remain fail closed until each caller migrates. |
| A3 | A caller supplies intent, domain payload, subject locator, trigger, initiator, idempotency key, priority, and optional eligibility time. `JobDefinition` supplies schemas, entrypoint, timeout, retry, safety, configuration selection, progress, presenter, and actions. |
| A4 | Request validation, immutable subject construction, configuration snapshotting, `Job`, dispatch/event rows, and PgQueuer ticket creation occur in one caller-owned SQLAlchemy transaction. |
| A5 | Canonical idempotency is authoritative. Reuse returns the existing job only when type, version, normalized request, subject, and semantic scope match; otherwise it is a typed conflict. PgQueuer dedupe remains defense in depth. |
| A6 | Bulk enqueue uses public `Queries.enqueue()` list arguments through the existing raw asyncpg bridge. Returned IDs must be one-for-one, ordered, unique, and stored before the caller commits. |
| A7 | A canonical batch parent is a product projection with no PgQueuer ticket, transport attempt, or execution handler. It never consumes an entrypoint slot while children run. |
| A8 | A new Marquee-owned batch projection is not the retired `MediaBatch`; it stores product scope/seal/counters only and owns no claim, lease, worker, or schedule state. |
| A9 | Fixed batches create the parent, batch projection, children, dispatch/events, and every transport ticket atomically. Any failure rolls back all Marquee and PgQueuer rows. |
| A10 | Dynamic batches have an explicit open generation. Child append locks the projection, validates the registered child type and cap, and fails after sealing. Sealing is permanent and idempotent. |
| A11 | Open batches are indeterminate. Sealed batches use the sealed child count as an immutable denominator. A current child/stage change never resets overall progress. |
| A12 | Child terminal transition updates batch accounting in the same transaction and is counted once. Bounded repair may rebuild semantic projection from canonical children but may not enqueue, claim, retry, or infer transport success. |
| A13 | Schedule callbacks only call the canonical submission service. They never import route modules or execute product logic. |
| A14 | Schedule occurrence identity is derived from the code-owned schedule key and PgQueuer due time. Duplicate callback execution returns the same canonical job. |
| A15 | Periodic missed work is coalesced to at most one current occurrence; JMC4 does not create an unbounded catch-up backlog. All schedule timestamps and occurrence keys use UTC. |
| A16 | Register `control`, `network`, `cpu`, `media_read`, `gpu`, and `maintenance`. No `media_write` product definition is enabled before Chunk 5. Every delivered entrypoint must equal the definition entrypoint. |
| A17 | PgQueuer's database-global entrypoint limits are complemented by `JOB_WORKER_CONCURRENCY` as the per-worker total cap. Advisory safety gates remain secondary physical-safety constraints. |
| A18 | No real non-noop definition becomes enabled in JMC4A. Readiness and static tests enforce this boundary. |
| A19 | Phase commits are mandatory during implementation. Squashing is mandatory only after all final gates pass, with verified recovery refs/bundle and exact tree identity. No JMC4 agent pushes. |
| A20 | Existing failures may shrink but may not grow. Never add a skip or `xfail`, and never run `ruff format`. |

The transport contracts are the installed PgQueuer 1.1.1 package plus the official
[package release](https://pypi.org/project/pgqueuer/). PostgreSQL transaction and locking
behavior follows the [PostgreSQL 18 documentation](https://www.postgresql.org/docs/18/).

## 3. Canonical submission contract

Implement one narrow service with an interface equivalent to:

```python
async def submit_job(
    session: AsyncSession,
    *,
    job_type: str,
    request: Mapping[str, object],
    subject: SubjectLocator,
    trigger: TriggerKind,
    initiator: Initiator | None,
    idempotency_key: str,
    priority: int | None = None,
    eligible_at: datetime | None = None,
    parent: ParentBinding | None = None,
) -> SubmissionResult:
    ...
```

`SubmissionResult` contains the canonical job ID, created/reused disposition, compact snapshot
link, detail link, and current canonical phase. It never exposes the numeric PgQueuer ID.

The service must:

- require an active transaction and never commit, roll back, or open an independent session;
- resolve an enabled definition before creating any row;
- reject reserved webhook triggers and client-provided execution policy;
- validate and normalize the current request version before idempotency comparison;
- build and validate the immutable subject snapshot from live data at enqueue time;
- snapshot only the definition's bounded non-secret configuration keys;
- apply definition-owned default priority, eligibility, retry, timeout, feature, presenter,
  progress, and action policy;
- create the canonical job, root/correlation hierarchy, dispatch audit, and initial semantic
  event before calling PgQueuer;
- store the returned transport ID on the canonical job and dispatch audit;
- turn PgQueuer duplicate-key races into canonical idempotency resolution, not a 500;
- return a typed conflict when one idempotency key is reused for different semantic intent;
- redact request-validation and subject-resolution failures from logs and API errors.

The public queue envelope remains exactly `job_id`, `payload_version`, and
`dispatch_generation`. Domain request data and secrets never enter PgQueuer payloads.

### 3.1 Bulk submission

Add an internal ordered bulk primitive used by fixed batches. It accepts fully validated child
intents, inserts all canonical children and dispatch/event rows, then invokes one public
`Queries.enqueue()` batch call. It must reject:

- empty or oversized input outside an explicitly allowed empty-batch path;
- duplicate canonical or dedupe identities within one request;
- returned ID count/order/type mismatch;
- a child type outside the parent's registered policy;
- inconsistent root, correlation, configuration version, or trigger provenance.

Batch size is bounded by a code-owned maximum and API-specific lower caps. Chunk requests rather
than constructing an unbounded SQL/JSON payload, but preserve one outer transaction for the fixed
batch. Prove rollback after canonical insert, event insert, raw-driver acquisition, PgQueuer
enqueue, ID assignment, and final flush.

## 4. Canonical batch projection

Add a strict 1:1 projection keyed by parent job ID with at least:

- mode: fixed or dynamic;
- generation and seal state;
- sealed timestamp and immutable sealed child total;
- created and terminal totals;
- per-outcome counters;
- projection sequence and updated timestamp;
- bounded failure/attention summary metadata.

The parent `Job` remains the public identity, subject, presentation, progress, desired state, and
outcome. The projection is internal coordination state and is never a second job lifecycle.

### 4.1 Fixed creation

Creation order inside one transaction:

1. validate parent definition, parent request, scope, and all child intents;
2. create the parent and sealed projection;
3. create every child with `parent_id`, common `root_id`/correlation, child-specific immutable
   subject, and `batch`/`parent` trigger provenance as appropriate;
4. create parent/child semantic events and dispatch audits;
5. bulk enqueue every child;
6. store transport IDs and flush;
7. publish the initial sealed aggregate snapshot;
8. let the caller commit.

An explicitly empty fixed batch becomes terminal `no_change` with a friendly reason and no ticket.

### 4.2 Dynamic append and seal

- Opening creates a ticketless parent and an open projection with a unique generation.
- Append row-locks the projection, checks open/generation/cap/idempotency/child policy, and creates
  canonical child tickets in the caller transaction.
- A retry of the same append returns the same child; a different payload conflicts.
- Seal row-locks the projection, freezes the exact total, emits a seal event, and is idempotent
  only for the same generation.
- A crashed producer may resume the same open generation. A different producer cannot take it
  over without an explicit fenced repair contract.
- Open batches cannot terminalize. A sealed empty batch terminalizes `no_change`.

### 4.3 Aggregate state and outcomes

Update batch accounting when a child first transitions terminal. Parent phase is queued while no
child has started, running once any child starts/terminates, stopping after parent cancellation,
and terminal only when sealed and all children are terminal.

Outcome rules:

- all `no_change` or empty sealed scope → `no_change`;
- one or more `succeeded`, with the remainder `succeeded`/`no_change` → `succeeded`;
- all cancelled after parent cancellation and no positive result → `cancelled`;
- all superseded and no positive result → `superseded`;
- only failure-class results → `unsafe` if any child is unsafe, otherwise `failed`;
- any positive result mixed with failure, unsafe, dead-letter, or cancellation →
  `partially_succeeded`;
- a child already marked `partially_succeeded` makes the parent partial unless every other child
  is `no_change` and the presenter has a stricter registered rule.

Projection repair reads children in bounded pages, recomputes counters, and compare-and-sets the
projection sequence. It emits a repair event only when durable state changes. It never modifies a
child or PgQueuer row.

### 4.4 Parent commands

- Cancel sets parent desired state, then issues canonical cancellation to nonterminal descendants
  in bounded pages. It never cancels jobs that share only a correlation ID.
- Pause/resume and priority changes apply only where the registered parent policy supports them;
  priority is applied to still-queued children within their execution classes.
- Retry creates a new canonical parent. Its definition decides whether to clone all children or
  only failed/cancelled children; the original history is immutable.
- Child listing and failure summaries remain cursor-paginated and query-bounded.

## 5. Schedule catalog and callbacks

Create a code-owned schedule catalog containing stable key, PgQueuer entrypoint/expression,
produced job type, trigger/initiator, enabled predicate, occurrence policy, and request/subject
builder. Do not add a Marquee schedule table.

JMC4A registers but does not product-enable:

- a periodic library-sync tick derived from `SYNC_INTERVAL_MINUTES`;
- an hourly audio/subtitle deep-scan tick that reads the current versioned
  `AUDIO_SUBS_DEEP_SCAN_ENABLED`, `AUDIO_SUBS_DEEP_SCAN_HOUR`, and batch settings.

Callbacks receive PgQueuer's schedule value, derive a normalized UTC due occurrence, read current
configuration, and submit through the canonical service. Disabled or ineligible occurrences are
recorded as bounded scheduler diagnostics, not product jobs. A callback failure leaves PgQueuer's
schedule recovery behavior authoritative.

Use canonical keys such as `schedule:<catalog-key>:<normalized-due-utc>`. Restart or a second
scheduler must resolve to the same job. For periodic sync, use a stable frequent cron callback and
an interval bucket rather than inventing a persistent Marquee cursor. Coalesce missed buckets to the
current eligible bucket.

## 6. Worker entrypoints and readiness

Generalize `create_worker()` so each registered PgQueuer entrypoint calls the same delivery kernel
with an immutable expected-entrypoint value. The kernel verifies transport entrypoint, canonical
definition, migration state, and enabled handler before admission.

Add explicit global limits for CPU and maintenance if absent; retain configured control, network,
media-read, GPU, and later media-write limits. `max_concurrent_tasks` uses
`JOB_WORKER_CONCURRENCY`, and configuration validation must prove it is coherent with PgQueuer
batch size and the database/safety-gate connection budget.

Readiness reports sanitized registered/enabled entrypoints, definition counts, schedule catalog
health, batch projection schema, and connection arithmetic. It exposes no raw PgQueuer rows,
schedule IDs, advisory keys, payloads, or secrets.

## 7. Implementation phases

Each phase ends with focused tests, the full retained pytest comparison, `ruff check marquee tests`,
schema checks where applicable, deterministic OpenAPI/type checks where affected, affected frontend
check/lint/build, `git diff --check`, one short lowercase phase commit, and a timeline update.

### Phase A0 — verify JMC3 and freeze producer contracts

- Complete §1, freeze producer/route/registry/batch/schedule/worker inventories, and record the
  exact retained baseline and plan base.
- Add contract tests for installed PgQueuer bulk enqueue order, rollback, schedule values, and
  entrypoint concurrency semantics.

### Phase A1 — canonical single and bulk submission

- Implement definition-owned submission, idempotency comparison, subject/configuration snapshots,
  API-safe results, and ordered bulk gateway support.
- Keep `create_system_noop` as a thin typed caller or migrate it without changing its contract.

### Phase A2 — batch schema and fixed creation

- Add the target Alembic/model projection, fixed atomic creation, empty scope, bulk tickets,
  parent APIs, and bounded child listing.
- Prove model/Alembic equivalence on a fresh database.

### Phase A3 — dynamic sealing, aggregation, and commands

- Implement open/append/seal, terminal accounting, progress/outcome projection, repair, parent
  cancellation, priority, and retry lineage.
- Exercise concurrent append/seal/terminal races on PostgreSQL.

### Phase A4 — schedule catalog and callbacks

- Register fixed test schedules plus inactive production sync/deep-scan callbacks.
- Prove occurrence idempotency, coalescing, multi-scheduler uniqueness, restart, configuration
  disable/re-enable, and misfire behavior.

### Phase A5 — entrypoints, load gate, and certification

- Generalize worker registration/delivery/readiness and certify all A contracts under concurrent
  control/network/CPU/media-read/GPU/maintenance test canaries.
- Prove only `system_noop` is product enabled, complete all final gates, then perform §9.

## 8. Acceptance matrix

JMC4A is incomplete until automated evidence proves:

- single and bulk canonical/PgQueuer commit and rollback at every boundary;
- canonical idempotency reuse, mismatch conflict, and concurrent duplicate race;
- exact wire payload and absence of secrets/domain documents in PgQueuer;
- fixed all-or-nothing batch creation and sealed empty outcome;
- dynamic append idempotency, cap, generation, append-after-seal rejection, and seal races;
- terminal child counted exactly once under retry/redelivery/concurrent completion;
- every aggregate outcome rule and stable nested progress denominator;
- parent cancellation does not cross correlation-only boundaries;
- projection repair is bounded, idempotent, and transport-inert;
- schedule duplicate/restart/misfire/disable/coalescing and two-scheduler uniqueness;
- entrypoint mismatch fails before attempt admission;
- per-entrypoint and per-worker concurrency plus database connection budgets;
- no non-noop product handler, legacy writer, inline executor, or media-write definition starts;
- no new failure, error, skip, or `xfail`; all backend/frontend/generated/schema gates pass.

## 9. Mandatory final-only history compaction

Do this only after A5 is fully certified and recorded. It is part of completion, not a substitute
for phase commits.

1. Ensure the working tree is clean. Verify every commit after the recorded A0 plan base is
   linear, authored by the configured repository user, owned solely by JMC4A, and not pushed.
   Stop on any merge, unrelated commit, concurrent work, or uncertain ownership.
2. Append the complete pre-squash phase map, tests, schema/contracts, deviations, pending operator
   work, pre-squash tip, and intended resolver tag `jmc4a-complete` to the shared timeline and
   commit that timeline as the last phase commit.
3. Create timestamped local recovery branch and annotated tag named with
   `backup/jmc4a-pre-squash-<UTC timestamp>` / `backup-jmc4a-pre-squash-<UTC timestamp>`.
4. Create a complete Git bundle outside the repository, preferably under
   `/home/quartermaster/backups/Marquee/`, containing the current branch and recovery refs. If
   permission is unavailable, request it; do not silently rely on `/tmp`. Run `git bundle verify`.
5. Record the certified pre-squash commit and tree hash outside the worktree output. Through RTK,
   soft-reset to the exact A0 plan base and create one commit using only the configured author:
   `jmc4a: establish canonical job orchestration`.
6. Verify the new commit's tree hash exactly equals the certified pre-squash tree. Verify the
   plan base is its sole parent, the worktree is clean, and the recovery refs/bundle still resolve.
7. Create the local annotated tag `jmc4a-complete` on the compact commit. Report its full hash,
   tree hash, recovery refs, bundle path, and verification. Do not edit the timeline afterward;
   it resolves the compact hash through this tag because a commit cannot contain its own hash.
8. Do not push, force-push, delete recovery refs, or start JMC4B. Stop immediately if any check
   differs. Tree identity permits relying on the just-completed full certification; rerun a focused
   smoke and `git diff --check`, but do not claim a post-squash full suite unless it was run.

All Git and development commands go through RTK. No agent/model attribution may appear in the
compact commit, tag message, timeline, bundle name, or files.

## 10. Out of scope

- enabling a real non-noop handler or production schedule occurrence;
- media mutation, poster deployment/reset, healing, backup creation, cache/retention deletion,
  webhooks, or `radarr_upgrade`;
- Projection Room/shared feature-page visual work;
- authentication, Docker hardening, GitHub Actions modernization, or zero-failure cleanup;
- pushing any JMC4 commit or tag.

## 11. Operator handoff

The final pre-squash timeline entry and completion report must identify the compact resolver tag,
full compact hash, plan base, recovery refs/bundle, exact retained failures, Alembic/schema and
contract versions, batch caps/outcome rules, schedule keys/UTC/coalescing behavior, entrypoint and
connection budgets, performed smokes, deferred host-only smokes, and the JMC4B starting condition
with only `system_noop` product enabled.
