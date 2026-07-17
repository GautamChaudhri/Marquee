# JMC6C Activation Audit

> **Post-certification notice (2026-07-17):** The owner's required whole-system source audit found
> activation-blocking gaps not exercised by the JMC6C green suite. This audit remains historical
> evidence for the compact `jmc6c-complete` tree but is not the current activation authority.
> Complete [JMC6D runtime recovery and activation safety](jmc6d-runtime-recovery-and-activation-safety.md),
> [JMC6E canonical seam and refresh closure](jmc6e-canonical-seam-and-refresh-closure.md), and
> [JMC6F legacy retirement and activation certification](jmc6f-legacy-retirement-and-activation-certification.md)
> before repeating the owner activation decision.

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
