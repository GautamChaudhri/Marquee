# Marquee — Preference-Ranking Feedback (bucket UI + pairwise learned head)

**Status:** Implemented 2026-06-22. Realizes the pairwise step of
`design/learning-roadmap.md` (Level 4 §9). Extends `design/09-feedback-loop-design.md`.

## Why

The Phase-1 learned head ([marquee/ml/learned_head.py](../marquee/ml/learned_head.py))
was a **pointwise** logistic regression. Two structural weaknesses:

1. **Sparse.** Each `approve`/`override` click produced only 1–2 absolute 0/1
   labels, so it took ~75–150 reviewed movies to clear `HEAD_MIN_LABELS=150`.
   Meanwhile every run archives the full normalized feature vector of *every*
   candidate — ~28 per movie — and all but the 1–2 clicked were discarded.
2. **Cross-movie scale confound.** Pooling absolute 0/1 labels across movies
   meant a "0" auto-pick from a strong movie could out-score a "1" pick from a
   weak one. The real task is *relative ordering within one movie*.

This feature lets the user express a richer preference per movie and trains the
head on **within-movie preference pairs** instead.

## The bucket scheme

On the run-results page the user sorts a movie's candidates (whole design
**stacks** by default) into three buckets:

- **Favorites** — an *ordered* list of tiers; ties allowed (tier 1 = best).
- **Hate** — an *unordered* set.
- **Indifferent** — everything left untouched (derived server-side).

This is a partial order: `fav.tier₁ ≻ … ≻ fav.tierₖ ≻ Indifferent ≻ Hate`.

## Two-channel routing

One submission feeds both learning channels with correct semantics:

| Bucket | k-NN exemplar store (Channel 1) | Pairwise head (Channel 2) |
|---|---|---|
| Favorites | tier-1 → **positive** exemplars (`_add_to_profile`) | top of the order (all tiers) |
| Hate | high-ranked (`pipeline_rank ≤ FEEDBACK_HARD_NEGATIVE_RANK_MAX`) → **negative** exemplars (hard-negative mining) | bottom of the order |
| Indifferent | — (never an exemplar) | middle tier only |

**Hard-negative mining:** a hated poster the pipeline ranked *high* is a
confusable, boundary-defining negative (the model was confident and wrong) —
worth far more than an easy negative the model already rejected. Only those
become negative exemplars. Indifferent posters are neutral, not disliked, so
they never enter the negative set.

## v3 ranking event

One self-contained append-only JSONL row per reviewed movie (written by
[feedback_store.append_labels](../marquee/ml/feedback_store.py); raw order
stored, pairs derived at train time — same philosophy as v2 embedding features):

```jsonc
{ "v": 3, "type": "ranking", "event_id": "…", "ts": "…",
  "run_id": "…", "movie_id": 123, "tmdb_id": …, "title": …, "year": …,
  "model_name": "clip-vit-b-32", "scorer_name": "learned", "gate_snapshot": {…},
  "favorites": [["A.jpg","B.jpg"], ["C.jpg"]],   // ordered tiers; ties share a sublist
  "hated": ["X.jpg"],
  "favorites_exemplars": ["Movie (2011).jpg"],   // training_data names (undo)
  "negatives_added": ["X.jpg"],                  // negative-dir names (undo)
  "candidates": [
    { "orig_filename":"A.jpg","bucket":"fav","tier":1,"pipeline_rank":3,
      "rejection_reason": null, "normalized_features": { … } }, … ] }
```

## Pairwise training (RankNet, linear)

`build_pairwise_training_data` ([marquee/ml/head_trainer.py](../marquee/ml/head_trainer.py))
expands each event's partial order into `(x⁺ − x⁻, weight)` rows — one per
cross-group/cross-tier pair, **none within a tier** (ties = no constraint):

```
weight = movie_norm · confidence
  movie_norm = 1 / (pairs in that movie)   # large slates can't dominate
  confidence = 1.0 explicit (between fav tiers, fav↔hate)
             = FEEDBACK_INDIFF_HATE_PAIR_WEIGHT for pairs touching indifferent
```

`LogisticHead.train_pairwise` minimizes the weighted RankNet loss
`−Σ weight·log σ(w·d)` by full-batch gradient descent (reusing the existing
numpy logistic machinery). **No bias** is learned — a bias shifts all scores
equally and cannot change a within-movie ranking. The artifact format and the
inference scorer (`LearnedScorer` / `LogisticHead.score`) are **unchanged**, so
the learned weights remain a readable taste read-out and Stage-6 ranking is
untouched.

`HEAD_TRAIN_MODE=pairwise` (default) trains on **v3 events only**. Legacy v1/v2
labels still work under `HEAD_TRAIN_MODE=pointwise`.

## API

`POST /api/feedback` gains `action="rank"`:

```jsonc
{ "run_id": "…", "action": "rank",
  "favorites": [["A.jpg","B.jpg"], ["C.jpg"]], "hated": ["X.jpg"], "deploy": true }
```

Response adds `favorites_exemplars` + `negatives_added`. Deploys the tier-1
favorite. `POST /api/feedback/undo` removes the event's labels, positive
exemplars, **and** hard-negative files (response: `negatives_removed`).

## Config knobs ([marquee/core/pipeline_config.py](../marquee/core/pipeline_config.py))

| Knob | Default | Meaning |
|---|---|---|
| `HEAD_TRAIN_MODE` | `pairwise` | `pairwise` (v3 events) or `pointwise` (legacy) |
| `HEAD_MIN_PAIRS` | `200` | min derived pairs to activate (with `HEAD_MIN_MOVIES`) |
| `FEEDBACK_INDIFF_HATE_PAIR_WEIGHT` | `0.3` | down-weight for implicit pairs |
| `FEEDBACK_HARD_NEGATIVE_RANK_MAX` | `10` | hated rank ≤ this → negative exemplar |

## Frontend

- `PosterRankingPanel.svelte` — bucket UI (per-card ♥/–/✕ segmented control,
  favorite-tier stepper), builds the `favorites`/`hated` payload, submits
  `action="rank"`. Whole design stacks move together.
- The run-results page (`.../runs/[run_id]/+page.svelte`) adds a **Rank by taste**
  toggle that swaps the stage-tabs/grid for the panel; on submit it reloads and
  enables Undo. The existing approve/override/reject flow is unchanged.

## Activation note

Because pairwise trains on v3 only, the learned head stays dormant (auto-falls
back to the weighted scorer — no regression) until enough v3 ranking events
clear `HEAD_MIN_PAIRS` / `HEAD_MIN_MOVIES`.

## Tests

- `tests/test_pairwise_head.py` — pair/tie expansion, per-movie + confidence
  weighting, feature intersection, ordering recovery.
- `tests/test_feedback.py` — `rank` event write, hard-negative rank cutoff, undo
  round-trip (labels + exemplars + negatives), validation, v3 summary counts.

## Future (Phase 2)

A tree-based ranker (LambdaMART/LightGBM `lambdarank`) over the same v3 events
captures feature interactions once data is plentiful — see
`design/learning-roadmap.md`. Drag-and-drop reordering is a UI polish over the
current click-to-assign controls.
