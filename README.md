# Marquee

**AI poster selection for self-hosted Radarr movie libraries.**

Marquee fetches every poster TMDB has for a title, removes the ones that can never be right,
ranks the rest against a taste profile learned from posters you chose yourself, writes the
winner into the media folder, and learns from how you re-rank or override its choice.

Marquee works on movies managed by Radarr, and it needs an NVIDIA GPU.

> **Status: mid-rebuild.** The selection engine builds its models, seeds a taste profile
> from the posters already in your library, and ranks a movie's TMDB posters as a job: one
> movie or a batch, with every run and its candidates recorded in the database. A pick can be
> written into the movie's folder, with the poster it replaces backed up, and any write can
> be undone byte for byte. You can review each run (approve the pick, choose another, re-rank
> with a hate pile, reject them all, or take any of that back), and the taste profile is
> rebuilt from your feedback. Nothing is written to your library unless you ask for it or turn
> on `DEPLOY_POLICY`, and your own choice always beats the policy's. A web interface shows
> the library, each movie's latest run and poster history, and the job queue; its review page
> answers a run (keep the pick, use another, rank the designs with a hate pile, or refuse them
> all, then undo it), its taste page shows the profile, the agreement, and the exemplars, and
> its system page shows how the installation is set. The learned correction to the score is
> still to come.

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

## Deployment

Docker is the supported way to run Marquee: a Docker Compose stack on a Linux host with an
NVIDIA GPU and the NVIDIA Container Toolkit. The container image already exists and the
engine's commands run in it (see [docs/architecture.md](docs/architecture.md#container-image)).
The full stack, with the API server, the job worker, and the web interface, arrives with the
rest of the rebuild, and its install steps with it. Until then, the setup below runs the engine
directly on a Linux machine, which is also how Marquee is developed.

## Requirements

- Linux x86_64.
- [uv](https://docs.astral.sh/uv/) (it installs and manages Python 3.12 for the project).
- Docker with Compose, for PostgreSQL.
- The system OpenGL library (`libGL.so.1`: `mesa-libGL` on Fedora, `libgl1` on Debian and
  Ubuntu). PaddleOCR requires the full OpenCV build, which links against it.
- An NVIDIA GPU. CUDA and cuDNN come from Python wheels; only the driver is needed.

## Setup

```bash
# 1. Install the project environment. The cpu extra exists only for tests and CI.
uv sync --extra nvidia

# 2. Configure. Every setting is listed, with a comment, in .env.example.
cp .env.example .env
#    then edit .env: database password, movie roots (and, if Radarr sees them at other
#    paths, RADARR_MOVIE_ROOTS), TMDB token, Radarr URL and key, and an API key

# 3. Start PostgreSQL (published on 127.0.0.1 only) and create the schema.
docker compose up -d
uv run marquee db upgrade

# 4. Build the models into data/models/. The exports need the export tools (PyTorch,
#    Transformers, ONNX), which only this step uses.
uv sync --extra nvidia --group export
uv run marquee models fetch
uv sync --extra nvidia              # optional: remove the export tools again

# 5. Build the first taste profile from the posters already in your library.
uv run marquee taste seed --dry-run # see what would be seeded; writes nothing
uv run marquee taste seed

# 6. Copy the movie list from Radarr and record the posters already on disk.
uv run marquee sync --dry-run       # see what would change; writes nothing
uv run marquee sync

# 7. Make the seeded posters the first exemplar records, matched to movies by IMDb id.
uv run marquee taste import-seed

# 8. Check the machine.
uv run marquee doctor

# 9. Run the job queue (in its own terminal, or as a service). Ctrl-C stops it gracefully.
uv run marquee worker

# 10. Rank one movie's posters (the worker runs it), then review the pick.
uv run marquee run "Die Hard"
uv run marquee review show <run id> # the run id is in the summary `marquee run` prints
uv run marquee review approve <run id>

# 11. Serve the HTTP API (on API_HOST:API_PORT, 127.0.0.1:8000 by default).
uv run marquee api

# 12. Serve the web interface (see "The web interface" below), and sign in with API_KEY.
```

`marquee models fetch` builds every model that is missing and leaves alone what is present:
the CLIP image encoder, DINOv2, the zero-shot style axes, the YOLO person detector (exported
in an isolated environment, so its dependencies never enter the project), the OCR weights,
the LAION aesthetic head, and the SCRFD face detector. The last two are downloaded from
their publishers and must match a pinned SHA-256. Each build is checked before it is put in
place, and `data/models/manifest.json` records every model's size, SHA-256, and origin.
`--force ITEM` rebuilds one item.

`marquee taste seed` treats the `poster.jpg` in each movie folder as a poster you chose, at
full weight. A movie without a `poster.jpg` is ignored. Each poster is staged as a lossless
copy 500 px wide under `data/ml/seed/`, the size every candidate is measured at, and the
taste profile is built from those copies. The report lists what was seeded, what was
skipped and why, and any folder holding an image named after the movie file. Once a staged
set exists the command refuses to scan the library again, because posters Marquee has
written would look like your own picks: `--reuse` rebuilds the profile from the staged
copies, and `--rescan` deliberately scans again.

`marquee run "<title>"` finds the movie in the database by its title as Radarr lists it
(case ignored), queues a run of it, and follows the run to the end: the worker fetches the
movie's posters from TMDB and ranks them, and the command prints each stage, the ranked
table, and the pick (the highest-scoring candidate). `--year` or `--movie-id` picks between
movies that share a title, `--no-wait` queues the run and returns, and `--json` prints the
summary as JSON. It needs a running worker, unless `--no-wait` is given. Every run is
recorded in the database with its candidates, and leaves a folder under `data/runs/`, named
after the run (`00000042-die-hard`): the candidates it downloaded, the ranked posters,
`pipeline.log`, and the run archive `pipeline_run.json`. `marquee runs list` and
`marquee runs show <id>` show them, and `marquee runs batch --scope never-run|no-poster|all`
queues runs of many movies at once (`--limit N` caps it). A movie can be run when Radarr has
its file and the sync has fetched its TMDB details.

`marquee deploy <run id>` writes the run's pick into the movie's folder as `poster.jpg`;
`--candidate N` deploys another candidate of the run instead. The worker fetches TMDB's
original-size file of the candidate (it must be a JPEG, and it is never converted), backs up
the `poster.jpg` already there to `data/backups/`, writes the new file under a temporary name
and renames it into place, reads it back to check it, and records the write.
`marquee deployments list` shows every write and what a restore of it would put back, and
`marquee restore <deployment id>` restores any of them, not only the newest: it backs up what
is there now and puts back exactly what was in the folder before that deployment, or removes
the poster if there was none. Deploys and restores are jobs too, one at a time, and never
two for the same movie. A write records what it is about to do before it renames the new
file into place, so a write interrupted at any point finishes correctly on its next attempt.

`marquee taste import-seed` turns the posters `marquee taste seed` staged into exemplar
records, one per movie, each keeping a copy of its image under `data/ml/exemplars/`, and
registers the taste profile file as it is. From then on the profile is built from those
records, and `marquee taste seed` is refused in every form.

`marquee review list` shows the movies whose latest run you have not reviewed, the ones the
deploy policy deployed first, and `marquee review show <run>` shows a run's ranked candidates
with their ids. Each act of feedback goes to a movie's latest run and is recorded once, with
a copy of everything you were shown:

- `review approve <run>`: you keep the pick. It becomes the movie's exemplar at weight 0.75,
  unless the movie already has one.
- `review pick <run> --candidate <id>`: you choose another poster. It replaces the movie's
  exemplar at full weight.
- `review rank <run> --order <ids> [--hate <ids>]`: best first, with a hate pile. The first
  replaces the movie's exemplar; each hated poster the AI ranked in its top 10 becomes a
  negative exemplar (at most 8 per movie).
- `review reject <run> [--hate <ids>]`: none of them; the hated ones (all, by default) become
  negatives the same way.
- `review revoke <event>`: takes one of those back. Its exemplars are revoked and the one its
  choice replaced comes back. It does not touch your library; `marquee restore` does that.

Approve, pick, and rank also deploy the chosen poster unless given `--no-deploy`. Whenever
the exemplars change, a `taste_rebuild` job builds a new taste profile from them on the GPU
and swaps it in with one rename; `marquee taste rebuild [--force]` asks for one by hand.
`marquee taste status` shows the exemplars, the active profile, whether a rebuild is due, and
the agreement: how often you kept the AI's pick, counted only on runs made while the movie
had no exemplar of its own (a percentage appears from ten reviews). It also says how much
evidence the learned score correction would have; that correction is not trained yet.

Two settings decide what happens without you asking. `DEPLOY_POLICY` (default `off`) makes a
finished run deploy its pick by itself: `missing` only where the folder has no poster or the
poster there is Marquee's own, `all` everywhere, replacing your own posters too (each still
backed up). `RUN_NEW_MOVIES` (default `false`) makes the worker run every movie that has
never been run after each library sync. Once Marquee has written a poster,
`marquee taste seed --rescan` is refused: the library then holds Marquee's picks beside
yours, and the staged copies in `data/ml/seed/` stay the record of your own.

`marquee db upgrade` brings the database's schema up to the newest migration, and
`marquee db status` shows where it is (it exits non-zero when the schema is behind).

`marquee sync` copies every movie Radarr lists into the database, including the ones Radarr
still wants and has no file for, and records each movie folder's `poster.jpg`. It first
checks that every root folder Radarr reports maps to a folder Marquee can see, so a missing
share stops the sync instead of looking like an empty library. A poster is read and hashed
only when it is new or its size or modification time changed, so a repeat sync over a
network share only lists and stats. Movies Radarr no longer lists are kept and marked as
retired. Movies with a file get their director, studios, and tagline from TMDB once. A
poster Marquee wrote that you have since replaced by hand counts as yours again. The report
lists folders on disk that no Radarr movie points at, and movies whose folder was not found.
`--dry-run` does everything except write to the database, and `--json` prints the report as
JSON. The sync reads the library; it never writes to it.

The database stores paths as Radarr reports them. If Radarr sees your library at a
different path than Marquee does (Radarr in a container, for example), set
`RADARR_MOVIE_ROOTS` to the folders in `MOVIE_ROOTS` as Radarr sees them, in the same order.

`marquee worker` runs the job queue. It queues a library sync by itself every
`SYNC_INTERVAL_MINUTES` (default 360; 0 turns it off), runs GPU jobs in a child process that
keeps its models loaded between jobs and stops after `GPU_IDLE_SECONDS` (default 300) without
one or once it holds more than `GPU_CHILD_MAX_RSS_MB` of memory (default 4096), retries
failed jobs with a growing delay, and, after a crash or a restart, picks up the
jobs it was running. Only one worker runs at a time. `marquee jobs list`, `show`, `enqueue`,
`cancel`, and `retry` work on the queue from the command line; for example
`uv run marquee jobs enqueue gpu_selftest` checks that the GPU works in the child process.

`marquee api` serves a small HTTP API: `/health/live` and `/health/ready` for probes, and,
under `/api`, the movie list (`/api/movies`, with search, filters, sorting, and paging), one
movie, and its poster; runs (`POST /api/movies/{id}/run`, `POST /api/runs/batch`,
`/api/runs`, one run with its candidates, and each candidate's image), deploys
(`POST /api/runs/{id}/deploy`), the deployments and restoring one
(`POST /api/deployments/{id}/restore`); review (`/api/review`, `POST` and `GET`
`/api/runs/{id}/feedback`, `/api/feedback`, `POST /api/feedback/{id}/revoke`) and taste
(`/api/taste`, `POST /api/taste/rebuild`, `/api/taste/exemplars` and each one's image); a
summary of the installation (`/api/system`); the jobs
(`/api/jobs`, one job, its events, and a
feed of every job's events after a cursor), cancel and retry, `POST /api/sync` to queue a
sync, and `/api/worker`. Every `/api` request must carry the key in `API_KEY`, as
`Authorization: Bearer <key>` or `X-Api-Key: <key>`; with no key configured the API answers
503. The interactive documentation (`/docs`) is served only when `MARQUEE_ENVIRONMENT` is
`development`.

`marquee doctor` prints one line per check (Python, settings, data directory, backups,
models, model manifest, taste profile and whether it is the registered build, ONNX Runtime,
GPU, OCR, database and its schema, the worker and the queue, media roots, TMDB, Radarr, path
mapping) with `PASS`, `WARN`, or `FAIL`, and exits
non-zero if anything fails. `--json`
prints the same report as JSON. It never writes to a media folder and never prints a secret.

Use `uv sync --extra nvidia` (or `--extra cpu`) whenever you sync: a plain `uv sync` removes
the extra's packages.

## The web interface

The web interface is a SvelteKit app in `frontend/` (Node 26, through
[fnm](https://github.com/Schniz/fnm) and `frontend/.node-version`). The browser signs in with
the API key and never holds it: the web server keeps the key and forwards `/api/*` to the API
itself.

```bash
cd frontend
npm ci                      # also generates the API types from frontend/openapi.json
cp .env.example .env        # then set MARQUEE_API_URL and MARQUEE_API_KEY (the API's API_KEY)
npm run dev                 # http://localhost:3166, localhost only
```

To serve the built app instead, `npm run build`, then
`HOST=127.0.0.1 ORIGIN=http://127.0.0.1:3000 node --env-file=.env build` (port 3000 by
default; without `HOST` it listens on every address; `ORIGIN` is the address the browser
uses, which the server needs for its same-origin check when no proxy sets it). The `.env`
file is gitignored; `frontend/.env.example` names both settings with placeholders.

The pages: **Library** (the grid, and each movie's poster, latest run, and poster history,
where any write can be put back), **Review** (the runs waiting for your answer; the number
waiting is in the navigation), **Taste** (the profile, the agreement, recent feedback, and the
exemplars), **Activity** (the job queue), and **System** (what the installation is and how it
is set, read-only). On a run's review page each card is one design, its variants folded
under it; keep the pick, use another design, put them in your order with a hate pile (with
the buttons on each card, or by dragging), or refuse them all. "Write the chosen poster to
the movie's folder" is on by default; the page asks first before anything replaces your own
poster, and Undo takes an answer back without touching the folder (put the poster back from
the movie's poster history).

## Development

```bash
uv run ruff check .
uv run ruff format --check .
uv run lint-imports                 # layering contracts
uv run pytest                       # everything
uv run pytest -m "not gpu and not models"   # what CI runs
uv run marquee api-schema --check   # frontend/openapi.json matches the API
```

After changing an API route or response, run `uv run marquee api-schema` and commit
`frontend/openapi.json`: the web interface's types are generated from it. The frontend's
checks, as CI runs them:

```bash
cd frontend
npm ci
npm run check               # types (generates them first)
npm run lint                # prettier and eslint
npm run test:unit           # vitest
npm run build
npx playwright install chromium     # once; on Linux it may need system libraries
npm run test:e2e            # the built app against a fake API in frontend/e2e
```

Tests that need model files are marked `models`, tests that need a GPU `gpu`, and tests that
need PostgreSQL `db`. Database tests run in a throwaway `marquee_test_<random>` database
that the suite creates and drops; it finds the server through `MARQUEE_TEST_DATABASE_URL`,
or else the `POSTGRES_*` values in your `.env` (the only values the suite reads from it), and
skips the database tests when there is neither. Apart from that, the test suite never reads
your `.env` and never touches the real `data/` directory. `uv run marquee db revision -m
"<what it does>"` writes a new migration from the models; read it before committing it.

## Layout

```
marquee/        the application package (see docs/architecture.md)
tests/          mirrors marquee/ one-to-one
data/           runtime state: models, caches, runs, backups (not committed)
docs/           architecture and other documentation
frontend/       the web interface (SvelteKit)
_salvage/       code from the earlier codebase still waiting to be re-homed
compose.yaml    PostgreSQL for development, and the application image's services
Dockerfile      the container image (targets runtime and export)
```

See [docs/architecture.md](docs/architecture.md) for what each package is for, the layering
rule, and how to add a module.

## License

[GNU GPL v3](LICENSE).
