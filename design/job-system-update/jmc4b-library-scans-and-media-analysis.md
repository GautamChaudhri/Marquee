# JMC4B — Library, Scans, and Media Analysis

**Program:** [clean-slate PgQueuer migration](job-system-pgqueuer-migration.md)  
**Architecture:** [direct PgQueuer adoption](job-system-pgqueuer-direct-adoption.md)  
**Progress contract:** [job progress and loading experience](job-progress-and-loading-experience.md)  
**Previous plan:** [JMC4A producers, batches, and schedules](jmc4a-producers-batches-and-schedules.md)  
**Next plan:** [JMC4C poster, ML, and certification](jmc4c-poster-ml-and-certification.md)

> **For the implementing agent:** Read `AGENTS.md`, `CLAUDE.md` when present,
> `design/plans/README.md`, `design/plans/04-television-backend.md` §0, all JMC1–JMC3
> plans/timelines, JMC4A, the complete shared JMC4 timeline, and this document **in full**
> before changing code. Decisions are final. **Verify in code** means inspect the current
> implementation and all references; do not transplant legacy handlers into the new kernel.
>
> **Shared JMC4 timeline:**
> `design/job-system-update/jmc4-nonmutating-jobs-timeline.md`. Verify `jmc4a-complete`
> resolves to the compact JMC4A commit, compare its tree and handoff with the timeline and
> Git, then append to the same timeline after every phase commit. Never create another JMC4
> timeline.
>
> **Git authorship:** use only the repository's configured Git user. Never add yourself, a
> model, or an assistant as author, co-author, contributor, or generator. No
> `Co-Authored-By`, “Generated with,” model-name, or assistant-name attribution is allowed.

**Goal:** Migrate library synchronization, audio/subtitle inventory, policy audit, letterbox
detection, and Dolby Vision analysis onto the canonical JMC4A producer/batch/schedule layer and
the JMC3 execution/evidence kernel without permitting any source-media mutation.

**Ordering:** second of three JMC4 plans. JMC4A must be compacted and certified. JMC4C must not
start until JMC4B is compacted to one verified commit and tagged `jmc4b-complete`.

## 1. Preconditions and stop gates

Before implementation:

1. Verify `jmc4a-complete`, its sole compact commit, recovery handoff, schema, canonical single/
   bulk submission, batch projection, scheduler catalog, entrypoint registration, and exact
   retained baseline. Stop if only the timeline claims completion.
2. Recreate an owned disposable PostgreSQL database and re-run JMC4A's producer, batch,
   schedule, delivery, readiness, and no-non-noop gates.
3. Re-inventory all current handlers and routes for `library_sync`, `subtitle_scan`,
   `subtitle_scan_all`, `audio_subs_deep_scan`, subtitle policy audit, letterbox detection,
   Dolby Vision analysis, their parent types, synchronous loops, detached tasks, subprocess
   calls, configuration reads, filesystem paths, and frontend API consumers.
4. Freeze representative current domain results for movie, series, season, episode, shared
   episode file, media file, audio/subtitle tracks, letterbox state, Dolby Vision state, and
   missing/retired subjects. These are migration fixtures, not legacy lifecycle contracts.
5. Verify real installed ffprobe/FFmpeg/dovi tooling capabilities in the test environment.
   Unit tests use fixed fake tools; live-media tests use owned fixtures only.
6. Record branch/HEAD, exact JMC4B plan base, configured author, full pytest/Ruff baseline,
   schema/contracts, frontend gates, available tool versions, and pending operator work.

Stop if JMC4A is incomplete, a target route can still run inline or through a legacy writer, a
handler requires direct access to an ORM `Job`, or analysis cannot be separated from media
mutation. Preserve unrelated work. A missing optional native tool may defer its live smoke but
cannot weaken deterministic adapter tests.

## 2. Locked decisions

| ID | Decision |
|---|---|
| B1 | Every migrated handler is registered in the canonical definition registry and dispatched only by its declared PgQueuer entrypoint through the JMC3 kernel. |
| B2 | Handlers accept immutable `ExecutionContext` capabilities and typed domain input. They never receive or mutate the canonical `Job` ORM row, open a legacy manager lifecycle, or choose execution policy. |
| B3 | Replace `cancel_registry`, `job_manager.emit`, `media_job_manager`, page progress bridges, detached tasks, and direct subprocess APIs in migrated paths with JMC3 cancellation, progress, event, log, artifact, launcher, workspace, and fenced result services. |
| B4 | Derived database projections may be updated transactionally, but source media, embedded tracks, sidecars, poster files, crop metadata, and Dolby Vision essence are never changed in JMC4B. |
| B5 | Library synchronization marks missing live projections retired; it does not physically delete them or invalidate immutable job history. |
| B6 | A single `subtitle_scan` is a read-only media inspection child. Scan-all and deep-scan are canonical parent batches, not handlers that create `MediaJob` rows. |
| B7 | Add `subtitle_policy_audit` as a typed read-only job. `subtitle_policy` remains the disabled mutating operation for Chunk 5. |
| B8 | Letterbox detection stores derived observations only. `LETTERBOX_AUTO_APPLY_HIGH` is ignored for execution until Chunk 5; no detection path may enqueue or call apply/re-encode/heal. |
| B9 | Dolby Vision analysis probes and stores typed derived state only. It cannot invoke conversion, stage converted media, or publish a replacement. |
| B10 | Batches use JMC4A parent projection. Overall progress is sealed child completion; current progress is the most specific show/season/episode/movie/file/sample stage and may reset only with a new scope ID. |
| B11 | Missing optional totals are indeterminate. Tool output is parsed only through declared adapters; no frame-rate-derived or guessed percentage/ETA is allowed. |
| B12 | Every family has strict request/result/error versions, subject builders, configuration keys, stages, retry classifier, presenter golden, action policy, and no-change reason before enablement. |
| B13 | Transient upstream/network/tool availability failures request definition-owned retry. Invalid input, missing stable subject, unsupported media, and corrupt permanent output are permanent. Cancellation is never classified as failure. |
| B14 | A target endpoint returns a bounded canonical submission response with job ID, disposition, phase, snapshot link, and detail link. It never returns a legacy job ID or numeric PgQueuer ID. |
| B15 | Schedule callbacks for library sync and deep scan become product-active only after their complete handler family is certified. Manual and scheduled jobs use separate occurrence/idempotency scopes. |
| B16 | `radarr_upgrade`, webhooks, healing, conversion, generation, track mutation, apply/re-encode, and all destructive maintenance remain dispatch disabled. |
| B17 | Narrow frontend contract adaptations are allowed to keep initiating pages functional. Shared visual progress cards and Projection Room redesign remain Chunk 6. |
| B18 | JMC4B is squashed only after complete certification, recovery backup, and exact tree verification. No agent pushes. |

## 3. Handler boundary

Define a narrow domain-handler interface equivalent to:

```python
async def execute(context: ExecutionContext, request: RequestV1) -> ResultV1:
    ...
```

The context exposes immutable request/configuration/subject identity plus:

- cooperative cancellation and deadline checks;
- typed progress observations and current-subject changes;
- attempt-scoped structured logging;
- confined workspace/artifact publication;
- allowlisted tracked process execution;
- database session factory for domain projections only;
- fenced terminal result/error authority owned by the delivery kernel.

Handlers return typed domain results or raise typed retry/permanent/cancel exceptions. They do not
commit canonical job lifecycle, acknowledge PgQueuer, write raw event rows, or mark themselves
terminal. Domain projection transactions must be idempotent for the canonical job/fence and must
not hold a database transaction while awaiting external networks or long-running tools.

Every migrated direct executable is either routed through the tracked launcher or proven to be an
in-process pure/read-only library call. Blocking CPU work uses a bounded worker thread/process path
without bypassing cancellation or worker concurrency.

## 4. Library synchronization

Migrate `library_sync` to the `network` entrypoint with typed scope and result documents.

Required behavior:

- independently report configured/skipped Radarr, Sonarr, and TMDB sources;
- fetch outside write transactions, then apply bounded transactional pages;
- preserve stable live identities and immutable enqueue subject/configuration snapshots;
- mark absent movies, series, seasons, episodes, and media projections `is_present=false` with
  retirement time; do not physically delete historical evidence;
- distinguish added, updated, unchanged, retired, revived, skipped, warning, and failed counts by
  subject kind/source;
- preserve successful source results if another optional source is unavailable and report partial
  warnings honestly;
- emit human stages such as connecting, fetching movies, fetching series, reconciling seasons,
  reconciling episodes, refreshing metadata, and finalizing;
- use item/page totals when supplied by the upstream API; otherwise use indeterminate phases and
  count-so-far;
- use the canonical configuration snapshot and environment-owned credentials without logging or
  persisting credentials.

Manual sync routes submit one job and return 202. The periodic callback uses the JMC4A UTC interval
bucket. Overlap resolves through an active semantic idempotency scope; it does not start concurrent
full-library syncs. Scheduled recurrence may create a later job only after the prior scope is no
longer active or the definition's overlap rule explicitly permits it.

## 5. Audio and subtitle inventory

### 5.1 `subtitle_scan`

- Resolve one immutable media-file snapshot, including movie/series/season/episode context and
  shared-file relationships.
- Use the confined media boundary and tracked ffprobe adapter; never accept a caller path.
- Inventory embedded audio/subtitle streams and permitted external sidecars without modifying them.
- Normalize language, codec, channels, title, disposition/default/forced/HI flags, embedded/external
  state, and file signature into typed results.
- Atomically upsert inventory only after a complete successful probe; retain the prior valid
  inventory on probe failure/cancellation.
- Return `no_change` when the complete inventory and signature are unchanged.
- Use indeterminate probe progress plus determinate stream/sidecar processing when totals exist.

### 5.2 `subtitle_scan_all` and `audio_subs_deep_scan`

Replace legacy fan-out with JMC4A fixed batches:

- parent request fixes scope, force/staleness policy, series/season filters, and selection time;
- child candidates are selected in bounded pages and receive immutable subject snapshots;
- the parent, complete selected child set, and tickets commit atomically;
- force=false excludes valid fresh inventory; no candidates produces parent `no_change`;
- deep scan reads its enqueue-time batch limit and schedule provenance;
- cancellation cascades to children without cancelling unrelated manual scans;
- parent detail reports selected, scanned, unchanged, failed, cancelled, skipped, and stale counts.

### 5.3 `subtitle_policy_audit`

Add a definition and canonical route for long policy dry-runs. Snapshot the policy version/content,
selected subject scope, and relevant configuration at enqueue so later policy edits do not change
the result. Evaluate existing inventory only; do not create mutation plans or apply changes.

Return bounded totals for proposed removals, protected tracks, review-required subjects, coverage
before/after, warnings, missing inventory, and unavailable media. Keep a compact result in the job
row and register the full sanitized per-subject report as a downloadable artifact when necessary.
No raw path, provider credential, or unbounded track document may enter list/presentation payloads.

## 6. Letterbox detection

Migrate:

- `letterbox_detect` for one movie/file;
- `letterbox_detect_episode` for one physical episode file/shared episode group;
- `letterbox_detect_tv_scope` for a series, season, or episode scope;
- `letterbox_detect_batch` and `letterbox_detect_tv_batch` as canonical parents.

Use the tracked launcher for ffprobe/FFmpeg/ImageMagick tools and the confined media path. Store
typed derived observation/state only after complete validation. Record source dimensions/aspect,
sampling scope, sample positions/count, detected crop, confidence, variability/uniformity,
classification, backend, warnings, and no-change/not-required reasons.

Progress separates:

- sealed overall movies/files/episodes/shows completed;
- current show → season → episode/file subject;
- current sample window and named probe/detection/validation stage.

An episode group that shares one physical file counts as one physical child but presents all
affected episode identities. A current-stage reset changes `scope_id`; overall never resets between
shows or seasons.

Explicitly sever or guard any old automatic path from detection to crop tagging, apply, re-encode,
revert, or healing. Static tests search migrated call graphs and runtime tests enable the old
auto-apply setting while proving no mutation call or media-write ticket occurs.

## 7. Dolby Vision analysis

Migrate `dovi_analyze` and `dovi_analyze_batch` to `media_read` plus ticketless parent aggregation.

- Snapshot movie/episode/file identity, file signature, source codec/HDR metadata, and requested
  analysis depth.
- Run allowlisted ffprobe/dovi tooling through JMC3 launcher with bounded sanitized evidence.
- Report Dolby Vision presence, profile/level, compatibility ID, RPU/EL/BL observations, HDR base,
  codec, bit depth, color metadata, supported/unsupported analysis, warnings, and validation.
- Preserve the last valid derived state on failure; update it only when the source signature and
  fence still match.
- Treat already-current analysis as `no_change`.
- Use outer subject counts and named indeterminate tool phases. Do not invent an analysis ETA.

Static and runtime tests prove no import/call/enqueue path reaches conversion, FFmpeg encode,
staged media publication, backup, or replacement.

## 8. API and generated contracts

For every migrated initiating route:

- validate domain intent and subject existence before submission where that check is cheap/stable;
- return 202 `JobSubmission` with `job_id`, `created|reused`, phase, snapshot/detail URLs, and
  optional parent/child count;
- use 409 for semantic idempotency conflicts and canonical command conflicts;
- keep logs/artifacts/detail discoverable through the existing JMC2/JMC3 job APIs;
- remove per-job legacy SSE and legacy media-job IDs from the migrated contract;
- regenerate deterministic OpenAPI and static TypeScript types;
- update only affected frontend API calls and null-safe acceptance handling.

Routes for unmigrated mutations remain explicitly fail closed with the established stable error;
do not make them appear successful or silently synchronous.

## 9. Implementation phases

Every phase ends with focused tests, full retained pytest comparison, `ruff check marquee tests`,
schema/generated-contract checks, affected frontend check/lint/build, `git diff --check`, a short
lowercase phase commit, and a shared timeline update.

### Phase B0 — verify JMC4A and freeze domain contracts

- Complete §1 and add static/runtime inventories proving exact target and deferred types.
- Freeze representative source/domain results and legacy bypass call graphs before replacement.

### Phase B1 — execution-context handler adapters and schemas

- Finalize typed requests/results/errors/configuration/stages/retry/presenters for all target types,
  add `subtitle_policy_audit`, and build shared domain-handler adapters.
- Keep all new target definitions disabled until their family gate passes.

### Phase B2 — library synchronization and schedule

- Migrate `library_sync`, its routes/presenter/progress/evidence, and enable manual execution.
- Certify then activate the periodic sync callback, including overlap and partial-source behavior.

### Phase B3 — subtitle inventory, scan batches, policy audit, and schedule

- Migrate `subtitle_scan`, scan parents, deep scan, and policy audit; remove legacy media-job fan-out
  from these paths.
- Certify then activate configured hourly deep scan.

### Phase B4 — letterbox detection families

- Migrate movie/episode/TV detection and both parent families with nested progress and no automatic
  mutation path.

### Phase B5 — Dolby Vision analysis and JMC4B certification

- Migrate single/batch analysis, run all per-family and cross-family gates, verify exact enabled/
  disabled manifest and schedule catalog, then perform §11.

## 10. Acceptance matrix

For every enabled definition prove success, `no_change`, permanent failure, transient retry delay,
cancellation, timeout, duplicate delivery, terminal redelivery, stale fence/sequence, missing or
retired subject, configuration determinism, logs/artifacts/events, presenter golden, and direct API
submission/recovery.

Additionally prove:

- library partial-source reconciliation, retirement/revival, pagination, overlap, and credential
  redaction;
- subtitle scan atomic inventory replacement, sidecar confinement, shared-file subjects, stale and
  forced batch selection, empty batch, and full audit artifact bounds;
- letterbox movie/show/season/episode transitions, sample scopes, indeterminate tools, stable parent
  progress, and auto-apply disabled under every configuration;
- Dolby Vision profile fixtures, absent/unsupported/corrupt inputs, stale signature, batch partial
  failure, and zero conversion/media-write calls;
- scheduled/manual idempotency separation and schedule disable/re-enable;
- bounded parent/child/failure queries and database/worker connection use under concurrent scans;
- all migrated routes contain no legacy creation/emission/detached execution;
- every deferred destructive type remains disabled and fail closed;
- no new failure, error, skip, or `xfail`; backend/frontend/generated/schema gates pass.

## 11. Mandatory final-only history compaction

Perform this only after B5 is fully certified and recorded:

1. Verify a clean tree and a linear, configured-author, JMC4B-only, unpushed range after the exact
   `jmc4a-complete` plan base. Stop for merges, unrelated commits, concurrent work, or ambiguity.
2. Commit the final pre-squash timeline entry with all B phase hashes, verification, enabled and
   deferred types, schedule state, deviations, operator work, pre-squash tip, and resolver tag
   `jmc4b-complete`.
3. Create timestamped local recovery branch/tag for the pre-squash tip and a verified complete Git
   bundle outside the repository, preferably `/home/quartermaster/backups/Marquee/`. Request
   permission if needed; do not silently rely on `/tmp`.
4. Record the certified pre-squash tree hash. Through RTK, soft-reset to the exact JMC4B plan base
   and create one configured-author commit:
   `jmc4b: migrate library scans and media analysis`.
5. Verify exact tree-hash identity, sole parent, clean tree, recovery refs, and bundle. Create the
   local annotated tag `jmc4b-complete` and report its full hash/tree/recovery data.
6. Do not edit the timeline after compaction, push, force-push, delete backups, or start JMC4C.
   Rerun a focused smoke and `git diff --check`; report truthfully whether the full suite was rerun.

All commands use RTK. Any mismatch stops the rewrite. No agent/model attribution is permitted.

## 12. Out of scope

- poster pipeline, taste/model work, and poster rescan (JMC4C);
- `radarr_upgrade`, all webhooks, poster/letterbox healing, and backup creation;
- Dolby Vision conversion, letterbox apply/revert/re-encode, poster deploy/reset, subtitle/audio
  mutation/generation/restore, cache deletion, and retention/metrics deletion;
- Projection Room/shared visual progress rebuild, authentication, Docker hardening, GitHub Actions,
  zero-failure cleanup, or any remote push.

## 13. Operator handoff

The final report must give the compact tag/hash/tree, plan base, recovery refs/bundle, exact enabled
and disabled definitions, production schedules and UTC occurrence rules, schema/contracts, retained
failures, native tool/fake fixture versions, live-media smokes performed or deferred, connection and
batch limits, and the exact JMC4C starting state.
