# JMC3A — Execution Kernel and Filesystem Safety

**Program:** [clean-slate PgQueuer migration](job-system-pgqueuer-migration.md)  
**Architecture:** [direct PgQueuer adoption](job-system-pgqueuer-direct-adoption.md)  
**Previous program:** [JMC2 presentation and API contracts](jmc2c-presentation-and-api-contracts.md)  
**Next plan:** [JMC3B progress, logs, artifacts, and events](jmc3b-progress-logs-artifacts-and-events.md)

> **For the implementing agent:** Read `AGENTS.md`, `CLAUDE.md` when present,
> `design/plans/README.md`, `design/plans/04-television-backend.md` §0, the complete JMC1
> and JMC2 plans/timelines, and this document **in full** before changing code. Decisions
> below are final. **Verify in code** means inspect the current symbol and every reference;
> JMC2 is still being implemented while this plan is authored, so locations may drift.
>
> **Shared JMC3 timeline:**
> `design/job-system-update/jmc3-safety-and-evidence-timeline.md`. JMC3A creates it before
> its first implementation commit if absent. If it exists, read it, verify every claim
> against `git log` and the working tree, and resume rather than repeating completed work.
> After every phase commit append the hash, verification, current work, exact next steps,
> deviations and why, and pending operator actions. JMC3B and JMC3C append to the same file.
>
> **Git authorship:** use only the repository's configured Git user. Never add yourself, a
> model, or an assistant as author, co-author, contributor, or generator. No
> `Co-Authored-By`, “Generated with,” model-name, or assistant-name attribution is allowed.

**Goal:** Generalize the JMC1 no-op delivery wrapper into a fenced, registry-driven execution
kernel; prove physical cancellation and replay-safe publication; and replace Marquee's
scattered path handling with one confined filesystem authority.

**Ordering:** first of three JMC3 plans. JMC2C must be complete and certified. JMC3B and
JMC3C must not begin until this plan is complete and verified in the shared timeline.

**Dispatch boundary:** only `system_noop` remains production-enabled. Fixed development/test
canaries exercise process and staging behavior but cannot accept an executable, arbitrary
arguments, environment variables, filesystem paths, or code from an HTTP payload.

## 1. Preconditions and stop gates

Before implementation:

1. Verify the final JMC2 timeline against Git: JMC2A/B/C hashes, Alembic head, target schema,
   definition manifest, presenters, canonical APIs, generated OpenAPI/TypeScript contracts,
   exact retained failure set, and pending operator work.
2. Recreate a disposable database and prove only the final JMC2 dispatch-enabled manifest is
   executable. At the planned boundary that must be `control/system_noop` only.
3. Inventory the implemented delivery wrapper, canonical command service, PgQueuer gateway,
   worker startup, cancellation commands, readiness, evidence models, and all direct process
   launches. Freeze their current behavior with tests before generalization.
4. Inventory every path classification, `FileResponse`, open/copy/replace/delete, archive
   extraction, temporary directory, staging area, backup root, and raw-path fallback in
   backend code. Distinguish path-prefix translation from security confinement.
5. Record branch/HEAD, clean/owned paths, configured Git author, complete pytest/Ruff result,
   Alembic/reset/schema fingerprints, generated-contract drift checks, and affected frontend
   gates in the shared timeline.
6. Verify the pinned Python, asyncpg, PostgreSQL, PgQueuer, and Linux/container behavior from
   installed source/runtime. Record whether cgroup v2 is mounted and delegated in the test
   and operator environments.

Stop if JMC2C is incomplete, the working tree contains overlapping work owned by another
agent, the final registry has an uncovered built-in, or a non-noop definition is executable.
Preserve unrelated changes. A pending JMC1/JMC2 operator-only smoke is not automatically a
blocker when the prior timeline explicitly accepted it; carry it forward honestly.

## 2. Locked decisions

| ID | Decision |
|---|---|
| E1 | PgQueuer remains the sole dequeue, heartbeat, stale-delivery, retry-timing, and transport-cancellation authority. JMC3 adds no claim, lease, reservation, or queue-recovery loop. |
| E2 | The delivery kernel resolves the completed JMC2 `JobDefinition`; hard-coded `system_noop` lifecycle policy is removed, but only `system_noop` remains production dispatch-enabled. |
| E3 | Safety gates are acquired before a Marquee attempt is admitted. Waiting, pause, or cancellation before admission creates no audit attempt and consumes no domain-attempt allowance. |
| E4 | Every canonical write after admission compares job ID, current attempt ID, fence token, dispatch generation, and allowed nonterminal state. Zero affected rows means stale ownership, not success. |
| E5 | Advisory locks are deterministic session-level PostgreSQL locks held on a dedicated direct asyncpg connection for the complete physical attempt. They are not represented as queue rows or product resources. |
| E6 | Lock keys use a domain-separated stable signed 64-bit digest; Python `hash()`, raw user integers, and numeric PgQueuer IDs are prohibited. |
| E7 | Lock order is media-file identity, media-write permit, GPU permit, then shared maintenance. An exclusive maintenance operation acquires only the maintenance key; it may not mix exclusive maintenance with lower-order locks. |
| E8 | All external commands use one tracked exec launcher—never a shell—with an allowlisted environment, confined working directory, new POSIX session/process group, concurrently drained pipes, and bounded inputs. |
| E9 | Process identity is `(worker/node identity, host boot ID, PID, kernel process-start identity)`. PID alone is never sufficient for recovery or signaling. A live pidfd may supplement but not replace durable identity. |
| E10 | New process groups are mandatory on supported production POSIX hosts. Delegated cgroup v2 is used when available. Lack of cgroup delegation is a reported capability, not silently claimed support. |
| E11 | Fixed read-only canaries may run with certified process-group containment. Later unsafe mutation definitions fail admission unless their definition's required containment capability is present and certified. |
| E12 | Cancellation is cooperative, then TERM, then KILL, followed by positive process-tree death confirmation. Canonical cancellation and safety-gate release cannot precede death confirmation. |
| E13 | If recovery cannot prove process identity or death, it does not signal or publish. The job/attempt becomes `unsafe`/quarantined with operator remediation. |
| E14 | Children write only attempt-scoped staging. The fenced coordinator alone validates and publishes. Cross-device copy-and-replace is not an atomic fallback. |
| E15 | The filesystem service is the only authority for path classification, serving, copying, replacement, deletion, extraction, and cleanup. Public and evidence contracts use confined keys, not paths. |
| E16 | `Path.is_relative_to` after resolution is the minimum containment rule. String-prefix checks are prohibited for security decisions. |
| E17 | The transport-intent monitor reconciles committed pause/cancel intent with the current ticket through PgQueuer's public gateway. It cannot schedule work, recover picked tickets, or infer semantic success. |
| E18 | Existing failures may shrink but the retained set may not grow. Every phase runs complete pytest and `ruff check marquee tests`; never run `ruff format`. |

The locking design follows PostgreSQL's documented distinction between session and
transaction advisory locks: session locks survive transaction boundaries and release when
the dedicated connection closes. See
[PostgreSQL explicit locking](https://www.postgresql.org/docs/18/explicit-locking.html).

## 3. Execution-kernel contracts

### 3.1 Immutable delivery and attempt context

After strict transport-envelope parsing, construct an immutable context equivalent to:

```text
DeliveryIdentity
  canonical_job_id
  dispatch_generation
  pgqueuer_job_id              internal only
  pgqueuer_attempt
  definition_key/version

AttemptIdentity
  attempt_id/number
  fence_token
  worker_node/build/boot identity

ExecutionContext
  delivery + attempt identity
  validated request/configuration/subject snapshots
  definition policies
  cancellation token
  acquired safety-gate handle
  confined attempt workspace
  process launcher
  fenced writer
```

The handler cannot receive an ORM `Job`, database session, PgQueuer row, raw destination
path, or client-owned execution policy. It receives validated immutable values and narrow
capabilities. JMC3B later supplies real log/artifact/progress capabilities through this
context; JMC3A may use no-op evidence sinks only in unit tests.

### 3.2 Delivery and admission

The sequence is fixed:

1. parse the exact JMC1 transport envelope;
2. load the canonical job, dispatch, and JMC2 definition;
3. no-op or reject terminal, stale generation/version, cancelled, paused, superseded,
   disabled, or duplicate delivery;
4. compute safety requirements entirely from the stored definition/subject/request;
5. acquire the ordered safety-gate set while PgQueuer's delivery heartbeat remains active;
6. re-read and row-lock canonical job/dispatch state;
7. if intent/generation changed, release gates and record an admission disposition without
   creating an attempt;
8. atomically increment the fence, insert the attempt audit, make it current, and transition
   queued to running;
9. construct the execution context and run the definition through its allowed executor;
10. terminalize through fenced compare-and-set writes, close evidence, prove process death,
    release gates, and only then return to PgQueuer.

Simultaneous deliveries serialize on canonical admission. Serial redelivery after terminal
commit returns without an effect. Redelivery after worker loss advances the fence and marks
the prior audit interrupted only after its process identity has been reconciled.

### 3.3 Fenced writer

Create one canonical writer used by delivery, cancellation, progress, result, error, and
publish coordination. Its update predicate includes:

- canonical job ID;
- current attempt ID;
- current fence token;
- dispatch generation;
- expected phase/desired-state constraints.

It returns an explicit applied/stale/conflict result. Callers never treat a zero-row update
as a successful state change. Terminal writes validate JMC2 result/error models and commit
attempt outcome, job outcome, dispatch disposition, and semantic event atomically.

The fenced writer does not hold a SQL transaction while a handler, process, lock wait, file
copy, validation, or network call runs.

## 4. Advisory safety gates

### 4.1 Key construction and requirements

- Domain-separate every key kind before hashing to a signed PostgreSQL `bigint`.
- Media-file exclusion uses the stable canonical media safety identity selected by the
  definition, not a basename, transient inode, client path, or PgQueuer ID.
- Media-write and GPU permits are numbered keys derived from the snapshotted configured
  capacity. Capacity cannot change under an admitted attempt.
- All ordinary execution holds the one shared maintenance key for its physical lifetime.
- Exclusive maintenance holds the exclusive form of that same key and requests no other
  safety gate.

Definitions declare requirements; the lock service validates and sorts them again. Unknown,
duplicate, contradictory, or client-supplied requirements fail closed.

### 4.2 Acquisition behavior

Use a dedicated direct asyncpg connection with a role-specific application name. Acquisition
is asynchronous and cancellation-aware. It must:

- publish a friendly durable wait reason without creating an attempt;
- observe committed `desired_state`, PgQueuer cancellation, worker shutdown, and configured
  admission deadline;
- avoid a circular wait by following the fixed global order;
- release any partial set on cancellation/error;
- retain the complete acquired set until processes are dead and the attempt is sealed;
- rely on connection close as the final fail-safe release while explicitly unlocking during
  normal completion for diagnostics.

Prove fairness/latency under contention and include these long-lived connections in the JMC1
role connection budget. Never expose exact advisory keys as a product contract.

## 5. Process containment and cancellation

### 5.1 Tracked launcher

The one launcher accepts a server-built executable identifier and argument list. It:

- resolves the executable from an allowlisted tool catalog;
- rejects shell strings, NULs, unbounded arguments, secret-bearing arguments without an
  explicit redaction rule, and unconfined working directories;
- constructs a minimal environment from an allowlist plus definition-owned additions;
- sets stdin to closed/null unless a bounded adapter explicitly needs it;
- starts a new session/process group using Python's supported subprocess arguments rather
  than unsafe `preexec_fn` callbacks;
- drains stdout/stderr concurrently from launch to exit even after evidence truncation;
- records durable identity before reporting the handler as started;
- returns exit code/signal and a bounded structured execution summary.

See [Python subprocess management](https://docs.python.org/3/library/subprocess.html).

### 5.2 cgroup capability tiers

When a writable delegated cgroup v2 subtree exists, create one attempt cgroup, place the
runner in it before meaningful work, let descendants inherit membership, use `cgroup.kill`
for final escalation, and require `cgroup.events populated=0` before death confirmation and
cleanup. See [Linux cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html).

Without delegation, use the mandatory process group plus verified `/proc` identities. Report
the containment tier through worker capabilities/readiness. Do not weaken a definition's
declared minimum at runtime. Unsupported non-Linux production execution fails closed for
process-requiring definitions; pure `system_noop` remains available.

### 5.3 Cancellation and orphan reconciliation

The coordinator watches the PgQueuer cancellation scope and durable canonical intent. On
cancellation:

1. persist `stopping` through the current fence;
2. signal the cooperative token/control channel;
3. wait the definition's cooperative grace;
4. send TERM to the verified cgroup/process group;
5. wait the TERM grace;
6. send KILL;
7. verify cgroup emptiness or every known process identity is dead;
8. discard/quarantine staging, seal the attempt, terminalize cancellation, then release
   safety gates.

Worker startup scans unfinished attempts owned by the same node/boot history and stale
attempt workspaces. Before signaling it verifies host boot ID, kernel process-start identity,
and cgroup membership. PID reuse, partial identity, inaccessible `/proc`, or an unexpected
cgroup produces quarantine and an Operations alert, not a speculative kill.

## 6. Filesystem boundary and staged publication

### 6.1 Root and key model

Define configured root classes for media, application data, logs, artifacts, cache, staging,
backup, and temporary work. A root records its purpose, read/write policy, symlink policy,
required/optional state, and same-filesystem requirements.

A confined key is a normalized relative POSIX key. Reject:

- absolute paths, drive/UNC forms, empty/dot/dot-dot components, NUL and backslash ambiguity;
- sibling-prefix and case-normalization escapes;
- symlink traversal where the operation disallows it;
- missing/unresolved required roots;
- paths received from a database, integration, archive, payload, or tool that have not been
  reclassified immediately before use.

Keep Radarr/Sonarr remote-to-local prefix translation separate. A translated path is still
untrusted until classified under a configured local media root.

### 6.2 Central operations

Expose narrow operations for read/serve, create, temporary file/directory, copy, atomic
replace, delete, directory cleanup, archive creation/extraction, and storage-key conversion.
Use descriptor-relative/no-follow primitives where supported so validation and use bind to
the same directory. Response code serves an already validated descriptor/stream rather than
reopening a caller/database path after validation.

Remove the raw poster unlink fallback. Replace direct mutation from route modules and all
security-sensitive `str(path).startswith(str(root))` checks. A static inventory allowlists
only internal tool-owned cleanup that cannot cross a classified root and documents why.

### 6.3 Workspaces and publish

- Evidence/log workspaces use confined `DATA_DIR` keys.
- Media replacement staging is created in the destination directory/filesystem with a name
  containing canonical job, attempt, and fence identity.
- The child/handler receives the staging descriptor/key but not authority to rename the
  destination.
- The coordinator validates output, source signature, desired state, current fence, file
  type/size/metadata, and definition-specific rules.
- Flush file data and the containing directory as required, then atomically replace and
  flush the destination directory.
- `EXDEV`, changed source/destination identity, stale fence, cancellation, or failed
  validation prevents publication. There is no copy fallback.
- Startup cleanup deletes only positively identified safe stale workspaces. Ambiguous or
  potentially published data is quarantined with bounded evidence for JMC3B.

JMC3A proves the coordinator protocol with fixed synthetic files only. Real media validators
and mutations remain Chunk 5.

## 7. Transport-intent consistency monitor

Add a low-frequency monitor that reads bounded canonical rows whose committed desired state
and current transport linkage disagree. Through the public gateway it may:

- retry cancellation of a known queued/picked ticket after an interrupted API command;
- clear a confirmed cancelled queued ticket and terminalize the canonical job;
- surface a missing/held/mismatched current ticket as attention/Operations state;
- reconcile a consumed paused delivery so later resume can create a new generation.

It may not enqueue ordinary work, create attempts, inspect raw PgQueuer tables, decide a
handler result, recover a PgQueuer heartbeat, or run on an unbounded scan. Use `FOR UPDATE
SKIP LOCKED` or equivalent bounded ownership only for its Marquee rows; it is not a queue.

## 8. Fixed canary design

Do not create a public product definition. Tests register fixed definitions in an isolated
registry, and an optional development-only CLI may enqueue a hard-coded read-only canary
only when the same explicit development guards as the reset tooling pass.

Canary behaviors are selected by a closed enum compiled into the application/tests:

- clean child exit;
- bounded stdout/stderr;
- cooperative wait;
- ignore cooperative/TERM until KILL;
- fixed staged-file creation and validation;
- fixed retry/failure.

No behavior accepts an executable, argument list, environment key/value, working directory,
destination, or raw path from HTTP or queue payload. Canary definitions are excluded from
production registry coverage and OpenAPI.

## 9. Implementation phases

Each phase ends with focused tests, the retained full pytest comparison,
`ruff check marquee tests`, applicable Alembic/reset/schema and generated-contract checks,
affected frontend gates, `git diff --check`, one short lowercase commit, and a shared-timeline
update.

### Phase A0 — verify JMC2C and freeze safety contracts

- Perform all preconditions and baselines.
- Freeze current delivery/cancellation/transport and path/process inventories.
- Verify authoritative platform behavior from installed/runtime sources.
- Add regression fixtures before shared delivery/path code changes.

### Phase A1 — filesystem boundary and unsafe-path removal

- Implement roots, confined keys, descriptor-safe operations, and archive policy.
- Migrate security-sensitive serving/mutation and remove the raw poster fallback.
- Add static/path-race/traversal fixtures and update generated contracts only if unavoidable.

### Phase A2 — advisory safety gates and maintenance barrier

- Implement key derivation, ordered requirements, dedicated connections, wait state, and
  cancellation-aware acquisition/release.
- Integrate connection/readiness/capability diagnostics without exposing raw lock keys.
- Prove same-file, permit, maintenance, loss, and ordering behavior.

### Phase A3 — fenced admission and canonical writer

- Generalize delivery through the JMC2 registry.
- Implement pre-admission rechecks, atomic attempt/fence creation, and fenced state writers.
- Prove duplicate, stale, retry, pause, cancel, and terminal-before-ack behavior.

### Phase A4 — process containment, cancellation, and orphan reconciliation

- Implement the tracked launcher, durable identity, process groups, optional cgroup v2, and
  worker capability reporting.
- Add cooperative/TERM/KILL escalation and death confirmation.
- Add identity-safe startup reconciliation and fixed process canaries.

### Phase A5 — staging, atomic publish, consistency monitor, and certification

- Implement attempt workspaces, validation/publish coordinator, cleanup/quarantine, and the
  bounded transport-intent monitor.
- Run the complete JMC3A failure matrix and manual read-only canary smoke.
- Hand off exact execution/evidence extension points to JMC3B.

## 10. Acceptance matrix

JMC3A is incomplete until automated evidence proves:

- only `system_noop` is production-enabled and fixed canaries cannot accept arbitrary code;
- safety wait creates no attempt and cannot consume domain attempts;
- simultaneous and serial duplicate delivery admit at most one current effect;
- every post-admission canonical write rejects stale attempt/fence/generation ownership;
- success is committed before PgQueuer acknowledgement and redelivery after commit no-ops;
- advisory keys/order are deterministic and contention cannot deadlock;
- file, media-write, GPU, and shared/exclusive maintenance exclusion work across processes;
- connection loss releases locks but never authorizes stale publication;
- blocking processes do not block the API/event loop;
- stdout/stderr are always drained and fixed high-output children cannot deadlock;
- process identity rejects PID reuse and boot mismatch;
- cancellation proves process-tree death before terminal state/gate release;
- cgroup-supported and process-group fallback paths report honest capability;
- orphan reconciliation never signals an unrelated process;
- path traversal, sibling-prefix, poisoned stored paths, symlinks, path swaps, arbitrary
  serving, raw deletion, and archive escapes fail closed;
- crash or cancellation at create/write/validate/fsync/replace/commit boundaries never
  publishes duplicate or unvalidated output;
- stale staging is deleted or quarantined deterministically;
- the consistency monitor uses only bounded canonical rows and PgQueuer's public gateway;
- retained test failures do not grow and all applicable static/schema/frontend gates pass.

## 11. Out of scope

- production enablement of any real read-only or mutating feature handler;
- physical attempt logs, artifact registry implementation, progress persistence, tool
  progress parsing, and multiplexed SSE (JMC3B);
- coordinated backup/restore and streaming request limits (JMC3C);
- real media staging validators and mutation fixtures (Chunk 5);
- Projection Room/shared frontend progress rendering (Chunk 6);
- authentication, webhooks, public reset replacement, Docker hardening, or CI modernization;
- a queue/resource/lease table or second recovery scheduler.

## 12. Operator handoff

The shared timeline must identify final hashes, exact retained failures, schema/fingerprint
changes, lock key/version rules, role connection budget, containment capability by tested
environment, performed process-kill/path/publish smokes, quarantined fixtures, inherited
operator work, and the exact JMC3B starting point.
