# Marquee

**AI poster selection for self-hosted Radarr and Sonarr libraries.**

Marquee fetches every poster TMDB has for a title, removes the ones that can never be right,
ranks the rest against a taste profile learned from posters you chose yourself, writes the
winner into the media folder, and learns from how you re-rank or override its choice.

> **Status: mid-rebuild.** The selection engine runs from the command line: it builds its
> models, seeds a taste profile from the posters already in your library, and ranks one
> movie's TMDB posters at a time. The database, job queue, API, deploy-and-review loop, and
> web interface are being rebuilt on top of it. Nothing writes to a media library yet.

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

# 4. Build the models into data/models/. The exports need the export tools (PyTorch,
#    Transformers, ONNX), which only this step uses.
uv sync --extra nvidia --group export
uv run marquee models fetch
uv sync --extra nvidia              # optional: remove the export tools again

# 5. Build the first taste profile from the posters already in your library.
uv run marquee taste seed --dry-run # see what would be seeded; writes nothing
uv run marquee taste seed

# 6. Check the machine.
uv run marquee doctor

# 7. Rank one movie's posters.
uv run marquee run "Die Hard"
```

`marquee models fetch` builds every model that is missing and leaves alone what is present:
the CLIP image encoder, DINOv2, the zero-shot style axes, the YOLO person detector (exported
in an isolated environment, so its dependencies never enter the project), and the OCR weights.
Each build is checked before it is put in place, and `data/models/manifest.json` records
every model's size, SHA-256, and origin. Two models cannot be fetched yet: copy the LAION
aesthetic head (`sa_0_4_vit_b_32_linear.pth` or `.npz`) and the SCRFD face detector
(`scrfd_500m_bnkps.onnx`) into `data/models/` yourself. `--force ITEM` rebuilds one item.

`marquee taste seed` treats the `poster.jpg` in each movie folder as a poster you chose, at
full weight. A movie without a `poster.jpg` is ignored. Each poster is staged as a lossless
copy 500 px wide under `data/ml/seed/`, the size every candidate is measured at, and the
taste profile is built from those copies. The report lists what was seeded, what was
skipped and why, and any folder holding an image named after the movie file. Once a staged
set exists the command refuses to scan the library again, because posters Marquee has
written would look like your own picks: `--reuse` rebuilds the profile from the staged
copies, and `--rescan` deliberately scans again.

`marquee run "<title>"` finds the movie through its folder in your library (the IMDb id in
the movie file's name), fetches its posters from TMDB, and prints each stage, the ranked
table, and the pick. Everything lands in a new folder under `data/runs/`: the ranked
posters, `pipeline.log`, and the run archive `pipeline_run.json`. `--year` picks between
movies that share a title, `--tmdb-id` skips the library lookup, and `--json` prints the
summary as JSON.

`marquee doctor` prints one line per check (Python, settings, data directory, models, model
manifest, taste profile, ONNX Runtime, GPU, OCR, database, media roots, TMDB, Radarr,
Sonarr) with `PASS`, `WARN`, or `FAIL`, and exits non-zero if anything fails. `--json`
prints the same report as JSON. It never writes to a media folder and never prints a secret.

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
