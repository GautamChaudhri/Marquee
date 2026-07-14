# Marquee Job Progress and Loading Experience

**Decided:** 2026-07-12
**Status:** Target semantic-progress architecture
**Runtime context:** [direct PgQueuer adoption](job-system-pgqueuer-direct-adoption.md)
**Product surface:** [Projection Room redesign](projection-room-job-experience-redesign.md)
**Delivery sequence:** [clean-slate migration program](job-system-pgqueuer-migration.md)
**Product research:** [Activity comparison](projection-room-activity-comparison.md)
**Typed contract implementation:** [JMC2B definitions and policies](jmc2b-definition-registry-and-policies.md)
**Presentation/API implementation:** [JMC2C presentation and API contracts](jmc2c-presentation-and-api-contracts.md)
**Durable writer and delivery:** [JMC3B progress, logs, artifacts, and events](jmc3b-progress-logs-artifacts-and-events.md)
**First real-handler bindings:** [JMC4B library, scans, and media analysis](jmc4b-library-scans-and-media-analysis.md)
and [JMC4C poster, ML, and certification](jmc4c-poster-ml-and-certification.md)

## Decision

Marquee owns one durable, typed semantic-progress model for every job. Projection Room and
the feature page that initiated an operation render the same server-backed snapshot through
one shared client store and component family. PgQueuer owns delivery and retry timing; it
does not define what media work is happening, which subject is being processed, or how far
that work has progressed.

The product must answer four questions while work is active:

1. What high-level operation is happening?
2. Which movie, show, season, episode, file, track, poster, or model is affected now?
3. How far through the complete request is Marquee?
4. What current step is running, and is that step measurably progressing or simply alive?

A truthful indeterminate state is better than a fabricated percentage or ETA. Internal
codes such as `waiting_external`, `batch_created`, and handler stage keys are never primary
user-facing labels.

## Current-system findings

The present loading bars are not all fed by one reliable job-manager contract:

- `Job.progress` is untyped JSON. Generic helpers, poster pipeline events, letterbox and
  Dolby Vision handlers, child aggregation, and page-local state produce different shapes.
- Frontend code infers meaning from optional keys such as `done`, `total`, `movie_index`,
  `movie_total`, and `movies_done`. It also owns a partial stage-label map, so unknown
  backend codes leak into the UI or are mechanically humanized without domain context.
- The shared progress component uses one percentage for concepts that have different
  scopes. A batch's overall completion and the current child's stage can therefore reset or
  appear to regress.
- Batch parents durably update child completion counts mainly when a child terminates.
  Child-start events and current-subject information are not consistently projected into
  the parent snapshot.
- Letterbox TV parents count shows while a child may process many episodes. The active
  subject frequently lacks season and episode context. The poster batch bridge similarly
  combines pipeline-stage counts with movie position.
- Dolby Vision analysis children emit useful events, but the parent snapshot does not
  consistently contain the percentage shape expected by the page, producing a stale
  `batch created` presentation.
- Active-job recovery is page-specific. Some routes query active jobs, some combine a
  server lookup with local storage, and others retain the job ID only in component memory.
- Several pages treat an EventSource network error as job failure and remove their progress
  state even though native EventSource reconnects and snapshot polling may still continue.
- The current tracker can overlap snapshot requests because its fixed interval has no
  in-flight guard. Poll failures are hidden rather than represented as stale connectivity.
- Current per-job SSE tails PostgreSQL independently for each client/job. The target design
  already replaces this with one durable multiplexed stream and bounded reconciliation.

The refresh problem is therefore mostly a discovery and reconciliation failure, not an
absence of durable backend jobs. The new architecture fixes it at both boundaries: the
backend persists a complete semantic snapshot, and every page discovers/reconciles active
jobs through the same store.

## Progress ownership

| Concern | Owner | Rule |
|---|---|---|
| Delivery, retry delay, heartbeat, redelivery | PgQueuer | Transport state is diagnostic input, not user-facing progress |
| Semantic stage and subject | Marquee definition/handler | Use stable keys and enqueue-time subject snapshots |
| Measurement and percentage | Marquee progress service | Validate units and compute percentages on the server |
| Batch aggregation | Marquee parent projection | Project children into separate overall and current scopes |
| Native tool parsing | Marquee tool adapters | Parse machine-readable output; never scrape display prose when a native format exists |
| Persistence and events | Marquee progress writer | Fence, sequence, coalesce, persist, then broadcast |
| Human wording | Marquee presenter | Clients do not expose raw stage/transport codes |
| Rendering and reconnect state | Shared frontend store/components | Feature pages and Activity consume the same contract |

## Typed `JobProgress` contract

`JobProgress` is a bounded, versioned snapshot stored on the canonical job. Large histories
remain semantic events or attempt logs rather than accumulating in this document.

```text
JobProgress
  version
  sequence
  job_id
  attempt_id
  attempt_number
  fence_token
  updated_at
  headline
  stage
    key
    label
    detail
  current_subject
  overall
    scope_id
    mode                 determinate | indeterminate | none
    unit
    completed
    total
    percent
    label
  current
    scope_id
    mode                 determinate | indeterminate | none
    unit
    completed
    total
    percent
    label
  metrics
    elapsed_seconds
    eta_seconds
    speed
    fps
    bytes_processed
    bytes_total
    throughput
    encoder
    decoder
  wait
    kind
    label
    eligible_at
  concurrent_subjects[]
  warning_count
  failure_count
```

`current_subject` uses the same durable presentation vocabulary as a job subject. It may
contain movie/show/season/episode titles and IDs, episode code/title, file name, track
identity, poster preview, artwork, or a non-media target. Batch snapshots include the most
specific useful hierarchy, not only the leaf ID.

`mode` describes measurability:

- `determinate` requires a stable numerator, denominator, and unit;
- `indeterminate` proves named work and liveness but carries no percentage;
- `none` is reserved for immediate operations that should show a short busy/accepted state
  rather than a progress bar.

The server omits unsupported metrics. Zero, unknown, and unavailable are not interchangeable.
An ETA is present only when the definition declares it supportable and the current sample is
credible.

### Progress invariants

1. The server computes and clamps percentages after validating finite, non-negative values
   and compatible units. The frontend never calculates a job percentage from handler JSON.
2. `sequence` strictly increases for accepted updates. The current attempt and fence must
   match; duplicate, stale, and out-of-order updates cannot replace the snapshot.
3. `overall.percent` is monotonic within one admitted attempt. Changing stage or current
   subject cannot reset it.
4. `current.percent` may reset only when `current.scope_id` changes. The UI renders this as
   a separate current-step bar, never as overall regression.
5. A changed denominator must be explicitly reconciled. If discovered scope grows, the
   definition either uses sealed totals before determinate display or remains indeterminate
   until sealed; it does not silently move the bar backward.
6. Successful and no-change/not-required terminal jobs end with a coherent completed
   snapshot. Failed and cancelled jobs retain the last measured values and change outcome
   tone; they do not jump to 100%.
7. Stage boundaries, current-subject changes, warnings/failures, wait transitions, and
   terminal updates are always persisted. High-frequency tool ticks are coalesced by time
   and meaningful delta with a maximum durable-snapshot staleness.
8. Progress publication is best-effort with respect to the media operation: a telemetry
   write failure cannot corrupt or fail media work. It is logged/alerted and the last good
   snapshot remains visible.
9. A redelivered attempt receives a new fenced progress scope and sequence origin. The
   canonical timeline preserves the prior attempt; stale writers cannot progress or finish
   the new attempt.
10. Waiting for an execution class, retry time, schedule, safety gate, or cancellation is a
    named product state. It is not simulated as a slowly advancing percentage.

## `JobDefinition` progress policy

Every built-in definition declares `progress_policy` alongside its presenter:

```text
ProgressPolicy
  strategy              determinate | indeterminate | hybrid | none
  overall_unit
  denominator_source
  current_unit
  aggregation_strategy
  stage_vocabulary[]
  tool_adapter
  persistence_cadence
  meaningful_delta
  max_snapshot_staleness
  eta_capability
```

Parent-only definitions declare how sealed children aggregate, including the terminal
weights of succeeded, no-change, failed, and cancelled children. Long-running definitions
may not use `none`. CI/startup coverage fails when a built-in handler, parent, or operation
lacks a progress policy, presenter stage vocabulary, or subject builder.

## Honest trackability matrix

| Family/work | Overall measurement | Current measurement | User-facing behavior |
|---|---|---|---|
| AI poster library/batch | Sealed subjects or candidates completed | Current download, feature, gate, scoring batch, or subject | Stable movie/show total plus current subject and pipeline step; never substitute stage percent for batch percent |
| Poster selection/deploy/reset | Candidate/gate counts where known | Current selection, backup, deploy, rescan, or validation stage | Preview selected candidate and use indeterminate activity for opaque model/provider calls |
| HDR/Dolby Vision analysis | Sealed movies/episodes completed | Opaque `dovi_tool`/probe stage is indeterminate unless it exposes a reliable total | Keep outer batch determinate and name the exact movie/show/season/episode being analyzed |
| HDR/Dolby Vision conversion | Sealed files completed | FFmpeg processed media time against validated duration | Show native percent, speed/FPS and credible ETA; show extraction/injection/validation as separate current scopes |
| Audio/subtitle inventory and scans | Files or tracks completed | Current file/stream probe; count tracks as discovered only when total is known | Show full media hierarchy and scan phase; individual probes may be indeterminate |
| Audio/subtitle generation | Sealed targets completed | Provider-reported units when available, otherwise named request/wait/download stage | Never invent provider percentage; show language, source track, and current target |
| MKV audio/subtitle remux | Sealed files completed | `mkvmerge --gui-mode` native percentage | Follow with explicit rescan and validation scopes; show requested track outcomes |
| FFmpeg media transform/reorder | Sealed files completed | FFmpeg processed time against validated duration | Degrade to indeterminate when duration/timestamps are unreliable |
| Sidecar extract/copy/embed | Files/targets completed | Bytes when a reliable total is available, otherwise named atomic step | Short operations use step activity; do not animate a fake percentage |
| Letterbox detection | Sealed show/episode or movie scope | Samples completed for the current episode/movie; opaque probe/detection stages indeterminate | Show series, season, episode code/title, sample count, crop/confidence when available |
| Letterbox tag/revert | Sealed targets completed | Named write/rescan/validation step | Use a short busy state for the metadata write, then show validation outcome |
| Letterbox re-encode | Sealed files completed | FFmpeg processed time against validated duration | Preserve overall TV scope while current encode resets only under a new episode `scope_id` |
| Radarr/Sonarr synchronization | Services/pages/items when totals exist | Current service/page/entity | Otherwise show phase and count-so-far without a percentage |
| ML/taste training | Datasets/models completed | Examples, batches, epochs, candidates, or calibration steps when instrumented | Model load/warmup remains indeterminate; display device/model/profile context |
| Maintenance/retention/backup | Records, files, or bytes when pre-counted | Current category/file or named phase | If discovering while processing, remain indeterminate until the scope is sealed |
| Instant control/no-op | None | None | Show accepted/busy state and terminal result, not a loading bar |

FFmpeg provides program-friendly `key=value` updates via `-progress`, terminates each packet
with `progress=continue|end`, and allows cadence control with `-stats_period`.
[FFmpeg documentation](https://www.ffmpeg.org/ffmpeg.html)

For MKV remuxing, run `mkvmerge --gui-mode` and parse its progress records rather than
trying to infer work from log lines.
[MKVToolNix maintainer guidance](https://help.mkvtoolnix.download/t/mkvmerge-events/656)

Do not derive completion from guessed frame counts or nominal frame rates. A documented
Tdarr failure mode completed encodes while its UI was below 40% because input/output frame
assumptions diverged. The issue is useful negative evidence even though it is now closed.
[Tdarr progress/ETA issue](https://github.com/HaveAGitGat/tdarr/issues/1236)

## Shared progress experience

One `JobProgressStore` and one `JobProgressCard` component family serve Projection Room and
every initiating feature page. Domain presenters supply content; pages do not create their
own trackers or stage dictionaries.

The expanded card contains:

- artwork and a plain-language operation headline;
- the complete applicable subject hierarchy;
- one stable overall bar when determinate;
- a separate current-work row with a determinate bar or indeterminate activity indicator;
- elapsed time and only credible ETA/throughput/tool metrics;
- friendly wait, retry, cancellation, warning, or reconnect information;
- Cancel, View in Activity, Logs, and relevant artifact/detail actions allowed by the
  server.

For concurrent parents, show the primary current subject plus “N more running” and an
expandable bounded list. Do not rapidly rotate one label without exposing concurrency.

Example:

```text
Scanning TV letterbox bars                         12 of 44 shows
The Expanse · Season 3 · S03E07 — analyzing sample 4 of 12
[overall: 27%]                                    [current: 33%]
```

An indeterminate card uses a restrained activity indicator and elapsed time, not a pulsing
100%-wide bar. Unmanic explicitly distinguishes known percentage from indeterminate
processing and exposes the live command log from the same worker surface; Marquee adopts
that clarity while keeping logs visually secondary.
[Unmanic worker progress and logs](https://docs.unmanic.app/docs/dashboard/workers/)

FileFlows demonstrates the value of humanized step names and relevant FPS, ETA, decoder,
encoder, and bitrate data. Marquee adapts those metrics only when the job's definition can
measure them correctly.
[FileFlows version history](https://fileflows.com/docs/versions)

## Refresh, navigation, and connection reliability

The canonical database snapshot is authoritative. Browser memory and local storage are not.

1. Every command response returns the canonical `job_id`, compact snapshot URL, Activity
   URL, and detail URL. The initiating page binds immediately.
2. On load/navigation, feature pages call the bounded Queue API with feature-area, subject,
   root/correlation, and applicable job-type filters. This rediscovers matching active jobs
   even if browser storage is empty.
3. The shared store merges the initial list/snapshot, multiplexed durable SSE deltas, and
   low-frequency snapshot repair using event cursors and progress sequences.
4. Local storage may cache canonical IDs as an optimization. A missing, stale, or cleared
   cache never hides server-visible work.
5. Native EventSource errors set connection state to `reconnecting`; they do not call a job
   failure handler. The last good card remains visible with freshness and reconnect status.
6. Snapshot polling uses one in-flight request per job/list scope, cancellation for
   superseded navigation, exponential bounded backoff, and hidden-tab cadence reduction.
7. A successful snapshot clears stale connectivity. A terminal authoritative snapshot,
   not an SSE disconnect, removes a job from Queue and reconciles it into History.
8. If progress is older than its definition's liveness threshold, the UI says “No recent
   progress update” and links to logs/Operations. It does not assume failure or disappear.

The target API carries typed progress in:

- `GET /api/jobs?view=queue` compact rows, with filters used for active discovery;
- `GET /api/jobs/{id}/snapshot` authoritative reconciliation;
- `GET /api/jobs/{id}/presentation` full subject/action/status presentation;
- multiplexed durable job-event SSE frames using `progress.updated` and semantic lifecycle
  events with `Last-Event-ID` replay.

## Acceptance criteria

### Contract and backend

- every long-running definition declares an honest strategy and all built-ins have stage,
  subject, and presenter coverage;
- invalid units, non-finite values, stale sequences, and wrong fences are rejected;
- overall progress never regresses and current progress resets only with a new `scope_id`;
- high-frequency FFmpeg/mkvmerge updates remain bounded while durable snapshots meet their
  maximum-staleness target;
- progress-write failure leaves media work safe and produces operational evidence;
- redelivery cannot accept progress from the prior fenced attempt;
- parent projections expose sealed overall scope, current subjects, and terminal child
  counts without loading an unbounded child graph.

### UI and recovery

- the same job snapshot renders consistently inline and in Activity;
- refresh with empty local storage rediscovers and restores every active card;
- dropped SSE, API restart, PostgreSQL restart, hidden-tab throttling, duplicate/late events,
  and overlapping reconciliation do not hide, fail, or regress a job;
- raw status/stage codes never appear as primary labels;
- indeterminate work uses no percentage or ETA;
- accessibility tests announce meaningful stage/subject changes without announcing every
  high-frequency tick;
- narrow/mobile cards preserve subject, overall/current distinction, and warning/actions.

### Regression fixtures

- TV letterbox scanning retains one monotonic 44-show overall scope while showing the
  current series, season, episode, and sample scope;
- poster processing retains movie/batch completion while pipeline stages and subjects
  change;
- Dolby Vision batch analysis moves beyond `batch created` and names its current subject;
- FFmpeg conversion uses native processed time and degrades safely when duration is invalid;
- MKV remux uses native GUI progress followed by rescan/validation;
- provider/probe/model-load work remains visibly alive without fabricated completion;
- cancellation, failure, retry, no-change, and success preserve coherent final progress and
  attempt lineage.

## Completion criteria

This work is complete when every active operation is rediscoverable from the server, every
long-running definition declares its measurement semantics, batch overall/current progress
cannot be confused, feature pages and Projection Room share one renderer/store, and no
percentage or ETA is shown without a validated source.
