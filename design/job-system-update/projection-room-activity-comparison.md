# Projection Room Activity Experience — Comparative Research

**Reviewed:** 2026-07-12
**Status:** Primary-source product comparison and Marquee decisions
**Resulting design:** [Projection Room redesign](projection-room-job-experience-redesign.md)
**Delivery:** [clean-slate PgQueuer program](job-system-pgqueuer-migration.md)
**Progress design:** [job progress and loading experience](job-progress-and-loading-experience.md)
**Implementation:** [JMC6A shared Activity client and progress](jmc6a-shared-activity-client-and-progress.md)
and [JMC6B Projection Room and feature pages](jmc6b-projection-room-and-feature-pages.md)

## Purpose and method

This document compares activity, queue, history, job-report, and processing-result surfaces
in Sonarr, Radarr, Tdarr, FileFlows, and Unmanic. It answers a product question rather than a
runtime question: what should Marquee show so a person can understand and control its work
without reading queue internals or raw JSON?

The review used current official documentation and upstream source as of the review date:

- [Sonarr Activity documentation](https://wiki.servarr.com/sonarr/activity) and
  [Activity source](https://github.com/Sonarr/Sonarr/tree/develop/frontend/src/Activity);
- [Radarr Activity documentation](https://wiki.servarr.com/radarr/activity) and
  [Activity source](https://github.com/Radarr/Radarr/tree/develop/frontend/src/Activity);
- [Tdarr workers and outcomes](https://docs.tdarr.io/docs/nodes/workers/),
  [job reports](https://docs.tdarr.io/docs/other/job-reports/), and
  [hold behavior](https://docs.tdarr.io/docs/library-setup/source-options/);
- [FileFlows Dashboard](https://fileflows.com/docs/webconsole/dashboard),
  [Files](https://fileflows.com/docs/webconsole/files/),
  [version history](https://fileflows.com/docs/versions),
  [Processing Summary](https://fileflows.com/docs/webconsole/reporting/processing-summary),
  and [Optimized Files](https://fileflows.com/docs/webconsole/reporting/optimized-files);
- [Unmanic completed tasks](https://docs.unmanic.app/docs/dashboard/completed_tasks/) and
  [worker progress/logs](https://docs.unmanic.app/docs/dashboard/workers/);
- [FFmpeg progress output](https://www.ffmpeg.org/ffmpeg.html),
  [MKVToolNix GUI progress guidance](https://help.mkvtoolnix.download/t/mkvmerge-events/656),
  and the closed [Tdarr inaccurate progress/ETA report](https://github.com/HaveAGitGat/tdarr/issues/1236)
  as implementation evidence and a negative case.

Descriptions under **Observed** report what those products expose. Decisions under
**Marquee use** are recommendations for Marquee; they are not claims about the other apps.
Screenshots are useful context but are not treated as a stable API contract.

## Comparison matrix

| Product | Observed activity model | Useful information/actions | Marquee use |
|---|---|---|---|
| Sonarr | Activity contains Queue, History, and Blocklist; Queue represents recognized downloads still awaiting import, while History records grabs, imports, failures, deletes, and upgrades | Status icons and explanatory tooltips, filters, optional columns, pagination, sorting, selection, refresh, removal/manual actions, event details, and attention coloring | **Adapt:** Queue + History shell, attention severity, status explanations, filters/options, and contextual actions. **Reject:** download-client and blocklist semantics |
| Radarr | Same Activity vocabulary as Sonarr, applied to movie downloads/import history | Subject-aware queue/history, filters, table options, information dialogs, failure handling, and action-specific history icons | **Adapt:** durable movie identity, compact action icons, configurable history, and clear terminal event types. Do not copy release/indexer fields |
| Tdarr | Separate staging and status groupings for transcode/health-check work, including success/not-required and error/cancelled outcomes | Worker/node association, hold behavior, active and historical job reports, task report history, last tool output by default, optional full FFmpeg/HandBrake output | **Adapt:** first-class `not_required`, held/waiting reasons, report access everywhere, attempt history, worker evidence. **Reject:** seven status tables |
| FileFlows | Dashboard summarizes upcoming, recent, failed, nodes, and savings; Files separates unprocessed, processing, processed, and failed | Live/completed logs, failure reason, move to top, cancel, reprocess, node, encoder, file sizes, storage saved, timing, resolution, VMAF/evaluation data, optional failed temp retention | **Adapt:** attention strip, class-local reprioritization, reprocessing, direct logs, retained failure evidence, media-impact metrics. **Reject:** generic flow graph as the primary explanation |
| Unmanic | Completed tasks are successful/failed, ordered by completion, expandable, requeueable, and removable | Full commands run by workers and diagnostics for failures | **Adapt:** terminal attempt reports and operator retry as a new job. Do not hide command evidence behind container logs |

## Progress and loading-bar findings

The comparison supports a subject-first activity surface, but reliable progress requires a
stronger contract than copying another product's bar:

- Sonarr/Radarr pair a subject with status and explanatory attention text. Marquee adopts
  the subject/status hierarchy, while rejecting download-client progress semantics that do
  not describe analysis, remux, ML, validation, or staged publication.
- FileFlows has added humanized processing steps and can show FPS, ETA, decoder, encoder,
  and bitrate for the active operation. Marquee adapts those metrics only when a definition
  has a validated source; they are not universal columns.
- Unmanic explicitly distinguishes processing with a known percentage from indeterminate
  processing and exposes a live command log from the worker. Marquee adopts both modes and
  direct log access.
- Tdarr's job reports remain valuable evidence, but its closed inaccurate-progress issue is
  a caution: deriving completion from assumed frame rate/frame count can leave a successful
  encode below 40%. Marquee therefore uses native processed timestamps against validated
  duration and degrades to indeterminate when those units are unreliable.
- FFmpeg supplies machine-readable progress packets and cadence control; MKVToolNix supplies
  GUI-mode progress. Native tool formats should feed typed backend adapters rather than
  page-specific log parsing.

No reviewed product removes the need for Marquee-specific nested progress. Its batches can
contain shows, seasons, episodes, files, tracks, candidates, and tool stages. The correct
adaptation is a stable overall request scope plus a separate current-subject/current-step
scope—not one percentage reused at every level.

## What the Arr model gets right

Sonarr and Radarr make Activity the place for both present and past work. Their Queue/History
split is simpler than making every lifecycle state a navigation destination:

- Queue answers what is active, waiting, delayed, or needs intervention now.
- History answers what happened and why.
- Status is communicated by icon/tone plus a textual explanation.
- Filtering, sorting, pagination, selectable columns, and contextual actions scale better than
  an ever-growing dashboard.
- A subject remains the visual anchor; transport identity is secondary.

Marquee should follow that information architecture, not the download-specific model behind
it. Indexers, protocols, grabs, download clients, manual import, release blocklisting, and
unknown downloader items do not describe Marquee jobs and do not belong in Projection Room.

## What media processors add

Arr activity primarily explains acquisition/import work. Marquee also changes media files,
analyzes images, trains/runs ML components, and validates complex results. Tdarr and
FileFlows demonstrate additional requirements:

1. **No work needed is a real result.** Tdarr distinguishes success from “not required.” A
   Marquee scan, policy, detection, healing, or poster analysis that correctly makes no
   change must end as `no_change`/`not_required`, not ambiguous success or skip.
2. **Reports must follow the job.** Tdarr exposes reports from workers, staging, status, and
   search. FileFlows opens live or completed processing logs from the file. Projection Room
   needs log/report affordances on Queue and History rows as well as detail pages.
3. **Failure reason belongs in the list.** A red badge with no explanation forces needless
   drill-down. The row should name the failed stage or target, indicate whether media
   changed, and offer the likely next action.
4. **Processing impact matters.** FileFlows reports original/output size, savings, duration,
   node, encoder, resolution, and quality metrics. Marquee should show the relevant subset,
   not a universal wall of columns.
5. **Reprocessing is a product operation.** A terminal retry must create a new canonical job
   linked to the original so both outcomes remain auditable.
6. **Failure evidence can be perishable.** Bounded staged-output, validation reports, and
   tool diagnostics should be retained as artifacts when their diagnostic value outweighs
   storage cost.

## Current redesign gap analysis

| Capability | Previous plan | Decision | Required change |
|---|---|---|---|
| Human subject/action/outcome | Already covered | **Adopt** | Keep as the mandatory core of every row and detail page |
| Typed presenters and raw-data fallback | Already covered | **Adopt** | Keep built-in presenter coverage as a release gate |
| Logs, artifacts, and attempts | Already covered | **Adapt** | Add direct row affordances and visible retention/availability |
| Queue and History as the Activity shell | Partially covered | **Adopt** | Fold Running into Queue and remove Batches as a primary tab |
| Attention summary and navigation severity | Missing | **Adopt** | Add running, waiting/held, retrying, and needs-attention counts plus highest-severity badge |
| Deterministic ordering | Partially covered | **Adopt** | Specify Queue and History default orders and server-side sort keys |
| Configurable columns/density and URL filters | Missing | **Adapt** | Keep subject/action/status mandatory; persist display preferences locally and filters in URL |
| Bulk and contextual actions | Partially covered | **Adapt** | Drive cancel, pause/resume, priority, retry, logs, artifacts, and detail actions from server capabilities |
| Exact queue position | Missing | **Reject** | Offer optional approximate rank within one execution class only |
| Waiting/hold/remediation explanations | Partially covered | **Adopt** | Elevate eligibility, retry, schedule, lock, failure, atomicity, and next-action text |
| `not_required`/`no_change` | Missing | **Adopt** | Add a terminal outcome distinct from success, skip, and failure |
| Retry lineage | Partially covered | **Adopt** | Link original/replacement jobs and display the chain in History |
| Trigger and initiator provenance | Missing | **Adopt** | Record manual, schedule, webhook, policy, batch, parent, healing, or maintenance origin |
| File impact and efficiency | Partially covered | **Adapt** | Present input/output size, delta, speed, duration, encoder, and validation only when relevant |
| Failure-stage evidence retention | Partially covered | **Adapt** | Register bounded staged output and validation diagnostics with retention state |
| Infrastructure charts in Activity | Present in current UI, moved in plan | **Reject** | Keep infrastructure in lazy Operations |
| Download-client blocklist | Not planned | **Reject** | Failed jobs remain filterable History; no separate blocklist |
| Generic flow graph | Not planned | **Reject** | Use domain presenters and an optional friendly step summary |
| Typed semantic progress | Missing | **Adopt** | Add server-owned versioned overall/current scopes, stages, subject context, sequence, and freshness |
| Determinate versus indeterminate work | Partially covered | **Adopt** | Require every definition to declare honest measurement semantics; never render a fake 100% activity bar |
| Nested batch/current-subject progress | Missing | **Adopt** | Keep overall scope monotonic and reset current progress only under a new `scope_id` |
| Refresh/reconnect recovery | Partially covered | **Adopt** | Rediscover active jobs from Queue APIs and reconcile SSE with snapshots; local storage is only a hint |
| Native media-tool progress | Missing | **Adapt** | Parse FFmpeg `-progress` and `mkvmerge --gui-mode`; use indeterminate stages for opaque tools |
| Guessed percentage/ETA | Implicit risk | **Reject** | Do not use nominal frame rate, arbitrary milestones, or client-inferred JSON denominators |

## Marquee Activity decision

Projection Room is Marquee's **Activity** area. Its product navigation is:

1. **Queue** — running, cancelling, queued, waiting, held, paused, deferred, and retrying.
2. **History** — succeeded, partially succeeded, no-change/not-required, failed, cancelled,
   and superseded.
3. **Operations** — a separate, lazy infrastructure view, visually secondary to Activity.

Batch parents appear as expandable/grouped rows in Queue or History. The child list is
bounded and server-paginated. There is no Batches primary tab and no Blocklist.

### Common row contract

Every row answers the same minimum questions:

- which subject and media context;
- what action was requested;
- which feature area and trigger created it;
- current state/outcome, stage, and attention severity;
- progress, elapsed time, and running ETA/throughput when credible;
- separate monotonic overall progress and current-subject/current-step progress, or an
  explicit indeterminate state when no denominator exists;
- progress freshness/reconnect state without treating a network interruption as job failure;
- eligible/retry/hold/safety-gate reason when waiting;
- terminal effect, no-change reason, or failure/remediation summary;
- timestamps and duration;
- valid actions and availability of logs/artifacts/detail.

Queue may show an approximate `queue_rank` only inside the job's primary execution class.
PgQueuer priorities, `execute_after`, per-entrypoint concurrency, retries, and Marquee safety
locks mean a truthful global position or queued-start ETA does not exist. The UI must never
fabricate either.

## Four primary feature areas

Common data is necessary but insufficient. The presenter for each job supplies the facts
that define success for its feature area.

### AI posters

List rows emphasize the movie/show/season, requested pipeline/deploy/reset action, current
stage, candidate progress, chosen preview when available, and review/failure state.

Detail adds candidate and source counts, rejection gates, selected preview/source,
score/confidence with a human interpretation, model/profile versions, prior versus selected
and deployed poster, backup/archive, deployment/reset result, and reason manual review is
needed. “Analysis succeeded but selected nothing” is a distinct no-change outcome.

### HDR management

List rows emphasize subject/file, source-to-target HDR/Dolby Vision operation, processing
stage/progress/speed, hardware path, validation, and size impact.

Detail adds Dolby Vision profile/level, codec, bit depth, color metadata, RPU
extraction/injection/preservation, encoder and fallback, input/output size, validation and
preservation failures, backup/staged output, atomic publish result, and exact failed stage.

### Audio and subtitles

List rows summarize requested changes and counts, such as “remove 2 subtitle tracks and 1
audio track,” plus per-target failures/attention.

Detail adds requested selectors, before/after inventory, language, codec, channels/layout,
title, default/forced/HI/VI flags, embedded/external state, stable track identity,
per-target outcome/stage/reason, generated or extracted artifacts, sidecar effects, post-job
rescan, backup, and atomicity. An all-or-nothing remux failure marks every intended embedded
change `not_applied`.

### Letterbox management

List rows emphasize detection/tag/re-encode/revert action, detected crop, confidence,
current stage, hardware path, and validation.

Detail adds detection scope/mode, samples and diagnostic frames, source dimensions/aspect,
crop measurements and variability, requested versus actual operation, output dimensions,
encoder/fallback, HDR/Dolby Vision preservation, input/output size, tag/pixel validation,
backup/staged output, and publish result.

### Supporting work

Library synchronization, scans, ML/taste training, maintenance, backup, retention, healing,
and aggregate jobs still use typed presenters. They share the common contract and add their
scope, counts, versions, artifacts, validation, and operator actions. They do not fall back
to unlabelled JSON merely because they sit outside the four primary filters.

## Adopt, adapt, and reject summary

**Adopt:** Activity with Queue/History, subject-first rows, attention severity, deterministic
ordering, rich filters, configurable tables, `not_required`, direct reports, retry lineage,
failure remediation, media-impact facts, determinate/indeterminate distinction, and durable
active-job recovery.

**Adapt:** bulk actions to Marquee's safe command capabilities; move-to-top to class-scoped
priority; processor metrics to presenter-specific impact; retained temp output to bounded
artifacts; status tables to two views plus filters; native tool progress to typed overall and
current scopes shared by feature pages and Activity.

**Reject:** blocklists, download-client vocabulary, global rank/ETA promises, generic flow
graphs as the main explanation, many lifecycle tabs, and infrastructure data in the primary
Activity surface. Also reject page-local stage maps, fabricated milestones, guessed
frame-count percentages, and disappearance of active work on refresh or SSE interruption.
