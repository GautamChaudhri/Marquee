# JMC5A — Mutation Contracts, Artwork, Backup, and Maintenance

> **Implementer:** Read this document in full before changing code. Decisions are locked.
> Execute every internal phase continuously; phase boundaries are verification checkpoints, not
> stopping points. Stop only for a condition in
> [`design/plans/README.md`](../plans/README.md#implementer-continuity-and-stop-conditions)
> or after the complete plan, certification, and final-only history compaction succeed.

**Program:** [clean-slate PgQueuer migration](job-system-pgqueuer-migration.md)
**Architecture:** [direct PgQueuer adoption](job-system-pgqueuer-direct-adoption.md)
**Safety base:** [JMC3A execution and filesystem safety](jmc3a-execution-kernel-and-filesystem-safety.md)
**Evidence base:** [JMC3B progress, logs, artifacts, and events](jmc3b-progress-logs-artifacts-and-events.md)
**Predecessor:** [JMC4C poster, ML, and certification](jmc4c-poster-ml-and-certification.md)
**Successor:** [JMC5B audio and subtitle mutations](jmc5b-audio-subtitle-mutations.md)
**Shared timeline:** `design/job-system-update/jmc5-destructive-media-timeline.md`
**Recommended model tier:** **God**

## 1. Objective

Establish the one canonical contract used by every destructive job, then migrate the
reversible artwork, backup, healing, and maintenance write families. No API route, scheduled
callback, or feature helper may perform these writes inline after this plan.

JMC5A deliberately starts with smaller files and reversible product state before source-media
remuxing or transcoding. It proves the target-outcome, backup, validation, publish, and
uncertain-side-effect rules that JMC5B and JMC5C reuse.

## 2. Preconditions and stop gates

Before changing code:

1. Verify annotated tag `jmc4c-complete`, its compact commit/tree, recovery material, and the
   post-rewrite smoke recorded in the JMC4 timeline.
2. Record the exact plan base, branch, configured Git author, clean-tree state, Python/Node/tool
   versions, PostgreSQL target, schema fingerprints, full retained test baseline, Ruff,
   Alembic, generated-contract, and frontend gates in the new shared JMC5 timeline. JMC5A
   creates that timeline; the architect does not.
3. Freeze an exact inventory of every direct artwork/backup/maintenance writer and every
   registered, route-constructed, scheduled, or presenter-visible job type in this plan.
4. Use only disposable databases and confined synthetic artwork/data roots. Never run a
   mutation fixture against an operator library, normal `DATA_DIR`, or real backup set.

Stop rather than weakening the plan if:

- the JMC4 compact tree or ancestry cannot be proven;
- a route cannot be made asynchronous without retaining an inline side effect;
- an operation cannot identify and confine every source and destination;
- a destructive effect cannot be fenced, validated, backed up where required, and represented
  honestly after a crash;
- tests cannot prove whether an observed side effect occurred;
- concurrent/unrelated commits make the plan-owned range or final rewrite unsafe.

## 3. Locked decisions

| ID | Decision |
|---|---|
| A01 | PgQueuer remains the only delivery authority. All production writes enter through the canonical submission and fenced execution kernel. |
| A02 | Add shared versioned mutation request/result/error documents. Generic `BuiltInIntentV1` and `BuiltInResultV1` are forbidden for every enabled mutating definition. |
| A03 | A target outcome is exactly `succeeded`, `failed`, `skipped`, or `not_applied`. `no_change` is a job outcome with an explicit reason, not a synonym for success or skip. |
| A04 | Results record requested targets, before snapshot, expected snapshot, actual post-operation snapshot, per-target stage/reason, validation, atomicity, backup, publish, and bounded failure evidence. |
| A05 | Unsafe work has one automatic domain attempt by default. A retry is allowed only when the classifier proves no side effect was published. Unknown publication becomes `unsafe`/quarantined and requires reconciliation, never blind retry. |
| A06 | Every write rechecks canonical fence, cancellation intent, source identity/signature, and confined destination immediately before publication. The handler never publishes directly. |
| A07 | Public mutation routes return `202` canonical job summaries and links. They do not wait for execution, mutate files/rows, synthesize legacy job responses, or accept client-selected execution policy. |
| A08 | Add canonical leaf definitions `poster_deploy`, `poster_restore`, `poster_reset`, and `poster_backup_subject`. Existing bulk/heal types coordinate children and carry no transport attempt. |
| A09 | `poster_heal`, `poster_deploy_reset`, and `poster_backup_all` are fixed or sealed dynamic parent workflows whose leaf failures are isolated and itemized. No monolithic loop hides the affected subject. |
| A10 | Poster deployment consumes a confined JMC4 artifact/storage key and immutable selection snapshot, never a caller-provided filesystem path. |
| A11 | Poster leaves use `media_write` as their primary execution class. Network download is a stage/secondary concern and never permits bypassing media-write or subject-file exclusion. |
| A12 | A deploy/restore/reset preserves or creates recoverable evidence before overwriting/deleting a deployed poster, validates the final bytes, and only then commits live artwork state. |
| A13 | `backup_create` wraps the certified JMC3 operator backup service under the exclusive maintenance barrier. Restore remains the guarded offline fresh-target CLI; no online restore job or route is introduced. |
| A14 | `pipeline_cache_clear`, `poster_maintenance`, `job_retention_purge`, and `system_metrics_purge` use the maintenance entrypoint, confined keys, bounded dry-run/plan evidence, and explicit deletion counts. |
| A15 | Retention never deletes active jobs, current configuration, referenced artifacts/backups, unexpired logs/evidence, or the job performing cleanup. Deletion scope is sealed before mutation. |
| A16 | `radarr_upgrade` and all webhook-triggered restore behavior remain reserved and disabled. Webhooks are not implemented or certified here. |
| A17 | `letterbox_heal` remains disabled for JMC5C. Audio/subtitle and source-media mutations remain disabled for JMC5B/JMC5C. |
| A18 | Every newly enabled definition receives typed progress stages, presenter fixtures, action capabilities, subject snapshots, configuration keys, and exact safety gates before its endpoint is enabled. |
| A19 | There is no data backfill or legacy response adapter. The database is still disposable and Marquee remains unreleased. |
| A20 | All internal phases run in one implementer session unless a documented stop condition occurs. Final history compaction happens only after complete certification. |

## 4. Shared destructive-operation contract

Create one typed vocabulary reused by later plans:

- `MutationTargetV1`: stable target key, target kind, human label, requested operation, and
  immutable selector facts;
- `MutationTargetOutcomeV1`: status, stage, reason code, safe human message, before/expected/
  actual summaries, and whether bytes or product state changed;
- `MutationValidationV1`: source/output probe summaries, invariant checks, warnings, and final
  verdict;
- `MutationAtomicityV1`: atomic group ID, all-or-nothing boundary, published flag, rollback
  availability, and uncertain-state flag;
- `MutationBackupV1`: confined artifact/storage key, checksum, size, source signature, retention,
  and restore eligibility;
- `MutationPublishV1`: candidate checksum/signature, destination identity, fence used, fsync/
  replace result, post-publish rescan, and reconciliation state;
- family-specific request/result models that embed these common types without exposing paths,
  command lines, PgQueuer IDs, or secrets.

`MediaOperationDetail` remains strict 1:1 canonical evidence. Its JSON fields are validated
through these models on every write and every API read. A failed or cancelled operation retains
its last truthful evidence. An all-or-nothing failure reports every requested target as
`not_applied`; partial sidecar/artwork groups report only actually published targets as
`succeeded`.

Add a coordinator-facing mutation protocol:

1. resolve the durable subject and confined storage keys;
2. compare the live source with its enqueue/plan snapshot;
3. persist the before snapshot and requested targets;
4. create candidate/backup output in an attempt workspace or destination-filesystem staging
   location;
5. validate candidate output;
6. recheck fence, intent, signature, and destination;
7. publish only through the JMC3 publication service;
8. rescan/reload the actual state;
9. persist target outcomes and canonical result before PgQueuer acknowledgment.

## 5. Artwork workflows

### 5.1 Deploy, restore, and reset leaves

Move `PosterService.deploy` and `PosterService.restore` behind execution-context handlers.
Routes such as feedback selection and television “use this poster” commands submit
`poster_deploy`; no route calls the service directly.

Each leaf:

- snapshots movie/series/season identity, folder identity, expected filename, prior artwork,
  candidate artifact, selection/profile/model provenance, and content checksums;
- uses the subject/file safety key plus media-write permit;
- creates a recoverable backup when an existing deployed file will be replaced or deleted;
- confines cache, backup, candidate, staging, and destination independently;
- validates image decode, size/content checksum, configured filename, and destination after
  atomic publication;
- commits `ArtworkEvent`, live artwork columns, `MediaOperationDetail`, artifacts, target result,
  and semantic event coherently;
- treats an already-identical deployed poster as `no_change`;
- never clears live DB state if filesystem deletion or replacement was not proven.

`poster_reset` resets one subject. `poster_deploy_reset` resolves its scope, creates sealed
children, and aggregates them. It is not a loop inside one attempt. `poster_restore` chooses
only from the recorded backup/cache chain; an optional provider download is staged and verified
before publication.

### 5.2 Healing and scheduled work

`poster_heal` is a parent/control workflow:

- discover missing or drifted deployed posters without writing;
- seal the child scope;
- create one `poster_restore` child per affected subject;
- report unchanged/unsupported subjects distinctly;
- preserve movie, series, and season context and per-child remediation.

Activate the code-owned poster-heal schedule only after multi-scheduler uniqueness,
overlap/coalescing, parent sealing, and all leaf mutation gates pass. A schedule callback only
submits the parent job.

## 6. Backup and destructive maintenance

### 6.1 Backup creation

Canonical `backup_create` invokes the JMC3 backup implementation inside a maintenance attempt.
It acquires the exclusive maintenance barrier before the consistency point and records the
manifest/checksums as job artifacts. API creation becomes asynchronous. Read-only list and
verification endpoints may remain bounded and confined. Restore remains offline-only.

Credential handling, manifest contents, fresh-target verification, and DB/`DATA_DIR` linkage
remain exactly as certified in JMC3C. Do not create a second backup format or run `pg_dump`
outside the tracked launcher/credential boundary.

### 6.2 Poster backup and maintenance

`poster_backup_all` seals subjects and uses `poster_backup_subject` children. The child records
source checksum, backup checksum, bytes copied, no-change reason, and retention. Bulk maintenance
must not accept arbitrary roots.

`poster_maintenance` has an explicit dry-run mode. A mutating run freezes the dry-run plan or
recomputes and stores a new sealed plan; it may not delete a larger scope than the confirmed
plan. Candidate/cache cleanup preserves referenced job artifacts, active attempt workspaces, and
recoverable poster backups.

### 6.3 Cache and row retention

`pipeline_cache_clear`, `job_retention_purge`, and `system_metrics_purge` pre-count a bounded
scope, emit determinate progress when the count is stable, delete in bounded transactions, and
return per-category counts. A cancellation between batches leaves an honest partial outcome.
Filesystem deletion goes through confined keys; database cleanup never calls arbitrary ORM
cascades that erase retained evidence.

## 7. APIs, presentations, and contracts

Update the definition registry, presenters, canonical APIs, and generated OpenAPI/TypeScript
contracts together. Required behavior:

- mutation requests use explicit idempotency keys and return existing-compatible versus conflict;
- row/detail presentations expose prior and selected/deployed poster, backup/validation/publish
  state, current subject, attention, remediation, and direct logs/artifacts;
- bulk parents expose sealed counts and per-subject drill-down;
- command capability is server-returned and stale commands conflict rather than guessing;
- no API exposes physical paths, command arguments, credentials, or raw exception text;
- old synchronous response shapes and inline writer helpers are deleted, not adapted.

## 8. Implementation phases

### Phase A0 — verify JMC4C and freeze mutation inventory

Create the shared timeline, establish all baselines, verify the compact predecessor, inspect
installed dependencies, freeze direct-writer/route/model/test inventories, and add contract-freeze
tests. Do not change runtime behavior in A0.

### Phase A1 — common mutation documents and coordinator protocol

Add typed target/result/validation/atomicity/backup/publish models, validate
`MediaOperationDetail`, add shared mutation helpers, update presenters, and prove stale fence,
unknown publication, no-change, all-or-nothing, and partial-group semantics.

### Phase A2 — poster deploy, restore, and reset

Add the new definitions/handlers, migrate direct deployment routes, implement confined backup and
coordinator publication, add post-write validation, and certify movie/series/season leaves.

### Phase A3 — poster parents, healing, and schedule

Convert reset/backup/heal to sealed parent workflows, enable their certified children, activate
the poster-heal schedule, and prove overlap, cancellation, partial failure, and bounded queries.

### Phase A4 — backup creation and destructive maintenance

Migrate backup creation, poster maintenance, cache cleanup, job retention, and metrics retention.
Keep restore offline. Prove exclusive maintenance, dry-run sealing, reference preservation,
idempotency, confinement, and cancellation between bounded batches.

### Phase A5 — JMC5A certification and history compaction

Run the entire acceptance matrix, full retained backend suite, Ruff, schema/Alembic checks,
OpenAPI/type drift, affected frontend check/lint/build, and `git diff --check`. Record every
enabled/deferred definition and schedule, then perform §10 only if every gate is successful.

After every phase: run focused tests and all shared gates, commit with a short lowercase message,
append the timeline, and immediately start the next phase. Do not stop merely because a phase
finished.

## 9. Acceptance matrix

Automated fixtures cover:

- movie, series, and season deploy/restore/reset success and no-change;
- existing-poster backup, invalid candidate, missing cache/provider fallback, filename/path
  attacks, symlink/path-swap, read-only/full-disk, and checksum mismatch;
- crash/cancel before staging, after backup, before/after publication, after publish before DB
  result, and after canonical success before acknowledgment;
- duplicate delivery, stale fence, stale source/destination, conflicting idempotency, and
  uncertain publication quarantine;
- sealed reset/backup/heal parents with zero children, partial failure, retry-safe discovery,
  cancellation, subject retirement/deletion, and bounded pagination;
- backup creation consistency, exclusive maintenance, credential secrecy, manifest/artifact
  linkage, and offline verification compatibility;
- dry-run scope equality, referenced-artifact preservation, bounded deletion transactions,
  cancellation, and accurate partial counts;
- schedule uniqueness, coalescing, disable/re-enable, and restart;
- presenter goldens, typed API errors, no raw paths/secrets, deterministic generated contracts;
- proof that migrated routes contain no inline writer or legacy job-manager call.

No new failure, error, skip, or `xfail` is allowed relative to the recorded JMC4C baseline.
JMC5A may reduce inherited failures but does not claim the final zero-failure baseline.

## 10. Mandatory final-only history compaction

Perform this only after A5 is fully certified and recorded:

1. Verify a clean tree and a linear, configured-author, JMC5A-only, unpushed range after the exact
   `jmc4c-complete` plan base. Stop for merges, unrelated/concurrent commits, uncertain ownership,
   or any plan-owned commit already pushed.
2. Commit the final pre-squash timeline entry with all A-phase hashes, verification, enabled and
   deferred manifests, deviations, operator actions, pre-squash tip, and intended resolver tag
   `jmc5a-complete`.
3. Create a timestamped recovery branch and annotated tag for the pre-squash tip. Create a
   verified complete repository-external Git bundle, preferably under
   `/home/quartermaster/backups/Marquee/`. Request permission if required; do not rely only on
   `/tmp`.
4. Record the certified pre-squash tree hash. Through RTK, soft-reset to the exact plan base and
   create one configured-author commit: `jmc5a: establish canonical mutation workflows`.
5. Verify exact tree-hash identity, sole-parent ancestry, clean worktree, recovery refs, and bundle.
6. Create local annotated tag `jmc5a-complete`. Report the resolved commit/tree/base and recovery
   locations. Do not edit the timeline after compaction.
7. Do not push, force-push, delete recovery material, or start JMC5B.

Any ancestry, ownership, backup, or tree mismatch stops the rewrite. Never add agent/model
attribution to commits, tags, files, bundles, or the timeline.

## 11. Out of scope

- audio/subtitle mutations and generation (JMC5B);
- letterbox writes/re-encode and Dolby Vision conversion/publication (JMC5C);
- webhooks and `radarr_upgrade`;
- online restore, public database-reset replacement, authentication, or Docker hardening;
- Projection Room visual rebuild, final test cleanup, zero-failure baseline, or GitHub Actions;
- pushing commits/tags or deleting any recovery material.

## 12. Operator handoff

The final report records the compact tag/hash/tree/base, recovery refs/bundle, exact enabled and
deferred definitions/schedules, schema/OpenAPI/type versions, retained failures, fixture/tool
versions, storage/connection/event/log budgets, every live smoke performed or deferred, and the
exact JMC5B starting condition. It states plainly that JMC5A certifies artwork, backup creation,
healing, and bounded maintenance writes—not source-media remuxing or transcoding.
