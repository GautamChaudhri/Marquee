# JMC6E — Canonical Seam and Refresh Closure

> **Implementer:** Read this document in full before changing code. Decisions are locked.
> Execute every internal phase continuously; phase boundaries are verification checkpoints, not
> stopping points. Stop only for a condition in
> [`design/plans/README.md`](../plans/README.md#implementer-continuity-and-stop-conditions)
> or after the complete plan, certification, and final-only history compaction succeed.

**Predecessor:** [JMC6D runtime recovery and activation safety](jmc6d-runtime-recovery-and-activation-safety.md)  
**Progress architecture:** [job progress and loading experience](job-progress-and-loading-experience.md)  
**Projection Room:** [job experience redesign](projection-room-job-experience-redesign.md)  
**Shared timeline:** `design/job-system-update/jmc6-post-certification-hardening-timeline.md`  
**Recommended model tier:** **God**

## 1. Objective

Close every user-visible seam that still bypasses canonical PgQueuer execution or loses active work
after refresh. JMC6E migrates the remaining single-file subtitle and taste/learned-model work,
centralizes overlap/idempotency policy, and makes every initiating feature page recover the exact
active work for its subject rather than a feature-wide first page.

The result must have one durable answer to four questions: what operation was requested, on which
subject, whether equivalent/conflicting work is already active, and which canonical job the UI must
recover after navigation or connection loss.

## 2. Preconditions and stop gates

Before edits:

1. Verify annotated `jmc6d-complete`, compact ancestry/tree/recovery bundle, shared timeline,
   migration head, runtime recovery tests, effective schedule state, removed webhook surface,
   generated contracts, and zero-green gates.
2. Inventory every nontrivial route and frontend action that performs scanning, analysis, model or
   map publication, feedback-triggered training, subprocess work, filesystem publication, or
   detached/background execution.
3. Freeze all `FeatureActivityPanel` consumers and their job types, subject kinds/references,
   correlation roots, local busy state, and submission idempotency keys.
4. Establish failing tests for the current inline subtitle scan, taste recomputation/enrichment,
   automatic learned-head retraining, feature-wide 20-row recovery loss, and duplicate submission
   after refresh.

Stop if an operation's product semantics cannot determine its immutable input snapshot, if
feedback and successor training cannot be committed without losing lineage, or if overlap policy
would merge non-equivalent destructive requests.

## 3. Locked decisions

| ID | Decision |
|---|---|
| E01 | Every long-running, tool-backed, model-building, media-scanning, or artifact-publishing user action creates or resolves a canonical job. Routes may perform bounded validation/planning but never execute that work inline. |
| E02 | `POST /api/media-files/{id}/subtitles/scan` becomes a canonical `subtitle_scan` submission and returns the standard job-submission contract. Inventory reads remain GET/read-only. |
| E03 | `GET /api/taste/map` is read-only and never recomputes. Remove the `recompute=true` execution path. Rebuilding uses the canonical `taste_map` submission. |
| E04 | Add a typed canonical `taste_enrich` definition only if enrichment remains a product feature. It publishes immutable profile/map artifacts through the JMC3/JMC4 ML protocol; it does not mutate active profile/map files inline. Do not overload `taste_rebuild` with materially different semantics. |
| E05 | Feedback application/undo may update its bounded canonical feedback record synchronously, but optional learned-head retraining is a successor `learned_head_train` job linked to the exact feedback/profile revision. HTTP response never waits for training. |
| E06 | Exemplar/profile mutations that require taste-map or model regeneration enqueue typed successor jobs after their durable mutation commits. Process-local rebuild state is not updated. |
| E07 | Add one definition-owned active-overlap policy with `coalesce_equivalent`, `reject_conflict`, or `allow`. The server computes its scope from job type, immutable subject identity, relevant payload/configuration version, and correlation rules; clients cannot choose the policy or raw key. |
| E08 | Equivalent active work returns the existing canonical job with `idempotent=true`. Conflicting unsafe work returns a typed conflict and link to the active job. `allow` is reserved for definitions whose concurrency is explicitly safe and tested. |
| E09 | Active overlap resolution is serialized transactionally with submission, using a database constraint or ordered advisory lock plus canonical lookup. It cannot race two API requests into duplicate jobs. Transport dedupe remains defense in depth. |
| E10 | Add bounded exact job-list filters needed by initiating pages, including `subject_reference` and applicable root/parent/correlation identity. Filters are indexed/query-budgeted and generated into TypeScript. |
| E11 | Every subject-detail page queries its exact subject scope and relevant job types. A feature-area-only query is insufficient for movie, series, season, episode, file, track, poster, or model detail. |
| E12 | Feature overview pages may use feature-wide scopes, but their bounded panel must expose deterministic pagination or an Activity link; they do not claim to represent every active item after the first page. |
| E13 | Page action availability derives from the shared server-backed Activity store and server capabilities/overlap state. Local `busy` variables may represent the current HTTP request only; refresh cannot re-enable a conflicting operation while its job is active. |
| E14 | Locally initiated job IDs remain an immediate display optimization, never the recovery authority. Refresh with empty local storage must rediscover the same job by exact Queue scope. |
| E15 | Submission responses always expose canonical job ID, idempotency disposition, snapshot/detail links, and active-conflict information where applicable. Raw PgQueuer identity remains private. |
| E16 | Terminal callbacks refresh product data without removing the job card. Failed/cancelled jobs preserve evidence and do not masquerade as successful product refresh. |
| E17 | Every new/revised handler uses typed documents, subject snapshots, honest progress, fenced writes, logs/artifacts/events, cancellation, retry policy, and presenter coverage. No generic fallback is introduced. |
| E18 | Remove handwritten compatibility fields/types such as the permanently empty subtitle `active_job`/`MediaJob` seam when their consumers migrate. Do not preserve old response shapes without a live caller. |
| E19 | Regenerate deterministic OpenAPI/TypeScript contracts and keep the existing typed fetch runtime. No generated runtime SDK is added. |
| E20 | No JMC6E implementer pushes, activates the system, weakens zero-green gates, or adds agent/model attribution. |

## 4. Canonical route migration

### 4.1 Subtitle scan

Resolve the media file and immutable subject/configuration snapshot, submit `subtitle_scan`, and let
the existing handler rescan/persist typed inventory. The movie/episode detail UI tracks the returned
job and refreshes inventory from terminal success/no-change. Repeated equivalent clicks resolve to
the same active job; a conflicting mutation uses the definition's conflict policy.

### 4.2 Taste map, enrichment, and learned head

Keep all map/profile/head GET routes read-only. Canonical jobs publish immutable versioned artifacts
and atomically activate only after validation/checksum/fence checks. Failure/cancellation preserves
the previous active version.

Feedback and undo responses include any scheduled successor job without conflating “feedback
saved” with “model trained.” Use revision/idempotency lineage so retries do not train or activate
twice. Presenters explain the triggering feedback/profile revision and publication outcome.

Remove old multiprocessing/process-local rebuild machinery once no route depends on it; final broad
legacy deletion is JMC6F, but JMC6E must leave no executable taste bypass.

## 5. Exact recovery and overlap behavior

Extend the generated list contract with exact subject/correlation filters. Build a manifest mapping
each initiating action to:

- definition/job type;
- feature area;
- immutable subject kind/reference;
- parent/root/correlation scope where applicable;
- overlap policy;
- action capability while active;
- terminal product refresh callback.

The shared store exposes an exact scope view and derived active/conflicting state. Page components
must not implement independent polling, EventSource, raw stage maps, or local-storage authority.
Test more than 20 unrelated active jobs, refresh/navigation with storage empty, dropped SSE,
snapshot repair, hidden-tab throttling, rapid double click, two browser tabs, and two concurrent API
requests.

## 6. Implementation phases

### Phase E0 — verify JMC6D and freeze route/page manifest

Verify predecessor compaction and gates; inventory all route work bypasses and every initiating
page. Add contract tests for inline-work absence, exact page scope, overlap policy coverage, and
generated API drift.

### Phase E1 — overlap policy and exact list filters

Add definition-owned overlap policies, transactional equivalent/conflict resolution, subject/root/
correlation filters, indexes/query budgets, submission dispositions, and generated contracts.
Certify concurrent API calls and terminal release of active scopes.

### Phase E2 — canonical subtitle scan

Replace inline single-file scanning with `subtitle_scan` submission, migrate clients and result
refresh, remove `active_job`/`MediaJob` compatibility data, and certify movie/episode/file context,
refresh recovery, duplicate clicks, cancellation, failure, and no-change.

### Phase E3 — canonical taste/enrichment/training successors

Make map reads pure, migrate rebuild/enrich/model training to canonical jobs, link feedback/undo and
exemplar changes to typed successors, remove executable process-local rebuild paths, and certify
immutable activation, failure preservation, retry/redelivery, progress, evidence, and lineage.

### Phase E4 — exact feature-page recovery and action state

Update every `FeatureActivityPanel` consumer and initiating action to its manifest scope. Derive
action disable/conflict state from the shared store; preserve local flags only for in-flight HTTP.
Add pagination/Activity handoff for overview panels and exhaustive refresh/navigation/multi-tab
fixtures.

### Phase E5 — integrated certification and compaction

Run §7 across all feature families plus the full backend/frontend/static/schema/generated suites.
Update the shared timeline and perform §8 only after every gate passes.

After every phase, run gates, commit, update the timeline, and continue immediately. Successful
phase completion is not a reason to stop.

## 7. Acceptance and verification

- Static route scan finds no nontrivial subtitle/taste/model work executed inline or in detached
  page-local/process-local tasks.
- Subtitle scan, taste map rebuild, enrichment, and learned-head training all have exactly one
  enabled definition/handler/submission path/presenter/progress policy.
- GET routes never trigger recomputation, artifact writes, training, or filesystem mutation.
- Feedback save/undo succeeds independently of successor training and returns truthful lineage.
- Equivalent concurrent requests resolve to one canonical job; different unsafe requests conflict
  rather than coalesce.
- Refresh with empty storage recovers the exact movie, series, season, episode, file, track, poster,
  model, and batch work even with more than 20 unrelated active jobs.
- Page actions remain unavailable or appropriately conflict while matching work is active; terminal
  state restores capabilities from server truth.
- Dropped/late SSE, API restart, hidden tab, overlapping repair, two browser tabs, and navigation do
  not lose cards or start duplicate work.
- Query counts and cursors remain bounded and indexed; malformed/retired subjects remain readable.
- Backend pytest stays zero-failure/zero-skip/zero-xfail; Ruff, Alembic/model equivalence, PgQueuer
  verify, OpenAPI/type drift, frontend unit/check/lint/build/E2E, accessibility, and
  `git diff --check` pass.

## 8. Mandatory final-only history compaction

Perform only after E5 succeeds:

1. Verify a clean, linear, configured-author, JMC6E-only, unpushed range after exact
   `jmc6d-complete`.
2. Commit the final pre-squash timeline state with phase hashes, all gates, deviations, operator
   work, pre-squash tip, and intended tag `jmc6e-complete`.
3. Create timestamped recovery branch/tag and a verified repository-external Git bundle.
4. Record the certified tree; soft-reset through RTK to the plan base and create one
   configured-author commit: `jmc6e: close canonical job and refresh seams`.
5. Verify exact tree identity, sole parent, clean tree, recovery refs, and bundle.
6. Create annotated local tag `jmc6e-complete`; do not edit the timeline afterward.
7. Do not push, force-push, delete recovery material, or add authorship/generator attribution.

Stop rather than rewrite if ownership, ancestry, concurrency, backup, or tree identity is uncertain.

## 9. Out of scope and handoff

Out of scope: broad legacy helper/test deletion beyond the migrated paths, final activation audit,
browser authentication, public reset replacement, Docker hardening, webhook implementation,
`radarr_upgrade`, and activation.

Handoff reports the compact tag/hash/tree/base and recovery bundle; exact route/page/overlap
manifest; new/revised definitions and contracts; schema/OpenAPI/type hashes; no-inline-work proof;
refresh/duplicate/multi-tab results; complete gates; deviations/operator actions; and the exact
JMC6F starting condition.
