# JMC6C Activation Audit

> **Post-certification notice (2026-07-17):** The owner's required whole-system source audit found
> activation-blocking gaps not exercised by the JMC6C green suite. This audit remains historical
> evidence for the compact `jmc6c-complete` tree but is not the current activation authority.
> Complete [JMC6D runtime recovery and activation safety](jmc6d-runtime-recovery-and-activation-safety.md),
> [JMC6E canonical seam and refresh closure](jmc6e-canonical-seam-and-refresh-closure.md), and
> [JMC6F legacy retirement and activation certification](jmc6f-legacy-retirement-and-activation-certification.md)
> before repeating the owner activation decision.

## JMC6F corrected activation candidate (2026-07-17)

This section supersedes the historical JMC6C activation recommendation below. It becomes the local
activation authority only when the final-only JMC6F compaction preserves the certified tree and the
annotated `jmc6f-complete` tag is created. It still does not authorize a push or activation.

- **Base and history:** exact base is annotated `jmc6e-complete` at
  `b46766bb4cca229db087e2dd35a389d952e6115e`, tree
  `5a13909b381c4f632bac29e7be0c773c938a64a3`. The configured-author JMC6F phase range is linear,
  local-only, unpushed, and records F0 `862e4fe`, F1 `b4fb11b`, F2 `7632c40`, F3 `b8c9a90`, and
  F4 `5a7123f`; the final compact hash/tree and F5 hash are frozen in the shared timeline before
  recovery creation and exact-tree squashing.
- **Single lifecycle:** production source contains one execution lifecycle: typed canonical
  submission → PgQueuer 1.1.1 durable delivery → fenced execution kernel → canonical evidence and
  presentation. Detached `RunManager`, batch-poster execution, letterbox batch state, taste rebuild
  process state, and delivery-executor injection are absent. Neutral retained helpers are
  `extractor_runtime`, `official_pick`, and the pure detection/grouping portion of
  `letterbox_manager`; static tests prohibit lifecycle coupling.
- **Definitions and execution:** 62 built-in definitions: exactly 43 enabled leaves and 43 canonical
  delivery handlers. The 19 ticketless/reserved types are `audio_subs_deep_scan`,
  `dovi_analyze_batch`, `letterbox_apply_batch`, `letterbox_apply_tv_scope`,
  `letterbox_detect_batch`, `letterbox_detect_tv_batch`, `letterbox_heal`,
  `letterbox_reencode_publish_batch`, `letterbox_reencode_tv_batch`,
  `letterbox_revert_tv_scope`, `poster_backup_all`, `poster_deploy_reset`, `poster_heal`,
  `poster_pipeline_batch`, `poster_pipeline_tv_batch`, `radarr_upgrade`,
  `subtitle_generate_batch`, `subtitle_policy_batch`, and `subtitle_scan_all`.
- **Enabled leaves:** `audio_remove`, `audio_reorder`, `backup_create`, `dovi_analyze`,
  `dovi_convert`, `dovi_discard`, `dovi_publish`, `dovi_restore`, `job_retention_purge`,
  `learned_head_train`, `letterbox_apply`, `letterbox_detect`, `letterbox_detect_episode`,
  `letterbox_detect_tv_scope`, `letterbox_reencode`, `letterbox_reencode_discard`,
  `letterbox_reencode_publish`, `letterbox_reencode_restore`, `letterbox_remove`, `library_sync`,
  `pipeline_cache_clear`, `poster_backup_subject`, `poster_deploy`, `poster_maintenance`,
  `poster_pipeline`, `poster_rescan`, `poster_reset`, `poster_restore`, `subtitle_embed`,
  `subtitle_extract`, `subtitle_generate`, `subtitle_metadata`, `subtitle_policy`,
  `subtitle_policy_audit`, `subtitle_remove`, `subtitle_restore`, `subtitle_scan`,
  `system_metrics_purge`, `system_noop`, `taste_enrich`, `taste_map`, `taste_rebuild`, and
  `track_remove`.
- **Routes/pages and recovery:** the five canonical seam routes/successors and 9 detail + 14 overview
  Activity consumers are frozen in `tests/fixtures/jmc6e/route_page_manifest.json`. Exact
  subject/correlation filters recover matching work beyond 20 unrelated jobs; shared-store tests
  cover empty storage, dropped/late SSE, snapshot repair, hidden tabs, navigation, two tabs, and
  terminal action recovery. Concurrent equivalent requests coalesce; conflicting unsafe requests
  return the active canonical job; terminal work releases overlap scope.
- **External topology:** an actual worker process was stopped and recreated under the same logical
  node label with distinct incarnation/PID-start identities. A separate real external scheduler and
  worker appeared healthy in Operations while the API had no embedded supervisor. Operations
  reported one worker, one scheduler, zero stale instances, no entrypoint mismatch, zero queued /
  picked / held work, and 9 observed database connections within the 28 configured / 32 maximum
  budget. A real-row defect in schema-contract presentation was corrected and is regression-tested.
- **Schedules and webhooks:** production schedule keys are exactly `library-sync`, `poster-heal`, and
  `audio-subs-deep-scan`. The master gate remains default-off; a real scheduler logged disabled
  occurrences without creating work, while deterministic tests certify explicit-on behavior,
  disable/re-enable, two schedulers, coalescing, and no catch-up storm. Radarr/Sonarr/Subgen webhook
  routes, auth exemptions, OpenAPI paths, and tests remain absent; `radarr_upgrade` is reserved.
- **Schema/contracts:** sole Alembic head `0008_jmc6e`, 48 public tables, fresh/forward column
  fingerprint `5696b90d283f77e77a8239c59cdb13c4`, index fingerprint
  `5570599ca32f71ee49eabe65932b8845`. PgQueuer contract fingerprint remains
  `19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a`. OpenAPI remains 199
  paths at SHA-256 `4f70e9e7c5cbd90e5359fc8fadde37ad48272756e203a4a1f899b67459f9c7a4`; generated TypeScript
  SHA-256 is `96eedda857b02f9e54ae402bac3da5ea01c55438816e210e3eb8c4c73aa36d89`.
- **Current local gates:** 1309 backend tests pass with zero failure/skip/xfail/xpass; Ruff,
  Alembic current/head/offline/check, official migration + PgQueuer verify, deterministic generated
  contracts, static absence, `git diff --check`, and actionlint pass. Frontend Svelte check is 0
  errors/0 warnings; Prettier/ESLint, 111 Vitest tests, production build, and 11 Chromium
  Playwright/axe tests pass. F5 repeats the complete set and freezes its final counts in the shared
  timeline.
- **Capability disposition:** generated/confined poster, audio/subtitle, letterbox, HDR,
  backup/restore, progress/evidence, cancellation/process-death, and publication fixtures are green.
  A project-local `dovi_tool 17ebb13` is present, but no owner-approved real Profile 5/7 fixture
  smoke ran. Restart-owned `JOB_DOVI_CONVERSION_CERTIFIED` therefore defaults false, the live
  conversion route returns typed 503, and Operations reports binary availability separately from
  `dovi_conversion_certified=false`. No new operator-library publish/restore or hardware encode
  smoke is claimed.
- **Still deferred:** browser authentication/authorization/sessions/CSRF, public reset replacement,
  Docker hardening/version alignment, webhook implementation, `radarr_upgrade`, hosted CI, and any
  Profile 5/7 conversion certification.

### Current owner checklist

- [ ] Review the final compact hash/tree/base, annotated `jmc6f-complete`, phase ledger, test
  dispositions, recovery refs, and verified external bundle.
- [ ] Authorize a non-rewriting push of the compact branch/tag; preserve all JMC6 recovery material.
- [ ] Require GitHub-hosted Python 3.12/3.13 matrix jobs and the frontend job to pass. Local
  actionlint is not hosted CI success.
- [ ] Keep `JOB_PRODUCTION_SCHEDULES_ENABLED=false` until an explicit deployment/schedule review;
  approve only required entrypoints and certified capabilities for the target host.
- [ ] Keep `JOB_DOVI_CONVERSION_CERTIFIED=false` unless a witnessed, approved Profile 5/7 fixture
  smoke validates the real tool, RPU/profile/color/playback metadata, publish, and restore path.
- [ ] Decide the deferred browser-auth, reset, Docker, webhook, and `radarr_upgrade` risks, then make
  one final owner activation decision.

Owner checklist for the compact `jmc6c-complete` candidate. This record freezes the locally
verified first-release contracts and does not authorize a push, production activation, or an
uncertified media capability.

## Certified local state

- Base: annotated `jmc6b-complete`, compact commit `a31cc75c3b648e8b948c49f20c05b4d4ffe8401d`,
  tree `98781c5a42e6461aadae5b613ddf3f35d6f12f47`.
- JMC6C range: configured-author only, linear, sole-parent, local, and unpushed at the C5 audit.
- Backend: `1283 passed, 0 failed, 0 skipped, 0 xfail/xpass, 2 warnings in 93.21s` on the owned
  disposable PostgreSQL 18.3 target at `127.0.0.1:55450/marquee_test`. The two warnings are the
  known third-party UMAP `n_jobs` warning; no Marquee warning is suppressed.
- Frontend: `108` unit tests and `11` Chromium Playwright/axe tests passed; `svelte-check` reports
  zero errors and zero warnings; Prettier/ESLint, generated-client drift, and the production build
  pass.
- Static and workflow: Ruff passes over `marquee tests scripts`; OpenAPI is deterministic at 202
  paths; `git diff --check` passes; actionlint 1.7.7 passes `.github/workflows/ci.yml`.
- Fresh reset: the guarded disposable reset reapplies Alembic `0001_jmc1` through sole head
  `0006_jmc4c`; PgQueuer 1.1.1 durable install/upgrade/verify, `alembic check`, and all 14 JMC1
  reset/schema tests pass. JMC6 changed no Marquee model or migration, so the certified JMC5
  baseline is retained.
- Former failures: all original 31 entries have a verified final disposition in
  `jmc6-former-test-dispositions.md`: 18 fixed, 1 replaced, 10 removed-obsolete, and 2
  removed-deferred webhooks.

## Frozen manifests

- Schema contracts:
  - Marquee `0006_jmc4c`, fingerprint
    `9158c083cfe84e8975473bd681a67036bb5d5485ad41a42b108c0a89ebac9701`.
  - PgQueuer `1.1.1`, durable, fingerprint
    `19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a`.
- API/client:
  - `design/api-schema.json` SHA-256
    `d515a891f50d0afca25423b805d869ede3df02782f47397b4588582d9b6650fe`.
  - generated TypeScript SHA-256
    `0fa8e5b913236756e55246bf12b82b805a13e8744e16fbb9eb08260644f260d0`.
- Definitions/executors: 61 definitions, 42 enabled leaves, and exactly 42 canonical PgQueuer
  execution handlers. The 19 ticketless/reserved definitions remain non-dispatching. Production
  schedule occurrences remain disabled; the code-owned schedule keys are exactly
  `audio-subs-deep-scan`, `library-sync`, and `poster-heal`.
- CI: Python 3.12 and 3.13; Node 22.22.2; PostgreSQL 18 service; `npm ci`; fresh Marquee plus
  external PgQueuer schema; Ruff/full backend/schema/OpenAPI gates; frontend generated-client,
  zero-warning check, lint, unit, build, Playwright/axe gates; read-only permissions, concurrency
  cancellation, official caches, and sanitized failure artifacts.
- Runtime tools: Python 3.13.14, pytest 9.0.3, Ruff 0.15.17, Node 22.22.2, npm 10.9.7,
  PostgreSQL 18.3, FFmpeg/ffprobe 8.1.2, MKVToolNix 99.0, actionlint 1.7.7, PgQueuer 1.1.1.
- Media tool hashes:
  - FFmpeg `8704c8b0817beced8b35dd3a42a05efcaa7c221903a98b6bd125956a80acbf88`.
  - ffprobe `0ad40e54238de4692f94abd4c5b97c73cb963d5ad89f4c077ba0ef625e9ef07b`.
  - mkvmerge `ca5e8ecd3c8faba1cbfdb4ec2ccf2cc5f15e773af184e12b7163d44fbef716c2`.
- Hardware observed outside the sandbox: NVIDIA GeForce RTX 3070, driver 595.80, 8192 MiB.

The exact enabled/reserved definition names are frozen by the registry/handler coverage tests and
the JMC5C/JMC6B contract fixtures; their final human-readable list is also recorded in the shared
JMC6 timeline.

## Capability disposition

- Certified with real generated/confined fixtures: poster deploy/reset/restore; audio/subtitle
  scan/remux/extract/embed/remove/reorder/metadata/restore; letterbox detect/tag/re-encode/
  publish/restore; process cancellation/death; staged publication; backup/restore; PgQueuer
  restart/upgrade; Activity refresh/reconnect/evidence; CPU media tools; and the recorded RTX 3070
  HEVC smoke.
- Readiness-disabled: Dolby Vision Profile 5/7 conversion/RPU validation. `dovi_tool` is absent;
  mocks prove only contract/path safety and do not certify this capability.
- Deferred and not certified: browser authentication/authorization/sessions/CSRF, replacement of
  the public reset endpoint, Docker least-privilege/runtime hardening, Radarr/Sonarr/Subgen
  webhooks, and `radarr_upgrade`.
- No operator database, media library, normal `DATA_DIR`, backup set, external provider, or remote
  ref was touched during JMC6C certification.

## Owner activation checklist

- [ ] Review compact commit/tree, annotated `jmc6c-complete`, dispositions, this audit, shared
  timeline, recovery refs, and verified external bundle.
- [ ] Review the exact 42 enabled leaves, 19 non-dispatching definitions, three code-owned
  schedule keys, resource limits, and readiness reports; enable only capabilities actually
  certified for the target host.
- [ ] If Dolby Vision Profile 5/7 is desired, install and hash a real `dovi_tool`, use approved
  backed-up Profile 5/7 fixtures, validate RPU/profile/color/playback metadata, and record a
  witnessed smoke before enabling it.
- [ ] Perform the planned whole-system review, including deferred security/reset/Docker/webhook
  boundaries and the absence of production schedule activation.
- [ ] Authorize the push. Do not rewrite or delete the JMC6C recovery branch/tag/bundle.
- [ ] Require both Python matrix jobs and the frontend job to pass on GitHub-hosted Actions; a
  local actionlint result is not a hosted CI result.
- [ ] Only after review, authorized push, and passing hosted CI, activate the explicitly approved
  certified definitions/schedules. Retain disabled readiness for every unavailable capability.
