# JMC6 Runtime and Personalization Timeline

Shared execution and cold-handoff record for JMC6I (runner, progress, and certification closure)
and its successor JMC6J. JMC6I is the current and only active implementation plan. No push,
schedule activation, operator-state mutation, or JMC6J implementation is authorized.

## JMC6I Phase I0 — predecessor verification and plan-boundary freeze

### Exact starting state

- The JMC6I plan base is owner-authored planning commit
  `642c12a7b88b1f638fb998f4dc6789ddaf43ec26` (`once over fixes and learned head retraining
  plans`), tree `706604f972f0879c12d22e7787d2b4d5261d568c`, sole parent `jmc6h-complete`. That
  commit is docs-only (adds the JMC6I/JMC6J plans and design references; no `marquee/`,
  `tests/`, or `frontend/` change), so the JMC6H §2 source findings anchor without drift. It is
  immutable plan input, excluded from the JMC6I implementation range and final history rewrite.
- The exact predecessor is annotated `jmc6h-complete`, resolving to configured-author compact
  commit `f2ad64cb173481c34bc45bc3b077bce297da1593`
  (`jmc6h: complete product convergence and certification`), tree
  `d3ce2f4bf500420c8371845f4db27036e6f03cbd`, sole parent `jmc6g-complete`
  (`93a695b5843ffaaf26eae98e67669538c32b1a56`).
- JMC6H recovery material verified: branch `recovery/jmc6h-20260720T005731Z` and annotated tag
  `recovery/jmc6h-pre-squash-20260720T005731Z` both dereference to pre-squash commit
  `1c69140dc621a411e7f38943262cbb400a4994ae` with tree
  `d3ce2f4bf500420c8371845f4db27036e6f03cbd` — byte-identical to the compact jmc6h-complete
  tree. `git bundle verify /tmp/marquee-jmc6h-20260720T005731Z.bundle` reports a complete SHA-1
  history. All earlier recovery refs/bundles (jmc4b–jmc6g) are retained and unchanged.
- Branch is `job-manager` tracking `origin/job-manager`, exactly three commits ahead
  (`93a695b`, `f2ad64c`, `642c12a` — the two compact predecessors and the pushed-nothing
  planning commit); worktree clean at the plan base before this timeline. One worktree, no Git
  locks. Configured repository author and committer are Gautam Chaudhri
  `<gautam.chaudhri@gmail.com>`; that identity is the only permitted commit identity.
- Required documents read in full: `AGENTS.md`, `CLAUDE.md`, `design/plans/README.md`, the
  JMC6I plan, the JMC6H plan, the complete JMC6G/H final-closure timeline,
  `job-progress-and-loading-experience.md`, and `design/poster-pipeline.md`.
- Serena MCP, ByteRover MCP, and RTK are operational. Development and Git commands run through
  RTK.

### Owned baseline environment and tools

- Owned disposable PostgreSQL 18.3 cluster: `initdb -U marquee --auth=trust`, data root
  `/tmp/marquee-jmc6i-pg.Llx1Dx` (path saved to `/tmp/jmc6i-pgdata-path.txt`), listening
  `127.0.0.1:55467`, database `marquee_test`. Port 5432 (operator database) and the retired
  JMC6G/H clusters were not targeted.
  `DB_URL=postgresql+asyncpg://marquee@127.0.0.1:55467/marquee_test` is the only certification
  database.
- `python -m marquee.db_migration` applied Alembic `0001_jmc1` → `0012_jmc6h` and
  installed/verified PgQueuer 1.1.1 durable. Stored schema contracts on the fresh cluster match
  the JMC6H-certified fingerprints exactly: Marquee
  `18f206d4d74d1afb7f4d21b5b1efa1712c3dcc0715079be8fc8613499003f52a` at `0012_jmc6h`, PgQueuer
  `19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a` at 1.1.1. `DATA_DIR`
  remains the repository default (`tests/conftest.py` redirects per session).
- Tools: venv Python 3.13.14, pytest 9.0.3, Ruff (repo pin, `ruff check` clean), Alembic
  1.18.4, PgQueuer 1.1.1, PostgreSQL 18.3.

### Verified zero-green baselines

- Complete backend baseline on the owned migrated cluster: **1310 passed, 0 failed, 3
  skipped** — the three skips are exactly the sanctioned opt-in live smokes
  (`test_jmc6h_poster_live_smoke.py` ×1, `test_jmc6h_taste_live_smoke.py` ×2) without
  `MARQUEE_LIVE_SMOKE=1`, matching the JMC6H handoff (H6 ran them live for its 1313-passed
  zero-skip gate).
- `ruff check marquee tests scripts`: clean. Alembic current/head are the sole `0012_jmc6h`;
  `alembic check` reports no new upgrade operations.
- OpenAPI export is byte-identical: `design/api-schema.json` at 191 paths, SHA-256
  `f115de222a8c031f08d9c38825809afa96c22e11c9b10530b614b2f5a4e42091` (worktree stayed clean
  after regeneration). Frontend/browser baselines are recorded in the I0 checkpoint below.
- `git diff --check`: clean.

### Reserved JMC6J-owned files frozen at I0 (must be byte-identical at I5)

```
b2bfcd83698e121b179a347f1d0a34f6d0a76cf255f37e76c32c314a69a1979a  marquee/api/routes/onboarding.py
cb5cbc91e5b8cea289433c59de527c7769f289701d9a140e40e9f4240457a590  marquee/pipeline/scorer.py
b8dac8ab91e03468e5b34f1aeccb61ccca2d96903ea41b32351375d069c3172f  marquee/ml/learned_head.py
b3829d599963e3e5a4f11bcff81dd2ba2db2360313922004b32f05a3485f3787  marquee/ml/head_trainer.py
2f2be28fc93f77581aaa2ef416d1b7cfa0a52b6a64b5471c6caa29d94b36c002  marquee/onboarding/__init__.py
bbadf9ab83ede58a850c1d92e66c269344317f30b155b5d4d6555ec1dbb6255d  marquee/onboarding/build_seed_profile.py
6d00bd792978f95095ef00133cd33d089cf4d56781c20c81d1525c32ccec26b4  marquee/onboarding/build_taste_test.py
ba5a3fa38f2d763bb3abfe4ad88f907bfd3edbcf11ea05cdf1192c6631445bd6  marquee/onboarding/service.py
2f075a0821cc36f178f70ba937933463d8b267bd7bca45e334339553462580cd  frontend/src/lib/api/onboarding.ts
45de001edfe4ea2988ce02365e8bd7b928dbeff612b67cda5672b65ecf0edeb3  frontend/src/routes/onboarding/+page.svelte
2d786d1a74628daecdbe479eb1c71b1bee2bf34fb798b4aa8062a49d653aa257  frontend/src/routes/onboarding/+page.ts
```

Learned-head *behavior* additionally reserved: `marquee/core/jobs/handlers_ml.py::
_publish_native_learned_head` keeps its product behavior, publication contract, and absence of
a progress callback; only shared runner-host hardening may incidentally affect it.

### `run_internal_operation()` call-site inventory (§3.5)

| Call site | Operation | Classification |
|---|---|---|
| `marquee/core/jobs/poster_pipeline.py:367` | `POSTER_SINGLE` | poster — behaviorally in scope |
| `marquee/core/jobs/handlers_ml.py:76` | `TASTE_PROFILE` | non-head ML — in scope |
| `marquee/core/jobs/handlers_ml.py:170` | `TASTE_MAP` | non-head ML — in scope |
| `marquee/core/jobs/handlers_ml.py:270` | `ENRICHMENT` | non-head ML — in scope |
| `marquee/core/jobs/handlers_ml.py:390` | `LEARNED_HEAD` | learned-head — reserved to JMC6J; only incidental host hardening |
| `tests/test_jmc6h_internal_runner.py` (+ product-effect/workspace/live-smoke tests) | `NOOP` and family ops | fixed test canary / certification fixtures |

### Reconfirmed §2 findings at the plan base (all reproduced from source)

1. `run_internal_operation()` finally-block terminates the child only when its local
   `cancelled`/`timed_out`/`protocol_error` flags are set. Outer `Task.cancel()` or the
   delivery `asyncio.timeout` raises `CancelledError` at an `await` with all three false, so
   `finally` awaits `tracked.wait()` on a child nobody told to stop — an unbounded hang that
   also prevents the delivery-level shielded `process_launcher.shutdown()` net from running. A
   clean control-EOF without a result frame similarly reaches `tracked.wait()` unbounded.
2. Existing runner tests cover helper-local timeout and cooperative `should_stop` cancellation
   only; no test cancels the hosting task, fires the delivery timeout, or fails the progress
   callback.
3. Poster host adapter (`poster_pipeline.py::on_progress`) forwards only `_STAGE_MAP`-known
   stages whose `state == "start"`; the runner's `done`/`total`/`survivors` and OCR
   `state="progress"` counts are dropped.
4. `_run_taste_profile`/`_run_taste_map` child handlers discard trainer numeric fields
   (`processed`, `total`, `substage`, `current_item`); enrichment emits one raw stage; the
   taste/map/enrichment job handlers pass no `on_progress` at all.
5. `ExecutionProgress.stage()` mirrors one completed/total pair into both scopes **and** its
   fixed `"{job_type}:overall"` scope makes every legitimate indeterminate→determinate
   transition an invariant violation that `safe_write` silently swallows (reproduced with the
   dovi_convert policy: `overall measurement mode/unit change requires a new scope_id`).
6. JMC6G/H closure tests validate manifest fields and named test modules; `certification_test`
   strings are never resolved to collected nodes nor proven executed in the same session.
7. Onboarding/learned-head findings are real but reserved to JMC6J (files frozen above).

### Phase I0 checkpoint — intentional-red freeze verified

- Frontend/browser baselines on the unchanged plan base match the JMC6H handoff exactly:
  Svelte check 0 errors/0 warnings (677 files); Prettier/ESLint clean; Vitest **112/112** (10
  files); production build passes with only the inherited plugin-timing notice; generated
  TypeScript `0769bd0a09d6c505d0702b1b7ea7e98b353d3153fc9dc96a3d57e7639cfc1567` with a clean
  regeneration drift check; hermetic Chromium Playwright/axe **11/11 in 21.3s** (synthetic
  backend :3199 + built app :4173; no operator service touched).
- Added six intentional-red contracts, each failing on its asserted behavior (not harness
  error), reproduced against the live defect:
  - `test_jmc6i_runner_cancellation.py::test_outer_task_cancellation_terminates_runner_tree`
    — a single outer `Task.cancel()` leaves the host hung at
    `internal_runner_host.py` `await tracked.wait()`; bounded-cleanup assertion fails.
  - `…::test_outer_delivery_timeout_terminates_runner_tree` — the delivery
    `asyncio.timeout` cancellation reaches the same unbounded wait; TimeoutError never
    surfaces.
  - `…::test_progress_callback_error_terminates_runner_tree` — a progress-callback
    exception leaves the child alive and the host hung.
  - `test_jmc6i_runner_progress.py::test_poster_runner_counts_reach_durable_progress` — the
    runner's `done=5/total=12` and `survivors=9` never reach a durable determinate current
    scope (adapter forwards only recognized stage-start labels).
  - `…::test_taste_rebuild_trainer_counts_reach_durable_progress` — `execute_taste_rebuild`
    attaches no `on_progress` at all.
  - `test_jmc6i_executable_certification.py::test_certification_claims_require_executed_node_evidence`
    — a doctored manifest entry naming a never-executed node passes the static field
    acceptance; no session execution-evidence collection exists to reject it.
- I0 additionally reproduced (probe, not committed test): the `ExecutionProgress` facade's
  fixed `"{job_type}:overall"` scope rejects every indeterminate→determinate transition
  (`overall measurement mode/unit change requires a new scope_id`), which `safe_write`
  swallows — so today no determinate `stage(completed=, total=)` write ever persists after
  the initial delivery stage write. I2 owns the fix.
- Complete comparison on the owned cluster: **6 failed (exactly the six intentional reds),
  1310 passed, 3 skipped (sanctioned opt-in live smokes) in 133.44s** — no baseline
  regression. `ruff check marquee tests scripts` clean (one I001 import-order finding in the
  new test was auto-fixed before commit). `git diff --check` clean.
- **Current phase:** I0 checkpoint ready for the configured-author commit; I1 begins
  immediately after (single cancellation-safe cleanup state machine per plan §5).
- **Exact next steps:** commit I0; restructure `run_internal_operation()` so every exit after
  launch classifies outcome separately from cleanup necessity, runs bounded
  cooperative/TERM/KILL cleanup with owned-tree death confirmation in a
  cancellation-resistant section, preserves original cancellation/timeout classification,
  bounds the normal-exit wait, and surfaces death-confirmation failure as a hard operational
  failure; then make the three cancellation reds green plus the full §5 matrix.
- **Operator/live-smoke state:** no operator database, media, schedule, push, activation, or
  external coordination used. Only the owned cluster and synthetic fixtures. The three opt-in
  live smokes remain pending for I5 execution or exact unavailable-readiness recording.
- **Deviation:** none.

### I0 committed state

- Configured-author phase commit: `44b0df6fb034cf5a5afba61180e4e2b278b310f2`
  (`freeze runner and certification defects`), tree
  `67833028e234035589df3555615e96fdb4b6029a`; sole parent is the plan base
  `642c12a7b88b1f638fb998f4dc6789ddaf43ec26`. Author and committer are Gautam Chaudhri
  `<gautam.chaudhri@gmail.com>`. Worktree clean immediately after commit.
- **Current phase:** I1 — cancellation-safe runner cleanup is in progress.

## Phase I1 checkpoint — cancellation-safe runner cleanup and confirmed tree death

- `run_internal_operation()` now separates outcome classification from cleanup necessity.
  The protocol loop records observations in a `_ProtocolState`; **every** exit after launch —
  validated result, clean EOF, protocol violation, `should_stop`, helper deadline, outer
  `Task.cancel()`, the delivery `asyncio.timeout`, progress-callback/decoder failure, worker
  shutdown — funnels into one `_cleanup_tracked()` task: a bounded natural-exit grace
  (`exit_grace_seconds`, default 5.0) for non-terminate exits, then cooperative/TERM/KILL
  escalation with owned-tree death confirmation; the reader task is cancelled and awaited
  exactly once in its `finally`.
- Cleanup runs in a cancellation-resistant section (`_await_cleanup`): outer cancellations
  delivered mid-cleanup are absorbed and counted, never abandon the escalation, and are
  honored (re-raised) once death is confirmed. The original interrupt classification is
  preserved: outer `CancelledError` re-raises as `CancelledError` (so the delivery
  `asyncio.timeout` still converts it to its own `TimeoutError` classification), and a
  callback error re-raises unchanged. Death-confirmation failure raises
  `ProcessLaunchError` **over** any pending cancellation — a hard operational failure that
  can never be acknowledged as successful cancellation (delivery's existing
  `writer.unsafe`/quarantine/`DeliveryRejectedError` path handles it).
- `TrackedProcess` gains `wait_bounded(timeout)` (side-effect-free bounded natural-exit wait,
  safe to escalate after) and `cancel()` gains a bounded post-SIGKILL wait
  (`kill_seconds=5.0`) so no escalation stage can block forever; an unkillable child is
  death-confirmation failure, not silence. The fixed noop child gained three
  closed-vocabulary race behaviors (`result_then_cooperative`, `result_then_ignore`,
  `close_control_then_hold`) used only by certification.
- The three I0 cancellation reds are green, plus six new matrix contracts:
  worker-shutdown cancel delivered inside a blocking progress callback; a second cancel
  landing during cleanup (absorbed, honored, tree dead); result-then-resistant child
  (bounded, SIGKILL, honest failure — never success); result-then-cooperative linger
  (bounded nudge, still honest success); clean EOF without result (bounded `NoResult`
  failure); simulated death-confirmation failure (hard `ProcessLaunchError`, not
  cancellation). Focused runner set: **31 passed** (`test_jmc6i_runner_cancellation.py` 9 +
  `test_jmc6h_internal_runner.py` 22).
- Complete comparison: **1319 passed, 3 failed (exactly the sanctioned I2/I3/I4 intentional
  reds: poster progress, taste progress, executable certification), 3 skipped (opt-in live
  smokes) in 107.35s**. Ruff clean; `git diff --check` clean.
- **Current phase:** I1 checkpoint ready for the configured-author commit; I2 (independent
  typed progress scopes) begins immediately after.
- **Exact next steps:** commit I1; add the bounded versioned runner progress-frame model and
  parsing; extend `ExecutionProgress` with an observation API for independent
  overall/current scopes (epoch-scoped scope IDs so legitimate mode transitions persist
  instead of being silently swallowed), survivor metrics, and coalescing through
  `ProgressCoalescer`; certify monotonic overall, current-scope reset, stale/out-of-order
  rejection, and degradation isolation.
- **Deviation:** none. No operator state touched.

### I1 committed state

- Configured-author phase commit: `c640382f0d6591f9f1570b62f9e27065f1552326`
  (`make runner cleanup cancellation safe`), tree
  `22b95f1b152133f03c0c1fc42bf7ab37288e9822`; sole parent is I0 commit `44b0df6f…`. Worktree
  clean after commit.
- **Current phase:** I2 — independent typed progress scopes is in progress.

## Phase I2 checkpoint — independent typed progress scopes and the bounded runner bridge

- `ExecutionProgress` now owns an observation API (`observe`) with independent
  overall/current `ScopeObservation`s. The overall scope is stable and monotonic (tracked by
  fraction across epochs); the current scope resets only when its caller-stable scope key
  changes; legitimate mode/unit transitions allocate epoch-suffixed scope ids so they satisfy
  the durable validator instead of being silently swallowed; regressions, shrinking totals,
  non-finite values, and unit-less determinate claims are recorded as observable degradation
  (`degraded_observations`) and never raise into the product operation. Scopes whose policy
  declares no unit are honored as `none`-mode scopes (this also un-swallows every
  maintenance-family write that the old fixed-scope facade dropped).
- `stage()` keeps its one-axis compatibility surface (dovi/generation/re-encode/maintenance
  handlers unchanged) but now delegates to `observe`, so determinate `completed/total`
  measurements genuinely persist after the delivery-style indeterminate first write — the I0
  probe defect (`overall measurement mode/unit change requires a new scope_id`, silently
  swallowed) is fixed. Every write flows through a policy-configured `ProgressCoalescer`
  (cadence, meaningful delta, max staleness); `durable=True` writes flush immediately,
  high-frequency samples coalesce, and `flush_pending()` closes the tail.
- New `runner_progress.py`: the bounded versioned `RunnerProgressFrame` model
  (closed field allowlist, finite bounded values, `done<=total`, bounded text, monotonic
  optional cursor) and `RunnerProgressBridge`, which maps runner-native stages through a
  definition-owned vocabulary, derives a monotonic furthest-stage overall measurement over
  the declared stage denominator, produces determinate current measurements from real
  `done`/`total`, forwards survivor counts, coalesces intra-stage samples, and records
  unknown stages/fields/stale cursors as degradation. Runner strings never become primary UI
  copy.
- Typed metrics extension: `ProgressMetrics`/`ProgressMetricObservation` gain bounded
  `items_survived`; the JMC3B b0 progress-schema freeze was deliberately extended by that one
  field (same precedent as JMC6H's `launch_internal_runner` freeze update). ML publication
  policy `current_unit` corrected `"stage"`→`"items"` so real item counts are expressible.
- Focused certification: `test_jmc6i_progress_scopes.py` (11) — epoch-scoped mode
  transition persistence, overall monotonicity + degradation on regression, current reset
  semantics, same-key mode-change epochs, sticky survivor metrics, write-failure degradation
  isolation, coalescing + flush, malformed-frame rejection (11 shapes), bridge
  mapping/unknown-stage/stale-cursor degradation, bridge stage-position monotonicity, and
  legacy `stage()` persistence. Progress contract + JMC3B progress suites still green (23
  combined).
- Contract regeneration (intentional): OpenAPI remains 191 paths, SHA-256
  `47045ef0e90aad976566c341bf3e817530bc533fe8756e978e11dbe92ff2be55`; generated TypeScript
  `803b66976c4aa1f0fd6dd006c1f598a395a85bce510996a73d5975edad8a4983`; regeneration drift
  check clean.
- Complete comparison: **1330 passed, 3 failed (exactly the sanctioned I3/I4 reds), 3
  skipped in 111.78s**. Ruff clean after two auto-fixes (unused import, EOF newline);
  `git diff --check` clean. Frontend: check 0/0, lint clean, unit **112/112**, build passes,
  Chromium Playwright/axe **11/11 in 21.2s**.
- **Noted for family owners (not JMC6I scope):** remux/backup handlers mirror native percent
  or work counts through `stage()` with policy units `subjects`/`work`; the values are
  genuine and now persist, but those definitions' unit vocabulary could be made more precise
  by their owners later.
- **Current phase:** I2 checkpoint ready for the configured-author commit; I3 (poster and
  non-head ML progress wiring) begins immediately after.
- **Exact next steps:** commit I2; forward real numeric fields from the poster child and the
  taste/map/enrichment children; attach `RunnerProgressBridge` adapters in the poster,
  taste-rebuild, taste-map, and enrichment handlers (learned-head handler untouched); add
  handler-side validating/registering/publishing stage observations; flip the two progress
  reds green; prove snapshot retention across refresh/SSE journeys.
- **Deviation:** none. No operator state touched.

### I2 committed state

- Configured-author phase commit: `b77113cb5f3929797ccd26d5a8b3c3595ab4ef56`
  (`add independent typed progress scopes`), tree
  `a61fc80e26e508fdcc3c94da8912cff5ba400625`; sole parent is I1 commit `c640382f…`. Worktree
  clean after commit.
- **Current phase:** I3 — poster and non-head ML progress wiring is in progress.

## Phase I3 checkpoint — poster and non-head ML measurements reach durable progress

- **Poster.** `execute_poster_pipeline` replaces its stage-start-only adapter with a
  `RunnerProgressBridge` over the existing `_STAGE_MAP`: the runner's real
  `done`/`total` become the determinate current scope, OCR `state="progress"` counts and
  survivor counts persist (survivors as `metrics.items_survived`), the overall scope tracks
  the furthest registered stage over the declared 9-stage vocabulary, and `bridge.close()`
  flushes the coalesced tail in a `finally`. The immutable movie subject stays frozen. The
  poster child already emitted typed-compatible frames and is unchanged.
- **Non-head ML.** The taste-profile/taste-map children now forward the real trainers'
  numeric fields through a bounded `_trainer_progress_forwarder` (stage, state,
  done/total from `processed`/`total`, bounded subject/message, monotonic cursor);
  `profile_enrich.enrich` gains an optional `progress_callback` emitting per-item
  resolve counts over its trustworthy `len(names)` denominator (no algorithm/output
  change). `execute_taste_rebuild`/`_taste_map`/`_taste_enrich` attach bridges with
  definition-owned stage maps (`starting→collecting`, `clip*/dino*→features`,
  `calibration→evaluating`, `saving→training`, `completed→validating`; map
  `load/project/cluster/save→loading/features/evaluating/training`; enrichment
  `enriching/resolve→features`) and emit handler-side `validating`/`registering`/
  `publishing` stage observations around loader validation, artifact registration, and
  activation. **The learned-head handler is untouched — no bridge, no callback, reserved to
  JMC6J.**
- Both I0 progress reds are green
  (`test_poster_runner_counts_reach_durable_progress`,
  `test_taste_rebuild_trainer_counts_reach_durable_progress` — the latter's synthetic stage
  string was corrected from `embedding` to the trainer's real `clip`; the frozen assertions
  are unchanged). New `test_jmc6i_progress_journeys.py` proves refresh/API-restart
  equivalence (a fresh session factory reads the same job with the same measurements and
  fence), the shared presenter (`load_context` + `present_compact_progress`) projects the
  typed snapshot (overall percent, stage key, sequence), and terminal *failure* retains the
  last measured values (freshness `terminal`, no fabricated jump to 100%). Refresh/SSE
  card-retention journeys stay covered by the certified JMC6A/B/C shared-client suites and
  the Playwright gate, which passed unchanged against the extended snapshot shape.
- One harness accommodation: `_runner_bridge`/poster bridge use
  `getattr(context, "progress", None)` + an `ExecutionProgress` isinstance guard, because
  two legacy product-effect harnesses supply a `SimpleNamespace` progress (or none). The
  real delivery path always provides `ExecutionProgress`, and the real-path proof is the
  I0-frozen red tests, which use it.
- Reserved-file check: all 11 JMC6J-owned onboarding/scorer/learned-head hashes are
  byte-identical to the I0 freeze.
- Gates: family suites (product-effect, poster workspace, ML rescan, internal runner,
  PgQueuer delivery) **93 passed** after the harness accommodation; complete comparison
  **1334 passed, 1 failed (exactly the sanctioned I4 certification red), 3 skipped in
  108.90s**; Ruff clean; `git diff --check` clean; OpenAPI current at 191 paths (no drift);
  generated TS drift check clean; frontend check 0/0, lint clean, unit **112/112**, build
  passes, Chromium Playwright/axe **11/11**.
- **Current phase:** I3 checkpoint ready for the configured-author commit; I4 (executable
  behavioral certification) begins immediately after.
- **Exact next steps:** commit I3; add the session execution-evidence recorder plugin; write
  the typed executable certification matrix (checked-in classification +
  producer→delivery→runner→progress→terminal→consumer scenarios for poster_pipeline,
  taste_rebuild, taste_map, taste_enrich, and the noop transport canary) with negative
  controls; flip the certification red green; produce the executed/unavailable/deferred/
  failed report.
- **Deviation:** none. No operator state touched.

### I3 committed state

- Configured-author phase commit: `f5d60203f2bf7773ec49286a81318f7075a4324d`
  (`connect poster and ml runner progress`), tree
  `9e01be840b1cb03e02e4f16d68d19fd81615e1c3`, sole parent is I2 commit
  `b77113cb5f3929797ccd26d5a8b3c3595ab4ef56`. Worktree was clean after commit.

## Phase I4 checkpoint — executable producer-to-consumer certification

- Reconstructed the missing repository-external JMC6H recovery bundle at
  `/tmp/marquee-jmc6h-20260720T005731Z.bundle` from immutable recovery branch/tag
  `recovery/jmc6h-20260720T005731Z`; `git bundle verify` reports complete SHA-1 history and
  both recovery refs resolve to the compact `jmc6h-complete` tree
  `d3ce2f4bf500420c8371845f4db27036e6f03cbd`. The missing ephemeral `/tmp` copy was the only
  predecessor-verification deviation; source, refs, author, base, schema and generated-contract
  fingerprints remain as recorded in I0.
- Added a current-session pytest execution-evidence recorder and a typed checked-in executable
  certification matrix. Each JMC6I-owned manifest claim (`poster_pipeline`, `taste_rebuild`,
  `taste_map`, `taste_enrich`, `system_noop`) now references a concrete scenario node that must
  resolve, be collected, and pass in the current session; manifest nodes are normalized and checked
  against the matrix rather than accepted as strings. The report separates executed evidence,
  unavailable opt-in live capability, deferred JMC6J scope, and failed evidence.
- Executed scenarios drive canonical route/command producers, PgQueuer delivery, runner-frame
  progress, terminal persistence, artifacts/projections, and API/presentation consumers. The
  contained noop transport executes as a real fixed runner; the model-heavy family interiors use
  controlled typed outputs while the configured opt-in live nodes remain explicit capability evidence.
  Negative controls reject nonexistent, unexecuted, skipped/failed, stale-manifest, static-only,
  and schema-valid-but-consumerless claims.
- Focused executable-certification suite: **14 passed**. Full backend on a freshly recreated,
  UTF-8 owned PostgreSQL 18.3 cluster at `127.0.0.1:55468/marquee_test`, migrated by
  `python -m marquee.db_migration` through `0012_jmc6h`: **1348 passed, 0 failed, 3 skipped in
  107.18s**. The skips are precisely the sanctioned opt-in JMC6H poster/taste live smokes and are
  unavailable, not counted as passed; I5 must execute them or record exact unavailable readiness.
  A prior default-port invocation is excluded because it targeted an un-migrated, non-CREATEDB
  service and failed backup/migration infrastructure checks before product certification.
- Focused Ruff for the changed certification files and `git diff --check` pass. The OpenAPI export
  check passes at 191 paths; the installed `openapi-typescript` binary is unreadable (mode 700,
  another owner) even outside the sandbox, so the generated-TypeScript executable check is a pending
  operator environment repair. No schema or frontend source changed. All eleven JMC6J-reserved
  onboarding/learned-head file hashes remain byte-identical to I0.

### I4 committed state

- Configured-author phase commit: `b1533701dc121c446c2fe26ed950218bdbb5e9bd`
  (`certify executable closure evidence`), tree
  `98ebb4a75662638610dfc2bc68551685de0fa279`, sole parent is I3 commit
  `f5d60203f2bf7773ec49286a81318f7075a4324d`. Worktree was clean after commit.
- **Current phase:** I5 — full regression and readiness certification is in progress.
- **Exact next actions:** execute runner-death, progress-refresh, static-reachability, available
  live-capability, complete backend, frontend, and generated-contract gates; fix every I-owned
  finding and compare all reserved JMC6J hashes before final-only compaction.
- **Pending operator work:** restore read/execute access to the repository-installed
  `frontend/node_modules/openapi-typescript/bin/cli.js` so the generated-TypeScript drift command
  can run. No operator database, library, schedule, remote, or production service was mutated.

## I5 stop record — generated-contract tooling unavailable

- **Current tree/commit:** `b1533701dc121c446c2fe26ed950218bdbb5e9bd` is the last committed I4
  checkpoint; the only worktree change is this chronological timeline ledger. The JMC6I range after
  plan base `642c12a7b88b1f638fb998f4dc6789ddaf43ec26` remains linear, local, and configured-author.
- **Evidence:** `npm run api:check` passes and confirms the checked-in OpenAPI export is current at
  191 paths. `npm run api:generate:check` fails before generation because
  `frontend/node_modules/.bin/openapi-typescript` resolves to a mode-700 executable owned by another
  account. Direct `node node_modules/openapi-typescript/bin/cli.js …` fails with the same EACCES;
  an elevated retry also fails. The existing generated TypeScript file cannot therefore be
  deterministically regenerated or drift-checked in this environment.
- **Why this stops I5:** JMC6I §8/§9 requires the generated-contract gate to be green before I5
  certification and final-only compaction. Reinstalling or replacing the shared `node_modules` tree
  would be a destructive dependency rewrite beyond the current source change and requires operator
  authority. No skip, xfail, fabricated pass, or alternate unchecked generator was used.
- **Exact unblock:** restore readable/executable ownership or reinstall the frontend dependencies
  under the repository operator, then run `npm run api:generate:check` from `frontend/`; execute the
  remaining I5 backend/live/frontend/Playwright/static-reachability gates; append their results; only
  then certify I5 and begin the final recovery/squash/tag protocol.
- **Status:** blocked at I5. No runner/process, onboarding, learned-head, API, schema, frontend,
  operator, or Git-history change is made after the I4 checkpoint other than this stop record.

## I5 recovery update — contract/browser gates restored; poster live runtime unavailable

- The generated-contract blocker is resolved: `npm run api:generate:check` passes. Frontend check,
  lint, unit (**112/112**), and production build pass; after installing the repository-pinned
  Playwright Chromium runtime, the hermetic synthetic-backend browser/axe suite passes **11/11**.
  The earlier browser failure is excluded because Chromium was absent before any test executed.
- The I5 process-death/progress-refresh/executable-certification matrix passes **58/58** on the owned
  migrated database. All eleven I0-reserved JMC6J onboarding/scorer/learned-head hashes still match.
- Available live taste capabilities pass: `test_jmc6h_taste_live_smoke.py` reports **2 passed**.
  The real contained poster live smoke is unavailable on this host: with the default auto device its
  PaddleOCR worker fails CUDA initialization (`CUDA error(719)`); with `OCR_DEVICE=cpu` and
  `EXECUTION_PROVIDER=cpu` it fails Paddle's invalid-place initialization. Neither result is counted
  as passed, hidden, skipped, or attributed to JMC6I runner/progress code.
- **Current blocker:** JMC6I §8/§9 requires the final available/unavailable capability evidence and
  zero-green final gate. Repair or replace the host PaddleOCR/CUDA runtime so the existing real poster
  smoke can execute, or provide an operator-approved exact unavailable-capability disposition that
  satisfies the plan's final gate. Final-only compaction/tagging remains prohibited.

## I5 correction and completion — contained CPU OCR and final regression green

- **Correction to the preceding historical record:** the failed `OCR_DEVICE=cpu` attempt was not a
  host-only unavailable-capability result. `ProcessLauncher._minimal_environment()` correctly keeps a
  small security boundary, but it had no typed route for the canonical job snapshot's `OCR_DEVICE` and
  `OCR_WORKERS`; it also omitted trusted deployment `EXECUTION_PROVIDER` and deliberate CUDA visibility.
  The contained runner therefore ignored the parent's forced-CPU test environment and its OCR worker
  selected the CUDA-built Paddle wheel's invalid GPU path. The earlier CUDA-719 default-auto attempt is
  retained as historical evidence, but its root cause is now fixed as a JMC6I runner runtime-configuration
  defect rather than attributed solely to the host.
- I5 phase commit: `1cc7851e587cbcfd25bfdafe9d22df8a9872a79b`
  (`fix runner ocr runtime options`). It introduces frozen, bounded `RunnerRuntimeOptions`; validates the
  closed OCR/device/provider/CUDA-visibility vocabulary; and passes only those values into the existing
  minimal child environment. Poster OCR device/workers come solely from the immutable job configuration
  snapshot; execution provider and `CUDA_VISIBLE_DEVICES` come from trusted deployment configuration;
  explicit CPU hides CUDA with `-1`. The test-only fixed-runner observation proves no parent OCR override
  or arbitrary secret crosses the boundary.
- Paddle auto detection now means a usable CUDA runtime: it requires a CUDA-built Paddle wheel, positive
  device count, and a guarded tiny allocation/synchronization probe. Auto logs the probe diagnostic and
  falls back to CPU; forced GPU fails with the detected condition; successful GPU selection uses PaddleOCR
  `gpu:0` syntax. New focused tests cover zero-device auto fallback, forced-GPU clarity, snapshot-over-
  payload/environment control, forced CPU contained-runner transport, CUDA restriction preservation, and
  secret exclusion (**99 passed** together with the established OCR/runner suites).
- The real contained poster capability smoke now supplies explicit bounded CPU runner options and passes
  **1/1 in 21.04s**; real taste capability smokes pass **2/2 in 13.22s**. This is the actual
  host→contained-runner→OCR-worker proof, not a parent-environment assumption.
- A newly reproduced shared-progress-card WCAG AA contrast defect (4.43:1 in current Chromium) was fixed
  by the minimal dark `--muted` token adjustment (`#8a909f`→`#9096a5`); it is JMC6I progress presentation
  scope and the hermetic Playwright/axe suite now passes **11/11 in 19.9s**. No onboarding behavior or
  threshold changed.
- Final gates: full backend on the owned migrated PostgreSQL 18.3 database
  `127.0.0.1:55468/marquee_test` is **1358 passed, 3 opt-in live skips, 1 Paddle wheel warning in
  107.62s**; Ruff clean; `alembic check` reports no upgrade operations; OpenAPI check is current at 191
  paths; generated TypeScript drift check passes; frontend check 0/0, lint, unit **112/112**, and build
  pass; `git diff --check` passes. All eleven JMC6J-reserved onboarding/scorer/learned-head paths remain
  byte-identical to I0. No skip/xfail was added or hidden.
- **Current phase:** I5 is complete. **Exact next actions:** verify the committed range is clean, linear,
  local, unpushed, configured-author, and JMC6I-only; record final hashes; create timestamped local
  recovery branch/tag and a repository-external verified bundle; soft-reset exactly to plan base; create
  the one final configured-author squash commit; prove its tree equals the certified pre-squash tree;
  create annotated `jmc6i-complete`; do not edit this timeline afterwards; do not push.
- **Pending operator work:** none. Manual smokes for browser visual review and production deployment remain
  normal post-certification operator activities; no operator media, library, schedule, remote, or production
  service was mutated here.

## Final-only pre-squash audit

- The complete JMC6I range from recorded base `642c12a7b88b1f638fb998f4dc6789ddaf43ec26` is clean,
  linear, local, unpushed, and **12 commits ahead** of `origin/job-manager`; every commit uses configured
  author `Gautam Chaudhri <gautam.chaudhri@gmail.com>`. Phase hashes are I0
  `44b0df6fb034cf5a5afba61180e4e2b278b310f2`, I1
  `c640382f0d6591f9f1570b62f9e27065f1552326`, I2
  `b77113cb5f3929797ccd26d5a8b3c3595ab4ef56`, I3
  `f5d60203f2bf7773ec49286a81318f7075a4324d`, I4
  `b1533701dc121c446c2fe26ed950218bdbb5e9bd`, and I5
  `1cc7851e587cbcfd25bfdafe9d22df8a9872a79b`; chronological ledger commits preserve the required
  prerequisite, recovery, blocker, and correction evidence.
- Final verification is the I5 green record above: executable certification and cancellation/death matrix,
  full backend zero-green suite, real poster CPU and taste capability smokes, Ruff, Alembic/schema, OpenAPI,
  generated contract, frontend check/lint/unit/build, Playwright/axe, reserved-file comparison, and
  whitespace check. The range contains no JMC6J reserved onboarding or learned-head change.
- **Exact next action:** make timestamped local recovery branch/tag and verified repository-external bundle
  from this certified pre-squash history; then soft-reset through RTK to the exact base and create the one
  configured-author final commit. This is the final timeline edit before tagging.
