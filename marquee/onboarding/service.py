"""Onboarding logic: state, progress, stratified sampler, taste test (design 20).

Kept ML-free and DB-free so it is unit-testable: the route layer owns the DB
queries (for the sampler) and the job creation (for the profile rebuild + head
train). File operations (state, image staging, starter-profile copy) and the
pure sampling/progress logic live here.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from collections import defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from marquee.core.pipeline_config import pipeline_settings
from marquee.ml import feedback_store

logger = logging.getLogger(__name__)

PATH_LIBRARY = "library"
PATH_TASTE_TEST = "taste_test"


# ---------------------------------------------------------------------------
# State (runtime, gitignored)
# ---------------------------------------------------------------------------


def _default_state() -> dict:
    return {"path": None, "started_at": None, "complete": False, "completed_at": None}


def read_state() -> dict:
    path = Path(pipeline_settings.ONBOARDING_STATE_PATH)
    if not path.exists():
        return _default_state()
    try:
        return {**_default_state(), **json.loads(path.read_text())}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("ONBOARDING | unreadable state (%s) — resetting", exc)
        return _default_state()


def write_state(state: dict) -> None:
    path = Path(pipeline_settings.ONBOARDING_STATE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    os.replace(tmp, path)


def start(path: str) -> dict:
    """Begin (or switch) the rank test on the given path; stamps started_at once."""
    if path not in (PATH_LIBRARY, PATH_TASTE_TEST):
        raise ValueError(f"unknown onboarding path {path!r}")
    state = read_state()
    if not state.get("started_at"):
        state["started_at"] = datetime.now(UTC).isoformat()
    state["path"] = path
    state["complete"] = False
    state["completed_at"] = None
    write_state(state)
    return state


def mark_complete() -> dict:
    state = read_state()
    state["complete"] = True
    state["completed_at"] = datetime.now(UTC).isoformat()
    write_state(state)
    return state


def reset() -> None:
    write_state(_default_state())


# ---------------------------------------------------------------------------
# Progress (derived from the v3 ranking events written since the test started)
# ---------------------------------------------------------------------------


def ranked_movie_keys(since_iso: str | None) -> set:
    """Distinct movies with a v4 ranking event at/after ``since_iso``."""
    keys: set = set()
    for row in feedback_store.read_all():
        if row.get("type") != "ranking" or row.get("v") != 4:
            continue
        if since_iso and (row.get("ts") or "") < since_iso:
            continue
        key = row.get("movie_id") if row.get("movie_id") is not None else row.get("title")
        if key is not None:
            keys.add(key)
    return keys


def progress() -> dict:
    state = read_state()
    count = len(ranked_movie_keys(state.get("started_at")))
    minimum = pipeline_settings.ONBOARDING_RANK_TEST_MIN
    goal = pipeline_settings.ONBOARDING_RANK_TEST_GOAL
    maximum = pipeline_settings.ONBOARDING_RANK_TEST_MAX
    return {
        "ranked": count,
        "min": minimum,
        "goal": goal,
        "max": maximum,
        "can_complete": count >= minimum and not state["complete"],
        "at_goal": count >= goal,
        "at_max": count >= maximum,
        "complete": state["complete"],
        "path": state["path"],
        "started_at": state["started_at"],
    }


def status() -> dict:
    """Full onboarding status for the UI: data state + rank-test progress."""
    prog = progress()
    return {
        "profile_present": profile_present(),
        "head_active": Path(pipeline_settings.LEARNED_HEAD_PATH).exists(),
        "taste_test_available": taste_test_available(),
        "needs_onboarding": not (prog["complete"] and profile_present()),
        **prog,
    }


# ---------------------------------------------------------------------------
# Data-state probes
# ---------------------------------------------------------------------------


def profile_present() -> bool:
    return Path(pipeline_settings.TASTE_PROFILE_PATH).exists()


def taste_test_available() -> bool:
    return load_taste_test() is not None


def ensure_starter_profile() -> bool:
    """Copy the shipped starter profile into place if no profile exists yet, so
    the pipeline never hard-fails mid-onboarding. Returns True if it copied."""
    profile = Path(pipeline_settings.TASTE_PROFILE_PATH)
    if profile.exists():
        return False
    seed = Path(pipeline_settings.ONBOARDING_SEED_PROFILE_PATH)
    if not seed.exists():
        logger.info("ONBOARDING | no starter profile seed shipped (%s)", seed)
        return False
    profile.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(seed, profile)
    logger.info("ONBOARDING | seeded starter taste profile from %s", seed.name)
    return True


# ---------------------------------------------------------------------------
# Stratified sampler (pure — the route supplies the movie rows)
# ---------------------------------------------------------------------------


def stratified_sample(movies: list[tuple[int, list[str] | None]], n: int) -> list[int]:
    """Pick up to ``n`` movie ids spread across genres (round-robin by primary
    genre), so the rank test isn't dominated by one genre. Deterministic given
    input order."""
    if n <= 0 or not movies:
        return []
    buckets: dict[str, list[int]] = defaultdict(list)
    for movie_id, genres in movies:
        primary = (genres[0] if genres else "_ungenred").strip() or "_ungenred"
        buckets[primary].append(movie_id)
    queues = [deque(ids) for ids in buckets.values()]
    out: list[int] = []
    while queues and len(out) < n:
        for queue in list(queues):
            if not queue:
                queues.remove(queue)
                continue
            out.append(queue.popleft())
            if len(out) >= n:
                break
    return out


# ---------------------------------------------------------------------------
# Bundled taste test
# ---------------------------------------------------------------------------


def _taste_test_dir() -> Path:
    return Path(pipeline_settings.ONBOARDING_TASTE_TEST_DIR)


def load_taste_test() -> dict | None:
    """Load the bundled taste-test manifest, or None if no usable bundle ships.

    Manifest shape::

        { "model_name": "clip-vit-b-32",
          "movies": [ { "id": "tt_inception", "title": "Inception",
                        "year": 2010, "genres": ["Science Fiction"],
                        "posters": [ { "file": "inception_a.jpg",
                                       "normalized_features": { ... } }, ... ] } ] }
    """
    manifest = _taste_test_dir() / "manifest.json"
    if not manifest.exists():
        return None
    try:
        data = json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("ONBOARDING | unreadable taste-test manifest (%s)", exc)
        return None
    if not data.get("movies"):
        return None
    return data


def taste_test_movies() -> list[dict]:
    """The taste-test movies as the UI needs them (poster URLs, no features)."""
    data = load_taste_test()
    if not data:
        return []
    movies = []
    for movie in data["movies"]:
        movies.append(
            {
                "id": movie["id"],
                "title": movie.get("title"),
                "year": movie.get("year"),
                "genres": movie.get("genres") or [],
                "posters": [
                    {"file": p["file"], "url": taste_test_poster_url(p["file"])}
                    for p in movie.get("posters", [])
                ],
            }
        )
    return movies


def taste_test_poster_url(file: str) -> str:
    return f"/api/onboarding/taste-test/posters/{file}"


def taste_test_image_path(file: str) -> Path | None:
    """Resolve a bundled poster file to its on-disk path, confined to the bundle."""
    base = (_taste_test_dir() / "images").resolve()
    candidate = (base / file).resolve()
    if not str(candidate).startswith(str(base)) or not candidate.is_file():
        return None
    return candidate


def _stage_image(source: Path, dest_dir: Path) -> str | None:
    """Copy an image into a folder with a collision-free name; return that name."""
    if not source.is_file():
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem, suffix = source.stem, source.suffix
    target = dest_dir / source.name
    counter = 2
    while target.exists():
        target = dest_dir / f"{stem} - {counter}{suffix}"
        counter += 1
    shutil.copy2(source, target)
    return target.name


def _remove_prior_taste_test_event(movie_id: str) -> None:
    """Drop any earlier taste-test ranking for this movie so a re-rank replaces
    it (keeps the distinct-movie progress count and exemplar set clean)."""
    for row in feedback_store.read_all():
        if (
            row.get("type") == "ranking"
            and row.get("v") == 4
            and row.get("source") == "taste_test"
            and row.get("movie_id") == movie_id
        ):
            removed = feedback_store.remove_event(row.get("event_id"))
            for removed_row in removed:
                for name in removed_row.get("favorites_exemplars") or []:
                    _unstage(pipeline_settings.TRAINING_DATA_DIR, name)
                for name in removed_row.get("negatives_added") or []:
                    _unstage(pipeline_settings.NEGATIVE_DATA_DIR, name)


def _unstage(directory: Path | str, name: str) -> None:
    target = Path(directory) / name
    if target.is_file():
        target.unlink()


def taste_test_rank(
    movie_id: str,
    order: list[str],
    hated: list[str],
) -> dict:
    """Record one bundled taste-test movie's ranking as a v4 event (design 30).

    A taste-test movie never runs the real pipeline — there's no model
    prediction to correct, just the manifest's arbitrary poster listing
    order, used as the baseline the inversion trainer needs *some* numeric
    key for. Stages the top slice of the final order into the positive
    training folder, and *every* hated image into the negative folder
    unconditionally (no rank-gate like the live path's hard-negative mining —
    ``pipeline_rank`` is always None here, so that gate could never fire
    anyway). A later ``rebuild_profile`` consolidates staged images into
    Layer A. The v4 event's candidate features come from the bundled
    manifest, so the pairwise trainer reads it exactly like a live rank event.
    """
    data = load_taste_test()
    if not data:
        raise ValueError("no taste-test bundle is available")
    movie = next((m for m in data["movies"] if m["id"] == movie_id), None)
    if movie is None:
        raise ValueError(f"{movie_id!r} is not a taste-test movie")

    features = {p["file"]: p.get("normalized_features") for p in movie.get("posters", [])}
    baseline_rank = {p["file"]: i + 1 for i, p in enumerate(movie.get("posters", []))}
    valid = set(features)

    order_files = list(dict.fromkeys(f for f in order if f in valid))
    hated_files = list(dict.fromkeys(f for f in hated if f in valid))
    if not order_files and not hated_files:
        raise ValueError("rank requires order and/or hated")
    overlap = set(order_files) & set(hated_files)
    if overlap:
        raise ValueError(f"a poster cannot be both ordered and hated: {sorted(overlap)}")
    missing = valid - (set(order_files) | set(hated_files))
    if missing:
        raise ValueError(f"order/hated must cover every poster (missing: {sorted(missing)})")

    _remove_prior_taste_test_event(movie_id)

    # Stage images → training folders.
    n_pos = feedback_store.positive_exemplar_count(len(order_files))
    favorites_exemplars: list[str] = []
    for file in order_files[:n_pos]:
        src = taste_test_image_path(file)
        if src is not None:
            name = _stage_image(src, Path(pipeline_settings.TRAINING_DATA_DIR))
            if name:
                favorites_exemplars.append(name)
    negatives_added: list[str] = []
    for file in hated_files:
        src = taste_test_image_path(file)
        if src is not None:
            name = _stage_image(src, Path(pipeline_settings.NEGATIVE_DATA_DIR))
            if name:
                negatives_added.append(name)

    def _entry(file: str) -> dict | None:
        if not features.get(file):
            return None
        return {
            "orig_filename": file,
            "pipeline_rank": None,  # taste-test posters never run the real pipeline
            "baseline_rank": baseline_rank.get(file),
            "normalized_features": features[file],
        }

    order_entries = [e for e in (_entry(f) for f in order_files) if e is not None]
    hated_entries = [e for e in (_entry(f) for f in hated_files) if e is not None]

    event_id = uuid4().hex
    record = {
        "v": 4,
        "type": "ranking",
        "source": "taste_test",
        "event_id": event_id,
        "ts": datetime.now(UTC).isoformat(),
        "run_id": None,
        "movie_id": movie_id,
        "tmdb_id": None,
        "title": movie.get("title"),
        "year": movie.get("year"),
        "order": order_entries,
        "hated": hated_entries,
        "favorites_exemplars": favorites_exemplars,
        "negatives_added": negatives_added,
        "scorer_name": None,
        "model_name": pipeline_settings.AI_MODEL,
        "gate_snapshot": feedback_store.gate_snapshot(),
    }
    feedback_store.append_labels([record])
    return {
        "event_id": event_id,
        "favorites_exemplars": favorites_exemplars,
        "negatives_added": negatives_added,
    }
