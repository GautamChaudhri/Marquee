# JMC6D — Runtime Recovery and Activation Safety

> **Implementer:** Read this document in full before changing code. Decisions are locked.
> Execute every internal phase continuously; phase boundaries are verification checkpoints, not
> stopping points. Stop only for a condition in
> [`design/plans/README.md`](../plans/README.md#implementer-continuity-and-stop-conditions)
> or after the complete plan, certification, and final-only history compaction succeed.

**Program:** Post-certification hardening of the [clean-slate PgQueuer migration](job-system-pgqueuer-migration.md)  
**Predecessor:** [JMC6C zero-green certification](jmc6c-zero-green-certification-and-ci.md)  
**Audit:** [JMC6C activation audit](jmc6c-activation-audit.md)  
**Shared timeline:** `design/job-system-update/jmc6-post-certification-hardening-timeline.md`  
**Recommended model tier:** **God**

## 1. Objective

Correct the release-blocking runtime findings discovered by the owner's post-JMC6 source audit:

1. make redelivery and orphan recovery safe across worker-process and container recreation;
2. make production schedule activation explicit, default-off, and truthfully reported;
3. remove the uncertified webhook attack surface rather than leaving it permissive;
4. make external worker/scheduler health and capabilities observable without depending on the
   API's embedded supervisor; and
5. prove that an active attempt can never be silently acknowledged while its canonical job stays
   `running`.

JMC6D does not add a second queue lease or heartbeat authority. PgQueuer still owns transport
claiming, delivery heartbeat, retry timing, and redelivery. Marquee runtime-instance heartbeats are
topology and safety evidence only.

## 2. Preconditions and stop gates

Before edits:

1. Verify annotated `jmc6c-complete`, its compact commit/tree, recovery refs/bundle, clean ancestry,
   zero-green baseline, schema/OpenAPI fingerprints, exact definition/handler manifest, and the
   owner audit findings against current source.
2. Record the exact plan base and create the shared JMC6 post-certification timeline before the
   first implementation commit.
3. Inspect the installed PgQueuer 1.1.1 delivery, heartbeat, retry, hold, cancellation, and callback
   exception contracts. Code and installed package source beat historical documentation.
4. Use an owned disposable PostgreSQL 18 target and disposable worker processes. Never signal an
   operator process or reuse the operator database.
5. Freeze current `WorkerNode`, `JobAttempt`, worker/scheduler startup, delivery admission, orphan
   reconciliation, Operations, schedule, webhook, readiness, and Docker-environment contracts.

Stop if takeover would require guessing that a process on another host is dead, if PgQueuer can
acknowledge a callback after the handler requests retry/hold, if process identity cannot prevent PID
reuse, or if a migration would rewrite released data rather than applying a forward-compatible
revision.

## 3. Locked decisions

| ID | Decision |
|---|---|
| D01 | PgQueuer remains the only transport claim, delivery heartbeat, retry, schedule-row, and redelivery authority. Marquee runtime heartbeats never reclaim a PgQueuer ticket. |
| D02 | Split logical node labeling from process incarnation. A new durable runtime-instance record has a generated immutable instance ID, role, node label, build, host boot identity, process-start identity, advertised entrypoints/capabilities, readiness, start/stop times, and expiring heartbeat. |
| D03 | `JobAttempt` records the exact worker runtime-instance ID in addition to its human node label. Recovery never selects attempts solely by the current hostname or node label. |
| D04 | Runtime-instance heartbeat is operational/safety evidence only. It cannot extend or replace a queue ticket heartbeat, claim a job, schedule a retry, or authorize a product write. |
| D05 | A delivery encountering a canonical `running`/`stopping` job with an active attempt must produce an explicit result. It may wait/retry, safely supersede a proven-dead attempt, or quarantine an uncertain mutation; it may not return success with no admitted execution. |
| D06 | A live prior instance causes bounded retry/deferral, not acknowledgment. A dead same-host process may be terminated and superseded only after boot ID, PID start identity, and process-group/cgroup membership are verified and death is confirmed. |
| D07 | Read-only or definition-certified idempotent work may supersede a stale remote/unverifiable attempt under a new fence after the prior audit is closed as lost. Unsafe mutation never resumes when prior process death or publication state is unprovable; it becomes `unsafe`/quarantined and its transport ticket is held for operator inspection. |
| D08 | Fence increment, prior-attempt closure, new-attempt creation, and canonical phase transition are one row-locked transaction. All stale progress/result/publish writes remain rejected. |
| D09 | Startup reconciliation scans a bounded global candidate set by runtime-instance state and process evidence. It does not assume that only attempts sharing the new process's node label can be orphaned. |
| D10 | Worker and scheduler create runtime-instance rows, heartbeat periodically through separately bounded SQLAlchemy use, mark graceful stop, and tolerate telemetry-write degradation without corrupting product work. Heartbeat failure is surfaced operationally. |
| D11 | Worker capability evidence includes configured entrypoints, containment tier, required media binaries and versions, and discovered GPU/encoder facts. It contains no paths, credentials, environment values, payloads, or raw device dumps. |
| D12 | A worker registers only explicitly configured and locally supportable entrypoints. Unsupported optional capabilities remain unavailable rather than accepting work that cannot execute. |
| D13 | Operations derives external and embedded worker/scheduler health from durable non-expired runtime instances. The embedded supervisor remains supplemental local process evidence, not the external topology authority. |
| D14 | API liveness remains process/event-loop only. API readiness remains schema/config/event-infrastructure focused; temporary worker absence is a product-operations alert and capability-readiness failure, not an API liveness failure. |
| D15 | Add one restart-owned `JOB_PRODUCTION_SCHEDULES_ENABLED` master gate, default `false`. Every production schedule predicate requires it in addition to its per-schedule code/configuration conditions. Fixed test schedules are unaffected. |
| D16 | Schedule diagnostics report registered, individually activated, configured, effectively enabled, and disabled reason per schedule. Remove the contradictory constant/report that can claim disabled while callbacks enqueue work. |
| D17 | Schedule enablement changes do not create catch-up storms. Existing occurrence idempotency and coalescing remain authoritative. |
| D18 | Webhooks remain deferred. Remove production mounting of Radarr, Sonarr, and Subgen webhook routes, remove the Subgen global-auth exemption, and delete permissive callback execution. Do not implement webhook authentication or behavior in this plan. |
| D19 | The deferred public reset endpoint, browser authentication, and Docker hardening are not redesigned here. Their existing activation warnings remain explicit. Functional tests must not mistake them for certified surfaces. |
| D20 | Add a forward data-preserving Alembic revision for runtime-instance/attempt evidence. Do not rewrite the JMC1-JMC6 baseline; post-JMC6 migrations are treated as production-style upgrades even though Marquee remains unreleased. |
| D21 | OpenAPI/TypeScript contracts are regenerated for removed webhook routes and revised Operations/readiness data. Raw runtime-instance IDs and PgQueuer IDs remain diagnostic, never product-facing job identity. |
| D22 | No JMC6D implementer pushes, activates schedules, deletes recovery refs, or adds agent/model attribution. |

## 4. Runtime-instance and recovery contract

Create a durable runtime-instance model separate from canonical job ownership. The exact table name
is implementation-owned, but it must support multiple process incarnations per logical node and
both worker and scheduler roles. Keep or reshape `WorkerNode` only if it still has a clear
non-overlapping purpose; do not retain two competing health authorities.

Worker startup order is:

1. verify schema/configuration;
2. create the runtime incarnation and begin heartbeat;
3. reconcile bounded stale attempts/workspaces/logs/artifacts;
4. advertise only certified entrypoints/capabilities;
5. enter PgQueuer's manager loop.

Delivery admission must distinguish:

- canonical terminal/cancelled/stale generation: safe no-op;
- concurrent duplicate with a live active attempt: bounded transport retry/deferral;
- stale attempt with proven-dead owned process: close, fence, and admit a new attempt;
- stale read-only/idempotent remote attempt: policy-authorized supersession;
- stale unsafe/unverifiable mutation: quarantine and hold;
- published-but-uncommitted evidence: use the existing publication reconciler before any retry.

Add deterministic tests for every branch. A callback that does not execute must prove whether the
transport remains retryable/held or the canonical job is terminal; silent successful return is
forbidden.

## 5. Schedule, webhook, and Operations behavior

The production scheduler may register code-owned callbacks while the master gate is off, but no
ordinary production occurrence may create a canonical job. Enabling the master gate is an explicit
operator restart/configuration decision. Readiness and Operations must show the same effective
answer used by callbacks.

Remove deferred webhook routes from the mounted API and generated contract. Preserve future-facing
design documentation if useful, but no executable callback, auth exemption, state singleton, or
route test remains. This does not authorize work on webhook product behavior.

Operations must show bounded runtime summaries:

- role, logical label, build, readiness, heartbeat freshness, entrypoint classes, containment, and
  sanitized capability availability;
- active/stale/stopped counts and last heartbeat;
- scheduler presence and production schedule master/effective state;
- mismatches such as an enabled definition with no fresh capable worker;
- no credentials, filesystem paths, PIDs, raw environment, payloads, or unbounded rows.

## 6. Implementation phases

### Phase D0 — verify JMC6C and freeze failure contracts

Create the shared timeline; verify compact ancestry, recovery material, zero-green gates, current
schema/contracts, and the audit reproduction cases. Add failing regression tests for container
identity change, running-attempt redelivery, misleading schedule state, unmounted webhooks, and
external Operations health before changing behavior.

### Phase D1 — runtime-instance schema and lifecycle

Add the forward migration/model, worker and scheduler registration, periodic heartbeat, graceful
stop, capability/entrypoint advertisement, expiration semantics, and bounded telemetry failure
handling. Update attempt admission to record the incarnation.

### Phase D2 — redelivery, takeover, and orphan reconciliation

Implement explicit running-attempt outcomes, global bounded stale-instance reconciliation,
same-host verified death, policy-limited supersession, unsafe quarantine, and publication-aware
recovery. Certify worker `SIGTERM`, `SIGKILL`, container-style identity change, database loss,
listener recovery, PID reuse, and stale-fence rejection.

### Phase D3 — truthful schedule gating

Add the restart-owned master gate, require it in every production predicate, preserve occurrence
coalescing/idempotency, and make readiness/Operations use one effective-state calculator. Test
multi-scheduler, gate toggles, restart, misfire, and no backlog burst.

### Phase D4 — webhook removal and external Operations

Unmount/delete deferred webhook execution and auth exemptions; regenerate contracts. Switch
Operations to fresh runtime-instance/capability evidence, retain embedded-supervisor details only
as supplemental data, and add bounded query/index tests.

### Phase D5 — integrated certification and compaction

Run §7, the complete retained suites, migration upgrade/fresh-target checks, generated contracts,
and external worker/scheduler smokes. Update the shared timeline, then perform §8 only after every
gate succeeds.

After every phase, run its gates, commit, update the timeline, and continue immediately. Successful
phase completion is not a reason to stop.

## 7. Acceptance and verification

- Worker/container recreation with a changed hostname cannot strand or silently acknowledge a
  running canonical job.
- Live duplicate delivery does not execute twice or complete the transport early.
- Proven-dead read-only work redelivers under a new attempt/fence; uncertain mutation quarantines.
- Same-host orphan termination rejects wrong boot ID, reused PID, changed start ticks, and foreign
  cgroup/process group.
- Runtime heartbeat loss, graceful stop, hard kill, API restart, scheduler restart, and PostgreSQL
  loss/recovery produce truthful bounded Operations state.
- External healthy workers/scheduler no longer appear unhealthy solely because the API has no
  embedded supervisor.
- An enabled definition without a fresh capable entrypoint is visible as unavailable.
- With the master schedule gate false, no production callback creates a job. With it true, only
  individually configured/activated schedules run, with no catch-up flood.
- No webhook path appears in the mounted route table, auth exemptions, OpenAPI, generated client,
  or production tests.
- Alembic upgrades the certified JMC6 schema and also creates an equivalent fresh target; downgrade
  expectations are documented without destructive operator use.
- Full backend pytest remains zero-failure/zero-skip/zero-xfail; `ruff check marquee tests scripts`,
  Alembic/model equivalence, PgQueuer verify, OpenAPI/type drift, frontend unit/check/lint/build/E2E,
  and `git diff --check` pass.

Use disposable PostgreSQL and subprocesses. Record any real process-kill or restart smoke honestly;
never claim a Docker recreation smoke if only a unit simulation ran.

## 8. Mandatory final-only history compaction

Perform only after D5 succeeds:

1. Verify a clean, linear, configured-author, JMC6D-only, unpushed range after exact
   `jmc6c-complete`.
2. Commit the final pre-squash timeline state with phase hashes, gates, deviations, operator work,
   pre-squash tip, and intended tag `jmc6d-complete`.
3. Create timestamped recovery branch/tag and a verified repository-external Git bundle.
4. Record the certified tree hash; soft-reset through RTK to the exact plan base and create one
   configured-author commit: `jmc6d: harden runtime recovery and activation safety`.
5. Prove exact tree identity, sole parent, clean worktree, recovery refs, and verified bundle.
6. Create annotated local tag `jmc6d-complete`; do not edit the timeline afterward.
7. Do not push, force-push, delete recovery material, or add authorship/generator attribution.

Stop instead of rewriting history if ancestry, ownership, concurrency, backup, or tree identity is
uncertain.

## 9. Out of scope and handoff

Out of scope: canonical subtitle/taste route closure, feature-page recovery, broad legacy module
deletion, browser auth, public reset replacement, Docker hardening/version alignment, webhook
implementation, `radarr_upgrade`, and activation.

Handoff reports the compact tag/hash/tree/base and recovery bundle; migration and generated
contract hashes; runtime-instance/heartbeat/expiration semantics; exact takeover/quarantine
matrix; schedule master/effective state; removed webhook surface; capability manifest; complete
gates; manual smoke evidence; and the exact JMC6E starting condition.
