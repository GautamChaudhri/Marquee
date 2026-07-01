# Marquee — Cold-Start Onboarding ("Rank Test")

**Status:** Implemented 2026-06-22. Builds on design 19 (pairwise head) and
`design/learning-roadmap.md`.

## Why

A fresh install ships **no taste profile** (`data/**` is gitignored), so
`NumpyTasteStore` raises `FileNotFoundError` and the pipeline can't rank. The
engine has two data-driven layers with different needs:

- **Layer A — taste profile (k-NN exemplars):** powers `knn_sim`, `dino_knn`,
  `taste_typicality`, calibration, the style gates. Seeds from example posters.
- **Layer B — pairwise learned head (design 19):** personalizes the re-rank.
  Seeds *only* from within-movie rankings — an existing library (one poster per
  movie) cannot seed it.

Onboarding is a guided **Rank Test** that seeds both.

## The Rank Test

The user bucket-ranks movies with the existing `PosterRankingPanel`
(Favorites tiers / Hate / Indifferent). Thresholds (config):

| | value | meaning |
|---|---|---|
| `ONBOARDING_RANK_TEST_MIN` | 15 | minimum to mark complete + activate |
| `ONBOARDING_RANK_TEST_GOAL` | 25 | the encouraged sweet spot |
| `ONBOARDING_RANK_TEST_MAX` | 40 | hard cap |

Progress = distinct movies with a v3 ranking event since the test started
(derived, not separately stored). Progress persists; the head stays dormant
until the test is **marked complete** (≥ MIN). `HEAD_MIN_MOVIES` must stay ≤ MIN
so a completed test always activates (validated in config).

## Two paths (auto-selected by data state)

- **Library** — the user has downloaded movies. `POST /api/onboarding/start`
  kicks the existing `taste_rebuild source="library"` job (Layer A from deployed
  posters) and a stratified `poster_pipeline_batch` over a genre-spread sample
  (`service.stratified_sample`, ≤ MAX). The user ranks those runs on the normal
  results pages; each counts toward the meter.
- **Taste test** — no/empty library. A bundled set (~40 movies × 3–5 posters,
  shipped under `marquee/onboarding/taste_test/`) is ranked in-app via
  `POST /api/onboarding/taste-test/rank`. Favorites are staged into the positive
  training folder, hated into the negative folder, and a v3 ranking event is
  written from the bundle's precomputed `normalized_features` (so the pairwise
  trainer reads it identically to a live rank).

A tiny **starter profile** (`marquee/onboarding/seed/`) is copied into
`TASTE_PROFILE_PATH` on first run if absent, so nothing hard-fails.

**Completion** (`POST /api/onboarding/complete`, ≥ MIN): enqueues
`taste_rebuild` (source = the path's, fills calibration) + `learned_head_train`
→ the head activates.

## Negatives

The Hate bucket *is* the negative-exemplar source during the test (high-ranked
hated posters → negatives, per design 19's cutoff). Not a separate stage — it's
fed as-used.

## Endpoints (`marquee/api/routes/onboarding.py`)

| Endpoint | Purpose |
|---|---|
| `GET /api/onboarding/status` | data state + Rank Test progress |
| `POST /api/onboarding/start` | choose/auto-detect path; library → rebuild+batch; test → movies |
| `GET /api/onboarding/taste-test/movies` | bundled movies (poster URLs) |
| `GET /api/onboarding/taste-test/posters/{file}` | serve a bundled poster (path-confined) |
| `POST /api/onboarding/taste-test/rank` | record one bundled movie's ranking (v3 event) |
| `POST /api/onboarding/complete` | require ≥ MIN → rebuild + train head |

## Reused (not rebuilt)

`taste_rebuild source="library"` ([taste.py](../marquee/api/routes/taste.py)),
`rebuild_profile` ([taste_trainer.py](../marquee/ml/taste_trainer.py)),
`poster_pipeline_batch` + `_downloaded()` ([pipeline.py](../marquee/api/routes/pipeline.py)),
the v3 `rank` flow + `build_pairwise_training_data`/`train_pairwise` (design 19),
`PosterRankingPanel.svelte` (now takes an optional `onSubmit`).

## Bundle build (runtime, GPU box)

The taste test ships empty (READMEs only); generate it where models + a
reference profile exist:

```bash
python -m marquee.onboarding.build_taste_test   # source/ posters → images/ + manifest.json
python -m marquee.onboarding.build_seed_profile  # images/ → starter profile seed
```

`service.taste_test_available()` is False (library path only) until the bundle
exists, so production is safe before it's built.

## Config knobs (`marquee/core/pipeline_config.py`)

`ONBOARDING_RANK_TEST_MIN/GOAL/MAX`, `ONBOARDING_STATE_PATH`,
`ONBOARDING_TASTE_TEST_DIR`, `ONBOARDING_SEED_PROFILE_PATH`; `HEAD_MIN_MOVIES`
raised 5→10 (≤ MIN).

## Frontend

`/onboarding` page: intro → begin → per-movie `PosterRankingPanel` (taste test)
or library instructions, with a 15/25/40 progress meter and milestone
reassurance. A banner on `/pipeline` links in when `needs_onboarding`.

## Tests

`tests/test_onboarding.py` (no ML): stratified sampler, taste-test rank → v3
event + staging, re-rank replacement, progress/completion gate, starter-profile
copy, and the status/rank/complete endpoints (job creation monkeypatched).

## Open / runtime

- Curating + building the taste-test bundle (images + embeddings) is a one-time
  data task on the GPU box.
- Live end-to-end (wipe `data/` → onboard → activate) needs models + TMDB.
