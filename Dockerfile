# syntax=docker/dockerfile:1
#
# Marquee's container image, with two targets:
#
#   runtime  what runs the product: the package and its NVIDIA dependencies, no PyTorch
#   export   runtime plus the export tools and uv; it exists only for `marquee models fetch`
#
# CUDA and cuDNN come from the Python wheels in the lockfile, as on a bare-metal install;
# the NVIDIA Container Toolkit supplies only the driver. Models, taste data, caches, and runs
# live on a volume mounted at /data, and every setting comes from the environment: the image
# holds no .env file, no model, and no secret.
#
#   docker build --target runtime -t marquee:runtime .
#   docker build --target export -t marquee:export .

ARG PYTHON_IMAGE=python:3.12.16-slim-trixie
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.9

FROM ${UV_IMAGE} AS uv

# ── Base: system libraries, the user, and the data folder ────────────────────
FROM ${PYTHON_IMAGE} AS base

# Only what the code needs to import and run, each found by running it:
#   libgl1           the OpenCV build PaddleOCR requires loads libGL.so.1
#   libglib2.0-0t64  that OpenCV build also loads libgthread-2.0.so.0
#   libgomp1         PaddlePaddle loads libgomp.so.1 (OpenMP)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0t64 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# A fixed id for now, matching the owner of the media share on the host.
ARG MARQUEE_UID=1000
ARG MARQUEE_GID=1000
RUN groupadd --gid "${MARQUEE_GID}" marquee \
    && useradd --uid "${MARQUEE_UID}" --gid "${MARQUEE_GID}" --create-home marquee \
    && mkdir /data \
    && chown marquee:marquee /data

# uv installs into /opt/venv with the base image's own Python, so the environment works
# in every stage built from this one.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=0 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:${PATH} \
    DATA_DIR=/data \
    MARQUEE_ENV_FILE=""

# The CUDA driver compiles, on first use, the GPU kernels a wheel ships without machine code
# for the card, and caches the result. On the data volume that cache outlives the container;
# without it every new container compiles again, which made a run three times slower. The
# driver's default size limit, 1 GiB, holds what Marquee compiles (about 0.6 GB).
ENV CUDA_CACHE_PATH=/data/cache/cuda-jit

# ── Dependencies: from the lockfile only, before any source, so a code change reuses them
FROM base AS deps
WORKDIR /src
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=.python-version,target=.python-version \
    uv sync --locked --extra nvidia --no-dev --no-install-project

# ── runtime ─────────────────────────────────────────────────────────────────
FROM deps AS runtime
# The PostgreSQL client tools (pg_dump, pg_restore, psql) for the scheduled database dump and
# `marquee db restore`. They must be the database server's major version or newer, which
# Debian does not carry, so they come from the PostgreSQL project's own apt repository, as
# postgresql.org/download/linux/debian sets it up: its signing key (pinned here by checksum;
# fingerprint B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8) and a deb822 source that names it.
# Only the client package; no server.
ARG PG_CLIENT_MAJOR=19
ADD --checksum=sha256:0144068502a1eddd2a0280ede10ef607d1ec592ce819940991203941564e8e76 \
    https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
# apt reads the key as its own unprivileged user.
RUN chmod 755 /usr/share/postgresql-common /usr/share/postgresql-common/pgdg \
    && chmod 644 /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && . /etc/os-release \
    && printf '%s\n' \
        'Types: deb' \
        'URIs: https://apt.postgresql.org/pub/repos/apt' \
        "Suites: ${VERSION_CODENAME}-pgdg" \
        'Architectures: amd64' \
        'Components: main' \
        'Signed-By: /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc' \
        > /etc/apt/sources.list.d/pgdg.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends "postgresql-client-${PG_CLIENT_MAJOR}" \
    && rm -rf /var/lib/apt/lists/*
# Its own folder holds only the PostgreSQL tools; first, so no wrapper stands in for them.
ENV PATH=/usr/lib/postgresql/${PG_CLIENT_MAJOR}/bin:${PATH}
# The package is installed into the environment, not left as source in the image.
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,target=/src,rw \
    uv sync --locked --extra nvidia --no-dev --no-editable
USER marquee
WORKDIR /home/marquee
CMD ["marquee", "doctor"]

# ── export ──────────────────────────────────────────────────────────────────
FROM deps AS export
# The YOLO export runs the Ultralytics exporter through `uv tool run`.
COPY --from=uv /uv /uvx /usr/local/bin/
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=.python-version,target=.python-version \
    uv sync --locked --extra nvidia --no-dev --group export --no-install-project
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,target=/src,rw \
    uv sync --locked --extra nvidia --no-dev --group export --no-editable
USER marquee
WORKDIR /home/marquee
CMD ["marquee", "models", "fetch"]
