"""JMC6H H1 — contained internal-runner certification.

Proves the fixed runner's process-group/cgroup containment, bounded control
protocol, cancellation/timeout/kill with descendant-death confirmation, output
confinement, and honest outcomes (a killed or fence-lost runner never becomes a
success). The real poster/ML operations are wired in H2/H4; ``NOOP`` certifies the
transport itself.
"""

from __future__ import annotations

import asyncio
import json
import signal
import struct
from pathlib import Path

import numpy as np
import pytest

from marquee.core.filesystem import FilesystemBoundary, RootSpec
from marquee.core.jobs.internal_runner import _safe_output_name
from marquee.core.jobs.internal_runner_host import (
    OUTCOME_CANCELLED,
    OUTCOME_FAILED,
    OUTCOME_PROTOCOL_ERROR,
    OUTCOME_SUCCEEDED,
    OUTCOME_TIMEOUT,
    RunnerFile,
    _finalize,
    _read_frame,
    run_internal_operation,
    validate_runner_files,
)
from marquee.core.jobs.process_identity import ProcessIdentity
from marquee.core.jobs.process_launcher import ProcessLauncher, ProcessLaunchError
from marquee.core.jobs.runner_protocol import (
    MAX_FRAME_BYTES,
    ProtocolError,
    RunnerOperation,
    decode_manifest,
    decode_payload,
    encode_frame,
)
from marquee.ml.learned_head import LogisticHead


def _launcher(tmp_path: Path, **kwargs: object) -> tuple[ProcessLauncher, Path]:
    work = tmp_path / "work"
    work.mkdir()
    boundary = FilesystemBoundary(
        {"test": RootSpec(name="test", path=tmp_path, purpose="test", access="read_write")}
    )
    launcher = ProcessLauncher(
        worker_node="runner-node",
        boundary=boundary,
        working_directory=boundary.classify(work),
        **kwargs,
    )
    return launcher, work


# --------------------------------------------------------------------------- #
# End-to-end contained execution
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_noop_streams_ready_progress_result_and_validated_file(tmp_path: Path) -> None:
    launcher, work = _launcher(tmp_path)
    progress: list[dict] = []

    async def on_progress(frame: dict) -> None:
        progress.append(frame)

    outcome = await run_internal_operation(
        launcher,
        operation=RunnerOperation.NOOP,
        manifest={
            "params": {
                "stages": 3,
                "echo": "token",
                "produce_file": {"name": "profile.bin", "content": "payload"},
            }
        },
        on_progress=on_progress,
        resolve_output=lambda key: work / key,
    )

    assert outcome.outcome == OUTCOME_SUCCEEDED, outcome.error
    assert outcome.ready is True
    assert outcome.exit_code == 0
    assert outcome.summary == {"echo": "token"}
    assert len(progress) == 3
    assert [frame["ordinal"] for frame in progress] == [1, 2, 3]
    assert len(outcome.files) == 1
    assert outcome.files[0].key == "profile.bin"
    assert (work / "profile.bin").read_bytes() == b"payload"


@pytest.mark.asyncio
async def test_taste_map_builds_native_artifact_from_staged_profile(tmp_path: Path) -> None:
    launcher, work = _launcher(tmp_path)
    embeddings = np.eye(8, 512, dtype=np.float32)
    np.savez(
        work / "profile.npz",
        embeddings=embeddings,
        centroid_emb=embeddings.mean(axis=0),
        poster_names=np.asarray([f"movie-{index}.jpg" for index in range(8)]),
        model_name=np.asarray("clip-vit-b-32"),
    )

    outcome = await run_internal_operation(
        launcher,
        operation=RunnerOperation.TASTE_MAP,
        manifest={"params": {"library": "movies"}},
        resolve_output=lambda key: work / key,
    )

    assert outcome.outcome == OUTCOME_SUCCEEDED, outcome.error
    assert outcome.summary["family"] == "taste_map"
    assert outcome.summary["exemplars"] == 8
    with np.load(work / "map.npz", allow_pickle=False) as result:
        assert result["coords_2d"].shape == (8, 2)
        assert result["coords_3d"].shape == (8, 3)


@pytest.mark.asyncio
async def test_enrichment_emits_native_successor_without_overwriting_source(tmp_path: Path) -> None:
    launcher, work = _launcher(tmp_path)
    embeddings = np.eye(2, 512, dtype=np.float32)
    np.savez(
        work / "source-profile.npz",
        embeddings=embeddings,
        centroid_emb=embeddings.mean(axis=0),
        poster_names=np.asarray(["Alpha Movie (2020).jpg", "Beta Movie (2021).jpg"]),
        model_name=np.asarray("clip-vit-b-32"),
    )
    original = (work / "source-profile.npz").read_bytes()

    outcome = await run_internal_operation(
        launcher,
        operation=RunnerOperation.ENRICHMENT,
        manifest={"params": {"library": "movies", "use_tmdb": False}},
        resolve_output=lambda key: work / key,
    )

    assert outcome.outcome == OUTCOME_SUCCEEDED, outcome.error
    assert (work / "source-profile.npz").read_bytes() == original
    with np.load(work / "profile.npz", allow_pickle=False) as result:
        assert "genres_json" in result.files
        assert result["embeddings"].shape == (2, 512)


@pytest.mark.asyncio
async def test_runner_records_identity_and_is_process_group_leader(tmp_path: Path) -> None:
    identities: list[ProcessIdentity] = []

    async def record(identity: ProcessIdentity) -> bool:
        identities.append(identity)
        return True

    launcher, _ = _launcher(tmp_path, record_identity=record)
    outcome = await run_internal_operation(
        launcher, operation=RunnerOperation.NOOP, manifest={"params": {"stages": 1}}
    )
    assert outcome.outcome == OUTCOME_SUCCEEDED
    assert len(identities) == 1
    assert identities[0].pid == identities[0].process_group_id


@pytest.mark.asyncio
async def test_learned_head_trains_native_artifact_from_feedback_snapshot(tmp_path: Path) -> None:
    launcher, work = _launcher(tmp_path)
    rows = []
    for movie_id, winner_value in ((1, 1.0), (2, 2.0)):
        rows.append(
            {
                "v": 4,
                "type": "ranking",
                "movie_id": movie_id,
                "order": [
                    {
                        "orig_filename": f"winner-{movie_id}.jpg",
                        "baseline_rank": 2,
                        "normalized_features": {"x": winner_value},
                    },
                    {
                        "orig_filename": f"loser-{movie_id}.jpg",
                        "baseline_rank": 1,
                        "normalized_features": {"x": 0.0},
                    },
                ],
                "hated": [],
            }
        )
    (work / "feedback.jsonl").write_text("".join(f"{json.dumps(row)}\n" for row in rows))

    outcome = await run_internal_operation(
        launcher,
        operation=RunnerOperation.LEARNED_HEAD,
        manifest={
            "params": {
                "library": "movies",
                "mode": "pairwise",
                "min_movies": 2,
                "min_pairs": 2,
            }
        },
        resolve_output=lambda key: work / key,
    )

    assert outcome.outcome == OUTCOME_SUCCEEDED, outcome.error
    assert outcome.summary["feedback_rows"] == 2
    assert outcome.summary["n_pairs"] == 2
    assert LogisticHead.load(work / "head.npz").feature_names == ["x"]


@pytest.mark.asyncio
async def test_launch_rejects_unknown_operation_string(tmp_path: Path) -> None:
    launcher, _ = _launcher(tmp_path)
    with pytest.raises(ValueError):
        await launcher.launch_internal_runner("delete_everything", manifest=b"\x00\x00\x00\x02{}")


@pytest.mark.asyncio
async def test_launch_rejects_oversized_manifest(tmp_path: Path) -> None:
    launcher, _ = _launcher(tmp_path)
    with pytest.raises(ProcessLaunchError):
        await launcher.launch_internal_runner(RunnerOperation.NOOP, manifest=b"x" * (128 * 1024))


# --------------------------------------------------------------------------- #
# Cancellation, timeout, and kill with descendant-death confirmation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_cooperative_cancellation_confirms_tree_death(tmp_path: Path) -> None:
    launcher, _ = _launcher(tmp_path)
    stop = {"flag": False}

    async def on_progress(_frame: dict) -> None:
        stop["flag"] = True  # request stop once the runner is live

    # run_internal_operation only returns after tracked.cancel() proved tree death.
    outcome = await run_internal_operation(
        launcher,
        operation=RunnerOperation.NOOP,
        manifest={"params": {"stages": 1, "hold": "cooperative"}},
        on_progress=on_progress,
        should_stop=lambda: stop["flag"],
        cooperative_seconds=2.0,
        term_seconds=0.2,
    )
    assert outcome.outcome == OUTCOME_CANCELLED


@pytest.mark.asyncio
async def test_timeout_terminates_and_reports_timeout(tmp_path: Path) -> None:
    launcher, _ = _launcher(tmp_path)
    outcome = await run_internal_operation(
        launcher,
        operation=RunnerOperation.NOOP,
        manifest={"params": {"stages": 1, "hold": "cooperative"}},
        timeout_seconds=0.4,
        cooperative_seconds=2.0,
        term_seconds=0.2,
    )
    assert outcome.outcome == OUTCOME_TIMEOUT


@pytest.mark.asyncio
async def test_signal_ignoring_runner_is_killed(tmp_path: Path) -> None:
    launcher, _ = _launcher(tmp_path)
    stop = {"flag": False}

    async def on_progress(_frame: dict) -> None:
        stop["flag"] = True

    outcome = await run_internal_operation(
        launcher,
        operation=RunnerOperation.NOOP,
        manifest={"params": {"stages": 1, "hold": "ignore"}},
        on_progress=on_progress,
        should_stop=lambda: stop["flag"],
        cooperative_seconds=0.1,
        term_seconds=0.1,
    )
    assert outcome.outcome == OUTCOME_CANCELLED
    assert outcome.exit_signal == signal.SIGKILL


@pytest.mark.asyncio
async def test_fence_loss_never_reports_success(tmp_path: Path) -> None:
    launcher, _ = _launcher(tmp_path)
    outcome = await run_internal_operation(
        launcher,
        operation=RunnerOperation.NOOP,
        manifest={"params": {"stages": 1, "hold": "cooperative"}},
        should_stop=lambda: True,  # fence already lost
        cooperative_seconds=1.0,
        term_seconds=0.2,
    )
    assert outcome.outcome == OUTCOME_CANCELLED
    assert not outcome.succeeded


# --------------------------------------------------------------------------- #
# Output confinement
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_output_confinement_rejects_traversal(tmp_path: Path) -> None:
    launcher, work = _launcher(tmp_path)
    outcome = await run_internal_operation(
        launcher,
        operation=RunnerOperation.NOOP,
        manifest={"params": {"produce_file": {"name": "../escape.bin", "content": "x"}}},
        resolve_output=lambda key: work / key,
    )
    assert outcome.outcome == OUTCOME_FAILED
    assert not (tmp_path / "escape.bin").exists()


def test_safe_output_name_rejects_traversal_and_hidden() -> None:
    assert _safe_output_name("candidate.jpg") == "candidate.jpg"
    for bad in ["../x", "a/b", "..", ".", ".hidden", "", "x" * 300]:
        with pytest.raises(ProtocolError):
            _safe_output_name(bad)


def test_validate_runner_files_detects_checksum_mismatch(tmp_path: Path) -> None:
    good = tmp_path / "a.bin"
    good.write_bytes(b"abc")
    import hashlib

    digest = hashlib.sha256(b"abc").hexdigest()
    validate_runner_files((RunnerFile("a.bin", digest, 3),), lambda k: tmp_path / k)
    with pytest.raises(ProtocolError):
        validate_runner_files((RunnerFile("a.bin", "0" * 64, 3),), lambda k: tmp_path / k)
    with pytest.raises(ProtocolError):
        validate_runner_files((RunnerFile("missing.bin", digest, 3),), lambda k: tmp_path / k)


# --------------------------------------------------------------------------- #
# Protocol safety (pure)
# --------------------------------------------------------------------------- #


def test_encode_frame_enforces_bounds_and_version() -> None:
    with pytest.raises(ProtocolError):
        encode_frame({"v": 1, "type": "x", "blob": "z" * (MAX_FRAME_BYTES)})
    with pytest.raises(ProtocolError):
        encode_frame({"v": 2, "type": "x"})
    with pytest.raises(ProtocolError):
        encode_frame({"v": 1})


def test_decode_payload_rejects_malformed() -> None:
    for bad in [b"not json", b"[]", b'{"v":2,"type":"x"}', b'{"v":1}']:
        with pytest.raises(ProtocolError):
            decode_payload(bad)
    assert decode_payload(b'{"v":1,"type":"ready"}')["type"] == "ready"


def test_decode_manifest_validates_operation() -> None:
    with pytest.raises(ProtocolError):
        decode_manifest(b'{"v":1}')
    with pytest.raises(ProtocolError):
        decode_manifest(b'{"v":2,"operation":"noop"}')
    assert decode_manifest(b'{"v":1,"operation":"noop"}')["operation"] == "noop"


@pytest.mark.asyncio
async def test_read_frame_handles_clean_eof_truncation_and_oversize() -> None:
    empty = asyncio.StreamReader()
    empty.feed_eof()
    assert await _read_frame(empty) is None

    truncated = asyncio.StreamReader()
    truncated.feed_data(b"\x00\x00")
    truncated.feed_eof()
    with pytest.raises(ProtocolError):
        await _read_frame(truncated)

    oversize = asyncio.StreamReader()
    oversize.feed_data(struct.pack(">I", MAX_FRAME_BYTES + 1))
    oversize.feed_eof()
    with pytest.raises(ProtocolError):
        await _read_frame(oversize)


# --------------------------------------------------------------------------- #
# Finalization honesty (pure)
# --------------------------------------------------------------------------- #


class _Summary:
    exit_code = 0
    exit_signal = None


def test_finalize_missing_result_is_failure() -> None:
    outcome = _finalize(
        ready=True, warnings=(), file_frames=[], result_frame=None, protocol_error=None,
        cancelled=False, timed_out=False, summary=_Summary(), should_stop=None, resolve_output=None,
    )
    assert outcome.outcome == OUTCOME_FAILED


def test_finalize_late_fence_loss_overrides_success() -> None:
    outcome = _finalize(
        ready=True, warnings=(), file_frames=[],
        result_frame={"v": 1, "type": "result", "outcome": "succeeded", "summary": {}},
        protocol_error=None, cancelled=False, timed_out=False, summary=_Summary(),
        should_stop=lambda: True, resolve_output=None,
    )
    assert outcome.outcome == OUTCOME_CANCELLED


def test_finalize_nonzero_exit_downgrades_success() -> None:
    class _Bad:
        exit_code = 3
        exit_signal = None

    outcome = _finalize(
        ready=True, warnings=(), file_frames=[],
        result_frame={"v": 1, "type": "result", "outcome": "succeeded", "summary": {}},
        protocol_error=None, cancelled=False, timed_out=False, summary=_Bad(),
        should_stop=None, resolve_output=None,
    )
    assert outcome.outcome == OUTCOME_FAILED


def test_finalize_protocol_error_is_reported() -> None:
    outcome = _finalize(
        ready=False, warnings=(), file_frames=[], result_frame=None,
        protocol_error=ProtocolError("boom"), cancelled=False, timed_out=False,
        summary=_Summary(), should_stop=None, resolve_output=None,
    )
    assert outcome.outcome == OUTCOME_PROTOCOL_ERROR
