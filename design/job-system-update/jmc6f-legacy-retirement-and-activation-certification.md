# JMC6F — Legacy Retirement and Activation Certification

> **Implementer:** Read this document in full before changing code. Decisions are locked.
> Execute every internal phase continuously; phase boundaries are verification checkpoints, not
> stopping points. Stop only for a condition in
> [`design/plans/README.md`](../plans/README.md#implementer-continuity-and-stop-conditions)
> or after the complete plan, certification, and final-only history compaction succeed.

**Predecessor:** [JMC6E canonical seam and refresh closure](jmc6e-canonical-seam-and-refresh-closure.md)  
**Historical certification:** [JMC6C activation audit](jmc6c-activation-audit.md)  
**Runtime retirement predecessor:** [JMC5C](jmc5c-letterbox-hdr-and-runtime-retirement.md)  
**Shared timeline:** `design/job-system-update/jmc6-post-certification-hardening-timeline.md`  
**Recommended model tier:** **God**

## 1. Objective

Remove the duplicate/deprecated runtimes and compatibility residue exposed by the post-JMC6 audit,
then repeat activation certification against the corrected system. JMC6F is complete only when
production source, tests, generated contracts, and documentation all describe one canonical
PgQueuer execution architecture and the worker-recreation/schedule/refresh defects are proven fixed.

This is a post-certification corrective suite, not a seventh construction chunk. Completion still
does not authorize a push or activation; the owner performs one final review, pushes, requires
hosted CI, and separately approves deployment capabilities.

## 2. Preconditions and stop gates

Before edits:

1. Verify annotated `jmc6e-complete`, compact ancestry/tree/recovery bundle, shared timeline, exact
   definition/route/page manifest, forward migration state, zero-green suites, and all JMC6D/E
   failure regressions.
2. Build a symbol/reference/import inventory for deprecated runtime code, process-local state,
   compatibility DTOs, old tests/fixtures, stale design/API docs, startup migrations, and duplicated
   algorithms before deleting anything.
3. Identify reusable pure algorithms currently trapped inside old managers and move them behind
   neutral typed modules before deleting their lifecycle wrappers.
4. Use only owned disposable PostgreSQL, `DATA_DIR`, process, and generated-media fixtures.

Stop if a supposedly deprecated symbol still owns a live product path, if deleting it would reduce
coverage of a required media invariant without a canonical replacement, if certified tree history
is not linear/owned, or if any activation fault gate cannot be made deterministic and safe.

## 3. Locked decisions

| ID | Decision |
|---|---|
| F01 | Production has one execution lifecycle: typed canonical submission → PgQueuer delivery → fenced kernel → canonical evidence/presentation. No process-local job/run/batch lifecycle remains. |
| F02 | Delete detached letterbox batch lifecycle (`JobState`, active job singleton, `start_batch`, `_execute_batch`, legacy progress bridge) after extracting any still-used pure detection/grouping algorithms. |
| F03 | Delete detached poster pipeline `RunManager.start/_execute`, process-local active-run state, subscriptions, and obsolete `batch_runner` lifecycle. Retain archive/GPU/helper behavior only through small purpose-named services used by canonical handlers. |
| F04 | Delete taste multiprocessing/process-local rebuild state, monitors, cancellation flags, and status compatibility after JMC6E canonical jobs own all rebuilds. |
| F05 | Delete unused legacy delivery executor injection, stale facades/DTOs/client helpers, per-job SSE/polling helpers, raw stage label maps, and compatibility response fields when reference analysis proves no live caller. |
| F06 | Tests for removed lifecycle behavior are removed or rewritten around canonical definitions, handlers, progress, cancellation, evidence, and API behavior. Do not preserve dead implementation solely to keep historical tests green. |
| F07 | Retain and strengthen pure algorithm/media regression tests. Moving a helper must preserve letterbox detection, poster ranking/OCR, archive parsing, taste math, and media validation behavior without invoking a lifecycle singleton. |
| F08 | Add static absence manifests for detached tasks, process-local job registries, inline long-running route work, old manager symbols, duplicate event streams, deprecated DTOs, and unmounted webhooks. Avoid naive bans on legitimate bounded SSE producers or request-scoped async tasks. |
| F09 | Remove or update stale design/API handoff documents that describe nonexistent `/api/activity`, custom workers, inline media jobs, or superseded response shapes. Historical architecture documents may remain only with an explicit superseded notice. |
| F10 | Legacy artifact/data migration helpers run at startup only when still required for the supported first-release target. Obsolete unreleased compatibility migrations are deleted; any retained migration is bounded, idempotent, observable, and tested. |
| F11 | Repeat the entire JMC6C zero-green matrix plus the JMC6D/E regressions. Passing the old 1,283/108/11 counts alone is insufficient. |
| F12 | Final fault certification includes actual worker-process recreation with a changed incarnation, schedule master gate off/on, external worker/scheduler Operations, equivalent/conflicting submissions, refresh with more than 20 unrelated jobs, and deferred webhook absence. |
| F13 | Final certification records exact enabled/reserved definitions, handlers, entrypoints, runtime instances/capabilities, effective schedules, routes, schema, OpenAPI/client, tests, tools, hardware exceptions, and deferred surfaces. |
| F14 | Dolby Vision Profile 5/7 remains readiness-disabled unless a real `dovi_tool` and approved fixture smoke are completed. Mocks do not change that disposition. |
| F15 | Browser authentication/authorization, public reset replacement, Docker hardening/version alignment, webhook implementation, and `radarr_upgrade` remain explicit owner/deferred work. They are not silently certified. |
| F16 | Update the activation audit with corrected evidence and a clear remaining owner checklist. Do not describe local workflow lint as hosted CI success. |
| F17 | The implementer never pushes or activates. The owner pushes the compact history and requires GitHub-hosted Python matrix and frontend jobs to pass before activation. |
| F18 | No agent/model authorship, co-author trailer, contributor line, generator footer, or similar attribution is added. |

## 4. Retirement inventory and extraction rules

Use Serena/reference analysis plus static import tests before removal. Expected candidates include,
but are not limited to:

- `marquee/media/letterbox_manager.py` lifecycle classes and detached batch entrypoints;
- `marquee/pipeline/run_manager.py` detached execution/subscription/active-run lifecycle;
- `marquee/pipeline/batch_runner.py` when no canonical handler imports it;
- taste rebuild process state in `marquee/api/routes/taste.py`;
- test-only legacy delivery executors and result wrappers;
- handwritten frontend `MediaJob`/`active_job`, unused SSE/label helpers, and stale API handoffs.

Do not delete a whole module blindly. Extract a pure function/service when canonical code or valuable
tests still need its algorithm. New modules must not import API routes, global lifecycle managers,
canonical `Job` ORM mutation, PgQueuer rows, or page state.

Delete obsolete tests only with a disposition record naming the canonical replacement coverage.
The full suite remains zero-failure, zero-skip, and zero-xfail.

## 5. Final activation certification

Repeat and extend JMC6C certification:

### 5.1 Runtime and transport

- fresh schema plus forward upgrade from `jmc6c-complete` schema;
- PgQueuer durable install/upgrade/verify and queued/deferred/retrying/held state;
- API, external worker, scheduler, PostgreSQL, listener, and child restart/failure;
- changed worker incarnation and hostname, same-host PID reuse, remote uncertain mutation,
  publication-boundary recovery, cancellation death confirmation, and stale fence writes;
- connection/advisory-lock/task/log/event/storage budgets under saturation.

### 5.2 Producers, schedules, and user recovery

- every enabled definition, parent, producer, and effective schedule;
- schedule master gate default-off and explicit-on behavior without catch-up storm;
- equivalent/coalesced and conflicting concurrent submissions from two clients;
- exact feature-page recovery with empty storage, more than 20 unrelated jobs, dropped SSE,
  snapshot repair, hidden tabs, API restart, and terminal transition;
- external Operations health/capability warnings and missing-entrypoint behavior.

### 5.3 Product/media evidence

- all four primary feature areas plus supporting library/ML/maintenance work;
- success, no-change, partial, failure, retry, cancellation, timeout, unsafe, superseded, and
  deleted-subject presentation;
- representative generated/confined poster, audio/subtitle, letterbox, HDR, backup/restore, logs,
  artifacts, progress, and publish/recovery fixtures;
- optional live tool/hardware smokes only where the capability is actually available.

### 5.4 Security/deferred boundaries

- no mounted webhook routes or auth exemptions;
- no secret/path/raw PgQueuer leakage in APIs, logs, artifacts, diagnostics, or CI artifacts;
- deferred public reset and browser-auth risks remain prominently named;
- Docker is not declared certified by this plan.

## 6. Implementation phases

### Phase F0 — verify JMC6E and freeze retirement manifest

Verify predecessor compaction and gates; build the complete symbol/import/test/doc/startup inventory;
record pure-algorithm owners; add precise static absence and canonical replacement tests before
deletion.

### Phase F1 — letterbox and poster lifecycle extraction/removal

Extract required pure detection/ranking/archive/GPU helpers, migrate canonical callers/tests, delete
detached letterbox/pipeline execution state and unused batch runtime, and prove behavior parity plus
absence of executable legacy entrypoints.

### Phase F2 — taste, delivery, frontend, and compatibility cleanup

Delete process-local taste rebuild machinery, legacy delivery injection, stale DTO/client/SSE/label
helpers, compatibility fields, and obsolete fixtures. Regenerate contracts and update/remove stale
API/handoff documentation.

### Phase F3 — startup/schema/static retirement certification

Audit startup migration helpers and remove obsolete unreleased compatibility work. Prove fresh and
forward-upgraded schema equivalence, one executor per enabled definition, no legacy lifecycle
imports/startup hooks, and bounded retained migrations.

### Phase F4 — production-like activation certification

Run §5 in owned disposable infrastructure, including real worker-process recreation and external
topology smokes where the harness owns the processes. Record exact tools, capabilities, warnings,
resource budgets, and every manual/operator exception.

### Phase F5 — audit handoff, complete gates, and compaction

Update `jmc6c-activation-audit.md` or create its clearly linked successor with corrected manifests,
remaining deferred risks, hosted-CI/push requirements, and owner checklist. Run §7, update the
timeline, and perform §8 only when all local certification succeeds.

After every phase, run gates, commit, update the timeline, and continue immediately. Successful
phase completion is not a reason to stop.

## 7. Completion gates

- No referenced production symbol implements a process-local job/run/batch lifecycle or bypasses
  canonical submission/delivery.
- No deprecated manager/DTO/route/event-stream compatibility seam remains merely for old tests.
- Pure media/ML algorithm coverage remains at least as strong as before removal.
- Exactly one definition, handler, entrypoint policy, presenter, progress policy, and canonical
  producer exists for every enabled leaf; reserved definitions have no executable path.
- Worker recreation, schedule gate, external Operations, canonical route, overlap, and refresh
  regressions all pass under process/integration tests.
- Backend full suite has zero failures/skips/xfails; Ruff over `marquee tests scripts` passes.
- Alembic upgrade/fresh equivalence, PgQueuer verification, schema fingerprints, reset safety, and
  backup/restore pass.
- OpenAPI and generated TypeScript are deterministic; frontend unit/check with zero warnings,
  lint, production build, Playwright/axe, and generated-client drift pass.
- `git diff --check`, static secret/path/deprecated-runtime checks, and local actionlint pass.
- The working tree is clean and all certification output is sanitized.

Hosted GitHub Actions remains pending until the owner authorizes and performs the push. The agent
must say so plainly.

## 8. Mandatory final-only history compaction

Perform only after F5 and every completion gate succeed:

1. Verify a clean, linear, configured-author, JMC6F-only, unpushed range after exact
   `jmc6e-complete`.
2. Commit the final pre-squash timeline state with phase hashes, dispositions, complete manifests,
   certification, exceptions, pre-squash tip, and intended tag `jmc6f-complete`.
3. Create timestamped recovery branch/tag and a verified repository-external Git bundle.
4. Record the certified tree; soft-reset through RTK to the exact plan base and create one
   configured-author commit: `jmc6f: retire legacy runtimes and recertify activation`.
5. Prove exact tree identity, sole parent, clean worktree, recovery refs, and verified bundle.
6. Create annotated local tag `jmc6f-complete`; do not edit the timeline afterward.
7. Do not push, force-push, delete recovery material, or add authorship/generator attribution.

Any ownership, ancestry, backup, concurrent-work, or tree mismatch stops the rewrite.

## 9. Owner handoff

Report compact tag/hash/tree/base and recovery material; exact retired/extracted symbol and test
dispositions; schema/PgQueuer/OpenAPI/client hashes; enabled/reserved/handler/entrypoint/schedule/
capability manifests; full test/static/frontend counts; worker-recreation and refresh evidence;
live media/tool/hardware smokes and exceptions; resource budgets; deferred security/reset/Docker/
webhook/`radarr_upgrade` work; and the final checklist:

1. owner source/tree/recovery review;
2. owner-authorized push without rewriting compact history;
3. passing GitHub-hosted Python matrix and frontend jobs;
4. explicit deployment configuration, schedule gate, and capability approval;
5. one final owner activation decision.
