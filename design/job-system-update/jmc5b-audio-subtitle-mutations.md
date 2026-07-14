# JMC5B — Audio and Subtitle Mutations

> **Implementer:** Read this document in full before changing code. Decisions are locked.
> Execute every internal phase continuously; phase boundaries are verification checkpoints, not
> stopping points. Stop only for a condition in
> [`design/plans/README.md`](../plans/README.md#implementer-continuity-and-stop-conditions)
> or after the complete plan, certification, and final-only history compaction succeed.

**Program:** [clean-slate PgQueuer migration](job-system-pgqueuer-migration.md)
**Mutation base:** [JMC5A mutation, artwork, and maintenance](jmc5a-mutation-contracts-artwork-and-maintenance.md)
**Progress contract:** [job progress and loading experience](job-progress-and-loading-experience.md)
**Successor:** [JMC5C letterbox, HDR, and runtime retirement](jmc5c-letterbox-hdr-and-runtime-retirement.md)
**Shared timeline:** `design/job-system-update/jmc5-destructive-media-timeline.md`
**Recommended model tier:** **God**

## 1. Objective

Migrate every audio/subtitle command that creates, removes, remuxes, reorders, restores, or
publishes media/sidecar content to the canonical PgQueuer runtime. Preserve the exact user request,
attribute outcomes to individual tracks/sidecars/stages, validate actual post-operation state, and
remove the duplicate `MediaJob`/`MediaBackup` authority.

## 2. Preconditions and stop gates

Before changing code:

1. Verify `jmc5a-complete`, its tree identity, sole-parent ancestry, recovery material, shared
   timeline, enabled/deferred manifest, and retained test baseline.
2. Record `jmc5a-complete` as the exact JMC5B plan base. Verify a clean tree, configured author,
   disposable PostgreSQL target, and confined synthetic media fixture roots.
3. Freeze every audio/subtitle route, plan/confirm endpoint, media-operation map entry, legacy
   `MediaJob`/`MediaBackup` reference, direct subprocess call, cancellation bridge, sidecar writer,
   and current request/result shape.
4. Record installed FFmpeg/ffprobe/MKVToolNix/Subgen capabilities and exact fixture codecs. Tests
   must not contact a live Subgen service or mutate an operator library.

Stop if a requested selector cannot be resolved deterministically, a source signature cannot be
proven, a format cannot preserve required streams/metadata, a provider has no bounded
reconciliation contract, or publication state cannot be distinguished after failure.

## 3. Locked decisions

| ID | Decision |
|---|---|
| B01 | JMC5A's typed mutation vocabulary and coordinator protocol are mandatory. No family-local lifecycle, raw result dict, or direct publication is introduced. |
| B02 | The canonical job supports a real `planned` state with no PgQueuer ticket. Confirmation validates plan version, expiry, source signature, target selectors, and expected configuration version, then enqueues the same canonical job exactly once. |
| B03 | Plan creation stores immutable request, before snapshot, expected target, source signature, confirmation requirements, and expiration in `MediaOperationDetail`. Confirmation never rewrites the requested operation. |
| B04 | Enabled leaf types are `audio_remove`, `track_remove`, `subtitle_remove`, `subtitle_embed`, `subtitle_metadata`, `audio_reorder`, `subtitle_extract`, `subtitle_generate`, `subtitle_policy`, and `subtitle_restore`. Each receives a typed request/result and explicit stage vocabulary. |
| B05 | Add parent-only `subtitle_policy_batch` where policy application spans files. Existing `subtitle_generate_batch` is parent-only. Parents have no PgQueuer ticket and seal their child denominator. |
| B06 | Every source-changing leaf uses `media_write`, per-file exclusion, media-write permit, shared maintenance barrier, one automatic domain attempt, and certified process containment. |
| B07 | Stable selectors use inventory/stream identity plus immutable track facts. Raw current stream indexes alone are not durable selectors; stale or ambiguous resolution fails before mutation. |
| B08 | Embedded MKV remuxes are all-or-nothing. A failed remux marks every requested embedded target `not_applied`. External sidecars form a separately reported atomic group and may produce an honest partial-success result. |
| B09 | MKV remuxes parse `mkvmerge --gui-mode`; applicable FFmpeg transforms parse `-progress`. Invalid/missing native progress degrades to indeterminate without affecting the operation. |
| B10 | Every subprocess uses the JMC3 tracked launcher. Direct `asyncio.create_subprocess_exec`, legacy PID bookkeeping, `cancel_registry`, and ORM/job mutation are removed from migrated code. |
| B11 | Candidate output is staged on the destination filesystem, fully probed/validated, fsynced, fence/signature checked, and atomically published by the coordinator. Cross-device copy-over is forbidden. |
| B12 | A canonical job-linked backup artifact is created before destructive source replacement when policy requires it. The legacy `MediaBackup` table/model/service is removed; restore consumes a confined checksummed canonical artifact. |
| B13 | Post-operation ffprobe/MKVToolNix inventory is authoritative. Expected deltas alone cannot declare success. Live `MediaFile` and track projections update only from the actual rescan. |
| B14 | `subtitle_generate` stages provider output in the attempt workspace, validates it, and publishes it through the same protocol. It never changes its job type/request mid-execution. |
| B15 | Embedded generation remains one typed workflow with an explicit generation atomic group and an embed/remux atomic group; failure evidence distinguishes “generated but not embedded” from “nothing published.” |
| B16 | Subgen webhook completion is not required or enabled. Provider polling/reconciliation is bounded, cancellable, and truthful; webhook routes/tests remain deferred. |
| B17 | Extraction and external generation return registered artifacts/managed sidecars, not arbitrary physical paths. Downloads use confined artifact APIs. |
| B18 | Policy application freezes the evaluated per-file plan before creating children. It does not silently adopt a changed policy/configuration halfway through a batch. |
| B19 | Cancellation never deletes a pre-existing sidecar or source. It cleans only owned staging output and becomes terminal only after child death is proven. |
| B20 | Existing legacy response adapters, duplicate media-job SSE, and page-local job identities are deleted. Narrow frontend callers migrate to canonical IDs/contracts; visual redesign waits for Chunk 6. |
| B21 | JMC5B runs continuously through all phases unless a documented stop gate is reached. It compacts history only after complete certification. |

## 4. Planned and confirmed execution

Extend the canonical submission service with two explicit commands:

- `plan_mutation(...)` creates a canonical planned job, 1:1 `MediaOperationDetail`, semantic
  event, subject/configuration snapshots, and idempotency record without a transport ticket;
- `confirm_mutation(...)` locks the job/detail, checks expected version and desired state,
  re-resolves selectors/signature, records confirmation provenance, and transactionally creates
  the first dispatch/PgQueuer ticket.

Repeated compatible plan/confirm requests are idempotent. Mismatched confirmation, stale plan,
expired plan, retired/missing subject, changed source, or already-dispatched generation returns a
typed conflict. Cancelling a planned job creates no transport cancellation. A retry of completed
work creates a new canonical successor and new plan snapshot.

## 5. Track selectors and typed outcomes

Requests identify the desired operation without accepting execution policy. Selectors capture
the facts needed to detect drift: track kind, source (embedded/external), language, codec,
channels, title, dispositions (default/forced/hearing-impaired), source-relative managed key,
inventory signature, and the original stream/track identity as a hint.

Before and actual inventories use the same typed track representation. Every requested target
gets one outcome with the failing stage (`resolve`, `preflight`, `remux`, `sidecar`, `validate`,
`backup`, `publish`, or `rescan`) and a safe reason. Presenters show requested versus actual
changes rather than raw JSON.

## 6. Mutation families

### 6.1 Remove, reorder, and metadata

Migrate audio removal, generic track removal, subtitle removal, audio reorder, and subtitle
metadata edits first. Build command arrays from the frozen plan, not from client arguments.
Preserve attachments, chapters, tags, non-target streams, language/title/disposition metadata,
container compatibility, and input duration unless the operation explicitly changes them.

No selected target or already-satisfied metadata becomes `no_change`. A stale selector or
container incompatibility is a permanent pre-effect failure. Tool failure or validation failure
publishes nothing.

### 6.2 Embed and extract

Embedding consumes only a validated managed subtitle artifact/sidecar key, stages a full remux,
and verifies the exact new track. Extraction writes to an attempt workspace, validates subtitle
content/format, then atomically publishes a managed sidecar/artifact without altering the source.
Existing identical output is `no_change`; conflicting unowned output fails closed.

### 6.3 Generation

Provider submission, waiting, download/reconciliation, validation, optional sidecar publication,
optional remux, rescan, and result are distinct stages/scopes. Provider progress is determinate
only when the provider supplies a defensible total. Otherwise display named indeterminate work
with current language, source track, movie/show/season/episode/file context, elapsed time, and
wait reason.

Temporary audio and provider output remain confined. Polling has bounded intervals/deadlines and
cooperative cancellation. Late provider output after cancellation cannot publish because the
fence and owned request identity are stale.

### 6.4 Policy and restore

Policy application stores the evaluated rule/version and one immutable child plan per file.
`subtitle_policy_batch` aggregates exact child outcomes. It never reevaluates live settings inside
an already sealed batch.

Restore validates the canonical backup artifact checksum, original source identity, current
destination, and requested restore mode. It stages and validates restored output before atomic
publication. A restore does not delete its only verified backup and cannot overwrite a newly
changed file without an explicit fresh plan.

## 7. Batches, APIs, and presentations

Migrate every audio/subtitle mutation route to planned/confirm or immediate canonical submission
as appropriate. Batch routes create ticketless parents and transactional children. Responses
provide canonical job ID, snapshot/presentation/Activity links, idempotency disposition, and any
confirmation requirement.

Update presenter goldens and generated contracts for:

- track-rich before/after tables;
- requested selectors and per-target outcomes;
- generation provider/task/language and produced artifact;
- backup, validation, atomicity, publish, rescan, no-change, warnings, and remediation;
- movie/episode/show/season/file/track and sealed-batch subjects;
- direct live/final logs and artifact availability without exposing paths.

## 8. Implementation phases

### Phase B0 — verify JMC5A and freeze audio/subtitle contracts

Verify the compact predecessor and all shared gates; inventory routes, definitions, legacy
lifecycle calls, models, subprocesses, files, and fixtures. Add a contract freeze and record exact
tool/provider capabilities without changing behavior.

### Phase B1 — planned/confirm flow, selectors, and canonical backup evidence

Implement planned jobs and confirmation, typed selectors/inventories/results, canonical backup
artifacts, stale-plan/idempotency rules, and delete `MediaBackup` authority from target metadata.

### Phase B2 — removals, reorder, and metadata

Migrate `audio_remove`, `track_remove`, `subtitle_remove`, `audio_reorder`, and
`subtitle_metadata`; add native progress, staging, validation, rescans, target attribution, route
submission, and presenter/API fixtures.

### Phase B3 — embed, extract, and generation

Migrate `subtitle_embed`, `subtitle_extract`, and `subtitle_generate`; remove direct subprocess and
process-local cancellation paths; certify provider polling, late output, external/embedded atomic
groups, artifacts, and bounded waits.

### Phase B4 — policy, restore, and batches

Migrate `subtitle_policy`, `subtitle_restore`, `subtitle_policy_batch`, and
`subtitle_generate_batch`; freeze batch plans, seal children, and prove restore safety, batch
aggregation, cancellation, and partial results.

### Phase B5 — JMC5B certification and history compaction

Run all media fixtures/fault gates, full retained suite, Ruff, schema/Alembic, generated-contract,
affected frontend check/lint/build, static legacy scans, and `git diff --check`. Record the final
manifest and perform §10 only after every gate succeeds.

After every phase, commit/update the timeline and immediately continue. Do not stop at a routine
phase boundary.

## 9. Acceptance matrix

Use small generated MKV/MP4 fixtures covering:

- multiple audio/subtitle codecs, languages, channels, titles, default/forced/HI dispositions,
  embedded/external tracks, duplicate metadata, attachments, chapters, and empty selections;
- per-family success, no-change, stale selector, ambiguous selector, unsupported container,
  permanent failure, proven-pre-effect transient retry, cancellation, timeout, and unsafe unknown;
- multi-track all-or-nothing remux failure with every target `not_applied`;
- partial external-sidecar failure separated from an embedded atomic group;
- native mkvmerge/FFmpeg progress, malformed output, process stderr flood, tool signal/exit, and
  truthful indeterminate fallback;
- backup creation/checksum/retention/restore and source-changed restore refusal;
- crashes at tool launch, candidate completion, validation, backup, fsync/replace, rescan,
  terminal commit, and pre-ack redelivery;
- hardlink/symlink/path-swap/traversal, full disk, permissions, read-only destination, missing
  binaries, cross-device rejection, and destination replacement races;
- generation accept/reject/poll/timeout/failure/late completion, invalid SRT, cancellation, and
  generated-but-not-embedded evidence without relying on webhooks;
- policy snapshot drift, fixed/sealed batches, zero-child/no-change, mixed outcomes, cancellation,
  subject retirement, retry lineage, bounded queries, and correct parent progress;
- every presenter/API contract, no raw path/secret/stage leakage, and direct logs/artifacts;
- static proof that migrated modules contain no `MediaJob`, `MediaBackup`, `media_job_manager`,
  `cancel_registry`, direct child launch, or inline writer.

The full retained failure/skip/xfail set cannot grow. JMC5B does not claim Chunk 6's eventual
zero-failure baseline.

## 10. Mandatory final-only history compaction

Perform only after B5 certification:

1. Verify a clean, linear, configured-author, JMC5B-only, unpushed range after exact tag
   `jmc5a-complete`. Stop for merges, unrelated/concurrent commits, uncertain ownership, or pushed
   phase history.
2. Commit the final pre-squash timeline record with phase hashes, verification, manifest,
   deviations, operator actions, pre-squash tip, and intended tag `jmc5b-complete`.
3. Create timestamped recovery branch/tag and a verified complete external Git bundle, preferably
   under `/home/quartermaster/backups/Marquee/`.
4. Record the certified tree; through RTK soft-reset to the plan base and create one configured-
   author commit: `jmc5b: migrate audio and subtitle mutations`.
5. Prove tree identity, sole parent, clean worktree, and recovery resolution.
6. Create annotated local tag `jmc5b-complete`; report final hashes and recovery locations. Do not
   edit the timeline after compaction.
7. Do not push, force-push, delete recovery material, or start JMC5C.

Any uncertainty stops the rewrite. No agent/model attribution is permitted.

## 11. Out of scope

- letterbox tag/re-encode/publish/restore and HDR/Dolby Vision conversion (JMC5C);
- poster/artwork work already completed by JMC5A;
- webhooks, `radarr_upgrade`, online restore, public reset replacement, authentication, Docker;
- Projection Room visual rebuild, general frontend state cleanup, final test cleanup, GitHub Actions;
- pushing commits/tags or deleting recovery material.

## 12. Operator handoff

Report compact tag/hash/tree/base and recovery material, exact enabled/deferred job types,
schema/OpenAPI/type versions, retained failures, FFmpeg/ffprobe/MKVToolNix/provider versions,
fixture matrix, native progress and failure-injection evidence, pending live smokes, and the exact
JMC5C starting condition. State whether real provider and representative hardware smokes ran; do
not imply them from mocks.
