"""What the GPU child runs for the gpu job kinds. Only the child imports this module.

Each function takes the payload, the cancel event, and a progress function, and returns a
JSON-able result. None of them uses the database: what a function needs from it arrives in
the payload, and what it learns goes back in the result.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marquee.jobs.gpu_child import run_async
from marquee.jobs.registry import PermanentJobError, ProgressFn

SELFTEST_BATCH = 8

_clip: tuple[Any, Any] | None = None
"""The self-test's CLIP session and encoder, loaded once per child."""


def gpu_selftest(payload: dict[str, Any], cancel: Any, progress: ProgressFn) -> dict[str, Any]:
    """Load CLIP as ``marquee doctor`` does (once per child), embed one batch, and report."""
    global _clip
    import numpy as np
    import onnxruntime as ort

    from marquee.config import pipeline_settings
    from marquee.ml import hardware
    from marquee.ml.embedding import CLIPImageEncoder

    already_loaded = _clip is not None
    started = time.perf_counter()
    if _clip is None:
        ort.set_default_logger_severity(3)
        session = hardware.create_onnx_session(pipeline_settings.CLIP_MODEL_PATH)
        _clip = (session, CLIPImageEncoder(pipeline_settings.CLIP_MODEL_PATH, session=session))
    session, encoder = _clip
    loaded = time.perf_counter()
    rng = np.random.default_rng(0)
    images = [
        rng.standard_normal((1, 3, 224, 224)).astype(np.float32) for _ in range(SELFTEST_BATCH)
    ]
    vectors = encoder.encode_batch(images)
    done = time.perf_counter()
    if vectors.shape != (SELFTEST_BATCH, 512) or not np.all(np.isfinite(vectors)):
        raise RuntimeError(f"CLIP returned shape {vectors.shape}")
    return {
        "provider": session.get_providers()[0],
        "already_loaded": already_loaded,
        "batch": SELFTEST_BATCH,
        "load_seconds": round(loaded - started, 4),
        "embed_seconds": round(done - loaded, 4),
        "seconds": round(done - started, 4),
        "pid": os.getpid(),
    }


def _optional_int(payload: dict[str, Any], name: str) -> int | None:
    value = payload.get(name)
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise PermanentJobError(f"the payload's {name} must be a whole number")
    return value


@dataclass(frozen=True)
class _MovieGateInputs:
    """The attributes ``OcrGateContext.from_movie`` reads, by the names it reads them.

    The engine reads ``production_companies_json``; the movies table calls the column
    ``production_companies``.
    """

    director: str | None
    production_companies_json: list[str]
    tagline: str | None
    text_profile_id: str | None = None


def _run_folder(payload: dict[str, Any]) -> str:
    name = payload.get("run_folder")
    if not isinstance(name, str) or not name or name in {".", ".."} or "/" in name:
        raise PermanentJobError("the payload needs the run folder's name")
    return name


def _required_int(payload: dict[str, Any], name: str) -> int:
    value = _optional_int(payload, name)
    if value is None:
        raise PermanentJobError(f"the payload needs {name}")
    return value


def _file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _identity(payload: dict[str, Any], name: str) -> dict[str, Any] | None:
    """The ``{"id", "sha256", "file_name"}`` of the active profile or residual artifact the
    worker read, or ``None``."""
    value = payload.get(name)
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("id"), int)
        or not isinstance(value.get("sha256"), str)
        or len(value["sha256"]) != 64
        or not isinstance(value.get("file_name"), str)
        or value["file_name"] != f"{value['sha256']}.npz"
    ):
        raise PermanentJobError(f"the payload's {name} is malformed")
    return value


def _mask(payload: dict[str, Any]) -> tuple[list[str], list[str]] | None:
    value = payload.get("mask")
    if value is None:
        return None
    positives, negatives = (
        value.get(key) if isinstance(value, dict) else None for key in ("positives", "negatives")
    )
    if not isinstance(positives, list) or not isinstance(negatives, list):
        raise PermanentJobError("the payload's mask needs its positives and negatives")
    if not positives or not all(isinstance(n, str) for n in (*positives, *negatives)):
        raise PermanentJobError("a masked run leaves out at least one positive, by name")
    return positives, negatives


def poster_run(payload: dict[str, Any], cancel: Any, progress: ProgressFn) -> dict[str, Any]:
    """Run one movie through the real pipeline in the run folder the worker named.

    The payload carries everything the engine needs (the folder's name, the movie's id,
    title, and TMDB id, and the three text-gate fields from the movies table), the active
    profile's and residual artifact's identity as the worker read them, and for a masked run
    the exemplars to leave out. The result is small; the archive the pipeline writes into
    the folder is the full record.

    A masked run scores against a derived copy of the active profile without the movie's own
    exemplars (:mod:`marquee.taste.blind`), deleted when the run ends whatever happens; its
    profile is the active one. A residual artifact is passed to the engine with the runtime
    context it must match (the scorer weights, the profile's checksum and id); the engine
    decides whether it ranks, and falls back to the weighted scorer when it does not.
    """
    from marquee.config import pipeline_settings, settings
    from marquee.integrations.tmdb.exceptions import TMDBError
    from marquee.ml.residual import baseline_signature
    from marquee.pipeline.orchestrator import PosterSubjectInput
    from marquee.pipeline.scorer import ResidualRuntimeContext
    from marquee.pipeline.single_run import run_single_subject
    from marquee.pipeline.text_profiles import OcrGateContext

    folder = _run_folder(payload)
    movie_id = _required_int(payload, "movie_id")
    tmdb_id = _required_int(payload, "tmdb_id")
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise PermanentJobError("the payload needs a title")
    active = _identity(payload, "profile")
    residual = _identity(payload, "residual")
    mask = _mask(payload)
    token = settings.TMDB_READ_ACCESS_TOKEN
    if token is None or not token.get_secret_value():
        raise PermanentJobError("TMDB_READ_ACCESS_TOKEN is not set")
    profile = Path(pipeline_settings.TASTE_PROFILE_PATH)
    if not profile.is_file():
        raise PermanentJobError("there is no taste profile; run `marquee taste seed` first")
    profile_sha256 = _file_sha256(profile)
    gate = OcrGateContext.from_movie(
        _MovieGateInputs(
            director=payload.get("director"),
            production_companies_json=list(payload.get("production_companies") or []),
            tagline=payload.get("tagline"),
        )
    )
    stages: list[dict[str, Any]] = []

    def on_event(event: Any) -> None:
        if event.state == "end":
            stages.append(
                {"stage": event.stage, "survivors": event.survivors, "seconds": event.elapsed_s}
            )
        progress(event.done, event.total, f"{event.stage} {event.state}")

    started = time.perf_counter()
    blind_path: Path | None = None
    try:
        extractor = None
        if mask is not None:
            from marquee.ml.taste_store import NumpyTasteStore
            from marquee.pipeline.features import FeatureExtractor
            from marquee.taste import blind, rebuild

            if active is None:
                raise PermanentJobError("a masked run needs the active profile")
            source = rebuild.profile_copy(active["file_name"])
            if not source.is_file() or _file_sha256(source) != active["sha256"]:
                raise PermanentJobError(
                    f"the kept copy of the active profile {active['sha256'][:12]} is missing "
                    "or does not hold its own bytes"
                )
            blind_path = blind.blind_profile_path(active["sha256"], movie_id)
            try:
                blind.mask_profile(source, blind_path, positives=mask[0], negatives=mask[1])
            except blind.MaskError as exc:
                raise PermanentJobError(f"the active profile cannot be masked: {exc}") from exc
            extractor = FeatureExtractor(
                personalization_mode="personalized",
                taste_store=NumpyTasteStore(
                    blind_path, expected_model_name=pipeline_settings.AI_MODEL
                ),
            )
            # The run is of the active profile, less one movie: that is the profile it names.
            profile_sha256 = active["sha256"]
        residual_path = None
        residual_context = None
        if residual is not None and active is not None:
            residual_path = settings.residuals_dir / residual["file_name"]
            residual_context = ResidualRuntimeContext(
                library="movies",
                baseline_signature=baseline_signature(pipeline_settings.scorer_weights),
                profile_checksum=profile_sha256,
                profile_generation=active["id"],
                artifact_id=residual["id"],
                artifact_checksum=residual["sha256"],
            )
        output = run_async(
            run_single_subject(
                subject=PosterSubjectInput(title=title, tmdb_id=tmdb_id, movie_id=movie_id),
                run_dir=settings.runs_dir / folder,
                ocr_gate=gate,
                progress=on_event,
                should_cancel=cancel.is_set,
                feature_extractor=extractor,
                residual_path=residual_path,
                residual_context=residual_context,
            )
        )
    except TMDBError as exc:
        raise RuntimeError(f"TMDB: {exc}") from exc
    finally:
        if blind_path is not None:
            blind_path.unlink(missing_ok=True)
    pick = output.recommendation
    # The engine names the scorer that ranked; a run that ranked nothing keeps the archive's
    # label, as before.
    scorer = output.scorer_name or output.payload.get("scorer")
    return {
        "status": output.status,
        "counts": output.counts,
        "stages": stages,
        "scorer": scorer,
        "residual_id": residual["id"] if scorer == "residual" and residual else None,
        "engine_run_id": output.run_id,
        "ai_model": pipeline_settings.AI_MODEL,
        "profile_sha256": profile_sha256,
        "pick_file": str(pick["orig_filename"]) if pick else None,
        "masked": mask is not None,
        "seconds": round(time.perf_counter() - started, 2),
        "pid": os.getpid(),
    }


def taste_rebuild(payload: dict[str, Any], cancel: Any, progress: ProgressFn) -> dict[str, Any]:
    """Build a taste profile from the exemplars the worker staged, with the trainer as it is.

    The payload names the build folder (inside the taste build directory) and the two weight
    maps; the trainer reads ``positive/`` and ``negative/`` there and writes ``profile.npz``
    beside them. The worker checks and publishes the result.
    """
    from marquee.config import settings
    from marquee.taste.rebuild import BUILT, NEGATIVE, POSITIVE
    from marquee.taste.trainer import rebuild_profile

    name = payload.get("build")
    if not isinstance(name, str) or not name or name in {".", ".."} or "/" in name:
        raise PermanentJobError("the payload needs the build folder's name")
    positive_weights = payload.get("positive_weights")
    negative_weights = payload.get("negative_weights")
    if not isinstance(positive_weights, dict) or not isinstance(negative_weights, dict):
        raise PermanentJobError("the payload needs both weight maps")
    build = settings.taste_build_dir / name
    negatives = build / NEGATIVE
    has_negatives = negatives.is_dir() and any(negatives.iterdir())

    def on_progress(update: dict[str, Any]) -> None:
        message = update.get("message") or update.get("stage")
        progress(update.get("processed"), update.get("total"), message)

    started = time.perf_counter()
    output = rebuild_profile(
        training_dir=build / POSITIVE,
        negative_dir=negatives if has_negatives else None,
        output=build / BUILT,
        progress_callback=on_progress,
        cancel_event=cancel,
        positive_weights=positive_weights,
        negative_weights=negative_weights,
    )
    return {
        "sha256": _file_sha256(Path(output)),
        "seconds": round(time.perf_counter() - started, 2),
        "pid": os.getpid(),
    }
