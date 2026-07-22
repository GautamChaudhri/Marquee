# JMC6J — Taste Onboarding and Residual Preference Learning

> **Implementer:** Read this document in full before changing code. Decisions are locked.
> Execute every internal phase continuously; phase boundaries are verification checkpoints, not
> stopping points. Stop only for a condition in
> [`design/plans/README.md`](../plans/README.md#implementer-continuity-and-stop-conditions)
> or after the complete plan, certification, and final-only history compaction succeed.

**Program:** Two-part post-JMC6H readiness closure

**Predecessor:** [JMC6I runner, progress, and certification closure](jmc6i-runner-progress-and-certification-closure.md)

**Architecture context:** [Poster pipeline](../poster-pipeline.md) and
[job progress/loading experience](job-progress-and-loading-experience.md)

**Shared timeline:** `design/job-system-update/jmc6-runtime-and-personalization-timeline.md`

**Recommended model tier:** **God**

## 1. Objective

Replace Marquee's conflicting cold-start/onboarding authorities with one durable, canonical taste
collection workflow, then change the learned head from a replacement scorer trained mostly on
baseline mistakes into a bounded residual correction learned naturally from ordinary use.

The resulting product must behave coherently from a brand-new database:

1. With no active taste profile, the poster pipeline still fetches, validates, deduplicates, OCR
   filters, and quality-filters candidates.
2. It does not pretend to know the user's taste, rank candidates as recommendations, apply
   taste-dependent gates, or deploy anything automatically.
3. The user explicitly chooses and deploys posters for unique library subjects. Those confirmed
   choices become durable positive exemplars; explicit hate becomes negative evidence.
4. At 50 valid unique confirmed subjects, Marquee is eligible to build the first profile. The UI
   encourages 75 and shows 100 as the strong-coverage target, but 100 is not a hard storage cap.
5. Personalization activates only after the canonical build produces, validates, publishes, and
   reloads a native profile artifact. A count alone never marks onboarding complete.
6. The weighted taste-aware scorer remains the baseline permanently. A learned residual begins
   later through normal review/use, activates only when held-out evidence proves improvement, and
   can make only a bounded correction to the baseline.

This is the final implementation plan before owner browser acceptance and operator activation.
Any defect found by JMC6J's final in-scope certification is fixed within JMC6J rather than deferred
to another post-plan.

## 2. Revalidated current-state findings

The planning recheck confirmed these conflicts at `jmc6h-complete`:

- onboarding state/completion lives in mutable JSON under `DATA_DIR`, while active ML artifacts
  use canonical `MlActivePublication` rows;
- onboarding status tests configured profile/head filesystem paths instead of the active
  publication authority;
- onboarding start may enqueue a taste rebuild and poster batch together, and completion enqueues
  taste rebuild plus learned-head training together before the profile build has succeeded;
- `ensure_starter_profile()` copies a seed directly to a configured live path, bypassing canonical
  publication and making an unearned profile look ready;
- the bundled taste-test path and legacy training directories are a second exemplar authority;
- the contained poster runner always constructs `NumpyTasteStore("profile.npz")` and preflights it,
  so a truly fresh install cannot run the real pipeline without a fake seed;
- taste-dependent features and gates include CLIP k-NN, DINO k-NN, taste typicality, the off-style
  floor, and the taste-based aesthetic rescue; merely switching the final scorer is insufficient;
- `select_scorer()` chooses either `WeightedScorer` or `LearnedScorer`; a valid head replaces the
  weighted baseline rather than correcting it;
- pairwise training consumes v4 ranking events but derives only baseline inversions and
  contradictions, so ordinary agreement supplies no training signal;
- approve/override feedback and rank/hate interactions are split across legacy JSONL/filesystem
  stores, and route-local exemplar result lists are not the durable authority;
- movie and TV use separate profile/head namespaces, while the proposed mandatory first-run
  collection is movie-based.

JMC6J must inventory again from the compact `jmc6i-complete` base. Shared runner/progress safety
from JMC6I is reused, not redesigned.

## 3. Preconditions and stop gates

Before edits:

1. Verify annotated `jmc6i-complete`, exact compact commit/tree/base, recovery refs/bundle, shared
   timeline, zero-green gates, reserved-file comparison, progress matrix, and executable
   certification report.
2. Append the JMC6J baseline to the existing timeline: Git state/author, schema head, generated
   contract fingerprint, full backend/Ruff/frontend gates, active definitions, and capability
   smokes. Do not create a second timeline.
3. Inventory every onboarding route/service/file/config/frontend consumer, feedback event/write/
   undo path, exemplar/training directory, taste profile builder/loader/cache, gate and feature
   dependency, scorer/head trainer, publication family, retrain trigger, status/readiness endpoint,
   and movie/TV namespace.
4. Freeze owned deterministic poster images/features and a disposable database/`DATA_DIR` fixture
   for cold-start, profile-build, feedback, residual-training, publication, refresh, cancellation,
   and rollback tests. External TMDB/model/GPU checks remain explicit capability smokes.
5. Add failing end-to-end tests proving: a fresh install cannot currently analyze candidates
   without a profile; onboarding completion can race its profile/head jobs; the head replaces the
   baseline; ordinary approval/agreement produces no pairwise training evidence; and filesystem
   state can disagree with active publications.

Stop if a cold-start run can only be implemented by silently seeding preferences, if the selected
poster bytes cannot be durably retained under the filesystem boundary, if deployment success
cannot be tied idempotently to exemplar activation, if held-out evaluation cannot be split by
subject, if residual compatibility cannot be bound to its baseline/profile/feature schema, or if
the worktree gains unrelated/concurrent commits. Continue automatically across successful phases.

## 4. Locked product decisions

| ID | Decision |
|---|---|
| J01 | Onboarding builds taste profiles only. It does not train, require, or activate the residual learned model. |
| J02 | There is no starter/seed profile and no bundled taste-test authority. A fresh user supplies explicit preferences through real library subjects and real candidate posters. |
| J03 | The canonical database plus confined retained assets are the sole authority for readiness, exemplars, preference evidence, active profiles, residual models, and lineage. JSON state, JSONL labels, mutable training folders, and configured live artifact paths are retired. |
| J04 | Cold-start mode is derived by the server from compatible active-profile readiness. Clients cannot request or bypass it. |
| J05 | Cold start keeps only profile-independent work: bounded source fetch/download, decode/dimensions, exact/near dedupe, OCR/title validation, objective quality/aesthetic safety, and other explicitly profile-independent checks. |
| J06 | Cold start disables CLIP k-NN, DINO k-NN, taste typicality, off-style gating, taste-based aesthetic rescue, weighted taste ranking, residual scoring, recommendation labels, and auto-deploy. |
| J07 | Surviving candidates are shown in a neutral, source-diverse deterministic order labeled as choices, not recommendations. The order may not use hidden quality/taste scores as a ranking proxy. |
| J08 | A subject counts only after an explicit user selection is canonically deployed and post-deploy validation succeeds. Failed/cancelled deployment, duplicate subject, duplicate content, automatic selection, or merely viewing/ranking candidates does not increment readiness. |
| J09 | Default thresholds are 50 distinct confirmed subjects for build eligibility, 75 as the encouraged goal, and 100 as the strong-coverage target. They are configuration revisions, not frontend constants. One subject contributes at most one active positive readiness unit. |
| J10 | At the minimum, Marquee submits one idempotent profile build for the exact exemplar revision. More feedback may create a later coalesced revision; it cannot race or rewrite the in-flight artifact. |
| J11 | Completion means the active profile for the required families was built from the canonical exemplar revision, validated by production loaders/evaluation, atomically published, and successfully reloaded by a consumer. Count and job submission are insufficient. |
| J12 | One global visual-style exemplar base seeds both initial movie and TV taste profiles. Normal post-activation movie/TV feedback adds namespace-specific overlays. Profile inputs are `global + namespace`, while learned residuals remain namespace-specific. |
| J13 | Existing local posters may accelerate collection only after explicit user confirmation. Marquee never treats every existing, imported, or automatically deployed poster as a preference merely because it exists. |
| J14 | Explicit successful selection/approval is positive profile evidence. Explicit hate is negative profile evidence. An override creates strong comparative residual evidence but does not make the unchosen poster a negative exemplar unless the user explicitly hates it. |
| J15 | Automated pipeline picks never train the profile or residual by themselves. There is no self-reinforcing pseudo-label loop. |
| J16 | Taste profiles continue to learn after onboarding through deduplicated immutable evidence and coalesced immutable rebuilds. Direct in-place mutation is forbidden. |
| J17 | The hand-weighted, taste-aware scorer remains the baseline in every personalized run. The learned model outputs a bounded residual logit correction; it never replaces or bypasses the baseline. |
| J18 | Residual training uses natural explicit interactions, including baseline agreements and disagreements. Full reorder is strongest, override is strong, hate is strong negative evidence, and approval/selection supplies weak agreement against actually exposed alternatives. Unseen/filtered candidates create no preference. |
| J19 | Exposure position/order and candidate availability are recorded so implicit evidence can be down-weighted and audited for presentation bias. Neutral onboarding order is not used to train the residual. |
| J20 | Residual activation requires minimum evidence plus subject-held-out improvement over the exact compatible baseline. Train-set accuracy alone never activates a model. |
| J21 | A residual artifact is immutable and bound to feature schema, normalization version, baseline scorer/config version, profile generation/checksum, namespace, evidence revision, seed, and evaluation report. Incompatibility makes it dormant until retrained. |
| J22 | Residual correction strength and magnitude are bounded. A model cannot overturn hard gates, resurrect rejected candidates, or produce an unbounded score jump. |
| J23 | Training/retraining is coalesced after enough new distinct reviewed subjects/effective pairs, on a bounded schedule or explicit operator action—not inline on every feedback request. |
| J24 | Candidate failure, insufficient evidence, no held-out improvement, regression, cancellation, timeout, stale fence, or publication conflict preserves the current profile/residual and returns truthful no-change/failure evidence. |
| J25 | The pre-release project has no compatibility obligation for the old onboarding/head APIs or stored development data. Remove obsolete routes, settings, files, models, tests, generated types, and terminology rather than maintaining aliases. |

## 5. Canonical data and state model

Add a forward migration and typed models for the target authority. Names may follow repository
conventions, but the responsibilities may not be collapsed back into files.

### 5.1 `TasteExemplar`

One immutable preference asset/evidence projection containing:

- canonical ID and version;
- applicability: `global`, `movies`, or `tv`;
- positive or negative polarity and bounded evidence weight/source;
- subject kind/reference plus immutable movie/show/season snapshot;
- source `PipelineRun`, candidate artifact, selection/feedback event, deployment job/result, and
  initiator;
- retained confined physical asset key, checksum, perceptual hash, content type, dimensions, and
  embedding/model identity;
- lifecycle `pending_deploy`, `active`, `revoked`, or `invalid`, with timestamps/reason/lineage;
- configuration/evidence revision and optional supersession/undo linkage.

Candidate job artifacts normally expire. An active exemplar must therefore promote/copy the chosen
bytes through the filesystem-boundary/artifact service into a pinned preference retention class.
Retention cleanup may not delete bytes referenced by an active exemplar or active publication.
Neither APIs nor trainers receive physical paths.

### 5.2 `PosterPreferenceEvent`

One append-only database event stream replacing JSONL labels for learning. Each event stores:

- event/version, namespace, subject snapshot, run and candidate artifact identities;
- action: approval/selection, override, ranked tiers/order, hate, undo/revoke;
- exact exposed candidate set and presentation order;
- candidate normalized features, baseline scores/ranks, profile/baseline/residual versions, and
  bounded gate/effect metadata needed for reproducible training;
- explicit/implicit confidence, initiator, timestamp, and supersession/revocation lineage.

Store only bounded model inputs required for reproducibility; large images/reports remain artifacts.
Undo appends revocation/supersession—it does not rewrite history.

### 5.3 Derived readiness

Do not create another mutable completion flag. A typed service derives namespace readiness from:

- active non-revoked unique exemplars and diversity summary;
- current evidence revision digest;
- in-flight build job for that revision;
- compatible active `MlActivePublication` generation/checksum/source revision;
- last validation/reload result and precise failure/remediation.

Expose states such as `collecting`, `eligible`, `building`, `personalized`, and `degraded`. A user may
continue collecting in every non-failed state. Refresh/restart must reconstruct the same state from
PostgreSQL and active publications without local storage or JSON files.

## 6. Cold-start pipeline contract

Split feature/gate preflight into explicit profile-independent and personalized capabilities.
Absence of a taste profile is expected only when the server-derived mode is `collecting`; a missing
profile in a supposedly personalized run is a readiness fault, not silent fallback.

### 6.1 Profile-independent candidate curation

Cold start may use:

- bounded provider enumeration/download and source metadata;
- file/image safety, decode, dimensions, resolution, format, and corruption checks;
- exact and perceptual duplicate grouping;
- OCR/title matching, residual-text rules, and diagnostic evidence;
- profile-independent aesthetic/quality, face/person/composition, provenance, and language signals
  only when they are used as documented eligibility/safety gates rather than a personal rank;
- bounded artifacts, logs, rejection counts/reasons, and plain-language progress from JMC6I.

It must omit or neutralize every profile-derived feature/gate listed in J06. Candidate output records
`personalization_mode="collecting"`, `recommendation=null`, `scorer=null`, and an explicit message
that Marquee is filtering unusable posters but has not learned the user's preferences.

After objective gates, order candidates using a stable hash of subject/candidate identity with
source-family interleaving. Do not sort by source rank, aesthetic, model score, or hidden baseline.
Tests must prove permutation of provider input does not create a systematic first-source advantage
and that changing taste/head artifacts cannot change cold-start eligibility/order.

### 6.2 User journey

- A fresh user first connects/synchronizes a library. If no movie subjects exist, onboarding gives
  that precise next action; it cannot fabricate examples.
- The onboarding page chooses an unconfirmed, diversity-aware movie subject and submits the normal
  canonical poster-analysis job.
- Activity/shared progress shows fetch, filtering, OCR, and artifact stages plus the movie context.
- The result page displays neutral survivors and rejection explanations. There is no preselected
  winner or `recommended` badge.
- The user explicitly selects a poster. The existing canonical poster-deploy job performs backup,
  publish, rescan, and validation.
- In the fenced post-effect transaction, deployment success idempotently activates/promotes the
  pending positive exemplar and records the preference event. Failure/cancellation activates
  nothing.
- Explicit hate can register bounded negative evidence without deployment. It does not count toward
  the positive-subject threshold.
- The onboarding view recovers its exact active analysis/deploy/build jobs after refresh and links
  directly to Activity, logs, evidence, and retry.

Existing posters may be offered in a separate confirmation queue. Each needs an affirmative action;
bulk silent import is prohibited. Diversity status should report genre/era/source coverage as
guidance, not misrepresent it as learned certainty. Content/subject dedupe and per-subject caps are
hard invariants.

## 7. Taste-profile build, activation, and continued learning

At the first active revision with at least 50 distinct positive subjects:

1. atomically snapshot the exact active global exemplar IDs/checksums and negative evidence;
2. submit one canonical `taste_rebuild` parent/workflow for that revision, with movie and TV profile
   children built from the global base;
3. stage exemplar bytes through confined keys into runner workspaces;
4. build real CLIP/DINO/calibration/native profile artifacts with honest progress;
5. validate format, finite embeddings, dimensions, dedupe, expected count/revision, held-out k-NN/
   gate safety, and production-loader inference;
6. fenced compare-and-set both required active profile publications and record exact lineage;
7. invalidate/repair caches and prove API and worker consumers reload the published checksums;
8. derive onboarding `personalized` only after required publications and consumer reload succeed.

The initial movie choices are `global` because they express broad visual-poster preference and must
make the whole application usable. After activation, normal movie feedback contributes to
`global + movies` only as policy specifies, and TV feedback contributes to `global + tv`; profile
builders resolve deterministic global-plus-namespace inputs. Residual models never share movie/TV
training events.

Further confirmed selections/hates update the exemplar ledger immediately but rebuild profiles only
at a bounded coalescing threshold or explicit request. Use revision-derived idempotency keys. One
build may run per family; changes arriving during a build create a later revision rather than
mutating its snapshot. Failure leaves the prior active profile and readiness personalized with a
visible "new preferences pending" or degraded warning as appropriate.

## 8. Residual preference-learning architecture

### 8.1 Baseline plus bounded correction

Retire the replacement `LearnedScorer` selection model. The scoring contract becomes:

```text
baseline_score = WeightedScorer(features, active taste profile)
baseline_logit = logit(clamp(baseline_score, epsilon, 1 - epsilon))
residual_delta = compatible_residual(features, context)
final_score = sigmoid(baseline_logit + alpha * clamp(residual_delta, -delta_max, delta_max))
```

`alpha` and `delta_max` are versioned bounded policy values selected/validated during training and
capped by configuration. With no compatible active residual, `residual_delta=0`, so the result is
bit-for-bit the baseline ranking and explanation. The residual operates only on candidates that
already survived all hard gates.

Replace `SCORER=weighted|learned|auto` with an unambiguous pre-release contract such as
`SCORER=weighted|residual|auto`: `weighted` disables correction, `residual` requires a compatible
validated artifact, and `auto` uses it when compatible. Remove the old `learned` replacement mode,
aliases, UI copy, generated enums, and filesystem head path.

Per-candidate explanations show baseline score/contributions, bounded residual adjustment, final
score, active profile/residual versions, and why the residual was absent/dormant. Never describe a
small learned adjustment as the whole recommendation.

### 8.2 Natural evidence and weighting

Build subject-local preference pairs from actually exposed candidates:

- full explicit reorder/tier comparisons: strongest weight;
- explicit override chosen over prior auto pick: strong pair;
- explicit hate: strong surviving-candidate-over-hated pairs, bounded per subject;
- approval/selection of the displayed pick: weak agreement pairs against actually exposed nearby
  alternatives;
- ordinary explicit choice among neutral alternatives after personalization: medium/strong based on
  action specificity;
- untouched, unseen, filtered, download-failed, or non-exposed candidates: no pair.

Include both baseline agreements and disagreements. Normalize each subject's total contribution so
large candidate sets cannot dominate. Down-weight implicit agreement and top-position exposure.
Record full ordering/exposure so alternative counterfactual debiasing can be added later without
inventing historical impressions. Onboarding's neutral collection events train the taste profile
only and are excluded from residual training until a personalized baseline exists.

Train the residual directly against the baseline margin: the pairwise objective evaluates
`baseline_margin + bounded residual_margin`, not an independent absolute replacement score. Use
regularization, deterministic seeds, finite-value/schema checks, and subject-grouped splits.

### 8.3 Eligibility and held-out activation

Safe defaults are at least 25 distinct post-personalization subjects and 200 effective weighted
pairs per namespace. These are independent from the 50-subject onboarding threshold and may be
raised by versioned configuration. Retraining is considered after either 10 newly reviewed
distinct subjects or 100 new effective pairs since the last candidate, with a daily coalesced
schedule and explicit manual request. It never runs inline with feedback.

Partition training/validation/test by subject, not by pair. Compare the candidate residual against
the exact frozen weighted baseline on held-out evidence using at minimum weighted pair accuracy,
top-choice accuracy where an explicit top exists, and NDCG/order quality where full ranks exist.
Activation requires a configured minimum primary improvement, no material regression in explicit
high-confidence/subgroup checks, and bounded calibration/correction statistics. If confidence is
insufficient or the candidate does not improve, return `no_change` with the report and keep the
current residual/baseline.

Publish under a clear family such as `ranking_residual:{library}` and use a job definition such as
`ranking_residual_train`. Because Marquee is unreleased, remove the misleading
`learned_head_train` definition/family/API rather than retain a compatibility alias. Publication
uses the existing immutable artifact/CAS service and JMC6I runner safety. Activation records the
baseline config/profile/feature schema it corrects; any mismatch disables it until a new candidate
passes evaluation. Failed activation or regression preserves the prior compatible residual.

## 9. APIs, presentation, and frontend

Replace the old onboarding contract with bounded database-backed APIs. Exact route naming follows
the generated API conventions, but the product contract must provide:

- readiness state, active/pending exemplar counts, distinct subjects, thresholds, diversity
  guidance, active profile generations, current build/deploy/analyze jobs, failures, and next
  recommended action;
- bounded unconfirmed subject discovery and exact active-job recovery filters;
- cold-start analysis submission through the canonical job service;
- explicit candidate selection/deployment and hate/revoke/undo commands with idempotency and
  server-returned capabilities;
- profile-build status/lineage and residual evidence/eligibility/evaluation/publication status;
- no physical paths, raw PgQueuer IDs, mutable completion flag, or client-selected execution mode.

The onboarding page must be understandable without ML vocabulary:

- explain why choices are needed and that Marquee is not ranking yet;
- show `x of 50 required`, `75 recommended`, and `100 strong target`, unique-subject/dedupe rules,
  profile build/validation state, and precise recovery action;
- reuse the shared Activity/progress components for analysis, deploy, and profile build;
- show neutral candidate cards with OCR/eligibility explanations and explicit choose/hate actions;
- survive refresh, navigation, SSE loss, API restart, failed deployment, failed build, and retry;
- after activation, disappear as a blocking first-run flow while remaining available as taste
  management/history.

Projection Room presenters and Operations readiness must cover profile collection/build and
residual training/evaluation with subject/revision, evidence counts, baseline comparison, no-change
reason, activation/rollback, logs, artifacts, and remediation. Raw training data remains bounded and
redacted. Regenerate deterministic OpenAPI/TypeScript contracts and migrate every frontend caller.

## 10. Retirement

After all consumers migrate, delete:

- JSON onboarding state and its read/write/reset/complete service;
- starter-profile copy/build path and shipped seed assumptions;
- bundled taste-test builders/assets/routes/UI and mutable training-folder staging;
- filesystem profile/head presence checks and configured live artifact paths;
- JSONL feedback label authority and direct exemplar/negative-directory mutation;
- old rank-test thresholds/comments coupling head activation to onboarding;
- replacement `LearnedScorer`, `SCORER=learned`, inversion-only trainer behavior, and misleading
  learned-head job/publication/API names;
- stale tests, generated contracts, docs, settings metadata, and startup hooks for removed paths.

Static reachability tests must prove there is one onboarding/readiness authority, one exemplar and
preference event authority, one profile publication authority, one residual publication authority,
and no import/string/dynamic route/startup reference can revive a retired path.

## 11. Implementation phases

Every phase ends with focused tests, the complete zero-green backend suite, `ruff check marquee
tests scripts`, Alembic/model checks where affected, deterministic OpenAPI/type generation where
affected, frontend unit/check/lint/build/Playwright where affected, `git diff --check`, one short
lowercase phase commit, and the shared-timeline update. Continue immediately after a successful
checkpoint.

### Phase J0 — verify JMC6I and freeze personalization contracts

- Complete §3 and append the baseline/inventory to the existing timeline.
- Freeze cold-start objective gates, personalized features/gates, profile format/loaders,
  feedback/exemplar authorities, scorer math, active publications, namespaces, APIs, and UI.
- Add the failing fresh-start, race, replacement-score, ordinary-feedback, and authority-drift tests.

### Phase J1 — create canonical exemplar, preference, and readiness authority

- Add the migration/models/services from §5 with constraints, indexes, retention, revocation,
  revision digests, and subject-independent history.
- Implement pinned exemplar promotion through confined storage.
- Replace JSON/JSONL/training-folder writes behind tests, but do not activate the new user flow
  until J2/J3 gates pass.
- Certify concurrent selection, duplicate subject/content, undo, subject deletion, retention,
  checksum/path attacks, and refresh/restart derivation.

### Phase J2 — implement honest cold-start candidate curation

- Split feature/gate preflight and implement server-derived collecting mode from §6.
- Remove fake seed requirements and profile-dependent work from cold start.
- Produce neutral, deterministic, source-diverse survivor order and explicit no-recommendation
  results/presentations.
- Certify identical behavior with absent/different profile/head artifacts, provider-order
  permutation, all objective rejection stages, no survivors, cancellation, timeout, and refresh.

### Phase J3 — connect selection, deployment, onboarding UI, and profile activation

- Build the canonical analyze → choose → deploy → validate → exemplar sequence.
- Implement readiness/status/discovery APIs and the guided frontend.
- Submit revision-keyed movie/TV profile builds at 50 active unique positives; encourage 75/100.
- Certify build failure/cancel/retry, evidence arriving mid-build, CAS conflict, loader validation,
  cache invalidation, both namespace publications, and completion only after consumer reload.

### Phase J4 — implement continuing taste learning

- Make normal explicit feedback populate the same canonical event/exemplar authority.
- Apply global/namespace overlays, positive/negative policy, dedupe/diversity/caps, bounded rebuild
  coalescing, and undo/revocation.
- Prove auto picks never self-train, overrides do not create unrequested negative exemplars, prior
  profiles survive failure, and new active revisions change known fixture k-NN/gate behavior as
  expected.

### Phase J5 — replace learned replacement with residual ranking

- Implement §8's evidence builder, baseline-offset pairwise trainer, held-out evaluation, immutable
  publication, compatibility, bounded scorer, explanations, coalesced successor scheduling, and
  rollback.
- Rename/remove old learned-head product contracts; migrate presenters, API, settings, frontend,
  Operations, closure manifest, and generated types.
- Certify no-residual bit identity, bounded adjustments, agreements and disagreements, exposure
  weighting, subject split, insufficient/no-improvement no-change, real improvement activation,
  incompatible dormancy, cancellation, stale fence, redelivery, conflict, and prior-version safety.

### Phase J6 — retire old authorities and certify fresh-to-learned lifecycle

- Complete §10 and the independent static reachability scan.
- From a fresh disposable database/`DATA_DIR`, run: sync → 49 confirmed subjects (still collecting)
  → 50th successful deploy → real profile build/validation/publication/reload → personalized
  pipeline → ordinary feedback accumulation → residual candidate/evaluation → bounded activation
  → refresh/restart/rollback/undo.
- Cover existing-poster confirmation, duplicate selections, 75/100 guidance, movie and TV profile
  use, distinct namespace residuals, concurrent jobs, SSE loss, API/worker/PostgreSQL restart,
  process cancellation, artifact retention, backup/restore, and presentation/log evidence.
- Run the complete enabled-definition executable closure matrix, saturation/resource budgets,
  capability smokes, schema/reset/backup/upgrade, backend, Ruff, frontend, Playwright, generated
  contract, CI parity, and `git diff --check` gates.
- Fix every in-scope finding within J6 and rerun affected plus complete gates before §13.

## 12. Final acceptance

JMC6J passes only when:

- a truly fresh install runs the real objective poster pipeline without any taste/profile/head
  file, hidden seed, personal rank, recommendation, or auto-deploy;
- only explicit successful deployments add positive readiness units, hate adds negative evidence,
  and automatic outputs never self-train;
- 49 distinct positives remain collecting; the 50th makes an exact revision eligible; activation
  waits for successful native build, validation, publication, and real consumer reload;
- the UI accurately shows 50/75/100 guidance, current subject/jobs/failures, and survives refresh/
  reconnect/restart;
- global initial exemplars produce valid movie and TV profiles, with deterministic namespace
  overlays thereafter;
- profiles continue learning through canonical immutable evidence and coalesced rebuilds without
  in-place mutation or authority races;
- the weighted scorer is always the personalized baseline, no residual gives bit-identical baseline
  output, and a residual is bounded, compatible, explainable, and unable to bypass gates;
- natural explicit use produces correctly weighted evidence including agreements, while unseen and
  cold-start candidates produce none;
- held-out subject evaluation must beat the frozen baseline before activation; failure/no gain/
  incompatibility preserves the current baseline/residual;
- old onboarding state, starter/taste-test, JSONL/training-folder, live-path, replacement-scorer,
  and learned-head aliases are unreachable and removed;
- every enabled product definition passes executable producer-to-consumer certification;
- fresh schema/reset, upgrade, backup/restore, full backend, Ruff, generated contracts, frontend
  unit/check/lint/build/Playwright, resource/fault smokes, and `git diff --check` are green with zero
  failure, skip, or `xfail`.

## 13. Mandatory final-only history compaction

Perform only after J6 is completely certified:

1. Verify a clean, linear, configured-author, JMC6J-only, unpushed range after exact
   `jmc6i-complete`. Stop for unrelated/concurrent commits, merges, uncertain ownership, or pushed
   phase history.
2. Commit the final pre-squash timeline entry with phase hashes, migrations/fingerprints, executable
   closure and fresh-start lifecycle reports, full gates, capability evidence, deviations,
   operator work, pre-squash tip, certified tree, and intended tag `jmc6j-complete`.
3. Create a timestamped recovery branch/tag and verified repository-external Git bundle. Preserve
   all earlier recovery refs and bundles.
4. Record the certified tree hash; soft-reset through RTK to the exact plan base and create one
   configured-author commit: `jmc6j: replace onboarding and add residual learning`.
5. Prove exact tree identity, sole parent, clean worktree, recovery refs, and verified bundle.
6. Create annotated local tag `jmc6j-complete`; do not edit the timeline afterward.
7. Do not push, force-push, activate schedules, mutate an operator library, or add agent/model/
   generator attribution.

## 14. Final owner handoff

Report the compact tag/hash/tree/base, recovery refs/bundle, migrations and generated fingerprints,
retired authorities, canonical exemplar/evidence/readiness contracts, cold-start gate matrix,
movie/TV profile lineage, residual math/compatibility/evaluation thresholds, executable closure
report, fresh-to-learned end-to-end evidence, full gates, live/unavailable capabilities, and exact
operator actions.

After an independent owner/Codex once-over confirms this handoff, the next steps are browser-agent
acceptance, operator-owned live media/model/GPU smokes, remote push plus GitHub workflow
verification, and selective production activation. They are release gates, not another construction
plan.

## 15. Research basis

The residual-learning design follows the pairwise ranking principle introduced by
[RankNet](https://www.microsoft.com/en-us/research/publication/learning-to-rank-using-gradient-descent/),
retains explicit/implicit confidence distinctions consistent with
[Bayesian Personalized Ranking](https://arxiv.org/abs/1205.2618), and records exposure needed to
reason about position bias rather than treating unobserved candidates as negatives, as discussed in
[counterfactual learning-to-rank research](https://pmc.ncbi.nlm.nih.gov/articles/PMC7148247/).
