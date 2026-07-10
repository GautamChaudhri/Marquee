# 15 — Letterbox TV Verdict / Terminology / Uniformity Overhaul — Backend

> **For the implementing agent:** Read `CLAUDE.md`, the shared backend ground rules in
> `design/plans/04-television-backend.md` §0, and this document IN FULL before writing any
> code. Decisions are final — no redesigning, no questions. Re-locate every line anchor
> before editing. **No Alembic migration** — buckets/verdicts/uniformity are read-time
> derivations; DB status values (`not_letterboxed`, `sampled_clear`, `candidate`, …) are
> NOT renamed. If you find yourself editing a model or writing a migration, stop and
> re-read.
>
> **Timeline doc (shared with the frontend plan):**
> `design/plans/15-letterbox-tv-verdict-overhaul-timeline.md`. FIRST check whether it
> exists; exists → read, verify against `git log`, RESUME. Missing → create before the
> first commit. Append after every commit: completed (hash), in progress, next steps,
> deviations, pending operator actions.

**Goal:** Replace the "Clear/Clean/Mixed" vocabulary with content-descriptive verdicts
(Widescreen / Open Matte / Pillarbox), introduce the content-types rollup for multi-badge
rendering, replace the uniformity model with Uniform / Clean Mix / Dirty Mix (fixing the
"one variable episode still reads Uniform" bug), and fix the shows-list segments/counter so
OM/PB shows stop rendering an empty bar. **TV surfaces only** — the movie workspace and the
movie half of the landing page keep their current vocabulary (a later phase renames them).

Primary files: `marquee/core/letterbox_rollups.py`, `marquee/api/routes/letterbox.py`
(TV paths only), their tests.

---

## 1. Locked decisions

| # | Decision |
|---|---|
| V1 | **Bucket renames (read-time).** In `letterbox_rollups.py`: `clear` → `widescreen`, `sampled_clear` → `sampled_widescreen` — in `BUCKET_ORDER` (:7), `episode_bucket` (:23; the `not_letterboxed` status maps to `widescreen`, the `sampled_clear` STATUS still maps to the `sampled_widescreen` BUCKET), `bucket_counts` keys, and every API payload that carries bucket names. All other bucket names keep their identifiers. Sweep `marquee/api/routes/letterbox.py` TV paths for the old bucket strings (e.g. the TV `verdict_breakdown` keys at ~:884-896, prefetch/rollup payloads); DB status strings written to `LetterboxState.status` are untouched. |
| V2 | **Verdict model.** `_verdict_for_counts` (:92) is replaced. Single value: `needs_action` (candidate or error present) → `treated` (else tagged or reencoded present) → `ok` (else at least one of widescreen/sampled_widescreen/open_matte/pillarbox/variable present) → `unanalyzed` (nothing known; ineligible-only also lands here). `variable` counts as known/resolved — it contributes to `ok`, never to `needs_action` — but forces Dirty Mix uniformity (V4). The values `clean` and `mixed` cease to exist. |
| V3 | **Content types.** `season_rollup` and `show_rollup` gain `content_types`: an ordered list of `{type, count}` for the present members of `widescreen` (sampled_widescreen folds into its count), `open_matte`, `pillarbox`. Order: descending count, then the listed type order. Empty list when none known. This is what the FE renders as multi-badges — no "mixed" aggregation anywhere. |
| V4 | **Uniformity model.** Per-episode presentation label: for bar-free episodes the content type (`widescreen` — sampled included — / `open_matte` / `pillarbox`); for `candidate`/`tagged`/`reencoded` the detected `aspect_label` (as today); `variable` episodes are a poison pill; `unanalyzed`/`error`/`ineligible` cast no vote. Season uniformity: `null` (no votes) / `dirty_mixed` (any variable, or >1 distinct label) / `uniform`. Show uniformity: `null` (no voting seasons) / `uniform` (all voting seasons uniform with the same label set) / `clean_mixed` (every voting season internally uniform but labels differ across seasons) / `dirty_mixed` (anything else — any non-uniform season or any variable). The value `uniform_by_season` is deleted. `_season_uniformity` (:83) and the `show_rollup` uniformity block (:127-138) are rewritten; `BAR_BEARING_BUCKETS` usage for uniformity is superseded by the presentation-label rule (dominant_aspect_label keeps its current basis — verify its consumers before changing anything about it). |
| V5 | **Filters + summary.** `list_tv_letterbox` (letterbox.py:917): `verdict` param accepts the new state values AND content-type membership — `verdict=widescreen|open_matte|pillarbox` matches shows whose `content_types` include that type; `verdict=needs_action|treated|ok|unanalyzed` matches the rollup verdict. `uniformity` param accepts `uniform|clean_mixed|dirty_mixed`. The `/summary` TV block (~:880-912): rename `verdict_breakdown` keys `clear`→`widescreen`, `sampled_clear`→`sampled_widescreen` (other keys incl. `letterboxed_untreated` unchanged); `show_verdict_counts` / `uniformity_counts` emit the new value sets. The MOVIE half of `/summary` and `_verdict_breakdown_key` (:421, movie statuses) are untouched. |
| V6 | **Season/show payload shape.** Rollups keep `bucket_counts` (renamed keys), `verdict`, `uniformity` (now nullable), `dominant_aspect_label`, `episodes_total`, `has_candidates`, plus new `content_types`. Anything else in the TV detail/list payloads keeps its shape so the FE diff stays mechanical. |
| V7 | **Regression guards.** Movie letterbox routes byte-identical (snapshot tests). Detection/apply/reencode/preview behavior untouched — this plan only renames read-time vocabulary and rewrites two pure rollup functions. `media_type` guards on any new query. Full pytest baseline must not grow (known env failure: `test_effective_ocr_workers_caps_cuda_unless_gpu_forced`); `ruff check marquee tests` only. |

## 2. Phase 1 — rollups (V1-V4, V6)

Rewrite `letterbox_rollups.py` per the table; keep it pure (no DB). Rewrite its unit tests
as a decision matrix:

- bucket mapping incl. `not_letterboxed`→`widescreen`, `sampled_clear`→`sampled_widescreen`;
- verdict precedence incl. variable-contributes-to-ok, error→needs_action,
  ineligible-only→unanalyzed;
- content_types folding + ordering;
- uniformity: no votes → null; one label → uniform; one variable anywhere → dirty_mixed;
  two seasons internally uniform with different labels → clean_mixed (the user's
  "2 seasons pillarbox + 2 seasons widescreen" case); mixed labels inside one season →
  dirty_mixed; letterboxed aspect labels (2.39:1 tagged season vs widescreen season) →
  clean_mixed; specials (season 0) still excluded from show rollups.

## 3. Phase 2 — routes (V1, V5)

Sweep the TV paths in `marquee/api/routes/letterbox.py`: listing filters, `/summary` TV
block, TV detail payloads, any bucket-string literals in TV scope logic (**verify each hit
individually** — several places compare against LetterboxState STATUS strings like
`sampled_clear`, which must NOT change; only BUCKET vocabulary changes). Update/extend route
tests: filter by each new verdict value, filter by content type, filter by new uniformity
values, summary key assertions, movie snapshot tests untouched and passing.

## 4. API contract (changed)

| Surface | Change |
|---|---|
| Bucket vocabulary (all TV payloads) | `clear`→`widescreen`, `sampled_clear`→`sampled_widescreen` |
| `verdict` (season/show rollups, filters) | `needs_action` / `treated` / `ok` / `unanalyzed`; filter additionally accepts `widescreen`/`open_matte`/`pillarbox` (content membership) |
| `uniformity` (rollups, filters) | `uniform` / `clean_mixed` / `dirty_mixed` / `null` |
| Rollups | new `content_types: [{type, count}]` |
| `/summary` TV `verdict_breakdown` | keys renamed per V1 |

## 5. Out of scope

Movie vocabulary (workspace, landing movie half, movie statuses). Any FE change (sibling
plan 15-frontend). Detection/scan semantics. Migrations. The job-platform work (plan 14).

## 6. Operator notes

- API vocabulary changes are breaking for any external consumer of the TV letterbox routes;
  the only consumer is the bundled FE, which ships in the sibling plan — deploy backend and
  frontend together.
- After deploy, saved FE filter URLs with `verdict=clean|mixed` or
  `uniformity=uniform_by_season` silently match nothing — harmless, they just show empty
  until re-selected.
