# JMC6C — Zero-Green Certification, CI, and Activation Handoff

> **Implementer:** Read this document in full before changing code. Decisions are locked.
> Execute every internal phase continuously; phase boundaries are verification checkpoints, not
> stopping points. Stop only for a condition in
> [`design/plans/README.md`](../plans/README.md#implementer-continuity-and-stop-conditions)
> or after the complete plan, certification, and final-only history compaction succeed.

**Program:** [clean-slate PgQueuer migration](job-system-pgqueuer-migration.md)
**Companion fixes:** [reset-window miscellaneous fixes](job-system-miscellaneous-reset-window-fixes.md)
**Predecessor:** [JMC6B Projection Room and feature pages](jmc6b-projection-room-and-feature-pages.md)
**Shared timeline:** `design/job-system-update/jmc6-activity-and-certification-timeline.md`
**Recommended model tier:** **God**

## 1. Objective and completion meaning

Finish the final numbered job-manager chunk:

1. triage every inherited failing/obsolete test and reach one honest zero-failure local baseline;
2. certify the complete PgQueuer-only system through workload, saturation, fault, backup/upgrade,
   UI/reconnect, and live-media matrices;
3. modernize GitHub Actions only after that local baseline is green;
4. freeze the unreleased schema/API/client/test contracts and produce the activation-audit handoff.

`jmc6c-complete` means the target job manager and Projection Room are locally feature-complete and
certified for every capability actually exercised. It is **not** permission to activate an
untested optional tool/profile or a substitute for the owner's final once-over. Remote CI cannot
be claimed green until the owner authorizes a push and the workflow passes on GitHub.

The separately deferred browser authentication, public reset replacement, Docker hardening, and
webhooks remain outside the six-chunk completion claim.

## 2. Preconditions and stop gates

Before edits:

1. Verify annotated `jmc6b-complete`, its exact tree/sole-parent ancestry/recovery bundle, shared
   timeline, zero frontend warnings, full Activity/feature consumer manifest, generated contracts,
   and retained backend baseline.
2. Run the complete backend suite on an owned disposable PostgreSQL target and record exact test
   node IDs, failures, warnings, skips, `xfail`/`xpass`, duration, environment, binaries, and schema
   fingerprints. Do not rely only on JMC5's historical count.
3. Record frontend unit/E2E/check/lint/build, Ruff, Alembic/model equivalence, PgQueuer verify,
   OpenAPI/type drift, static safety, and current CI workflow baselines.
4. Create `design/job-system-update/jmc6-former-test-dispositions.md` during implementation and map
   every test from the original 31-failure inventory plus any later removed/replaced member to a
   verified `fixed`, `replaced`, or `removed-obsolete/deferred` disposition.

Stop rather than hiding a failure if a product contract is ambiguous, a destructive effect cannot
be proven safe, a required live capability is unavailable but still enabled, a test requires
operator media/data, or a remote-only CI result is being represented as locally verified.

## 3. Locked decisions

| ID | Decision |
|---|---|
| C01 | Completion requires zero backend test failures, zero frontend test/check/lint/build failures, zero `svelte-check` warnings, and clean generated/schema/static gates. No blanket `xfail`, quarantine, ignored job, warning suppression, assertion weakening, or test deletion merely to obtain green. |
| C02 | Existing capability-conditioned skips are not silently accepted. Run them in a certified environment where practical; otherwise record the missing capability and ensure its product definition/readiness remains unavailable. No new skip is introduced. |
| C03 | Triage order is product defect fix, canonical replacement test, then obsolete/deferred removal. A passing assertion must still prove the intended invariant. |
| C04 | Remove route-level webhook tests and any webhook-only fixtures because webhooks remain unsupported. Do not implement webhooks to satisfy old tests. |
| C05 | Preserve or strengthen coverage for fencing, cancellation/process death, staged publication, path confinement, backup/restore, progress/reconnect, subject history, idempotency, PgQueuer delivery/retry, and every destructive media boundary. |
| C06 | The original 31-test ledger remains the audit universe even though JMC5C reduced the live set to 17. Every original entry gets a final disposition and replacement link where applicable. |
| C07 | Current retained categories—development OCR labels, effective OCR hardware policy, run endpoints, taste artifacts, and Whisper catalog policy—must be classified from current source/test output, not mechanically changed to match historical expectations. |
| C08 | Complete local green is established before `.github/workflows/ci.yml` is rewritten. CI reproduces the local commands/contracts; it does not redefine or weaken them. |
| C09 | CI supports the repository-declared Python 3.12 and 3.13 versions, pins the verified Node version, uses `npm ci`, and runs against a health-checked PostgreSQL service with isolated credentials. |
| C10 | PgQueuer 1.1.1 durable install/upgrade/verify and Marquee fresh-schema equivalence/reset rehearsal are explicit CI gates. PgQueuer DDL remains outside Alembic. |
| C11 | CI runs backend Ruff/full tests, frontend unit/check/lint/build/E2E, deterministic OpenAPI/TypeScript regeneration, security/path/secret/static absence checks, and uploads sanitized failure reports only. |
| C12 | Workflow permissions stay read-only/minimal, PR/branch concurrency cancels stale runs, caches use official setup actions, secrets/media are never artifacts, and deferred webhooks/reset/Docker hardening are not falsely certified. |
| C13 | The implementer never pushes. Local workflow lint/config checks are recorded; the final owner-authorized push and GitHub run remain a named activation prerequisite. |
| C14 | Run the full workload/saturation/fault matrices on disposable infrastructure and confined generated media. Operator library/database/normal `DATA_DIR` are never used. |
| C15 | Required live media smokes use explicit owner-approved fixtures and versions. A missing `dovi_tool` or unsupported GPU/profile does not pass via mocks; the affected conversion stays readiness-disabled until a later witnessed smoke. |
| C16 | Target database plus `DATA_DIR` restore and PgQueuer upgrade rehearsals must preserve readable Queue/History/presentations/logs/artifacts and transport state. |
| C17 | If JMC6 changes Marquee schema, rewrite/squash the unreleased baseline and repeat fresh reset. If it does not, preserve the certified JMC5 baseline. After completion, all later migrations are treated as data-preserving first-release upgrades. |
| C18 | Final certification records exact enabled/disabled definition, schedule, entrypoint, capability, schema, OpenAPI, client, test, binary, resource, and operator-exception manifests. |
| C19 | Internal phases continue autonomously. Final history compaction occurs only after local system certification and the locally verifiable CI rewrite gates pass. |

Official GitHub guidance supports health-checked PostgreSQL service containers and `npm ci` with
`setup-node` caching; the replacement workflow follows those contracts rather than the current
Python-only job.
[GitHub PostgreSQL service containers](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers),
[GitHub Node.js build/test guidance](https://docs.github.com/en/actions/tutorials/build-and-test-code/nodejs)

## 4. Test triage and disposition ledger

The original measured reset-window baseline was 814 passed/31 failed. JMC5C ended at 1249 passed/
17 failed. The implementer must reconcile both historical inventories with a fresh JMC6C run.

For each original failure, record:

- exact original and current node ID;
- required product invariant;
- current canonical owner/module/route, or proof the behavior was deleted/deferred;
- classification: product defect, stale expectation, obsolete custom-runtime behavior, deferred
  webhook behavior, nondeterministic fixture, environment/capability issue, or duplicate coverage;
- disposition and code/test commit;
- replacement node IDs for removed/rewritten tests;
- verification commands and result.

Expected group treatment:

- development OCR-label tests: preserve real archive/current-run isolation and diagnostic
  correctness; fix nondeterminism/schema drift rather than weakening label evidence;
- effective OCR worker policy: align with the actual configuration/capability contract and test
  CPU/CUDA/forced modes deterministically;
- run endpoints: determine whether each endpoint is still a product route; migrate valid behavior
  to canonical job/action contracts and remove only truly obsolete run-manager expectations;
- taste artifacts: repair immutable active-version/profile normalization and evidence linkage;
- Whisper catalog: assert the intended hardware/model recommendation policy against explicit
  capability inputs;
- webhooks from the original 31: remove as deferred and record that no webhook route is certified;
- prior custom manager/cancellation/media-job failures: link their canonical JMC3-JMC5 replacement
  tests instead of restoring deleted modules.

Test cleanup also removes dead fixtures/imports/helpers and misleading old names, consolidates
duplicated expensive setup, and keeps tests deterministic and independent. It must not delete
useful regression evidence merely because the implementation moved.

## 5. Production-like certification

### 5.1 Definition and workload matrix

Exercise every enabled definition and every parent/schedule producer against applicable movie,
series, season, episode, media-file, track, poster, model, batch, integration, maintenance, backup,
and system subjects. Cover success, partial success, no-change/not-required, failure, retry,
cancellation, timeout, unsafe/quarantine, supersession, and subject retirement/deletion.

For disabled/reserved definitions, prove no executable route/schedule/handler path and verify
readiness/presentation explains the reason. `radarr_upgrade`/webhooks remain absent.

### 5.2 Saturation matrix

Simultaneously exercise CPU, certified GPU, media-read, media-write, network, maintenance,
PostgreSQL, PgQueuer scheduler/listener, Queue/History queries, multiplexed SSE clients, live log
tails, artifact downloads, snapshot repair, and hidden tabs. Record:

- class fairness, oldest eligible age, priority/deferred/retry behavior;
- API/control p50/p95/error rate and bounded query counts;
- event lag/buffer resets, log throughput/truncation, artifact/storage growth;
- per-role PostgreSQL connection/pool/advisory-lock budget;
- browser memory/request counts and Operations alerts.

### 5.3 Fault matrix

Inject worker, child, API, scheduler, listener, and PostgreSQL failure at claim, admission, start,
progress, retry, cancellation, validation, staging, backup, fsync, publish, product commit, and
transport acknowledgment. Include lock contention, full/read-only disk, missing/stale media,
source change, GPU loss, invalid duration, slow/disconnected client, and process PID/cgroup cases.

Prove no duplicate unsafe effect, conflicting file mutation, leaked process, stale-fence write,
lost terminal evidence, orphan detail/artifact, false cancellation, fabricated progress, or
unbounded reconnect/query loop.

### 5.4 Backup, upgrade, and restart

- create and verify a consistent target DB/`DATA_DIR` backup under the maintenance barrier;
- restore into a fresh target and verify schema/configuration/jobs/history/progress/events/logs/
  artifacts/models/subjects;
- rehearse PgQueuer install/upgrade/verify with queued, picked, deferred, retrying, held, and
  completed tickets;
- restart API/workers/scheduler/PostgreSQL and confirm liveness/readiness, Queue/History, event
  replay, log/artifact access, and canonical outcomes;
- verify code rollback by empty target reset and document the last safe commit/tag.

## 6. Live-media and user-experience certification

Use approved, backed-up, non-operator fixtures and record exact commands/tool/driver/media hashes:

- poster analyze/select/deploy/reset/restore with visual artifacts and backup;
- multi-track audio/subtitle scan/remux/generate/extract/embed/remove/reorder/metadata/restore with
  per-target rescans;
- letterbox detect/tag/re-encode/publish/revert/restore with dimension/HDR validation;
- Dolby Vision Profile 5/7 analysis/conversion/RPU/publish/restore only if real `dovi_tool`, media,
  and playback/metadata validation are available;
- cancellation during a long-running child with proven death;
- refresh initiating page and Activity during each smoke, clear local storage, drop/reconnect SSE,
  and verify the same subject-aware overall/current card plus logs returns;
- restart services/PostgreSQL and verify final History/evidence.

If a live capability cannot be performed, record it as unavailable and prove its readiness gate
prevents execution. The rest of the job system can be certified without pretending that optional
media transformation is ready.

## 7. GitHub Actions modernization

Only after a timestamped complete local zero-green result:

1. Pin the exact tested Node runtime in repository tooling and CI; retain Python 3.12/3.13 support.
2. Provision health-checked PostgreSQL with isolated non-production credentials on Linux runners.
3. Install project dependencies and pinned PgQueuer, then run Marquee migration plus external
   PgQueuer durable install/upgrade/autovacuum/verify against an owned CI database.
4. Run backend dependency check, Ruff, schema equivalence, complete retained/unit/integration/media
   fixture tests, and static safety/secret/obsolete-route/runtime assertions.
5. Use `npm ci`; run deterministic OpenAPI export/type generation and fail on diff; run frontend
   unit tests, zero-warning check, lint/format check, production build, and Playwright Chromium/
   axe tests.
6. Configure minimal permissions, concurrency cancellation, dependency caching through official
   setup actions, timeouts, and sanitized JUnit/Playwright/log artifacts on failure.
7. Validate workflow syntax/action references locally with a pinned linter where available. Do
   not claim a GitHub-hosted result before push.

Do not add Docker image hardening/build certification, webhook secrets, operator media, or broad
write permissions.

## 8. Implementation phases

### Phase C0 — verify JMC6B and establish the exact zero-green audit

Verify completion/recovery, create the disposition document, run all baselines, inventory every
failure/warning/skip/xfail/test helper/CI assumption/capability, and freeze completion manifests.

### Phase C1 — repair retained product failures and replace obsolete expectations

Fix genuine OCR/hardware/run/taste/Whisper defects; rewrite valid behavior against canonical APIs;
remove obsolete custom-runtime and deferred-webhook expectations with linked replacement coverage;
clean fixtures/helpers; update every ledger entry. End with the complete local test suite at zero
failures before proceeding.

### Phase C2 — complete workload and UI contract certification

Run every definition/parent/schedule/presenter/progress/action/subject/evidence and browser
reconnect/accessibility matrix. Repair defects found without weakening tests; repeat full green.

### Phase C3 — saturation, fault, backup, upgrade, and live-media certification

Run disposable saturation/fault/restart/restore/PgQueuer upgrade matrices and every available live
media smoke. Record unavailable capability gates explicitly. Repeat the complete local green suite
after all fixes.

### Phase C4 — modernize GitHub Actions after local green

Rewrite CI to reproduce the certified commands/environments, pin runtimes/actions, add PostgreSQL,
PgQueuer/schema/contracts/frontend/E2E/security gates, lint workflow configuration locally, and
rerun the complete local green baseline. Do not push.

### Phase C5 — freeze first-release contracts, final audit package, and compaction

Rehearse fresh reset and conditionally rewrite the unreleased Alembic baseline only if JMC6 changed
schema. Mark subsequent migrations as data-preserving. Finalize dispositions/manifests/operator
exceptions, run every local gate one final time, create the activation-audit checklist, then
perform section 10.

After each phase, commit, update the shared timeline, and immediately continue. A green phase is
not a reason to stop.

## 9. Final local exit gate

Before compaction, all must be true:

- complete backend suite: zero failures/errors/unexpected skips/xfail/xpass and no warnings hidden;
- all original 31 failures have auditable dispositions and replacement coverage where needed;
- Ruff and dependency checks pass;
- fresh Marquee schema/model equivalence, reset, PgQueuer durable install/upgrade/verify pass;
- OpenAPI and generated TypeScript reproduce with no diff;
- frontend unit tests, zero-warning `svelte-check`, lint/format check, build, Playwright/axe pass;
- every enabled definition has exactly one executor and completes its required matrices;
- no legacy runtime/schema/route/client/tracker/test dependency remains;
- backup/restore and restart preserve readable Activity/evidence;
- measured connection/query/event/log/storage/client budgets pass;
- CI configuration locally reproduces the command set and passes syntax/static validation;
- disabled capabilities and deferred auth/reset/Docker/webhook work are explicit;
- no operator database/media/normal `DATA_DIR` was touched without explicit approval.

The owner-authorized remote push and successful GitHub-hosted workflow are required before merge or
activation, but are not performed by this plan's agent.

## 10. Mandatory final-only history compaction

Perform only after C5 and the complete local exit gate succeed:

1. Verify a clean, linear, configured-author, JMC6C-only, unpushed range after exact
   `jmc6b-complete`; stop for merges, unrelated commits, uncertain ownership, or pushed history.
2. Commit the final pre-squash timeline entry with phase hashes, zero-green totals, disposition/
   executor/capability/schema/API/client/CI manifests, live smokes/exceptions, pending remote CI and
   owner audit, pre-squash tip, and intended tag `jmc6c-complete`.
3. Create timestamped recovery branch/tag and a verified repository-external bundle.
4. Record the certified tree; through RTK soft-reset to the base and create one configured-author
   commit: `jmc6c: certify pgqueuer job system`.
5. Prove exact tree identity, sole parent, clean tree, recovery refs/bundle; create annotated local
   tag `jmc6c-complete`.
6. Do not edit the timeline after compaction, push, force-push, delete recovery material, merge,
   or activate the system.

No agent/model attribution is permitted.

## 11. Out of scope and post-JMC6 activation gate

Still deferred:

- browser authentication/authorization/sessions/CSRF;
- replacement/removal of the public reset endpoint;
- Docker least-privilege/runtime hardening;
- Radarr/Sonarr/Subgen webhooks and `radarr_upgrade`;
- unsupported/unavailable Dolby Vision profiles or hardware paths.

After JMC6C, the owner performs the planned whole-system once-over, reviews the final disposition
and capability manifests, authorizes the push, requires GitHub Actions to pass, and only then
enables certified production schedules/definitions. That audit is a release/activation decision,
not a seventh implementation chunk.

## 12. Handoff

Report compact base/tag/hash/tree/recovery material; exact zero-green commands/counts/durations;
disposition document; schema/PgQueuer/config/OpenAPI/client/runtime versions; enabled/disabled
definitions/schedules/capabilities; resource/performance measurements; backup/upgrade/restart/live
smoke evidence; CI changes and local validation; deferred work; pending remote CI; and a concise
activation-audit checklist.
