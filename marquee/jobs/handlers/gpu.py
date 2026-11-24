"""What the GPU child runs for the gpu job kinds. Only the child imports this module.

Each function takes the payload, the cancel event, and a progress function, and returns a
JSON-able result. None of them uses the database.
"""

from __future__ import annotations

import os
import time
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


def engine_probe(payload: dict[str, Any], cancel: Any, progress: ProgressFn) -> dict[str, Any]:
    """Run one title through the real pipeline, as ``marquee run`` does. A diagnostic.

    It writes a run folder under ``data/runs/`` and nothing in the database.
    """
    from marquee.integrations.tmdb.exceptions import TMDBError
    from marquee.library.folders import LibraryRootError
    from marquee.library.subjects import SubjectLookupError
    from marquee.library.title_run import TitleRunError, ranked_row, run_title, stack_ranks

    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise PermanentJobError("the payload needs a title")
    year, tmdb_id = _optional_int(payload, "year"), _optional_int(payload, "tmdb_id")
    stages: list[dict[str, Any]] = []

    def on_event(event: Any) -> None:
        if event.state == "end":
            stages.append(
                {"stage": event.stage, "survivors": event.survivors, "seconds": event.elapsed_s}
            )
        progress(event.done, event.total, f"{event.stage} {event.state}")

    started = time.perf_counter()
    try:
        run = run_async(
            run_title(
                title, year=year, tmdb_id=tmdb_id, progress=on_event, should_cancel=cancel.is_set
            )
        )
    except (TitleRunError, SubjectLookupError, LibraryRootError) as exc:
        raise PermanentJobError(str(exc)) from exc
    except TMDBError as exc:
        raise RuntimeError(f"TMDB: {exc}") from exc
    output = run.output
    return {
        "run_folder": run.run_dir.name,
        "subject": {
            "title": run.subject.title,
            "tmdb_id": run.subject.tmdb_id,
            "imdb_id": run.subject.imdb_id,
        },
        "status": output.status,
        "counts": output.counts,
        "stages": stages,
        "ranked": len(output.ranked),
        "pick": ranked_row(output.recommendation, stack_ranks(output.payload)),
        "seconds": round(time.perf_counter() - started, 2),
        "pid": os.getpid(),
    }
