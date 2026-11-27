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
> its system page shows how the installation is set. A learned correction to the score turns
> on once there is enough feedback. All of it runs as a Docker Compose stack that dumps its
> database on a schedule and deletes old runs by written rules.

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

## Running Marquee with Docker

Docker is the supported way to run Marquee: a Docker Compose stack on a Linux host with an
NVIDIA GPU. The stack is five services: PostgreSQL (`db`), a one-shot schema migration
(`migrate`), the HTTP API (`api`), the job worker, which runs the engine on the GPU and writes
posters (`worker`), and the web interface (`web`), the only one published on the network.
Each long-running service has a health check and restarts by itself unless you stop it.

### What you need

- A Linux x86_64 host with an NVIDIA GPU and its driver. CUDA comes inside the image; only
  the driver is needed on the host.
- Docker Engine with the Compose plugin, and the
  [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
  configured for Docker (`sudo nvidia-ctk runtime configure --runtime=docker`, then restart
  Docker). `docker run --rm --gpus all ubuntu nvidia-smi` must list your GPU.
- Your movie library on the host, laid out as Radarr manages it, writable by the user the
  containers run as: 1000:1000 unless you choose other ids (see "If your library's owner is
  not user 1000"). On a network share, mount it with that owner.
- Radarr's URL and API key, and a TMDB API read access token (v4).
- About 25 GB of disk for the images and their build, and 2 GB for models and data to start.

### Install

```bash
# 1. Get the code.
git clone <this repository's URL> marquee
cd marquee

# 2. Settings. Every setting is listed, with a comment, in .env.example.
cp .env.example .env
#    Then edit .env:
#    - COMPOSE_PROFILES=app (uncomment it), so `docker compose up -d` starts the stack
#    - POSTGRES_PASSWORD and API_KEY: long random values, for example from
#      python3 -c "import secrets; print(secrets.token_urlsafe(32))"
#    - MOVIE_LIBRARY_HOST_PATH: the library's folder on this host (it may hold several
#      Radarr root folders, such as 1080p/ and 4K/)
#    - RADARR_MOVIE_ROOTS: that same folder as Radarr sees it, if Radarr sees it at another
#      path (Radarr in a container, or on another machine)
#    - TMDB_READ_ACCESS_TOKEN, RADARR_URL, RADARR_API_KEY
#    - MARQUEE_WEB_ORIGIN: the address you will type in the browser, for example
#      http://marquee.example.lan:3000 (MARQUEE_WEB_PORT changes the port)
#    The data folder is ./data unless MARQUEE_DATA_HOST_PATH says otherwise; if you move it,
#    create the folder first and give it to the containers' user (`sudo chown 1000:1000
#    <folder>`, or the ids in MARQUEE_UID and MARQUEE_GID).

# 3. Build the images (marquee:runtime, about 7 minutes the first time, and marquee:web).
docker compose build

# 4. Build the models into the data folder (about 3 minutes; it downloads about 1.5 GB and
#    builds its own image, marquee:export, the first time).
docker compose run --rm setup

# 5. Start the stack. The migration creates the database schema, then the API, the worker,
#    and the web app start. The worker syncs the movie list from Radarr as soon as it starts.
docker compose up -d
docker compose ps                    # wait until api, worker, and web say (healthy)

# 6. Build the first taste profile from the posters already in your library, then make them
#    the first exemplar records (this needs the movie list, so let the first sync finish:
#    `docker compose exec worker marquee jobs list` shows it).
docker compose exec worker marquee taste seed --dry-run   # what would be seeded; writes nothing
docker compose exec worker marquee taste seed
docker compose exec worker marquee taste import-seed

# 7. Check each service.
docker compose exec worker marquee doctor
docker compose exec api marquee doctor
```

Then open `MARQUEE_WEB_ORIGIN` in your browser and sign in with `API_KEY`.

Any `marquee` command runs in the worker's container while the stack is up:
`docker compose exec worker marquee <command>` (for example `marquee sync`, or
`marquee run "Die Hard"`). When the stack is down, `docker compose run --rm marquee marquee
<command>` runs one in a container of its own.

**The first run of a movie takes about a minute** instead of about 20 seconds: the CUDA
driver compiles GPU kernels for your card on first use. It keeps them in the data folder
(`cache/cuda-jit`), so this happens once, not on every start.

**A small library.** `marquee taste seed` builds the first taste profile from however many
posters you picked by hand, but rebuilding the profile from your feedback needs at least 50
active positive exemplars (`TASTE_SEEDING_MIN_POSTERS`). With fewer, Marquee ranks with the
seeded profile and records every answer you give; each poster you choose in a review adds a
positive, and the first rebuild happens once there are 50.

### If your library's owner is not user 1000

The application containers run as user and group 1000 by default, and that user must be able
to write in the movie folders (the worker writes posters) and own the data folder. If your
library belongs to another user, set `MARQUEE_UID` and `MARQUEE_GID` in `.env` to that user's
ids (`id -u` and `id -g` as that user), give the data folder to the same ids (`sudo chown
<uid>:<gid> data`), and build the images again: `docker compose build` and `docker compose
build setup`. The web container runs as Node's own user and touches none of your folders.

### Running it

- `docker compose ps` shows each service and its health; `docker compose logs -f worker`
  follows one service's log (times are UTC). Each service keeps at most five log files of
  10 MB; older lines are dropped.
- `docker compose stop` stops the stack and `docker compose up -d` starts it again; a job
  that was running goes back to the queue and runs again. **Never run `docker compose down
  -v`:** `-v` deletes the database volume, and with it every run, answer, and exemplar.
- To update, see "Updating and going back" below.
- Everything Marquee keeps on disk is in the data folder: models, the taste profile and the
  exemplars' images, every run, a backup of every poster it replaced, and the database
  dumps. The database itself lives in the Docker volume `<project>_db-data`.
- Only one worker runs at a time. A second one, in a container or on the host, finds the
  first one's lock and exits.
- The web app is published on `MARQUEE_WEB_BIND:MARQUEE_WEB_PORT` (every address, port 3000,
  by default), over plain HTTP; "Serving Marquee over HTTPS" below puts it behind a reverse
  proxy. The API is not published; to reach it from the host itself, uncomment the `ports`
  lines of the `api` service in `compose.yaml` (loopback only).
- In the stack the services run in production mode: the API serves no interactive
  documentation, and `marquee doctor` fails, rather than warns, when the engine falls back
  to the CPU, when there is no GPU, or when the worker cannot write to the library. It
  checks the library without writing to it.

See [docs/architecture.md](docs/architecture.md#the-compose-stack) for what each service
mounts and receives.

### Security

- **What is reachable.** Only the web app, on `MARQUEE_WEB_BIND:MARQUEE_WEB_PORT`. The API is
  not published, and the database is published on this machine's loopback address only (for
  `marquee` commands run on the host).
- **The key.** Every API request needs `API_KEY`, and you sign in to the web app with it. The
  web server keeps the key and forwards your requests to the API itself; the browser never
  holds it. No secret is built into an image or written to a log.
- **Sessions.** Signing in sets a signed cookie that lasts 30 days, and the server keeps no
  list of sessions, so signing out forgets the cookie in that browser only. Changing `API_KEY`
  (then `docker compose up -d`) ends every session.
- **Two limiters.** Five wrong keys at sign-in within a minute from one address, and that
  address waits until the minute has passed. The API answers 429 to an address that sent more
  than ten requests with a missing or wrong key within a minute, until the minute has passed;
  the right key is never slowed. Both are held in memory and forgotten on a restart.
- **The pages' policy.** Every page carries a Content-Security-Policy: the browser runs only
  the app's own scripts and loads scripts, styles, images, and data from this site alone, and
  no other site can frame it. Every answer also says `nosniff`, `X-Frame-Options: DENY`, and
  `Referrer-Policy: same-origin`, and over HTTPS, `Strict-Transport-Security`.
- **Plain HTTP** is for a network you trust: over it, the key you type at sign-in and the
  session cookie cross the network unencrypted.

### Serving Marquee over HTTPS

Marquee does not terminate TLS itself. To serve it over HTTPS, put a reverse proxy that does
in front of the web app, and set in `.env`:

- `MARQUEE_WEB_ORIGIN=https://marquee.example.org`, the address you open. The session cookie
  is then `Secure`, and every answer carries `Strict-Transport-Security: max-age=31536000`, so
  a browser that has seen it uses HTTPS for that name for a year.
- `MARQUEE_WEB_BIND=127.0.0.1` when the proxy runs on the same host, so that only the proxy
  reaches the web app.
- `MARQUEE_WEB_ADDRESS_HEADER=x-forwarded-for` and `MARQUEE_WEB_XFF_DEPTH=1` (the number of
  trusted proxies in front of Marquee), so the sign-in limiter counts each visitor on their
  own rather than every visitor as the proxy. With a header set, a request that does not come
  through the proxy cannot sign in.

Then `docker compose up -d` recreates the web container. With [Caddy](https://caddyserver.com/)
on the same host, which obtains the certificate and sets `X-Forwarded-For` itself, the whole
`Caddyfile` is:

```
marquee.example.org {
	reverse_proxy 127.0.0.1:3000
}
```

Over plain HTTP, without a proxy, the API key and the session cookie cross the network
unencrypted.

### Updating and going back

```bash
git pull
docker compose run --rm marquee marquee db backup   # a dump first: the way back
docker compose pull db                              # PostgreSQL's newest 19.x release
docker compose --profile app up -d --build
```

`up -d --build` builds the images again and recreates what changed; the migration brings the
database's schema up to date before the API and the worker start. The `manual-…` dump the
second line makes is never deleted by Marquee.

**Going back to older code after an update migrated the database.** Checking out the older
code is not enough: its migration does not know the newer schema, so the API and the worker
would not start. And `marquee db restore` always brings a dump up to the running code's
newest schema. So restore the dump you made before the update with the older code itself:

```bash
docker compose --profile app stop api worker web
git checkout <the version you updated from>
docker compose --profile app build
docker compose run --rm marquee marquee db restore manual-<time>-<revision>.dump --yes
docker compose --profile app up -d
```

The dump's schema is the older code's own, so it loads as it is. Everything the database
recorded after the dump is gone (runs, answers, deployments since); files written since stay
in the data folder and the library, and `marquee doctor` and `marquee deployments verify` say
what no longer matches. This needs a version with `marquee db restore` (this one and later).
There is no downgrade command.

**Moving a pin.** The base images are pinned by digest in `Dockerfile` (`PYTHON_IMAGE`,
`UV_IMAGE`) and `frontend/Dockerfile` (`NODE_IMAGE`), and every model input in
`marquee/model_fetch.py` (a Hugging Face revision, a commit, and a SHA-256 for each
download). To move an image, put the new tag's digest (`docker buildx imagetools inspect
<image>:<tag>`) in its line and build again. To move a model, change its revision or address
and its SHA-256, build `setup` again, and run `docker compose run --rm setup marquee models
fetch --force <item>`. A changed model is a different model: `marquee doctor`'s manifest
check must pass again, and measurements made with the old model no longer describe it.

### Operations notes

- **`docker kill` is not a crash** to Docker's restart policy: it is taken as a deliberate
  stop, and the service stays down until `docker compose up -d`. A process that dies on its
  own is restarted.
- **Start a stopped service with `docker compose up -d`,** not `docker compose start
  <service>`: `start` runs the one-shot `migrate` again as well, while `up -d` starts what is
  stopped and leaves the rest alone.
- **What the worker's health check proves:** that a worker holds the queue's lock and the
  database answers. It does not prove that the worker makes progress: a worker stuck inside a
  job still holds the lock. The Activity page shows what is running and how far it has got.

### Backups and restore

**What is backed up, where, and how often.** The worker dumps the whole database (movies,
runs and their candidates, deployments, your answers, exemplar records, taste profiles'
records, evaluations, jobs) with PostgreSQL's `pg_dump` into the data folder, under
`backups/db/`, as `marquee-<UTC time>-<schema revision>.dump`. It makes the first dump
when it starts, then one every `DB_BACKUP_INTERVAL_HOURS` (default 24; 0 turns it off), and
keeps the newest `DB_BACKUP_KEEP` (default 14) of these. A dump counts only once it has been
read back; each is readable by its owner only. The System page shows the newest dump and
lists it under "Needs attention" when it is older than two intervals, or when the last
backup job failed; `marquee doctor` reports its age too.

- `marquee db backup` makes a dump now, in the command's own process: no worker or job is
  needed, so it works while the stack is stopped (the database must be running) and before
  an update's migration. Its name starts with `manual-`; Marquee never deletes one.
- `marquee db backups` lists every dump, newest first.

**What to copy off the machine.** The dumps hold the database; the files the database
refers to are beside them in the data folder: the exemplars' images and the taste profiles
(`ml/`), every poster backup (`backups/posters/`), and the runs. To be able to rebuild this
installation elsewhere, copy the whole data folder off the machine, `backups/` and `ml/`
at least. A dump alone brings back the records, not those files. Marquee does not copy or
encrypt anything off the machine for you.

**Restoring.** A restore replaces everything in the database with the dump. It is a
command, never a job or a button, and it refuses to start while anything else uses the
database, so stop the API, the worker, and the web app first (the database keeps running):

```bash
# 1. Stop everything that uses the database.
docker compose --profile app stop api worker web
# 2. Pick a dump.
docker compose run --rm marquee marquee db backups
# 3. Restore it (a file name in backups/db/, or a path inside the data folder as /data/...).
docker compose run --rm marquee marquee db restore marquee-20261127T010000Z-0007.dump --yes
# 4. Start the stack again and check it.
docker compose --profile app up -d
docker compose exec worker marquee doctor
docker compose exec worker marquee deployments verify
```

Before it drops anything the restore dumps the database as it is to
`pre-restore-<time>.dump` (the newest three are kept), so a restore can itself be undone.
It loads the dump in one transaction: if anything fails, the database is left as it was.
If the dump is from an older schema, it then brings the schema up to the newest one. It
refuses without `--yes`, when the file is not a dump, when a worker is running, or while
another connection is open. A restore does not undo files newer than the dump: runs made
since, the taste profile and exemplar images in the data folder, and posters written to the
library since. `marquee doctor` says whether the taste profile on disk is the one the
records register (if not, `marquee taste rebuild --force` builds it from the restored
exemplars), and `marquee deployments verify` compares the library's posters with the
restored deployments.

On a new machine: follow the install steps up to building the images, copy the old data
folder into place, start only the database (`docker compose up -d db`), restore the dump as
in step 3, then start the stack.

### What Marquee deletes by itself

Once a day (`RETENTION_INTERVAL_HOURS`, default 24; 0 turns it off) the worker deletes what
has gone stale, by these rules. The first pass comes one whole interval after the worker
first runs with it, never at once, so you can look first: `marquee retention --dry-run`
lists, rule by rule, everything that would go, with counts and sizes; `marquee retention`
does it now. The System page shows what the last pass freed.

- **Finished jobs** (succeeded, failed, or cancelled) more than `RETENTION_DAYS` (default 90)
  ago, with their events. Never a waiting or running job, never one batch job without the
  rest of its batch, never the jobs of a blind evaluation, and never a schedule's last job.
- **Runs** older than `RETENTION_DAYS`, with their candidates and folders, but never a
  movie's newest run or newest completed run, never a run a deployment or one of your
  answers came from, and never the newest evaluation's runs.
- **Run folders no run belongs to**, a week after anything in them last changed.
- **Poster thumbnails** not changed for 30 days, and **cached embeddings** not changed for
  `RETENTION_DAYS`; both are made again when needed.
- **Leftovers** a day old that no waiting or running job needs: what a deploy fetched, a
  rebuild's staging folder, a blind run's profile, and temporary files.
- **Old copies of taste profiles and residuals** beyond the active one and the newest 20
  (the seed profile's copy is kept too); their records stay.

Never deleted: poster backups, deployments, your answers, exemplars and their images,
evaluations and their results, the seed posters, the models, the GPU kernel cache, the
database dumps (beyond the counts above), anything outside the data folder, and anything in
it not named above. Retention refuses to run if the data folder and the movie library
overlap. Apart from retention, Marquee also deletes on its own: each movie's older blind
evaluation run when a new evaluation runs it, profile copies beyond the newest five when a
rebuild publishes one (the seed's and the active one stay), residual files beyond the newest
five, and what a deploy fetched once its write is recorded.

## Development setup (on the host)

Marquee is developed on the host, with only PostgreSQL in Docker. You need:

- Linux x86_64.
- [uv](https://docs.astral.sh/uv/) (it installs and manages Python 3.12 for the project).
- Docker with Compose, for PostgreSQL.
- The system OpenGL library (`libGL.so.1`: `mesa-libGL` on Fedora, `libgl1` on Debian and
  Ubuntu). PaddleOCR requires the full OpenCV build, which links against it.
- An NVIDIA GPU. CUDA and cuDNN come from Python wheels; only the driver is needed.

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
the LAION aesthetic head, and the SCRFD face detector. Everything it downloads is pinned and
must match a SHA-256 in `marquee/model_fetch.py` (the CLIP files at a fixed revision, the
DINOv2 code at a fixed commit and its weights, the YOLO weights, the OCR folders, the aesthetic
head, the face detector), so a fresh machine builds the models every measurement was made
with. Each build is checked before it is put in place, and `data/models/manifest.json`
records every model's size, SHA-256, and origin. Their licences are in
[docs/third-party.md](docs/third-party.md).
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
`pipeline.log`, the run archive `pipeline_run.json`, and `large/`, a copy up to 1500 px wide
of each ranked poster for looking at one closely in the web app. `marquee runs list` and
`marquee runs show <id>` show them, and `marquee runs batch --scope never-run|no-poster|all`
queues runs of many movies at once (`--limit N` caps it). A movie can be run when Radarr has
its file and the sync has fetched its TMDB details.

`marquee deploy <run id>` writes the run's pick into the movie's folder as `poster.jpg`;
`--candidate N` deploys another candidate of the run instead. The worker fetches TMDB's
original-size file of the candidate (it must be a JPEG, and it is never converted), backs up
the `poster.jpg` already there to `data/backups/`, writes the new file under a temporary name
and renames it into place, reads it back to check it, and records the write.
`marquee deployments verify` checks, reading only, that each movie's newest deployment is
still what is in its folder (or reports it replaced since, gone, or unreadable when the share
does not answer), and that every backup a deployment names exists and holds the poster it
should; it exits 1 when a backup is missing or damaged, since that is the way back. A poster
you replaced is not a fault (the sync hands it back to you), and nothing is written back by
itself. `marquee deployments list` shows every write and what a restore of it would put back, and
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
had no exemplar of its own (a percentage appears from ten reviews), and the residual's state.

**The residual** is the second way Marquee learns: a small, bounded correction to the score,
learned from your answers (never from the posters you picked by hand, which stay a clean
measurement). Every rebuild trains it for the new profile, and it turns on by itself only when
it agrees with your order better than the plain score on movies it did not train on (at least
25 movies and 200 pairs of answers, and 2 points better). Until then every training is
recorded and says what is still missing. `marquee taste residual status` says whether runs use
it and why; `marquee taste residual train` trains it now. To switch it off, set
`SCORER=weighted`: runs then always rank with the plain score.

**A blind evaluation** measures how good the picks are. `marquee taste evaluate` runs every
movie whose poster you chose again, each without its own poster in the taste profile, and
looks for your poster among the candidates: was it the pick, how far down did it land (by
design, as the review page shows them), did a gate remove it and why, or is it not among
TMDB's posters at all. The posters you picked by hand are the headline; ones you chose in a
review are counted apart. It changes no poster, no answer, and no profile, takes about 20 s a
movie on the GPU, and keeps only each movie's newest evaluation run. `marquee taste
evaluation [ID] [--results]` prints the summary, and one line per movie. Those runs never
appear in the review list and nothing is deployed from them. `EVAL_MATCH_MIN_COSINE` (0.95)
is how alike a candidate must be to your poster to count as it when no TMDB file name says so.

Two settings decide what happens without you asking. `DEPLOY_POLICY` (default `off`) makes a
finished run deploy its pick by itself: `missing` only where the folder has no poster, or
over the poster the policy itself deployed last, never over one you chose (your own file, a
design you picked in a review, a poster you put back or replaced by hand); `all` everywhere,
replacing your own posters too (each still backed up). `RUN_NEW_MOVIES` (default `false`) makes the worker run every movie that has
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
`SYNC_INTERVAL_MINUTES` (default 360; 0 turns it off), a database dump every
`DB_BACKUP_INTERVAL_HOURS` and a retention pass every `RETENTION_INTERVAL_HOURS` (see
"Backups and restore" and "What Marquee deletes by itself"), retries a sync whose share or
Radarr did not answer in time, runs GPU jobs in a child process that
keeps its models loaded between jobs and stops after `GPU_IDLE_SECONDS` (default 300) without
one or once it holds more than `GPU_CHILD_MAX_RSS_MB` of memory (default 4096), retries
failed jobs with a growing delay, and, after a crash or a restart, picks up the
jobs it was running. Only one worker runs at a time. `marquee jobs list`, `show`, `enqueue`,
`cancel`, and `retry` work on the queue from the command line; for example
`uv run marquee jobs enqueue gpu_selftest` checks that the GPU works in the child process.

`marquee api` serves a small HTTP API: `/health/live` and `/health/ready` for probes, and,
under `/api`, the movie list (`/api/movies`, with search, filters, sorting, and paging), one
movie, and its poster; runs (`POST /api/movies/{id}/run`, `POST /api/runs/batch`,
`/api/runs`, one run with its candidates and what each ranked score is made of, and each
candidate's image and large copy), deploys
(`POST /api/runs/{id}/deploy`), the deployments and restoring one
(`POST /api/deployments/{id}/restore`); review (`/api/review`, `POST` and `GET`
`/api/runs/{id}/feedback`, `/api/feedback`, `POST /api/feedback/{id}/revoke`) and taste
(`/api/taste`, `POST /api/taste/rebuild`, `/api/taste/exemplars` and each one's image,
`POST /api/taste/evaluations`, the evaluations, one, and its results); a
summary of the installation (`/api/system`); the jobs
(`/api/jobs`, one job, its events, and a
feed of every job's events after a cursor), cancel and retry, `POST /api/sync` to queue a
sync, and `/api/worker`. Every `/api` request must carry the key in `API_KEY`, as
`Authorization: Bearer <key>` or `X-Api-Key: <key>`; with no key configured the API answers
503. The interactive documentation (`/docs`) is served only when `MARQUEE_ENVIRONMENT` is
`development`.

`marquee doctor` prints one line per check (Python, settings, data directory, backups,
models, model manifest, taste profile and whether it is the registered build, ONNX Runtime,
GPU, OCR, the CUDA kernel cache's size against the driver's limit, database and its schema,
the backup tools, the newest database dump's age, the worker and the queue, media roots,
TMDB, Radarr, path mapping) with `PASS`, `WARN`, or
`FAIL`, and exits non-zero if anything fails. `--json` prints the same report as JSON. It
never writes to a media folder (it judges whether one is writable from the mount's options
and the folder's permission bits) and never prints a secret. `--role api` or `--role worker`
checks what that service of the Compose stack needs (the stack sets `MARQUEE_ROLE` for
each): the API runs no GPU work and only reads the library, the worker must be able to write
it. With `MARQUEE_ENVIRONMENT=production`, as in the stack, a fall-back to the CPU, a missing
GPU, a library the worker cannot write, and (for the worker) missing or old backup tools
are failures instead of warnings.
`marquee worker --health` is the worker service's health check: it runs nothing and exits 0
only when a worker holds the queue's lock and the database answers.

Use `uv sync --extra nvidia` (or `--extra cpu`) whenever you sync: a plain `uv sync` removes
the extra's packages.

## The web interface

The web interface is a SvelteKit app in `frontend/` (Node 26, through
[fnm](https://github.com/Schniz/fnm) and `frontend/.node-version`). The browser signs in with
the API key and never holds it: the web server keeps the key and forwards `/api/*` to the API
itself. In the Compose stack it runs from its own image (`frontend/Dockerfile`, built by
`docker compose build`); the steps below are for development.

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
waiting is in the navigation), **Taste** (the profile, the agreement, the residual, the blind
evaluation with a button that runs one and a page of every poster's result, recent feedback,
and the exemplars), **Activity** (the job queue), and **System** (what the installation is and how it
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
compose.yaml    PostgreSQL for development, and the whole stack (profile app)
Dockerfile      the application image (targets runtime and export)
frontend/Dockerfile   the web interface's image
```

See [docs/architecture.md](docs/architecture.md) for what each package is for, the layering
rule, and how to add a module.

## License

Marquee is free software under the [GNU General Public License, version 3 only](LICENSE).

The models `marquee models fetch` downloads come under their own licences, listed with
everything else Marquee uses in [docs/third-party.md](docs/third-party.md). **The YOLO11
weights are AGPL-3.0, and the face detector (SCRFD, from InsightFace's `buffalo_sc` pack) is
published for non-commercial research use only, so a Marquee installation with the default
models is for personal, non-commercial use.** NVIDIA's CUDA libraries are installed on your
machine under NVIDIA's terms. The project publishes no image and no model: you build both
on your own machine.

Movie details and posters come from [TMDB](https://www.themoviedb.org/). This product uses
TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB. The
web app's System page shows TMDB's logo with this notice, as TMDB's terms ask.
