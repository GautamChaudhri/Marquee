# JMC2B — Definition Registry and Execution Policies

**Previous plan:** [JMC2A canonical model and configuration](jmc2a-canonical-model-and-configuration.md)  
**Progress architecture:** [job progress and loading experience](job-progress-and-loading-experience.md)  
**Next plan:** [JMC2C presentation and API contracts](jmc2c-presentation-and-api-contracts.md)

> **For the implementing agent:** Read `AGENTS.md`, `CLAUDE.md` when present,
> `design/plans/README.md`, `design/plans/04-television-backend.md` §0, the complete JMC1
> plan and timeline, JMC2A, the shared JMC2 timeline, and this document **in full** before
> changing code. Decisions below are final. **Verify in code** means inspect the current
> symbol and every reference before editing; line anchors and inventories may drift.
>
> **Shared JMC2 timeline:**
> `design/job-system-update/jmc2-canonical-product-timeline.md`. JMC2B must read it, verify
> JMC2A's commits and gates against Git and the working tree, then append to the same file
> after every phase commit. Never create a second JMC2 timeline.
>
> **Git authorship:** use only the repository's configured Git user. Never add yourself, a
> model, or an assistant as author, co-author, contributor, or generator. No
> `Co-Authored-By`, “Generated with,” model-name, or assistant-name attribution is allowed.

**Goal:** Replace Marquee's fragmented handler decorators and media-operation maps with one
authoritative, typed `JobDefinition` registry covering every built-in job, parent, policy,
subject, configuration dependency, and honest progress strategy—without enabling any
non-JMC1 handler for dispatch.

**Ordering:** second of three JMC2 plans. JMC2A must be complete. JMC2C must not begin until
this plan is complete and verified in the shared timeline.

## 1. Preconditions and stop gates

Before implementation:

1. Verify JMC2A's final commits, Alembic head, clean target schema, configuration catalog,
   retained pytest failures, and frontend gates from the shared timeline.
2. Recreate a disposable database and prove the implemented schema—not merely the plan—has
   no transitional or lifecycle-bearing legacy runtime objects.
3. Verify only `control/system_noop` is dispatch-enabled and no custom worker, legacy writer,
   or inline handler can start.
4. Re-run a source inventory of decorators, handler maps, media-operation maps, route-created
   job types, schedule callbacks, healing jobs, and parent-only batch types. Record drift
   against §6 before editing.
5. Record branch/HEAD, working-tree ownership, configured Git author, full pytest/Ruff
   baseline, schema checks, and affected frontend baseline in the shared timeline.

Stop rather than papering over an incomplete JMC2A configuration or subject model. Preserve
unrelated changes, especially work owned by another agent.

## 2. Locked decisions

| ID | Decision |
|---|---|
| R1 | `JobDefinitionRegistry` is the sole authority for built-in job type, schemas, versions, execution class, timeout, retry, safety, configuration dependencies, subject building, progress, actions, and presenter key. |
| R2 | Existing decorators and media-operation maps become migration inventory or thin registration adapters only. They must not retain independent policy defaults. |
| R3 | Every known built-in type—including parent-only and future-disabled work—has exactly one definition. Unknown or duplicate types fail startup/coverage tests. |
| R4 | Only `system_noop` remains enabled for dispatch. Describing a non-JMC1 job does not make its legacy writer or handler executable. |
| R5 | Payload, result, and error documents are strict versioned Pydantic models. Version 1 types reject unknown versions and extra fields unless a field is explicitly extensible. |
| R6 | Provide an upcaster protocol, but implement only semantically real conversions. Never add identity chains merely to imply compatibility. |
| R7 | Clients choose intent and payload values, never entrypoint, timeout, retry, concurrency, safety, progress strategy, or server action capabilities. |
| R8 | Unsafe mutation defaults to one transport attempt unless a definition documents a fenced/staged idempotency proof and explicitly opts into retry. |
| R9 | Every definition has an immutable enqueue-time subject snapshot. Snapshot building may use live projections at enqueue but rendering cannot require them later. |
| R10 | Every long-running definition declares truthful determinate, indeterminate, hybrid, or no-progress behavior. Missing totals never become invented percentages or ETAs. |
| R11 | The complete `JobProgress` value contract is implemented now. Chunk 3 implements fenced writes, tool adapters, coalescing, and multiplexed delivery. |
| R12 | Progress stage keys are stable machine identifiers; labels are presenter-owned human language. Raw keys never become primary UI text. |
| R13 | Action capabilities are computed server-side from definition policy plus canonical state. Registration does not grant an action unconditionally. |
| R14 | Every definition declares bounded configuration keys. Jobs snapshot only relevant non-secret `next_job`/effective values from JMC2A. |
| R15 | Every built-in has a non-generic presenter key. JMC2C supplies the implementations and makes presenter coverage executable. |
| R16 | Webhook is a reserved trigger value but remains disabled. Do not restore webhook routes, tests, or writers. |
| R17 | Existing failures may shrink but the retained set may not grow. Run full pytest and `ruff check marquee tests` after every phase; never run `ruff format`. |

## 3. Stable taxonomy

Define shared string enums whose serialized values become product contracts:

- **Execution class:** `control`, `network`, `cpu`, `media_read`, `media_write`, `gpu`,
  `maintenance`.
- **Primary feature area:** `ai_posters`, `hdr`, `audio_subtitles`, `letterbox`.
- **Supporting area:** `library_integrations`, `ml_taste`, `maintenance`, `system`.
- **Trigger provenance:** `manual`, `schedule`, `policy`, `batch`, `parent`, `healing`,
  `system`; reserve `webhook` but reject it while disabled.
- **Effect safety:** `read_only`, `staged_idempotent`, `unsafe_mutation`.
- **Progress strategy:** `determinate`, `indeterminate`, `hybrid`, `none`.
- **Attention:** the canonical levels and reasons established by JMC2A and the Projection Room
  design; do not duplicate a frontend-only severity enum.
- **Actions:** `cancel`, `pause`, `resume`, `change_priority`, `retry`, `open_logs`,
  `open_artifacts`, `open_detail`. Keep execution-class “move toward top” semantics within
  `change_priority`; never promise an exact global position.

Enum additions are explicit schema/API changes. Do not silently coerce unknown strings.

## 4. `JobDefinition` contract

Each immutable definition must contain:

- canonical `job_type`, label key, feature area, presentation family, presenter key;
- `enabled` and migration state, with a reason when disabled;
- payload, result, and error model adapters plus current/supported versions;
- explicit upcaster lookup keyed by source version;
- primary execution class/entrypoint and bounded timeout policy;
- retry classifier and effect-safety policy;
- subject snapshot builder;
- progress policy and allowed stable stages;
- action-capability policy;
- bounded configuration-key selection;
- parent/child eligibility and aggregation policy where applicable.

Registration is complete before application readiness succeeds. The registry must offer
bounded lookup and iteration APIs without importing route modules or starting workers. A
definition is data plus policy functions; it must not hide database sessions or global
mutable settings.

### 4.1 Document envelopes and version handling

- Queue payload remains the JMC1 transport envelope only: canonical `job_id`, payload
  version, and dispatch generation. Domain payload stays in Marquee's canonical row.
- Validate request before canonical enqueue. Validate result/error before terminal commit.
- Store explicit document versions even where all current versions are `1`.
- Reject unsupported future versions with a typed permanent error; never guess.
- Upcasting produces the current model without modifying historical stored JSON.
- Error models distinguish safe user-facing summary/remediation from bounded diagnostics.
  They must not contain secrets, arbitrary environment data, or unconfined paths.

## 5. Durable subject and progress contracts

### 5.1 `SubjectSnapshot`

Implement a versioned discriminated union covering:

- movie;
- series;
- season;
- episode;
- media file;
- audio/subtitle track;
- poster/candidate set;
- model/profile/training subject;
- aggregate batch;
- maintenance scope;
- system work.

Every variant carries a stable display identifier and the most specific useful hierarchy.
Media variants preserve enqueue-time movie/show title, year, season/episode code and title,
file display name, media kind, artwork key, and applicable IDs. Track variants preserve
language, codec, channels, title, flags, index/UID and embedded/external state. Snapshot
fields are immutable evidence, not a live projection cache.

Snapshot builders must:

- run inside the canonical enqueue transaction;
- fail clearly when a required subject does not exist;
- tolerate optional artwork or integration metadata;
- sanitize paths and exclude secrets;
- remain renderable after all nullable live references are removed;
- support parent batches without embedding unbounded child lists.

### 5.2 `JobProgress`

Implement the versioned contract from
`job-progress-and-loading-experience.md` with:

- monotonic sequence, attempt/fence identity, version and update time;
- semantic headline, stable stage key, presenter label key and liveness/freshness;
- durable current-subject snapshot;
- separate `overall` and `current` scopes;
- determinate/indeterminate mode, unit, completed, total and server-computed percentage;
- current `scope_id`, which alone permits a current-scope reset;
- elapsed time, credible ETA capability, speed/FPS/bytes/throughput where valid;
- wait reason, warnings and bounded concurrent-subject summaries.

Validate these invariants in pure contract tests:

- clients never supply percentages;
- overall progress cannot regress within a fenced attempt;
- current progress can reset only with a changed `scope_id`;
- completed never exceeds total and zero/unknown denominators are not determinate;
- failed/cancelled retains the last measurement rather than becoming 100%;
- success/no-change has a coherent completed presentation;
- stale sequence/fence identity is distinguishable for Chunk 3 rejection;
- ETA is absent unless the policy marks its denominator and rate credible.

### 5.3 `ProgressPolicy`

Each definition declares:

- strategy and nesting/aggregation behavior;
- unit and denominator source;
- stable allowed stages and presenter label keys;
- native tool adapter identifier, if supported;
- persistence cadence/max snapshot staleness for Chunk 3;
- ETA capability and credibility conditions;
- whether concurrent child summaries are allowed.

Use FFmpeg machine-readable `-progress` and MKVToolNix `--gui-mode` only where the actual
operation supports them. Reject frame-rate guesses, arbitrary stage percentages, and fake
denominators. Instant operations use a short busy state with `none`, not a decorative 0–100
bar.

## 6. Required built-in inventory

The implementer must regenerate this inventory from current source and reconcile every
difference in the shared timeline. The known starting inventory is:

- **Generic/registered:** `system_noop`, `poster_heal`, `letterbox_heal`,
  `letterbox_detect`, `letterbox_detect_episode`, `letterbox_detect_tv_scope`,
  `letterbox_apply`, `letterbox_remove`, `letterbox_apply_tv_scope`,
  `letterbox_revert_tv_scope`, `backup_create`, `taste_rebuild`, `taste_map`,
  `library_sync`, `subtitle_scan_all`, `audio_subs_deep_scan`, `radarr_upgrade`,
  `poster_pipeline`, `poster_pipeline_batch`, `poster_pipeline_tv_batch`,
  `learned_head_train`, `pipeline_cache_clear`, `poster_deploy_reset`, `poster_rescan`,
  `poster_backup_all`, `poster_maintenance`, `job_retention_purge`,
  `system_metrics_purge`, `dovi_analyze`, and `dovi_convert`.
- **Media operations:** `subtitle_scan`, `audio_remove`, `track_remove`,
  `subtitle_remove`, `subtitle_embed`, `subtitle_metadata`, `audio_reorder`,
  `subtitle_extract`, `subtitle_generate`, `subtitle_policy`, `subtitle_restore`, and
  `letterbox_reencode`.
- **Known constructed parent-only types:** `subtitle_generate_batch`,
  `dovi_analyze_batch`, `letterbox_detect_tv_batch`, `letterbox_detect_batch`,
  `letterbox_apply_batch`, and `letterbox_reencode_tv_batch`.

Do not assume this list is complete. Search literal job creation, schedule callbacks, healing,
retry creation, route helpers and tests. `radarr_upgrade` remains defined but webhook-triggered
creation remains disabled.

### 6.1 Minimum family policy expectations

| Family | Typical class | Progress expectation | Safety default |
|---|---|---|---|
| Poster analysis/ranking | `gpu` or `cpu` | hybrid candidates/sources/stages, separate current subject | read-only |
| Poster deployment/reset | `network` or `media_write` as verified | named stages and target outcomes | staged/idempotent only if proven |
| HDR/Dolby Vision analysis | `media_read` | stable outer count; indeterminate opaque probes | read-only |
| HDR/Dolby Vision conversion | `media_write`/`gpu` as verified | hybrid outer count plus FFmpeg-native duration progress | unsafe unless staged/fenced proof |
| Audio/subtitle scans | `media_read` | files/tracks when totals known | read-only |
| Audio/subtitle mutations | `media_write` | hybrid tracks/stages plus MKVToolNix/FFmpeg native progress | unsafe unless staged/fenced proof |
| Letterbox detection | `media_read` | show/season/episode/sample context; opaque steps indeterminate | read-only |
| Letterbox apply/revert/re-encode | `media_write`/`gpu` as verified | stable outer scope plus current file/tool progress | unsafe unless staged/fenced proof |
| Library/integration sync | `network` | pages/items when upstream total exists, otherwise count-so-far | read-only product effect |
| ML/taste training | `gpu` or `cpu` | loading plus epochs/candidates/batches when instrumentable | read-only product effect |
| Maintenance/backups | `maintenance` | records/files/bytes when known, otherwise named stages | definition-specific |
| Parent batches | child class is not inherited automatically | aggregate terminal counts and concurrent child summaries | no direct media mutation |
| Instant system work | `control` | `none` | definition-specific |

The table is a starting expectation, not permission to choose an entrypoint without inspecting
the handler's actual I/O and process behavior.

## 7. Retry, actions, and aggregation

- Retry classifiers return permanent, transient-with-delay, cancelled, or unsafe. PgQueuer
  persists/schedules the chosen transport retry; Marquee owns the classification.
- Operator retry always creates a new canonical successor and preserves lineage.
- Parent aggregation has explicit fixed/sealed child-set semantics, outcome precedence, and
  no-change/partial-success rules. It never infers progress from transport rows.
- Allowed actions are computed from definition, phase, desired state, effect safety,
  active attempt, child state and evidence availability.
- Pause is capability-gated. Do not advertise it for work that can only be cancelled.
- Priority changes are constrained to the definition's execution class.
- Logs/artifact actions may report unavailable until Chunk 3, but definitions still declare
  whether those evidence kinds can exist.

## 8. Implementation phases

Each phase ends with focused tests, the retained full pytest comparison,
`ruff check marquee tests`, applicable schema/frontend checks, one short lowercase commit,
and a shared-timeline update.

### Phase B0 — verify JMC2A and freeze inventory

- Verify JMC2A implementation and baseline.
- Produce machine-checkable inventories of handlers, constructors, media operations and
  parents.
- Freeze enums and registry interfaces with contract tests.

### Phase B1 — document envelopes and registry core

- Implement typed request/result/error adapters, version lookup and real upcaster protocol.
- Implement registry construction, startup validation and disabled dispatch behavior.
- Remove independent policy defaults from existing maps without enabling handlers.

### Phase B2 — subject snapshots and configuration selection

- Implement every snapshot variant and definition builder.
- Connect bounded configuration-key selection to JMC2A snapshots.
- Prove snapshots remain complete after live-subject retirement/deletion.

### Phase B3 — progress, retry, safety, actions, and aggregation

- Implement progress contracts/invariants and every progress policy.
- Implement retry/effect/action policy types and parent aggregation rules.
- Keep high-frequency persistence/native adapters deferred to Chunk 3.

### Phase B4 — complete built-in manifest and certification

- Define every inventoried job and parent, including disabled future migrations.
- Eliminate generic fallbacks and duplicate authorities.
- Prove only `system_noop` is enabled and certify the complete matrix.

## 9. Acceptance matrix

JMC2B is not complete until automated evidence proves:

- every handler, route construction, media operation, schedule/healing producer and parent
  maps to exactly one definition;
- there is no duplicate, unknown, unclassified or generic-fallback built-in;
- payload/result/error versions validate, extra/unknown fields fail, and unknown future
  versions are rejected;
- any implemented upcaster performs a real documented conversion;
- clients cannot override server execution/retry/safety/progress policy;
- all snapshot variants survive deletion of optional live rows and contain no secrets;
- every long-running definition has an honest progress policy and every immediate one
  explicitly declares `none`;
- overall/current progress invariants cover letterbox/poster reset cases and the Dolby Vision
  `batch_created` stalled-display case;
- unsafe definitions default to one attempt unless a tested exception is documented;
- parent aggregation distinguishes success, partial success, no-change, failure and
  cancellation;
- action capabilities are state-aware and execution-class scoped;
- only `system_noop` remains dispatch-enabled;
- retained pytest failures do not grow and Ruff is green.

## 10. Out of scope

- enabling or executing non-noop handlers;
- backend presenter implementations and public job API replacement (JMC2C);
- fenced progress persistence, native tool parsing, process safety, logs/artifacts and SSE
  (Chunk 3);
- Projection Room visual components/shared client progress store (Chunk 6);
- webhooks, auth, public reset replacement, Docker hardening or CI modernization;
- legacy payload/history compatibility.

## 11. Operator handoff

The shared timeline must identify final commits, exact retained failures, reconciled inventory,
every disabled/migrated definition, policy exceptions and their evidence, pending operator
work, and the exact JMC2C starting point.
