# JMC4C — Poster, ML, and Non-Mutating Certification

**Program:** [clean-slate PgQueuer migration](job-system-pgqueuer-migration.md)  
**Architecture:** [direct PgQueuer adoption](job-system-pgqueuer-direct-adoption.md)  
**Progress contract:** [job progress and loading experience](job-progress-and-loading-experience.md)  
**Activity contract:** [Projection Room job experience](projection-room-job-experience-redesign.md)  
**Previous plan:** [JMC4B library, scans, and media analysis](jmc4b-library-scans-and-media-analysis.md)

> **For the implementing agent:** Read `AGENTS.md`, `CLAUDE.md` when present,
> `design/plans/README.md`, `design/plans/04-television-backend.md` §0, all JMC1–JMC3
> plans/timelines, JMC4A, JMC4B, the complete shared JMC4 timeline, and this document
> **in full** before changing code. Decisions below are final. **Verify in code** means
> inspect every current symbol and reference before editing; legacy poster/run-manager state
> is an inventory source, not a lifecycle to preserve.
>
> **Shared JMC4 timeline:**
> `design/job-system-update/jmc4-nonmutating-jobs-timeline.md`. Verify
> `jmc4b-complete` resolves to the compact JMC4B commit and reconcile its tree, handoff,
> schemas, enabled manifest, schedules, and retained test baseline with Git. Append to this
> same timeline after every phase commit. Never create another JMC4 timeline.
>
> **Git authorship:** use only the repository's configured Git user. Never add yourself, a
> model, or an assistant as author, co-author, contributor, or generator. No
> `Co-Authored-By`, “Generated with,” model-name, or assistant-name attribution is allowed.

**Goal:** Migrate poster analysis, taste-map/profile work, learned-head training, and poster
rescanning to the canonical execution/evidence platform without deploying or resetting library
posters, then certify every Chunk 4 producer, batch, schedule, handler, progress policy, and
resource budget under a production-like non-mutating workload.

**Ordering:** third and final JMC4 plan. It starts only after JMC4B is compacted, certified, and
tagged `jmc4b-complete`. Chunk 5 must not begin until JMC4C is compacted to one verified commit
and tagged `jmc4c-complete`.

## 1. Preconditions and stop gates

Before implementation:

1. Verify `jmc4b-complete`, its sole compact commit, recovery handoff, exact enabled/disabled
   definitions, production schedule catalog, Alembic/schema/API contracts, generated types,
   retained failures, and pending native-tool/operator smokes.
2. Recreate an owned disposable PostgreSQL database and re-run the JMC4A producer/batch/schedule
   and JMC4B family gates. Confirm all enabled work is non-mutating and no `media_write`
   definition can dispatch.
3. Inventory every route, handler, registry, task, process, shared singleton, progress bridge,
   filesystem write, model activation, poster cache mutation, pipeline run, and frontend consumer
   for `poster_pipeline`, its movie/TV batches, `taste_rebuild`, `taste_map`,
   `learned_head_train`, and `poster_rescan`.
4. Freeze owned fixtures covering movies, series, seasons, episodes, missing/retired subjects,
   candidate sources, rejection gates, no viable candidate, review-required results, duplicate
   images, model/profile versions, interrupted training, and stale poster files.
5. Verify optional OCR/CLIP/DINO/GPU/model dependencies separately from the base environment.
   Deterministic fakes are mandatory; live-model and GPU certification is recorded explicitly
   when the owned host supports it.
6. Record branch/HEAD, exact plan base, configured author, full pytest/Ruff baseline, schema,
   OpenAPI/generated types, affected frontend gates, tool/model versions, hardware capability,
   storage and connection budgets, and operator-only exceptions in the shared timeline.

Stop if JMC4B cannot be reproduced, target work can still execute inline or through the legacy
run manager, poster analysis cannot be separated from deployment/reset, a model activation can
bypass fenced atomic publication, or the working tree contains unrelated work. Missing optional
GPU/model capabilities may defer an explicit live smoke; they may not weaken deterministic
failure, cancellation, stale-fence, or publication tests.

## 2. Locked decisions

| ID | Decision |
|---|---|
| C1 | Enable only `poster_pipeline`, `poster_pipeline_batch`, `poster_pipeline_tv_batch`, `taste_rebuild`, `taste_map`, `learned_head_train`, and `poster_rescan` after their individual family gates pass. |
| C2 | Poster pipeline work discovers, downloads, filters, scores, ranks, and recommends candidates. It never deploys, resets, restores, or changes the library's active poster. |
| C3 | Every network download, decoded image, feature vector, preview, report, and candidate output is created in a confined attempt workspace and registered through JMC3 artifacts. No caller supplies a physical path. |
| C4 | Remove shared mutable run-manager job state, page-local progress bridges, detached execution, direct canonical `Job` mutation, and raw filesystem publication from migrated call graphs. |
| C5 | Single and batch pipelines use the same immutable execution context, typed schemas, definition policies, presenter family, progress writer, logs, artifacts, cancellation, and fenced terminal authority. |
| C6 | A batch's overall denominator is its sealed subject count. Candidate/source/stage progress is current-scope progress and may reset only with a new subject/stage `scope_id`. |
| C7 | Pipeline success records selected or recommended candidates, rejection gates, score interpretation, profile/model versions, prior-poster snapshot, review requirement, and bounded warnings. It does not claim deployment. |
| C8 | Pipeline work begins classified read-only. It may become staged/idempotent only after duplicate delivery, retry, cancellation, artifact naming, and stale-fence tests prove no duplicate or stale publication. |
| C9 | ML/taste outputs are immutable, content-addressed or versioned artifacts. Training never writes directly over the current active model, profile, taste map, or learned head. |
| C10 | Activation is one coordinator-owned fenced compare-and-set after validation, checksum, fsync, and durable artifact registration. Failure/cancellation preserves the prior active version. |
| C11 | Retry or redelivery may reuse verified immutable work, but cannot activate two versions, regress an active generation, or publish from a stale attempt. |
| C12 | `poster_rescan` reconciles derived database metadata with confined poster/cache state. It does not delete files, deploy artwork, restore artwork, or trigger healing. |
| C13 | Clients cannot select model paths, execution class, GPU allocation, retry policy, timeout, activation destination, or artifact key. These remain definition/configuration policy. |
| C14 | Chunk 4 certification measures class fairness and boundedness; it does not promise a global queue position, exact queued ETA, or equal throughput across heterogeneous classes. |
| C15 | `radarr_upgrade`, webhooks, healing, backup creation, cache/retention/metrics deletion, poster deployment/reset, Dolby Vision conversion, letterbox mutation, and audio/subtitle mutation remain disabled. |
| C16 | Projection Room visuals and the shared feature-page progress card remain Chunk 6. JMC4C supplies complete canonical APIs, presentations, events, logs, artifacts, and progress data. |
| C17 | Phase commits are required until final certification. Squashing is final-only and requires verified external recovery plus exact tree identity. No JMC4 agent pushes. |
| C18 | Existing test failures may shrink but may not grow. Do not add skips or `xfail`; never run `ruff format`. |

## 3. Poster execution boundary

Refactor the poster pipeline behind a typed domain interface equivalent to:

```python
async def execute_poster_pipeline(
    context: ExecutionContext,
    request: PosterPipelineRequestV1,
) -> PosterPipelineResultV1:
    ...
```

The handler receives an immutable subject/configuration/request snapshot. It obtains cancellation,
network access, progress, logs, workspace, artifacts, database projection sessions, and bounded
CPU/GPU inference only through declared execution-context capabilities. It never owns canonical
terminal state, PgQueuer acknowledgement, worker admission, or transport retry.

Split legacy orchestration into deterministic stages with stable semantic keys:

1. resolve immutable subject and prior-poster snapshot;
2. enumerate configured candidate sources;
3. download and validate bounded candidates;
4. deduplicate and apply safety/quality/OCR gates;
5. extract declared model features;
6. score and rank with the snapshotted profile/model versions;
7. select a recommendation or explain why none is viable;
8. render bounded previews/reports;
9. register immutable artifacts and typed result;
10. finalize without touching active library artwork.

Stages may be skipped with explicit reasons. A candidate failure is target-attributed and need not
fail the job when policy permits remaining candidates. A source-wide transient failure follows the
definition's retry classifier. No viable candidate is a truthful `no_change`/review-required
outcome according to the registered policy, not a fabricated success.

### 3.1 Workspace and artifact rules

- Candidate downloads use bounded size/count/time limits and sanitized source descriptors.
- Decode and model processing never reads an arbitrary caller or database path.
- Original downloads, accepted/rejected previews, contact sheets, rankings, feature summaries,
  and diagnostic reports are registered with typed artifact kinds and retention metadata.
- List/detail results contain compact artifact availability and confined keys only.
- Artifact names are deterministic within job/attempt/fence and cannot collide across redelivery.
- Stale attempts may leave quarantined evidence but cannot attach it as the canonical successful
  result or promote it into shared active state.
- Secrets, signed URLs, authorization headers, local roots, raw model paths, and unbounded model
  tensors/features are excluded or redacted.

### 3.2 Single-subject result and progress

The typed result records subject, sources/candidate counts, accepted/rejected counts by gate,
ranked candidate summaries, selected/recommended candidate, score/confidence interpretation,
profile/model versions and checksums, previous-poster snapshot, review reason, warnings, metrics,
and artifact references.

Use hybrid progress honestly:

- determinate candidate/source/item counts when the denominator is stable;
- determinate model batches when the adapter exposes a stable total;
- indeterminate loading, opaque OCR/model initialization, and external-source waits;
- no guessed percentage based on expected providers, average inference time, or historic counts.

Plain-language status always includes the most specific movie/show/season/episode context and
current stage. Raw internal stage keys are never the primary label.

## 4. Poster batches

Migrate `poster_pipeline_batch` and `poster_pipeline_tv_batch` to JMC4A ticketless parents with
ordinary canonical pipeline children.

- Freeze selection scope and child subjects at creation for fixed batches.
- Count one child per logical poster decision, not per candidate or pipeline stage.
- Preserve series/season/episode context even when several episodes share artwork or one pipeline
  decision applies to a series-level poster.
- Overall progress uses sealed child completion and never resets between movies or shows.
- Current progress displays one primary running child plus bounded concurrent-child summaries.
- Parent results aggregate recommended, no-change, review-required, warning, failed, cancelled,
  and source-unavailable counts without copying every candidate into the parent row.
- Parent cancellation reaches nonterminal descendants only. A child already publishing immutable
  artifacts must finish or prove process death before becoming cancelled.
- Retry creates a successor parent and uses the registered failed-only/all-child policy; it never
  rewrites the original batch.

Prove movie and TV endpoints no longer create page-local run IDs, background tasks, or legacy batch
rows. Their response is the canonical JMC4A submission contract.

## 5. Immutable ML and taste publication

Migrate `taste_rebuild`, `taste_map`, and `learned_head_train` through canonical definitions and
the JMC3 execution kernel.

### 5.1 Common publication protocol

Each run:

1. snapshots training/input selection, relevant feedback/library identity, configuration, base
   model/profile version, random seed, algorithm/version, and expected active generation;
2. writes only within its confined attempt workspace;
3. emits truthful loading, collection, feature, epoch/batch, evaluation, validation, and
   publication progress according to the registered policy;
4. produces immutable versioned artifacts plus bounded metrics/report documents;
5. validates format, compatibility, required metadata, checksum, and loadability;
6. registers artifacts before activation;
7. checks cancellation and fence immediately before the coordinator's activation;
8. atomically compare-and-sets the active pointer from the expected generation to the new one;
9. records the activated version in the typed result and semantic event.

An activation conflict is not overwritten. The definition either reports a safe superseded result
or requests a policy-approved retry from fresh inputs. A failed, cancelled, timed-out, or stale
attempt leaves the prior active version untouched and its bounded diagnostics available.

### 5.2 Family-specific evidence

- `taste_rebuild`: selected feedback/example counts, excluded/invalid items, profile/version,
  feature/model versions, coverage, before/after summary metrics, and immutable profile artifact.
- `taste_map`: subject/candidate coverage, embedding/model version, projection parameters/seed,
  bounded preview/report, and immutable map/data artifacts.
- `learned_head_train`: dataset split and provenance, feature schema, epochs/batches, validation
  metrics, early stopping, calibration/compatibility checks, base/produced versions, and immutable
  head/report artifacts.

Training progress is determinate only for stable epochs/batches/items. Model download/loading,
opaque native calls, and final validation remain named indeterminate stages. ETA appears only after
the policy's minimum stable observations and is withdrawn when the rate becomes unreliable.

## 6. Poster rescan

Migrate `poster_rescan` as read-only/derived reconciliation on the `media_read` or definition-owned
class justified by its actual I/O.

- Resolve poster and subject identities from immutable snapshots and confined storage keys.
- Compare bounded file metadata/checksums and live projections without accepting raw paths.
- Update only derived availability/staleness metadata through idempotent fenced domain writes.
- Mark missing or changed observations explicitly and retain historical evidence.
- Return `no_change` when the projection is already current.
- Do not unlink, replace, restore, deploy, heal, fetch a replacement, or enqueue a destructive job.

## 7. APIs, presentations, and generated contracts

For each migrated initiating route:

- submit through the JMC4A service and return the bounded canonical job response;
- expose canonical snapshot/detail/log/artifact links and idempotency disposition;
- keep request policy server-owned and reject raw filesystem/model/execution values;
- remove legacy run IDs, detached-task success responses, and per-page lifecycle authority;
- use 409 for semantic idempotency or activation-generation conflicts where applicable;
- regenerate deterministic OpenAPI and TypeScript contracts and update only affected frontend API
  calls; do not build the Chunk 6 visual experience.

Complete JMC2 presenters and golden fixtures for candidate gates/ranking, selected/review-required
posters, movie/TV batch aggregates, taste/profile/map/head artifacts and metrics, prior/active/new
versions, rescans, warnings, failures, cancellation, and no-change. Presenters use friendly stages
and never expose internal model paths, raw status keys, secrets, or unbounded feature data.

## 8. Chunk 4 saturation and certification

Certify all definitions enabled by JMC4B and JMC4C together, not only poster/ML paths.

### 8.1 Fairness and resource budgets

- Saturate `control`, `network`, `cpu`, `media_read`, `gpu`, and `maintenance` with fixed owned
  workloads while proving control work remains responsive and each eligible class progresses.
- Verify PgQueuer global entrypoint limits, per-worker total task cap, SQLAlchemy/direct-asyncpg
  connection arithmetic, advisory-gate connections, event tailers, log writers, and artifact I/O
  remain within documented budgets.
- Measure API queue/snapshot/detail p50/p95, queue oldest-eligible age, semantic-event lag, SSE
  repair, log throughput, storage growth, batch aggregation query counts, and scheduler callback
  latency against recorded thresholds.
- Report class-local approximate ordering only where meaningful; do not certify a global rank/ETA.

### 8.2 Failure and concurrency matrix

Inject API, scheduler, worker, child-process, listener, event tailer, database, network, native-tool,
GPU, and artifact-storage failures at submission, admission, progress, retry, cancellation,
validation, immutable publication, batch aggregation, and terminal commit boundaries.

Prove:

- no duplicate canonical job, child, transport ticket, artifact attachment, or active model version;
- no stale fence/attempt can publish a result, artifact promotion, derived projection, or activation;
- no batch remains falsely terminal/open or loses a counted child after repair;
- cancellation confirms child death and preserves the prior model/profile/library poster;
- schedule overlap, duplicate callback, restart, disable/re-enable, misfire, and coalescing remain
  deterministic with two schedulers;
- current subject/stage transitions and legitimate scope resets never regress overall progress;
- logs, artifacts, presentations, History, and retry lineage remain readable after subject retirement;
- terminal failures retain bounded remediation/evidence and never report 100% completion falsely;
- every enabled endpoint has one canonical producer and every enabled type has exactly one executor.

## 9. Implementation phases

Every phase ends with focused tests, full retained pytest comparison, `ruff check marquee tests`,
schema/generated-contract checks, affected frontend check/lint/build, `git diff --check`, one short
lowercase phase commit, and a shared timeline update.

### Phase C0 — verify JMC4B and freeze poster/ML contracts

- Complete §1 and add static/runtime inventory tests for all target and deferred routes, writers,
  activators, files, model pointers, and executor paths.
- Freeze representative results, presenter goldens, progress policies, budgets, and plan base.

### Phase C1 — poster workspace, pipeline context, and immutable evidence

- Replace shared run-manager/page-progress/filesystem state with execution-context services.
- Implement confined candidate handling, stage adapters, typed results, artifact kinds, and stale
  attempt quarantine while keeping the production definition disabled.

### Phase C2 — single poster pipeline

- Migrate and certify `poster_pipeline`, its canonical endpoints, presentation, progress, logs,
  artifacts, retry/cancellation, and strict non-deployment proof; then enable it.

### Phase C3 — movie and television poster batches

- Migrate both parent families, nested subject-aware progress, aggregate results, cancellation,
  retry lineage, and bounded child/detail APIs; then enable them.

### Phase C4 — taste, learned head, and poster rescan

- Implement immutable validation/activation for `taste_rebuild`, `taste_map`, and
  `learned_head_train`; migrate the non-destructive rescan; certify and enable each family.

### Phase C5 — complete Chunk 4 certification and history compaction

- Run §§8 and 10 across every enabled JMC4 family and schedule, prove the exact deferred manifest,
  finish operator/live-model smokes or record honest exceptions, then perform §11.

## 10. Acceptance matrix

For every enabled definition prove success, `no_change`, permanent failure, transient retry,
cancellation, timeout, duplicate delivery, terminal redelivery, stale fence/sequence, missing or
retired subject, configuration determinism, subject-aware progress, logs, artifacts, events,
presenter golden, submission recovery, and bounded API/query behavior.

Additionally prove:

- poster source/candidate caps, malformed images, duplicate candidates, each rejection gate, no
  viable candidate, selected/review-required result, score explanation, and zero deployment/reset;
- movie/show/season/episode batch labels, stable overall denominator, concurrent child summary,
  partial failure, cancellation, successor retry, and bounded parent payload;
- model load failure, deterministic seed, epoch/batch progress, validation failure, activation
  conflict, cancellation immediately before activation, worker death during activation, stale
  redelivery, checksum/load verification, and prior-version preservation;
- poster rescan missing/changed/unchanged observations, path confinement, and zero delete/heal;
- queue fairness, API p95, event lag, log/artifact throughput, PostgreSQL connections, advisory
  gates, storage caps, and batch queries under saturation;
- periodic sync/deep-scan uniqueness plus every fixed/dynamic batch seal/outcome/repair rule;
- static proof that all enabled endpoints use canonical submission, all enabled definitions have
  exactly one PgQueuer executor, and no legacy writer/worker/scheduler/run manager starts;
- exact disabled manifest from C15 and absence of `media_write` product dispatch;
- no new failure, error, skip, or `xfail`; backend/frontend/schema/generated gates all pass.

## 11. Mandatory final-only history compaction

Perform this only after C5 is fully certified and recorded:

1. Verify a clean tree and a linear, configured-author, JMC4C-only, unpushed range after the exact
   `jmc4b-complete` plan base. Stop for merges, unrelated/concurrent commits, uncertain ownership,
   or a plan-owned commit already pushed.
2. Commit the final pre-squash timeline entry containing all C phase hashes, full certification,
   enabled/deferred manifest, schedules, budgets, live-model/operator exceptions, deviations,
   pre-squash tip, and intended resolver tag `jmc4c-complete`.
3. Create a timestamped local recovery branch and annotated tag for the pre-squash tip. Create a
   complete repository-external Git bundle, preferably under
   `/home/quartermaster/backups/Marquee/`, containing the current branch and recovery refs, then
   run `git bundle verify`. Request permission if needed; do not silently rely on `/tmp`.
4. Record the certified pre-squash commit and tree hash. Through RTK, soft-reset to the exact plan
   base and create one configured-author commit:
   `jmc4c: complete nonmutating job migration`.
5. Verify the compact commit's tree hash exactly equals the certified pre-squash tree. Verify the
   plan base is its sole parent, the worktree is clean, and the recovery refs/bundle still resolve.
6. Create the local annotated tag `jmc4c-complete`. Report its full hash, tree hash, plan base,
   recovery refs, bundle path, and verification. Do not edit the timeline after compaction.
7. Do not push, force-push, delete recovery refs, or start Chunk 5. Rerun a focused smoke and
   `git diff --check`; report truthfully whether the full suite was rerun after the tree-identical
   rewrite.

All Git and development commands use RTK. Any ancestry, ownership, bundle, or tree mismatch stops
the rewrite. No agent/model attribution is permitted in commits, tags, files, bundles, or timeline.

## 12. Out of scope

- every source-media mutation and `media_write` product definition;
- poster deploy/reset/restore/healing, Dolby Vision conversion, letterbox apply/revert/re-encode,
  audio/subtitle removal/generation/embed/restore, backup creation, and destructive maintenance;
- `radarr_upgrade`, webhooks, authentication, public reset replacement, Docker hardening;
- Projection Room/shared progress visuals, GitHub Actions modernization, zero-failure cleanup;
- pushing JMC4 commits/tags or deleting recovery material.

## 13. Operator handoff

The final report identifies the compact tag/hash/tree, plan base, recovery refs/bundle, exact
enabled/deferred definitions and schedules, schema/OpenAPI/type versions, retained failures,
fixture/tool/model/GPU versions, live smokes performed or deferred, storage/event/log/database/
worker budgets and observed results, every pending operator action, and the exact Chunk 5 starting
condition. It must state plainly that Chunk 4 certifies analysis and immutable derived artifacts,
not deployment or source-media mutation.
