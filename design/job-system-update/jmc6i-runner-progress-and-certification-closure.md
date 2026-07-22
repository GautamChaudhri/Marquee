# JMC6I — Runner, Progress, and Certification Closure

> **Implementer:** Read this document in full before changing code. Decisions are locked.
> Execute every internal phase continuously; phase boundaries are verification checkpoints, not
> stopping points. Stop only for a condition in
> [`design/plans/README.md`](../plans/README.md#implementer-continuity-and-stop-conditions)
> or after the complete plan, certification, and final-only history compaction succeed.

**Program:** Two-part post-JMC6H readiness closure

**Predecessor:** [JMC6H product convergence and final certification](jmc6h-product-convergence-retirement-and-final-certification.md)

**Successor:** [JMC6J taste onboarding and residual preference learning](jmc6j-taste-onboarding-and-residual-learning.md)

**Shared timeline:** `design/job-system-update/jmc6-runtime-and-personalization-timeline.md`

**Recommended model tier:** **God**

## 1. Objective

Close the remaining shared execution defects found by the independent post-JMC6H source audit:

- cancellation or timeout of the coroutine hosting a fixed internal runner must still terminate
  and confirm the death of the owned process tree;
- poster and non-head ML runner measurements must reach Marquee's typed durable progress model
  instead of collapsing to stage-start labels;
- behavioral closure claims must execute the declared behavior rather than infer it from manifest
  fields, source strings, or the existence of a test file.

This plan deliberately does **not** redesign onboarding, taste readiness, feedback semantics,
learned-head training, learned-head progress, learned scoring, or personalization activation. Those
are one coherent product change owned exclusively by JMC6J. JMC6I may harden a shared primitive
that those features also use, but it may not change their product behavior, API, data, thresholds,
training inputs, scorer selection, or publication contract.

## 2. Revalidated current-state findings

The planning recheck at `jmc6h-complete` confirmed these exact seams:

1. `run_internal_operation()` decides whether to cancel its child from local `cancelled`,
   `timed_out`, and `protocol_error` flags. An outer `Task.cancel()` or `asyncio.timeout()` can enter
   `finally` with all three flags false and call `tracked.wait()` indefinitely instead of
   terminating the process tree.
2. The current internal-runner tests prove the helper's own timeout and cooperative cancellation,
   but not cancellation of the hosting task, the delivery timeout that surrounds a handler, or
   worker shutdown during a runner frame/progress callback.
3. The poster runner emits `stage`, `state`, `done`, `total`, and `survivors`, but its host adapter
   forwards only a recognized stage whose state is `start`.
4. Taste-profile and taste-map runners discard their trainers' numerical progress fields;
   enrichment emits only one raw stage; their job handlers do not attach progress callbacks.
5. `ExecutionProgress.stage()` currently mirrors one completed/total pair into both overall and
   current scopes. It cannot honestly express stable batch/request progress plus a resettable
   current stage/subject measurement.
6. JMC6G/H closure tests validate registry/manifest fields, source restrictions, stage vocabularies,
   and named certification files. They do not universally execute the manifest's declared producer
   through delivery and assert the declared downstream consumer.
7. The current onboarding and learned-head findings are real but separable: their files and
   behavior are reserved to JMC6J and are not an excuse to weaken JMC6I's runner/progress gates.

If the implementation base has moved beyond `jmc6h-complete`, re-run this inventory and record
drift in the shared timeline before editing. Code is authoritative when a line anchor changes.

## 3. Preconditions and stop gates

Before edits:

1. Verify annotated `jmc6h-complete`, its compact commit/tree/base, the JMC6 final-closure timeline,
   recovery refs/bundle, schema/OpenAPI fingerprints, zero-green backend/frontend gates, and listed
   live-smoke exceptions.
2. Record the exact plan base, branch/upstream, configured Git author, worktree state, full backend,
   Ruff, Alembic/model, generated-contract, frontend unit/check/lint/build/Playwright baselines, and
   current opt-in capability results in the new shared timeline. The JMC6I implementer creates it.
3. Freeze the `ProcessLauncher`/`TrackedProcess`, delivery timeout, cancellation monitor, runner
   protocol, `ExecutionProgress`, durable writer, poster pipeline, ML publication, batch projection,
   Activity snapshot, and closure-manifest contracts.
4. Add failing focused tests for outer task cancellation, outer timeout, progress-field loss, and
   a manifest declaration that passes static inspection but fails its actual consumer assertion.
5. Inventory all call sites of `run_internal_operation()` and classify them as poster, non-head ML,
   learned-head, or fixed test canary. Record which are behaviorally in scope.

Stop if owned-process death cannot be proven without releasing a safety gate, if cancellation-safe
cleanup requires swallowing the caller's cancellation, if progress cannot be fenced to the active
attempt, if an in-scope manifest entry has no deterministic owned fixture, or if the worktree gains
unrelated/concurrent commits. Do not stop merely because an internal phase completes successfully.

## 4. Strict ownership boundary

### 4.1 JMC6I owns

- `internal_runner_host` lifecycle, protocol draining, and cleanup semantics;
- shared tracked-process cancellation/death confirmation needed by the runner host;
- typed runner progress-frame validation and mapping;
- the ability to publish independent overall and current progress observations;
- poster-pipeline numerical progress and subject/stage freshness;
- taste-profile, taste-map, and enrichment progress plumbing without changing their inputs,
  algorithms, outputs, activation, or consumer behavior;
- executable closure evidence for the above and regression-proof manifest mechanics.

### 4.2 JMC6I must not change

- `marquee/api/routes/onboarding.py`, `marquee/onboarding/**`, onboarding frontend routes/components,
  onboarding settings, starter profiles, taste-test bundles, or onboarding thresholds;
- `marquee/pipeline/scorer.py`, learned-score selection, `marquee/ml/learned_head.py`,
  `marquee/ml/head_trainer.py`, learned-head evidence generation, activation thresholds,
  retraining policy, or head publication semantics;
- feedback meaning, training examples, profile exemplar policy, movie/TV personalization policy, or
  whether/when personalization activates;
- the public onboarding or learned-head API contract.

The shared runner host may become safer for every operation, including the existing learned-head
operation. That incidental safety improvement is permitted. Direct learned-head handler/progress/
trainer/scorer changes are not. JMC6I certification must list onboarding and learned-head behavior
as JMC6J-owned open scope rather than claiming they are fully closed.

## 5. Cancellation and death-confirmation contract

`run_internal_operation()` must have one cleanup path for every exit after launch:

- normal validated result;
- clean EOF without a result;
- malformed, oversized, or truncated protocol;
- explicit `should_stop` cancellation;
- helper-local deadline;
- surrounding delivery timeout;
- direct `Task.cancel()`;
- cancellation while awaiting a progress callback;
- unexpected callback/decoder/validation exception;
- worker shutdown or connection-loss propagation.

The implementation must distinguish outcome classification from cleanup necessity. Once a tracked
runner exists, any exit that has not already proven normal process termination initiates bounded
cooperative/TERM/KILL cancellation and death confirmation. Cleanup runs in a cancellation-resistant
section, preserves the original exception/cancellation for the caller, and does not convert an
external cancellation into success, failure, or a local timeout.

Required invariants:

- no safety gate, workspace, fence-owned publication, or attempt terminal state is released before
  the owned tree is confirmed dead;
- process-group/cgroup capability and `(boot ID, PID, process-start identity)` checks remain the
  authority—never signal an unverifiable or reused PID;
- cleanup is bounded at each escalation stage and cannot wait forever on `tracked.wait()`;
- if death cannot be confirmed, the attempt becomes unsafe/quarantined and the worker reports a
  hard operational failure; it must not acknowledge successful cancellation;
- reader/drainer/control tasks are cancelled and awaited exactly once; queues and file descriptors
  do not leak;
- a final result racing with outer cancellation cannot publish after the caller lost ownership;
- nested cancellation preserves `CancelledError` after cleanup; timeout preserves the delivery
  timeout classification selected by the execution kernel;
- normal success still waits for exit and validates exit status, files, final fence, and protocol.

There must be one definition-owned timeout authority at delivery. A runner-local deadline may be
used only as a lower-level bounded safety mechanism with a documented relationship to that
deadline; handlers must not silently create a second contradictory timeout policy.

## 6. Typed runner-progress bridge

Add a bounded versioned progress-frame model. It accepts only allowlisted fields and finite bounded
values, rejects negative totals/impossible counts, and never lets runner strings become primary UI
copy. At minimum it carries:

- protocol version and operation;
- stable semantic stage key and event state;
- optional current scope ID and subject discriminator;
- completed, total, unit, survivors, and bounded message/metric fields;
- optional bytes, throughput, speed, FPS, and credible ETA inputs where the definition permits;
- a monotonic runner-frame cursor used only within the active attempt.

Extend the execution progress facade with an observation API that can express:

- a stable monotonic `overall` scope independent of current work;
- a `current` scope that may reset only when its scope ID changes;
- current subject/stage without changing the immutable job subject;
- determinate, indeterminate, or none modes independently per scope;
- maximum-staleness/liveness updates without inventing a percentage.

The server remains the sole percentage authority. The adapter must map runner-native units through
the registered definition's progress policy and stage vocabulary. Unknown stages/units, decreasing
same-scope values, out-of-order cursors, stale fences, and unbounded fields are rejected or recorded
as operational degradation; clients never infer progress from arbitrary runner JSON.

### 6.1 Poster pipeline

- Preserve the immutable movie/series/season subject from the job snapshot.
- Translate real download/gate/feature/OCR/dedup/rank/archive events into plain-language registered
  stages.
- Forward `done`/`total` for the current stage and survivor/rejection counts as bounded metrics.
- Keep overall single-subject progress indeterminate unless the pipeline has a defensible stable
  stage denominator. For a batch, the canonical sealed parent remains the only overall child-count
  authority; a child runner must not reset or overwrite parent completion.
- A new stage/current scope may reset; the same scope may not regress.
- Do not change ranking, recommendation, taste-profile, or learned-head behavior in this plan.

### 6.2 Non-head ML work

- Taste rebuild forwards trainer stage, substage, processed, total, current item, and liveness where
  supported, with bounded sanitized labels.
- Taste map forwards real projection/embedding item counts where the builder exposes a denominator;
  otherwise it uses named indeterminate phases.
- Enrichment exposes item/page counts only when the source supplies a trustworthy total; otherwise
  it remains indeterminate with liveness.
- Handlers attach the shared adapter and preserve current publication/activation semantics.
- Learned-head progress and behavior are explicitly left unchanged for JMC6J.

Progress callback failure must be observable in Operations/evidence but cannot fail the underlying
poster or ML operation. High-frequency frames are coalesced through the existing fenced durable
writer while stage, warning, subject, terminal, and maximum-staleness observations remain durable.

## 7. Executable behavioral certification

Replace closure-by-declaration with a registry-driven executable certification matrix for every
in-scope definition. The checked-in manifest remains useful inventory, but each behavioral claim
must reference a concrete collected test node or typed scenario callable—not a filename or source
substring—and the certification runner must prove that the node actually ran in the current test
session.

For poster pipeline, taste rebuild, taste map, enrichment, and fixed runner canaries, prove:

```text
canonical producer → PgQueuer delivery/execution context → fixed runner
→ real progress/evidence/result → terminal state → declared API/presentation consumer
```

Each scenario asserts success or truthful no-change, failure, cancellation, timeout, duplicate or
stale delivery, logs/artifacts, progress semantics, and refreshed consumer output as applicable.
Negative fixtures must demonstrate that a handler returning schema-valid placeholder output, a
nonexistent test-node reference, a skipped/xfail node, a stale manifest entry, or a consumer that
never observes the effect fails certification.

Do not overstate the result. JMC6I closes its owned runner/progress definitions and strengthens the
general certification mechanism. The final all-product closure is JMC6J's responsibility after
onboarding and residual learning are replaced and certified.

## 8. Implementation phases

Every phase ends with focused tests, the complete zero-green backend suite, `ruff check marquee
tests scripts`, Alembic/model checks where affected, deterministic OpenAPI/type generation where
affected, frontend unit/check/lint/build/Playwright where affected, `git diff --check`, one short
lowercase phase commit, and a shared-timeline update. Continue immediately after a successful
checkpoint.

### Phase I0 — verify JMC6H and freeze plan boundaries

- Complete §3 and create the shared timeline.
- Record exact in-scope/out-of-scope call sites and hashes for reserved onboarding/head files.
- Add the failing outer-cancellation, progress-loss, and false-certification tests.
- Freeze public contracts; no visual redesign or personalization behavior change is allowed.

### Phase I1 — make runner cleanup cancellation-safe

- Implement the single cleanup state machine from §5.
- Cover outer `Task.cancel()`, delivery `asyncio.timeout`, callback cancellation/error, worker
  shutdown, EOF/result races, ignored TERM, and death-confirmation failure.
- Prove no child, drainer task, descriptor, safety gate, workspace, or publish opportunity survives.

### Phase I2 — add independent typed progress scopes

- Add the bounded runner progress model and shared mapping service.
- Extend the execution facade without weakening fenced writer invariants.
- Certify monotonic overall values, legitimate current-scope reset, stale/out-of-order rejection,
  coalescing, maximum staleness, and publication-degradation isolation.

### Phase I3 — connect poster and non-head ML progress

- Map poster events and real counts through the registered vocabulary.
- Map taste rebuild, map, and enrichment progress without changing their algorithms or activation.
- Prove Activity/detail/initiating-page snapshots retain the same job and meaningful progress after
  refresh, navigation, SSE reconnect, API restart, and terminal failure.
- Verify reserved onboarding/head files and behavior remain unchanged from I0.

### Phase I4 — replace declarative closure with executed proof

- Implement §7's typed scenario/test-node collection and negative controls.
- Convert in-scope manifest entries and assertions.
- Add a report distinguishing executed, unavailable live-capability, deferred JMC6J, and failed
  evidence; an unavailable capability may not be reported as passed.

### Phase I5 — full regression and readiness certification

- Run all focused and repository-wide gates, runner/process kill smokes, progress refresh journeys,
  bounded resource checks, and independent static reachability scan.
- Compare reserved onboarding/head file hashes and behavior to I0; revert any accidental drift.
- Fix every in-scope finding within I5 and rerun affected plus complete gates.
- Close the JMC6I timeline entry and perform §10 only when no I-owned finding remains.

## 9. Final acceptance

JMC6I passes only when:

- every exit after runner launch either proves normal termination or performs bounded cancellation
  and proves owned-tree death;
- outer task cancellation and delivery timeout cannot fall into an unbounded normal wait;
- original cancellation/timeout semantics reach the execution kernel without false success;
- poster and non-head ML numerical progress reaches durable typed snapshots and consumer views;
- overall/current scopes obey monotonicity and reset invariants without fabricated percentages;
- refresh/SSE interruption retains the canonical active card and its last durable evidence;
- executable certification fails on false, stale, skipped, uncollected, or consumerless claims;
- onboarding and learned-head product files/behavior remain unchanged and are explicitly assigned
  to JMC6J;
- fresh schema checks, complete backend, Ruff, generated contracts, frontend unit/check/lint/build/
  Playwright, `git diff --check`, and required owned process smokes are green with zero failure,
  skip, or `xfail`.

## 10. Mandatory final-only history compaction

Perform only after I5 is completely certified:

1. Verify a clean, linear, configured-author, JMC6I-only, unpushed range after the exact recorded
   plan base. Stop for unrelated/concurrent commits, merges, uncertain ownership, or pushed phase
   history.
2. Commit the final pre-squash timeline entry with phase hashes, gates, deviations, operator work,
   pre-squash tip, certified tree, and intended tag `jmc6i-complete`.
3. Create a timestamped recovery branch/tag and verified repository-external Git bundle. Preserve
   all earlier recovery refs and bundles.
4. Record the certified tree hash; soft-reset through RTK to the recorded plan base and create one
   configured-author commit: `jmc6i: close runner progress and certification`.
5. Prove exact tree identity, sole parent, clean worktree, recovery refs, and verified bundle.
6. Create annotated local tag `jmc6i-complete`; do not edit the timeline afterward.
7. Do not push, force-push, activate schedules, or add agent/model/generator attribution.

## 11. Handoff to JMC6J

Report the compact tag/hash/tree/base, recovery refs/bundle, exact changed contracts, process-death
evidence, progress matrices, executable certification report, full gates, opt-in/manual smokes, and
the unchanged hashes/status of reserved onboarding/head files. JMC6J begins only after verifying
that handoff and must append to the existing shared timeline rather than recreating it.
