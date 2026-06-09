# Linux Deployment Plan — Marquee on theforge

> **For Hermes:** Implement this plan task-by-task. Read each task fully before starting.

**Goal:** Run the Marquee poster pipeline on theforge (Linux x86_64 + RTX 3070) alongside the existing macOS deployment, without overwriting or breaking either.

**Architecture:** Platform-agnostic code + per-platform env files. The ONNX runtime provider selection auto-detects CUDA on Linux (falls back to CPU). The `.env` file is split: shared secrets stay in `.env`, platform-specific overrides go in `.env.linux` / `.env.macos`. Model files are shared (ONNX is platform-agnostic; PyTorch `.pth` loads CPU-side on both).

**Tech Stack:** Python 3.12+, fastapi, onnxruntime-gpu (CUDA 12.x), PyTorch 2.x CUDA, PaddlePaddle/PaddleOCR

---

## Current State

| Item | macOS (current) | Linux (target) |
|---|---|---|
| Hostname | Mac (Apple Silicon) | theforge |
| CPU | Apple M-series | Intel 13600KF (12c) |
| GPU | Apple GPU (MPS/CoreML) | RTX 3070 (8GB, CUDA 13.2) |
| Python | 3.13 (Homebrew) | 3.14.5 (system) |
| ONNX providers | CoreML > CPU | **CUDA** > CPU |
| venv | Set up, all deps | Empty venv |
| Model files | Present | Missing |
| .env | macOS paths | Needs Linux paths |

---

## Platform Differences — What Changes

### 1. ONNX Runtime: `onnxruntime` → `onnxruntime-gpu`

| macOS | Linux |
|---|---|
| `onnxruntime` (CoreML/Silicon) | `onnxruntime-gpu` (CUDA 12.x) |
| Providers: CoreML, CPU | Providers: CUDA, CPU |

The package name differs. `onnxruntime-gpu` is a separate PyPI package that includes CUDA/CuDNN/TensorRT. The import is still `import onnxruntime as ort`.

### 2. Provider Selection Logic

`marquee/ml/embedding.py:choose_execution_providers()` currently prefers:
```
OpenVINO → CoreML → CPU
```

Needs to become:
```
CUDA → OpenVINO → CoreML → CPU
```

CUDA is only available when `onnxruntime-gpu` is installed. The `"auto"` mode should detect it.

### 3. PyTorch: CPU → CUDA

`requirements.txt` lists `torch>=2.0.0` but doesn't specify the CUDA variant. On Linux, we need the CUDA 12.4 build (compatible with CUDA 13.2 driver — forward compatible).

Default PyTorch from PyPI includes CUDA 12.4 support, so `pip install torch torchvision` should work.

### 4. PaddleOCR: CPU-only stays CPU

`ocr_filter.py` hardcodes `device="cpu"`. PaddleOCR GPU support requires PaddlePaddle-GPU which has complex CUDA/cuDNN version coupling. Given that OCR is the bottleneck but is multiprocessed, and the 12-core CPU will handle it faster than the Mac, CPU is fine for now. Can revisit GPU later.

### 5. Config: Per-platform .env files

| File | Purpose |
|---|---|
| `.env` | Shared secrets (API keys, tokens) |
| `.env.linux` | theforge-specific overrides (paths, OCR workers, execution provider) |
| `.env.macos` | macOS-specific overrides (paths, CoreML hints) |

The application loads `.env` always. Platform-specific overrides are sourced by a helper or manually set in the shell before launching.

### 6. Path Mappings

The Radarr/Sonarr instances are at `192.168.4.200` — reachable from both Mac and Linux. Path mappings differ:

| Setting | macOS | Linux |
|---|---|---|
| `RADARR_PATH_PREFIX` | `/plunder/movies` | `/plunder/movies` |
| `RADARR_MEDIA_PATH` | `/Volumes/PLUNDER/Media/Movies` | **TBD** — how is PLUNDER mounted? |
| `SONARR_PATH_PREFIX` | `/plunder/tv` | `/plunder/tv` |
| `SONARR_MEDIA_PATH` | `/Volumes/PLUNDER/Media/TV` | **TBD** |

**Question for Gautam:** How is the PLUNDER share mounted on theforge? Is it at a local path like `/mnt/plunder/Media/...` or via NFS/SMB?

### 7. OCR Workers

Mac was limited to 4 (crashed above). Theforge has 12 cores + 32GB RAM. Can likely use 6-8 workers safely. Configurable via `OCR_WORKERS` env var.

---

## Implementation Tasks

### Task 0: Verify CUDA Compatibility

**Objective:** Confirm ONNX Runtime GPU and PyTorch CUDA will work with driver 595.80 / CUDA 13.2.

**Step 1: Check driver compatibility**
```bash
nvidia-smi  # Already confirmed: Driver 595.80, CUDA 13.2
```

**Step 2: Check PyTorch compatibility**
- PyTorch 2.6 ships with CUDA 12.4 libraries bundled — works with CUDA 13.2 driver (forward compatible)
- No action needed

**Step 3: Check ONNX Runtime compatibility**
- `onnxruntime-gpu` 1.20+ requires CUDA 12.x and cuDNN 9.x (bundled in the wheel)
- Compatible with CUDA 13.2 driver
- No action needed

**Verdict:** Both will work with the existing driver. The CUDA toolkit (nvcc) is NOT needed — both PyTorch and ONNX Runtime ship their own CUDA libraries in the wheel.

---

### Task 1: Set Up Linux Virtual Environment

**Objective:** Create a working venv with CUDA-accelerated packages.

**Files:**
- Create: `.venv/` (fresh venv)
- Modify: None (install packages into venv)

**Step 1: Create venv**
```bash
cd /home/cptbandit/Marquee
python3 -m venv .venv --clear
source .venv/bin/activate
```

**Step 2: Upgrade pip**
```bash
pip install --upgrade pip setuptools wheel
```

**Step 3: Install CUDA-accelerated core packages**
```bash
# PyTorch with CUDA 12.4 (works with CUDA 13.2 driver)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124

# ONNX Runtime with CUDA
pip install onnxruntime-gpu

# Verify
python -c "import torch; print('CUDA available:', torch.cuda.is_available()); print('Device:', torch.cuda.get_device_name(0))"
python -c "import onnxruntime; print('Providers:', onnxruntime.get_available_providers())"
```
Expected: `CUDA available: True`, `NVIDIA GeForce RTX 3070`, `['CUDAExecutionProvider', 'CPUExecutionProvider', ...]`

**Step 4: Install remaining dependencies**
```bash
pip install -e ".[all]"
# OR manually:
pip install fastapi uvicorn[standard] pydantic pydantic-settings \
    sqlalchemy[asyncio] aiosqlite alembic httpx \
    Pillow imagehash opencv-python numpy \
    python-dotenv python-multipart \
    paddlepaddle paddleocr onnx tqdm transformers \
    pytest pytest-asyncio pytest-cov ruff
```

**Step 5: Verify full import chain**
```bash
python -c "
import torch; print('Torch:', torch.__version__, 'CUDA:', torch.cuda.is_available())
import onnxruntime; print('ONNX:', onnxruntime.get_available_providers())
from paddleocr import PaddleOCR; print('PaddleOCR: OK')
import cv2; print('OpenCV:', cv2.__version__)
from PIL import Image; print('Pillow: OK')
import numpy; print('NumPy:', numpy.__version__)
"
```

---

### Task 2: Update Provider Selection Logic

**Objective:** Add `CUDAExecutionProvider` to the auto-detection chain so ONNX models use the GPU.

**Files:**
- Modify: `marquee/ml/embedding.py:18-38` (`choose_execution_providers`)

**Step 1: Update `choose_execution_providers()`**

Change the `preferred` tuple from:
```python
preferred = (
    "OpenVINOExecutionProvider",
    "CoreMLExecutionProvider",
    "CPUExecutionProvider",
)
```
To:
```python
preferred = (
    "CUDAExecutionProvider",
    "OpenVINOExecutionProvider",
    "CoreMLExecutionProvider",
    "CPUExecutionProvider",
)
```

No other changes needed. The function already filters to only available providers. When running on macOS, CUDA won't be in `ort.get_available_providers()` so it's harmlessly skipped. When running on Linux with `onnxruntime-gpu`, CUDA is available and gets priority.

**Step 2: Verify the change**
```bash
python -c "
from marquee.ml.embedding import choose_execution_providers
providers = choose_execution_providers('auto')
print('Selected providers:', providers)
assert 'CUDAExecutionProvider' in providers, 'CUDA should be first on this machine'
print('OK: CUDA prioritized')
"
```

---

### Task 3: Create Platform .env Files

**Objective:** Separate macOS and Linux configurations so neither overwrites the other.

**Files:**
- Create: `.env.linux` — Linux-specific overrides
- Create: `.env.macos.example` — documented macOS overrides
- Modify: `.env.example` — add note about platform-specific files

**Step 1: Create `.env.linux`**
```bash
# Marquee — Linux (theforge) overrides
# Source BEFORE launching: set -a; source .env.linux; set +a

# Path Mappings — theforge NFS mounts
# RADARR_PATH_PREFIX=/plunder/movies
# RADARR_MEDIA_PATH=/mnt/plunder/Media/Movies   # TBD — adjust to actual mount
# SONARR_PATH_PREFIX=/plunder/tv
# SONARR_MEDIA_PATH=/mnt/plunder/Media/TV        # TBD — adjust to actual mount

# ONNX execution — CUDA is auto-detected, but can force:
# EXECUTION_PROVIDER=CUDAExecutionProvider

# OCR workers — theforge has 12 cores / 32GB
OCR_WORKERS=6
```

**Step 2: Update `.env.example` with platform note**
Add a section explaining the `.env` / `.env.linux` / `.env.macos` split.

**Step 3: Document the launch pattern**
The user sources `.env` first (shared secrets), then `.env.linux` (or `.env.macos`) before launch:
```bash
set -a; source .env; source .env.linux; set +a
uvicorn marquee.main:app --host 0.0.0.0 --port 3165
```

---

### Task 4: Copy Model Files to Linux

**Objective:** Transfer ONNX models, aesthetic head, and taste profile from Mac to theforge.

**Files needed:**
- `marquee/ml/models/clip-vit-b-32.onnx` (~150 MB)
- `marquee/ml/models/sa_0_4_vit_b_32_linear.pth` (~2 MB)
- `marquee/ml/models/scrfd_500m_bnkps.onnx` (~2 MB)
- `marquee/ml/taste_profile.clip-vit-b-32.npz` (~20 MB)

**Step 1: Create models directory**
```bash
mkdir -p /home/cptbandit/Marquee/marquee/ml/models
```

**Step 2: Transfer files from Mac**
Use `scp`, `rsync`, or any file transfer tool. Example:
```bash
# From the Mac:
scp marquee/ml/models/*.onnx marquee/ml/models/*.pth marquee/ml/taste_profile.*.npz \
    cptbandit@theforge:/home/cptbandit/Marquee/marquee/ml/
```

**Step 3: Verify**
```bash
ls -lh marquee/ml/models/
ls -lh marquee/ml/taste_profile.*.npz
python -c "
from marquee.ml.embedding import create_onnx_session
session = create_onnx_session()
print('Providers:', session.get_providers())
# Should show CUDAExecutionProvider first
"
```

---

### Task 5: Configure .env with Secrets

**Objective:** Ensure theforge has the shared secrets (API keys, *arr config).

**Files:**
- Modify: `.env` (copy secrets from Mac, keep paths platform-agnostic or Linux-specific)

**What moves from Mac `.env` to theforge `.env`:**
- `TMDB_READ_ACCESS_TOKEN`
- `RADARR_URL` / `RADARR_API_KEY`
- `SONARR_URL` / `SONARR_API_KEY`
- `TVDB_API_KEY` / `FANART_API_KEY` (if set)
- Platform-agnostic: `PORT`, `DB_URL`, pipeline weights/thresholds

**What stays in `.env.linux`:**
- `RADARR_MEDIA_PATH` / `SONARR_MEDIA_PATH` (platform-specific paths)
- `OCR_WORKERS`
- `EXECUTION_PROVIDER` (if not auto)

---

### Task 6: Test GPU-Accelerated ONNX Inference

**Objective:** Verify CUDA execution provider is actually used and is faster than CPU.

**Step 1: Run a CLIP encoding benchmark**
```bash
cd /home/cptbandit/Marquee
source .venv/bin/activate
set -a; source .env; source .env.linux; set +a
python -c "
import time
from PIL import Image
import numpy as np
from marquee.ml.embedding import CLIPImageEncoder

# Create a fake image
img = Image.fromarray(np.random.randint(0, 255, (224, 224, 3), dtype=np.uint8))

encoder = CLIPImageEncoder()
print('Session providers:', encoder.session.get_providers())

# Warm-up
for _ in range(3):
    encoder.encode(img)

# Benchmark
start = time.perf_counter()
for _ in range(50):
    encoder.encode(img)
elapsed = time.perf_counter() - start
print(f'50 encodings: {elapsed:.2f}s ({elapsed/50*1000:.1f}ms each)')
"
```
Expected: < 5ms per encoding on GPU. CPU-only would be ~15-30ms.

---

### Task 7: Test Full Pipeline End-to-End

**Objective:** Run the full pipeline on a test movie and verify everything works.

```bash
# Start the server
source .venv/bin/activate
set -a; source .env; source .env.linux; set +a
uvicorn marquee.main:app --host 0.0.0.0 --port 3165 &

# Test health
curl http://localhost:3165/health

# Run a test pipeline (pick a simple movie)
curl -X POST http://localhost:3165/api/test/pipeline/movie/550 \
    -H "Content-Type: application/json" \
    --max-time 300
```

Expected: Pipeline completes successfully. Check logs for CUDA provider usage. Ranked posters appear in `experiments/runs/`.

---

### Task 8: Document Everything

**Objective:** Update project docs to reflect dual-platform support.

**Files:**
- Modify: `README.md` — add Linux setup section
- This file (`design/05-linux-deployment.md`) is the deployment reference

---

## Pitfalls & Notes

1. **PyTorch CUDA version mismatch**: The driver is CUDA 13.2, but PyTorch ships CUDA 12.4 libraries. This is fine — the CUDA driver is forward-compatible. PyTorch's bundled CUDA 12.4 libs work on any driver ≥ 525.60.13.
2. **onnxruntime vs onnxruntime-gpu**: These are different PyPI packages. The import is the same (`import onnxruntime`), but `onnxruntime-gpu` includes CUDA/CuDNN/TensorRT providers. Installing both in the same venv causes conflicts — use `onnxruntime-gpu` exclusively on Linux.
3. **PaddleOCR GPU**: Not attempted in this plan. CPU with 6-8 workers on 12 cores should be 2-3× faster than Mac's 4 workers. GPU PaddleOCR requires PaddlePaddle-GPU with specific CUDA/cuDNN versions — fragile and not worth it yet.
4. **Model files are platform-agnostic**: ONNX models work identically on any platform. The `.pth` file loads on CPU regardless. No re-export needed.
5. **Taste profile is NumPy**: `.npz` files are platform-agnostic. Same file works everywhere.
6. **VRAM headroom**: RTX 3070 has 8GB. CLIP B/32 ONNX uses ~150MB. SCRFD ONNX uses ~2MB. Plenty of room for batch processing later.
7. **.env management**: Never commit `.env` or `.env.linux` to git. `.env.example` and an `.env.linux.example` are the templates (Task 3).
8. **The Mac deployment stays untouched**: All changes are additive or use platform-detection. No code paths are removed from macOS. The `choose_execution_providers()` change adds CUDA to the priority list — CoreML is still tried before CPU on macOS.
9. **File tool path mapping**: The `write_file` tool may have a path resolution issue on this SSH backend — use `terminal` with heredocs as a fallback for file creation.
