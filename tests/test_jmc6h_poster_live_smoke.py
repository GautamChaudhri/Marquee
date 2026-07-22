"""JMC6H poster live capability smoke (H25 evidence).

Runs the *real* ``poster_single`` operation end to end through the contained
internal runner: a real ``FeatureExtractor`` (CLIP/DINO/aesthetic), PaddleOCR, the
taste profile, the gates, and the scorer — on confined fixture images. It proves
the enabled poster capability actually executes on this host rather than trusting
synthetic registration.

It is opt-in because it loads hundreds of MB of models and runs CPU inference:
set ``MARQUEE_LIVE_SMOKE=1`` to run it. It is skipped in the default suite and its
result is recorded as live-capability evidence, separate from the deterministic
projection test in ``test_jmc6h_product_effect.py``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from PIL import Image

from marquee.core.filesystem import FilesystemBoundary, RootSpec
from marquee.core.jobs.internal_runner_host import OUTCOME_SUCCEEDED, run_internal_operation
from marquee.core.jobs.process_launcher import ProcessLauncher
from marquee.core.jobs.runner_protocol import RunnerOperation, RunnerRuntimeOptions

pytestmark = pytest.mark.skipif(
    os.environ.get("MARQUEE_LIVE_SMOKE") != "1",
    reason="poster live smoke is opt-in (set MARQUEE_LIVE_SMOKE=1)",
)


def _fixture_poster(path: Path, colour: tuple[int, int, int]) -> None:
    image = Image.new("RGB", (500, 750), colour)
    image.save(path, format="JPEG", quality=90)


@pytest.mark.asyncio
async def test_real_poster_single_runs_on_fixture_candidates(tmp_path: Path) -> None:
    work = tmp_path / "work"
    candidates = work / "candidates"
    candidates.mkdir(parents=True)
    _fixture_poster(candidates / "candidate_one.jpg", (32, 48, 96))
    _fixture_poster(candidates / "candidate_two.jpg", (128, 32, 32))
    from marquee.ml.namespaces import get_namespace

    source_profile = get_namespace("movies").profile_path
    assert source_profile.is_file(), "installed movie taste profile is required"
    shutil.copyfile(source_profile, work / "profile.npz")

    boundary = FilesystemBoundary(
        {"test": RootSpec(name="test", path=tmp_path, purpose="test", access="read_write")}
    )
    launcher = ProcessLauncher(
        worker_node="poster-live-smoke",
        boundary=boundary,
        working_directory=boundary.classify(work),
    )
    manifest = {
        "params": {
            "subject": {
                "title": "Live Smoke Movie",
                "media_type": "movie",
                "movie_id": 1,
                "tmdb_id": 100,
            },
            "source": {"mode": "fixture"},
            "run_id": "postersmoke0001",
        }
    }

    outcome = await run_internal_operation(
        launcher,
        operation=RunnerOperation.POSTER_SINGLE,
        manifest=manifest,
        resolve_output=lambda key: work / key,
        timeout_seconds=600.0,
        runtime_options=RunnerRuntimeOptions(
            ocr_device="cpu",
            ocr_workers=1,
            execution_provider="cpu",
            cuda_visible_devices="-1",
        ),
    )

    assert outcome.outcome == OUTCOME_SUCCEEDED, outcome.error
    assert outcome.ready is True
    # run.json is a produced, checksum-validated file.
    assert any(descriptor.key == "run.json" for descriptor in outcome.files)
    assert (work / "run.json").is_file()
    # The real pipeline executed over the fixture bytes (fetch + stages ran).
    counts = outcome.summary.get("counts", {})
    assert counts.get("posters_found") == 2
    assert outcome.summary.get("run_id") == "postersmoke0001"
