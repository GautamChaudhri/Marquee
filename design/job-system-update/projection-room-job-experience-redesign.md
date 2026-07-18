# Projection Room — Job-Specific Experience, Logs, and Diagnostics

**Decided:** 2026-07-12
**Status:** Target product and API design
**Runtime context:** [direct PgQueuer adoption](job-system-pgqueuer-direct-adoption.md)
**Delivery sequence:** [clean-slate migration program](job-system-pgqueuer-migration.md)
**Product research:** [activity comparison](projection-room-activity-comparison.md)
**Progress contract:** [job progress and loading experience](job-progress-and-loading-experience.md)
**Destructive-job presentation bindings:** [JMC5A mutation, artwork, and maintenance](jmc5a-mutation-contracts-artwork-and-maintenance.md),
[JMC5B audio and subtitle mutations](jmc5b-audio-subtitle-mutations.md), and
[JMC5C letterbox, HDR, and runtime retirement](jmc5c-letterbox-hdr-and-runtime-retirement.md)
**Frontend delivery:** [JMC6A shared Activity client and progress](jmc6a-shared-activity-client-and-progress.md)
and [JMC6B Projection Room and feature pages](jmc6b-projection-room-and-feature-pages.md)
**Final certification:** [JMC6C zero-green certification and CI](jmc6c-zero-green-certification-and-ci.md)

## Product decision

Projection Room will become a **job-centric media activity experience**, not a generic dump
of queue tables, JSON documents, host charts, and resource internals.

Its product shell follows the useful part of Sonarr and Radarr's Activity model: **Queue**
for work that is active or still expected to run and **History** for terminal work. Running
is a Queue state, not a separate destination. Batch parents are grouped records inside those
views, not a third activity silo. A separate, lazy **Operations** view remains available to
administrators. The evidence and adopt/adapt/reject decisions are recorded in the
[activity comparison](projection-room-activity-comparison.md).

The primary questions it must answer are:

1. What ran?
2. Which movie, show, season, episode, file, poster, or model did it affect?
3. What exactly was requested?
4. What changed in reality?
5. Did it succeed, partially succeed, skip work, retry, or fail?
6. If something failed, which stage or track failed and why?
7. What can the user do next?
8. Where are the complete logs and raw evidence when deeper diagnosis is necessary?

Queue, worker, database, resource, and host telemetry remains available, but moves to a
separately loaded **Operations** tab. Technical execution details remain accessible on each
job without dominating its default view.

Attaching logs is both practical and valuable. Media systems already treat the job report as
the bridge between an approachable status view and complete diagnostics: Tdarr exposes
downloadable reports and optional full ffmpeg/HandBrake output, and Unmanic exposes full
commands/logs for completed tasks.
[Tdarr job reports](https://docs.tdarr.io/docs/other/job-reports/),
[Tdarr log-size configuration](https://docs.tdarr.io/docs/installation/variables/),
[Unmanic completed tasks](https://docs.unmanic.app/docs/dashboard/completed_tasks/),
[Unmanic live worker logs](https://docs.unmanic.app/docs/dashboard/workers/)

## Problems being corrected

The current detail route and page are generic in the wrong places:

- one `GET /api/jobs/{id}` loads every attempt, reservation, event, child, linked media row,
  and raw document without pagination;
- attempts display JSON metrics/error in table cells;
- result, error, request, plan, and child data render primarily as `<pre>` JSON;
- the UI often shows a raw type and ID instead of durable media identity;
- useful track data already stored in media plans/results is not elevated into the page;
- media failure can be an operation-level string even when users need per-track attribution;
- the event timeline ignores structured detail and is not continuously reconciled;
- there is no durable per-attempt stdout/stderr surface;
- current host metrics describe the API environment, not necessarily the worker that ran the
  job;
- Projection Room refreshes job, queue, worker, resource, current-host, and historical-host
  queries even when the relevant tab is not visible;
- SSE and full-detail polling run at the same time for every active job.
- progress is untyped JSON assembled by incompatible producers, while pages infer a
  percentage from whichever optional keys they recognize;
- one frontend bar often represents both batch completion and the current child/stage, so
  letterbox and poster work can reset or regress when the subject changes;
- active-job rediscovery and EventSource error handling differ by feature page, causing
  otherwise durable work to disappear after refresh or a routine reconnect.

The redesign keeps raw evidence but changes its place: **human explanation first, technical
diagnostics second, raw source last**.

## Information architecture

### Projection Room top level

Projection Room is the application-level **Activity** area. It has two primary product views
and one secondary operational view:

1. **Queue** — running, cancelling, queued, waiting, held, paused, deferred, and retrying
   jobs. Rows show the subject, action, stage/progress, priority, eligibility, attention, and
   a friendly explanation of any wait.
2. **History** — succeeded, partially succeeded, no-change/not-required, failed, cancelled,
   and superseded jobs with server-side search, filters, sorting, and retry lineage.
3. **Operations** — PgQueuer, worker, safety-lock class, PostgreSQL, log storage, scheduler,
   listener, and per-node telemetry. It is separately routed or lazy-loaded and never the
   default Activity view.

Batch parents appear as expandable/grouped Queue or History rows. They show bounded child
counts, aggregate progress/outcome, failed children first, and a link to a server-paginated
child list. There is no Batches primary tab and no download-style Blocklist.

Queue and History never show raw resource JSON or a PgQueuer ticket ID as the headline.

### Activity attention and ordering

Above Queue, a compact attention strip reports:

- running;
- waiting/held;
- retrying;
- needs attention.

The application/sidebar Activity badge reflects the highest active severity (`normal`,
`warning`, or `error`) and may include the affected count. It is not a context-free queue
number. Severity is accompanied by text/iconography and never depends on color alone.

Default server ordering is deterministic:

- Queue: attention severity, running before non-running, priority, eligibility time, then
  enqueue time and canonical ID as a final stable tie-breaker;
- History: finished time descending, then canonical ID descending.

Filters are URL-backed and shareable. Column visibility and density are local user
preferences. Subject, action, and status/outcome are mandatory columns; optional columns may
include feature area, trigger, progress, priority, wait reason, input/output impact, worker,
and timestamps.

### Queue rank and time expectations

Marquee does not promise an exact global queue position or a queued-start ETA. PgQueuer
entrypoint concurrency, priority, `execute_after`, retries, and Marquee advisory safety gates
mean no single total order predicts execution. The API may expose an approximate
`queue_rank` within one primary execution class, labelled accordingly.

Queued rows instead explain what is knowable: eligible-at/retry time, hold or schedule
restriction, primary execution class, priority, and friendly safety-gate wait reason.
Running rows may show ETA and throughput only when a definition supplies credible progress
units.

### Activity actions

Rows and bulk-selection toolbars render only server-authorized `allowed_actions`:

- cancel;
- pause or resume;
- change priority or move toward the top of the same execution class;
- retry terminal work as a new canonical job linked to the original;
- open live/final logs;
- open artifacts;
- open full job detail.

Bulk commands return a result for every selected job so a partial conflict or validation
failure is visible rather than turning the entire operation into an ambiguous toast.

### Job detail

Every detail page uses the same shell with job-specific content:

- **Overview** — default, polished semantic view;
- **Timeline** — durable semantic events;
- **Logs** — live/final per-attempt log viewer and download;
- **Artifacts** — output, report, archive, backup, and validation references;
- **Raw data** — formatted/downloadable request, plan, result, error, and event detail;
- **Execution** — collapsed technical attempt/worker/process/fence/transport information.

The status header stays visible and contains:

- friendly job name;
- subject artwork and durable display title;
- plain-language action headline;
- status/outcome badge and progress;
- created/queued/started/finished/duration;
- cancellation or retry action when valid;
- parent/batch link when applicable.

## Stable presentation contract

Presentation is produced by the backend from versioned domain data. Svelte does not inspect
arbitrary job JSON and guess what it means.

### `JobDefinition`

Every built-in type registers:

```text
JobDefinition
  type
  feature_area
  payload_version
  payload_model
  result_model
  error_model
  queue_class
  retry_policy
  timeout
  safety_requirements
  subject_snapshot_builder
  presentation_family
  presenter
  progress_policy
  action_policy
```

Parent-only batch types also register a definition even when no handler executes them. A CI
coverage test fails if any registered handler, media operation, or constructed parent type
lacks a definition and presenter.

Unknown historical/plugin jobs may use a deliberately labelled generic fallback. No
built-in job may reach that fallback at release.

### `JobPresentation`

The public representation is stable and bounded:

```text
JobPresentation
  version
  job_id
  job_type
  label
  feature_area
  subject
  action
  trigger
  attention
  allowed_actions[]
  status
  outcome
  impact
  sections[]
  warnings[]
  failures[]
  suggested_actions[]
  artifact_summary[]
  diagnostic_links
```

`subject` contains:

- kind (`movie`, `series`, `season`, `episode`, `media_file`, `model`, `system`, or `batch`);
- stable database/external IDs when present;
- display title, year, series title, season/episode numbers and episode title;
- media filename and non-secret display path when relevant;
- poster/artwork URL;
- enqueue-time snapshot timestamp;
- optional link to the current library page.

`action` contains a headline and short explanation, for example:

- “Remove 2 subtitle tracks and 1 audio track”;
- “Generate English subtitles from audio stream 3”;
- “Detect letterbox bars using thorough analysis”;
- “Re-encode to crop 132 px from the top and 132 px from the bottom”;
- “Select a poster from 47 TMDB candidates”;
- “Synchronize Radarr and Sonarr libraries.”

`status` is current state: friendly label/tone, stage, progress, wait/retry reason, current
attempt, elapsed time, cancellation state, eligible/retry time, optional class-local rank,
and running ETA/throughput when meaningful.

`status.progress` uses the versioned `JobProgress` contract defined in
[job progress and loading experience](job-progress-and-loading-experience.md). It contains
separate overall and current scopes, durable current-subject context, a monotonic sequence,
attempt/fence identity, measurement mode, freshness, and only supported metrics. List rows
receive a compact projection of the same contract; detail and feature pages do not reinterpret
handler dictionaries.

`outcome` is terminal meaning: headline, tone, explanation, counts/metrics, whether the
requested effect was actually applied, whether user attention is needed, validation and
atomicity, and a first-class no-change/not-required reason where applicable.

`trigger` records provenance: manual action and initiator, schedule, webhook, policy, batch,
parent job, healing action, or system maintenance. `attention` contains severity, short
reason, affected target/stage, whether media changed, atomicity/rollback state, and suggested
next action. `impact` is a compact typed summary such as input/output size, storage delta,
duration, speed, or validation result; definitions omit irrelevant measures.

### Presentation sections

The backend returns a limited component vocabulary rather than HTML or arbitrary JSON:

| Section kind | Intended UI |
|---|---|
| `facts` | Label/value grid with typed units and links |
| `before_after` | Two-column semantic comparison |
| `change_list` | Requested/actual target rows with outcome badges |
| `track_table` | Audio/subtitle track cards or compact table |
| `metric_cards` | Counts, durations, sizes, confidence, scores, throughput |
| `warnings` | Ordered warning callouts |
| `failures` | Stage/target-attributed error cards |
| `steps` | Friendly execution-stage summary |
| `artifacts` | Safe links, status, size, and retention |
| `children` | Bounded batch child summary and failure drill-down |
| `notice` | Skipped/no-op/retry/quarantine explanation |

Values carry a type (`text`, `integer`, `duration`, `bytes`, `percent`, `language`, `codec`,
`path`, `timestamp`, `boolean`, or `link`) so the frontend formats consistently. Presenter
output never includes markup.

## Semantic progress and loading behavior

Marquee, not PgQueuer or an individual Svelte page, owns semantic progress. Every built-in
definition declares whether its work is determinate, indeterminate, hybrid, or immediate;
its units and denominator source; its stage vocabulary; its native-tool adapter; its
aggregation behavior; persistence cadence; and whether ETA is credible.

The product renders two independent scopes:

- **overall** is monotonic within an attempt and represents the complete sealed request or
  batch;
- **current** represents one subject/stage identified by `scope_id` and may reset only when
  that scope changes.

This prevents a show, episode, movie, or pipeline-stage transition from resetting the
overall bar. A TV letterbox parent can remain “12 of 44 shows” while the current row says
“The Expanse · Season 3 · S03E07 — analyzing sample 4 of 12.” Concurrent batches show a
primary subject plus a bounded “N more running” expansion.

Server presenters translate stable stage keys into plain language. Raw values such as
`waiting_external`, `batch_created`, or handler function names never appear as the primary
status. Opaque probes, provider calls, model loading, and tool phases without a defensible
denominator use named indeterminate activity and elapsed time. They do not render a pulsing
100% bar, guessed percentage, or ETA.

FFmpeg transformations use machine-readable `-progress` data against validated media
duration; MKV remuxes use `mkvmerge --gui-mode`. If timestamps, duration, or units are not
reliable, the current scope degrades to indeterminate. The complete measurement matrix and
write invariants live in the progress contract.

## Durable subject identity

Current subject titles are resolved from live library rows. Historical jobs become obscure
if media is renamed, detached, or deleted. Capture an immutable `subject_snapshot` at job
creation/confirmation.

### Movie snapshot

- Marquee, Radarr, TMDB, and IMDb IDs when present;
- title and year;
- poster/artwork reference;
- media filename/path and media-file ID when applicable;
- edition/quality/HDR/DoVi hints relevant to the job.

### Episode snapshot

- Marquee episode/series/media-file IDs and Sonarr/TVDB IDs when present;
- series title and artwork;
- season/episode number and episode title;
- air date when useful;
- media filename/path;
- multi-episode file membership when applicable.

### Series/season/batch snapshot

- series/season identity and artwork;
- requested scope/filter;
- child-subject count known at sealing;
- correlation/parent identity.

### Non-media snapshot

Models, taste profiles, backup tasks, cache/retention work, and system jobs capture the named
artifact/scope/configuration necessary to explain them later.

The presentation may link to current live metadata, but the historical headline comes from
the snapshot.

## Presenter families

### Audio and subtitle track changes

Applies to:

- `audio_remove`;
- `track_remove`;
- `subtitle_remove`;
- `subtitle_embed`;
- `subtitle_metadata`;
- `audio_reorder`;
- `subtitle_extract`;
- `subtitle_generate`;
- `subtitle_policy`;
- `subtitle_restore`;
- `subtitle_scan` and library scan parents.

Overview shows:

- movie/episode identity and media file;
- container and input signature/plan age;
- requested operation and confirmation/override warnings;
- before/after counts for embedded/external subtitle and audio tracks;
- each targeted track with language, codec, channels/layout, title, source, stream/tool IDs,
  default/forced/HI/VI flags, and requested change;
- actual outcome for each target;
- backup created/restored and its status;
- validation/rescan result;
- generated/extracted path and language/task/provider when relevant;
- coverage before and after;
- partial sidecar failures or all-or-nothing remux failure.

Queue/History rows summarize the requested target counts and surface the first failed target
or stage. Detail preserves requested selectors as well as resolved tracks so a user can see
both what a policy asked for and what the enqueue-time plan targeted. Track facts include
language, codec, channels/layout, title, default/forced/HI/VI flags, embedded/external state,
and stable stream/tool identity. Generated and extracted outputs are linked as artifacts.

### Typed target-outcome contract

Media mutation results must distinguish intention from reality:

```text
MediaMutationOutcome
  atomic
  requested_targets[]
  target_outcomes[]
  before_snapshot
  expected_after_snapshot
  actual_after_snapshot
  validation
  backup
  external_sidecar_outcomes[]
  warnings[]
```

Each target outcome contains:

- stable target key;
- target kind (`audio`, `subtitle`, `sidecar`, or `generated_subtitle`);
- human track snapshot;
- requested action;
- `succeeded`, `failed`, `skipped`, or `not_applied`;
- stage;
- code and human reason;
- actual observed track after rescan when applicable.

For an all-or-nothing remux:

- success marks every requested embedded target succeeded only after validation and rescan;
- tool/validation/publish failure marks every requested embedded target `not_applied`;
- independently removed external sidecars record their own outcome;
- the overall page must not claim partial success unless a side effect actually committed.

This extends data the current mutation code already produces: selected track/audio summaries,
before/after counts, operation, and external removal results. It formalizes failures and
actual post-operation state.

### Letterbox family

Applies to movie/episode/TV detect, apply/remove/revert, re-encode, heal, and batch types.

Show:

- subject/file, source dimensions/aspect, and detected status;
- detection scope, detector version, thorough/fast mode, confidence, sample count/frames,
  variability, and crop measurements;
- requested top/bottom crop and whether it changes tags or pixels;
- actual output dimensions and applied crop/tag state;
- source/target codec, encoder, CPU/GPU acceleration, fallback reason;
- source/candidate/output size, percentage change, speed, and duration;
- HDR and Dolby Vision preservation status;
- tag/pixel validation problems, diagnostic frames/reports, and candidate/backup/original
  paths as safe artifacts;
- batch child successes, skipped/no-bars results, and failures grouped by show/season.

Queue rows emphasize detection/tag/re-encode/revert action, detected crop, confidence,
progress, hardware path, and attention. History rows emphasize no-bars/no-change, validated
crop, revert, preservation, or exact failed stage.

### Poster family

Applies to pipeline, batch, TV batch, heal, reset, rescan, backup, and maintenance jobs.

Show:

- movie/series/season artwork subject;
- candidate and source counts, filtered/rejected/ranked counts, selected poster thumbnail
  and source;
- scorer/profile/model identifiers;
- important gate/rejection summary and confidence/score with a human interpretation where
  meaningful;
- prior versus selected/deployed poster and whether deployment/reset occurred;
- backup status and selected/archive/output links;
- review-required state and reason;
- “analysis succeeded but no candidate selected” as a distinct outcome;
- per-child outcome for batches;
- cache/backup/maintenance counts with dry-run versus applied state.

Pipeline archive JSON remains a raw/artifact diagnostic, not the default result view.
Queue rows show candidate progress, current pipeline stage, selected preview when available,
and review/failure attention without exposing raw model output.

### Dolby Vision/HDR family

Show:

- subject/file and source video/container/codec/profile, bit depth, and color metadata;
- detected Dolby Vision profile/level and HDR metadata;
- requested conversion (`P5 → P8.1`, `P7 enhancement-layer removal`, etc.);
- encoder/acceleration, processing speed, and fallback;
- RPU extraction/injection/preservation result;
- source/output sizes, storage delta, output validation, and atomic publish result;
- backup/original/candidate/staged diagnostic artifacts;
- preservation failures and whether source media remained unchanged;
- exact stage/code when conversion fails.

Queue rows emphasize source-to-target operation, progress/speed, hardware path, and
validation. History rows emphasize the actual HDR/Dolby Vision state, preservation, size
impact, and publish outcome.

### Sync, scan, and integration family

Show:

- requested scope and trigger;
- configured/involved services;
- created/updated/deleted/unchanged/skipped/error counts by movie, series, season, episode, or
  file;
- duration and rate;
- service-specific warnings/failures without exposing credentials;
- child scans queued/completed for scan parents.

### Taste and ML family

Show:

- target library/namespace and source mode;
- model/profile/head identifiers and versions;
- exemplar/label/candidate counts;
- device/provider and major stage timings;
- calibration/training metrics expressed with friendly labels;
- new active artifact and prior artifact link;
- warnings such as insufficient examples or training skipped.

### Maintenance and system family

Show:

- named scope and dry-run/applied mode;
- records/files/bytes affected by category;
- backup/archive identifiers and retention window;
- skipped/protected items and errors;
- whether restart or operator action is required.

### Parent and batch family

Show:

- scope and sealed child count;
- queued/running/succeeded/skipped/failed/cancelled counts;
- overall progress and duration;
- failed children first, with subject/action/error summary;
- server-paginated child list and filters;
- explicit partial-success outcome rather than reducing everything to succeeded/failed.

Parents use the same Queue/History lists as ordinary jobs. The row expands to a bounded
summary; full child data remains a separately paginated resource.

### Feature areas

The main Activity filters emphasize four feature areas:

- `posters`;
- `hdr`;
- `audio_subtitles`;
- `letterbox`.

Supporting definitions use `library`, `ml`, `maintenance`, or `system`. Feature area is a
stable presentation/filter property, not a queue class and not something clients may choose
to alter execution policy.

## Public API contract

### Job lists

`GET /api/jobs` is cursor-paginated. `view=queue|history` selects the lifecycle partition and
its default ordering. It supports bounded server-side text search, filters, and allowlisted
sort keys for:

- phase/outcome;
- feature area, job type, and presentation family;
- subject type/ID and text search;
- movie/series/season/episode/media-file ID;
- parent/correlation/batch membership;
- warning/failure/retry/attention severity;
- trigger;
- created/started/finished time;
- worker/node and execution class for Operations use.

Each row returns only:

- identity/type/label;
- feature area and trigger summary;
- subject summary/artwork;
- action and outcome summary;
- friendly status/stage and compact typed progress, including current subject, overall and
  current measurement modes, freshness, and credible running metrics;
- parent/batch indicator;
- attention, wait reason, eligibility/retry time, optional class-local queue rank, and
  allowed actions;
- compact media impact;
- log/artifact availability indicators without storage keys;
- primary timestamps/duration;
- links.

No raw payload, plan, result, events, attempts, logs, or child graph appears in list rows.

### Activity commands

Command endpoints operate on canonical jobs and validate current phase, desired state,
definition policy, and authorization on the server:

- `POST /api/jobs/{id}/cancel`;
- `POST /api/jobs/{id}/pause` and `/resume`;
- `POST /api/jobs/{id}/priority` with an execution-class-scoped priority;
- `POST /api/jobs/{id}/retry`, returning the original and replacement canonical IDs;
- `POST /api/jobs/actions` for a bounded bulk request.

The bulk response contains one accepted/rejected/conflicted result per requested job. Moving
toward the top changes priority only within the job's primary execution class; it never
claims a global position or bypasses eligibility/safety gates.

### Compact snapshot

`GET /api/jobs/{id}/snapshot` returns current phase/outcome/desired state, typed progress,
stage, current attempt, retry availability/time, progress sequence/freshness, and event
cursor. This is the only endpoint used for periodic active-job reconciliation. It never
returns a page-specific progress shape.

Feature pages rediscover work through `GET /api/jobs?view=queue` using bounded feature-area,
job-type, subject, root/correlation, and parent filters. Every command that creates work
returns its canonical job ID plus snapshot, Activity, and detail links. Local storage is an
optional lookup hint, not an active-job registry.

### Presentation

`GET /api/jobs/{id}/presentation` returns one versioned `JobPresentation`. It performs
bounded domain lookups and presenter work; it does not include full events/logs/children.

Presenter output may be cached by `(job_id, updated_at, presentation_version)` after terminal
state.

### Bounded diagnostics

- `GET /api/jobs/{id}/attempts?cursor=&limit=`;
- `GET /api/jobs/{id}/events?after=&limit=`;
- `GET /api/jobs/{id}/children?cursor=&limit=&outcome=`;
- `GET /api/jobs/{id}/artifacts?cursor=&limit=`;
- `GET /api/jobs/{id}/attempts/{attempt}/logs?after=&limit=&level=&source=`;
- `GET /api/jobs/{id}/attempts/{attempt}/logs/stream`;
- `GET /api/jobs/{id}/attempts/{attempt}/logs/download`;
- `GET /api/jobs/{id}/raw/{request|plan|result|error}`;
- `GET /api/jobs/{id}/raw/events/{event_id}` for a complete event detail document.

All cursors are stable and limits have conservative maximums.

### Clean-slate contract

Marquee is unreleased and the approved implementation starts from an empty target database.
Do not retain the existing unbounded detail/media-job shapes as compatibility adapters, old
ID resolvers, or JSON-scanning bridges. Projection Room and feature pages move directly to
the bounded canonical APIs. Media routes resolve through an indexed 1:1 canonical job link.

## Semantic event delivery

Use one durable event cursor and one broadcaster per API instance:

1. Job/event transactions append `job_events` and emit a PostgreSQL notification containing
   only the newest cursor.
2. The broadcaster tails persisted events once.
3. It multiplexes authorized job updates to local clients.
4. SSE frames use the durable event ID.
5. Reconnect uses `Last-Event-ID` and queries the gap.
6. Low-frequency polling repairs a missed notification.
7. Slow clients use a bounded buffer and reconnect rather than accumulating memory.

Projection Room opens one session/account stream, not one database-polling EventSource per
job. Deltas update list/snapshot state. A slow periodic snapshot reconciliation protects
against client bugs and missed delivery without fetching full detail.

The event vocabulary separates lifecycle from progress. `state` cannot contain action words
such as `progress` or `start` that the frontend might mistake for job status. Typed
`progress.updated` events carry both the durable event cursor and progress sequence. Clients
discard duplicate/late sequences and use the compact snapshot to repair gaps.

## Per-attempt log design

### Storage

API and workers already share the `DATA_DIR` Docker volume. Store logs under a confined
root such as:

```text
data/job-logs/{job_id}/{attempt_number}/
  active.jsonl
  final.jsonl.gz
```

The database stores a storage key relative to that root, never an arbitrary absolute path.
Terminal compression replaces the active file atomically.

Defaults:

- retention: 30 days, aligned with `JOB_RETENTION_DAYS`;
- maximum: 100 MB per attempt before compression;
- at cap: stop accepting ordinary lines and append one durable truncation record;
- terminal logs remain downloadable until retention deletes job/log metadata and file;
- active jobs expose live tail even if the final compressed file does not exist yet.

Future multi-host deployments may implement the same storage interface with object storage.
The current architecture does not require that extra service.

Queue and History rows use list-safe availability flags to open the current/latest attempt
log in a drawer or the job's Logs tab. They do not embed log lines in list responses. A
failed row therefore offers its report directly without making diagnostics the primary
presentation.

### Log record

Each JSONL record contains:

- monotonically increasing line/cursor number;
- UTC timestamp;
- `job_id`, attempt, and fence;
- level;
- semantic stage;
- source (`marquee`, handler name, `ffmpeg.stderr`, `ffmpeg.stdout`, `mkvmerge.stderr`,
  provider, ML service, etc.);
- message;
- optional small structured fields;
- truncation/redaction flags.

The UI formats fields but the download preserves exact captured records.

### Capture

- A `contextvars` job logging handler tees Python records into the current attempt file.
- The approved subprocess launcher tees stdout/stderr without waiting for process completion,
  preventing full pipes.
- Commands are recorded in a sanitized structured record before launch.
- Exit code, signal, runtime, and bounded stderr tail are copied into attempt diagnostics.
- ML services propagate job/attempt/fence context and emit to the same logical stream.
- Infrastructure logs not associated with a job stay in process/container logging and the
  Operations view.

### Redaction and safety

Redact by key and by known value:

- API keys/tokens/secrets;
- authorization/cookie headers;
- database DSNs/passwords;
- provider credentials;
- environment variables marked secret;
- sensitive command arguments.

Do not indiscriminately redact media paths: they are often necessary to diagnose mapping
problems and are already visible to the Marquee administrator. Still escape control/ANSI
sequences, never render log HTML, and apply authorization/path confinement to downloads.

## Artifacts and raw data

### Artifact metadata

`JobArtifact` records:

- job and optional attempt;
- stable artifact kind/label;
- storage backend/key or domain object reference;
- MIME type, size, checksum, created/expiry time;
- status (`available`, `missing`, `expired`, `quarantined`, or `deleted`);
- safe preview/download URL;
- small presenter metadata.

Physical examples:

- pipeline run archive/report;
- selected/generated poster preview;
- letterbox candidate and validation report;
- subtitle output;
- sanitized command report;
- backup/original reference;
- quarantined staging diagnostics.

On failure, definitions may register bounded staged output, validation reports, sampled
frames, probe output, or tool diagnostics that would otherwise be deleted. Every retained
failure artifact records why it was kept, whether it is safe to download, its size, and its
expiry. Retention refuses arbitrary directories and never turns a failed staging tree into
an unbounded archive.

Never turn an arbitrary result path into a downloadable artifact. Registration validates
the path against an approved storage root or creates a domain reference served by a
dedicated safe endpoint.

### Virtual raw documents

Request, plan, result, error, checkpoint, and event detail already live in PostgreSQL. Expose
them as virtual JSON artifacts; do not duplicate them into files merely to enable download.

The Raw Data tab provides:

- searchable collapsible tree;
- copy/download;
- schema version;
- redaction notice;
- links from pretty fields to their raw source when helpful.

Raw data is never the only presentation of a built-in job.

## Execution diagnostics

The collapsed Execution tab/section shows:

- canonical and PgQueuer ticket identity;
- attempt/fence and transport attempt number;
- worker/node/build and execution class;
- process/cgroup identity and exit/signal;
- admitted/start/finish timing;
- queue wait, runtime, cancellation, and retry classification;
- active advisory safety gates in friendly terms;
- transport held/retry state;
- log size/truncation and artifact count.

Do not expose raw PgQueuer row JSON by default. A diagnostics download may include a
redacted transport snapshot for administrators.

## Operations tab

Load only when visible. Separate panels cover:

### PgQueuer

- depth and oldest age by execution class;
- picked, deferred, retrying, and failed-held counts;
- enqueue/dequeue/completion/failure throughput;
- queue wait and runtime percentiles;
- listener health and polling fallback;
- schedule health/next execution/misfires;
- schema version, table size, dead tuples, and autovacuum health.

### Workers and devices

- worker/node/build status and drain state;
- registered classes and handler/payload versions;
- CPU/RAM/GPU/VRAM/device/tool versions;
- media path mapping/read-write probes;
- current jobs and local concurrency;
- per-node telemetry rather than presenting API-host GPU as worker GPU.

### Safety and storage

- waiting/held advisory safety gates by friendly category;
- file/GPU/media-write/maintenance contention duration;
- orphan/quarantined attempt count;
- job-log bytes, truncation count, retention backlog, missing files;
- artifact/quarantine storage.

### API and database

- API latency/error/event-broadcaster metrics;
- active/slow SSE clients and event lag;
- pool use and connection budget by role;
- query latency, lock timeout, long transaction, and database size;
- raw/rollup metrics retention.

Host history uses cached per-node samples and server-side rollups with a fixed maximum point
count. Hidden panels stop polling and abort superseded requests.

## Frontend component model

Implement reusable presentation primitives rather than one giant conditional page:

- Activity attention strip and severity badge;
- configurable Queue/History table-card shell;
- URL filter/search/sort controls and bulk-action toolbar;
- expandable batch parent row;
- shared `JobProgressStore` plus compact/expanded `JobProgressCard` used by Activity and
  every initiating feature page;
- separate overall and current-work progress surfaces, determinate/indeterminate variants,
  freshness/reconnect state, and bounded concurrent-subject expansion;
- subject header/artwork;
- action and outcome hero;
- typed fact grid;
- track card/table and change outcome row;
- before/after comparison;
- warning/failure callout;
- metric cards;
- artifact card/gallery;
- child outcome list;
- virtualized event timeline;
- virtualized live log viewer;
- JSON tree/raw download;
- collapsed execution inspector;
- Operations panels.

The backend section vocabulary selects/composes these components. Family-specific
components may enhance track, poster, or letterbox presentation, but they consume typed
contracts rather than original handler dictionaries.

The shared store reconciles initial server discovery, one multiplexed SSE connection, and
low-rate compact snapshots. EventSource interruption changes connection state to
`reconnecting`; it never marks the job failed, clears its card, or invokes a page-specific
failure handler. Snapshot intervals have an in-flight guard, cancellation, bounded backoff,
and hidden-tab cadence reduction. A terminal server snapshot—not a network error—moves a
job from Queue to History.

Accessibility requirements:

- status/outcome never relies only on color;
- keyboard-accessible tabs, filters, details, and downloads;
- tables/cards have compact responsive forms;
- live updates use restrained announcements;
- long logs and JSON do not trap focus or force horizontal page scrolling;
- timestamps, byte units, language, codec, booleans, and percentages are humanized
  consistently.

## Performance rules

1. Lists, events, attempts, children, logs, and artifacts are paginated/capped.
2. The compact snapshot never loads event, child, or log history.
3. Presentation fetch performs bounded batch joins and no per-row JSON scan.
4. Terminal presentation may be cached by version/update timestamp.
5. One event tailer exists per API instance, not per client/job.
6. Active tracking uses stream deltas plus low-rate compact reconciliation.
7. Hidden tabs/panels do not refresh.
8. Each interval has an in-flight guard and cancellation.
9. Long histories use server rollups and fixed point counts.
10. Log viewer reads cursor windows/streams and virtualizes lines.
11. Raw data loads only when its tab is opened.
12. Operations queries use PgQueuer/Marquee metric projections, not repeated raw-table scans.

## Testing and acceptance

### Presenter contract

- every built-in handler, media operation, and parent type has a presenter;
- payload/result versions have fixtures and upcast tests;
- presentation remains stable if live movie/episode/media rows are deleted or renamed;
- missing/corrupt optional detail produces a friendly diagnostic, not a page failure;
- no secret appears in presentation, raw downloads, logs, artifacts, or command records.

### Job-family golden fixtures

Cover at least:

- movie and episode subtitle/audio remove success;
- remux failure with all targets `not_applied`;
- external sidecar partial failure;
- metadata before/after and audio reorder;
- generation skipped/succeeded/provider failed/embed failed;
- letterbox no-bars/detected/re-encode GPU/fallback/validation failed;
- poster selected/no candidate/pipeline failed/batch partial;
- DoVi analyze and conversion/preservation failure;
- sync counts with one integration failure;
- ML training success/insufficient data;
- backup/maintenance dry-run/applied;
- batch mixed outcomes and cancelled parent.

For each of the four primary feature areas, fixtures cover both the compact Queue/History
row and the full detail presentation. Supporting job families also require typed fixtures.

### Logs and raw evidence

- concurrent Python/stdout/stderr ordering/cursors;
- live tail and terminal gzip download;
- 100 MB truncation marker and no further ordinary writes;
- crash leaves a readable active/finalizable file;
- retention deletes metadata and file together/idempotently;
- redaction of every configured secret class;
- ANSI/control/HTML content displays as text;
- path traversal and arbitrary-file registration are rejected;
- raw virtual artifacts round-trip the canonical redacted JSON.

### UI and API

- `view=queue|history` partitions every lifecycle state exactly once;
- Queue ordering is stable across pages and equal-value tie-breakers;
- History ordering is newest-finished first;
- URL search/filter/sort state round-trips and browser navigation restores it;
- column/density preferences persist without hiding subject/action/status;
- batch parents expand without loading an unbounded child graph;
- attention counts and sidebar severity update/reconcile correctly;
- `no_change`/`not_required` is visually and semantically distinct;
- class-local rank is labelled approximate and absent when meaningless;
- allowed actions match server phase/policy and stale commands conflict safely;
- bulk partial success returns and renders one result per selected job;
- operator retry creates and displays original/replacement lineage;
- active and terminal rows open the correct attempt log/report;
- server-side filters return complete results beyond the first 100 rows;
- snapshot query count is independent of history size;
- each diagnostic endpoint enforces limits/cursors;
- SSE reconnect/replay and slow-client behavior;
- refresh with empty local storage rediscovers all applicable active jobs;
- network interruption retains the last good card with reconnecting/stale freshness and
  later reconciles without progress regression;
- overall progress is monotonic, current progress resets only with a new `scope_id`, and
  late/wrong-fence updates are ignored;
- determinate, indeterminate, hybrid, and immediate policies render without invented
  percentages or ETA;
- movie, series, season, episode, file, track, poster, model, aggregate-parent, and concurrent
  child current-subject fixtures;
- letterbox TV, poster batch, and Dolby Vision analysis regressions prove stable overall
  progress and correct current context;
- FFmpeg and mkvmerge fixtures verify native progress parsing, coalescing, and safe
  indeterminate fallback;
- hidden-tab/in-flight guards;
- mobile/narrow layout and keyboard/screen-reader tests;
- long logs/events/children stay responsive through virtualization;
- API remains within the latency gates defined by the migration program under saturated
  workers and multiple Projection Room clients.

## Completion criteria

Projection Room's final data correctness depends on
[JMC6G](jmc6g-execution-truth-progress-and-evidence-closure.md) for canonical outcome/progress/
evidence truth and [JMC6H](jmc6h-product-convergence-retirement-and-final-certification.md) for real
poster/ML projections and consumer linkage. Presenter goldens cannot substitute for the underlying
product operation.

Projection Room is complete when:

- Queue and History are the only primary Activity views and running jobs appear in Queue;
- batch parents are understandable and drillable without a separate Batches tab;
- a user can identify the movie/show/episode/file and requested action from every list row;
- every row identifies its feature area, trigger, attention, and valid actions;
- every built-in detail page explains requested versus actual outcome without opening JSON;
- audio/subtitle jobs identify every requested track and its observed outcome;
- AI poster, HDR, audio/subtitle, and letterbox jobs expose their required family-specific
  list and detail facts;
- failures identify stage/target, media-change/atomicity state, and next action where
  possible;
- correct no-op work ends as no-change/not-required rather than ambiguous success/skip;
- complete bounded logs and raw evidence are accessible but visually secondary;
- technical infrastructure data lives in the lazy Operations tab;
- no active-job page combines per-job DB-polling SSE with unbounded detail polling;
- feature pages and Activity render the same durable typed progress through one shared
  store/component family;
- every long-running definition declares honest measurement semantics and no client infers
  percentage from arbitrary job JSON;
- refresh, navigation, SSE interruption, and temporary API/database failure do not hide or
  falsely fail active work;
- batch overall progress never resets when its current show, season, episode, movie, or stage
  changes;
- built-in jobs never rely on the generic JSON fallback;
- query, event, log, and history costs remain bounded as job history grows.
