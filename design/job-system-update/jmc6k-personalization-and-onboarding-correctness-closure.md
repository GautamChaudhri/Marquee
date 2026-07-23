# JMC6K — Personalization and Onboarding Correctness Closure

> **Implementer:** Read this document in full before changing code. Decisions are locked.
> Execute every internal phase continuously; phase boundaries are verification checkpoints, not
> stopping points. Stop only for a condition in
> [`design/plans/README.md`](../plans/README.md#implementer-continuity-and-stop-conditions)
> or after the complete plan, certification, and final-only history compaction succeed.

**Program:** Final corrective closure after the independent JMC6I/J audit

**Predecessor:** [JMC6J taste onboarding and residual learning](jmc6j-taste-onboarding-and-residual-learning.md)

**Architecture context:** [Poster pipeline](../poster-pipeline.md),
[job progress/loading experience](job-progress-and-loading-experience.md), and
[Projection Room experience](projection-room-job-experience-redesign.md)

**Implementer timeline:** `design/job-system-update/jmc6k-personalization-correctness-timeline.md`

**Recommended model tier:** **God**

## 1. Objective

Close the remaining correctness and product-completeness defects found after JMC6I and JMC6J
without reopening the completed job runtime. The work joins four pieces that must agree on the same
canonical evidence and publication lineage:

1. a complete, recoverable first-run poster-choice experience;
2. serialized and retryable movie/TV taste-profile publication;
3. readiness derived from active, pending, and failed publication lineage rather than one digest;
4. a residual scorer whose training evaluation exactly matches deployed scoring and whose artifact
   is validated against the actual library, baseline, and active taste profile.

This is one plan with internal phases because the current defects cross the same onboarding,
evidence, publication, and scorer contracts. Splitting them would allow each layer to pass locally
while the end-to-end journey remained broken.

JMC6K is the final construction plan before browser-agent acceptance and operator activation.
Every in-scope defect discovered by its certification is fixed within JMC6K. A green unit test that
does not prove the real producer-to-consumer path is not sufficient evidence.

## 2. Preserved foundations and non-goals

The following completed systems are reused and changed only if a failing regression proves an
integration defect:

- PgQueuer transport ownership, canonical submission, fenced execution, and retry-as-successor;
- JMC3 process containment, cancellation, logs, artifacts, events, and confined storage;
- JMC6 typed progress, shared Activity state, and internal-runner death confirmation;
- immutable ML artifact publication and compare-and-set activation;
- the clean-slate database decision and generated OpenAPI/TypeScript workflow.

JMC6K does **not** redesign the job manager, add a second scheduler, restore a learned-head
replacement scorer, add starter profiles, reintroduce filesystem onboarding state, modernize
GitHub Actions, harden Docker, implement webhooks, or activate deferred destructive work. It does
not perform the final human/browser acceptance against the operator's real library; it builds and
certifies the application that acceptance will exercise.

## 3. Revalidated audit findings

The independent post-JMC6J inspection and green-suite run found the following gaps at the compact
`jmc6j-complete` tree:

| ID | Severity | Finding | Consequence |
|---|---|---|---|
| K-A1 | Critical | The onboarding page can start analysis/build work but cannot render neutral candidate choices or call the existing choose endpoint. It redirects into generic job detail. | A fresh user cannot complete the mandatory poster-selection journey in the product UI. |
| K-A2 | High | Onboarding status and UI links use `/activity`, while the actual Activity route is `/projection-room`. | Recovery and navigation lead to a missing page. |
| K-A3 | Critical | `POST /api/onboarding/choose` trusts client-supplied candidate reference and presentation order rather than reconstructing them from the archived pipeline run. | Preference evidence can claim an exposure/order that the server never presented; invalid deploy work can be created before downstream validation fails. |
| K-P1 | Critical | `TasteRebuildRequestV1.expected_generation` defaults to zero and `schedule_profile_builds()` omits it. | Generation 2 and later builds lose publication CAS and are reported superseded. |
| K-P2 | Critical | Build idempotency is keyed only by library and revision, and a reused terminal failed job can move a revision back to `building`. | A failed exact revision can become permanently stuck rather than creating a canonical retry successor. |
| K-P3 | High | Movie and TV builds share one revision state/job field and can race from the same generation. | Partial namespace success, concurrent revisions, and latest-evidence coalescing cannot be represented or recovered reliably. |
| K-R1 | Critical | Readiness hashes only active global evidence and looks up that digest as the active build. | Namespace overlays or later evidence can make a valid personalized installation appear merely eligible, building, or degraded. |
| K-R2 | High | Readiness conflates current evidence, active publications, in-flight builds, reload state, and update failures. | A failed refresh can hide still-valid profiles, and the UI cannot explain what is active versus pending. |
| K-L1 | Critical | Residual runtime compatibility self-validates the artifact namespace and does not require the active profile checksum at scorer selection. | A stale or wrong-library residual can be applied after the taste profile changes. |
| K-L2 | Critical | Held-out activation evaluates `baseline probability margin + pair-level residual`, while production adds clipped per-candidate deltas in logit space. | The trainer can activate a model that did not improve the scorer users actually receive. |
| K-C1 | Medium | Current closure fixtures and comments still describe retired learned-head paths and rank-test behavior. | Static certification can overstate the current product and future agents can restore obsolete assumptions. |
| K-T1 | High | There are no direct choose-route tests and no browser test for the complete cold-start journey, refresh recovery, partial profile failure, or retry. | The existing green suite cannot detect the user-visible failures above. |

The audit also reconfirmed that full backend, Ruff, Alembic, generated-contract, frontend
check/lint/build/unit, and existing Playwright suites are green. JMC6K preserves those results while
adding executable coverage for the missing behavior.

## 4. Locked architecture decisions

| ID | Decision |
|---|---|
| K01 | The server is the sole authority for candidate exposure, presentation order, evidence lineage, readiness, and active ML compatibility. The browser submits user intent only. |
| K02 | Onboarding remains movie-first and mandatory until valid movie and TV taste profiles are published and reloaded. The residual model is never part of onboarding completion. |
| K03 | Cold-start candidates remain neutral choices. The UI and API expose no hidden rank, score, recommendation, or auto-selection signal. |
| K04 | A positive readiness unit activates only after the selected poster is deployed and post-deploy validation succeeds. Analysis, viewing, submitting, failure, cancellation, and hate do not add a positive. |
| K05 | Explicit hate records canonical negative evidence without deploying a poster and never increments positive readiness. |
| K06 | Candidate choice is bound to a terminal, reviewable `PipelineRun` archive and its registered artifact metadata. Client-supplied exposure/order is removed from the public contract. |
| K07 | One profile build is in flight per library namespace. New evidence updates a durable desired revision; it does not create competing builds from the same generation. |
| K08 | Every profile build request carries the exact active `expected_generation`; the default-zero shortcut is removed from internal construction paths. Publication CAS remains authoritative. |
| K09 | Automatic build idempotency includes library, revision, and expected generation. Retrying a terminal failed job always creates a new canonical successor linked through normal retry lineage. |
| K10 | Reusing a submission can mark a revision building only when the reused job is nonterminal and compatible. A terminal reused job can never resurrect build state. |
| K11 | A superseded build cannot activate stale output. The coordinator reconciles the newest desired revision into one bounded successor and never loops without progress. |
| K12 | Initial global exemplars feed both movie and TV profiles. Later global and namespace overlays are preserved in immutable revision lineage and reported independently. |
| K13 | Readiness is derived from four distinct facts: current evidence, active compatible publications, in-flight/failed build lineage, and newest desired revision. No single digest represents all four. |
| K14 | Once both compatible profiles are active and reloaded, the primary state remains `personalized` while newer evidence is pending or rebuilding. Update failure becomes attention/remediation; it does not deactivate a valid prior profile. |
| K15 | Per-namespace status reports active generation/checksum/revision, desired revision, build job/state/expected generation, reload state, failure, and rebuild-due status. |
| K16 | Residual selection requires the requested library namespace, current weighted-baseline signature, and active taste-profile checksum. Artifact metadata may not validate itself. |
| K17 | Auto scorer mode falls back visibly and safely to the bit-identical weighted baseline on missing or incompatible residuals. Forced residual mode fails with a precise compatibility reason. |
| K18 | One pure scoring primitive defines baseline probability-to-logit conversion, normalized residual delta, per-candidate clipping, alpha application, final logit, and final score. Runtime and held-out evaluation both call it. |
| K19 | Held-out acceptance evaluates the exact deployed per-candidate transformation and pair ordering. A pair-level approximation cannot decide activation. |
| K20 | Training, validation, and final test partitions are separated by subject. Training never reads validation/test labels; activation thresholds and reports identify each partition independently. |
| K21 | Taste-profile activation makes an incompatible prior residual dormant without deleting it. A new residual may activate only after enough post-profile evidence passes exact held-out evaluation. |
| K22 | Onboarding and profile work use the canonical Activity snapshot/SSE repair model. Refresh, navigation, EventSource loss, API restart, or hidden-tab throttling cannot erase active work. |
| K23 | Historical freeze fixtures may remain only when named and documented as historical. Current closure manifests, generated contracts, product copy, and executable tests describe the residual model and real routes. |
| K24 | Internal phases continue automatically. The implementer stops only for an explicit stop gate, a new failing safety invariant, unrelated/concurrent history, or a required operator capability that cannot be isolated. |

## 5. Target product journey

The finished first-run experience is server-recoverable and deterministic:

1. `/onboarding` explains that Marquee needs explicit poster choices and displays 50 required, 75
   encouraged, and 100 strong-coverage guidance from server configuration.
2. The user chooses an eligible movie. Marquee submits canonical profile-free poster analysis and
   returns the job ID plus Activity and recovery links.
3. The shared job store shows subject-aware analysis progress. Refresh/navigation rediscovers the
   job from server state.
4. On success, the page opens a bounded review resource for the archived run. It displays
   source-diverse surviving posters, preview images, OCR/eligibility explanations, and objective
   rejection summaries without ranks or recommendation styling.
5. `Choose` sends the run plus selected candidate identity and idempotency intent. The server
   reconstructs the canonical exposure/order and submits deployment. `Not for me` records explicit
   negative evidence without deployment.
6. The deployment card remains visible through refresh. The readiness count increments only after
   deployment and validation activate the pending exemplar.
7. At 50 unique positives, the coordinator builds movie and TV profiles. The page shows both
   namespaces independently, including partial success/failure and retry actions.
8. Personalization activates only after both publications are compatible and consumers confirm
   reload. The user may continue refining without returning to blocking onboarding.
9. Ordinary later feedback can train a residual. Until an artifact is compatible with the current
   library, baseline, and profile checksum, the weighted scorer remains the complete scoring path.

## 6. Canonical onboarding review and evidence contract

### 6.1 Review resource

Add a bounded versioned response, exposed from a route such as
`GET /api/onboarding/runs/{run_id}/review`, containing:

- canonical run ID, analysis job ID, terminal/review status, and subject snapshot;
- a server-generated immutable review revision/checksum;
- neutral presentation order;
- survivor entries containing an opaque candidate ID/reference, canonical artifact ID, bounded
  image URL, source label, OCR/eligibility explanation, and objective facts safe for display;
- bounded rejection counts/reasons and links to detailed artifacts/logs;
- server-computed allowed actions and any already-recorded selection/hate/deployment state.

The response must not contain personalized rank, recommendation, residual/taste score, or an
ordering derived from hidden scoring. Pagination or bounded survivor limits are mandatory.

### 6.2 Intent-only decisions

Replace the current choose body with a versioned intent containing only:

- run ID;
- selected opaque candidate identity or artifact ID;
- review revision/checksum for stale-screen detection;
- caller idempotency key.

Remove `presentation_order` and any evidence facts from client authority. The service loads the
canonical `PipelineRun`, archived candidate report, and registered artifact; validates terminal
reviewability, subject, content checksum, storage key, candidate membership, and current decision
state; and derives the exposure/order stored in the preference event.

Use `pipeline_candidate_selection` or one equivalent domain service as the single validation and
binding path. The deploy request, preference event, pending exemplar, and selection lineage are
created atomically with canonical submission. If canonical evidence cannot be reconstructed, no
job or preference row is created.

Add an explicit hate endpoint or a single typed decision endpoint. Hate must bind to the same run
and exposure evidence, create negative global evidence idempotently, submit no deployment, and
return updated readiness. Repeating the same decision returns the same canonical result;
conflicting reuse returns a conflict.

### 6.3 Recovery and lifecycle

Onboarding status returns the latest actionable analysis/review/deployment/build lineage and valid
links under `/onboarding`, `/projection-room`, and canonical job detail. It must support a missing or
retired live Movie row through stored subject snapshots. Completed/expired artifacts produce a
plain remediation state rather than a 500.

## 7. Taste-profile build coordinator and readiness model

### 7.1 Durable per-namespace lineage

Replace the single `TasteProfileRevision.state/build_job_id` authority with explicit per-library
build lineage. Use a normalized Marquee-owned record keyed by revision and library, or equivalently
strict typed columns if the implementer proves the same constraints. It must represent:

- immutable revision digest and exact global/namespace exemplar inputs;
- library (`movies` or `tv`);
- desired/current coordinator generation;
- publication `expected_generation` captured at submission;
- canonical job ID and retry/successor lineage;
- queued, running, succeeded, no-change, superseded, failed, or cancelled state;
- resulting artifact/publication generation/checksum and consumer reload checksum;
- bounded failure/remediation information and timestamps.

Add a per-library coordinator cursor containing the newest desired revision and current in-flight
job/build reference. Constrain one in-flight lineage per library in the database; do not rely on
process-local locks. Use transaction row locking or a deterministic transaction-scoped advisory
lock to serialize the scheduling decision.

### 7.2 Submission, coalescing, and retry

Within one transaction, the coordinator:

1. freezes the newest applicable global-plus-library revision;
2. records it as the desired revision for that library;
3. observes the active publication generation under the coordinator lock;
4. reuses a compatible nonterminal build or submits exactly one build with that expected
   generation;
5. uses `taste_rebuild:{library}:{revision}:g{expected_generation}` as automatic idempotency;
6. records the canonical job and expected generation before releasing the lock.

If evidence changes during a build, update `desired_revision` without starting a competing job.
When the in-flight job reaches a terminal state, run a bounded domain reconciliation hook that
submits the newest still-due revision exactly once. This hook may be invoked by the handler/kernel
terminal integration and by bounded status/repair entry points, but it may not poll as a second
scheduler or execute profile work inline.

CAS supersession records the stale attempt and reconciles once against the newest desired revision.
It cannot overwrite a newer publication or spin. Failed/cancelled work stays terminal. User retry
uses the canonical retry-as-successor command, links old and new jobs, preserves the expected
generation contract, and updates lineage only after the successor exists. Partial movie/TV failure
retries only the failed namespace.

### 7.3 Readiness projection

Return a versioned readiness document with:

- evidence counts, thresholds, current global digest, per-library overlay digests, and pending
  positive deployments;
- an overall state and next action;
- per-library active publication, desired revision, build/retry lineage, reload state, update
  attention, and compatibility;
- booleans for initial profile readiness, personalized scoring availability, rebuild due, and
  residual dormancy.

Derive the overall state deterministically:

- `collecting`: below the required positive threshold and no valid initial profile pair;
- `eligible`: threshold reached, no valid initial pair, and no initial build in flight;
- `building`: initial pair incomplete and at least one required namespace is building;
- `degraded`: initial pair unavailable and required build/reload failed;
- `personalized`: both active profiles are compatible and reloaded, regardless of newer pending
  evidence; update problems are attention attached to this state rather than deactivation.

The implementation may add typed secondary `update_state`/attention fields. It must not oscillate
the blocking onboarding state merely because current evidence differs from the active revision.

## 8. Exact residual-scoring contract

### 8.1 Runtime compatibility

Change scorer selection to require a runtime context containing:

- requested library namespace;
- current weighted-baseline signature;
- active taste-profile checksum and generation;
- selected residual artifact identity/checksum.

`ResidualScorer` validates its artifact against those supplied values. Passing the artifact's own
namespace or omitting the profile checksum is forbidden. Carry the same context through the poster
pipeline, contained runner manifest, artifact resolver, reload cache, and presentation/logging.

Auto mode records a bounded dormant reason and returns the same weighted scorer object/path that
would have been selected with no residual. Forced residual mode raises a typed compatibility
failure naming namespace, baseline, profile, feature schema, or artifact corruption without
exposing secrets or filesystem paths.

### 8.2 Shared deployed math

Create a pure, deterministic scoring primitive used by both runtime and evaluation. Given baseline
probability and normalized features, it performs exactly:

1. the production epsilon/clamp policy for baseline probability;
2. baseline logit conversion;
3. residual linear delta plus bias;
4. the production per-candidate `delta_max` clamp;
5. alpha application;
6. final logit and sigmoid score;
7. bounded contribution/explanation output.

Pair comparison computes the difference between the two candidates' **final logits** after each
candidate's own clamp. Evaluation cannot use a clipped feature difference or add a correction to a
probability-space margin.

Train on subject-isolated training data, select/accept against subject-isolated validation data,
and report untouched subject-isolated test results separately. The exact deployed primitive must
produce every validation/test prediction used for activation. Persist partition identities,
baseline/candidate metrics, thresholds, seed, and compatibility inputs in the sanitized artifact
report. Insufficient or non-improving evidence returns no-change and preserves the active scorer.

Taste-profile activation must immediately make a mismatched residual dormant. It must not delete
the immutable artifact. If sufficient canonical evidence exists, schedule a normal residual
successor against the new profile lineage; otherwise wait for ordinary future evidence.

## 9. Frontend implementation contract

Build the complete onboarding experience rather than another API-only scaffold:

- Add a recoverable candidate-review state/route owned by the onboarding feature.
- Use generated OpenAPI types and the existing fetch layer for status, review, choose, hate,
  retry, and job discovery.
- Use the shared Activity/job store and progress card for analysis, deployment, and both profile
  builds. Do not add page-local EventSource or local-storage authority.
- Render neutral candidate cards with accessible image labels, source, OCR/eligibility explanation,
  selection and hate controls, keyboard operation, loading/error states, and no recommendation
  affordance.
- Keep the current subject visible. Explain why a candidate or run cannot be acted on and provide
  direct logs/detail/remediation links.
- Show counts and 50/75/100 guidance from the readiness API, not frontend constants.
- Show movie and TV build rows independently, including active generation, current work, partial
  failure, retry-as-successor, and continued-use state when an update fails after personalization.
- Recover analysis, review, deployment, and build state after refresh, navigation, dropped SSE,
  hidden-tab throttling, and API restart using server snapshots plus bounded repair.
- Replace every `/activity` link with `/projection-room` or the canonical URL returned by the API.

The generic Projection Room remains available for diagnostics, but it is not a substitute for
candidate selection or onboarding remediation.

## 10. Internal implementation phases

### Phase K0 — verify the compact base and freeze failing contracts

1. Verify clean `jmc6j-complete` ancestry/tree, configured author, recovery refs/bundle, migration
   head, shared JMC6I/J timeline, generated contracts, enabled definitions, and retained full gates.
2. Create `design/job-system-update/jmc6k-personalization-correctness-timeline.md` before the first
   implementation commit. Record exact base/tag/hash/tree, environment, existing opt-in live-smoke
   behavior, and K0 in progress.
3. Re-inventory onboarding UI/routes, `PipelineRun` archives, candidate artifacts, preference
   events/exemplars, profile revisions/publications/reload caches, job retry commands, residual
   trainer/runtime call sites, current closure manifests, and frontend/browser coverage.
4. Freeze a disposable PostgreSQL/`DATA_DIR` fixture and deterministic poster/feature inputs.
5. Add failing regression tests for K-A1 through K-T1. Tests must demonstrate the production call
   paths, not only source-text assertions.

**K0 stop gates:** stop if canonical run evidence lacks enough retained candidate identity to bind a
real choice; if the compact base or recovery material is unverifiable; if concurrent/unrelated
commits appear; or if fixing the issue would require weakening fenced publication or deployment
validation. Otherwise commit and continue automatically.

**Suggested phase commit:** `capture personalization closure regressions`

### Phase K1 — bind canonical onboarding review and decisions

1. Implement the bounded review resource and canonical evidence-binding service.
2. Replace choose with the intent-only contract and add explicit hate behavior.
3. Make deployment submission, preference event, pending exemplar, and idempotency atomic.
4. Ensure only validated deploy completion activates a positive; failure/cancellation leaves
   explainable pending/failed evidence and supports retry-as-successor.
5. Correct canonical links and status recovery data.
6. Regenerate OpenAPI and static TypeScript types; migrate frontend API helpers without yet
   completing the page visuals.

**K1 gates:** run/revision/candidate/artifact/subject tamper tests; duplicate/conflicting intent;
hate/no-deploy; deploy success/failure/cancel/retry; deleted subject snapshot; bounded query/payload;
secret/path exclusion; migration/model equivalence if schema changes; generated-contract drift.

**Suggested phase commit:** `bind onboarding decisions to canonical evidence`

### Phase K2 — serialize profile publication and correct readiness

1. Add explicit per-namespace revision/build/coordinator lineage and the forward migration.
2. Pass exact expected generations and generation-aware idempotency through all initial, continued,
   manual, and repair scheduling paths.
3. Implement one-in-flight coalescing, terminal reconciliation, supersession, retry successor, and
   partial-namespace recovery.
4. Replace single-digest readiness with the active/desired/build/evidence projection in §7.
5. Update presenters, status APIs, allowed actions, and generated frontend types.

**K2 gates:** 49/50 threshold; first movie/TV generation; generation 2 and 3; concurrent evidence;
older completion after newer desired revision; duplicate callbacks; failure then retry; terminal job
reuse; partial movie/TV success; reload failure/recovery; active-old/update-failed; global plus
namespace overlay; cancelled/superseded jobs; PostgreSQL/API/worker restart; bounded repair and query
counts; no second scheduler or inline build.

**Suggested phase commit:** `coordinate taste profile revisions`

### Phase K3 — make residual evaluation match production

1. Introduce the explicit runtime compatibility context and thread it through every scorer caller,
   runner manifest, resolver, and cache.
2. Implement and adopt the shared exact scoring primitive.
3. Make training/evaluation subject-partitioned and evaluate activation with deployed math.
4. Persist exact validation/test evidence and handle profile-change dormancy/retraining.
5. Update typed result/presentation/log documents and current certification manifests.

**K3 gates:** namespace mismatch; baseline signature mismatch; profile checksum/generation change;
feature-schema mismatch; corrupt/missing artifact; auto fallback; forced failure; no-residual
bit-identical baseline; hand-calculated per-candidate clamp cases; evaluation/runtime equivalence;
subject leakage; deterministic split/seed; no-gain and insufficient-data preservation; concurrent
activation CAS; stale fence; profile-change dormancy and successor behavior.

**Suggested phase commit:** `align residual evaluation with deployed scoring`

### Phase K4 — complete the recoverable onboarding UI

1. Implement the first-run flow in §5 and frontend contract in §9.
2. Add neutral candidate review, choose/hate, deployment progress, profile namespace progress,
   partial-failure remediation, and post-personalization refinement.
3. Use shared snapshots/SSE repair and remove generic-job-detail as the primary happy path.
4. Update accessible copy, routes, API helpers, frontend units, component fixtures, and Playwright
   journeys.

**K4 gates:** no-library/empty state; analyze; active refresh; reconnect; terminal discovery;
neutral candidate rendering; no score/rank/recommendation leaks; choose/hate; duplicate decision;
deploy failure/cancel/retry; count after validation only; 49/50/75/100; two-namespace building and
partial failure; personalized/update-pending; mobile/narrow layout; keyboard/screen-reader semantics;
Activity/log/detail links; frontend request cancellation and stale-response handling.

**Suggested phase commit:** `complete recoverable taste onboarding`

### Phase K5 — retire stale assumptions and certify end to end

1. Replace or explicitly mark historical closure fixtures so current executable manifests describe
   residual artifacts, real routes, current modules, and generated contracts.
2. Remove stale learned-head/rank-test copy, unreachable aliases, obsolete frontend calls, and
   unused route/request fields. Prove no production consumer references them.
3. Run the complete certification matrix in §11 from a fresh disposable database and data root.
4. Fix every in-scope failure, rerun focused and complete gates, update the timeline, and only then
   perform §12 history compaction.

**Suggested phase commit:** `certify personalization and onboarding closure`

## 11. Verification and certification matrix

Every phase ends with focused tests, the retained full backend suite, `ruff check marquee tests`,
Alembic/model checks where applicable, deterministic OpenAPI/type generation, affected frontend
unit/check/lint/build/Playwright gates, `git diff --check`, one short lowercase configured-author
commit, and a timeline entry. No new failure, skip, warning, or `xfail` may be hidden, quarantined,
or accepted without an explicit plan stop gate.

### 11.1 Backend and database

- fresh upgrade/reset plus Alembic head/model equivalence;
- onboarding review and decision authorization/tamper matrix;
- atomic deploy/evidence creation and activation after validated effect only;
- exact 49/50/75/100 distinct-subject behavior and duplicate-content handling;
- movie/TV generation 1, 2, and later publication/reload;
- concurrent coalescing, supersession, failure, cancellation, retry successor, and repair;
- global/namespace overlay readiness with deleted live subjects and retained snapshots;
- exact residual runtime/evaluation equivalence and compatibility fallback;
- process cancellation, stale fence, duplicate delivery, artifact/log retention, backup/restore, and
  PostgreSQL/API/worker restart regression coverage;
- bounded query counts, payloads, logs, artifacts, connections, and event repair.

### 11.2 Frontend and browser automation

- deterministic generated-contract drift check;
- all frontend API functions type-check against real route paths/bodies/responses/errors;
- unit tests for onboarding store/state transitions and stale responses;
- Playwright/axe desktop and narrow-layout journeys from empty state through profile activation;
- refresh/navigation/dropped-SSE/reconnect at analysis, review, deploy, and build boundaries;
- partial namespace failure/retry and continued personalization during later rebuild failure;
- neutral cards contain no rank/score/recommendation semantics in accessible or hidden text;
- direct Activity, log, artifact, detail, retry, and remediation links work.

### 11.3 Executable product lifecycle

Using owned deterministic fixtures and a disposable database/`DATA_DIR`, execute:

1. fresh sync with no active profiles or residual;
2. cold-start analysis and neutral review;
3. hate plus successful and failed/cancelled choices;
4. 49 confirmed unique subjects remain collecting;
5. the 50th validated deployment causes exact movie/TV profile builds;
6. partial namespace failure, canonical retry successor, publication, and consumer reload;
7. personalized poster analysis using both active profiles;
8. additional evidence coalescing into generation 2 without losing active readiness;
9. natural feedback, residual train/validation/test, bounded compatible activation;
10. profile generation change makes the old residual dormant and preserves weighted output;
11. refresh, API/worker/PostgreSQL restart, backup/restore, and recovery of the same lineage.

The lifecycle report records canonical job IDs, revision digests, expected/actual generations,
checksums, reload acknowledgements, readiness transitions, residual compatibility, and user-visible
links without secrets or physical paths.

### 11.4 Live capability smokes and zero-green accounting

Run the normal full suite and separately run every opt-in live OCR/model test with its explicit
environment flag against owned fixtures. The final report must distinguish hermetic coverage from
host capabilities, but a default skip is not treated as proof: each opted-in test must have an
actual pass result or a named operator-blocking capability exception. CPU OCR must pass because it
is the supported fallback. GPU availability may remain a reported capability, not a prerequisite,
only if CPU produces the certified product result.

The final local baseline is:

- zero backend failures;
- zero Ruff failures;
- zero migration/schema drift;
- zero OpenAPI/TypeScript drift;
- zero frontend unit/check/lint/build/Playwright failures;
- no added skips or `xfail` markers;
- every pre-existing opt-in live test executed explicitly and accounted for;
- clean `git diff --check` and worktree.

## 12. Mandatory final-only history compaction

Perform only after K5 and all certification gates succeed:

1. Record the exact `jmc6j-complete` plan base before the first implementation commit.
2. Verify a clean, linear, configured-author, JMC6K-only, unpushed range. Stop for unrelated or
   concurrent commits, merges, uncertain ownership, missing phase hashes, or a prior push.
3. Commit the final pre-squash timeline entry with all phase hashes, migration/generated
   fingerprints, lifecycle evidence, live-smoke accounting, deviations, operator actions,
   pre-squash tip, certified tree, and intended tag `jmc6k-complete`.
4. Create a timestamped recovery branch and tag plus a verified repository-external Git bundle.
   Preserve all earlier JMC recovery refs/bundles.
5. Record the certified pre-squash tree hash.
6. Soft-reset through RTK to the exact plan base and create one configured-author commit:
   `jmc6k: close personalization and onboarding correctness`.
7. Prove the squashed tree hash exactly equals the certified pre-squash tree, the commit has the
   intended sole parent, the worktree is clean, and recovery material verifies.
8. Create annotated local tag `jmc6k-complete`; do not edit the timeline afterward.
9. Never push, force-push, delete recovery refs, or add agent/model/assistant/generator attribution.

Because a commit cannot contain its own stable hash, the timeline references `jmc6k-complete`; the
implementer reports the resolved compact hash in chat.

## 13. Stop conditions

Stop and report instead of weakening the plan if:

- archived pipeline evidence cannot canonically identify the displayed candidates;
- deployment success cannot be tied atomically and idempotently to positive activation;
- one-in-flight namespace coordination cannot be enforced at the database boundary;
- publication CAS would need to be bypassed or stale artifacts overwritten;
- exact deployed residual math cannot be reused for held-out evaluation;
- subject-isolated partitions cannot be proven;
- the supported CPU OCR path cannot run on an owned fixture after dependency/runtime repair;
- an unrelated/concurrent commit enters the plan range or verified recovery material cannot be
  created;
- a required external capability is unavailable and no truthful bounded fallback exists.

Ordinary phase completion, a long test run, inherited warnings, or an available next phase are not
stop conditions. Record and continue whenever gates pass.

## 14. Final handoff and next step

Report the compact tag/hash/tree/base, recovery refs/bundle, migrations and generated fingerprints,
onboarding review/decision contracts, evidence-binding proof, namespace coordinator behavior,
readiness truth table, profile generations/checksums/reload evidence, exact residual math and
compatibility proof, frontend journeys, lifecycle report, full gates, live capabilities, and any
remaining operator action.

If the independent owner/Codex once-over finds no major defect after `jmc6k-complete`, Marquee moves
to a browser-agent acceptance script against the running application, then operator-owned real
library/GPU/media smokes, remote push and GitHub workflow verification, and selective activation.
No further construction plan is expected.
