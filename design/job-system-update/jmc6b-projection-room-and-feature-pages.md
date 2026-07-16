# JMC6B — Projection Room Activity and Feature-Page Integration

> **Implementer:** Read this document in full before changing code. Decisions are locked.
> Execute every internal phase continuously; phase boundaries are verification checkpoints, not
> stopping points. Stop only for a condition in
> [`design/plans/README.md`](../plans/README.md#implementer-continuity-and-stop-conditions)
> or after the complete plan, certification, and final-only history compaction succeed.

**Program:** [clean-slate PgQueuer migration](job-system-pgqueuer-migration.md)
**Product design:** [Projection Room redesign](projection-room-job-experience-redesign.md)
**Research:** [Activity comparison](projection-room-activity-comparison.md)
**Predecessor:** [JMC6A shared Activity client and progress](jmc6a-shared-activity-client-and-progress.md)
**Successor:** [JMC6C zero-green system certification and CI](jmc6c-zero-green-certification-and-ci.md)
**Shared timeline:** `design/job-system-update/jmc6-activity-and-certification-timeline.md`
**Recommended model tier:** **God**

## 1. Objective

Turn Projection Room into Marquee's polished, subject-first Activity area and replace every
initiating page's private loading/tracking state with JMC6A's shared server-backed store/card.

At exit, Queue and History answer what is happening, on which movie/show/season/episode/file or
other subject, what was requested, what changed, what failed, and what the user can do next.
Complete logs, artifacts, raw evidence, and execution details remain one click away without
dominating the default product presentation.

## 2. Preconditions and stop gates

Before edits:

1. Verify `jmc6a-complete`, its tree/sole-parent/recovery bundle, shared timeline, generated
   contracts, frontend test harness, store/component certification, and exact remaining consumer
   inventory.
2. Record the plan base, clean tree, configured author, backend/frontend baselines, OpenAPI path
   count, browser versions, and every current Projection Room/feature-page route/component/API
   consumer.
3. Verify route code and generated OpenAPI for list/snapshot/presentation/actions/attempts/events/
   children/logs/artifacts/raw/SSE. Freeze any narrow Operations API gap before page work.
4. Use only synthetic fixture data and owned disposable PostgreSQL/data roots. Browser tests may
   not invoke real destructive jobs against operator media.

Stop if a required presentation/action/evidence fact is absent from bounded canonical APIs and
cannot be added without changing job semantics, if UI logic would need to inspect arbitrary
handler JSON, or if the store/card must be forked for one feature family.

## 3. Locked decisions

| ID | Decision |
|---|---|
| B01 | `/projection-room` is the one application Activity destination. It exposes Queue and History as primary views and Operations as a secondary lazy view. Running and waiting work live in Queue; batches are grouped rows, not tabs. |
| B02 | Remove the separate stub `/activity` navigation/page and the unused legacy `/api/activity` aggregate plus its obsolete tests. JMC5 canonical history supersedes its poster/job/pipeline merge; any still-useful subject fact belongs in canonical events/presentation, never a second global Activity product. |
| B03 | The sidebar label becomes `Activity` with Projection Room identity/route. Its badge communicates highest active severity plus an optional affected count, not a raw queue length. |
| B04 | Queue default order is attention severity, running before non-running, priority, eligibility, enqueue time, canonical ID. History is terminal time descending then canonical ID. Server order is authoritative and stable across cursors. |
| B05 | Search/filter/sort are URL-backed. Column/density preferences are local and versioned. Subject, action, and status cannot be hidden. No local preference controls job discovery or lifecycle truth. |
| B06 | Rows are subject-first and use backend `JobPresentation`/compact row data. Raw type/status/stage/PgQueuer IDs/resource JSON are never primary labels. |
| B07 | Batch parents expand to a bounded summary and separately cursor-paginated children, failed/attention children first. Expansion never fetches a complete graph. |
| B08 | Row/bulk controls render only server-returned capabilities. Bulk responses show one accepted/rejected/conflicted result per job; optimistic UI never claims success before the canonical response/snapshot. |
| B09 | Approximate rank is class-local, labelled approximate, and absent when meaningless. Queued jobs show known eligibility/retry/hold/safety reason rather than fabricated start ETA. |
| B10 | Job detail tabs are Overview, Timeline, Logs, Artifacts, Raw Data, and collapsed Execution. Each resource loads only when visible, is abortable/cursor-bounded, and stops streaming/polling when hidden. |
| B11 | Presenters' typed section vocabulary selects reusable components. Svelte does not branch on job type to inspect request/result dictionaries; family enhancements consume typed presentation sections. |
| B12 | Operations is lazy and infrastructure-only. Add or consolidate bounded snapshot/history endpoints if current system endpoints require client fan-out. It reports workers/capabilities, PgQueuer transport, entrypoint classes, safety waits, PostgreSQL/event/log/artifact/storage/scheduler/listener/node health without raw tables or secrets. |
| B13 | Every initiating page discovers active jobs through the shared store using feature area, subject hierarchy, root/correlation, and applicable types. It renders `JobProgressCard`; local storage is optional acceleration only. |
| B14 | Remove `trackJob`, `RunProgress`, `RunningJobCard`, page-owned EventSource/poll intervals/stage maps, legacy media-job client shapes, and job-ID storage after the last consumer migrates. Static tests prevent their return. |
| B15 | Navigation/subject/source-version changes abort old requests and reset drafts. Do not suppress `state_referenced_locally` to preserve stale captures. JMC6B exits with zero `svelte-check` warnings and no broad file-level lint disables. |
| B16 | Queue/History/detail use responsive table-card layouts, keyboard-accessible controls, restrained live announcements, virtualized long lists/logs/timelines, and text/icon meaning independent of color. |
| B17 | No-change/not-required is distinct from succeeded/skipped. Failures show affected target/stage, whether media changed, atomicity/rollback, retained evidence, and suggested next action. |
| B18 | Retry creates and displays a new canonical successor with original/replacement lineage. It never mutates or requeues the terminal canonical job in place. |
| B19 | Backend additions are limited to already-designed bounded presentation/Operations/filter/action data. No frontend-derived product semantics or compatibility API is added. |
| B20 | Internal phases run continuously; final compaction follows complete browser, accessibility, backend, generated-contract, and frontend certification only. |

## 4. Activity Queue and History

Build reusable controls and row/card shells for:

- attention strip counts: running, waiting/held, retrying, needs attention;
- text search plus feature area, job type, subject kind, lifecycle/outcome, attention, trigger,
  batch, worker/class, and time filters;
- allowlisted server sorting and cursor pagination;
- configurable columns/density and responsive compact cards;
- selection plus cancel, pause/resume, priority, retry, log, artifact, and detail actions;
- trigger/initiator, action/status, subject artwork/hierarchy, compact progress, wait/remediation,
  impact, evidence availability, time, lineage, and batch summary.

Empty/loading/error/stale states must be explanatory and preserve last-good data during transient
failure. Browser back/forward restores URL state without reusing the prior subject or stale page
data. Search/filter changes abort prior queries.

## 5. Job detail and evidence

### Overview

Render status hero, subject, requested action, actual result, warnings/failures/remediation,
impact, progress, and typed sections. Required primitives include facts, before/after, changes,
track table/outcomes, metrics, steps, notices, warnings/failures, children, and artifacts.

### Timeline, logs, artifacts, and raw data

- virtualize and cursor-page semantic events; merge live events by durable cursor;
- select the current/latest or explicitly chosen attempt for live/final logs;
- filter logs by level/source, preserve cursors, show truncation/redaction/retention, stream only
  while visible, and provide bounded download;
- display physical/virtual artifacts through availability/retention metadata and safe download;
- render raw request/plan/result/error/event documents in a collapsed formatted tree and provide
  canonical download; never inject HTML or show an unbounded `<pre>`;
- show missing/expired evidence honestly rather than treating it as job failure.

### Execution

Keep attempt number/outcome, worker/node/build, fence, process/exit/signal, timing, sanitized
command summary, containment, safety waits, and transport diagnostics collapsed. Never expose raw
credentials, environment, filesystem paths, PgQueuer rows, or numeric ticket IDs as product data.

## 6. Feature-family presentation

Every compact row and detail fixture must cover these facts when relevant:

- **AI posters:** subject artwork, candidate/source counts, gates/rejections, selected preview,
  interpreted score/confidence, model/profile versions, previous versus selected/deployed poster,
  deployment/reset/backup result, and review reason;
- **HDR/Dolby Vision:** source/target HDR state, profile/level, codec/bit depth/color metadata, RPU,
  encoder/hardware/fallback, source/output size, speed, validation/preservation, backup, candidate,
  and publish/restore result;
- **Audio/subtitles:** requested selectors/tracks, before/after inventory, language/codec/channels/
  title/default/forced/HI/external state, per-target stage/outcome, generated/extracted artifacts,
  rescan, validation, and atomicity;
- **Letterbox:** detection scope/samples, hierarchy, aspect/dimensions, crop/confidence/variability,
  requested operation, tag/re-encode/revert, output dimensions, encoder/fallback, size delta,
  validation, and diagnostic frames/reports;
- **Supporting work:** synchronization, scans, ML/taste, maintenance, backups, healing, schedules,
  no-op, and aggregate parents use typed sections rather than a generic JSON fallback.

## 7. Initiating-page migration

Migrate every action surface, not only Projection Room:

- poster/pipeline movie, television, onboarding, feedback, taste, rescan, deploy/reset/restore;
- HDR/Dolby Vision movie and television analysis/conversion/publication;
- audio/subtitle scan, policy, generation, extract/embed/remove/reorder/metadata/restore;
- letterbox movie/TV detection, apply/revert, re-encode, publication/restore/discard;
- library sync, backup, and applicable maintenance controls.

Each command binds the returned canonical ID immediately, then the shared store discovers and
reconciles it. Reload with empty storage must restore matching active cards. Parent cards preserve
stable overall progress while current movie/show/season/episode/file/stage changes. A stream or
snapshot failure keeps the card visible with stale/reconnecting state and direct Activity/log links.

Remove obsolete client functions/components only after source searches prove no consumer. Retain
domain-specific form/plan/review UI; only execution tracking and canonical result presentation are
centralized.

## 8. Implementation phases

### Phase B0 — verify JMC6A and freeze page/API inventory

Verify compaction/recovery and all gates; record route/component/client/warning inventories,
generated APIs, fixture subjects, browser/a11y baseline, and Operations data gaps. Add visual/
contract snapshots before shared-page edits.

### Phase B1 — Activity shell, Queue, History, and navigation

Build canonical navigation, attention badge/strip, URL filters/search/sort, column/density
preferences, stable cursor lists, responsive rows/cards, batch expansion, and loading/stale/empty
states. Remove the duplicate Activity page/navigation after its source audit.

### Phase B2 — actions, batch drill-down, and lineage

Implement server-capability row/bulk commands, partial-result feedback, stale conflict handling,
class-scoped priority, retry successor lineage, direct log/artifact actions, and bounded child
views. Certify lifecycle partition/order and actions beyond the first page.

### Phase B3 — job detail and diagnostic surfaces

Build Overview typed sections, Timeline, Logs, Artifacts, Raw Data, and Execution. Add virtualized
cursor loading/stream teardown/download, attempt selection, retention/truncation/redaction states,
deleted-subject resilience, and all presenter-family detail goldens.

### Phase B4 — lazy Operations

Consolidate/add bounded Operations snapshot/history contracts as needed, implement separately
loaded panels, stop hidden work, and certify query/poll/event/storage/connection budgets. No
infrastructure data returns to primary Queue/History.

### Phase B5 — posters and HDR initiating pages

Migrate poster/pipeline/taste/onboarding and HDR/Dolby Vision pages to the shared store/card,
remove their trackers/stage maps/job-ID authority, fix navigation/draft state, and certify single/
batch refresh/reconnect/subject transitions.

### Phase B6 — audio/subtitle, letterbox, and supporting initiating pages

Migrate remaining pages, including known letterbox TV/reset and Dolby Vision batch progress
regressions. Delete all legacy job/media-job tracker components/client adapters after the final
consumer and add static absence proofs.

### Phase B7 — accessibility, zero-warning UI certification, and compaction

Run complete browser/component/E2E/a11y/mobile/virtualization/performance fixtures, eliminate all
Svelte warnings and broad suppressions, run backend/full retained comparison, Ruff, schema,
OpenAPI/type drift, frontend unit/check/lint/build/E2E, and `git diff --check`, then perform section
10 only after every gate succeeds.

After each phase, commit, update the shared timeline, and continue immediately. Do not stop at an
internal phase boundary.

## 9. Acceptance matrix

- every Queue/History lifecycle exactly once; stable ties/cursors and filters beyond 100 rows;
- URL round-trip/back-forward, saved columns/density, mandatory columns, narrow/mobile layout;
- attention strip/sidebar severity reconciliation and non-color meaning;
- batch expansion pagination, failed-first attention, zero/large/concurrent children;
- server-authorized actions, stale conflicts, bulk partial results, class priority, retry lineage;
- direct correct-attempt logs/artifacts from active and terminal rows;
- every presentation family row/detail golden, all subject kinds, deleted/missing subject data;
- no-change, partial, failure/cancellation/unsafe/superseded and actionable remediation;
- lazy bounded Timeline/Logs/Artifacts/Raw/Execution, long data virtualization, safe text rendering;
- Operations hidden-panel abort, bounded history, node/worker/transport/database/event/storage data;
- first load/refresh/navigation with storage empty, dropped SSE, late events, API restart,
  PostgreSQL restart simulation, hidden tab, and transient failures;
- letterbox TV 44-show overall/current episode behavior, poster batch scope changes, Dolby Vision
  movement beyond `batch created`, native FFmpeg/mkvmerge progress, and indeterminate fallback;
- every initiating action finds the same canonical card inline and in Activity;
- keyboard/focus/live-region/screen-reader semantics and automated axe checks plus recorded manual
  keyboard review;
- zero `svelte-check` errors **and zero warnings**, no broad file-level lint disable introduced,
  frontend unit/E2E/lint/build green, generated contracts clean;
- no new backend failure, skip, or `xfail`; the JMC5C retained failure set may only decrease until
  JMC6C performs final triage.

## 10. Mandatory final-only history compaction

Perform only after B7 succeeds:

1. Verify a clean, linear, configured-author, JMC6B-only, unpushed range after exact
   `jmc6a-complete`; stop for merges, unrelated commits, uncertain ownership, or pushed phase
   history.
2. Commit the final pre-squash timeline entry with all phase hashes, verification, route/consumer
   removal manifest, deviations, manual checks, pre-squash tip, and intended `jmc6b-complete` tag.
3. Create timestamped recovery branch/tag and a verified repository-external bundle.
4. Record the certified tree; through RTK soft-reset to the base and create one configured-author
   commit: `jmc6b: rebuild projection room activity`.
5. Prove tree identity, sole parent, clean tree, recovery refs/bundle, then create annotated local
   tag `jmc6b-complete`.
6. Do not edit the timeline after compaction, push, force-push, delete recovery material, or start
   JMC6C.

No agent/model attribution is permitted.

## 11. Out of scope

- final backend legacy-failure triage and complete production-like certification (JMC6C);
- GitHub Actions modernization (JMC6C, strictly after local green);
- authentication, public reset replacement, Docker hardening, and webhooks;
- activating uncertified optional capabilities, remote push, or production deployment.

## 12. Handoff

Report compact base/tag/hash/tree/recovery material; final route/component/client manifest;
OpenAPI/type/browser/test versions; zero-warning proof; page/subject/presenter/action/evidence
coverage; accessibility/manual browser checks; performance/query budgets; retained backend
failures and capability exceptions; and exact JMC6C starting condition.
