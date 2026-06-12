# Marquee — Extended Features & Exemplar-Calibrated Scoring

Implements recommendations 1–6 of the scoring-resolution plan (2026-06-11).
Extends `04-revised-pipeline-design.md` (GATE/RANK architecture unchanged);
deployment/tier mechanics in `06-multi-platform-deployment.md`.

The goal: give the RANK stage many more taste axes than the original 9
scalars, and replace "higher = better" ramps with **personalized taste
bands derived from the user's own exemplars**. Nothing here touches GATE —
gating stays validity-only (resolution, aesthetic floor, off-style floor,
strict title-only OCR).

---

## 1. Exemplar-calibrated normalization (the core piece)

`marquee/ml/calibration.py`

Fixed min-max normalization assumes taste is monotonic. It isn't: nobody
wants the *darkest* poster — they want darkness in the range their picks
live in. The taste trainer measures every extended feature on every
positive exemplar and stores the raw distributions in the profile
(`calib_feature_names` / `calib_feature_values`, NaN = unmeasurable on that
exemplar). At runtime a candidate's value is scored by **KDE typicality**:

```
typicality(x) = KDE(x) / max(KDE)          ∈ [0, 1]
```

**Why KDE, not a mean±σ band:** taste is multimodal (the design's founding
observation — 04 §3/§6). A user with a dark-horror cluster and a bright-
animation cluster would get a Gaussian band peaked in the meaningless
middle — centroid blur reborn at the feature level. The KDE rewards *any*
dense exemplar region: both clusters score ~1.0, the middle scores low.
This is covered by an explicit anti-centroid regression test.

Mechanics:
- Bandwidth: Silverman's rule on a robust sigma (`min(std, IQR/1.349)`),
  scaled by `CALIBRATION_BANDWIDTH_SCALE` (>1 = more forgiving bands).
- `max(KDE)` approximated by evaluating at the sample points.
- Features with < `CALIBRATION_MIN_SAMPLES` usable values are skipped
  (logged) and fall back to fixed normalization.
- Unanimous features (zero spread, e.g. every exemplar has face_count 0)
  peak at the unanimous value and decay away from it.
- Negative exemplars do **not** contribute to bands — bands describe what
  you like.

Per-feature typicalities are aggregated (mean) into one scorer feature,
`taste_typicality` (weight 0.10). The set is configurable
(`TYPICALITY_FEATURES`); each member's raw value, typicality, and the band
it was judged against are logged per candidate and serialized to
`pipeline_run.json` (`typicality_detail`).

Master switch: `CALIBRATION_ENABLED` (default true).

## 2. The new feature inventory

### Free features (rec 1 — derived from data already computed)

| Feature | Source |
|---|---|
| `title_height_frac`, `title_y_center`, `title_centeredness` | OCR title bbox |
| `face_count`, `largest_face_frac` | existing SCRFD boxes |
| `axis_illustrated`, `axis_minimalist`, `axis_vintage` | CLIP zero-shot axes (below) |
| `aspect_ratio_deviation` | TMDB metadata |

**Zero-shot axes** (`marquee/ml/zeroshot.py`): unit directions in CLIP space
built from prompt ensembles ("an illustrated movie poster" minus "a
photographic movie poster"), evaluated as one dot product against the
already-computed embedding. Built once with
`python -m marquee.ml.zeroshot` → `zeroshot_axes.clip-vit-b-32.npz`.
The old design dropped CLIP prompts as a hard gate; as calibrated soft
rank features the failure mode (occasional misfire deleting a poster) does
not exist. The exemplar distribution of each axis tells the scorer which
side the user prefers — no manual configuration.

### Classic-CV pack (rec 3 — `marquee/ml/visual_features.py`)

Palette/mood: `darkness`, `mean_saturation`, `hue_entropy`,
`global_colorfulness`, `contrast_rms`. Composition: `negative_space_frac`,
`edge_density`, `visual_entropy`, `symmetry`. All computed on
width-500-standardized images so candidates (w500 downloads) and exemplars
(arbitrary sizes) are measured on the same scale — resolution-dependent
metrics would otherwise corrupt calibration.

### DINOv2 second style opinion (rec 4 — `marquee/ml/dino.py`)

DINOv2-S/14 (ONNX, ~84MB, dynamic batch) embeds survivors; `dino_knn` is
the same contrastive softmax k-NN as `knn_sim` but in DINO space, where
texture/medium (painterly vs airbrushed vs photo composite) is encoded far
better than in CLIP's content-dominated space. Normalization range comes
from the profile itself: p5..p95 of the exemplars' own k-NN similarities
(`dino_self_knn`), i.e. the empirical "belongs in this profile" range.

**Activation is hardware-tiered**: `DINO_ENABLED=auto` (default) → on for
`cuda`/`openvino-gpu`/`coreml`, off for CPU tiers; `on`/`off` force. The
decision and the reason are logged every run (`DINO | ACTIVE | ...`).
Missing model / profile without DINO embeddings / model-name mismatch
disable it for the run with an ERROR log — enhancements degrade loudly,
they don't abort (04 §13 applies to per-candidate and core failures).

Weight: `WEIGHT_DINO_KNN = 0.10`. When inactive its weight is
redistributed (see §4).

### Quality artifacts + person detection (rec 5 — flag: `EXTRA_QUALITY_ENABLED`)

- `blockiness`: JPEG 8×8 block-boundary energy vs in-block energy.
- `noise_sigma`: Immerkær's fast noise estimate.
- Blended into the monotonic `quality_artifacts` scorer feature
  (weight 0.03, normalized to cleanliness). Deliberately **not** in the
  typicality set — artifacts are objectively bad, not a taste band.
- `person_count`, `person_area_frac`: YOLO11n ONNX (~11MB), letterboxed
  640², COCO person class, custom decode + NMS (no ultralytics runtime
  dependency — only needed once for the export). These two **are** in the
  typicality set (ensemble vs solo composition is taste).

BRISQUE was considered and rejected: it requires opencv-contrib (conflicts
with PaddleOCR's opencv pin) plus downloaded model files; the analytic
metrics are model-free, transparent, and equally effective as rank
features.

`EXTRA_QUALITY_ENABLED=false` turns off both artifact metrics and the
person detector in one flag for A/B testing.

### Official key-art family (2026-06-11 — `official_family`)

The single strongest kept-vs-flagged discriminator measured on three labeled
movies (Avengers/Interstellar/Strange Darling): **CLIP cosine to the movie's
TMDB primary poster** (the movie detail's `poster_path`, community-selected
and in practice the official key art). Fan art diverges from the primary
(med ~0.75-0.85); official variants — including clean title-only versions of
a text-heavy primary — cluster at 0.90+. Costs nothing: the primary is
already in the candidate set, its embedding already computed in the style
batch.

- Raw feature on every candidate, ramped through `NORM_OFFICIAL_MIN/MAX`
  (0.60/0.95), weight `WEIGHT_OFFICIAL_FAMILY = 0.12`.
- The primary's filename also joins the pHash preference tuple
  `(title_found, -residual_boxes, is_primary, knn_sim)` — when the official
  primary is in a near-dupe group it wins the group (fixed Interstellar,
  where the clean official primary used to lose its cluster to a fan
  variant on resolution).
- The primary itself is often OCR-rejected (official theatrical one-sheets
  carry billing text) — that is correct under the title-only target; its
  art family still inherits the boost.
- Degrades loudly: primary missing from TMDB, never downloaded, or gated
  before embedding → `OFFICIAL | ... disabled` log, weight redistributed.
- Validated empirically; independently confirmed by a preview learned head
  trained on the 78 reconstructed labels (official_family got the largest
  positive weight; aesthetic was zeroed — LAION's head loves slick fan art,
  hence its scorer weight dropped 0.20 → 0.12, provenance 0.07 → 0.02 since
  TMDB poster votes are almost all zero).

Future authority anchors (designed, not built): Wikipedia film-infobox
poster via imdb_id → Wikidata → enwiki pageimage (free, no key; the infobox
image is virtually always the official theatrical one-sheet) and Fanart.tv
movieposter likes as a weaker prior. Both would extend `official_family` to
`max(cos to any authority image)` rather than introduce new features.

## 3. Profile build (`marquee/ml/taste_trainer.py`)

The trainer now measures everything on the exemplars, matching pipeline
conditions exactly:

- CLIP + DINOv2 embeddings (positives and negatives), batched.
- `dino_self_knn` per exemplar → runtime normalization range.
- CV pack + quality artifacts on width-500-standardized images.
- Face (SCRFD) and person (YOLO) geometry on the same standardized images.
- Zero-shot axes from the CLIP embeddings.
- Aesthetic score per exemplar (personalized quality band).
- **Title geometry via OCR**: each exemplar's title is parsed from its
  filename ("Movie Title (Year).jpg"), the existing single-process OCR path
  finds the title box, fractions are resolution-independent. This is the
  slow part (~0.4s/exemplar); `--skip-ocr` opts out, `--skip-dino` likewise.

Diagnostics print every calibration band (n/median/p10/p90/bandwidth),
negative-separation, and the dino self-knn percentiles.

## 4. Scorer changes (`marquee/pipeline/scorer.py`)

Three new top-level features join the weighted sum: `dino_knn` (0.10),
`taste_typicality` (0.10), `quality_artifacts` (0.03). Core weights are
unchanged; the scorer **renormalizes over the features each candidate
actually has** — a feature is active iff its weight > 0 AND its key exists
in `normalized`. Disabled/missing optional features therefore redistribute
their weight instead of dragging scores down, and CPU-tier rankings stay
in [0,1] without any config edits.

### Learned head (rec 6 — Phase 1)

- `marquee/ml/learned_head.py`: numpy L2-regularized logistic regression
  (no sklearn). Artifact records feature names, embedding model, sample
  count, accuracy, timestamp; all validated on load.
- `marquee/ml/head_trainer.py`: joins
  `experiments/feedback/labels.jsonl` (1 = approved/selected, 0 =
  overridden auto-pick) against the normalized features recorded in every
  `pipeline_run.json` — the run JSONs have been the training dataset all
  along. Trains on the feature set common to all samples; refuses below
  `--min-labels` (default 30) because hand weights beat a head trained on
  scraps.
- `SCORER=auto` (default): learned head if a valid artifact exists, else
  weighted; decision logged at rank time. `weighted`/`learned` force.
  A head that expects features the run didn't compute (e.g. trained with
  dino, run on CPU) fails loudly instead of scoring garbage.

## 5. Logging contract (cross-referencing)

Per run: hardware profile, DINO activation decision + reason, calibration
band table (median/p10/p90/bandwidth per feature), zero-shot axes status,
person/quality status, scorer selection. Per candidate: `STYLE FEATURES`
(knn/aesthetic/axes), `FEATURES` (scorer-level raws), `FEATURES EXTENDED`
(every fine-grained value), `TYPICALITY` (per-feature raw→typicality with
the band context inline), `RANK DETAIL` (raw → normalized → weight →
contribution, with absent optional features explicitly marked).
`pipeline_run.json` carries `extended_features` and `typicality_detail`
per candidate — the future training dataset grows with every run.

## 6. Cost (measured on theforge, RTX 3070, ~50 survivors)

| Addition | GPU cost | N150-class CPU cost |
|---|---|---|
| Free features + axes | ~0 | ~0 |
| CV pack + artifacts | ~0.1s | ~0.5–1s |
| Calibration | ~0 (lookups) | ~0 |
| DINOv2 batch | ~0.3s | off by default (auto) |
| Person detector | ~0.3s | ~3–5s (or EXTRA_QUALITY_ENABLED=false) |

## 7. Knob summary

| Knob | Default | Meaning |
|---|---|---|
| `DINO_ENABLED` | auto | auto/on/off; auto = GPU tiers only |
| `EXTRA_QUALITY_ENABLED` | true | artifacts + person detector |
| `CALIBRATION_ENABLED` | true | exemplar-calibrated typicality |
| `CALIBRATION_BANDWIDTH_SCALE` | 1.0 | taste-band width multiplier |
| `CALIBRATION_MIN_SAMPLES` | 20 | floor before a band activates |
| `TYPICALITY_FEATURES` | (list) | which features vote in taste_typicality |
| `WEIGHT_DINO_KNN / _TASTE_TYPICALITY / _QUALITY_ARTIFACTS` | .12/.12/.03 | new scorer weights |
| `WEIGHT_OFFICIAL_FAMILY` | 0.12 | CLIP cosine to TMDB primary poster |
| `NORM_OFFICIAL_MIN / _MAX` | 0.60 / 0.95 | official_family cosine ramp |
| `SCORER` | auto | auto/weighted/learned |
