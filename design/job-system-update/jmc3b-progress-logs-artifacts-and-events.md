# JMC3B — Progress, Logs, Artifacts, and Events

**Previous plan:** [JMC3A execution kernel and filesystem safety](jmc3a-execution-kernel-and-filesystem-safety.md)  
**Progress architecture:** [job progress and loading experience](job-progress-and-loading-experience.md)  
**Product contract:** [Projection Room job experience](projection-room-job-experience-redesign.md)  
**Next plan:** [JMC3C backup, ingress, and certification](jmc3c-backup-ingress-and-certification.md)

> **For the implementing agent:** Read `AGENTS.md`, `CLAUDE.md` when present,
> `design/plans/README.md`, `design/plans/04-television-backend.md` §0, the complete JMC1,
> JMC2, and JMC3A plans/timelines, and this document **in full** before changing code.
> Decisions below are final. **Verify in code** means inspect the completed JMC2/JMC3A
> implementation and every caller before editing; code and the shared timeline outrank stale
> line anchors.
>
> **Shared JMC3 timeline:**
> `design/job-system-update/jmc3-safety-and-evidence-timeline.md`. Read it, verify every
> completed hash and gate against Git and the working tree, and append to this same file after
> every phase commit. Never create a JMC3B-specific timeline.
>
> **Git authorship:** use only the repository's configured Git user. Never add yourself, a
> model, or an assistant as author, co-author, contributor, or generator. No
> `Co-Authored-By`, “Generated with,” model-name, or assistant-name attribution is allowed.

**Goal:** Make each admitted attempt durably explainable and each active job reliably
observable by implementing fenced progress, attempt logs, confined artifacts, and one
multiplexed replayable semantic-event stream.

**Ordering:** second of three JMC3 plans. JMC3A must be complete and verified. JMC3C must not
begin until this plan is complete and certified.

**Dispatch boundary:** only `system_noop` remains production-enabled. Evidence behavior is
certified with JMC3A's fixed development/test canaries. Real feature progress adapters are
implemented and fixture-tested here but attached to feature handlers only in Chunks 4–5.

## 1. Preconditions and stop gates

Before implementation:

1. Verify all JMC3A commits, exact retained failures, registry/delivery behavior, fenced
   writer, lock order, process identity, containment tiers, cancellation, filesystem roots,
   staging, readiness, connection budgets, and performed-versus-pending smokes.
2. Re-run JMC3A duplicate/stale-fence, process-kill, path-confinement, cancellation, and
   staged-publish gates against a disposable database/filesystem.
3. Verify the completed JMC2 `JobProgress`, `ProgressPolicy`, `JobPresentation`, event,
   attempt, log, artifact, API pagination, generated OpenAPI, and TypeScript contracts.
   Record any implementation drift from the earlier design before editing.
4. Inventory every current semantic-event insertion, per-job SSE/polling route, process-local
   subscription, progress bridge/helper, subprocess-pipe consumer, Python logging setup,
   physical artifact registry, and API evidence endpoint.
5. Record branch/HEAD, working-tree ownership, configured Git author, complete backend and
   frontend baselines, schema/reset fingerprints, generated-contract drift checks, DATA_DIR
   test roots, and current role connection/file-descriptor budgets.

Stop if JMC3A is incomplete, its stale fence can still write/publish, JMC2 contracts are
unavailable, a public per-job polling SSE compatibility path remains unexplained, or another
agent owns overlapping files.

## 2. Locked decisions

| ID | Decision |
|---|---|
| O1 | `job_events.id` is the one global durable semantic cursor. Transport notifications, process-local listener maps, and log cursors are not alternate job-event authorities. |
| O2 | Every semantic event is inserted through one service in the same transaction as the state change it describes. A PostgreSQL notification contains only the newest committed cursor and is only a wakeup hint. |
| O3 | One tailer per API instance reads each durable cursor range once and fans out to clients. Each SSE connection does not poll PostgreSQL. |
| O4 | `GET /api/jobs/events/stream` is the sole public live job-event stream. The global event ID supports native `Last-Event-ID` replay; obsolete per-job polling SSE is removed, not retained as compatibility. |
| O5 | Every client has a bounded queue. Overflow, retention gaps, and server restart produce an explicit snapshot-reconciliation control event; they never block the shared tailer or fabricate completion. |
| O6 | Canonical progress sequence is allocated by the server-side fenced writer. Producers may supply a bounded sample ordinal, but cannot select the durable sequence or percentage. |
| O7 | Progress writes validate the current JMC3A attempt/fence/generation and completed JMC2 definition policy. Stale progress is rejected without updating the snapshot or emitting an event. |
| O8 | Overall progress is monotonic within an attempt. Current progress may reset only when `scope_id` changes. Unknown totals and unreliable rates remain indeterminate. |
| O9 | Stage, subject, wait, warning/failure, terminal, and maximum-staleness updates bypass ordinary coalescing. High-frequency ticks coalesce by policy without exceeding the declared durable staleness. |
| O10 | Progress failure is telemetry degradation: preserve the last good snapshot, emit operational evidence when possible, and never corrupt or fail the media operation. |
| O11 | Attempt logs are UTF-8 JSONL with a stable per-line cursor. Python context logs and subprocess stdout/stderr share one sanitized capture path while continuing to tee appropriate records to normal process logs. |
| O12 | The cap is 100 MB across all stored segments for one attempt. Exactly one visible truncation record is stored, after which pipes continue draining but additional ordinary lines are discarded. |
| O13 | Redaction precedes persistence, size accounting, SSE/tail delivery, summaries, compression, and download. Raw secrets are never temporarily written to the log file. |
| O14 | Terminal logs are sealed and gzip-compressed with an atomic temporary-file replace and checksum. Open logs remain cursor-readable; recovery seals only after JMC3A proves the writer process is dead. |
| O15 | Physical artifacts live under the confined artifact root and are immutable once available. Virtual artifacts materialize only allowlisted canonical documents; neither form accepts an arbitrary path or ORM dump. |
| O16 | Default attempt-log retention is 30 days. Evidence expiration is idempotent and distinguishes expired metadata from missing/corrupt storage. |
| O17 | JMC2C's bounded evidence APIs are completed, not replaced with unbounded downloads. Every new/changed route regenerates and verifies deterministic OpenAPI and TypeScript types. |
| O18 | Infrastructure/event/log health is exposed through readiness/Operations diagnostics, not embedded into primary Activity presentation. |
| O19 | Existing failures may shrink but the retained set may not grow. Every phase runs backend, generated-contract, and affected frontend gates; never run `ruff format`. |

The SSE contract follows the browser's native event-ID reconnection semantics documented by
the [WHATWG HTML standard](https://html.spec.whatwg.org/multipage/server-sent-events.html).

## 3. Semantic event service and stream

### 3.1 Event envelope

Keep the durable row bounded and emit a versioned public frame equivalent to:

```text
JobEventFrame
  version
  cursor                         decimal job_events.id
  event_key
  job_id
  attempt_id                     optional
  canonical_version/fence        safe reconciliation tokens
  occurred_at
  compact snapshot delta         allowlisted fields only
  reconciliation
    snapshot_url
    presentation_url
```

Event detail is validated by event key and cannot contain raw ORM state, PgQueuer rows/IDs,
secrets, command arguments, stack traces, unconfined paths, or an unbounded result. Events
point clients to bounded snapshot/presentation/evidence APIs rather than duplicating those
documents.

Use a finite semantic vocabulary, including lifecycle, attention, progress, evidence-ready,
and reconciliation control events. Transport states are translated only when they change a
product state; raw PgQueuer notifications never enter this stream.

### 3.2 Transactional insert and notification

Create one event writer used by the JMC3A fenced writer, command service, progress writer,
artifact service, retention, and parent projection. It:

1. validates event type/detail;
2. inserts the append-only row in the caller's state transaction;
3. obtains the cursor;
4. calls transactional `pg_notify` with only that decimal cursor;
5. leaves commit authority with the caller.

To prevent silent writers, enforce this service through source/static tests and, if the
completed JMC2 schema makes it safer, an Alembic-owned insert trigger may provide the
notification. Do not create duplicate notifications from both paths. The timeline records
the chosen single implementation after verifying current JMC2 event creation sites.

### 3.3 API tailer and client fan-out

- One direct asyncpg listener connection per API instance listens on a fixed Marquee channel.
- A notification triggers a bounded query from the instance cursor through the announced
  cursor; periodic repair reads committed rows beyond the cursor even with no notification.
- Startup begins from the current durable high-water mark unless a connected client's replay
  requires older retained rows.
- Each client has a bounded in-memory queue and immutable authorization/filter snapshot.
- A slow queue never backpressures database reading or another client.
- Keepalive comments do not create durable event IDs.
- Graceful shutdown closes listeners/clients without synthesizing job failure.

The public literal route is registered before `/api/jobs/{id}` routes. Native
`Last-Event-ID` is authoritative; an allowlisted `after` query may support non-EventSource
tests/clients but cannot disagree with the header.

### 3.4 Replay and reset behavior

If the requested cursor is retained, replay strictly after it and then continue live without
a gap. Duplicate notifications or reconnects may resend an already observed frame; clients
can discard by cursor.

If the cursor predates retained history, is invalid, or a client queue overflows, send a
versioned `stream.reset_required` control event containing the current high-water mark and
bounded reconciliation links, then close or resume from that mark according to the fixed
client contract. Never silently jump or send every historical event. JMC3B tests both server
behavior and the generated client contract; the shared frontend store consumes it in Chunk 6.

## 4. Fenced progress writer

### 4.1 Input and sequence authority

The handler/tool adapter submits a validated observation containing attempt identity,
semantic stage/current subject, measurement values, metrics, wait/warnings, and optional
producer sample ordinal. It does not submit `percent`, the canonical sequence, user-facing
raw stage text, or an ETA unsupported by policy.

The writer:

1. resolves the current definition/progress policy;
2. validates stage, units, values, subject kind, scope IDs, and allowed metrics;
3. locks or compare-and-sets the current job at the expected JMC3A fence;
4. reconciles against the prior snapshot;
5. computes percentages and credible ETA server-side;
6. allocates `progress_sequence + 1` only for an accepted durable snapshot;
7. stores progress/current stage/current subject/freshness;
8. inserts `progress.updated` through the event service in the same transaction.

Stale producer samples, duplicate observations, wrong fence, disallowed stages, incompatible
units, non-finite/negative values, total shrinkage without an explicit policy transition, and
current-scope regression without a new `scope_id` are rejected.

### 4.2 Coalescing and failure isolation

Maintain one bounded coalescer per active attempt. Its definition policy controls minimum
cadence, meaningful delta, and maximum staleness. The latest valid tick may replace an older
unflushed tick, but these transitions force a flush:

- stage or current subject change;
- scope ID or measurement-mode change;
- wait enter/exit;
- warning/failure/attention change;
- cancellation/stopping;
- retry/failure/no-change/success terminalization;
- maximum staleness deadline.

Flush tasks cannot outlive their attempt fence. Shutdown attempts a bounded flush; worker
death may lose only coalescible ticks, never the last already durable snapshot. A database or
validation failure is recorded to normal operational logging/health without raising into
the media effect unless the handler separately fails.

### 4.3 Native adapters

Implement parsers as pure, fixture-driven components:

- **FFmpeg:** consume machine-readable `key=value` packets from `-progress`, use processed
  media time only against a separately validated reliable duration, expose speed/FPS/bytes
  only when parseable, and degrade to indeterminate when duration/timestamps are invalid.
- **mkvmerge:** consume `--gui-mode` progress records and explicit exit/error records; do not
  scrape ordinary display prose.
- **Indeterminate stages:** bounded liveness observations for ffprobe/dovi tools, external
  providers, model load/warmup, validation, copy, rescan, and other opaque work.

Parsers never own process launch, logs, lifecycle, cancellation, or publication. They feed
observations into the writer through the JMC3A execution context. Real handler command-line
changes remain Chunks 4–5.

### 4.4 Parent aggregation

Implement the generic JMC2 sealed-child policy now:

- a determinate overall denominator exists only after the fixed/dynamic child set is sealed;
- terminal child contributions distinguish success, no-change, partial failure, failure,
  cancellation, and supersession according to the registered parent policy;
- active child summaries are bounded and ordered; no unbounded child graph is loaded;
- one primary current subject plus a bounded concurrent list is projected from child
  snapshots;
- parent progress/event writes use the parent fence/version and cannot regress overall state.

Use synthetic parents/children only. Chunk 4 attaches this service to real batch creation.

## 5. Per-attempt log capture

### 5.1 JSONL record

Each stored line is a bounded object:

```text
AttemptLogLine
  version
  cursor                         monotonic within attempt
  timestamp
  level
  source                         python | stdout | stderr | system
  stage                          semantic key, optional
  message
  fields                         allowlisted bounded values
```

Job/attempt/fence/correlation context comes from the enclosing log metadata/context and may
be repeated only when useful for downloaded standalone records. The source byte stream is
decoded incrementally with explicit replacement accounting. Individual line/message/field
limits prevent one record from consuming the attempt cap.

### 5.2 Python and subprocess integration

- Use context variables and a scoped logging handler/filter so only records produced inside
  the current attempt are attached; unrelated worker logs never leak into a job.
- Tee safe records to the normal application logger without recursion.
- JMC3A's launcher drains stdout/stderr concurrently into this capture interface.
- Tool adapters may classify a line as progress, but the same raw/sanitized line is not
  duplicated unboundedly across progress events and logs.
- Sanitized command reports use tool-catalog redaction rules. Never log full environments,
  credential files, authorization headers, API keys, callback tokens, database URLs, or
  secret arguments.

Build a central redactor from configured secret values plus structural rules. Redaction must
handle exact values, bearer/header forms, URL userinfo/query secrets, split key/value
arguments, common token names, and bounded cross-chunk text without storing the unredacted
candidate.

### 5.3 Cap, segments, sealing, and recovery

- The 100 MB limit is calculated on persisted sanitized UTF-8 bytes across all attempt
  segments, including the truncation record.
- On the first over-cap record, persist one `log.truncated` system record if it fits by
  reserving space in advance, set metadata `truncated=true`, and discard later ordinary
  records while continuing to drain pipes.
- Keep active JSONL in a confined attempt key. Metadata byte/line counts and last cursor are
  updated at a bounded cadence and finalized exactly on seal.
- Seal by flush/fsync, close, gzip to a temporary confined key, checksum, fsync, atomic
  replace, then update metadata. Failure leaves a recoverable explicit state, not a falsely
  compressed record.
- Recovery seals an abandoned open segment only after JMC3A proves its owner process dead.
  A possibly live writer is never compressed/deleted underneath it.

### 5.4 Log APIs

Complete the JMC2C contracts:

- `GET /api/jobs/{id}/attempts/{attempt_id}/logs` — cursor pagination, default/hard limit,
  allowlisted level/source filters, stable next cursor, freshness/sealed/truncated metadata;
- `GET /api/jobs/{id}/attempts/{attempt_id}/logs/stream` — lazy attempt-log SSE from an
  explicit cursor, bounded queue, keepalive, terminal seal event, no database polling;
- `GET /api/jobs/{id}/attempts/{attempt_id}/logs/download` — safe final JSONL/gzip download,
  with defined behavior for active, expired, missing, or corrupt storage.

Attempt ownership is checked through the canonical job. A path/key is never accepted from
the request. Range requests are optional only if they remain bounded and valid for the
compression state; do not promise them otherwise.

## 6. Artifact service

### 6.1 Physical artifacts

Register a physical artifact through a staging call that accepts a JMC3A-classified source,
artifact kind, safe display name, content type, retention class, and bounded metadata. The
service copies/moves into an immutable confined key, calculates size/checksum while streaming,
fsyncs/atomically publishes it, then marks the database row available. Failed publication
leaves an explicit failed/pending row and safe cleanup target.

No caller may mark an arbitrary existing path available. Artifact kind controls extension,
content type, maximum size, preview/download behavior, and whether an attempt link is
required.

### 6.2 Virtual artifacts

Allow only explicit sources for canonical request, plan, result, error, and bounded semantic
event documents. At request time, reload and validate the canonical document, apply the same
sanitization/presentation boundaries as JMC2C, serialize deterministically, and stream it
without duplicating database storage. Unknown source kinds or malformed required documents
fail as integrity errors; optional missing documents return a typed unavailable state.

### 6.3 Failure evidence and downloads

Bounded command reports, validation reports, staged-output diagnostics, and safe image/frame
evidence use the same service. Failure does not authorize retaining an unlimited staged media
file. Artifact metadata exposes retention/expiry, checksum, size, attempt, safe name, and
availability—not a storage key.

Complete `GET /api/jobs/{id}/artifacts/{artifact_id}/download` and any JMC2C artifact metadata
links. Downloads bind artifact to job, open through the filesystem service, set safe
content-disposition/nosniff headers, and distinguish expired, missing, corrupt, and
unavailable states.

## 7. Retention and health

- Default attempt-log expiry is terminal time plus 30 days; active attempts do not expire.
- Artifact retention is class-driven and may exceed job list retention when required.
- Cleanup claims bounded metadata rows, removes confined files idempotently, and records
  expired status before/with deletion so retries are safe.
- A missing file for an available row and an untracked file under managed roots are
  reconciliation alerts. Cleanup never scans or deletes outside classified roots.
- Readiness verifies the event listener/tailer can initialize and required evidence roots
  are writable for worker/API roles that need them. Temporary listener/storage degradation
  after startup is exposed as stale/not-ready/Operations health according to JMC1/JMC2
  policy without killing a media effect already in progress.

Track bounded metrics for event lag, listener repair, client overflow, progress writes and
coalescing, log bytes/truncation/redaction/seal failures, artifact bytes/failures, retention,
and evidence-root capacity. Do not put these in primary Activity responses.

## 8. Implementation phases

Each phase ends with focused tests, complete retained pytest comparison,
`ruff check marquee tests`, schema/readiness checks where applicable, deterministic OpenAPI
and TypeScript generation checks, affected frontend check/lint/build, `git diff --check`, one
short lowercase commit, and a shared-timeline update.

### Phase B0 — verify JMC3A and freeze evidence contracts

- Perform prerequisites and baseline.
- Freeze JMC2 presentation/API/progress schemas and JMC3A evidence extension points.
- Inventory/remove plans for obsolete streams/helpers and establish redaction fixtures.

### Phase B1 — durable events and multiplexed SSE

- Implement the event writer/notification and one tailer per API instance.
- Add global replay/reset/slow-client behavior and remove per-job polling SSE.
- Integrate lifecycle/command events and event readiness/metrics.

### Phase B2 — attempt log capture and APIs

- Implement JSONL capture, context logging, launcher pipe sinks, redaction, cap, sealing,
  recovery, cursor reads, lazy streaming, download, and retention metadata.
- Prove high-output tools cannot deadlock after truncation.

### Phase B3 — physical and virtual artifacts

- Implement staging/registration, immutable storage, virtual documents, download, failure
  evidence, expiration, and reconciliation.
- Connect bounded artifact/log availability to JMC2 presentations/snapshots.

### Phase B4 — progress writer, adapters, and parent projection

- Implement server sequence/percentage authority, invariants, coalescing, forced flush,
  failure isolation, FFmpeg/mkvmerge/indeterminate adapters, and synthetic parent aggregation.
- Route fixed canary progress through the same service.

### Phase B5 — integrated canary and contract certification

- Exercise process, cancel, progress, log, artifact, event, restart, retention, and overflow
  paths together.
- Regenerate/verify OpenAPI and TypeScript contracts and run complete backend/frontend gates.
- Record the exact JMC3C evidence-sealing and backup integration points.

## 9. Acceptance matrix

JMC3B is incomplete until automated evidence proves:

- every semantic state change and progress snapshot uses the one event writer;
- notification is after-commit visibility only and missed/duplicate notifications repair;
- one API-instance tailer serves multiple clients without per-client database polling;
- `Last-Event-ID` replay is gap-free within retention and reset behavior is explicit outside
  retention or after slow-client overflow;
- SSE disconnect/restart never terminalizes or hides a canonical job;
- stale/wrong-fence/out-of-order progress cannot replace current state or emit a false event;
- server percentages, monotonic overall scope, current `scope_id` resets, indeterminate work,
  terminal preservation, and credible ETA rules hold;
- high-frequency observations remain inside database/event budgets and maximum staleness;
- progress-write failure preserves the media effect and last good snapshot;
- FFmpeg and mkvmerge parsers handle partial lines, malformed records, invalid duration,
  regression, exit, and cancellation without invented progress;
- synthetic sealed parents aggregate outcomes and concurrent subjects without unbounded
  child reads;
- Python/stdout/stderr lines interleave under stable cursors and remain readable live;
- secrets/sensitive arguments are redacted before disk/API/summary exposure;
- the 100 MB cap stores exactly one truncation record and never stops pipe draining;
- logs seal/compress/download after success, failure, cancellation, retry, and verified
  worker death; possibly live logs are never sealed;
- arbitrary paths, traversal, poisoned keys, wrong-job artifacts, and unsafe names/content
  dispositions are rejected;
- physical checksums and virtual documents are deterministic, bounded, sanitized, and
  correctly expired;
- metadata/file cleanup and reconciliation are idempotent;
- generated contracts contain the multiplexed stream/log/artifact APIs and drift checks pass;
- only `system_noop` remains production-enabled and the retained failure set does not grow.

## 10. Out of scope

- production migration/enablement of real feature handlers or schedules;
- feature-specific command-line integration of FFmpeg/mkvmerge adapters;
- Projection Room/shared feature-page visual progress store and cards;
- coordinated backup/restore and request-size enforcement (JMC3C);
- authentication redesign, webhooks, public reset replacement, Docker hardening, or CI
  modernization;
- raw PgQueuer rows/IDs or infrastructure metrics as product events;
- unbounded/full-process environment, stack-trace, or staged-media attachment.

## 11. Operator handoff

The shared timeline must identify final hashes, exact retained failures, event channel and
schema objects, replay/retention rules, API-instance/client budgets, log/artifact root and
retention configuration, redaction sources, cap/compression results, progress cadence and
adapter fixtures, performed restart/slow-client/download smokes, pending operator work, and
the exact JMC3C starting point.
