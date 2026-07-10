# Plan 13 Letterbox TV Open Matte/Reencode Timeline

## Status

- completed: none
- in progress: phase 1 — classification + rollups
- exact next steps: implement TV dimension classification, rollup buckets/verdicts, API bucket overrides, and phase 1 tests
- deviations from the plan and why: none
- pending operator actions: none

## After phase 1

- completed: phase 1 — classification + rollups (`d499284633f3a3dd4892643a56a62a34a8378fd4`)
- in progress: phase 2 — scan semantics
- exact next steps: add `include_open_matte` request/payload forwarding, skip OM/PB by default before force, implement mixed-season auto-exhaustive scan behavior, pass payload through the job handler, and add phase 2 tests
- deviations from the plan and why: none
- pending operator actions: none

## After phase 2

- completed: phase 2 — scan semantics (`9147b710f1b1f5db0ebb0a89aeb3d8d5c91590e6`)
- in progress: phase 3 — scanned-clear preview warm
- exact next steps: add a clear-preview warm helper, schedule one before-frame for `not_letterboxed` TV detects, and add phase 3 tests
- deviations from the plan and why: none
- pending operator actions: none

## After phase 3

- completed: phase 3 — scanned-clear preview warm (`0425996785053cb95a7de008f2a7e496fd6c72b4`)
- in progress: phase 4 — scoped apply/revert jobs + confidence filter
- exact next steps: add TV apply confidence validation/filtering, extract scoped apply/revert helpers, register durable scope handlers, route scoped apply/revert to jobs, and add phase 4 tests
- deviations from the plan and why: none
- pending operator actions: none

## After phase 4

- completed: phase 4 — scoped apply/revert jobs + confidence filter (`003e8d014f9df8477c7aed75097439b1e48f3d11`)
- in progress: phase 5 — TV reencode
- exact next steps: add single-episode TV reencode planning, confirm fan-out, artifact stamping/fan-out, TV batch reencode parent jobs, artifact filters/labels, bulk replace-ready, and phase 5 tests
- deviations from the plan and why: none
- pending operator actions: none
