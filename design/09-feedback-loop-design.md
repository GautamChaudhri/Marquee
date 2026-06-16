# Marquee — Feedback Loop Design

**Status:** Reconciled with the codebase on 2026-06-16.

The feedback loop is implemented in `marquee/api/routes/feedback.py`,
`marquee/ml/feedback_store.py`, `marquee/ml/profile_updater.py`,
`marquee/ml/head_trainer.py`, and `marquee/pipeline/retro_features.py`.

## Golden Rule

Only explicitly approved or selected posters join the taste profile.
Unreviewed auto-picks never feed back into the exemplar store. Overrides write
a negative label for the rank-1 auto-pick and a positive label for the user's
selection.

## Implemented Actions

`POST /api/feedback` accepts:

| Action | Labels written | Taste profile | Deployment |
|---|---|---|---|
| `approve` | Positive label for the auto-pick | Auto-pick appended | Uses `PosterService.deploy()` when `deploy` is true/defaulted |
| `override` | Negative for auto-pick, positive for selected poster | Selected poster appended | Uses `PosterService.deploy()` when `deploy` is true/defaulted |
| `reject_all` | Negative for auto-pick | No exemplar added | No poster deployment |

`POST /api/feedback/undo` removes all labels with the provided `event_id` and
removes matching profile additions where possible. It does not restore a
previous deployed poster.

## Label Records

Labels are append-only JSONL records under `FEEDBACK_LABELS_PATH`
(`marquee/experiments/feedback/labels.jsonl` by default).

Version 2 records include:

- `event_id`, timestamp, run/movie/TMDB identity.
- `orig_filename`, action, role, label value.
- rank, stage reached, rejection reason, scorer, model name.
- gate snapshot.
- raw, normalized, and extended feature dictionaries when available.
- exemplar filename when a profile file was added.

The learned-head trainer reads v2 embedded normalized features directly and
falls back for older labels when possible.

## Rejected Poster Overrides

If the user selects a rejected candidate, the feedback route backfills missing
features when possible using `retro_features.compute_full_features()`.

Feature availability by rejection stage:

| Stage | Typical state | Feedback behavior |
|---|---|---|
| Download error | No usable file | Label may be recorded without training features |
| Resolution/style/OCR/pHash rejection | Original file usually exists | Retro feature computation attempts to fill raw/normalized/extended features |
| Detail gate rejection | Features already present | Label is fully trainable |

## Gate Override Tracking

Each label embeds the gate snapshot active at feedback time. Taste status uses
the label stream to report gate override pressure against the current knobs.
The threshold is `FEEDBACK_GATE_ALERT_THRESHOLD` (default `5`).

This is advisory only; Marquee does not automatically change gate thresholds.

## Learned Head

Implemented:

- `LogisticHead` in `marquee/ml/learned_head.py`.
- Training entry point `train_from_labels()` in `marquee/ml/head_trainer.py`.
- Auto-retrain after feedback when `HEAD_AUTO_RETRAIN=true` and activation
  thresholds are met.
- `SCORER=auto` uses a valid learned artifact when available, otherwise falls
  back to the weighted scorer.

Activation thresholds:

| Knob | Default |
|---|---|
| `HEAD_MIN_LABELS` | `150` |
| `HEAD_MIN_MOVIES` | `5` |
| `HEAD_AUTO_RETRAIN` | `True` |

[PLANNED] Pairwise LightGBM/LambdaMART ranking is not implemented.

## Related API

| Endpoint | Purpose |
|---|---|
| `POST /api/feedback` | Submit approve/override/reject_all |
| `POST /api/feedback/undo` | Undo one feedback event |
| `GET /api/taste/status` | Label/exemplar/head/gate status |
| `POST /api/taste/retrain` | Background profile/head rebuild |
| `GET /api/pipeline/runs/{run_id}` | Results payload consumed by feedback UI |
| `POST /api/pipeline/runs/{run_id}/rescore` | Arithmetic re-rank from archived features |
| `GET/PUT /api/config/pipeline` | Inspect or hot-update runtime knobs |

## UI Contract

No frontend is implemented in this repository. The backend already exposes the
pieces a UI needs:

- run trigger and SSE progress;
- results grouped as ranked/rejected candidates;
- poster image serving;
- feedback submission and undo;
- taste/config status and rescore endpoints.

## Needs Verification

- `[NEEDS VERIFICATION]` The exact gate-alert response shape should be verified
  from `GET /api/taste/status` before frontend implementation.

## Test Coverage Note

Undo behavior should stay documented conservatively until route tests cover
labels with DINO/calibration columns. Those artifacts are rewritten during
profile rebuilds, so feedback undo tests should assert both label removal and
profile-artifact consistency.
