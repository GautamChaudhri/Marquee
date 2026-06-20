# Marquee — multi-platform image.
#
# Build with exactly one hardware target:
#   docker build -f docker/Dockerfile --build-arg MARQUEE_HW=cpu    -t marquee:cpu .
#   docker build -f docker/Dockerfile --build-arg MARQUEE_HW=nvidia -t marquee:nvidia .
#   docker build -f docker/Dockerfile --build-arg MARQUEE_HW=intel  -t marquee:intel .
#
# cpu    — universal; Intel N150 up to any x86 box. ONNX CPU provider.
# nvidia — CUDAExecutionProvider via onnxruntime-gpu (CUDA libs from pip).
#          Run with the NVIDIA Container Toolkit (--gpus all / compose deploy).
# intel  — OpenVINOExecutionProvider for iGPU (UHD/QSV) and Arc dGPU (A310+).
#          Run with /dev/dri passed through.
#
# Apple Silicon is a bare-metal target (CoreML is not available inside Linux
# containers) — use the native install there instead.

FROM python:3.12-slim AS runtime
ARG MARQUEE_HW=cpu

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MARQUEE_HW=${MARQUEE_HW}

# OpenCV + Paddle native deps; OpenCL for the intel target.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        ffmpeg \
        imagemagick \
        mkvtoolnix \
        postgresql-client \
    && if [ "${MARQUEE_HW}" = "intel" ]; then \
        apt-get install -y --no-install-recommends \
            intel-opencl-icd \
            ocl-icd-libopencl1; \
    fi \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN mkdir -p /app/marquee/ml/models /app/data
COPY pyproject.toml README.md ./
COPY marquee ./marquee
COPY alembic ./alembic
COPY alembic.ini ./

# The accelerator extra installs the matching onnxruntime build; PaddleOCR
# (CPU build) handles OCR on every target. To run OCR itself on an NVIDIA
# GPU, swap paddlepaddle for the version-matched CUDA wheel:
#   pip install paddlepaddle-gpu==3.3.1 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/
RUN pip install --upgrade pip \
    && pip install ".[${MARQUEE_HW}]" \
    && pip install paddlepaddle paddleocr tqdm

# onnxruntime-gpu does not bundle CUDA — pull the runtime libs from pip and
# put them on the loader path (works with any host driver >= 525).
RUN if [ "${MARQUEE_HW}" = "nvidia" ]; then \
        pip install nvidia-cuda-runtime-cu12 nvidia-cudnn-cu12 nvidia-cublas-cu12 nvidia-cufft-cu12; \
    fi
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.12/site-packages/nvidia/cuda_runtime/lib:/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib:/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.12/site-packages/nvidia/cufft/lib

# Model files are NOT baked into the image — mount them:
#   ./marquee/ml/models          -> /app/marquee/ml/models

EXPOSE 3165
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s \
    CMD python -c "import httpx; httpx.get('http://localhost:3165/health').raise_for_status()"

CMD ["uvicorn", "marquee.main:app", "--host", "0.0.0.0", "--port", "3165", "--no-access-log"]
