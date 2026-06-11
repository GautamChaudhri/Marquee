# Marquee — Multi-Platform Deployment & Performance

How the pipeline runs on every homelab hardware tier — from an Intel N150
mini-PC to a dedicated RTX 3070 — with one codebase, one config knob, and
automatic fallthrough. Supersedes the platform notes in
`05-linux-deployment.md` (which remains the theforge-specific runbook).

---

## 1. The hardware module

`marquee/ml/hardware.py` is the single place that decides how ONNX models
execute. Every session (CLIP, face detector) is created through it, so all
models land on the same device with the same fallback behaviour.

### Resolution order (`EXECUTION_PROVIDER=auto`)

| Priority | Provider | Hardware | Tier name |
|---|---|---|---|
| 1 | CUDAExecutionProvider | NVIDIA GPU (RTX 3060/3070+) | `cuda` |
| 2 | OpenVINOExecutionProvider (GPU device) | Intel iGPU (UHD) / Arc dGPU (A310+) | `openvino-gpu` |
| 2b | OpenVINOExecutionProvider (CPU device) | Intel CPU without usable GPU | `openvino-cpu` |
| 3 | CoreMLExecutionProvider | Apple Silicon (M-series) | `coreml` |
| 4 | CPUExecutionProvider | anything (Intel N150, ...) | `cpu` |

Unavailable providers are skipped silently; an *explicitly requested*
provider that is unavailable raises loudly. The OpenVINO GPU/CPU split is
auto-detected by querying OpenVINO's device list; `EXECUTION_PROVIDER=openvino-cpu`
forces the CPU device when the iGPU is busy with Plex/QSV transcodes.

Friendly aliases: `cuda`/`nvidia`, `openvino`/`intel`, `openvino-cpu`,
`coreml`/`apple`, `cpu`, `tensorrt` (explicit only — engine builds are slow).

### CUDA without LD_LIBRARY_PATH

`onnxruntime-gpu` does not bundle CUDA. The hardware module preloads
`libcudart/libcublasLt/libcudnn/...` from pip-installed `nvidia-*-cu12`
packages (RTLD_GLOBAL) before creating a CUDA session, so a plain
`pip install` works with no environment surgery. Previously this only worked
by accident because the aesthetic head imported torch first.

### Tier performance defaults

Resolved automatically; any explicit config value wins.

| Tier | CLIP batch | OCR workers | OMP threads/worker |
|---|---|---|---|
| cuda | 32 | min(3, cores/4) | cores/workers |
| openvino-gpu | 16 | min(6, cores/2) | cores/workers |
| coreml | 16 | min(6, cores/2) | cores/workers |
| openvino-cpu / cpu | 8 | min(6, cores/2) | cores/workers |

`OCR_WORKERS=0` (default) means auto. The OMP budget is exported into each
PaddleOCR worker before paddle loads, so workers × threads ≈ cores — a fixed
worker count used to thrash a 4-core N150 and underuse a 13600KF.

---

## 2. Install matrix (bare metal)

The three Linux onnxruntime packages install the same module and conflict —
install **exactly one** accelerator extra:

| Platform | Install | Notes |
|---|---|---|
| NVIDIA GPU | `pip install -e ".[nvidia,ml]"` | optional: paddlepaddle-gpu for GPU OCR |
| Intel iGPU / Arc | `pip install -e ".[intel,ml]"` | needs `intel-opencl-icd` on the host |
| CPU-only (N150...) | `pip install -e ".[cpu,ml]"` | consider the INT8 model (§4) |
| Apple Silicon | `pip install -e ".[ml]"` | base deps include CoreML-capable onnxruntime |

`[all]` = dev + ml, deliberately without an accelerator extra so it never
clobbers an existing onnxruntime variant.

### Model artifacts per host

- `marquee/ml/models/clip-vit-b-32.onnx` — export with
  `python -m marquee.ml.clip_export` (dynamic batch axis; re-export old
  fixed-batch models to enable batching — old exports still work, loop mode).
- `marquee/ml/models/sa_0_4_vit_b_32_linear.npz` — numpy aesthetic head.
  Auto-converted from the `.pth` on first load when torch is present, or run
  `python -m marquee.ml.aesthetic` once. **Torch is no longer needed at
  runtime** — only for model export.
- `marquee/ml/models/scrfd_500m_bnkps.onnx` — face detector.
- `marquee/ml/taste_profile.<model>.npz` — `python -m marquee.ml.taste_trainer`.

All artifacts are platform-agnostic; build once, copy everywhere — except the
taste profile + embedding cache, which are **model-specific** (design 04 §12).

---

## 3. Docker

`docker/Dockerfile` (ARG `MARQUEE_HW=cpu|nvidia|intel`) and
`docker/docker-compose.yml` with one profile per tier:

```bash
docker compose -f docker/docker-compose.yml --profile cpu    up -d   # N150 etc.
docker compose -f docker/docker-compose.yml --profile nvidia up -d   # NVIDIA toolkit
docker compose -f docker/docker-compose.yml --profile intel  up -d   # /dev/dri passthrough
```

Same `.env` across all profiles; `EXECUTION_PROVIDER=auto` resolves inside
the container. Model files are volume-mounted, not baked in. Apple Silicon is
a bare-metal target (CoreML is unavailable inside Linux containers).

---

## 4. Per-tier performance playbook

**Every tier** benefits from the pipeline-order change (see design 04 §2):
the resolution gate fires from TMDB metadata before any inference, and the
CLIP-based style gates fire *before* OCR, so the expensive multi-pass OCR
only sees plausible candidates. CLIP runs as one batched ONNX call;
embeddings are cached on disk keyed by model name, so re-runs skip the GPU
entirely.

- **NVIDIA (cuda)**: CLIP ~5 ms/poster batched. OCR is the bottleneck —
  install the version-matched `paddlepaddle-gpu` wheel to move it to the GPU,
  or leave it on a strong CPU. `OCR_WORKERS` from `.env` overrides the cap.
- **Intel Arc / iGPU (openvino-*)**: CLIP + face on the GPU via OpenVINO,
  PaddleOCR stays on CPU. On an iGPU shared with Plex transcoding, use
  `EXECUTION_PROVIDER=openvino-cpu` during heavy transcode windows.
- **Apple Silicon (coreml)**: works out of the box; OCR on CPU
  (auto-sized workers).
- **CPU-only / N150 (cpu)**:
  1. `OCR_DETAIL_PASSES=false` — skips the top-strip and 2× bottom-strip OCR
     passes (~60% OCR time saved; slight risk of missing faint credit text).
  2. INT8 CLIP: `python -m marquee.ml.clip_export --quantize`, then
     `AI_MODEL=clip-vit-b-32-int8` and rebuild the taste profile. ~2-3×
     faster CLIP, ~4× smaller model. The artifact paths follow `AI_MODEL`
     automatically; the model-name guard prevents mixing embedding spaces.
  3. Auto worker sizing already prevents the old 5-worker thrash.

---

## 5. What changed for quality (false positives / taste)

Documented here for cross-reference; authoritative spec in design 04:

1. **Text-heavy gate is threshold-based** (`OCR_MAX_RESIDUAL_BOXES`,
   `OCR_MAX_RESIDUAL_AREA_FRACTION`). The default `OCR_MAX_RESIDUAL_BOXES=0`
   keeps the strict title-only-text project target (any significant non-title
   box rejects); raising it to 1-2 later demotes taglines to a
   `text_residual` rank penalty without a code change.
2. **No-title posters get neutral title_colorfulness** (0.5 normalized)
   instead of 0 — stylized titles the OCR cannot read are no longer punished
   as if they had plain white text.
3. **Softmax-weighted k-NN** (`KNN_WEIGHTING=softmax`): the closest taste
   exemplars dominate the style score, sharpening multimodal taste clusters.
4. **Negative exemplars**: `marquee/experiments/negative_data/` + retrain.
   Candidates closer to the disliked set than the liked set are penalized
   (`TASTE_NEG_WEIGHT`). No global score shift; gate thresholds stay valid.
