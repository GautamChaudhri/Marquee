# Marquee — Multi-Platform Deployment and Performance

**Status:** Reconciled with the codebase on 2026-06-16.

`marquee/ml/hardware.py` is the source of truth for ONNX Runtime provider
selection and tier-derived defaults.

## Provider Resolution

With `EXECUTION_PROVIDER=auto`, Marquee chooses the best available provider and
falls back safely.

| Priority | Provider/tier | Target |
|---|---|---|
| 1 | `CUDAExecutionProvider` / `cuda` | NVIDIA GPU |
| 2 | `OpenVINOExecutionProvider` / `openvino-gpu` | Intel iGPU or Arc GPU |
| 3 | `OpenVINOExecutionProvider` / `openvino-cpu` | Intel CPU with OpenVINO |
| 4 | `CoreMLExecutionProvider` / `coreml` | Apple Silicon bare metal |
| 5 | `CPUExecutionProvider` / `cpu` | Universal fallback |

Explicit provider requests are validated more strictly than auto mode.

Friendly aliases include:

- `cuda`, `nvidia`
- `openvino`, `intel`, `openvino-cpu`
- `coreml`, `apple`
- `cpu`
- `tensorrt`

## Auto Defaults

`CLIP_BATCH_SIZE=0` and `OCR_WORKERS=0` mean auto-size by hardware tier.
`effective_clip_batch_size()`, `effective_ocr_workers()`, and
`effective_ocr_omp_threads()` compute the runtime values.

## Bare-Metal Install Matrix

Install exactly one accelerator extra on Linux:

| Target | Install |
|---|---|
| CPU | `pip install -e ".[cpu,ml]"` |
| NVIDIA | `pip install -e ".[nvidia,ml]"` |
| Intel/OpenVINO | `pip install -e ".[intel,ml]"` |
| Apple Silicon | `pip install -e ".[ml]"` |

The ONNX Runtime packages conflict with one another because they install the
same `onnxruntime` module.

## Docker Profiles

Implemented Docker profiles:

```bash
docker compose -f docker/docker-compose.yml --profile cpu up -d
docker compose -f docker/docker-compose.yml --profile nvidia up -d
docker compose -f docker/docker-compose.yml --profile intel up -d
```

The Dockerfile uses `ARG MARQUEE_HW=cpu|nvidia|intel`. Apple CoreML is a
bare-metal target, not a Linux-container target.

## Model Artifacts

Default artifacts are platform-independent:

- `marquee/ml/models/clip-vit-b-32.onnx`
- `marquee/ml/models/sa_0_4_vit_b_32_linear.npz` or `.pth`
- `marquee/ml/models/scrfd_500m_bnkps.onnx`
- `marquee/ml/taste_profile.<AI_MODEL>.npz`

Optional artifacts:

- `marquee/ml/models/dinov2-vits14.onnx`
- `marquee/ml/models/yolo11n.onnx`
- `marquee/ml/zeroshot_axes.clip-vit-b-32.npz`
- `marquee/ml/models/learned_head.clip-vit-b-32.npz`

Embedding caches and taste profiles are model-specific. Changing `AI_MODEL`
requires rebuilding the profile and cache.

## Performance Levers

- Pipeline ordering avoids expensive OCR until after metadata/style gates.
- CLIP embeddings are batched and cached on disk.
- `OCR_DETAIL_PASSES=false` can reduce CPU OCR time on weak hosts at the cost
  of missing some small/faint text.
- INT8 CLIP can be exported with `python -m marquee.ml.clip_export --quantize`;
  use a distinct `AI_MODEL` and rebuild the taste profile.
- DINO is auto-enabled on GPU tiers and disabled on CPU tiers unless forced.

## Host Validation Rules

- Do not quote fixed millisecond/poster numbers without a fresh benchmark on
  the current target hardware.
- NVIDIA Docker profiles require a working host NVIDIA driver and NVIDIA
  Container Toolkit.
- Intel Docker profiles require `/dev/dri` plus working host OpenCL/iGPU
  access.
- OCR should use the strongest working provider on the host: prefer GPU when
  available and stable, then fall back to CPU if GPU setup is unavailable or
  fails.
