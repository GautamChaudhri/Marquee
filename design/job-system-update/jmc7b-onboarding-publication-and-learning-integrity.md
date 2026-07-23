# JMC7B — Onboarding, Publication, and Learning Integrity

> **Implementer:** Read this document in full before changing code. Decisions are locked.
> Execute every internal phase continuously; phase boundaries are verification checkpoints, not
> stopping points. Stop only for a condition in
> [`design/plans/README.md`](../plans/README.md#implementer-continuity-and-stop-conditions)
> or after the complete plan, certification, and final-only history compaction succeed.

**Program:** JMC7 release-readiness recovery

**Predecessor:** [JMC7A runtime control and execution safety](jmc7a-runtime-control-and-execution-safety.md)

**Successor:** [JMC7C Activity, contracts, and release certification](jmc7c-activity-contracts-and-release-certification.md)

**Architecture context:** [JMC6K correctness closure](jmc6k-personalization-and-onboarding-correctness-closure.md),
[poster pipeline](../poster-pipeline.md), and
[job progress/loading experience](job-progress-and-loading-experience.md)

**Shared timeline:** `design/job-system-update/jmc7-readiness-recovery-timeline.md`

**Recommended model tier:** **God**

## 1. Objective

Make the canonical personalization lifecycle true under real rejected candidates, retries,
failures, cancellation, no-change results, process crashes, profile updates, residual replacement,
artifact corruption, and consumer restart.

After JMC7B, onboarding exposes only objective survivors in the server-defined neutral order; one
user decision can recover through multiple canonical analysis/deployment successors; explicit
negative evidence changes profile construction; residual scheduling and held-out evaluation use
the exact frozen evidence and deployed replacement math; and readiness reports personalization
only after a real consumer has resolved and loaded valid publication bytes.

JMC7B relies on JMC7A's corrected cancellation, admission, retry, and process contracts. It does
not repair Activity client races or write the final browser suite; those are JMC7C responsibilities.

## 2. Audit findings owned by JMC7B

| ID | Severity | Finding at `jmc6k-complete` | Required result |
|---|---|---|---|
| 7B-C1 | Critical | Runner payloads serialize rejected and surviving poster candidates together in filename order; the onboarding archive labels all of them as survivors and takes the first 100. | The review resource contains only explicit objective survivors in the runner's canonical neutral order; rejected candidates remain diagnostics only. |
| 7B-C2 | Critical | Fixed onboarding idempotency and one decision→one deployment binding reuse terminal failed/cancelled jobs. Pending exemplars activate only on literal success, so retry, no-change, and crash-after-effect paths are unrecoverable. | Analysis and deployment use canonical successor lineage; decisions/exemplars reconcile every terminal and crash window without duplicate evidence. |
| 7B-C3 | Critical | Automatic residual scheduling supplies one feedback event ID where the worker expects a digest of the complete ordered event set, causing normal requests to supersede. Evaluation also treats a score that may include the old residual as the baseline for its replacement. | Scheduler and worker share one frozen evidence revision; candidate replacement scoring is evaluated from the weighted baseline and compared against both baseline and active compatible residual. |
| 7B-C4 | Critical | Publishers write their own consumer-reload checksum, while readiness trusts publication rows without artifact-kind/status/checksum/bytes/loader validation. Runtime may silently fall back while the UI says personalized. | Only a real consumer load through the production resolver can acknowledge reload; readiness and runtime share the same compatibility result. |
| 7B-H1 | High | Explicit positive and negative exemplars are snapshotted, but production profile construction drops the negative polarity. | Active negative evidence is a versioned profile input and measurably affects the native profile/runtime consumer. |

## 3. Preserved foundations and non-goals

Preserve canonical `PipelineRun` linkage, retained candidate artifacts, append-only preference
events, immutable exemplar/profile/residual artifacts, per-library profile coordination,
`MlActivePublication` compare-and-set activation, the weighted scorer as permanent baseline, and
JMC7A retry/cancellation/process behavior.

JMC7B does not add starter profiles, taste-test bundles, filesystem authorities, replacement heads,
implicit preference collection, automatic onboarding selection, hidden ranking in cold start, a
second scheduler, or a second artifact activation authority. It does not redesign the Activity UI
or keep handwritten frontend response models.

## 4. Locked decisions

| ID | Decision |
|---|---|
| 7B01 | Poster execution emits two structurally distinct products: a bounded diagnostic candidate ledger for evidence and an explicit reviewable-survivor list for user choice. A generic list plus inferred status is insufficient. |
| 7B02 | A reviewable survivor must have passed every enabled objective cold-start gate, have a valid registered candidate artifact, carry a stable opaque candidate identity, and appear in the exact server-computed neutral order. |
| 7B03 | Rejected candidates are never selectable and never copied into the review list. APIs may expose bounded rejection counts/reasons, but not rejected preview choices or hidden rank/score fields. |
| 7B04 | Neutral order is produced once from the eligible survivor set, persisted with a review revision/checksum, and preserved by the runner payload, archive service, API, and decision binder. Filename or artifact enumeration order may not replace it. |
| 7B05 | The decision binder revalidates survivor membership, objective eligibility, artifact status/kind/checksum/storage confinement, subject/run lineage, and review revision inside the decision transaction. |
| 7B06 | Onboarding is represented as a recoverable lineage, not a fixed idempotency key. A subject may have multiple canonical analysis attempts linked as successors; status points to the latest actionable attempt while preserving history. |
| 7B07 | One immutable choose/hate intent exists per run/review revision and idempotency key. Retrying its effect never creates a second preference event, decision, or pending exemplar. Conflicting intent remains a conflict. |
| 7B08 | A choose decision may own multiple canonical deployment jobs through explicit successor lineage. The decision stores current/latest job plus terminal history; a terminal failed/cancelled job is never reused as active work. |
| 7B09 | A pending positive exemplar remains pending and retryable after failed/cancelled deployment. It becomes active exactly once only after current poster bytes are post-effect validated against the chosen artifact. It becomes invalid/revoked only through an explicit conflicting decision, undo, subject retirement policy, or unrecoverable evidence failure. |
| 7B10 | `no_change` is success-like for exemplar activation only when validation proves the desired candidate bytes were already the deployed current poster. Any other no-change reason remains non-activating and exposes remediation. |
| 7B11 | Crash-after-effect recovery inspects durable deployment/result/artifact state and current poster checksum under the current fence. It may finalize and activate idempotently; it may never repeat an unproven destructive effect merely because terminal state is missing. |
| 7B12 | Onboarding analysis/deployment retry enters through JMC7A's shared retry command and domain coordinator. Routes do not invent separate retry endpoints or reset terminal jobs. |
| 7B13 | Profile snapshots preserve polarity, namespace, weight, artifact/embedding identity, and revocation lineage. The production builder consumes all active applicable positives and negatives exactly as frozen. |
| 7B14 | Native profile validation proves that negative exemplars are encoded in the loadable artifact and affect the same similarity/penalty path used by production scoring. A checksum-only test is insufficient. |
| 7B15 | Residual scheduling has one authority. Callers request reconciliation for a namespace; the coordinator locks, freezes the complete ordered eligible event set, derives its evidence revision/digest, and submits or coalesces the exact snapshot. Callers never fabricate a digest from one event ID. |
| 7B16 | Residual idempotency includes namespace, full evidence revision, active taste-profile generation/checksum, weighted-baseline signature, feature schema, and expected publication generation. Identical active/in-flight work may be reused; terminal failure creates a successor; stale inputs supersede truthfully. |
| 7B17 | Every exposure stores separately: normalized candidate inputs, weighted-baseline score/logit, active residual identity/delta when present, final deployed score, profile identity, and presentation order. The final score is never relabeled as the baseline. |
| 7B18 | Candidate residual training and evaluation compute the full replacement scorer as `weighted baseline + candidate residual` through the one production scoring primitive. They never add the candidate residual to a score already containing the old residual. |
| 7B19 | Activation requires subject-held-out improvement over the exact weighted baseline and non-regression/improvement over the currently active compatible residual on the same immutable held-out identities. Thresholds and tie behavior are definition-owned and recorded. |
| 7B20 | Train, validation, and final test partitions are durable report/artifact data containing exact subject IDs, event IDs/digest, seed, counts, metrics, and overlap proof. Activation cannot rely on ephemeral arrays or aggregate counts alone. |
| 7B21 | The publisher may validate and atomically activate an artifact, but it may not write a consumer reload acknowledgement. Publication and consumption are separate durable facts. |
| 7B22 | A registered consumer resolves the active publication through `resolve_active_publication` or its single successor, validates active row, artifact row/status/kind, confined bytes, size/checksum, metadata compatibility, and the actual production loader, then writes an acknowledgement containing consumer role/instance, generation, artifact checksum, load time, and result. |
| 7B23 | The acknowledgement is written only after the loaded object is installed in or successfully supplied to the real scoring consumer. A publisher-side test load is valuable validation but is not a consumer acknowledgement. |
| 7B24 | Readiness calls the same resolver/compatibility service used by runtime and requires current acknowledgements for both movie and TV profile consumers. Corrupt, missing, wrong-kind, stale-generation, loader-incompatible, or unacknowledged artifacts cannot produce `personalized`. |
| 7B25 | Runtime fallback to profile-free/weighted behavior is explicit in a durable diagnostic and readiness attention state. The API/UI may not claim personalized scoring for a namespace whose consumer rejected or could not load the active artifact. |
| 7B26 | A newer failed/unacknowledged publication attempt does not erase a still-valid prior active publication. Compare-and-set activation and rollback preserve the last known loadable generation, while readiness reports update attention separately. |
| 7B27 | JMC7B may add one forward migration after the JMC7A head for decision→successor lineage, consumer acknowledgement, or durable residual partitions. Prefer normalized constraints over overloaded JSON; do not resquash prior migrations. |

## 5. Canonical survivor and review contract

The internal runner result document must contain, at minimum:

- a versioned bounded diagnostic ledger with each candidate's stable identity, artifact identity,
  objective gate outcomes/rejection reasons, and evidence metadata;
- a separate `review_survivors` sequence containing only eligible candidate identities in the
  neutral presentation order plus the order algorithm/version and review checksum inputs;
- explicit counts for fetched, decoded, duplicate, resolution-rejected, style/objective-rejected,
  OCR-rejected, eligible, archived, and truncated candidates;
- enough artifact/checksum lineage to prove each survivor's bytes are registered and confined.

`build_run_payload`, the internal runner, `PipelineRun` persistence, `_archive_candidates`, the
review API, and `bind_onboarding_decision` must validate this schema rather than reconstructing
eligibility from filenames or missing fields. Truncation applies after survivor ordering and is
reported; it cannot promote rejected candidates.

## 6. Recoverable onboarding aggregate

Use the existing canonical models where possible, adding normalized lineage only where needed. The
status projection must identify:

- latest analysis job and its predecessor/successor history;
- terminal reviewable run/revision and bounded current actions;
- immutable decision and pending/active/revoked exemplar;
- latest deployment job plus all successor outcomes;
- post-effect validation result/checksum;
- per-namespace profile build/publication/reload attention inherited from the existing coordinator.

All status can be reconstructed after API/worker/PostgreSQL restart. No browser-local state is
required to find or retry work. Analysis and deployment submission transactions bind successor and
domain lineage only after canonical job creation succeeds.

## 7. Residual evidence and replacement evaluation

The residual coordinator owns an immutable input manifest. It selects eligible, non-revoked,
feature-complete, actually exposed events in deterministic order and records the ordered IDs and
digest. The runner request includes that manifest identity and all compatibility signatures. The
worker re-resolves and compares the same digest before training and again before activation; a
mismatch is supersession, not training on a different set.

For each held-out candidate, the common scoring primitive receives the weighted-baseline inputs
and either the active or candidate residual artifact independently. Reports compare:

1. weighted baseline alone;
2. current compatible active residual as a full scorer, when present;
3. candidate residual as a full replacement scorer.

The candidate must satisfy the locked activation thresholds against (1) and (2). Reports preserve
candidate-level outputs and subject-level aggregate metrics in bounded artifacts so a later audit
can reproduce the decision.

## 8. Publication and consumer acknowledgement

Create one typed consumer-resolution service rather than readiness-local validation. It returns a
validated loaded publication or a stable incompatibility reason. Every poster scoring entry point
uses it before supplying profile/residual artifacts to contained execution.

Activation publishes invalidation. A real registered consumer instance resolves, loads, installs,
and acknowledges. If the architecture uses on-demand contained runners rather than a resident
model cache, the server-side consumer that prepares the exact runner inputs must perform the load
through the production loader and acknowledge only after those inputs are ready for execution.
The publication handler itself cannot impersonate that consumer.

Acknowledgements are generation- and checksum-specific. Restart invalidates instance-liveness
claims until the replacement consumer reloads. Historical acknowledgements remain evidence but do
not certify a newer generation or a different instance.

### 8.1 Public contract deltas

| Method and path | Request authority | Target response/behavior |
|---|---|---|
| `GET /api/onboarding/status` | None beyond authenticated caller | Versioned readiness plus latest analysis/review/decision/deployment successor lineage, per-library publication/acknowledgement state, exact allowed actions, and stable remediation reasons. |
| `POST /api/onboarding/start` | Optional server-validated eligible subject intent | New/reused compatible nonterminal analysis snapshot. A terminal failed/cancelled analysis produces a canonical successor, never resurrection. |
| `GET /api/onboarding/runs/{run_id}/review` | Server-owned run ID | Only objective survivors in persisted neutral order, review revision, bounded rejection summaries, artifact-safe previews, and current decision/deployment state. |
| `POST /api/onboarding/choose` | Run, opaque survivor ID, review revision, idempotency intent | Immutable decision/pending exemplar plus latest canonical deployment snapshot. Repeated identical intent returns the same aggregate. |
| `POST /api/onboarding/hate` | Same server-owned review identity without a deploy choice | Immutable negative evidence and updated readiness; no deployment job or positive exemplar. |
| `POST /api/jobs/{job_id}/retry` | JMC7A shared command | Analysis/deployment/profile/residual successor when the concrete aggregate allows it; onboarding status discovers the successor. |
| `POST /api/onboarding/complete` | Intent only | Typed readiness result. It succeeds only with both valid current profile consumer acknowledgements; otherwise returns exact blocking/remediation state. |

JMC7B defines concrete Pydantic domain models for these results. JMC7C makes every route declaration
and frontend client generated/type-closed and removes remaining handwritten wire shapes.

## 9. Implementation phases

### Phase 7B0 — verify JMC7A and freeze real defects

1. Verify exact clean annotated `jmc7a-complete`, compact tree/base, external bundle, sole
   migration head, generated contracts, 43-definition inventory, and JMC7A gates.
2. Append the JMC7B baseline and exact active call graphs to the shared timeline.
3. Freeze the runner result schema, `PipelineRun` candidate archive, onboarding status/decision/
   deployment lineage, profile snapshot→builder inputs, feedback→residual coordinator inputs,
   scorer/evaluator primitive, publication resolver, and readiness projection.
4. Add failing tests with mixed accepted/rejected candidates, more than the review limit,
   failed/cancelled/no-change/crash-after-effect deployments, explicit negative exemplars, normal
   automatic residual scheduling, an already-active residual, durable partition identities,
   corrupt/missing/wrong-kind profile artifacts, and publisher-without-consumer acknowledgement.

### Phase 7B1 — survivor truth and neutral ordering

Implement decisions 7B01–7B05 and the canonical survivor contract. Update typed runner documents,
validation, persistence, artifact registration, review construction, and decision binding as one
vertical slice. Refuse legacy/ambiguous result documents rather than treating every candidate as a
survivor.

Prove rejection isolation across every objective gate, deterministic neutral ordering across
restarts, source diversity/tie behavior, truncation after eligibility, stale review conflict,
artifact corruption, and selection atomicity.

### Phase 7B2 — recoverable analysis, decision, and deployment

Implement decisions 7B06–7B12. Replace fixed terminal-job reuse with explicit successors. Reconcile
pending exemplars for succeeded, validated no-change, failed, cancelled, superseded, timeout,
stale fence, redelivery, and crash-after-effect paths. Add a bounded repair entry point that uses
canonical state and never becomes another scheduler.

Prove one decision/event/exemplar across repeated submissions and multiple successors, latest-work
status after restart, retry through the shared control command, choose-another/undo conflict rules,
and no readiness increment before validation.

### Phase 7B3 — polarity-complete profile construction

Implement decisions 7B13–7B14. Carry negative exemplars through the frozen revision and runner
codec into the native profile builder/loader. Preserve namespace applicability and bounded weights.
Retire positive-only filters from the production path.

Use deterministic embeddings to prove an explicit hate changes artifact content and the production
similarity/penalty for a targeted candidate while unrelated/opposite-namespace behavior remains
stable. Cover revocation, duplicates, limits, rebuild coalescing, rollback, and restart.

### Phase 7B4 — exact residual coordination and evaluation

Implement decisions 7B15–7B20. Centralize evidence revision construction, fix automatic scheduling,
separate baseline/active/final exposure data, call the exact production replacement primitive, and
persist partition identities/evaluation evidence.

Prove ordinary feedback schedules a non-superseded run without test-only digest overrides;
simultaneous feedback coalesces; stale profile/baseline/evidence supersedes; old and candidate
residuals are evaluated independently; overlapping subjects fail closed; candidate regression
preserves the active artifact; and a genuine held-out improvement activates by CAS.

### Phase 7B5 — real consumption and truthful readiness

Implement decisions 7B21–7B26. Remove publisher-authored reload state. Route all readiness and
runtime resolution through one validator/loader service and persist real consumer acknowledgements.
Make fallback and update attention observable without discarding a valid older generation.

Prove publish-before-consume, consume/ack, consumer restart, stale ack, corrupt bytes, wrong kind,
wrong namespace, wrong profile/baseline signature, missing artifact, loader failure, rollback, and
successful poster scoring with the acknowledged generation.

### Phase 7B6 — complete personalization lifecycle and handoff

1. Execute a fresh database lifecycle: neutral analysis with rejected candidates → review → choose
   → deployment failure → successor → crash-after-effect recovery → validated activation → 49→50
   → separate movie/TV profile build → real consumer load/ack → personalized scoring → positive
   and negative later evidence → generations 2 and 3 → coalesced residual training → held-out
   activation/no-change/rollback → API/worker/PostgreSQL restart.
2. Run focused suites, complete backend, Ruff, Alembic heads/current/check, OpenAPI/TypeScript
   generation, frontend check/lint/unit/build, existing Playwright/axe, and all available live CPU
   smokes in isolated storage. No result may regress from the recorded baseline. Keep the one
   inherited deterministic Activity screenshot failure red and unchanged for JMC7C; do not
   re-record it in JMC7B. Record unavailable GPU/operator capabilities precisely.
3. Run current-code reachability scans for positive-only profile builders, caller-built residual
   digests, publisher reload writes, readiness-local artifact shortcuts, and terminal onboarding
   job reuse.
4. Finalize timeline evidence and JMC7C handoff; create and verify recovery material; compact only
   the JMC7B range from exact `jmc7a-complete`; tag `jmc7b-complete`; do not push or activate.

## 10. Mandatory focused certification

| Area | Required executable proof |
|---|---|
| Candidate truth | Mixed survivors/rejections and >limit inputs yield only valid survivors in exact neutral order; every selection revalidates bytes and lineage. |
| Onboarding recovery | Failure, cancellation, validated/unvalidated no-change, timeout, stale fence, redelivery, crash after effect, API restart, and worker restart converge without duplicate evidence. |
| Negative profiles | Explicit hate is frozen, built, loaded, and changes production behavior; revocation removes it only in a later immutable generation. |
| Residual scheduling | Normal route feedback freezes the full ordered event digest and creates/reuses correct work without manual/test-only overrides. |
| Residual replacement | Candidate evaluation starts from the weighted baseline, compares full active and candidate scorers on identical held-out subjects, and persists exact partitions. |
| Publication truth | Publisher alone cannot satisfy readiness; only real resolver/loader consumption acknowledges. Corruption or incompatibility prevents personalized claims and triggers visible fallback. |
| Lifecycle | The full fresh→profiled→updated→residual→rollback/restart chain executes through public producers, real delivery, durable artifacts, consumers, and projections. |

## 11. Stop gates

Stop and record evidence if the runner cannot distinguish survivors without changing objective gate
semantics; if canonical candidate bytes cannot be retained/revalidated; if crash-after-effect state
cannot be proved without repeating an unsafe mutation; if current exposures lack enough bounded
inputs to reconstruct baseline and active residual separately and no forward-only correction is
possible; if a consumer cannot truthfully prove load/install; if an unrelated/concurrent commit
appears; or if operator data/media, remote push, schedule activation, or gate weakening would be
required.

## 12. Exit criteria

JMC7B is complete only when all five owned findings are corrected in production paths, the complete
personalization lifecycle passes with real successor and consumer behavior, compatibility failures
produce truthful fallback/readiness, no legacy shortcut remains reachable, every owned/backend gate
and available smoke passes with no regression beyond the exact inherited Activity screenshot
failure assigned to JMC7C, and the verified compact `jmc7b-complete` handoff is ready for JMC7C.
