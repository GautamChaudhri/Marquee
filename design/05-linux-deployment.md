# Marquee — Linux Deployment Notes

**Status:** Reconciled with the codebase on 2026-06-16.

This is a Linux deployment checklist for the current repository. Host-specific
facts such as GPU model, driver version, media mount paths, and secrets must be
verified on the target machine.

## Supported Install Shapes

### Bare Metal

Use Python 3.12 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
```

Install exactly one ONNX Runtime accelerator extra:

```bash
pip install -e ".[cpu,ml]"      # CPU-only
pip install -e ".[nvidia,ml]"   # NVIDIA/CUDA via onnxruntime-gpu
pip install -e ".[intel,ml]"    # Intel OpenVINO
```

`.[all]` is a dev/ML convenience extra and deliberately does not pick a Linux
accelerator package.

### Docker

The repository includes:

- `docker/Dockerfile`
- `docker/docker-compose.yml`

Profiles:

```bash
docker compose -f docker/docker-compose.yml --profile cpu up -d
docker compose -f docker/docker-compose.yml --profile nvidia up -d
docker compose -f docker/docker-compose.yml --profile intel up -d
```

Model files and `data/` are mounted, not baked into the image. Adjust media
library volume mounts in compose before allowing poster/subtitle/letterbox
writes.

## Runtime Configuration

Required for poster pipeline runs:

- `TMDB_READ_ACCESS_TOKEN`
- `RADARR_URL` / `RADARR_API_KEY` if syncing movies
- `SONARR_URL` / `SONARR_API_KEY` if syncing TV
- `RADARR_PATH_PREFIX` / `RADARR_MEDIA_PATH` when Radarr paths differ from
  local/container paths
- `SONARR_PATH_PREFIX` / `SONARR_MEDIA_PATH` when Sonarr paths differ from
  local/container paths
- `MEDIA_ROOTS` if you want explicit path containment enforcement beyond the
  derived path mappings

For webhooks:

- `WEBHOOK_TOKEN` is optional but recommended.
- Configure Radarr/Sonarr to call `/api/webhooks/radarr` and
  `/api/webhooks/sonarr`.

For subtitles and letterbox:

- Install or mount `ffmpeg`/`ffprobe`.
- Install or mount `mkvmerge`/`mkvpropedit` for MKV writes.
- Install ImageMagick `convert` only if using the letterbox `trim` backend.

## Model Artifacts

Expected artifact locations:

| Artifact | Default path |
|---|---|
| CLIP ONNX | `marquee/ml/models/<AI_MODEL>.onnx` |
| Aesthetic head | `marquee/ml/models/sa_0_4_vit_b_32_linear.pth` or `.npz` |
| Face model | `marquee/ml/models/scrfd_500m_bnkps.onnx` |
| DINO model | `marquee/ml/models/dinov2-vits14.onnx` |
| Person model | `marquee/ml/models/yolo11n.onnx` |
| Taste profile | `marquee/ml/taste_profile.<AI_MODEL>.npz` |
| Zero-shot axes | `marquee/ml/zeroshot_axes.clip-vit-b-32.npz` |

Required artifacts are CLIP, aesthetic head, face model, and taste profile.
DINO/person/zero-shot/learned-head artifacts are optional feature enhancers.

## Provider Selection

`EXECUTION_PROVIDER=auto` is resolved by `marquee/ml/hardware.py`.

Expected order:

```text
CUDA -> OpenVINO GPU/CPU -> CoreML -> CPU
```

Explicit provider aliases include `cuda`, `nvidia`, `openvino`, `intel`,
`openvino-cpu`, `coreml`, `apple`, `cpu`, and `tensorrt`.

Verify providers on a target host with:

```bash
python - <<'PY'
import onnxruntime as ort
from marquee.ml.hardware import detect_hardware, choose_execution_providers
print("ORT providers:", ort.get_available_providers())
print("Hardware:", detect_hardware())
print("Chosen:", choose_execution_providers())
PY
```

## Launch

Bare metal:

```bash
source .venv/bin/activate
uvicorn marquee.main:app --host 0.0.0.0 --port 3165
```

Health check:

```bash
curl http://localhost:3165/health
```

## Verification Checklist

1. `GET /health` returns `status: ok`.
2. `POST /api/sync/all` imports movies/series when clients are configured.
3. `GET /api/system/status` reports expected tool availability.
4. `GET /api/config/pipeline` shows expected model/provider settings.
5. A test run through `POST /api/pipeline/movie/{movie_id}/run` creates a
   `PipelineRun` row and archived run JSON.
6. Poster deployment via feedback writes only inside configured media roots.
7. Subtitle mutation and letterbox apply are tested on expendable media before
   use on a live library.

## Host Validation Rules

- Validate target host GPU driver/runtime compatibility before selecting an
  accelerated profile.
- Derive actual Radarr/Sonarr media mount paths from the live Arr instances and
  Marquee database immediately before testing.
- OCR should use the strongest working provider on the host: prefer a GPU
  PaddlePaddle/PaddleOCR path when available and stable, then fall back to CPU
  if GPU setup is unavailable or fails.
- Confirm Docker media volume mappings before enabling poster, subtitle, or
  letterbox write operations.
