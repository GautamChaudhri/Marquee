# JMC5C — Letterbox, HDR, and Runtime Retirement

> **Implementer:** Read this document in full before changing code. Decisions are locked.
> Execute every internal phase continuously; phase boundaries are verification checkpoints, not
> stopping points. Stop only for a condition in
> [`design/plans/README.md`](../plans/README.md#implementer-continuity-and-stop-conditions)
> or after the complete plan, certification, and final-only history compaction succeed.

**Program:** [clean-slate PgQueuer migration](job-system-pgqueuer-migration.md)
**Predecessor:** [JMC5B audio and subtitle mutations](jmc5b-audio-subtitle-mutations.md)
**Safety architecture:** [direct PgQueuer adoption](job-system-pgqueuer-direct-adoption.md)
**Progress architecture:** [job progress and loading experience](job-progress-and-loading-experience.md)
**Shared timeline:** `design/job-system-update/jmc5-destructive-media-timeline.md`
**Recommended model tier:** **God**

## 1. Objective

Migrate letterbox metadata writes, crop re-encoding, Dolby Vision/HDR conversion, candidate
publication/restoration, and remaining destructive maintenance. Then remove every executable and
persistent remnant of the custom/duplicate runtime and certify that each product command has
exactly one canonical PgQueuer path.

This is Chunk 5's final and highest-risk plan. It must leave no source-media operation inline, no
untracked process, no duplicate artifact lifecycle, and no legacy job manager available even as a
fail-closed facade.

## 2. Preconditions and stop gates

Before changing code:

1. Verify annotated `jmc5b-complete`, tree identity, sole-parent ancestry, external recovery
   bundle, shared timeline, full retained baseline, exact enabled/deferred manifest, and all
   JMC5A/B mutation contracts.
2. Record the exact JMC5C plan base and a clean inventory of letterbox/HDR routes, definitions,
   legacy artifacts/models, direct publish/restore/delete calls, tool launches, startup hooks,
   registries, facades, schedule producers, tests, and Alembic objects.
3. Use only owned disposable PostgreSQL and generated media fixture roots. Record FFmpeg,
   ffprobe, MKVToolNix, `dovi_tool`, GPU/encoder/decoder capabilities, filesystem type, free-space
   controls, and cgroup tier.
4. If `dovi_tool` or a hardware encoder is unavailable, deterministic mocks may cover unit
   contracts but may not certify the corresponding live capability. The handler remains disabled
   wherever its mandatory runtime capability is not readiness-certified.

Stop if HDR/Dolby Vision preservation cannot be validated for a supported conversion, a tool path
cannot be contained/killed, the destination cannot support same-filesystem atomic publication, or
removing a legacy module reveals a still-live product path not covered by a canonical definition.

## 3. Locked decisions

| ID | Decision |
|---|---|
| C01 | JMC5A's mutation protocol and JMC5B's planned/confirm semantics apply to all letterbox/HDR writes. No separate lifecycle is permitted. |
| C02 | Enable existing leaves `letterbox_apply`, `letterbox_remove`, `letterbox_reencode`, `letterbox_heal`, and `dovi_convert` only after their individual fixture/fault gates pass. |
| C03 | Existing TV/batch types remain ticketless parents: `letterbox_apply_tv_scope`, `letterbox_revert_tv_scope`, `letterbox_apply_batch`, and `letterbox_reencode_tv_batch`. Add only the child/control definitions needed for explicit publish, restore, and discard commands. |
| C04 | Add canonical leaves `letterbox_reencode_publish`, `letterbox_reencode_restore`, `letterbox_reencode_discard`, `dovi_publish`, `dovi_restore`, and `dovi_discard`; add parent `dovi_convert_batch` only if a bounded movie/episode route exists. No route mutates an artifact inline. |
| C05 | Letterbox tag/revert writes preserve all non-target metadata and validate crop tags from a fresh post-write probe. A state-row update without verified media metadata is not success. |
| C06 | Letterbox re-encode and Dolby Vision conversion produce immutable, checksummed canonical artifacts first. Publishing the source replacement is a separate confirmed job unless an endpoint explicitly requests an atomic encode-and-publish plan. |
| C07 | Candidate, saved original, diagnostics, and validation evidence use `JobArtifact` plus `MediaOperationDetail`. Remove `LetterboxReencodeArtifact` and any parallel candidate lifecycle. |
| C08 | `MediaBackup` has already been retired by JMC5B. Every saved original/restore source is a confined checksummed canonical artifact with retention and linkage. |
| C09 | Every source mutation uses `media_write`, per-file exclusion, media-write permit, shared maintenance barrier, one automatic domain attempt, and certified containment. GPU slots are secondary advisory gates. |
| C10 | FFmpeg progress uses machine-readable processed media time only when duration/timestamps are valid. `dovi_tool` stages are indeterminate unless they expose a defensible total. |
| C11 | A hardware failure may fall back only to a separately validated configuration that preserves the requested codec/HDR/Dolby Vision invariants. Fallback starts a new current scope but never regresses overall progress. |
| C12 | Profile 5→8.1 and Profile 7 EL-strip conversion follow the installed `dovi_tool` contract. Unsupported profiles, EL types, codecs, containers, or missing mandatory tools fail before publication. |
| C13 | Validation compares source and candidate duration, stream inventory, codec, bit depth, color primaries/transfer/matrix/range, mastering/content-light metadata, HDR10+, Dolby Vision profile/level/RPU state, audio/subtitle/attachment preservation, and output decodability as applicable. |
| C14 | Publication rechecks source signature and fence, preserves a restorable original, fsyncs candidate and directory, atomically replaces on the same filesystem, rescans actual media, and only then commits success. |
| C15 | Crash after publication but before canonical success is reconciled from signatures/artifacts. It never blindly encodes or publishes twice. Unprovable state becomes `unsafe`, with the file excluded from further mutation. |
| C16 | Bulk “replace ready” and TV publish/revert operations become sealed parent workflows with one per-file child. A parent never loops over inline writes. |
| C17 | `letterbox_heal` discovers drift, seals scope, and creates ordinary apply/remove children. It cannot bypass confirmation/safety rules or auto-apply variable/low-confidence crops. |
| C18 | Remove `job_manager`, `media_job_manager`, `cancel_registry`, legacy decorator/handler registry, duplicate media-job SSE/bridges, obsolete startup hooks, and all remaining inline writer entrypoints after the final handler migrates. |
| C19 | `radarr_upgrade` and webhook code remain deferred and fail closed/unmounted. They do not justify retaining the legacy manager or direct poster restore path. |
| C20 | Rewrite the unreleased Marquee migration baseline so fresh creation contains no legacy custom-runtime, `media_backups`, or `letterbox_reencode_artifacts` object. PgQueuer DDL remains outside Alembic. No data-preserving upgrade is promised. |
| C21 | The final release baseline may be squashed again after Chunk 6 if its schema changes; JMC5C nevertheless proves a deterministic fresh reset and no obsolete object now. |
| C22 | Every production definition is either enabled with exactly one kernel handler/entrypoint or explicitly reserved/deferred with no executable route/schedule. Unknown types fail closed. |
| C23 | Internal phases continue autonomously. History compaction and completion tagging occur only after the entire Chunk 5 runtime-removal and media certification succeeds. |

## 4. Letterbox metadata writes

Replace legacy `job_manager` route calls and direct helpers with typed canonical plans. The before
snapshot contains detection source, confidence/variability, frame dimensions/aspect, current crop
metadata, file signature, requested top/bottom values, and applicable movie/show/season/episode
context.

Apply/remove/revert handlers:

- reject absent/stale/variable-unsafe or out-of-bounds plans before mutation;
- use tracked MKVToolNix/FFmpeg tooling appropriate to the container;
- stage when the tool rewrites the container and never modify the source in place without a
  certified atomic protocol;
- rescan tags/dimensions and persist the actual crop result;
- update `LetterboxState`/events only from verified actual media;
- report no-change, partial parent outcomes, validation/rollback, and per-file failure.

TV scope and batch endpoints seal the resolved episode/file set. Overall progress remains the
stable file denominator; current subject includes series, season, episode code/title, file, and
stage.

## 5. Letterbox re-encode lifecycle

Refactor the existing re-encode code into pure plan/argument/validation helpers plus an
execution-context handler. Remove direct process management, job ORM mutation, path-based artifact
serving, and DB commits inside the tool layer.

`letterbox_reencode` writes a candidate artifact only. It records crop, source/output dimensions,
encoder/hardware/fallback, codec, HDR/Dolby Vision preservation, size delta, speed/ETA, validation,
and diagnostic artifacts. It never replaces the original merely because encoding succeeded.

Publish/restore/discard are separate canonical commands:

- publish confirms candidate/source signatures, saves a restorable original artifact, and uses
  coordinator publication;
- restore validates the saved original and current file before staged replacement;
- discard deletes only owned unreferenced candidate/saved-output artifacts through confined keys;
- bulk publish creates per-file children and returns partial results without hiding failures.

## 6. Dolby Vision/HDR conversion

Move conversion behind the tracked launcher and canonical workspace. Preserve the current
supported product scope—Profile 5→8.1 and eligible Profile 7 conversion—only where installed tool
contracts and fixtures prove it. Do not broaden conversions based on filename/profile guesses.

Conversion phases are probe, plan, base encode or stream conversion, RPU extraction/conversion/
injection, mux, validation, candidate registration, and optional later publication. Use FFmpeg
`-progress` for validated-duration encoding; pipe pumps remain owned child processes/tasks with
bounded buffers and cancellation propagation.

`dovi_publish`, `dovi_restore`, and `dovi_discard` use the same artifact and publication protocol as
letterbox. Candidate readiness is not presented as “media converted” until the destination was
published and rescanned. Presenters distinguish candidate creation, deployment, backup, and actual
final HDR/Dolby Vision state.

The authoritative tool references are [FFmpeg's program-friendly progress contract](https://ffmpeg.org/ffmpeg.html),
[the `dovi_tool` command/mode documentation](https://github.com/quietvoid/dovi_tool), and
[PostgreSQL session-level advisory-lock semantics](https://www.postgresql.org/docs/18/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS).

## 7. Runtime and schema retirement

After every product writer is canonical and certified:

1. delete the fail-closed `JobManager` and `MediaJobManager` facades and their exports;
2. delete process-local `cancel_registry`; migrate any retained read-only/ML callers to the
   immutable execution context before removal;
3. delete the legacy decorator registry and `builtin_handlers` once no canonical handler imports
   them;
4. remove `_generic_media_job_bridge`, legacy media-job serializers/status constants, duplicate
   polling/SSE, inline worker IDs, resource dictionaries, and `create_and_run` call sites;
5. remove direct mutation calls from routes, including re-encode replace/restore/delete and poster
   deploy/restore remnants;
6. remove obsolete models/tables/migrations for duplicate backup/re-encode lifecycle evidence;
7. prove API/worker/scheduler startup imports only canonical submission, registry, kernel, PgQueuer,
   and evidence services;
8. prove scheduled callbacks create canonical jobs only and no old worker/scheduler/claim/recovery
   loop exists;
9. regenerate OpenAPI/TypeScript contracts and delete old DTO/client functions rather than
   preserving compatibility.

The clean-slate migration rewrite is destructive by design and must be exercised only against an
owned disposable database. Fresh reset/install/upgrade verification replaces old-schema upgrade
testing.

## 8. Implementation phases

### Phase C0 — verify JMC5B and freeze final writer/runtime inventory

Verify predecessor compaction and gates; inventory every route/definition/process/model/table/
startup/test reference; freeze tool and hardware capabilities; add final legacy-runtime and schema
contract tests. Do not change behavior.

### Phase C1 — letterbox tag, revert, healing, and parents

Migrate apply/remove/revert leaves, TV/batch parents, and healing discovery/children. Add typed
plans/results/progress, post-write probes, route submissions, presenter fixtures, and cancellation/
crash gates.

### Phase C2 — letterbox re-encode and candidate evidence

Refactor encoding into the tracked launcher, canonical workspaces/progress/artifacts, remove the
parallel artifact lifecycle, and certify CPU/GPU/fallback plus HDR/Dolby Vision preservation.

### Phase C3 — letterbox publish, restore, discard, and bulk decisions

Replace every inline artifact mutation route with canonical leaves/parents. Prove backup,
same-filesystem publication, reconciliation, restore, retention, bulk partial failure, and stale
artifact rejection.

### Phase C4 — Dolby Vision conversion and publication

Migrate `dovi_convert`, candidate evidence, publish/restore/discard, and any bounded batch parent.
Certify supported profiles/tool modes, native progress, hardware fallback, preservation, failure
at each pipe/publish boundary, and capability-based readiness/enablement.

### Phase C5 — legacy runtime/schema removal

Delete every custom/duplicate manager, registry, cancellation bridge, inline writer, stale route
helper, obsolete model/table, and startup/test dependency. Rewrite the unreleased Marquee baseline,
run fresh reset/schema/PgQueuer verification, and prove every product type has exactly one executor.

### Phase C6 — complete Chunk 5 certification and history compaction

Run the full fixture, crash, cancellation, saturation, schema, static, API, presentation, frontend,
and retained-suite matrix. Record exact live-tool exceptions and final manifests, then perform §10
only after every gate passes.

After each phase, run gates, commit, update the timeline, and continue immediately. Routine phase
completion is not a reason to stop.

## 9. Acceptance matrix

### 9.1 Media fixtures

- MKV/MP4 movie and episode fixtures with crop tags, variable-aspect decisions, multiple streams,
  chapters/attachments, SDR/HDR10/HDR10+/Dolby Vision profiles, bit depths, and color metadata;
- tag apply/remove/revert success/no-change/stale/unsupported/variable-unsafe and exact post-probe;
- letterbox encode CPU, supported GPU, certified fallback, invalid duration/native progress,
  output dimensions/crop, size delta, HDR/RPU preservation, and decode validation;
- eligible Profile 5 and Profile 7 conversion plus unsupported profile/EL/container/tool cases;
- candidate/publish/restore/discard and bulk parent outcomes with immutable evidence;
- source signature change, hardlink/symlink/path-swap, permissions, full/read-only disk,
  cross-device rejection, missing/corrupt backup/candidate, and destination races.

### 9.2 Faults and cancellation

Inject cancellation and worker/child/API/database death:

- before admission/tool launch;
- during probe, tag write, encode, pipe pump, RPU handling, mux, and validation;
- after candidate completion, backup, fsync, and before/after atomic replacement;
- after publish before rescan/result and after canonical success before acknowledgment;
- during restore/discard and between bulk children.

Prove no duplicate publish, conflicting file mutation, stale output, leaked process, false terminal
outcome, lost target/evidence linkage, or blind retry. Uncertain state is quarantined and blocks
further writes.

### 9.3 Runtime removal and system certification

- fresh schema contains no custom queue/resource/schedule/recovery, `media_backups`,
  `letterbox_reencode_artifacts`, `MediaJob`, `MediaBatch`, or `MediaJobEvent` object;
- source contains no production `job_manager`, `media_job_manager`, `cancel_registry`, legacy
  register/resolve registry, `create_and_run`, inline worker, or per-job polling SSE;
- handler/tool modules contain no direct subprocess launch or filesystem publication outside the
  approved launcher/boundary/coordinator;
- every enabled leaf has one definition, one handler, one entrypoint, typed documents, presenter,
  progress policy, safety gates, and canonical route/schedule inventory;
- every reserved type, especially `radarr_upgrade`/webhooks, has no executable product path;
- full backend retained suite adds no failure/skip/xfail; Ruff, Alembic/model equivalence, fresh
  reset, PgQueuer verify, OpenAPI/type drift, affected frontend check/lint/build, and
  `git diff --check` pass;
- saturation proves control/API/event/log/database connection budgets while media-write/GPU jobs
  run, cancel, and recover.

Manual/operator smokes identify exact media/tool/hardware versions. Missing optional live
capabilities remain explicitly uncertified; they cannot be inferred from mocks.

## 10. Mandatory final-only history compaction

Perform only after C6 and the entire Chunk 5 exit gate succeed:

1. Verify a clean, linear, configured-author, JMC5C-only, unpushed range after exact
   `jmc5b-complete`. Stop for merges, unrelated/concurrent commits, uncertain ownership, or pushed
   phase history.
2. Commit the final pre-squash timeline record with all C hashes, complete certification, final
   executor/deferred/schema manifests, live-tool exceptions, deviations, operator actions,
   pre-squash tip, and intended tag `jmc5c-complete`.
3. Create timestamped recovery branch/tag and a verified complete external bundle, preferably
   under `/home/quartermaster/backups/Marquee/`.
4. Record the certified tree; through RTK soft-reset to the exact plan base and create one
   configured-author commit: `jmc5c: complete destructive media migration`.
5. Prove exact tree identity, sole parent, clean worktree, recovery refs, and verified bundle.
6. Create annotated local tag `jmc5c-complete`; report final hashes/base/recovery. Do not edit the
   timeline after compaction.
7. Do not push, force-push, delete recovery material, or start Chunk 6.

Any mismatch stops the rewrite. No agent/model attribution is permitted.

## 11. Out of scope

- Projection Room/shared progress-card visual implementation and general frontend state cleanup;
- final legacy-test triage and zero-failure repository baseline (Chunk 6);
- GitHub Actions modernization after local green;
- webhooks/`radarr_upgrade`, authentication, public reset replacement, Docker hardening;
- unsupported Dolby Vision transformations or unvalidated codec/container broadening;
- pushing commits/tags or deleting recovery material.

## 12. Operator handoff

Report compact tag/hash/tree/base and recovery material, final executor/deferred/schedule/schema
manifests, schema/OpenAPI/type versions, retained failures, fixture/tool/GPU/cgroup/filesystem
versions, every crash/publish/recovery result, live smokes and exceptions, resource budgets, and the
exact Chunk 6 starting condition. State plainly whether real `dovi_tool` and hardware conversion
smokes ran and which profiles were actually certified.
