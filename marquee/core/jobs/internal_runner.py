"""Fixed, code-owned internal-runner child entrypoint (JMC6H H1).

Invoked only as ``python -m marquee.core.jobs.internal_runner <operation>`` by the
tracked process launcher, inside the attempt's process group/cgroup. It reads one
bounded manifest from stdin, streams bounded control frames on the inherited
control pipe, writes any large output into confined workspace files (its cwd is the
attempt workspace), and returns a final result frame.

The module is intentionally light: importing it must not pull in the poster/ML
stack. Real poster/ML operations import their implementations lazily as their
family gates pass (H2/H4); until then only ``NOOP`` is registered, which exists to
certify the transport, containment, cancellation, and output-confinement behavior.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import signal
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from marquee.core.jobs.runner_protocol import (
    CONTROL_FD_ENV,
    PROTOCOL_VERSION,
    ControlWriter,
    ProtocolError,
    RunnerOperation,
    read_manifest_from_stdin,
)

# Upper bound on rejected-candidate images carried out of a run for the review
# UI. Mirrors the survivor bound just below it in _run_poster_single.
_MAX_REVIEW_EVIDENCE_FILES = 120

# Exit codes distinct from any real handler signal exit.
_EXIT_BAD_INVOCATION = 64
_EXIT_NO_CONTROL = 65
_EXIT_OPERATION_FAILED = 1

OperationHandler = Callable[[dict[str, Any], ControlWriter], dict[str, Any]]


class RunnerOperationNotEnabledError(RuntimeError):
    """A closed-vocabulary operation exists but is not yet wired to real behavior."""


def _safe_output_name(name: object) -> str:
    """Confine a produced-file name to a single safe component under the workspace."""
    if not isinstance(name, str) or not name:
        raise ProtocolError("produced file name is invalid")
    if len(name) > 200 or name != os.path.basename(name):
        raise ProtocolError("produced file name is not a confined basename")
    if name in {".", ".."} or name.startswith(".") or "\0" in name:
        raise ProtocolError("produced file name is not a confined basename")
    return name


def _write_confined_output(name: str, content: bytes, control: ControlWriter) -> dict[str, Any]:
    """Write one confined output into the workspace cwd and announce it by frame."""
    safe = _safe_output_name(name)
    with open(safe, "xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    checksum = hashlib.sha256(content).hexdigest()
    descriptor = {"key": safe, "checksum": checksum, "size": len(content)}
    control.emit({"v": PROTOCOL_VERSION, "type": "file", **descriptor})
    return descriptor


def _run_noop(manifest: dict[str, Any], control: ControlWriter) -> dict[str, Any]:
    """Transport-certification operation: progress, optional file, optional hold."""
    params = manifest.get("params")
    params = params if isinstance(params, dict) else {}
    stages = params.get("stages", 1)
    stages = max(0, min(int(stages) if isinstance(stages, int) else 1, 100))
    for ordinal in range(1, stages + 1):
        control.emit(
            {
                "v": PROTOCOL_VERSION,
                "type": "progress",
                "stage": "working",
                "ordinal": ordinal,
                "overall": {"completed": ordinal, "total": stages, "unit": "steps"},
            }
        )

    files: list[dict[str, Any]] = []
    produce = params.get("produce_file")
    if isinstance(produce, dict):
        content = produce.get("content", "")
        content_bytes = content.encode("utf-8") if isinstance(content, str) else b""
        files.append(_write_confined_output(produce.get("name"), content_bytes, control))

    summary: dict[str, Any] = {"echo": params.get("echo")}
    if params.get("report_runtime_options") is True:
        # Test-only transport observation: the closed runner contract exposes
        # only its four non-secret runtime keys, never arbitrary environment.
        summary["runtime_options"] = {
            key: os.environ.get(key)
            for key in ("OCR_DEVICE", "OCR_WORKERS", "EXECUTION_PROVIDER", "CUDA_VISIBLE_DEVICES")
        }
    result = {"outcome": "succeeded", "summary": summary, "files": files}
    hold = params.get("hold", "none")
    if hold == "cooperative":
        _cooperative_hold()
    elif hold == "ignore":
        _ignore_until_kill()
    elif hold == "result_then_cooperative":
        # Certify the result/exit race: a valid result frame, then a child that
        # lingers but still honors cooperative shutdown.
        _emit_result(control, result)
        _cooperative_hold()
    elif hold == "result_then_ignore":
        # A valid result frame, then a child that resists TERM until SIGKILL.
        _emit_result(control, result)
        _ignore_until_kill()
    elif hold == "close_control_then_hold":
        # A clean control-channel EOF at a frame boundary with no result frame.
        control.close()
        _cooperative_hold()

    return result


def _trainer_progress_forwarder(control: ControlWriter):
    """Forward real trainer callback payloads as bounded typed progress frames.

    Only allowlisted, bounded fields cross the control channel; the host-side
    bridge validates and maps them onto the registered stage vocabulary. Frame
    emission is best-effort — a full pipe can never fail the training work.
    """
    cursor = 0

    def _forward(event: object) -> None:
        nonlocal cursor
        if not isinstance(event, dict):
            return
        stage = event.get("stage")
        frame: dict[str, Any] = {
            "v": PROTOCOL_VERSION,
            "type": "progress",
            "stage": str(stage)[:200] if stage else "training",
            "state": str(event.get("state") or "progress")[:20],
        }
        processed = event.get("processed")
        total = event.get("total")
        if isinstance(processed, int) and not isinstance(processed, bool) and processed >= 0:
            frame["done"] = processed
        if isinstance(total, int) and not isinstance(total, bool) and total > 0:
            frame["total"] = total
        item = event.get("current_item")
        if isinstance(item, str) and item:
            frame["subject"] = item[:200]
        message = event.get("message")
        if isinstance(message, str) and message:
            frame["message"] = message[:200]
        cursor += 1
        frame["cursor"] = cursor
        with contextlib.suppress(Exception):
            control.emit(frame)

    return _forward


def _announce_file(name: str, control: ControlWriter) -> dict[str, Any]:
    """Emit a produced-file frame for a file already written into the workspace."""
    safe = _safe_output_name(name)
    digest = hashlib.sha256()
    size = 0
    with open(safe, "rb") as handle:
        while chunk := handle.read(1 << 20):
            size += len(chunk)
            digest.update(chunk)
    descriptor = {"key": safe, "checksum": digest.hexdigest(), "size": size}
    control.emit({"v": PROTOCOL_VERSION, "type": "file", **descriptor})
    return descriptor


def _poster_feature_runtime(personalization_mode: str) -> tuple[Any, Path | None]:
    """Resolve staged personalization artifacts after the host selects the mode."""
    from marquee.pipeline.features import FeatureExtractor  # noqa: PLC0415

    if personalization_mode == "collecting":
        return (
            FeatureExtractor(
                personalization_mode="collecting",
                embedding_cache_enabled=False,
            ),
            None,
        )
    from marquee.ml.taste_store import NumpyTasteStore  # noqa: PLC0415

    profile_path = Path("profile.npz")
    if not profile_path.is_file():
        raise ProtocolError("personalized poster run is missing its staged taste profile")
    residual_path = Path("residual.npz")
    return (
        FeatureExtractor(
            taste_store=NumpyTasteStore(profile_path),
            personalization_mode="personalized",
            embedding_cache_enabled=False,
        ),
        residual_path if residual_path.is_file() else None,
    )


def _ocr_gate_context(params: dict[str, Any], subject: Any) -> Any:
    """Rebuild the host-resolved text gate inside the runner process.

    Falls back to the subject's own scope when the manifest predates the
    ``text_gate`` params, so an in-flight job still gates a season as a season.
    """
    from marquee.core.text_profiles import OcrGateContext, get_active_profile  # noqa: PLC0415

    gate = params.get("text_gate") if isinstance(params.get("text_gate"), dict) else {}
    scope = gate.get("scope")
    if scope not in ("movie", "show", "season"):
        scope = {"series": "show", "season": "season"}.get(subject.media_type, "movie")
    studios = gate.get("studios")
    return OcrGateContext(
        director=gate.get("director"),
        studios=list(studios) if isinstance(studios, list) else None,
        tagline=gate.get("tagline"),
        profile=get_active_profile(scope, gate.get("profile_id")),
        scope=scope,
        season_number=subject.season_number,
    )


def _materialize_review_evidence(
    payload: dict[str, Any], diagnostic_paths: dict[Any, Any]
) -> dict[str, str]:
    """Copy out rejected candidates so the review UI's stage tabs can show them.

    Best-effort by contract: an unusable entry is dropped, never raised — a
    missing rejection thumbnail must not fail a run whose survivors are intact.
    """
    import shutil  # noqa: PLC0415

    block = payload.get("review_evidence")
    entries = block.get("candidates") if isinstance(block, dict) else None
    if not isinstance(block, dict) or not isinstance(entries, list):
        return {}
    rejected_files: dict[str, str] = {}
    retained: list[dict[str, Any]] = []
    for entry in entries[:_MAX_REVIEW_EVIDENCE_FILES]:
        if not isinstance(entry, dict):
            continue
        reference = entry.get("reference")
        image_path = diagnostic_paths.get(reference) if isinstance(reference, str) else None
        if not isinstance(reference, str) or not isinstance(image_path, str):
            continue
        source_path = Path(image_path).resolve()
        if not source_path.is_file() or not source_path.is_relative_to(Path.cwd().resolve()):
            continue
        key = f"rejected-{len(retained):03d}.jpg"
        try:
            shutil.copyfile(source_path, key)
        except OSError:
            continue
        entry["position"] = len(retained)
        entry["artifact_key"] = key
        rejected_files[reference] = key
        retained.append(entry)
    block["candidates"] = retained
    block["archived_count"] = len(retained)
    block["truncated_count"] = max(0, len(entries) - len(retained))
    return rejected_files


def _run_poster_single(manifest: dict[str, Any], control: ControlWriter) -> dict[str, Any]:
    """Run the real single-subject poster pipeline confined to the workspace.

    Heavy pipeline/ML imports are deferred to this handler so importing the runner
    module stays cheap. Candidate bytes, features, and outputs live only in the
    workspace cwd; run.json and the selected candidate are announced as produced
    files for the coordinator to validate and the handler to register.
    """
    import asyncio  # noqa: PLC0415
    import shutil  # noqa: PLC0415

    from marquee.pipeline.orchestrator import (  # noqa: PLC0415
        PosterSourceInput,
        PosterSubjectInput,
        run_poster_pipeline,
    )
    from marquee.pipeline.runner import ProgressEvent, write_run_json  # noqa: PLC0415
    from marquee.pipeline.scorer import ResidualRuntimeContext  # noqa: PLC0415

    params = manifest.get("params")
    params = params if isinstance(params, dict) else {}
    subject_params = params.get("subject") if isinstance(params.get("subject"), dict) else {}
    source_params = params.get("source") if isinstance(params.get("source"), dict) else {}
    if not isinstance(subject_params.get("title"), str) or not subject_params["title"]:
        raise ProtocolError("poster_single manifest is missing a subject title")

    subject = PosterSubjectInput(
        title=subject_params["title"],
        media_type=subject_params.get("media_type", "movie"),
        movie_id=subject_params.get("movie_id"),
        tmdb_id=subject_params.get("tmdb_id"),
        series_id=subject_params.get("series_id"),
        season_id=subject_params.get("season_id"),
        season_number=subject_params.get("season_number"),
        ocr_title=subject_params.get("ocr_title"),
    )
    source = PosterSourceInput(mode=source_params.get("mode", "tmdb"))
    run_id = params.get("run_id") if isinstance(params.get("run_id"), str) else None
    personalization_mode = params.get("personalization_mode", "collecting")
    if personalization_mode not in {"collecting", "personalized"}:
        raise ProtocolError("poster_single manifest has an invalid personalization mode")
    profile = params.get("taste_profile") if isinstance(params.get("taste_profile"), dict) else {}
    residual = (
        params.get("ranking_residual") if isinstance(params.get("ranking_residual"), dict) else {}
    )
    runtime_context = None
    if personalization_mode == "personalized" and residual:
        checksum = profile.get("checksum")
        generation = profile.get("generation")
        residual_checksum = residual.get("checksum")
        baseline = params.get("baseline_signature")
        if (
            isinstance(checksum, str)
            and isinstance(generation, int)
            and isinstance(residual_checksum, str)
            and isinstance(baseline, str)
        ):
            runtime_context = ResidualRuntimeContext(
                library="movies" if subject.media_type == "movie" else "tv",
                baseline_signature=baseline,
                profile_checksum=checksum,
                profile_generation=generation,
                artifact_id=residual.get("artifact_id")
                if isinstance(residual.get("artifact_id"), int)
                else None,
                artifact_checksum=residual_checksum,
            )

    def _emit_progress(event: ProgressEvent) -> None:
        frame: dict[str, Any] = {
            "v": PROTOCOL_VERSION,
            "type": "progress",
            "stage": event.stage,
            "state": event.state,
        }
        if event.total is not None:
            frame["total"] = event.total
        if event.done is not None:
            frame["done"] = event.done
        if event.survivors is not None:
            frame["survivors"] = event.survivors
        with contextlib.suppress(Exception):
            control.emit(frame)

    extractor, residual_path = _poster_feature_runtime(personalization_mode)
    extractor.preflight()
    output = asyncio.run(
        run_poster_pipeline(
            subject=subject,
            source=source,
            out_dir=Path.cwd(),
            feature_extractor=extractor,
            ocr_gate=_ocr_gate_context(params, subject),
            progress=_emit_progress,
            run_id=run_id,
            residual_path=residual_path,
            residual_context=runtime_context,
            personalization_mode=personalization_mode,
        )
    )

    candidate_files: dict[str, str] = {}
    review = output.payload.get("review")
    ledger = output.payload.get("diagnostic_ledger")
    survivors = review.get("survivors") if isinstance(review, dict) else None
    diagnostics = ledger.get("candidates") if isinstance(ledger, dict) else None
    diagnostic_paths = (
        {
            candidate.get("orig_filename"): candidate.get("image_path")
            for candidate in diagnostics
            if isinstance(candidate, dict)
            and isinstance(candidate.get("orig_filename"), str)
            and isinstance(candidate.get("image_path"), str)
        }
        if isinstance(diagnostics, list)
        else {}
    )
    if isinstance(survivors, list):
        retained: list[dict[str, Any]] = []
        for index, survivor in enumerate(survivors[:100]):
            if not isinstance(survivor, dict):
                continue
            reference = survivor.get("reference")
            image_path = diagnostic_paths.get(reference)
            if not isinstance(reference, str) or not isinstance(image_path, str):
                continue
            source_path = Path(image_path).resolve()
            if not source_path.is_file() or not source_path.is_relative_to(Path.cwd().resolve()):
                continue
            key = f"candidate-{index:03d}.jpg"
            shutil.copyfile(source_path, key)
            candidate_files[reference] = key
            survivor["artifact_key"] = key
            retained.append(survivor)
        review["survivors"] = retained
        review["archived_count"] = len(retained)
        review["truncated_count"] = max(0, len(survivors) - len(retained))

    try:
        rejected_files = _materialize_review_evidence(output.payload, diagnostic_paths)
    except Exception:  # noqa: BLE001 - evidence never fails a run
        rejected_files = {}

    write_run_json(Path("run.json"), output.payload)
    files = [_announce_file("run.json", control)]
    files.extend(_announce_file(key, control) for key in candidate_files.values())
    files.extend(_announce_file(key, control) for key in rejected_files.values())

    return {
        "outcome": "succeeded",
        "summary": {
            "pipeline_status": output.status,
            "run_id": output.run_id,
            "counts": output.counts,
            "recommendation": output.recommendation,
            "ranked": output.ranked[:20],
            "source_count": output.source_count,
            "candidate_count": output.candidate_count,
            "scorer_name": output.scorer_name,
            "personalization_mode": output.personalization_mode,
            "message": output.message,
            "candidate_files": candidate_files,
            "rejected_files": rejected_files,
        },
        "files": files,
    }


def _read_poster_group_document(params: dict[str, Any]) -> dict[str, Any]:
    """Read and authenticate the one large group input referenced by the manifest."""
    group_file = params.get("group_file")
    if group_file != "group.json":
        raise ProtocolError("poster_group manifest must reference group.json")
    checksum = params.get("group_checksum", params.get("checksum"))
    if (
        not isinstance(checksum, str)
        or len(checksum) != 64
        or any(character not in "0123456789abcdef" for character in checksum)
    ):
        raise ProtocolError("poster_group manifest has an invalid group checksum")
    path = Path(_safe_output_name(group_file))
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ProtocolError("poster_group group.json is missing or exceeds its fixed bound")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != checksum:
        raise ProtocolError("poster_group group.json failed checksum validation")
    try:
        document = json.loads(content)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError("poster_group group.json is not valid JSON") from exc
    if not isinstance(document, dict):
        raise ProtocolError("poster_group group.json must be an object")
    return document


def _poster_group_subject(raw: dict[str, Any]) -> tuple[str, Any, dict[str, Any], str]:
    """Validate one host-resolved group member without accepting physical paths."""
    from marquee.pipeline.orchestrator import PosterSubjectInput  # noqa: PLC0415

    request = raw.get("request") if isinstance(raw.get("request"), dict) else raw
    snapshot = raw.get("subject")
    if not isinstance(snapshot, dict):
        snapshot = raw.get("snapshot") if isinstance(raw.get("snapshot"), dict) else {}
    subject_key = raw.get("subject_key")
    if not isinstance(subject_key, str) or not subject_key or len(subject_key) > 200:
        raise ProtocolError("poster_group member has an invalid subject key")
    title = request.get("title")
    if not isinstance(title, str) or not title or len(title) > 300:
        raise ProtocolError("poster_group member has an invalid title")

    movie_id = request.get("movie_id")
    series_id = request.get("series_id")
    season_id = request.get("season_id")
    episode_id = request.get("episode_id")
    if episode_id is not None:
        raise ProtocolError("poster_group does not support episode posters")
    identities = [value for value in (movie_id, series_id, season_id) if isinstance(value, int)]
    if len(identities) != 1:
        raise ProtocolError("poster_group member must name exactly one supported subject")
    if isinstance(movie_id, int):
        media_type = "movie"
    elif isinstance(season_id, int):
        media_type = "season"
    else:
        media_type = "series"

    resolved_series_id = series_id
    season_number = None
    ocr_title = None
    if media_type == "season":
        resolved_series_id = snapshot.get("series_id")
        season_number = snapshot.get("season_number")
        ocr_title = snapshot.get("ocr_title") or snapshot.get("series_title")
        if not isinstance(resolved_series_id, int) or not isinstance(season_number, int):
            raise ProtocolError("poster_group season member is missing live snapshot identity")
        if not isinstance(ocr_title, str) or not ocr_title:
            raise ProtocolError("poster_group season member is missing its live series title")

    tmdb_id = request.get("tmdb_id")
    if not isinstance(tmdb_id, int):
        tmdb_id = snapshot.get("tmdb_id")
    subject = PosterSubjectInput(
        title=title,
        media_type=media_type,
        movie_id=movie_id if media_type == "movie" else None,
        tmdb_id=tmdb_id if isinstance(tmdb_id, int) else None,
        series_id=resolved_series_id if isinstance(resolved_series_id, int) else None,
        season_id=season_id if isinstance(season_id, int) else None,
        season_number=season_number,
        ocr_title=ocr_title,
    )
    run_id = raw.get("run_id")
    if not isinstance(run_id, str) or not run_id or len(run_id) > 128:
        run_id = uuid4().hex
    gate = raw.get("text_gate") if isinstance(raw.get("text_gate"), dict) else {}
    return subject_key, subject, gate, run_id


def _bounded_poster_group_members(members: list[Any]) -> list[dict[str, Any]]:
    """Return only bounded identity/status data for the result control frame.

    The host loads the checksummed ``group-result.json`` for authoritative
    member detail.  Keeping warnings, errors, titles, timings, or candidate
    maps here would let escaped Unicode or candidate volume breach the 64 KiB
    runner protocol frame despite character-count truncation.
    """
    return [
        {
            "member_index": member.member_index,
            "subject_key": member.subject_key,
            "run_id": member.run_id,
            "status": member.status,
            "outcome": member.outcome,
            "archive_file": member.archive_file,
        }
        for member in members
    ]


def _run_poster_group(manifest: dict[str, Any], control: ControlWriter) -> dict[str, Any]:
    """Run one authenticated 1-16 member poster group inside the attempt workspace."""
    import asyncio  # noqa: PLC0415

    from marquee.pipeline.poster_group_runner import (  # noqa: PLC0415
        PosterGroupMemberInput,
        materialize_group_output,
        run_poster_group,
    )
    from marquee.pipeline.scorer import ResidualRuntimeContext  # noqa: PLC0415

    params = manifest.get("params")
    params = params if isinstance(params, dict) else {}
    document = _read_poster_group_document(params)
    library = document.get("library")
    chunk_index = document.get("chunk_index")
    raw_members = document.get("members")
    if library not in {"movies", "tv"} or not isinstance(chunk_index, int) or chunk_index < 0:
        raise ProtocolError("poster_group group.json has invalid group identity")
    if not isinstance(raw_members, list) or not 1 <= len(raw_members) <= 16:
        raise ProtocolError("poster_group group.json must contain 1-16 members")

    members = []
    subject_keys: set[str] = set()
    for raw in raw_members:
        if not isinstance(raw, dict):
            raise ProtocolError("poster_group member must be an object")
        subject_key, subject, gate_params, run_id = _poster_group_subject(raw)
        if subject_key in subject_keys:
            raise ProtocolError("poster_group member subject keys must be unique")
        subject_keys.add(subject_key)
        if (subject.media_type == "movie") != (library == "movies"):
            raise ProtocolError("poster_group member does not match the declared library")
        members.append(
            PosterGroupMemberInput(
                subject_key=subject_key,
                subject=subject,
                ocr_gate=_ocr_gate_context({"text_gate": gate_params}, subject),
                run_id=run_id,
            )
        )

    personalization_mode = params.get("personalization_mode", "collecting")
    if personalization_mode not in {"collecting", "personalized"}:
        raise ProtocolError("poster_group manifest has an invalid personalization mode")
    profile = params.get("taste_profile") if isinstance(params.get("taste_profile"), dict) else {}
    residual = (
        params.get("ranking_residual")
        if isinstance(params.get("ranking_residual"), dict)
        else {}
    )
    runtime_context = None
    if personalization_mode == "personalized" and residual:
        checksum = profile.get("checksum")
        generation = profile.get("generation")
        residual_checksum = residual.get("checksum")
        baseline = params.get("baseline_signature")
        if (
            isinstance(checksum, str)
            and isinstance(generation, int)
            and isinstance(residual_checksum, str)
            and isinstance(baseline, str)
        ):
            runtime_context = ResidualRuntimeContext(
                library=library,
                baseline_signature=baseline,
                profile_checksum=checksum,
                profile_generation=generation,
                artifact_id=(
                    residual.get("artifact_id")
                    if isinstance(residual.get("artifact_id"), int)
                    else None
                ),
                artifact_checksum=residual_checksum,
            )

    cursor = 0

    def emit_progress(event: Any) -> None:
        nonlocal cursor
        cursor += 1
        frame: dict[str, Any] = {
            "v": PROTOCOL_VERSION,
            "type": "progress",
            "stage": event.stage,
            "state": event.state,
            "cursor": cursor,
        }
        for field in ("scope", "subject", "done", "total", "survivors"):
            value = getattr(event, field, None)
            if value is not None:
                frame[field] = value
        with contextlib.suppress(Exception):
            control.emit(frame)

    extractor, residual_path = _poster_feature_runtime(personalization_mode)
    extractor.preflight()
    output = asyncio.run(
        run_poster_group(
            library=library,
            chunk_index=chunk_index,
            members=members,
            out_dir=Path.cwd(),
            feature_extractor=extractor,
            residual_path=residual_path,
            residual_context=runtime_context,
            personalization_mode=personalization_mode,
            progress=emit_progress,
        )
    )
    produced = materialize_group_output(output, Path.cwd())
    # The group report is announced first because it is required fallback
    # evidence; result files remain empty and the host trusts only file frames.
    for key in produced:
        _announce_file(key, control)

    counts = {
        "succeeded_count": sum(member.outcome == "succeeded" for member in output.members),
        "no_change_count": sum(member.outcome == "no_change" for member in output.members),
        "review_required_count": sum(
            member.outcome == "review_required" for member in output.members
        ),
        "failed_count": sum(member.outcome == "failed" for member in output.members),
    }
    summary = {
        "library": library,
        "chunk_index": chunk_index,
        "outcome": output.outcome,
        "member_count": len(output.members),
        **counts,
        "run_ids": [member.run_id for member in output.members],
        "failed_subject_keys": [
            member.subject_key for member in output.members if member.outcome == "failed"
        ],
        "members": _bounded_poster_group_members(output.members),
        "group_result_file": "group-result.json",
    }
    return {"outcome": "succeeded", "summary": summary, "files": []}


# Source modes where the handler has already staged every training image under
# ./training in the attempt workspace. Anything else leaves the trainer without a
# training directory, which it rejects rather than guessing at a source.
_STAGED_SOURCE_MODES = frozenset({"fixture", "library_scan"})


def _run_taste_profile(manifest: dict[str, Any], control: ControlWriter) -> dict[str, Any]:
    """Run the real taste-profile trainer confined to the workspace (native .npz).

    ``source.mode`` selects the exemplar source. ``fixture`` is frozen exemplar
    evidence and ``library_scan`` is artwork copied out of the media library; both
    arrive pre-staged under ``training/`` in the workspace cwd, optionally split into
    per-kind subdirectories. Output is written only to ``profile.npz`` in the
    workspace — never the configured live taste-profile path.
    """
    from pathlib import Path  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415

    from marquee.ml.namespaces import get_namespace  # noqa: PLC0415
    from marquee.ml.taste_trainer import rebuild_profile  # noqa: PLC0415

    params = manifest.get("params")
    params = params if isinstance(params, dict) else {}
    library = params.get("library", "movies")
    source = params.get("source") if isinstance(params.get("source"), dict) else {}
    source_mode = source.get("mode", "library")
    namespace = get_namespace(library)
    training = Path("training")
    negative = Path("negative")
    # Both staged modes mean the same thing to the runner: the handler already
    # placed every training image under ./training. "fixture" is frozen exemplar
    # evidence; "library_scan" is artwork copied out of the media library.
    staged = source_mode in _STAGED_SOURCE_MODES
    training_dir = training if staged and training.is_dir() else None
    negative_dir = negative if staged and negative.is_dir() else None
    positive_weights = (
        source.get("positive_weights") if isinstance(source.get("positive_weights"), dict) else None
    )
    negative_weights = (
        source.get("negative_weights") if isinstance(source.get("negative_weights"), dict) else None
    )
    asset_identity = (
        source.get("asset_identity") if isinstance(source.get("asset_identity"), dict) else None
    )
    output = Path("profile.npz")

    rebuild_profile(
        training_dir=training_dir,
        negative_dir=negative_dir,
        output=output,
        skip_ocr=bool(params.get("skip_ocr", True)),
        skip_dino=bool(params.get("skip_dino", True)),
        progress_callback=_trainer_progress_forwarder(control),
        namespace=namespace,
        positive_weights=positive_weights,
        negative_weights=negative_weights,
        asset_identity=asset_identity,
    )

    files = [_announce_file("profile.npz", control)]
    exemplars = 0
    negatives = 0
    with np.load(output, allow_pickle=False) as data:
        if "embeddings" in data.files:
            exemplars = int(np.asarray(data["embeddings"]).shape[0])
        if "neg_embeddings" in data.files:
            negatives = int(np.asarray(data["neg_embeddings"]).shape[0])
    return {
        "outcome": "succeeded",
        "summary": {
            "family": "taste_profile",
            "library": library,
            "exemplars": exemplars,
            "negatives": negatives,
        },
        "files": files,
    }


def _run_taste_map(manifest: dict[str, Any], control: ControlWriter) -> dict[str, Any]:
    """Build a native map from the profile staged by the coordinator."""
    from pathlib import Path  # noqa: PLC0415

    from marquee.ml.namespaces import get_namespace  # noqa: PLC0415
    from marquee.ml.taste_map import build_map  # noqa: PLC0415

    params = manifest.get("params")
    params = params if isinstance(params, dict) else {}
    library = params.get("library", "movies")
    namespace = get_namespace(library)
    profile = Path("profile.npz")
    output = Path("map.npz")
    if not profile.is_file():
        raise FileNotFoundError("staged active taste profile is missing")

    result = build_map(
        progress_callback=_trainer_progress_forwarder(control),
        namespace=namespace,
        output=output,
        profile_path=profile,
        generate_thumbnails=False,
    )
    files = [_announce_file("map.npz", control)]
    summary = result.get("summary") if isinstance(result, dict) else {}
    projection = result.get("projection") if isinstance(result, dict) else {}
    return {
        "outcome": "succeeded",
        "summary": {
            "family": "taste_map",
            "library": library,
            "exemplars": int(summary.get("exemplars", 0) or 0),
            "projection_method": str(projection.get("method", "unknown")),
        },
        "files": files,
    }


def _run_enrichment(manifest: dict[str, Any], control: ControlWriter) -> dict[str, Any]:
    """Enrich a staged active profile and emit a native successor profile."""
    from pathlib import Path  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415

    from marquee.ml.artifact_codec import GENRES_JSON_KEY  # noqa: PLC0415
    from marquee.ml.profile_enrich import enrich  # noqa: PLC0415

    params = manifest.get("params")
    params = params if isinstance(params, dict) else {}
    library = params.get("library", "movies")
    source = Path("source-profile.npz")
    output = Path("profile.npz")
    if not source.is_file():
        raise FileNotFoundError("staged active taste profile is missing")
    control.emit({"v": PROTOCOL_VERSION, "type": "progress", "stage": "enriching"})
    enrich(
        use_tmdb=bool(params.get("use_tmdb", False)),
        profile_path=source,
        output=output,
        cache_path=Path("genre-cache.json"),
        progress_callback=_trainer_progress_forwarder(control),
    )
    resolved = 0
    exemplars = 0
    with np.load(output, allow_pickle=False) as data:
        exemplars = int(np.asarray(data["embeddings"]).shape[0])
        if GENRES_JSON_KEY in data.files:
            resolved = sum(1 for value in data[GENRES_JSON_KEY].tolist() if str(value) != "[]")
    files = [_announce_file("profile.npz", control)]
    return {
        "outcome": "succeeded",
        "summary": {
            "family": "taste_profile",
            "operation": "enrichment",
            "library": library,
            "exemplars": exemplars,
            "resolved": resolved,
        },
        "files": files,
    }


def _run_ranking_residual(manifest: dict[str, Any], control: ControlWriter) -> dict[str, Any]:
    """Train and evaluate a bounded residual from a frozen canonical event snapshot."""
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415
    from types import SimpleNamespace  # noqa: PLC0415

    from marquee.ml.residual import (  # noqa: PLC0415
        ResidualArtifact,
        build_residual_pairs,
        train_residual,
    )

    params = manifest.get("params")
    params = params if isinstance(params, dict) else {}
    snapshot = Path("preference-events.json")
    rows: list[dict[str, Any]] = []
    if snapshot.is_file():
        if snapshot.stat().st_size > 64 * 1024 * 1024:
            raise ProtocolError("preference snapshot exceeds the runner limit")
        try:
            loaded = json.loads(snapshot.read_text())
        except json.JSONDecodeError as exc:
            raise ProtocolError("preference snapshot is invalid") from exc
        if not isinstance(loaded, list) or len(loaded) > 100_000:
            raise ProtocolError("preference snapshot row count is invalid")
        rows = [row for row in loaded if isinstance(row, dict)]

    control.emit({"v": PROTOCOL_VERSION, "type": "progress", "stage": "training"})
    events = [SimpleNamespace(**row) for row in rows]
    pairs = build_residual_pairs(events)
    active_path = Path("active-residual.npz")
    active_residual = ResidualArtifact.load(active_path) if active_path.is_file() else None
    artifact, report = train_residual(
        pairs,
        namespace=str(params.get("library", "movies")),
        baseline=str(params.get("baseline_signature", "")),
        profile_checksum=str(params.get("profile_checksum", "")),
        profile_generation=int(params.get("profile_generation", 0)),
        evidence_revision=str(params.get("evidence_revision", "")),
        seed=int(params.get("seed", 0)),
        min_subjects=int(params.get("min_subjects", 25)),
        min_pairs=int(params.get("min_pairs", 200)),
        min_improvement=float(params.get("min_improvement", 0.02)),
        active_residual=active_residual,
    )
    summary = {
        "family": "ranking_residual",
        "library": params.get("library", "movies"),
        "event_rows": len(rows),
        "pairs": len(pairs),
        "subjects": len({pair.subject for pair in pairs}),
        **report,
    }
    if artifact is None:
        # ``outcome`` is the runner transport contract and therefore only
        # reports successful execution here. The coordinator maps this explicit
        # publication decision onto the durable no-change terminal outcome.
        summary["publication_outcome"] = "no_change"
        return {"outcome": "succeeded", "summary": summary, "files": []}
    artifact.save(Path("residual.npz"))
    summary["publication_outcome"] = "succeeded"
    return {
        "outcome": "succeeded",
        "summary": summary,
        "files": [_announce_file("residual.npz", control)],
    }


def _cooperative_hold() -> None:
    """Block until SIGINT/SIGTERM, then exit abruptly with no result frame."""

    def _exit(*_: object) -> None:
        os._exit(0)

    signal.signal(signal.SIGINT, _exit)
    signal.signal(signal.SIGTERM, _exit)
    while True:
        time.sleep(0.02)


def _ignore_until_kill() -> None:
    """Ignore cooperative/term signals; only SIGKILL stops this tree."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        time.sleep(0.02)


_OPERATIONS: dict[RunnerOperation, OperationHandler] = {
    RunnerOperation.NOOP: _run_noop,
    RunnerOperation.POSTER_SINGLE: _run_poster_single,
    RunnerOperation.POSTER_GROUP: _run_poster_group,
    RunnerOperation.TASTE_PROFILE: _run_taste_profile,
    RunnerOperation.TASTE_MAP: _run_taste_map,
    RunnerOperation.ENRICHMENT: _run_enrichment,
    RunnerOperation.RANKING_RESIDUAL: _run_ranking_residual,
}


def _emit_result(control: ControlWriter, result: dict[str, Any]) -> None:
    control.emit(
        {
            "v": PROTOCOL_VERSION,
            "type": "result",
            "outcome": result.get("outcome", "failed"),
            "summary": result.get("summary", {}),
            "files": result.get("files", []),
        }
    )


def _emit_error(control: ControlWriter, exc: BaseException) -> None:
    with contextlib.suppress(Exception):
        control.emit(
            {
                "v": PROTOCOL_VERSION,
                "type": "result",
                "outcome": "failed",
                "error": {"code": type(exc).__name__, "message": str(exc)[:500]},
            }
        )


def run(operation: RunnerOperation, control: ControlWriter) -> int:
    """Emit the ready barrier, read the manifest, dispatch, and return an exit code."""
    control.emit({"v": PROTOCOL_VERSION, "type": "ready", "operation": operation.value})
    manifest = read_manifest_from_stdin(0)
    if manifest.get("operation") != operation.value:
        raise ProtocolError("manifest operation does not match the invocation")
    handler = _OPERATIONS.get(operation)
    if handler is None:
        raise RunnerOperationNotEnabledError(operation.value)
    result = handler(manifest, control)
    _emit_result(control, result)
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        return _EXIT_BAD_INVOCATION
    try:
        operation = RunnerOperation(sys.argv[1])
    except ValueError:
        return _EXIT_BAD_INVOCATION
    raw_fd = os.environ.get(CONTROL_FD_ENV)
    if raw_fd is None or not raw_fd.isdigit():
        return _EXIT_NO_CONTROL
    control = ControlWriter(int(raw_fd))
    try:
        return run(operation, control)
    except BaseException as exc:  # noqa: BLE001 - report every failure as a frame
        _emit_error(control, exc)
        return _EXIT_OPERATION_FAILED
    finally:
        with contextlib.suppress(Exception):
            control.close()


if __name__ == "__main__":
    raise SystemExit(main())
