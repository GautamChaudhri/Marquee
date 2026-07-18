# JMC6G — Execution Truth, Progress, and Evidence Closure

> **Implementer:** Read this document in full before changing code. Decisions are locked.
> Execute every internal phase continuously; phase boundaries are verification checkpoints, not
> stopping points. Stop only for a condition in
> [`design/plans/README.md`](../plans/README.md#implementer-continuity-and-stop-conditions)
> or after the complete plan, certification, and final-only history compaction succeed.

**Program:** Final two-part closure of the clean-slate PgQueuer job system

**Predecessor:** [JMC6F legacy retirement and activation certification](jmc6f-legacy-retirement-and-activation-certification.md)

**Successor:** [JMC6H product convergence and final certification](jmc6h-product-convergence-retirement-and-final-certification.md)

**Shared timeline:** `design/job-system-update/jmc6-final-closure-timeline.md`

**Recommended model tier:** **God**

## 1. Objective

Correct the shared execution defects that can make an otherwise valid handler report the wrong
outcome, retry forever or not at all, run without a timeout, lose progress, block its worker, or
leave incomplete evidence. JMC6G establishes one behaviorally tested truth for:

- semantic job outcome, attempt outcome, dispatch disposition, transport acknowledgement, log
  summary, and workspace disposition;
- bounded retry classification and execution timeout;
- enqueue-time execution configuration;
- subject-aware progress for every enabled long-running definition;
- tracked subprocesses and cancellation-aware large-file I/O;
- subtitle/media projections, artifacts, retention, and Operations diagnostics.

This is not a schema-only or direct-handler exercise. Every terminal and retry branch is tested
through canonical submission, PgQueuer-style delivery, fenced persistence, durable evidence, batch
aggregation where applicable, and the bounded API/presenter projection.

JMC6G does not repair the intentionally incomplete poster and ML implementations. Those remain
enabled only for regression inventory during this plan and are rebuilt in JMC6H. JMC6G must still
make their shared outcome/retry/progress behavior correct.

## 2. Preconditions and stop gates

Before edits:

1. Verify annotated `jmc6f-complete`, its compact commit/tree, recovery material, clean ancestry,
   current migration head, generated contracts, definition manifest, zero-green test baseline, and
   the complete JMC6D–F shared timeline.
2. Create `design/job-system-update/jmc6-final-closure-timeline.md` before the first implementation
   commit. Record the exact plan base, repository author, branch/upstream state, tool versions,
   disposable PostgreSQL target, DATA_DIR fixture root, and all baseline gates.
3. Reproduce the audit findings before changing behavior: non-mutation results forced to success,
   mutation no-change/partial/unsafe terminal mismatch, unused retry classifier, missing handler
   timeout, missing configuration declarations, long-running handlers without progress, untracked
   process/file work, uncalled retention, stale subtitle projections, and the three bounded
   diagnostics defects.
4. Generate an enabled-definition closure manifest containing job type, result model and allowed
   outcomes, retry/timeout policy, configuration keys, progress strategy/stages, external tools,
   large-file operations, product projection, artifacts, presenter, and tests. An omitted enabled
   definition is a stop gate.
5. Inspect the installed PgQueuer 1.1.1 callback, `RetryRequested`, hold, cancellation, and
   acknowledgement contracts. Installed source and current code beat historical documentation.
6. Use an owned disposable PostgreSQL server/schema, disposable DATA_DIR, owned subprocesses, and
   generated/synthetic media. Never signal or reset an operator process/database.

Stop if PgQueuer cannot preserve retry/hold semantics after bounded policy enforcement, if a
terminal transition would acknowledge before canonical durability, if cancellation cannot prove
owned-process death, if a source-changing path cannot be fenced and staged, or if an edit would
silently change the real poster/ML product semantics assigned to JMC6H.

## 3. Locked decisions

| ID | Decision |
|---|---|
| G01 | `Job.outcome` is the product/domain result. `JobAttempt.outcome` describes execution of one attempt. `JobDispatch.disposition` describes transport/canonical handling. They are separate vocabularies and are never populated by copying one raw string into all three. |
| G02 | Add one typed, definition-owned terminal decision contract for every result document. It maps a validated result to job outcome, attention/remediation, attempt outcome, dispatch disposition, acknowledgement safety, log summary, and workspace cleanup/quarantine. No `isinstance(MutationResultV1)` special case or generic forced-success fallback remains. |
| G03 | A handler that returns a valid domain `failed`, `partially_succeeded`, `no_change`, `superseded`, or `unsafe` result completed its execution attempt. The canonical job retains that semantic outcome. Attempt/dispatch evidence uses its own valid values. A returned terminal result is acknowledged only after its canonical terminal write succeeds. |
| G04 | Kernel uncertainty is distinct from a returned domain `unsafe` result. Unproven process death, ambiguous publication, stale ownership, invalid terminal mapping, or terminal persistence failure is quarantined/held and must not be acknowledged as a normal completion. |
| G05 | Finalization order is: stop/prove descendants dead; validate result and derive terminal decision; apply fenced canonical terminal state/event; seal the log with both semantic and attempt outcomes; register/repair terminal evidence; then clean or quarantine the workspace and return to PgQueuer. A pre-terminal log never claims success. |
| G06 | Failure to seal/register non-authoritative evidence after a durable terminal transition does not rerun media work. It records an operational degradation and is repaired by a bounded evidence reconciler. Failure before the terminal transition follows the policy/hold matrix. |
| G07 | Every result model has a closed, versioned outcome enum or definition mapper. Free-form outcome strings and undocumented presenter inference are rejected. Parent aggregation consumes canonical child outcomes only. |
| G08 | Retry classification is enforced by the delivery kernel. A definition owns the classifier and bounded delay sequence; handlers provide typed failure evidence, not an unlimited delay loop. An explicit retry request is a hint constrained by remaining attempts and policy. |
| G09 | Attempt number is derived from Marquee's durable audit under the current canonical job. Operator retry remains a new canonical successor and does not reset automatic retry evidence on the original. |
| G10 | Exhausted transient failures become permanent failed terminal work with remediation. Cancellation remains cancellation. Unsafe uncertainty remains held/quarantined. No enabled definition can retry indefinitely. |
| G11 | The definition timeout wraps handler execution, not only safety-gate admission. Timeout triggers cooperative cancellation, TERM/KILL escalation, death confirmation, workspace disposition, and policy classification before PgQueuer sees retry/hold/failure. Admission timeout remains separately configured. |
| G12 | Timeout never fabricates a completed result. Unsafe mutations that may have crossed publication intent are reconciled or quarantined rather than automatically retried. |
| G13 | Every enabled definition explicitly declares execution-relevant configuration keys or an audited empty set. The configuration snapshot is bounded, secret-free, immutable for the canonical job, and used by every attempt. |
| G14 | Job handlers do not read mutable global database-owned settings. Static guards and runtime tests enforce use of `ExecutionContext.configuration` or typed values intentionally frozen in the request. Restart-owned capability/model paths are recorded as sanitized version/capability evidence, not secrets or arbitrary paths. |
| G15 | Every enabled long-running definition uses the JMC3 progress writer. Stable stage vocabularies, current subject, overall/current scope, wait reason, maximum staleness, and honest measurement mode come from the definition policy. |
| G16 | FFmpeg and mkvmerge progress is parsed while the process runs. Provider waits, rescan/validation, staging, backup, copy, checksum, and publication emit named semantic progress. No handler parses native progress only after process exit. |
| G17 | Percent and ETA require a validated denominator/rate. Indeterminate work remains visibly active. Terminal failure/cancellation preserves the last measurement; success/no-change receives a coherent final presentation. |
| G18 | All product subprocesses, including probes and `pg_dump` used by canonical backup work, run through the tracked launcher or a later fixed internal-runner extension. No enabled handler uses `subprocess.run`, raw `Popen`, or an unowned child. |
| G19 | Add one cancellation-aware execution-I/O service for chunked copy, checksum, fsync, and backup work. It yields between bounded chunks, checks cancellation/fence, reports bytes, and never performs multi-gigabyte copying/hashing synchronously on the worker event loop. |
| G20 | Source-changing publication remains coordinator-only, destination-local, fenced, validated, fsynced, and atomic. Making I/O asynchronous does not weaken path confinement, no-follow behavior, or publication preconditions. |
| G21 | Audio/subtitle mutations persist the actual post-operation inventory used by the UI in the same fenced domain completion. Generated/extracted managed sidecars receive media/job bindings and confined downloadable artifacts. |
| G22 | If generation succeeds but requested embedding fails, the result is partial/failed as defined by the request contract, never unconditional success. Every requested target has a typed final outcome and stage/reason. |
| G23 | Media, poster, letterbox, and HDR backup evidence that users may need is registered through `JobArtifact` with retention and subject linkage. A raw storage key in result JSON is not sufficient evidence. |
| G24 | Physical artifact/log expiration is a real idempotent maintenance workflow. It deletes confined files and updates metadata before eligible job-row retention; failures remain retryable evidence and never cause an orphaning DB purge. |
| G25 | Retention scheduling obeys the existing production-schedule master gate. A bounded operator command remains available when schedules are intentionally off, and Operations reports overdue retention truthfully. |
| G26 | Startup orphan reconciliation selects stale candidates before applying its bound; healthy recent rows cannot crowd stale work out. Listener health requires an actual fresh worker/listener role. Queue rank is computed correctly for the bounded active execution class or omitted; it never restarts at one per result page. |
| G27 | No raw PgQueuer row/ID, filesystem path, secret, command credential, or unbounded document becomes a product contract while implementing these fixes. |
| G28 | Browser acceptance testing, real poster/ML convergence, broad legacy deletion, deferred authentication/reset/Docker/webhook work, activation, push, and force-push are outside JMC6G. |

## 4. Terminal and retry behavior

Create one kernel-visible type similar in responsibility to:

```python
@dataclass(frozen=True)
class TerminalDecision:
    job_outcome: JobOutcome
    attempt_outcome: AttemptOutcome
    dispatch_disposition: DispatchDisposition
    acknowledge_transport: bool
    workspace: WorkspaceDisposition
    attention: AttentionSeverity
    summary: str
```

The exact name and location are implementation-owned. It must be returned by the definition/result
contract, not reconstructed by API presenters. Validate the complete Cartesian set of result types
and their allowed outcomes at registry startup. Unknown combinations fail closed before execution
is enabled.

The kernel exception path classifies a bounded error document into cancelled, transient,
permanent, or unsafe uncertainty. The definition policy chooses retry eligibility and its delay.
Handlers may attach a reason/classification, but cannot exceed `max_attempts`, choose arbitrary
delays, or bypass unsafe-mutation rules.

Timeout covers the handler and its owned descendants. On expiry, the kernel first prevents further
publication, then terminates the process tree, confirms death, records the attempt, and consults
the retry/safety policy. A timeout after publication intent invokes publication reconciliation
before any retry decision.

## 5. Progress, process, and I/O closure

Extend `ExecutionContext` with the only supported high-level services for enabled handlers:

- typed progress observation and wait-state updates;
- tracked external process execution and native-progress streaming;
- cancellation-aware confined copy/checksum/backup helpers;
- fenced domain projection and artifact registration.

Audit every enabled handler against that boundary. Small metadata reads may use bounded thread I/O;
large or unbounded file operations use chunked execution I/O. A test fixture with deliberately slow
chunks must prove that cancellation, heartbeat/event delivery, and another control job continue
while copy/hash work is active.

For remux and transformation work, native progress flows directly from the running subprocess into
the progress coalescer. Probe, provider, download, backup, rescan, validation, and publish phases
must remain visible even when no numerical denominator exists.

## 6. Evidence and retention closure

After audio/subtitle changes, persist an authoritative inventory revision with job/attempt/fence
provenance. Create/update managed-sidecar bindings without deleting historical job evidence when a
live media row retires. Register user-relevant generated, extracted, backup, validation, and
diagnostic files as confined artifacts.

Unify physical retention into a bounded service used by a canonical maintenance definition and an
operator CLI. Its order is:

1. claim eligible sealed log/artifact metadata in bounded batches;
2. delete or verify absence of the confined physical file;
3. mark/delete metadata idempotently;
4. only then allow canonical job retention when every required evidence row is resolved.

Retention never serves or deletes a caller-supplied path. Missing files become explicit evidence,
not silent success.

## 7. Implementation phases

Every phase ends with focused tests, the complete zero-green backend suite, `ruff check marquee
tests scripts`, Alembic/model checks where affected, deterministic OpenAPI/type generation where
affected, frontend unit/check/lint/build/E2E where affected, `git diff --check`, one short lowercase
phase commit, and a shared timeline update. After a successful phase, continue immediately.

### Phase G0 — verify JMC6F and freeze the closure manifest

- Complete §2 and create the shared timeline.
- Add failing behavioral tests that reproduce each audit defect through delivery rather than only
  invoking handlers directly.
- Freeze every enabled definition's result/outcome, retry/timeout, configuration, progress,
  process/I/O, projection, artifact, and consumer obligations.
- Freeze existing zero-green and generated-contract fingerprints before shared changes.

### Phase G1 — terminal truth and finalization ordering

- Implement the closed result/outcome and `TerminalDecision` contracts.
- Replace forced-success and mutation-specific mapping in `FencedWriter`.
- Correct delivery finalization, log sealing, terminal evidence repair, workspace disposition, and
  batch child aggregation.
- Test every allowed semantic outcome for every result family through a real disposable database,
  including a terminal-write failure after process completion.

### Phase G2 — bounded retry, timeout, and configuration authority

- Add definition-owned failure classifiers and enforce attempt budgets/delays in the kernel.
- Replace hard-coded/unbounded retry requests with typed classified failures.
- Apply execution timeout around handlers and certify process death/publication reconciliation.
- Complete every enabled definition's configuration-key inventory and remove mutable global reads
  from handlers.

### Phase G3 — progress, tracked processes, and cancellation-aware I/O

- Implement missing progress adapters/stages/current-subject updates for every enabled long job.
- Stream mkvmerge/FFmpeg progress while running and expose provider/copy/hash/publish waits.
- Route probes, canonical backup `pg_dump`, and remaining product children through tracked launch.
- Introduce chunked confined copy/hash/backup I/O and replace blocking large-file paths.
- Certify event-loop responsiveness, cancellation, stale-fence rejection, process kill, and
  progress freshness under deliberately slow I/O.

### Phase G4 — domain projections, artifacts, and retention

- Persist post-mutation audio/subtitle inventory and managed-sidecar bindings.
- Correct generation/embed partial outcomes and requested-target evidence.
- Register sidecars, media backups, poster/HDR/letterbox backups, and validation evidence through
  the artifact service.
- Wire idempotent physical log/artifact cleanup before job-row retention, plus schedule/CLI and
  Operations overdue state.

### Phase G5 — bounded recovery and diagnostics corrections

- Correct stale orphan candidate selection, real listener health, and execution-class queue rank.
- Regenerate bounded APIs/types and update affected presentation fixtures.
- Run multi-page, large-history, worker/scheduler topology, retention outage, and repair tests.

### Phase G6 — integrated execution/evidence certification and compaction

- Run §8 across the entire enabled manifest, including poster/ML only for shared kernel behavior.
- Prove no added failure, skip, `xfail`, warning, contract drift, schema drift, blocking I/O path,
  untracked child, unbounded retry, missing timeout, or unclassified terminal outcome.
- Record any genuine live-tool smoke separately from deterministic fixtures.
- Update the timeline completely, then perform §9 only after all gates pass.

## 8. Acceptance and verification

For every enabled result/outcome pair, prove:

- canonical job outcome, attempt outcome, dispatch disposition, log summary, event, presentation,
  batch parent result, transport acknowledgement, and workspace disposition agree;
- `no_change`, partial, failed, superseded, cancelled, dead-letter, and unsafe states are never
  silently converted to success or written into an incompatible vocabulary;
- a terminal persistence conflict never leaves a success log or acknowledges nonterminal work;
- transient retry uses exactly the registered attempt budget and delays; exhausted work terminates;
- timeout kills descendants and cannot republish from a stale fence;
- the persisted configuration snapshot contains exactly the declared execution values and no
  secret, while a later configuration revision cannot change a retry;
- progress has correct subject/stage, monotonic overall scope, legitimate current-scope reset,
  bounded staleness, and honest indeterminate fallback;
- cancellation during provider wait, probe, remux, copy, checksum, backup, validation, or publish
  leaves truthful terminal/evidence state and a responsive worker;
- post-mutation track inventory, sidecar binding, requested-target result, artifact, and UI/API
  projection agree after refresh and live-subject retirement;
- log/artifact retention deletes physical evidence safely before metadata/job retention and repairs
  partial failure idempotently;
- orphan recovery, listener health, and queue rank remain correct beyond the first bounded page.

Run the full backend zero-green suite, Ruff, Alembic upgrade/fresh-target/model equivalence,
PgQueuer verify, deterministic OpenAPI/TypeScript drift, frontend unit/check/lint/build/Playwright,
and `git diff --check`. The retained set remains zero failures, skips, and `xfail`s.

## 9. Mandatory final-only history compaction

Perform only after G6 is completely certified:

1. Verify a clean, linear, configured-author, JMC6G-only, unpushed range after exact
   `jmc6f-complete`. Stop for unrelated/concurrent commits, merges, uncertain ownership, or any
   plan-owned commit already pushed.
2. Commit the final pre-squash timeline entry with all phase hashes, gates, deviations, operator
   work, pre-squash tip, certified tree, and intended tag `jmc6g-complete`.
3. Create timestamped recovery branch/tag and a verified repository-external Git bundle. Never
   rely only on `/tmp` and never delete earlier recovery material.
4. Record the certified tree hash; soft-reset through RTK to the exact plan base and create one
   configured-author commit: `jmc6g: close execution truth and evidence`.
5. Prove exact tree identity, sole parent, clean worktree, recovery refs, and verified bundle.
6. Create annotated local tag `jmc6g-complete`; do not edit the timeline afterward.
7. Do not push, force-push, activate, or add agent/model/generator attribution.

## 10. Handoff to JMC6H

The handoff reports the compact tag/hash/tree/base and recovery bundle; schema and generated
contract fingerprints; complete definition closure manifest; terminal mapping table; retry/timeout
matrix; configuration snapshot inventory; process/I/O audit; progress coverage; evidence/retention
behavior; test and live-smoke results; deferred operator actions; and exact JMC6H starting state.

JMC6H must independently verify these claims against Git, current source, the disposable database,
and representative fixtures. JMC6G completion does not authorize activation or browser acceptance.
