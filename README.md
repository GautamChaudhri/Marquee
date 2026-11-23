# Marquee

**AI poster selection for self-hosted Radarr and Sonarr libraries.**

Marquee fetches every poster TMDB has for a title, removes the ones that can never be right,
ranks the rest against a taste profile learned from posters you chose yourself, writes the
winner into the media folder, and learns from how you re-rank or override its choice.

> **Status: mid-rebuild.** The selection engine was carried over from an earlier codebase and
> now sits in its new layout with the project tooling around it. The database, job queue, API,
> deploy-and-review loop, and web interface are being rebuilt on top of it. Nothing writes to
> a media library yet.

## Why

A title on TMDB can have thirty or more posters. Most of them are wrong for a given library: a
foreign-language plate, a text-plastered fan edit, a floating-head composite, a blurry upscale.
The right one is a matter of taste, and taste is exactly what rule-based artwork managers
cannot express. Rules like "1000×1500, English, from this source" describe a file, not a
picture, and are satisfied equally by a painted one-sheet and by a muddy render with four
taglines across the bottom. So you either accept whatever the rules return, or you choose by
hand, which is fine for fifty titles and impossible for five thousand.

**Marquee looks at the picture.** Every candidate is embedded with CLIP, scored for aesthetic
quality, measured for faces, palette, composition, and compression artifacts, and read with
OCR. It is then ranked against a taste profile built from posters you chose: a
k-nearest-neighbour match in embedding space, with negative examples for art you rejected,
rather than a generic quality score. Every decision you make feeds back into that profile.

Marquee is not a metadata manager. It writes no NFO files and scrapes no cast lists; it does
one job, choosing the poster.

## How a title is processed

Gates are absolute and identical for every title; ranking is relative to the survivors of one
title. The cheapest signals run first:

1. Fetch candidate metadata from TMDB and gate on resolution before downloading anything.
2. Drop byte-identical duplicates (SHA-256).
3. Style features with CLIP: taste similarity, aesthetic score, zero-shot style axes. Style gate.
4. OCR text gate: the title must be present and nothing else (taglines, credits, dates) may be.
5. Group variants of the same design into stacks instead of deleting them.
6. Detail features: faces, people, palette, composition, artifacts, title geometry. Final gate.
7. Rank with a weighted scorer, plus a small learned correction once there is enough feedback.

## Requirements

- Linux x86_64.
- [uv](https://docs.astral.sh/uv/) (it installs and manages Python 3.12 for the project).
- Docker with Compose, for PostgreSQL.
- The system OpenGL library (`libGL.so.1`: `mesa-libGL` on Fedora, `libgl1` on Debian and
  Ubuntu). PaddleOCR requires the full OpenCV build, which links against it.
- Optional: an NVIDIA GPU. CUDA and cuDNN come from Python wheels; only the driver is needed.

## Setup

```bash
# 1. Install the project environment. Pick exactly one extra: nvidia or cpu.
uv sync --extra nvidia

# 2. Configure. Every setting is listed, with a comment, in .env.example.
cp .env.example .env
#    then edit .env: database password, media roots, TMDB token, Radarr and Sonarr keys

# 3. Start PostgreSQL (published on 127.0.0.1 only).
docker compose up -d

# 4. Put the model files in data/models/: the CLIP ViT-B/32 ONNX export, the LAION
#    aesthetic head, and the SCRFD face detector. A `marquee models fetch` command is planned.

# 5. Check the machine.
uv run marquee doctor
```

`marquee doctor` prints one line per check (Python, settings, data directory, models, ONNX
Runtime, GPU, OCR, database, media roots, TMDB, Radarr, Sonarr) with `PASS`, `WARN`, or
`FAIL`, and exits non-zero if anything fails. `--json` prints the same report as JSON. It
never writes to a media folder and never prints a secret.

Use `uv sync --extra nvidia` (or `--extra cpu`) whenever you sync: a plain `uv sync` removes
the extra's packages.

## Development

```bash
uv run ruff check .
uv run ruff format --check .
uv run lint-imports                 # layering contracts
uv run pytest                       # everything
uv run pytest -m "not gpu and not models and not db"   # what CI runs
```

Tests that need model files are marked `models`, tests that need a GPU `gpu`, and tests that
need the Compose database `db`. The test suite never reads your `.env` and never touches the
real `data/` directory.

## Layout

```
marquee/        the application package (see docs/architecture.md)
tests/          mirrors marquee/ one-to-one
data/           runtime state: models, caches, runs, backups (not committed)
docs/           architecture and other documentation
frontend/       the web interface (being rebuilt)
_salvage/       code from the earlier codebase still waiting to be re-homed
compose.yaml    PostgreSQL for development
```

See [docs/architecture.md](docs/architecture.md) for what each package is for, the layering
rule, and how to add a module.

## License

[GNU GPL v3](LICENSE).
