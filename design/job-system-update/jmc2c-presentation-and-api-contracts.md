# JMC2C — Presentation and Canonical API Contracts

**Previous plan:** [JMC2B definition registry and policies](jmc2b-definition-registry-and-policies.md)  
**Product design:** [Projection Room job experience](projection-room-job-experience-redesign.md)  
**Research:** [Projection Room activity comparison](projection-room-activity-comparison.md)

> **For the implementing agent:** Read `AGENTS.md`, `CLAUDE.md` when present,
> `design/plans/README.md`, `design/plans/04-television-backend.md` §0, JMC1 and JMC2A/B,
> the shared JMC2 timeline, and this document **in full** before changing code. Decisions
> below are final. Inspect current symbols and references before every edit; inventories and
> route locations may drift.
>
> **Shared JMC2 timeline:**
> `design/job-system-update/jmc2-canonical-product-timeline.md`. Verify JMC2A and JMC2B
> commits and gates against Git and the working tree, then append to this same timeline after
> every phase commit. Never create a plan-specific timeline.
>
> **Git authorship:** use only the repository's configured Git user. Never add yourself, a
> model, or an assistant as author, co-author, contributor, or generator. No
> `Co-Authored-By`, “Generated with,” model-name, or assistant-name attribution is allowed.

**Goal:** Complete every backend job presenter, replace the current job endpoints with bounded
canonical contracts, and generate deterministic static TypeScript API types while leaving the
Projection Room visual rebuild and live progress transport to their later chunks.

**Ordering:** final JMC2 plan. JMC2A and JMC2B must be complete and verified first.

## 1. Preconditions and stop gates

Before implementation:

1. Verify JMC2A/B final hashes, schema head, registry manifest, disabled-handler proof,
   retained failure set, subject/progress contracts, and pending operator work in the shared
   timeline.
2. Prove registry coverage is complete and only `system_noop` is enabled. Do not build an API
   around an incomplete or generic-fallback manifest.
3. Inventory every existing `/api/jobs`, `/api/media-jobs`, per-job SSE, settings/configuration,
   and frontend API call site. Record which contracts are replaced, removed, or merely typed.
4. Record branch/HEAD, working-tree ownership, configured Git author, full backend and frontend
   baselines, current OpenAPI export, and package-lock state.
5. Verify the current stable `openapi-typescript` package from its official package metadata,
   pin an exact version in the frontend lockfile, and record that version in the timeline.

Stop if JMC2B has an uncovered definition, the current OpenAPI document is not reproducible,
or another agent owns overlapping implementation files.

## 2. Locked decisions

| ID | Decision |
|---|---|
| P1 | Every built-in and parent has a dedicated backend presenter. Generic fallback is allowed only for truly unknown historical/external data, and built-in coverage tests must make that path unreachable. |
| P2 | Presenters consume validated registry documents and immutable snapshots. Optional live library data may enrich but never be required to render history. |
| P3 | `JobPresentation` and each section are versioned typed data. Presenters return no HTML, component names, arbitrary JSON blocks, raw status codes, or transport rows. |
| P4 | Plain-language action/status/remediation is server-owned. Raw stage keys, `waiting_external`, `batch_created`, and PgQueuer transport labels are not primary user text. |
| P5 | Replace the current product jobs API with canonical bounded contracts. Do not keep old job/media-job compatibility adapters in the clean-slate design. |
| P6 | Cursor pagination, server filtering/sorting, query budgets and bounded raw documents are mandatory. No unbounded relationship arrays or full-table history response. |
| P7 | PgQueuer numeric IDs, rows and raw queue schemas remain private diagnostics. |
| P8 | Commands are capability-checked and optimistic. Stale or disallowed single-job commands return typed conflicts; bulk commands report every item independently. |
| P9 | Retry creates a successor canonical job and returns both IDs. It never reopens the old terminal row. |
| P10 | Physical log/artifact storage and streaming remain Chunk 3. JMC2C exposes bounded availability metadata and links only when evidence exists. |
| P11 | Multiplexed durable SSE remains Chunk 3. Remove per-job polling SSE as a public compatibility contract; do not invent an interim replacement. |
| P12 | Export one deterministic OpenAPI artifact and generate static types with an exactly pinned `openapi-typescript`. Do not introduce `openapi-fetch` or a generated runtime SDK. |
| P13 | Keep Marquee's existing fetch runtime; type paths, parameters, bodies, success responses and errors from generated `paths`. |
| P14 | The Projection Room visual rebuild, shared progress store/card, live logs and feature-page replacement remain Chunk 6/Chunk 3 as assigned. |
| P15 | Existing failures may shrink but the retained set may not grow. Every phase runs full backend and affected frontend gates; never run `ruff format`. |

## 3. `JobPresentation` contract

Return a versioned presentation containing:

- canonical ID, type, human label, feature area and presentation family;
- durable subject summary with artwork key and most-specific useful context;
- plain-language action headline and explanation;
- trigger provenance and sanitized initiator;
- attention severity/reason and concise remediation;
- state-derived allowed actions;
- friendly phase/outcome label and tone;
- compact typed progress with overall/current scope, current subject/stage, wait reason,
  liveness and freshness;
- impact summary, including input/output size and storage delta when meaningful;
- ordered typed sections;
- warnings, target-attributed failures and suggested actions;
- log/artifact availability and bounded diagnostic links.

Presentation is deterministic for the same stored evidence and presenter version. Labels may
be localization-ready keys plus rendered default English, but the API must not force clients
to decode machine stages into user language.

### 3.1 Fixed section vocabulary

Use a discriminated union with only:

- `facts` — ordered label/value facts;
- `before_after` — typed comparisons with changed/unchanged meaning;
- `change_list` — requested and actual target outcomes;
- `track_table` — audio/subtitle inventory and per-track outcomes;
- `metric_cards` — bounded named measurements with units/interpretation;
- `warnings` — nonfatal attention items;
- `failures` — stage/target-attributed errors and media-change/atomicity state;
- `steps` — semantic stage history, never raw transport events;
- `artifacts` — safe evidence metadata and retention state;
- `children` — bounded batch summary and link, never an unbounded child list;
- `notice` — concise no-change, missing-subject or retention explanation.

Typed values cover text, number/unit, duration, bytes, timestamp, boolean, badge/status,
subject reference and safe link. Arbitrary nested JSON is confined to bounded Raw Data
endpoints, not tunneled through a section.

### 3.2 Robustness and sanitization

- Missing/deleted optional live subjects render the immutable snapshot plus a notice.
- Malformed optional historical evidence yields a warning and omits only that section; it
  must not 500 the entire presentation.
- Invalid required canonical documents remain an operational integrity error, not silently
  fabricated data.
- Never expose secrets, environment values, authorization headers, unrestricted paths,
  unsanitized command lines, stack traces or raw exception representations.
- Error language states what happened, affected target/stage, whether media changed, whether
  staged rollback/atomicity held, and the next useful action.

## 4. Presenter families and required evidence

Implement presenter keys from JMC2B and golden fixtures for every definition and parent.

### 4.1 AI posters

Show subject artwork, candidate/source counts, pipeline stage, rejection gates, selected
candidate preview, score/confidence with interpretation, model/profile versions, previous
versus selected/deployed poster, deployment/reset result and review-required reason. Separate
stable batch completion from current movie/source/scoring work.

### 4.2 HDR and Dolby Vision

Show source/target HDR and Dolby Vision state, profile/level, codec, bit depth, color metadata,
RPU handling, encoder/hardware path and fallback, input/output size, speed/credible ETA,
validation/preservation results, backup and atomic publish outcome. Attribute failures to
analysis, conversion, preservation, validation, backup or publish.

### 4.3 Audio and subtitles

Show requested selectors, before/after track inventory, language, codec, channels, title,
default/forced/hearing-impaired flags, embedded/external state, per-target outcome and failed
stage, generated/extracted artifacts, rescan result and atomicity. For all-or-nothing failure,
report every requested target as not applied rather than implying partial mutation.

### 4.4 Letterbox

Show detection scope/samples, movie/show/season/episode context, source dimensions/aspect,
detected crop, confidence/variability, requested operation, tag/re-encode/revert result,
output dimensions, encoder/fallback, before/after size, validation and diagnostic evidence.
Nested progress must distinguish overall batch from current show/season/episode/sample/file.

### 4.5 Supporting work

Provide typed presenters for Radarr/Sonarr/library sync and scans, taste/profile/head/model
training, maintenance, backups, retention, healing, cache, metrics, `system_noop`, and every
parent batch. “Supporting” does not authorize a raw JSON fallback.

## 5. Canonical HTTP contracts

All list/detail responses have explicit response models and stable version fields. Missing
jobs return 404; stale/disallowed commands return typed 409/422 responses as appropriate.

### 5.1 Activity list

`GET /api/jobs?view=queue|history`:

- requires or defaults an explicit `view` with queue/history lifecycle partitioning;
- uses opaque cursor pagination with a documented default limit and hard maximum;
- returns compact subject, action, friendly status/outcome, typed progress, attention,
  trigger, impact, allowed actions and log/artifact availability;
- supports bounded search/filter/sort by feature area, job type, subject kind, phase/outcome,
  attention, trigger, batch/parent membership, worker/execution class and time;
- Queue defaults to attention severity, running before non-running, priority, eligibility,
  enqueue time; History defaults to completion time descending;
- exposes approximate class-local rank only when meaningful, never a global queue promise;
- returns `eligible_at`, retry/hold/safety-gate wait reason instead of a fabricated ETA.

Cursor encoding must bind to the view/filter/sort contract so a cursor cannot be silently
reused against a different query. Search input, sort keys and limits are allowlisted.

### 5.2 Detail and evidence

Implement:

- `GET /api/jobs/{id}/snapshot` — compact canonical reconciliation state;
- `GET /api/jobs/{id}/presentation` — curated detail;
- separately cursor-paginated `/attempts`, `/events`, `/artifacts`, and `/children`;
- bounded `/raw/{request|plan|result|error}` with safe content-disposition/download behavior.

Raw documents are validated canonical documents, not ORM dumps. Attempts do not expose
claim/heartbeat authority. Events are semantic and bounded. Artifact/log availability may be
false until Chunk 3.

### 5.3 Commands

Provide canonical single and bulk operations for:

- cancel;
- pause/resume when the policy and current state support it;
- priority change within the definition's execution class;
- retry terminal work as a new canonical successor.

Every mutation includes an expected canonical version/fence or equivalent optimistic token.
Responses return the new canonical snapshot and command result. Retry returns original and
replacement IDs plus lineage links. Bulk requests are bounded, deduplicated, preserve input
identity and return success/failure for every requested job; one conflict must not erase
successful siblings.

## 6. Query and authorization boundaries

- Use select-in/batched loading or explicit bounded queries; never N+1 per section/row.
- Establish query-count budgets for default and maximum list pages, presentations and batch
  children.
- Do not put infrastructure metrics in primary Activity contracts. Operations endpoints are
  a later separately loaded concern.
- Route models must not accept client-owned execution policy fields.
- Authentication remains deferred, but preserve a clean initiator/authorization boundary so
  later auth does not require changing canonical job semantics.
- Remove obsolete `/api/media-jobs` and per-job SSE contracts only after all current internal
  call sites/tests are migrated in the same phase. Do not leave dead unreferenced routes.

## 7. Deterministic OpenAPI and TypeScript contracts

### 7.1 Artifact generation

- Define one repository command that creates `design/api-schema.json` from the application
  without starting background roles or requiring a live production database.
- Normalize only intentionally nondeterministic metadata; do not post-process away schema
  differences.
- Verify generation twice produces byte-identical output.
- Add a check command that fails when committed OpenAPI differs from a fresh export.

### 7.2 Static frontend types

- Verify and pin one exact stable `openapi-typescript` version in the frontend manifest and
  lockfile. Do not use a floating range.
- Generate a committed file such as `frontend/src/lib/api/generated/openapi.ts` from the
  committed artifact with a deterministic script.
- Add a drift check that regenerates to a temporary path or verifies a clean diff.
- Do not hand-edit the generated file and do not add `openapi-fetch`, another generated
  client, or runtime schema machinery solely for generation.

### 7.3 Existing fetch runtime

Keep Marquee's existing request runtime and error behavior, but type its route path, query/path
parameters, request body, success response and error payload from generated `paths`. Migrate
job and JMC2A configuration functions first, then make a contract inventory test/type-check
cover every remaining frontend API function. A handwritten response interface cannot override
a conflicting generated contract.

This phase is contract plumbing only. Do not redesign pages, components, visual styling,
loading bars or the Projection Room information architecture.

## 8. Implementation phases

Each phase ends with focused tests, full retained pytest comparison,
`ruff check marquee tests`, frontend check/lint/build where affected, deterministic generation
checks, one short lowercase commit, and a shared-timeline update.

### Phase C0 — verify JMC2B and freeze route/presenter contracts

- Verify prerequisites and record baselines.
- Inventory routes and frontend calls.
- Freeze presentation/section schemas, pagination envelope and error model.

### Phase C1 — core presentation engine and primary feature families

- Implement presenter resolution and snapshot-first helpers.
- Complete AI poster, HDR/Dolby Vision, audio/subtitle and letterbox presenters.
- Add compact/detail goldens and malformed-optional-data tests.

### Phase C2 — supporting and parent presenters

- Complete integration, ML/taste, maintenance/system and parent families.
- Prove every definition resolves without built-in fallback.
- Add retry lineage, batch grouping and no-change/partial-success fixtures.

### Phase C3 — bounded read APIs

- Implement queue/history, snapshot, presentation and paginated evidence/children routes.
- Remove superseded product job/media-job reads and per-job polling SSE contracts.
- Enforce lifecycle partitions, cursor validity, query budgets and sanitization.

### Phase C4 — canonical commands

- Implement capability-checked single and bounded bulk commands.
- Prove stale conflicts, partial bulk failure, class-scoped priority and retry successor
  lineage.

### Phase C5 — deterministic contract generation and typed frontend runtime

- Pin `openapi-typescript`, export deterministic OpenAPI and generate static types.
- Type existing fetch helpers and all frontend API call sites against real routes.
- Do the narrow migration required for removed contracts, without the visual rebuild.

### Phase C6 — JMC2 certification

- Run all presenter/API/query/security/generation gates.
- Render or inspect manual fixtures for movie, episode, series, season, file, track, poster,
  model, batch and system subjects.
- Run complete backend/frontend suites and record exact retained failures and later work.

## 9. Acceptance matrix

JMC2C is not complete until automated evidence proves:

- every definition and parent has compact/detail presentation coverage;
- all four primary feature areas and supporting work have representative goldens;
- deleted live subjects and malformed optional evidence warn rather than 500;
- raw machine stage/status labels and secrets do not appear as primary presentation data;
- queue/history partitioning and deterministic ordering cover every phase/outcome;
- cursors are stable, filter-bound and bounded; query budgets hold at maximum page sizes;
- action capabilities, stale conflicts, class priority, retry lineage and bulk partial failure
  behave as specified;
- logs/artifacts expose only safe availability until physical support exists;
- no public raw PgQueuer IDs/rows or per-job polling SSE compatibility remains;
- OpenAPI and TypeScript generation are byte-reproducible and drift checks fail correctly;
- every frontend API function type-checks against an actual generated route contract;
- frontend check/lint/build pass and retained backend failures do not grow;
- manual fixtures cover movie, series, season, episode, file, track, poster, model, batch and
  system snapshots.

## 10. Out of scope

- Projection Room/Activity visual rebuild, shared progress card/store and feature-page UX;
- multiplexed durable SSE, progress writer/tool adapters and physical logs/artifacts (Chunk 3);
- enabling or migrating non-noop handlers (Chunks 4–5);
- operations telemetry UI;
- auth, webhook implementation, public reset replacement, Docker hardening;
- GitHub Actions modernization, which follows the eventual all-green local baseline;
- old database/API compatibility.

## 11. Operator handoff

The shared timeline must identify final commits, retained failure set, exact pinned generator
version, generation commands/artifacts, route removals, manual fixtures performed or pending,
all deferred Chunk 3/6 integration points, and the final JMC2 certification result.
