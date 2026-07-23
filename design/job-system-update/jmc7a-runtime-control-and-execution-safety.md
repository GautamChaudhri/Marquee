# JMC7A — Runtime Control and Execution Safety

> **Implementer:** Read this document in full before changing code. Decisions are locked.
> Execute every internal phase continuously; phase boundaries are verification checkpoints, not
> stopping points. Stop only for a condition in
> [`design/plans/README.md`](../plans/README.md#implementer-continuity-and-stop-conditions)
> or after the complete plan, certification, and final-only history compaction succeed.

**Program:** JMC7 release-readiness recovery

**Predecessor:** [JMC6K personalization and onboarding correctness closure](jmc6k-personalization-and-onboarding-correctness-closure.md)

**Successor:** [JMC7B onboarding, publication, and learning integrity](jmc7b-onboarding-publication-and-learning-integrity.md)

**Architecture context:** [Direct PgQueuer adoption](job-system-pgqueuer-direct-adoption.md),
[job progress/loading experience](job-progress-and-loading-experience.md), and
[Projection Room experience](projection-room-job-experience-redesign.md)

**Shared timeline:** `design/job-system-update/jmc7-readiness-recovery-timeline.md`

**Recommended model tier:** **God**

## 1. Objective

Repair the shared execution substrate before any further product-flow work. After JMC7A, a picked
job can be cancelled without fencing out its terminal writer, safety-gate contention cannot strand
transport work, every advertised retry action is executable and truthful, and letterbox preview
work uses the same canonical process, attempt, progress, fence, and evidence boundaries as every
other heavy operation.

This is the first of three JMC7 plans because its invariants are prerequisites for JMC7B profile,
deployment, and residual jobs and for JMC7C browser certification. Product-specific onboarding,
learning, Activity rendering, and generated-contract changes are intentionally assigned to the
successors.

## 2. Audit findings owned by JMC7A

| ID | Severity | Finding at `jmc6k-complete` | Required result |
|---|---|---|---|
| 7A-C1 | Critical | The public cancel path advances the execution fence before asking PgQueuer to cancel a picked delivery. The active writer then cannot persist `cancelled` or even its stopping/failure evidence. | One bounded cancellation owner reaches a durable terminal result, and stale writers remain unable to publish. |
| 7A-C2 | Critical | Safety-gate timeout/connection failures escape before admission while PgQueuer failure handling holds the transport item; linked run tickets are excluded from the monitor. | Gate contention durably defers or recovers the same delivery and cannot strand a canonical queued job. |
| 7A-H1 | High | All enabled definitions are presented as retryable, while the command supports only a small allowlist. | Presented actions and accepted commands derive from one definition-owned capability contract. |
| 7A-H2 | High | Letterbox preview routes still invoke synchronous probes and renders directly from the API path, outside canonical attempts, safety gates, progress, process containment, and artifacts. | Preview generation is canonical background work with bounded retrieval and no route-owned subprocess execution. |

JMC7A also supplies focused regression evidence for all 43 enabled definitions. JMC7C owns the
final whole-product producer-to-consumer matrix after all three plans land.

## 3. Preserved foundations and non-goals

Preserve PgQueuer as the only transport/claim authority, canonical immutable job history, fenced
attempt writers, retry-as-successor, `ProcessLauncher`/`TrackedProcess`, confined workspaces,
definition-owned timeout/progress/safety policies, and one multiplexed Activity event stream.

JMC7A does not change onboarding candidate semantics, profile evidence, residual scoring, ML
publication compatibility, Activity page layout, generated onboarding schemas, schedule defaults,
or operator activation. It does not add a second scheduler or process-local job queue. It does not
perform real media mutation.

## 4. Locked decisions

| ID | Decision |
|---|---|
| 7A01 | A cancellation request is intent, not terminal ownership. It may mark the job stopping and signal transport/runtime cancellation, but it may not invalidate the active attempt's fence before that attempt performs bounded cleanup and terminalization. |
| 7A02 | The active delivery writer owns normal picked-job cancellation through descendant termination, death confirmation, fenced `cancelled` persistence, log/evidence sealing, and transport acknowledgement. |
| 7A03 | A recovery finalizer may take cancellation ownership only after durable proof that no active writer can still publish: expired runtime/lease plus the existing process-identity and fence takeover checks. It advances the fence exactly once and records why takeover was safe. |
| 7A04 | `stopping` is transient and bounded. Every accepted cancellation ends in `cancelled`, `failed`, `unsafe`, or an explicitly recoverable queued state; no job may remain indefinitely stopping with no owning attempt. |
| 7A05 | Cancellation remains idempotent. Repeating the same request returns current canonical state; a terminal job is never reopened; cancellation racing with normal completion returns the winner's durable state. |
| 7A06 | Safety-gate acquisition occurs before attempt admission. Timeout or transient connection loss therefore creates no execution attempt and performs no domain effect. |
| 7A07 | A gate timeout is a durable admission deferral, not a terminal product failure. The same canonical job and dispatch lineage are rescheduled with bounded definition/platform backoff and an observable wait/defer event. |
| 7A08 | Only allowlisted transient PostgreSQL connection failures receive admission deferral. Invalid requirements, programming errors, corrupted payloads, or exhausted deferral budgets fail through an explicit operational path. |
| 7A09 | Deferral is acknowledged only after the transport change is durable. If PgQueuer cannot durably reschedule the picked item, the recovery monitor must still be able to find and repair the canonical queued job. |
| 7A10 | The transport-intent monitor includes linked run tickets when their canonical job is nonterminal and the transport item is absent, held, or stale beyond its bounded availability/lease window. A run-ticket link is evidence, not an exclusion from recovery. |
| 7A11 | Inspect the installed PgQueuer 1.1.1 source before implementing reschedule/cancel mechanics. Use its supported transaction and acknowledgement semantics; do not infer them from method names or mocks. Product invariants 7A06–7A10 remain fixed if adapter details differ. |
| 7A12 | Retry availability and retry execution call one shared capability resolver over the concrete job and its registered definition. Presenters, detail APIs, bulk actions, and control validation may not reproduce their own retry booleans. |
| 7A13 | Retry never resets a terminal row. It creates a new canonical successor with explicit lineage and a fresh idempotency generation while preserving the immutable subject, bounded request, configuration snapshot, and definition identity needed for safe replay. |
| 7A14 | Each definition explicitly declares whether user retry is unsupported, generic replay-safe, or domain-coordinated. Unsupported actions are absent from responses/UI; domain-coordinated retries still enter through the shared command and resolver. |
| 7A15 | Generic retry is available only for terminal failed/cancelled jobs whose definitions can reconstruct a valid bounded request and whose side-effect policy is replay-safe under existing fences/idempotency. `succeeded`, `no_change`, `superseded`, `unsafe`, and dead-letter outcomes are not generically retried. |
| 7A16 | The 43-definition registry is the enumerable authority for retry/action certification. Every enabled leaf and parent definition has an executable positive or negative action case; a default `enabled == retryable` assertion is forbidden. |
| 7A17 | Letterbox preview CPU/process work does not run in the API event loop or a route-owned executor. The public route submits or reuses a canonical preview job and returns a typed job summary plus preview-result link/state. |
| 7A18 | Prefer an existing letterbox-analysis definition only if its subject, request, progress, result, and idempotency contracts exactly represent preview generation. Otherwise add one explicit `letterbox_preview` definition and corresponding generated contract; do not hide preview behavior behind an unrelated job type. |
| 7A19 | Every preview probe/render launches through `ExecutionContext.process_launcher`, holds declared media-read/CPU safety requirements, observes cancellation/fence ownership, emits typed progress, writes only inside the attempt workspace, and registers bounded preview artifacts before publication. |
| 7A20 | Preview retrieval reads registered artifacts through the confined artifact service. It never accepts a physical path from the browser, exposes a workspace path, or regenerates media inline on cache miss. |
| 7A21 | No regression test may bypass the public control service, gateway adapter, delivery callback, or real definition capability resolver when claiming the corresponding behavior is certified. |
| 7A22 | JMC7A may add one forward migration after `0014_jmc6k` only if the locked durable state cannot be represented safely in existing canonical tables. No compatibility schema, legacy lifecycle, or destructive resquash is permitted. |

## 5. Target cancellation state machine

The implementation must preserve one writer at each transition:

1. The control transaction locks the canonical job, validates expected revision/actions, appends a
   cancellation intent, and moves an eligible job to `stopping` without advancing an active
   attempt fence.
2. For queued work, the gateway cancels the transport item and the control path terminalizes only
   after durable proof that no attempt owns execution.
3. For picked/running work, the gateway/runtime cancellation reaches the active delivery. That
   writer stops all owned descendants, confirms death, writes `cancelled` with its current fence,
   seals evidence, and allows transport acknowledgement.
4. If the writer/runtime disappears, bounded reconciliation proves expiration and process absence,
   takes a new fence, and terminalizes or safely redelivers according to the definition recovery
   policy.
5. Competing completion/cancellation transactions resolve through row lock, job revision, attempt
   identity, and fence. The losing path reports current state and performs no second effect.

Add a durable timestamp/reason/initiator field or typed event only where needed to make the intent
and timeout observable. Do not introduce a second mutable cancellation authority.

## 6. Admission deferral and recovery contract

Add one typed delivery result for pre-admission deferral at the gateway boundary. It records the
canonical job, dispatch generation, cause class, defer count, bounded next-eligible time, and event
metadata. It does not create a `JobAttempt` because no safety gate or execution ownership was
obtained.

The definition/platform policy must cap both delay and total admission-deferral age/count. Normal
contention remains queued and visible. Exhaustion becomes a precise operational failure or
dead-letter result according to the existing terminal policy; it may not silently loop forever.

The monitor must repair all of these crash windows:

- timeout selected but reschedule not committed;
- reschedule committed but callback interrupted before return;
- transport row held/absent while canonical job remains queued;
- linked run ticket exists but is no longer executable;
- database connection is lost during gate wait or deferral recording;
- cancellation arrives while gate acquisition or deferral is in progress.

## 7. Definition-owned action contract

Extend the definition/action policy with a typed retry mode and, where needed, a domain retry
adapter. The shared resolver returns both availability and an exact unavailable reason from the
locked job state. The control command re-evaluates that same resolver inside its transaction.

The successor submission must preserve immutable inputs but must not blindly replay stale live
identifiers. Definition codecs reconstruct the bounded request from canonical request/subject
snapshots and validate current preconditions. Domain-coordinated families such as profile builds
may attach their coordinator lineage only after successor creation succeeds.

Generated API types expose the resolved actions. Frontend code renders only those actions and
handles a stale-action conflict by refreshing the canonical snapshot.

### 7.1 Public contract deltas

| Method and path | Request authority | Target response/behavior |
|---|---|---|
| `POST /api/jobs/{job_id}/cancel` | Existing expected fence/revision plus user intent | Existing typed command response over the post-transaction canonical snapshot. Picked work normally returns transient `stopping`; the active writer later publishes the terminal snapshot through normal SSE/repair. |
| `POST /api/jobs/{job_id}/retry` | Existing expected fence/revision plus user intent | Typed command response containing the new successor snapshot/lineage when allowed. Unsupported/stale actions return the existing typed conflict with the resolver's stable reason and create no job. |
| `POST /api/letterbox/movies/{movie_id}/preview` | Typed bounded mode/minute/exact request; server resolves media | Canonical preview job snapshot, reuse status, Activity/job-detail links, and eventual registered artifact links. |
| `POST /api/letterbox/tv/{series_id}/episodes/{episode_id}/preview` | Same bounded request; server resolves the episode/media snapshot | Canonical preview job snapshot and eventual registered artifact links. |
| Canonical job detail/artifact routes | Job/artifact IDs returned by the server | Preview progress/result retrieval. The former generation-on-`GET` behavior is removed; a read never starts ffmpeg/mkvmerge work. |

The frontend must migrate to these generated contracts in JMC7A. Do not keep an alternate GET path
that generates bytes inline as a compatibility fallback.

## 8. Implementation phases

### Phase 7A0 — freeze the base and make the defects executable

1. Verify clean `jmc6k-complete`, exact commit/tree/parent, author configuration, local recovery
   refs, sole Alembic head/current, deterministic OpenAPI/TypeScript fingerprints, all 43 enabled
   definitions, and the complete backend/frontend baseline.
2. Record the expired/missing prior repository-external JMC6K bundle honestly; do not recreate or
   claim the missing bytes. Create no JMC7 recovery material until the final protocol.
3. Read the installed PgQueuer cancellation, callback, failure, acknowledgement, scheduling, and
   transaction code. Record exact source/version findings in the shared timeline.
4. Freeze the public-control → gateway → delivery → fenced-writer call graph and every direct
   subprocess/executor call reachable from both letterbox preview routes.
5. Add failing integration tests for picked cancellation, real gate contention beyond the current
   admission deadline, lost deferral crash windows, action truth across all definitions, and
   preview cancellation/process containment. Mocks may supply inert media bytes but may not bypass
   the canonical boundaries under test.

### Phase 7A1 — cancellation ownership and terminalization

Implement decisions 7A01–7A05. Cover queued, picked-before-admission, admitted, process-running,
effect-complete-before-terminal, shutdown, stale-writer, and recovery-takeover races. Remove or
replace any control-path fence advance that invalidates the active delivery before cleanup.

Gate the phase with focused public API and gateway-backed delivery tests, process death proof,
event/log/evidence assertions, and a restart reconciliation test. No test may call the gateway
directly as a substitute for the public control service.

### Phase 7A2 — contention-safe admission

Implement decisions 7A06–7A11 and the recovery contract. Use a real disposable PostgreSQL advisory
lock held longer than the configured test admission deadline. Prove the canonical job remains
discoverable, is durably deferred, later executes exactly once, and produces no phantom attempt or
effect during contention.

Add transient connection-loss and hard-error cases, bounded exhaustion, cancellation during wait,
callback interruption at each durability boundary, and monitor recovery for linked run tickets.

### Phase 7A3 — truthful generic and coordinated retry

Implement decisions 7A12–7A16. Inventory every enabled definition and assign an explicit retry
mode. Replace presenter defaults and the command allowlist with the shared resolver. Add successor
request codecs/adapters where safe; keep unsupported actions absent and explain why in the
definition manifest.

Execute the action matrix through the real command path. At minimum cover leaf/parent, failed/
cancelled, stale revision, active job, unsafe, missing subject, duplicate invocation, generic
successor, and profile-domain-coordinated successor behavior.

### Phase 7A4 — canonical letterbox preview

Implement decisions 7A17–7A20. Replace route-owned `run_in_executor`, direct `subprocess.run`, and
preview helper process launches with one registered handler and fixed process-launch path. Keep
preview artifacts bounded by count, bytes, MIME, dimensions, and retention. Add polling/SSE result
discovery through the shared job APIs and preserve existing product response semantics only where
they remain truthful.

Prove API responsiveness during long probes, cancellation and timeout death confirmation, safety
gate release ordering, artifact confinement, cache/reuse idempotency, source retirement, and no raw
subprocess reachability from the route/helper path.

### Phase 7A5 — integrated runtime certification and handoff

1. Run the focused runtime/process/control/definition suites and the complete backend suite against
   an owned disposable PostgreSQL cluster and temporary `DATA_DIR`.
2. Run Ruff, Alembic heads/current/check, deterministic OpenAPI export, generated-TypeScript drift,
   frontend check/lint/unit/build, Playwright/axe, and CPU live capability smokes. No result may
   regress from the recorded baseline. Keep the one inherited deterministic Activity screenshot
   failure red and unchanged for JMC7C; do not re-record it in JMC7A. Record GPU as an unavailable
   capability unless actually present; never convert it to green.
3. Run static reachability checks for route-owned preview processes and duplicated action logic.
4. Update the shared timeline with all phase commits, exact counts, fingerprints, capability
   accounting, deviations, and JMC7B handoff.
5. Create verified timestamped recovery branch/tag and repository-external bundle, then perform the
   final-only tree-identical compaction from exact `jmc6k-complete`. Tag the compact result
   `jmc7a-complete`; do not push or activate.

## 9. Mandatory focused certification

| Area | Required executable proof |
|---|---|
| Picked cancellation | Public cancel request reaches the real gateway and delivery; active descendants die; one current fence writes `cancelled`; transport and canonical state converge after restart. |
| Cancellation races | Success-before-cancel, cancel-before-success, repeated cancel, outer timeout, shutdown, stale writer, and takeover each have one durable winner and no leaked process. |
| Gate contention | A real PostgreSQL gate is held past the deadline; delivery defers durably, later executes once, and remains recoverable through injected crash windows. |
| Gate connection loss | Allowlisted transient loss defers within budget; invalid policy/hard loss does not loop; cancellation remains prompt. |
| Retry truth | Every enabled definition's presented action equals control acceptance. Positive cases create linked successors; negative cases return stable reasons and no job. |
| Preview execution | Movie and TV preview requests use canonical submission/delivery, fixed process launch, typed progress, fences, artifacts, cancellation, timeout, and confined retrieval. |
| Static retirement | No preview-reachable raw `subprocess`, synchronous binary helper, route executor, or alternate lifecycle writer remains. |

## 10. Stop gates

Stop and record evidence if the installed PgQueuer version cannot durably reschedule or cancel a
picked item without violating one-writer semantics; if process death cannot be confirmed before
terminalization; if generic retry would require reconstructing unbounded or missing request data;
if letterbox preview cannot be represented without exposing operator media paths; if an unrelated
or concurrent commit appears; or if any change would require touching an operator database/media
path, pushing, activating schedules, or weakening a locked gate.

## 11. Exit criteria

JMC7A is complete only when all four owned findings are corrected in production paths, the full
action matrix is truthful, every owned/backend gate passes with honest capability accounting, no
test regresses beyond the exact inherited Activity screenshot failure assigned to JMC7C, the
compact tag and external recovery material verify, and JMC7B can rely on bounded cancellation,
admission, retry, and heavy-process behavior without adding a special case.
