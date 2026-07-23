# JMC7C — Activity, Contracts, and Release Certification

> **Implementer:** Read this document in full before changing code. Decisions are locked.
> Execute every internal phase continuously; phase boundaries are verification checkpoints, not
> stopping points. Stop only for a condition in
> [`design/plans/README.md`](../plans/README.md#implementer-continuity-and-stop-conditions)
> or after the complete plan, certification, and final-only history compaction succeed.

**Program:** JMC7 release-readiness recovery

**Predecessor:** [JMC7B onboarding, publication, and learning integrity](jmc7b-onboarding-publication-and-learning-integrity.md)

**Successor:** Independent owner/Codex readiness audit, then browser/operator acceptance only if
that audit is Outcome B

**Architecture context:** [Projection Room experience](projection-room-job-experience-redesign.md),
[job progress/loading experience](job-progress-and-loading-experience.md), and
[JMC6K correctness closure](jmc6k-personalization-and-onboarding-correctness-closure.md)

**Shared timeline:** `design/job-system-update/jmc7-readiness-recovery-timeline.md`

**Recommended model tier:** **God**

## 1. Objective

Finish the product boundary and prove the recovered system rather than declaring it complete from
static manifests or mocked pages. After JMC7C, Activity discovers and reconciles relevant work
without dropping snapshot-only jobs, rejecting a new fence, or accumulating unrelated global SSE
traffic; every onboarding response is a generated typed contract; browser tests execute the real
stateful onboarding/retry/reconnect lifecycle; stale documentation/dead runtime paths are retired;
and all 43 enabled definitions are certified through executable producer-to-visible-effect cases.

JMC7C is the third and final JMC7 plan. Any in-scope defect found by its certification is fixed in
JMC7C. It may not convert a failed scenario into a manifest assertion, skip, screenshot update, or
manual follow-up.

## 2. Audit findings owned by JMC7C

| ID | Severity | Finding at `jmc6k-complete` | Required result |
|---|---|---|---|
| 7C-H1 | High | The feature Activity panel drops snapshot-only records; onboarding scopes do not naturally discover poster jobs; a newer fence retains the prior sequence and rejects new snapshots; every global SSE event schedules repair and grows unrelated state. | One bounded store accepts authoritative snapshots, resets ordering on fence advance, tracks submitted jobs explicitly, filters repair work, and prunes irrelevant history. |
| 7C-H2 | High | Onboarding routes lack response models, generated TypeScript exposes `unknown`, frontend code duplicates handwritten shapes, and Playwright covers only three mocked/static interactions. The closure manifest proves file existence rather than executing declared behaviors. | Pydantic/OpenAPI/generated types are authoritative and a real stateful browser matrix proves every critical lifecycle and recovery path. |
| 7C-M1 | Minor | Poster/timeline docs contain stale architecture claims, `BackupService.scheduler_loop` is dead, the previous external recovery bundle is unavailable, the build emits a very large eager Plotly chunk, and the Activity screenshot baseline fails deterministically. | Current docs and reachability match production, dead code is removed, JMC7 recovery is durable, large visualization code is lazy/bounded, and visual baselines pass for explained product output. |

JMC7C also owns the final independent-style verification of every JMC7A/JMC7B correction and all
43 enabled definitions.

## 3. Preserved foundations and non-goals

Preserve one browser-session `JobProgressStore`, one same-origin multiplexed EventSource, server
snapshot/SSE repair authority, generated OpenAPI/TypeScript workflow, canonical job detail and
Projection Room surfaces, JMC7A runtime/action contracts, and JMC7B domain/publication contracts.

JMC7C does not add a second stream, page-local polling authority, handwritten public response
interfaces, static manifest-only certification, production data fixtures, or release automation.
It does not push, activate schedules, mutate operator media, or claim unavailable GPU/real-library
capabilities.

## 4. Locked decisions

| ID | Decision |
|---|---|
| 7C01 | A valid authoritative snapshot is sufficient to insert a job into the Activity store. Prior local tracking/event presence is not required. |
| 7C02 | Job event order is lexicographic by execution fence/generation and then sequence within that fence. A higher fence always resets the per-fence cursor and accepts its initial snapshot/event even when its sequence is lower than the prior fence's. |
| 7C03 | Same-fence duplicate or decreasing sequence is ignored; lower-fence data is stale; higher-fence state replaces attempt-local progress while preserving immutable job identity and bounded historical lineage supplied by the server. |
| 7C04 | Every job-creating response passes its returned canonical snapshot/ID to the shared store immediately. Onboarding analysis, poster deployment, profile builds, residual work, and preview work do not depend on a broad feature scope matching their job type. |
| 7C05 | The single global EventSource may receive all authorized events, but a store instance schedules repair or retains state only for explicitly tracked job IDs, active visible scopes, current detail routes, parent/child lineage needed by those records, or bounded Queue/History views. |
| 7C06 | Scope matching is server-contract data, not a frontend guess from labels. Unknown/unrelated events do not create records or repair requests. A relevant unknown ID triggers one coalesced bounded snapshot repair. |
| 7C07 | Repair requests are deduplicated and bounded by ID/scope, use backoff, and cannot grow without limit under reconnect storms. Terminal records are retained/pruned by explicit count/age/view policy, not for the browser session forever. |
| 7C08 | Hidden-tab, EventSource loss, reconnect, server restart, snapshot/event overlap, parent/child updates, terminal pruning, and navigation all converge to the latest server snapshot without fabricated progress. |
| 7C09 | Every public onboarding endpoint declares a Pydantic request and response model, stable error model, and exact HTTP status. OpenAPI is the source for generated TypeScript; frontend domain helpers may narrow/validate generated types but may not duplicate their wire shapes. |
| 7C10 | Replace blind generic onboarding requests with generated-path calls and runtime validation at the network boundary consistent with the shared job client. No `unknown` success response is cast directly into product state. |
| 7C11 | Core browser certification uses the real FastAPI app, disposable PostgreSQL, canonical submission, PgQueuer delivery/worker, artifact service, SSE endpoint, and built Svelte application. Deterministic provider/model/media fixtures may replace external services and heavy inference, but route interception may not simulate the lifecycle being certified. |
| 7C12 | Mocked/unit browser tests remain useful for visual edge states, but they cannot satisfy a producer→job→handler→artifact/effect→consumer→SSE/UI closure claim. Each closure claim names an executable stateful test node. |
| 7C13 | The executable certification manifest is a registry of collected node IDs/scenarios with required capabilities and expected product effects. The harness fails on missing, skipped, uncollected, stale, false, or consumerless entries; checking that a test file exists is forbidden. |
| 7C14 | Every enabled definition has at least one real producer→canonical job→delivery→handler→terminal→durable effect/evidence→consumer/projection scenario. Definitions without a user-visible effect still prove their documented operational projection. Parent definitions prove child aggregation and partial outcomes. |
| 7C15 | The final 43-definition matrix also exercises every advertised action through the shared resolver, plus cancellation, retry successor, progress, evidence, and refresh behavior appropriate to the definition. |
| 7C16 | A screenshot baseline changes only after the implementer identifies the intended rendering difference, proves layout/axe/responsive behavior, records pixel-diff evidence, and reviews the new image. Re-recording solely to make CI green is forbidden. |
| 7C17 | Plotly or another large visualization dependency is loaded only when its hidden/lazy panel is opened. The normal shell/Activity/onboarding entry chunks must not eagerly include it. Add a deterministic bundle budget or chunk-graph assertion. |
| 7C18 | Remove `BackupService.scheduler_loop` only after static and runtime reachability prove no caller, startup hook, task factory, import side effect, or test depends on it. Do not preserve dead lifecycle code as a compatibility alias. |
| 7C19 | Correct active design documentation to match the canonical pipeline, residual/profile authority, preview execution, and JMC7 release state. Historical plans remain historical and are not rewritten to pretend they originally contained JMC7 fixes. |
| 7C20 | The unavailable prior JMC6K external bundle remains an honest audit fact. JMC7C creates and verifies new repository-external recovery material for each final history operation and records durable operator copy guidance; it does not claim to restore the missing bundle. |
| 7C21 | Full certification uses isolated disposable PostgreSQL and `DATA_DIR`, no operator database/library/media/schedule, and truthful capability accounting. CPU live smokes run when fixtures support them; unavailable GPU/real-media tests remain explicit release gates, not green results. |
| 7C22 | JMC7C may add a forward migration only for a product contract that cannot be represented after JMC7B. Frontend/store/test/doc cleanup alone is not a reason for schema churn. |

## 5. Activity reconciliation contract

Represent each tracked record with explicit ordering state:

- canonical job ID and immutable subject/definition identity;
- current execution fence or generation;
- last accepted sequence within that fence;
- authoritative snapshot revision/time and connection freshness;
- tracked reasons/scopes and parent/child membership;
- bounded repair state and terminal retention deadline.

On snapshot/event receipt:

1. reject a lower fence;
2. on a higher fence, replace attempt-local state and reset the sequence cursor before accepting the
   new payload;
3. on the same fence, accept only newer sequence/revision according to the server contract;
4. insert a previously unknown relevant record from a valid snapshot;
5. coalesce one repair for a relevant event whose base state is missing or whose sequence has a gap;
6. ignore unrelated events without allocating record or repair state.

The UI may preserve expanded/collapsed preference separately, but it may not preserve stale
attempt progress across a fence change.

## 6. Typed onboarding API surface

Define and export concrete response schemas for status, start, review, choose, hate, retry/action,
complete/continue, and their stable conflicts/remediation states. Reuse canonical generated job,
subject, action, artifact-link, readiness, publication, and error types rather than embedding loose
dictionaries.

Every route supplies `response_model`/status declarations and returns data that validates without
coercive casts. Deterministic OpenAPI export and generated TypeScript must have zero drift. Delete
handwritten wire interfaces after the last consumer moves; keep only UI view models derived from
generated types.

### 6.1 Required response models

| Method and path | Concrete generated success model | Required typed conflicts/remediation |
|---|---|---|
| `GET /api/onboarding/status` | `OnboardingStatusResponse` | Consumer/publication incompatibility and retry/action reasons are fields, not loose dictionaries. |
| `POST /api/onboarding/start` | `OnboardingStartResponse` containing canonical analysis job snapshot and recovery links | Ineligible subject, stale/conflicting active lineage, and unavailable capability. |
| `GET /api/onboarding/runs/{run_id}/review` | `OnboardingReviewResponse` with survivor choices, rejection summary, review revision, and decision state | Run not terminal/reviewable, expired/corrupt artifacts, retired subject snapshot, stale review. |
| `POST /api/onboarding/choose` | `OnboardingDecisionResponse` with immutable decision/exemplar and current deployment successor | Stale revision, candidate not a survivor, conflicting idempotency/decision, unrecoverable artifact. |
| `POST /api/onboarding/hate` | `OnboardingDecisionResponse` without deployment | Same review/idempotency conflicts plus negative-evidence limits. |
| `POST /api/onboarding/complete` | `OnboardingCompletionResponse` with current readiness and next action | Below threshold, build/reload incomplete, incompatible consumer, recoverable namespace failure. |

Names may be adjusted to repository naming conventions during Phase 7C0, but each row remains a
distinct closed schema in OpenAPI/generated TypeScript. A single `dict[str, Any]`, `object`, or
success `unknown` model does not satisfy the contract.

## 7. Stateful browser certification matrix

At minimum, the real-app Playwright project must execute these independent scenarios:

1. fresh status and neutral analysis start;
2. snapshot-only job insertion and progress through SSE;
3. mixed rejected/surviving review with only neutral survivors visible;
4. choose → deployment → validated readiness increment;
5. explicit hate with no deployment/positive increment and later negative profile effect;
6. analysis failure/cancellation and retry successor;
7. deployment failure/cancellation and retry successor using the same decision/exemplar;
8. validated and unvalidated no-change behavior;
9. crash/restart recovery after the domain effect but before terminal projection;
10. 49→50 boundary and separate movie/TV build progress;
11. partial namespace failure and retry while the successful namespace remains valid;
12. generations 2 and 3 with evidence coalescing and no stale activation;
13. publish without acknowledgement, then real consumer load/ack and personalized readiness;
14. corrupt/incompatible artifact with explicit fallback/attention;
15. residual automatic scheduling, held-out activation/no-change, and rollback preservation;
16. fence advance with lower new sequence, snapshot/event overlap, reconnect/loss, hidden-tab
    repair, API/worker restart, and unrelated global event filtering;
17. desktop/mobile keyboard and focus behavior, axe, navigation/reload recovery, and visual baselines.

Use multiple focused specs/fixtures rather than one fragile mega-test, but preserve database and
artifact continuity where a scenario explicitly proves a lifecycle.

## 8. Implementation phases

### Phase 7C0 — verify JMC7B and freeze certification gaps

1. Verify exact clean annotated `jmc7b-complete`, compact tree/base, external bundle, sole
   migration head, generated contracts, 43-definition/action inventory, and JMC7A/JMC7B gates.
2. Append exact backend/frontend/Playwright baseline and bundle sizes to the shared timeline. Keep
   the deterministic Activity screenshot failure as red evidence until its cause is resolved.
3. Freeze every Activity producer/track call, store reconciliation path, scope/filter, EventSource
   repair path, onboarding route/client type, closure-manifest entry, enabled-definition producer
   and consumer, Plotly import, and `BackupService.scheduler_loop` reference.
4. Add failing unit/integration/browser cases for snapshot-only insertion, fence reset, unrelated
   SSE filtering/bounds, generated onboarding responses, real stateful lifecycle scenarios, stale
   manifest nodes, eager Plotly load, and dead-code reachability.

### Phase 7C1 — bounded Activity truth

Implement decisions 7C01–7C08. Centralize comparison/reconciliation in pure tested helpers where
possible, then wire store, feature panels, onboarding producers, Projection Room, job detail, and
parent/child views. Remove scope-specific workarounds after all producers track canonical returned
jobs.

Prove snapshot-only discovery, higher-fence/lower-sequence acceptance, same-fence dedupe/gap repair,
unrelated-event zero allocation, bounded repair under storms, terminal pruning, reconnect, hidden
tab, server restart, and subject/parent scope behavior.

### Phase 7C2 — generated onboarding contracts

Implement decisions 7C09–7C10. Add response/error models, route declarations, deterministic schema
fixtures, generated types, runtime validators, and generated-path client calls. Delete handwritten
wire types and blind success casts. Update every frontend consumer and contract golden.

Gate with backend response validation/negative cases, deterministic OpenAPI hash, generated drift,
TypeScript compile, Svelte check, lint, Vitest, and no duplicate route-shape definitions.

### Phase 7C3 — executable stateful browser and definition certification

Implement decisions 7C11–7C16 and the complete browser matrix. Build a hermetic real-app harness
with owned PostgreSQL/data roots and deterministic provider/runner fixtures at the external
boundary. Convert the closure manifest to collected executable node IDs and fail closed on stale or
skipped coverage.

Execute all 43 definitions through their real producers and consumers. Reuse lower-level fixtures,
but do not replace a route/delivery/effect/consumer boundary with a mock when that boundary is the
claim. Resolve the Activity screenshot by identifying the intended change before reviewing any new
baseline.

### Phase 7C4 — retirement, documentation, and bundle budget

Implement decisions 7C17–7C20. Lazy-load/split Plotly and add a normal-entry bundle assertion.
Delete the dead backup scheduler after reachability proof. Update current poster pipeline and root
timeline documentation with the actual JMC7 authority and remaining operator-only gates. Remove
stale current-contract fixtures/copy while preserving clearly labeled history.

Run static scans for dead scheduler references, eager visualization imports, handwritten onboarding
wire types, old Activity workarounds, and historical learned-head/current-profile claims in active
docs.

### Phase 7C5 — final zero-green and recovery protocol

1. Re-run every JMC7A, JMC7B, and JMC7C focused gate, including real gate contention, picked
   cancellation, all action modes, preview containment, mixed onboarding candidates, successor
   recovery, negative profile effect, residual replacement equivalence, consumer acknowledgement,
   Activity races, generated schemas, stateful browser scenarios, and all 43 definition closures.
2. Run the complete backend suite, Ruff, Alembic heads/current/check, deterministic OpenAPI export,
   generated TypeScript drift, Svelte check, lint, Vitest, production build/bundle budget, all
   Chromium Playwright/axe/visual projects, and opt-in live CPU smokes in isolated storage.
3. Record exact passes/skips/warnings, schema and generated hashes, migration head, path count,
   enabled-definition count, capability results, working-tree state, and any remaining operator-only
   GPU/real-library/media checks. No unexplained failure, xfail, skip, or screenshot drift may pass.
4. Perform an independent source recheck against every JMC7 finding and locked decision. Fix any
   in-scope defect within JMC7C and repeat affected/full gates.
5. Finalize the shared timeline, create timestamped recovery branch/tag and verified external
   bundle, and compact only the JMC7C range from exact `jmc7b-complete` into one tree-identical
   configured-author commit. Tag `jmc7c-complete`; do not edit the timeline after tagging, push,
   activate schedules, or touch operator data.

## 9. Final release-readiness gate

After `jmc7c-complete`, an independent owner/Codex audit repeats source inspection and executable
gates from a clean checkout. Browser/operator acceptance is authorized only if that audit reports
Outcome B (no release-blocking finding). Outcome A returns defects to JMC7C scope unless the owner
explicitly authorizes a new program.

The later operator gate remains distinct and truthful:

- real library onboarding and refresh/restart journey;
- available GPU model/OCR/profile/residual smokes;
- representative movie/TV media and destructive-operation restore checks;
- remote push and GitHub workflow verification;
- selective schedule/capability activation only for passed live checks.

## 10. Mandatory final evidence

| Area | Required evidence |
|---|---|
| Activity | Unit + browser proof of snapshot-only insertion, fence reset, filtering, bounds, reconnect, repair, pruning, producer tracking, and parent/child behavior. |
| Contracts | Pydantic response/error coverage, deterministic OpenAPI and generated TypeScript hashes, no `unknown` onboarding successes, no handwritten wire duplicates. |
| Browser | Real app/DB/worker/SSE lifecycle matrix with deterministic external fixtures, axe, keyboard, responsive, restart, and reviewed visual baselines. |
| Definitions | 43/43 registry-driven executable producer-to-consumer scenarios plus truthful actions; no filename-existence or uncollected-node claim. |
| Runtime/personalization regression | Every critical JMC7A/JMC7B scenario repeated from public producer to durable/visible effect. |
| Retirement/performance | Static absence of dead scheduler/eager Plotly/legacy types/workarounds; normal-entry bundle budget green; current docs accurate. |
| Recovery | Clean configured-author history, exact compact tree identity, annotated tags, verified repository-external bundle, and honest missing-JMC6K-bundle record. |

## 11. Stop gates

Stop and record evidence if the real-app browser harness would require operator credentials/data or
route interception of the behavior it claims to prove; if an enabled definition has no reachable
producer or consumer and fixing that would change product scope; if generated schemas cannot
express current responses without a backend design change outside JMC7B contracts; if unrelated or
concurrent history appears; or if completion would require push, activation, destructive operator
media work, hidden skips, weakened budgets, or unexplained baseline replacement.

## 12. Exit criteria

JMC7C is complete only when all owned findings and every regression discovered by its certification
are corrected, all 43 definitions and all advertised actions have executable evidence, the full
backend/frontend/browser/live-CPU matrix is zero-green with truthful unavailable-capability
accounting, current docs and runtime reachability agree, recovery material verifies, and the clean
tree is tagged `jmc7c-complete` for an independent release-readiness audit.
