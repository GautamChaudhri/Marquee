# Third-party models, software, and services

Marquee is free software under the GNU General Public License, version 3 only
([LICENSE](../LICENSE)). The project publishes its source code and nothing else: no
container image and no model file. Everything below reaches a machine because its owner
builds or downloads it there, with `docker compose build` and `marquee models fetch`
(`docker compose run --rm setup`). None of it is in this repository.

Checked on 2026-11-27, against the versions this repository pins.

## Models

`marquee models fetch` downloads each of these from its publisher, or builds it on the
user's machine from what it downloads, and checks every download against a SHA-256 written
in `marquee/model_fetch.py`. The files land in the data folder (`models/`, and the zero-shot
axes in `ml/`), and `models/manifest.json` records where each came from.

| Model | Used for | Comes from | Licence | On the machine |
|---|---|---|---|---|
| OpenAI CLIP ViT-B/32, image tower | taste matching, aesthetic and style features | Hugging Face `openai/clip-vit-base-patch32`, revision `3d74acf9`, `pytorch_model.bin` | MIT ([github.com/openai/CLIP](https://github.com/openai/CLIP/blob/main/LICENSE)) | exported to ONNX (`models/clip-vit-b-32.onnx`) |
| Zero-shot style axes | the illustrated, minimalist, and vintage features | the same CLIP's text tower and tokenizer | MIT (as CLIP) | built (`ml/zeroshot_axes.clip-vit-b-32.npz`) |
| Meta DINOv2 ViT-S/14 | design grouping, detail features | code: `facebookresearch/dinov2` at commit `7764ea0f`; weights: `dl.fbaipublicfiles.com/dinov2/dinov2_vits14/dinov2_vits14_pretrain.pth` | Apache-2.0 (the repository's README: code and model weights) | exported to ONNX (`models/dinov2-vits14.onnx`) |
| Ultralytics YOLO11n | people in the picture | `github.com/ultralytics/assets`, release v8.4.0, `yolo11n.pt` | **AGPL-3.0** (or Ultralytics' Enterprise License, for commercial use) | exported to ONNX (`models/yolo11n.onnx`) in an isolated environment |
| PaddleOCR PP-OCRv5 mobile detection, PP-OCRv5 server recognition, PP-LCNet text-line orientation | the text gate and title geometry | PaddleX's official models, fetched by PaddleOCR 3.6.0 from Hugging Face (`PaddlePaddle/…`) | Apache-2.0 | downloaded (`models/paddlex/official_models/`) |
| LAION aesthetic predictor, linear head for ViT-B/32 | aesthetic score | `LAION-AI/aesthetic-predictor` at commit `8142140`, `sa_0_4_vit_b_32_linear.pth` | MIT | downloaded and converted (`models/sa_0_4_vit_b_32_linear.npz`) |
| InsightFace SCRFD 500M with keypoints | faces | `det_500m.onnx` from InsightFace's `buffalo_sc` pack (release v0.7) | **non-commercial research use only** (InsightFace's model zoo: "ALL models are available for non-commercial research purposes only.") | downloaded (`models/scrfd_500m_bnkps.onnx`) |

**What this means for an installation.** With the default models, a Marquee installation
is for **personal, non-commercial use**: the face detector is published for non-commercial
research only, and the YOLO11 weights are AGPL-3.0. CLIP's model card also describes the
model as a research output and says that "any deployed use case of the model - whether
commercial or not - is currently out of scope"; its licence is MIT. Replacing the face
detector or the person detector with a permissively licensed one would change what the
selection engine decides, and is the owner's decision for later.

## Python packages

The runtime image's environment (the `nvidia` extra, without the development tools), read
from each installed package's own metadata (`License-Expression`, else the `License` field,
else the licence classifiers; NumPy and SciPy state theirs as BSD in their classifiers):

```bash
docker run --rm -i --entrypoint python marquee:runtime - < licences.py
# licences.py prints, for every importlib.metadata.distributions() entry, its name,
# version, License-Expression, License, and "License ::" classifiers
```

**MIT and MIT-like (MIT, MIT-0, MIT-CMU, 0BSD)** (32): alembic 1.20.1, annotated-doc 0.0.7, annotated-types 0.8.0, anyio 4.16.0, attrs 26.1.0, cffi 2.1.1, chardet 7.7.0, charset-normalizer 3.5.3, colorlog 6.12.0, fastapi 0.165.0, filelock 4.3.0, future 1.0.0, h11 0.16.0, imagesize 2.0.1, Mako 1.4.3, onnxruntime-gpu 1.26.0, opt-einsum 3.3.0, pillow 13.0.0, py-cpuinfo 9.0.0, pyclipper 1.4.0, pydantic 2.14.4, pydantic-settings 2.16.3, pydantic_core 2.50.5, PyYAML 6.0.2, ruamel.yaml 0.19.1, setuptools 84.2.1, six 1.17.0, SQLAlchemy 2.1.9, truststore 0.10.5, typing-inspection 0.4.5, urllib3 2.8.0, wcwidth 0.9.5.

**BSD (2- and 3-clause)** (24): click 8.5.1, fsspec 2026.9.0, httpcore 1.0.9, httpcore2 2.16.2, httpx 0.28.1, httpx2 2.16.2, idna 3.20, ImageHash 4.3.2, MarkupSafe 3.0.4, networkx 3.7, numpy 2.3.5, nvidia-ml-py 13.615.71, nvidia-nccl-cu12 2.27.3, pandas 3.1.1, prettytable 3.19.0, protobuf 7.36.2, psutil 7.2.2, pycparser 3.12, python-dateutil 4.0.0, python-dotenv 1.2.4, scipy 1.18.1, shapely 2.2.0, starlette 1.10.0, uvicorn 0.54.0.

**Apache-2.0** (22): aiosignal 1.4.0, aistudio_sdk 0.3.9, bce-python-sdk 0.9.79, cuda-bindings 12.9.11, cuda-pathfinder 1.9.1, flatbuffers 25.12.19, frozenlist 1.8.0, hf-xet 1.8.0, huggingface_hub 2.4.0, modelscope 1.40.3, modelscope-hub 0.7.1, multidict 7.2.1, opencv-contrib-python 4.10.0.84, opentelemetry-api 1.45.2, paddleocr 3.6.0, paddlepaddle-gpu 3.3.1, paddlex 3.6.1, propcache 0.5.4, requests 2.34.2, safetensors 0.8.0, tzdata 2026.6, yarl 1.25.1.

**Two licences together or a choice of them** (9): aiohttp 3.14.5 (Apache-2.0 AND MIT), cryptography 50.0.2 (Apache-2.0 OR BSD-3-Clause), greenlet 3.5.6 (MIT AND PSF-2.0), packaging 26.3 (Apache-2.0 OR BSD-2-Clause), pycryptodome 4.6.0 (BSD-2-Clause AND Unlicense AND MIT), pypdfium2 5.15.2 (BSD-3-Clause AND Apache-2.0), PyWavelets 1.10.0 (MIT AND BSD-3-Clause), tqdm 4.70.1 (MPL-2.0 AND MIT), ujson 6.1.0 (BSD-3-Clause AND TCL).

**Weak copyleft (LGPL, MPL-2.0)** (5): certifi 2026.11.20 (MPL-2.0), crc32c 2.9.post0 (LGPL-2.1-or-later), psycopg 3.3.9 (LGPL-3.0-only), psycopg-binary 3.3.9 (LGPL-3.0-only), python-bidi 0.6.12 (LGPL).

**Python Software Foundation (PSF-2.0)** (2): aiohappyeyeballs 2.7.1, typing_extensions 4.16.0.

None of these licences prevents combining them with GPL-3.0-only code. psycopg, Marquee's
database driver, is LGPL-3.0-only.

The `export` dependency group (PyTorch 2.15 CPU build, Transformers, ONNX), which only
`marquee models fetch` uses, is installed in the `marquee:export` image. Ultralytics 8.4.216
(AGPL-3.0) runs in its own isolated `uv tool` environment during that one export and never
enters Marquee's environment.

## NVIDIA's wheels and the CUDA libraries

CUDA, cuBLAS, cuDNN, cuFFT, cuRAND, cuSOLVER, cuSPARSE, NVRTC, nvJitLink and the rest come as
NVIDIA's `nvidia-*-cu12` wheels (and `cuda-python`), which `uv` installs from PyPI and
PaddlePaddle's package index onto the user's machine when the image is built. They are
licensed by NVIDIA under its own terms (the NVIDIA Software License Agreement and the CUDA
EULA; their metadata says "NVIDIA Proprietary Software"; `nvidia-nccl-cu12` is BSD-3-Clause
and `nvidia-nvtx-cu12` Apache-2.0). The user installs them under NVIDIA's terms; the project
never redistributes them. Marquee's own code finds them in the environment
(`marquee/ml/hardware.py`) and loads them so that ONNX Runtime can use the GPU; ONNX Runtime
and PaddlePaddle load them too. The GPU driver is the user's own, on the host.

## The web app's packages

The production dependencies in the `marquee:web` image (`npm ls --omit=dev --all`, licences
from each package's `package.json`): `openapi-fetch` 0.17.0 and `svelte-dnd-action` 0.9.80,
which Marquee depends on, and what they need (`openapi-typescript-helpers`, `svelte` and its
runtime dependencies) are all MIT, except `aria-query` and `axobject-query` (Apache-2.0).
The build also bundles SvelteKit 3.4.1, `@sveltejs/adapter-node` 6.0.6, Svelte 5.59.2,
`devalue`, `cookie`, `esm-env`, and `clsx` into the server it runs: all MIT.

## Container images

Built on the user's machine from public base images: `python:3.12.16-slim-trixie` (Debian
13 and Python, under their own licences), `ghcr.io/astral-sh/uv` 0.13.9 (MIT or Apache-2.0;
only during the build), `node:26.13.1-trixie-slim` (Debian and Node.js, MIT), and
`postgres:19` (the PostgreSQL License). The runtime image adds the PostgreSQL 19 client
tools from the PostgreSQL project's apt repository (the PostgreSQL License).

## Services

- **TMDB.** Marquee asks TMDB for each movie's details and its posters, with the user's own
  API read access token, under TMDB's API terms of use, which permit non-commercial use and
  ask for attribution. The System page carries TMDB's logo and the notice their terms ask
  for: "This product uses TMDB and the TMDB APIs but is not endorsed, certified, or
  otherwise approved by TMDB." Commercial use needs a commercial agreement with TMDB.
- **Radarr.** Marquee reads the movie list from the user's own Radarr (GPL-3.0) through its
  API key. It never writes to Radarr.
