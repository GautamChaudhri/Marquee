# JMC6H — Product Convergence, Retirement, and Final Certification

> **Post-certification note:** JMC6H remains the completed historical implementation record. The
> later independent source recheck found shared runner/progress/certification defects and a separate
> onboarding/learned-ranking redesign. Their authoritative successor plans are
> [`JMC6I`](jmc6i-runner-progress-and-certification-closure.md) and
> [`JMC6J`](jmc6j-taste-onboarding-and-residual-learning.md).

> **Implementer:** Read this document in full before changing code. Decisions are locked.
> Execute every internal phase continuously; phase boundaries are verification checkpoints, not
> stopping points. Stop only for a condition in
> [`design/plans/README.md`](../plans/README.md#implementer-continuity-and-stop-conditions)
> or after the complete plan, certification, and final-only history compaction succeed.

**Program:** Final two-part closure of the clean-slate PgQueuer job system

**Predecessor:** [JMC6G execution truth, progress, and evidence closure](jmc6g-execution-truth-progress-and-evidence-closure.md)

**Original poster/ML contract:** [JMC4C poster, ML, and certification](jmc4c-poster-ml-and-certification.md)

**Shared timeline:** `design/job-system-update/jmc6-final-closure-timeline.md`

**Recommended model tier:** **God**

## 1. Objective

Replace the canonical poster and ML placeholders with real product behavior, converge their
artifacts and downstream consumers on one authority, remove the remaining obsolete lifecycle code,
and certify every enabled definition from user action to visible product effect.

JMC6H is the final implementation plan for the job-manager redesign. It may not close by proving
only that handlers, rows, or JSON artifacts exist. Completion requires this chain for every enabled
definition:

```text
producer → canonical job → PgQueuer delivery → real domain operation → terminal outcome
→ durable projection/artifact → actual consumer → refreshed API/UI presentation
```

Any defect found by the final certification is fixed within JMC6H. It does not become a third
post-plan. Browser-agent acceptance instructions are written only after JMC6H and an independent
source once-over succeed.

## 2. Preconditions and stop gates

Before edits:

1. Verify annotated `jmc6g-complete`, exact compact commit/tree/base, recovery refs/bundle, shared
   timeline, zero-green gates, schema/generated-contract fingerprints, and every G handoff matrix.
2. Re-run the complete enabled-definition closure manifest and prove the JMC6G terminal, retry,
   timeout, configuration, progress, process/I/O, artifact, and retention invariants before poster
   or ML changes.
3. Inventory the real poster pipeline from provider enumeration/download through validation,
   deduplication, OCR/gates, feature extraction, scoring/ranking, `PipelineRun`, review, feedback,
   deployment submission, metrics, and batch behavior. Record every process, cache, artifact,
   database row, and frontend consumer.
4. Inventory the real movie/TV taste profile, taste map, enrichment, and learned-head build/load
   paths, namespaces, artifact formats, current loaders/caches, feedback inputs, activation paths,
   API consumers, and configured model dependencies.
5. Add failing product-effect tests proving the current canonical poster handler does not run the
   real pipeline and the current ML publications are not consumed by the actual scorer/loaders.
6. Use owned fixtures, disposable PostgreSQL/DATA_DIR, and fixed local test models. No test may
   fetch unbounded external data or mutate an operator library.

Stop if the real pipeline can only run through an uncontained process tree, if a model artifact
cannot be validated by the same loader used in production, if active-pointer updates cannot keep
the prior version on failure, if review/deployment identity cannot be linked to a canonical job, or
if a supposedly enabled capability lacks either a successful live smoke or an explicit disabled/
unavailable readiness state.

## 3. Locked decisions

| ID | Decision |
|---|---|
| H01 | The canonical poster handler executes the real Marquee poster pipeline. Hash-derived placeholder candidates, fixed scores, synthetic completed stages, and descriptive-only artifacts are deleted. |
| H02 | Reuse and refactor the established provider, download, validation, deduplication, OCR, gate, feature, scorer, and rank implementations. Do not create a second simplified ranking pipeline under `core/jobs`. |
| H03 | Heavy poster/ML Python runs in a fixed, non-arbitrary internal runner process launched and owned by the JMC6G process boundary. Its module/operation vocabulary, arguments, environment, workspace, and protocol are allowlisted. No caller can submit Python code, module names, command fragments, or physical paths. |
| H04 | The internal runner and all OCR/model multiprocessing descendants remain in the attempt's process group/cgroup. Cancellation, timeout, worker death, and stale delivery prove descendant death before release/publication. |
| H05 | The runner uses a bounded versioned control/progress protocol separate from ordinary stdout/stderr. Malformed/oversized frames fail safely; stdout/stderr still enter redacted attempt logs. Large candidate/model data moves through confined workspace/artifact keys, never JSON pipes. |
| H06 | One batch runner process may load OCR/features/models once and process a fixed sealed batch. It still emits per-child subject/stage evidence and cannot publish a child from the wrong fence. |
| H07 | Poster source enumeration is bounded and snapshotted. Downloads are capped, confined, checksummed, decoded, and registered or discarded according to policy. A descriptor is not a candidate artifact. |
| H08 | Every real gate and ranking stage records bounded counts/reasons and actual model/profile versions. A selected/recommended candidate references real bytes and a registered artifact. |
| H09 | Poster analysis remains non-deploying. Review/feedback may select a candidate, but library poster deployment/reset/restore occurs only through the certified canonical mutation definitions from JMC5/JMC6G. |
| H10 | `PipelineRun` becomes a canonical product projection linked to canonical job, attempt/fence evidence, subject snapshot, selected/recommended candidate artifact, and batch/correlation identity. It owns no execution lifecycle or retry authority. |
| H11 | Review queue, run detail, feedback, metrics, deployment submission, and movie/TV pages consume the canonical projection/artifacts. There is no parallel page-local run authority or unlinked legacy run row. |
| H12 | Movie, series, and season poster batches use the existing canonical parent/child model. Overall progress is sealed-child completion; current progress is each real child's subject/stage. Parent payloads remain bounded. |
| H13 | `taste_rebuild`, `taste_map`, `taste_enrich`, and `learned_head_train` invoke the actual trainer/builder/enricher used by Marquee. Descriptive JSON may accompany a run report but cannot substitute for a native loadable artifact. |
| H14 | ML inputs freeze namespace, feedback/example selection, feature/model versions, configuration, seed, base generation, and relevant library identity. Determinism is asserted where the algorithm permits it and limitations are documented. |
| H15 | ML outputs are written only in the attempt workspace, validated/checksummed with the production loader, registered immutably, then activated with fenced compare-and-set. Failure, timeout, cancellation, stale fence, or conflict preserves the prior active artifact. |
| H16 | `MlActivePublication` or its replacement is the sole active-version authority. Existing artifact registry, configured live paths, loaders, scorer caches, taste APIs, and feedback successor logic are migrated to that authority or removed. Two competing activation systems are forbidden. |
| H17 | Runtime consumers resolve active artifacts through a typed publication service using confined keys. Cache identity includes family/namespace/generation/checksum, and activation invalidation plus bounded repair prevents indefinite stale use across API and workers. |
| H18 | Activation success is not certified until the actual scorer/map/profile/head consumer loads the new artifact and produces an expected bounded fixture result. Loader compatibility is a product-effect gate. |
| H19 | Review reset changes review disposition only. Clearing/removing deployed artwork is a separately authorized canonical poster-reset job. No route may clear database poster state while intentionally leaving the deployed file unchanged. |
| H20 | Extract reusable pure letterbox/poster algorithms from dead managers, then delete the remaining manager singleton, detached warm task, dead heal/TV-scope/service/cache modules, retired re-encode stubs, and stale lifecycle comments/tests. |
| H21 | Remove clean-slate legacy runtime/artifact startup migration calls and their unused implementations. If an operator import tool remains useful, it is an explicit offline command and not automatic API startup behavior. |
| H22 | Static retirement checks prove no import, route, startup hook, singleton, dynamic lookup, string reference, or test fixture can start the retired lifecycle. Token-only checks are insufficient. |
| H23 | Create a checked-in behavioral closure manifest for every enabled definition. It names producer, handler, execution class, real effect, result, projection/artifacts, downstream consumer, progress, retry/timeout, cancellation, representative fixture, and certification test. |
| H24 | Every enabled definition must have exactly one producer/executor and a real observable effect or truthful no-change. A placeholder, unconsumed artifact, always-success stub, or presenter-only illusion blocks completion. |
| H25 | Live external/model/GPU/hardware checks are capability gates. A feature may complete unavailable/disabled with a precise readiness reason, but an enabled/available feature cannot be certified on synthetic registration alone. |
| H26 | Deferred browser authentication, public reset replacement, Docker hardening, webhook behavior, `radarr_upgrade`, and TV Dolby Vision conversion remain outside this plan. Their routes/capabilities stay absent or explicitly unavailable as previously decided. |
| H27 | JMC6H does not push, force-push, activate production schedules, mutate an operator library, or write browser-agent test instructions. It produces the evidence needed for the owner to authorize those later steps. |

## 4. Fixed internal-runner boundary

Extend the JMC6G tracked launcher with a fixed Marquee internal-runner capability. The executable
and module are code-owned; the operation is selected from a closed enum such as poster single,
poster batch, taste profile, taste map, enrichment, or learned head. Input is a versioned bounded
manifest referring only to attempt workspace or confined artifact keys.

The runner emits bounded frames for:

- liveness and human semantic stage;
- current subject and stable scope IDs;
- determinate/indeterminate progress observations;
- warnings and bounded metrics;
- produced confined files and a final typed result manifest.

The coordinator validates every output file, checksum, size, media/model format, expected subject,
and fence before artifact registration or projection/activation. A runner cannot write the
canonical database directly or publish a library destination.

## 5. Real poster product contract

The canonical flow is:

1. resolve immutable movie/series/season subject and bounded source request;
2. enumerate actual source candidates and download them into the workspace;
3. validate decode, dimensions, size, format, and source identity;
4. deduplicate bytes/perceptual content;
5. apply real OCR/text, style, quality, face/person, fan-art, and configured rejection gates;
6. extract the actual configured feature families;
7. score/rank with the active profile/head and record their versions;
8. register candidate thumbnails/originals and bounded reports as artifacts;
9. write the canonical `PipelineRun` projection linked to the job/attempt/batch;
10. return selected/recommended/review-required/no-change/failure with real evidence.

Test each gate independently and the complete sequence. Source unavailability, malformed images,
no surviving candidate, model unavailability, cancellation, timeout, and partial source failure
must produce truthful outcomes and remediation. Analysis must prove that no library artwork changed.

Review/feedback/deployment tests continue from the resulting real `PipelineRun`: review a real
candidate artifact, record feedback/lineage, submit the canonical deploy job, publish it, refresh
the library projection and page, and preserve the audit trail.

## 6. Real ML publication and consumption

Refactor the established native functions behind runner-callable interfaces that accept explicit
input/output locations and progress callbacks rather than mutating configured live paths. At
minimum cover:

- movie and TV taste profile rebuild;
- movie and TV taste-map construction;
- retained profile/map enrichment semantics;
- movie and TV learned-head training.

For each family, validate the produced artifact with the same production reader and run a small
known inference/query before activation. After compare-and-set activation, invalidate generation-
keyed caches and prove a fresh API/worker consumer resolves the new checksum. A stale process must
continue to fail its fence/activation check even if its file is valid.

Unify listing, status, map/profile retrieval, scoring, feedback-triggered successor submission,
manual rebuild, archive/delete policy, and Operations readiness on the same publication authority.
Remove mutable live-path writes and the old artifact registry once all readers have migrated.

## 7. Behavioral closure manifest

The manifest is code/test data, not a prose assertion. For every enabled definition it records:

| Field | Required proof |
|---|---|
| Producer | Concrete route, schedule, parent, or system action using canonical submission |
| Executor | One registered definition/handler and correct execution class |
| Product effect | Real analysis, projection, artifact, mutation, maintenance result, or truthful no-change |
| Terminal contract | All supported outcomes through JMC6G delivery |
| Evidence | Result, events, progress, logs, artifacts, subject, attempts, and remediation |
| Consumer | Exact API/service/frontend reader that observes the effect after refresh |
| Safety | Retry, timeout, cancellation, duplicate delivery, stale fence, and containment |
| Fixture | Representative owned deterministic input plus any honest live capability smoke |

Static discovery compares mounted routes, submission calls, registry definitions, scheduler
callbacks, and handler modules with the manifest. Behavioral tests execute the declared fixture and
assert the declared consumer. An entry cannot pass merely because its handler returned schema-valid
JSON.

## 8. Implementation phases

Every phase ends with focused tests, the complete zero-green backend suite, `ruff check marquee
tests scripts`, Alembic/model checks where affected, deterministic OpenAPI/type generation where
affected, frontend unit/check/lint/build/E2E where affected, `git diff --check`, one short lowercase
phase commit, and the shared timeline update. Continue immediately after a successful checkpoint.

### Phase H0 — verify JMC6G and freeze real product contracts

- Complete §2 and append to the existing shared timeline.
- Freeze real poster/ML functions, formats, consumers, current live-path/registry behavior, review
  and feedback flows, and all remaining legacy references.
- Add failing end-to-end product-effect tests for placeholder poster/ML behavior.
- Create the initial complete enabled-definition behavioral closure manifest.

### Phase H1 — contained internal runner and native protocol

- Implement the fixed operation/manifest/progress/result protocol through the tracked launcher.
- Certify process-group/cgroup ownership, descendant death, malformed protocol, log capture,
  progress freshness, timeout, cancellation, worker kill, output confinement, and stale fence.
- Adapt existing poster/ML functions to explicit workspace inputs/outputs without enabling new
  behavior until their family gate passes.

### Phase H2 — real single-subject poster pipeline and projection

- Replace the placeholder candidate/scoring handler with the real pipeline stages in §5.
- Link `PipelineRun`, candidate artifacts, canonical job/attempt, subject, and configuration/model
  versions through a forward migration if required.
- Migrate review/run/metrics/feedback/deployment consumers to canonical identity.
- Certify movie, series, and season single-subject flows before enabling availability.

### Phase H3 — real poster batches and review lifecycle

- Run fixed movie/TV batches through contained stage reuse and canonical children/parents.
- Preserve sealed overall progress, current subject/stage, partial outcomes, cancellation, retry,
  and bounded parent presentation.
- Correct review-reset semantics and prove the full analysis → review → feedback → canonical deploy
  or reset → refresh round trip without a parallel lifecycle.

### Phase H4 — native taste/profile/map/head publication and consumption

- Replace descriptive ML artifacts with real native outputs from §6.
- Converge active publication, loaders, scorer caches, taste APIs, feedback successors, listing,
  archive/delete, and readiness on one authority.
- Certify loadability, known inference/query result, activation conflict, rollback/preservation,
  cross-process invalidation/repair, cancellation, timeout, and stale activation.

### Phase H5 — legacy removal and clean-slate startup closure

- Extract any still-used pure helpers and delete the legacy managers, singletons, detached tasks,
  services, cache modules, stubs, comments, tests, and automatic startup migrations in H20–H22.
- Regenerate route/contracts and update design references that describe removed executable paths.
- Prove fresh startup/reset plus explicit offline import behavior, with no hidden lifecycle start.

### Phase H6 — complete behavioral and activation certification

- Execute §9 for every enabled manifest entry, including the complete JMC6G matrix.
- Run representative movie, show, season, episode, file, track, poster, model, batch, maintenance,
  retry, cancellation, failure, no-change, and recovery journeys through actual consumers.
- Perform available live model/tool/GPU smokes. Mark missing capabilities unavailable with exact
  readiness evidence; never certify an enabled capability that was not exercised.
- Fix every newly discovered in-scope defect within H6 and rerun affected plus complete gates.
- Close the shared timeline and perform §10 only when no finding remains.

## 9. Final acceptance and verification

The final system passes only when:

- every enabled definition has one manifest entry, one canonical producer, one executor, a real
  product effect or truthful no-change, durable evidence, and a verified downstream consumer;
- poster jobs use real downloaded candidate bytes, real rejection gates/features/scores, real
  registered artifacts, canonical `PipelineRun` linkage, and working review/deploy lineage;
- poster analysis never deploys, reset routes never create DB/filesystem disagreement, and poster
  mutations remain canonical/fenced;
- taste/profile/map/head jobs produce native loadable artifacts and the actual consumer resolves
  the activated generation/checksum across process/cache boundaries;
- failure/cancellation/timeout/conflict preserves the previous active model and prevents stale
  activation or duplicate publication;
- no placeholder stage, fixed score, descriptive substitute, unused active pointer, parallel
  registry, legacy manager, detached product task, automatic legacy startup migration, or dead
  executor path remains;
- every result passes terminal mapping, retry/timeout, configuration, progress, process/I/O,
  evidence, retention, presenter, API, batch, and refresh gates from JMC6G;
- queue/control API latency, event lag, logs/artifacts, PostgreSQL connections, advisory gates,
  worker fairness, and query/storage bounds hold during representative saturation;
- fresh database creation, reset, migration upgrade, backup/restore, PgQueuer verify, schema/model
  equivalence, OpenAPI/types, full backend, Ruff, frontend unit/check/lint/build/Playwright, and
  `git diff --check` are green with zero failure, skip, or `xfail`;
- the capability/readiness report distinguishes successfully live-smoked features from explicitly
  unavailable ones and contains no unverified success claim.

The implementer must perform a final independent static reachability scan after all tests pass.
Tests are evidence, not a substitute for inspecting mounted routes, startup hooks, registries,
process calls, filesystem writes, active artifact readers, and actual product consumers.

## 10. Mandatory final-only history compaction

Perform only after H6 is completely certified:

1. Verify a clean, linear, configured-author, JMC6H-only, unpushed range after exact
   `jmc6g-complete`. Stop for unrelated/concurrent commits, merges, uncertain ownership, or any
   plan-owned commit already pushed.
2. Commit the final pre-squash timeline entry with all phase hashes, closure manifest, full gates,
   live/unavailable capability evidence, deviations, operator work, pre-squash tip, certified tree,
   and intended tag `jmc6h-complete`.
3. Create timestamped recovery branch/tag and a verified repository-external Git bundle. Preserve
   all prior recovery refs and bundles.
4. Record the certified tree hash; soft-reset through RTK to the exact plan base and create one
   configured-author commit: `jmc6h: complete product convergence and certification`.
5. Prove exact tree identity, sole parent, clean worktree, recovery refs, and verified bundle.
6. Create annotated local tag `jmc6h-complete`; do not edit the timeline afterward.
7. Do not push, force-push, activate, or add agent/model/generator attribution.

## 11. Final owner handoff

Report the compact tag/hash/tree/base, recovery refs/bundle, migrations and fingerprints, complete
behavioral closure manifest, poster/ML native artifact and consumer versions, exact enabled/
unavailable/deferred capabilities, real fixture and live-smoke evidence, performance/resource
results, all gates, and every remaining operator action.

After an independent owner/Codex once-over confirms this handoff, the next steps are browser-agent
acceptance, operator activation smokes, remote push/CI verification, and selective capability
activation. Those are release gates, not another implementation plan.
