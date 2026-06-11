# Marquee — Agent Build Instructions

Instructions for a coding agent (Claude Code / Codex) to implement the revised poster pipeline.
Read `04-revised-pipeline-design.md` first for the *why*; this document is the *how*. Implement to
this spec. Where this document and the older `04-*`/`05-*` docs disagree, **this document wins**.

---

## 0. Scope — build this, not more

**Build now (Phase 0), inside the existing test endpoint:**

- Stage 0 prerequisites: CLIP B/32 ONNX export, LAION B/32 aesthetic head, face detector, and the
  rebuilt taste/exemplar store.
- The new pipeline guts replacing the old middle stages: dedup → OCR (gate + feature emit) →
  feature extraction → hard gate → hand-weighted ranking → output.
- The expanded score record, the GATE/RANK split, detailed logging, and the renamed output with
  original-resolution re-download.

**Do NOT build (mark as TODO stubs / comments):**

- The approval / override / feedback capture loop.
- The learned head (logistic / LightGBM) and any training or retraining job.
- Deploy-to-media-library and DB writes for production.
- TVDB / TV-show support (the test endpoint is movies-only).

The scorer must be built as a **pluggable interface** so the learned head drops in later without
touching the pipeline. The taste store must be the existing `TasteStore` ABC so a Chroma backend
can be swapped later. Do not add ChromaDB now — use the numpy backend.

---

## 1. The existing test endpoint — keep, replace, why

The endpoint is `POST /api/test/pipeline/movie/{movie_id}` (see `05-test-endpoint-design.md`).

### Keep as-is — Stage 1 (Fetch/Download)

The fetch machinery is reused unchanged: DB lookup → `out_dir` creation under
`_EXPERIMENTS_DATA` → `tmdb.get_movie_images()` → concurrent w500 download (Semaphore 5) →
`candidate_map: dict[filename → PosterCandidate]` → `all_files: list[Path]`.

`PosterCandidate` already carries `width`, `height`, `vote_average`, `vote_count`, `language`, and
`.url(size)`. **These supply resolution, all three provenance signals, and language-match with no
new plumbing.** Do not change the fetch stage.

### Keep with modification — Stage 5 (Sort/Place/Re-download)

Three required changes:

1. **Expand `CandidateScore`** (see §5) to carry the full feature vector, gate decision, score
   breakdown, rank, and the preserved original TMDB basename.
2. **Change the rename** from `1.jpg`–`5.jpg` to `{rank}__{score:.4f}__{orig_basename}.jpg`.
3. **Fix the re-download lookup.** The original-resolution re-download currently does
   `candidate_map[s.image_path.name]`. After renaming, `image_path.name` is no longer the TMDB
   basename. Thread the original basename through as an explicit `CandidateScore.orig_filename`
   field and look up `candidate_map[score.orig_filename]`. **This is the one place the rename can
   silently break Stage 5 — get it right.**

### Replace — the guts (old Stages 2–4)

Old middle: SHA → OCR → pHash → `0.8·emb + 0.2·color` with CLIP negative prompts. Replaced by the
new middle in §4. **Why the replacement:** the old funnel threw everything away and ended on one
brittle similarity score. The new design separates a hard GATE (the only thing that removes
posters) from a soft within-movie RANK (everything past the gate is kept and ordered). This is
what the user means by "ranking heavily, not throwing stuff away except gating." Concretely:
floating-head handling moves off CLIP negative prompts (deleted) onto the `face_area` scalar as a
ranking penalty, so floating-head-only movies surface their best poster instead of falling back to
junk.

### Directory layout under the new design

The reject folders use a flat, sequentially-numbered structure at the movie root so you can
see the pipeline order just by listing the directory:

```
experiments/runs/<Movie>/
├── 0-originals/                    all w500 downloads land here
├── 1-sha256-rejected/              SHA-256 exact-duplicate rejects
├── 2-ocr-rejected/                 OCR text-filter rejects
├── 3-phash-rejected/               pHash near-duplicate rejects (on OCR survivors)
├── errored/                   ★    per-candidate failures
├── gated/                     ★    hard-gate rejects, reason in FILENAME + log+json
├── ranked/                    ★★   ALL ranked posters, renamed, original-res on top-5
├── pipeline.log                    human-readable run log
└── pipeline_run.json               machine-readable per-candidate record
```

`0-originals/` survives re-runs (Stage 1 skips existing files). All other directories are
regenerated. Rejected files are named `{reason}__{orig_basename}.jpg` (e.g.
`aesthetic_floor__gnb54....jpg`). Ranked files use `{rank}__{score:.4f}__{orig_basename}.jpg`.

---

## 2. Module layout

Extend the existing layout. New/changed modules:

```
marquee/
├── pipeline/
│   ├── deduper.py            Stage 2 (SHA + pHash) — keep
│   ├── ocr_filter.py         Stage 3 — MODIFY: emit title bbox + residual boxes
│   ├── features.py     NEW   Stage 4 — compute the scalar vector
│   ├── gate.py         NEW   Stage 5 — hard floors
│   ├── scorer.py             Stage 6 — REPLACE: pluggable head, Phase-0 weights
│   └── output.py             Stage 7 — MODIFY: rename + original re-download
├── ml/
│   ├── clip_export.py        export openai/clip-vit-base-patch32 → ONNX
│   ├── embedding.py          CLIP B/32 inference, EP-configurable
│   ├── aesthetic.py    NEW   LAION B/32 linear head
│   ├── face.py         NEW   face detector inference
│   ├── colorfulness.py NEW   Hasler–Süsstrunk on title crop
│   ├── taste_store.py        TasteStore ABC + NumpyTasteStore (k-NN)
│   ├── taste_trainer.py      MODIFY: B/32, drop LAB, store all embeddings
│   └── normalize.py    NEW   raw → 0–1 per feature
└── core/
    └── pipeline_config.py NEW  all weights/thresholds/ranges in one place
```

---

## 3. Stage 0 — prerequisites

### 3a. CLIP export

Export `openai/clip-vit-base-patch32` to ONNX (opset 14, `do_constant_folding=True`, input
`(1,3,224,224)` → output `(1,512)`). Verify with ONNX Runtime. Output:
`marquee/ml/models/clip-vit-b-32.onnx`. **Do not reuse the existing B/16 export** — it is the wrong
backbone for the aesthetic head.

`embedding.py` selects the execution provider with auto-detection and fallback, order:
`OpenVINOExecutionProvider` → `CoreMLExecutionProvider` → `CPUExecutionProvider`, overridable via
config. **The selection must skip providers that are not available on the current machine and fall
through to the next — never raise.** Current development is on an M3 Pro Mac (36 GB RAM): OpenVINO
is not present on macOS, so selection falls through to CoreML, which is the provider that should
activate here. Nothing needs to be installed for OpenVINO until the homelab deployment; size all
model artifacts to run on the Mac for now. Preprocess: RGB, resize 224×224 (bicubic/LANCZOS),
normalize with CLIP mean `[0.48145466, 0.4578275, 0.40821073]` / std
`[0.26862954, 0.26130258, 0.27577711]`. L2-normalize the output.

### 3b. Aesthetic head

Download the LAION v1 linear head for B/32 (`sa_0_4_vit_b_32_linear.pth`, shape `Linear(512,1)`).
`aesthetic.py` applies it to the CLIP B/32 embedding, replicating the reference preprocessing
exactly (the LAION code L2-normalizes the embedding before the linear layer). Output ~1–10. Verify
on a couple of known good and bad images that scores are sane before wiring it in. **This head only
works on B/32 embeddings — if the backbone changes, this must change too.**

### 3c. Face detector

Source a lightweight ONNX face detector (SCRFD or RetinaFace; OpenCV's res10 SSD is an acceptable
fallback). `face.py` returns face boxes; `face_area` = Σ(box area) / image area, clamped to 1.0.
Run on the w500 image. Config key for the model path so it is swappable.

### 3d. Taste store (rebuild)

Modify `taste_trainer.py`:

- Embed all `experiments/data/training_data/` posters with **CLIP B/32**, L2-normalized.
- **Drop** LAB color histograms and `centroid_color` entirely.
- Save `taste_profile.clip-vit-b-32.npz` with keys: `embeddings (N,512)`, `poster_names (N,)`,
  `centroid_emb (512,)` (diagnostics only), `model_name="clip-vit-b-32"`.
- Keep the diagnostics print (mean/std cosine to centroid, top-5 / bottom-5). Add: mean
  top-k-neighbor similarity across the set, as a cohesion read for the k-NN scorer.

`NumpyTasteStore.query_similar(embedding, k)` returns the top-k cosine similarities. On load,
**assert `model_name` matches the configured model**; raise a loud error on mismatch (see §11).

---

## 4. The new guts, stage by stage

### Stage 2 — Dedup (keep)

SHA-256 exact then pHash (`imagehash`, Hamming ≤ `DEDUP_PHASH_THRESHOLD`, default 6). Among
dupes/near-dupes keep the highest resolution. Log each removal with the file it duplicates and the
hashes.

### Stage 3 — OCR (modify to emit features)

Keep the proven 3-pass PaddleOCR algorithm and the text-filtering logic. **Change the return type**
so that for every poster it produces, in addition to the accept/reject gate decision:

- `title_bbox`: the OCR detection box whose recognized text best matches the title tokens (reuse
  the existing title-token matching). `None` if no title text found.
- `residual_boxes`: detected text boxes remaining after removing title/director tokens, with their
  areas.

Accept/reject (the gate) is unchanged: text-heavy or no-text → reject to `ocr_rejected/` with the
detected text logged as the reason. Survivors carry their `title_bbox` and `residual_boxes` forward.

### Stage 4 — Feature extraction (`features.py`)

For each OCR survivor, compute the raw scalars (this is where the CLIP embedding runs — last, on
the fewest images, and cache it keyed by orig filename + model name):

| Scalar | Computation |
|---|---|
| `knn_sim` | mean of `taste_store.query_similar(emb, k=K_NEIGHBORS)` |
| `aesthetic` | `aesthetic.py(emb)` |
| `title_colorfulness` | Hasler–Süsstrunk on the `title_bbox` crop; 0 if no title box |
| `text_residual` | raw clutter blend of residual box count and area (formula below) | 0–1 |
| `resolution` | `width*height` from `PosterCandidate` (original dims), in megapixels |
| `sharpness` | variance of `cv2.Laplacian(gray_w500, CV_64F)` |
| `face_area` | from `face.py` |
| `provenance` | shrinkage average of vote_average/vote_count (see below), normalized |
| `lang_match` | `language == PREFERRED_LANG → 1.0`; `None → 0.5`; else `0.2` |

**Hasler–Süsstrunk colorfulness** on a crop:
```
rg = R - G
yb = 0.5*(R + G) - B
colorfulness = sqrt(std(rg)^2 + std(yb)^2) + 0.3*sqrt(mean(rg)^2 + mean(yb)^2)
```

**Raw `text_residual` blend** (the constants are config values, not literals):
```
raw_text_residual = W_COUNT * min(box_count / RESIDUAL_COUNT_SAT, 1) + W_AREA * area_fraction
```
Defaults: `RESIDUAL_COUNT_SAT = 5`, `W_COUNT = 0.5`, `W_AREA = 0.5`, where `area_fraction` =
total residual-text-box area / image area. The blend caps at 1, so normalization is simply
`1 - raw` (see §6). `box_count` and `area_fraction` are 0 for a clean title-only poster.

**Provenance shrinkage:** `adjusted = (v/(v+m))*R + (m/(v+m))*C`, with `R=vote_average`,
`v=vote_count`, `C=PROV_PRIOR_MEAN` (default 6.5), `m=PROV_CONFIDENCE` (default 25). Normalize
`adjusted/10` to 0–1. Keep this per-source (TMDB only now; TVDB will need its own constants).

Store both raw and normalized values on the record (normalization in §6).

### Stage 5 — Gate (`gate.py`)

Hard rejects, evaluated on **raw** values against fixed thresholds:

| Gate | Condition (reject if) | Default |
|---|---|---|
| resolution floor | `width < GATE_MIN_WIDTH` | 500 |
| aesthetic floor | `aesthetic < GATE_MIN_AESTHETIC` | 4.5 |
| off-style floor | `knn_sim < GATE_MIN_KNN_SIM` | 0.45 |
| fan-junk combo (OFF by default) | `aesthetic < a AND provenance < p AND resolution < r` | disabled |

Gated-out → `gated/` named `{reason}__{orig}.jpg`, reason recorded in log + json. Keep the
off-style floor **conservative** so it rarely fires; over-gating reintroduces fallback-to-junk.

**Fallback:** if the gate removes every candidate for the movie, do not deploy anything — set the
run status to `flagged_manual` and record it in the log + json.

### Stage 6 — Rank (`scorer.py`)

Pluggable interface:
```python
class PosterScorer(ABC):
    def score(self, feat: FeatureVector) -> tuple[float, dict[str, float]]:
        """Returns (final_score_0_to_1, per_feature_contributions)."""
```
Phase-0 implementation `WeightedScorer`: `final_score = Σ wᵢ · normalizedᵢ`. Return the
per-feature contribution dict for logging.

**One scoring convention — every feature oriented so higher = better, every weight positive. No
mixed signs.** The two "penalty" features are inverted at normalization (§6) so they point the
same way as everything else: `text_residual` becomes *cleanliness* (`1 - raw`) and `face_area`
becomes *face-absence* (`1 - face_area`). Do not implement either as identity-plus-negative-weight
— two conventions in one scorer is what caused the original sign bug.

Default weights (all in config, all tunable from the contribution logs):

| Feature | Weight | Orientation |
|---|---|---|
| `knn_sim` | 0.30 | higher = more on-style |
| `aesthetic` | 0.20 | higher = better quality |
| `title_colorfulness` | 0.15 | higher = more colored/stylized title |
| `face_area` | 0.15 | normalized as face-**absence** (`1 - face_area`); higher = fewer faces |
| `text_residual` | 0.10 | normalized as **cleanliness** (`1 - raw`); higher = less clutter |
| `provenance` | 0.07 | higher = more official/popular |
| `sharpness` | 0.03 | higher = sharper |

**Score range is free under this convention.** Every normalized feature is in [0,1] and the active
weights sum to 1.0 (0.30 + 0.20 + 0.15 + 0.15 + 0.10 + 0.07 + 0.03 = 1.00), so `final_score` is
already in [0,1]. **No clamp/scale step, no theoretical-min/max mapping.** Guard: if any weight is
changed, or if `resolution`/`lang_match` are given nonzero weights, divide the sum by the total of
the active weights so the score stays in [0,1].

(`resolution` and `lang_match` act mainly via the gate / a small tiebreak; expose weights for them
too, default ~0.) Rank survivors by `final_score` descending; all ranked posters go to `ranked/`.

### Stage 7 — Output (`output.py`, modified Stage 5)

All ranked posters are copied to `ranked/` as `{rank}__{score:.4f}__{orig_basename}.jpg`.
For the top-5: re-download at `"original"` resolution via
`candidate_map[score.orig_filename].url("original")`, overwriting.

---

## 5. The expanded score record

```python
@dataclass
class FeatureVector:
    knn_sim: float; aesthetic: float; title_colorfulness: float
    text_residual: float; resolution: float; sharpness: float
    face_area: float; provenance: float; lang_match: float
    normalized: dict[str, float]          # feature → 0–1

@dataclass
class CandidateScore:
    image_path: Path                      # current file (may be renamed)
    orig_filename: str                    # TMDB basename — for re-download lookup
    features: FeatureVector | None        # None for pre-feature rejects (dedup/ocr/errored)
    final_score: float | None             # 0–1; None if it never reached ranking
    contributions: dict[str, float] | None
    gate_decision: str                    # "passed" | "gated" | "rejected" | "errored"
    gate_reason: str | None               # e.g. "aesthetic_floor"
    error: str | None                     # "ocr_error" | "feature_error", else None
    rank: int | None                      # 1-based among passed; None otherwise
    original_download: bool | None        # top-5 only: True if original-res fetch succeeded
    stage_reached: str                    # "dedup"|"ocr"|"features"|"gate"|"ranked"|"errored"
```

Drop the old `emb_similarity`, `color_similarity`, `neg_sim_max` fields. **Feature fields are
nullable:** any candidate whose `stage_reached` is earlier than `features` (dedup dupes, OCR
rejects, per-candidate errors) carries `features = None` and `final_score = None`, with its
`gate_reason`/`error` and `stage_reached` populated. Every candidate still gets a record (§7).

---

## 6. Normalization (`normalize.py`)

Phase 0 uses **fixed, documented ranges** (min-max with clipping), not data-fit params — robust,
interpretable, debuggable, and avoids a chicken-and-egg on a fresh build. Gates use raw values;
only the ranking scorer consumes normalized values.

| Feature | Map | Clip |
|---|---|---|
| `knn_sim` | `(x-0.4)/(0.9-0.4)` | [0,1] |
| `aesthetic` | `x/10` | [0,1] |
| `title_colorfulness` | `x/60` | [0,1] |
| `text_residual` | `1 - raw` → *cleanliness* (raw already 0–1, see §4 blend; higher = less text) | [0,1] |
| `resolution` | `x_megapixels/6` | [0,1] |
| `sharpness` | `log1p(x)/log1p(2000)` | [0,1] |
| `face_area` | `1 - face_area` → *face-absence* (higher = fewer faces) | [0,1] |
| `provenance` | identity (already 0–1) | [0,1] |
| `lang_match` | identity | [0,1] |

All bounds in `pipeline_config.py`. (Phase 1 trees won't need normalization; logistic will
standardize from accumulated data — out of scope now.)

---

## 7. Logging — first-class requirement

The user debugs by inspecting ranked posters and then reading the log to see *why* a poster landed
where it did. Two artifacts per movie in `out_dir`:

### `pipeline.log` (human-readable)

- **Run header:** movie title, tmdb_id, timestamp, and a snapshot of the active config (model
  name, K_NEIGHBORS, all weights, all gate thresholds).
- **Per stage:** a start line, then per-candidate detail, then an end line with **count of
  survivors and elapsed seconds** (use `time.perf_counter()` per stage).
  - Dedup: each removal, what it duplicated, hashes.
  - OCR: each poster — accepted/rejected, the detected text, and the reason.
  - Features: each survivor — every raw scalar value.
  - Gate: each survivor — passed, or which gate failed with the value-vs-threshold.
  - Rank: each survivor — a **score-decomposition table**: per feature `raw → normalized →
    ×weight = contribution`, then `final_score` and `rank`. This is the key debugging artifact.
- **Summary:** the top-5 with scores, total runtime, and a per-stage timing breakdown.

Use Python `logging` with a per-run `FileHandler` to `out_dir/pipeline.log` plus a console handler.

### `pipeline_run.json` (machine-readable)

Run metadata (config snapshot, model name, per-stage timings, status) plus an array of every
candidate with: `orig_filename`, all raw features, all normalized features, all contributions,
`gate_decision`, `gate_reason`, `final_score`, `rank`, `stage_reached`. This doubles as the
training dataset for the future learned head, so include every candidate, not just survivors.

---

## 8. Config surface (`pipeline_config.py`)

One module holding every knob, each with the documented default and a one-line comment:
`AI_MODEL` (default `clip-vit-b-32`), `EXECUTION_PROVIDER` (auto), `K_NEIGHBORS` (10 — **note in a
comment: lower = more permissive/multimodal/noisier; higher = smoother/more conservative, blur
returns**), the normalization ranges (§6), the scorer weights (§4 Stage 6), the gate thresholds
(§4 Stage 5), `RESIDUAL_COUNT_SAT` (5), `W_COUNT` (0.5), `W_AREA` (0.5) for the text-residual blend,
`PREFERRED_LANG` (`en`), `PROV_PRIOR_MEAN` (6.5), `PROV_CONFIDENCE` (25),
`DEDUP_PHASH_THRESHOLD` (6), `DEDUP_MIN_POSTER_WIDTH` (500), and the model file paths.

---

## 9. Build order

1. `pipeline_config.py` — all knobs first.
2. Stage 0: `clip_export.py` (B/32), `embedding.py`, `aesthetic.py`, `face.py`, `colorfulness.py`,
   rebuilt `taste_trainer.py` + `taste_store.py`. Verify each in isolation.
3. `normalize.py`.
4. Modify `ocr_filter.py` to emit title bbox + residual boxes.
5. `features.py`, `gate.py`, `scorer.py`.
6. Modify `output.py` (rename + re-download via `orig_filename`).
7. Wire into the test endpoint guts; add the logging.
8. Run on the existing test sets (`2001/`, `dune2/`, `endgame/`), inspect `ranked/` and
   `pipeline.log`, confirm decisions are explainable.

---

## 10. Validation

Run the endpoint on the three test movies. For each, confirm: stages log start/end and timings;
gated posters land in `gated/` with a readable reason; the top-5 in `ranked/` are renamed and
re-downloaded at original resolution; `pipeline_run.json` contains every candidate; and the
score-decomposition in `pipeline.log` makes a wrong pick diagnosable (you can see which feature
over- or under-contributed). The user is the final judge of ranking quality — the deliverable is
that every decision is *inspectable*, not that the ranking is perfect on day one.

---

## 11. Footnote — embeddings are model-specific (mandatory)

A B/32 embedding is not comparable to a B/16, L/14, or DINOv2 embedding even at matching
dimensionality, and the LAION B/32 aesthetic head produces garbage on any other backbone. Rules:

1. Tag every embedding artifact with `model_name` **and** put the model name in the filename
   (`taste_profile.clip-vit-b-32.npz`).
2. Changing the embedding model invalidates the whole store — rebuild it and recompute all cached
   embeddings.
3. The aesthetic head must always match the backbone; swap them together.
4. On load, verify the store's `model_name` equals the configured model and **fail loudly** on
   mismatch rather than scoring against a stale space.

---

## 12. Run robustness — failures, re-runs, re-download

**Per-candidate failures reject only that candidate; systemic failures abort the run.**

- A per-candidate data problem (corrupt image, decode error, an OCR or feature-extraction crash on
  one file) skips just that candidate: set `error = "ocr_error"` or `"feature_error"`,
  `stage_reached = "errored"`, route the file to `errored/` named `{error}__{orig_basename}.jpg`,
  include the record in `pipeline_run.json`, and continue with the remaining candidates.
- An infrastructure failure that affects every candidate — model fails to load, taste store
  missing, or the `model_name` mismatch guard (§11) trips — **aborts the whole run loudly**. Do not
  silently skip every candidate. Per-candidate problems are data; environment problems stop
  everything.

**Re-runs are idempotent.** At the start of a run, delete the generated stage subdirectories
(`1-sha256-rejected/`, `2-ocr-rejected/`, `3-phash-rejected/`, `gated/`, `errored/`, `ranked/`,
and any legacy `sha256/`, `phash/`, `ocr/`, `ranked_lower/` folders from prior runs) and the
previous `pipeline.log` / `pipeline_run.json`, then regenerate. **Do not delete the
`0-originals/` w500 downloads** — Stage 1 already skips existing files, so retaining them avoids
re-fetching from the CDN. A re-run must never mix stale results with fresh ones.

**Original-resolution re-download is best-effort.** The w500 copy in `ranked/` is already a valid
usable poster. If the original-resolution re-download for a top-5 item fails (CDN blip, network
error), keep the w500 copy, log the failure, and set `original_download = false` on that item's
record so it is visible which of the five are full-resolution. A failed re-download never loses the
pick or aborts the run; successful ones set `original_download = true`.
