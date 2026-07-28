"""Opt-in live capability smokes (MARQUEE_LIVE_SMOKE=1).

Real models against fixture posters — a poster run end to end, and a taste profile trained
and mapped from installed weights. Skipped by default because they need model files the
repository does not carry."""

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
    reason="live smoke is opt-in (set MARQUEE_LIVE_SMOKE=1)",
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


@pytest.mark.asyncio
async def test_real_taste_profile_trains_on_fixture_posters(tmp_path: Path) -> None:
    work = tmp_path / "work"
    training = work / "training"
    training.mkdir(parents=True)
    _fixture_poster(training / "Alpha Movie (2020).jpg", (40, 60, 120))
    _fixture_poster(training / "Beta Movie (2021).jpg", (120, 40, 40))
    _fixture_poster(training / "Gamma Movie (2022).jpg", (40, 120, 60))

    boundary = FilesystemBoundary(
        {"test": RootSpec(name="test", path=tmp_path, purpose="test", access="read_write")}
    )
    launcher = ProcessLauncher(
        worker_node="taste-live-smoke",
        boundary=boundary,
        working_directory=boundary.classify(work),
    )
    manifest = {
        "params": {
            "family": "taste_profile",
            "library": "movies",
            "source": {"mode": "fixture"},
            "skip_ocr": True,
            "skip_dino": True,
            "seed": 0,
        }
    }

    outcome = await run_internal_operation(
        launcher,
        operation=RunnerOperation.TASTE_PROFILE,
        manifest=manifest,
        resolve_output=lambda key: work / key,
        timeout_seconds=600.0,
    )

    assert outcome.outcome == OUTCOME_SUCCEEDED, outcome.error
    assert any(descriptor.key == "profile.npz" for descriptor in outcome.files)
    profile = work / "profile.npz"
    assert profile.is_file()
    assert outcome.summary.get("exemplars") == 3

    # The production loader must accept the freshly-built native profile.
    from marquee.ml.taste_store import NumpyTasteStore

    NumpyTasteStore(profile)._ensure_loaded()


@pytest.mark.asyncio
async def test_real_taste_map_builds_from_installed_profile(tmp_path: Path) -> None:
    """Run the native map builder in containment over the installed real profile."""
    from marquee.ml.namespaces import get_namespace
    from marquee.ml.taste_map import load_map

    work = tmp_path / "work"
    work.mkdir()
    source_profile = get_namespace("movies").profile_path
    assert source_profile.is_file(), "installed movie taste profile is required for this live smoke"
    shutil.copyfile(source_profile, work / "profile.npz")

    boundary = FilesystemBoundary(
        {"test": RootSpec(name="test", path=tmp_path, purpose="test", access="read_write")}
    )
    launcher = ProcessLauncher(
        worker_node="taste-map-live-smoke",
        boundary=boundary,
        working_directory=boundary.classify(work),
    )
    outcome = await run_internal_operation(
        launcher,
        operation=RunnerOperation.TASTE_MAP,
        manifest={"params": {"library": "movies", "seed": 0}},
        resolve_output=lambda key: work / key,
        timeout_seconds=600.0,
    )

    assert outcome.outcome == OUTCOME_SUCCEEDED, outcome.error
    loaded = load_map(namespace=get_namespace("movies"), path=work / "map.npz")
    assert len(loaded["points"]) == outcome.summary["exemplars"]
    assert loaded["points"]
