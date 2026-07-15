"""B3 subtitle generation with a stubbed provider (JMC5B §6.3/B14/B15/B16).

The provider is always a local stub.  `.env` configures a real Subgen at
``http://localhost:9000`` and pydantic-settings reads it, so contacting the live
provider is explicitly forbidden by the JMC5B safety boundary — no test here
performs any network I/O.

Webhook completion is deferred (B16), so completion is discovered only through
bounded, cancellable reconciliation polling.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from marquee.core.jobs.handlers_generation import (
    GenerationError,
    execute_subtitle_generate,
    wait_for_provider,
)
from marquee.core.subtitles.generators.base import (
    GenerationRequest,
    ProviderState,
    ProviderSubmission,
)
from tests.support.jmc5b_harness import execution_context as _context
from tests.support.jmc5b_harness import media_file_row as _media_file
from tests.support.media_fixtures import AudioSpec, build_mkv, require_media_tools

pytestmark = pytest.mark.usefixtures("jmc5b_media_roots")

SRT = b"1\n00:00:00,000 --> 00:00:01,000\ngenerated\n\n"


class StubProvider:
    """A local provider double; performs no network I/O."""

    id = "stub-provider"

    def __init__(self, states, *, accepted=True, detail=None) -> None:
        self.id = "stub-provider"
        self._states = list(states)
        self._accepted = accepted
        self._detail = detail
        self.submissions = 0
        self.polls = 0

    async def submit(self, request: GenerationRequest) -> ProviderSubmission:
        self.submissions += 1
        return ProviderSubmission(
            accepted=self._accepted, submitted_at="2026-07-15T00:00:00Z", detail=self._detail
        )

    async def reconcile(self, request: GenerationRequest) -> ProviderState:
        self.polls += 1
        return self._states[min(self.polls - 1, len(self._states) - 1)]


def _ctx(cancelled: bool = False):
    return SimpleNamespace(cancellation=SimpleNamespace(is_cancelled=lambda: cancelled))


async def _noop_sleep(_seconds: float) -> None:
    return None


@pytest.mark.asyncio
async def test_polling_stops_at_produced_and_reports_output() -> None:
    provider = StubProvider(
        [
            ProviderState(state="pending"),
            ProviderState(state="running"),
            ProviderState(state="produced", output_path="/tmp/out.srt"),
        ]
    )
    outcome = await wait_for_provider(
        _ctx(), provider, GenerationRequest(media_file_id=1, local_media_path="/m.mkv"),
        sleep=_noop_sleep,
    )
    assert outcome.state == "produced"
    assert outcome.output_path == "/tmp/out.srt"
    assert outcome.polls == 3


@pytest.mark.asyncio
async def test_polling_is_bounded_by_a_deadline() -> None:
    """B16: the provider is never trusted to finish; waiting is bounded."""
    provider = StubProvider([ProviderState(state="running")])
    clock = iter([0.0, 0.0, 5.0, 20.0, 100.0])
    with pytest.raises(GenerationError) as excinfo:
        await wait_for_provider(
            _ctx(), provider, GenerationRequest(media_file_id=1, local_media_path="/m.mkv"),
            deadline=10.0, sleep=_noop_sleep, now=lambda: next(clock),
        )
    assert excinfo.value.reason_code == "provider_timeout"
    assert excinfo.value.stage == "wait"


@pytest.mark.asyncio
async def test_cancellation_stops_waiting_without_the_provider_responding() -> None:
    provider = StubProvider([ProviderState(state="running")])
    with pytest.raises(GenerationError) as excinfo:
        await wait_for_provider(
            _ctx(cancelled=True), provider,
            GenerationRequest(media_file_id=1, local_media_path="/m.mkv"),
            sleep=_noop_sleep,
        )
    assert excinfo.value.reason_code == "cancelled"
    assert provider.polls == 0  # cancellation is checked before polling


@pytest.mark.asyncio
async def test_provider_failure_and_timeout_states_are_named_honestly() -> None:
    for state, expected in (("failed", "provider_failed"), ("timeout", "provider_timeout")):
        provider = StubProvider([ProviderState(state=state, message="nope")])
        with pytest.raises(GenerationError) as excinfo:
            await wait_for_provider(
                _ctx(), provider,
                GenerationRequest(media_file_id=1, local_media_path="/m.mkv"),
                sleep=_noop_sleep,
            )
        assert excinfo.value.reason_code == expected


async def _fixture(db, tmp_path: Path):
    require_media_tools()
    path = build_mkv(
        tmp_path / "library", "show.mkv",
        audio=(AudioSpec(language="eng", channels=2, codec="aac"),),
    )
    return path, await _media_file(db, path)


def _install(monkeypatch: pytest.MonkeyPatch, provider) -> None:
    """Force the stub; the live SUBGEN_URL is never reachable from here."""
    import marquee.core.subtitles.generation as generation

    monkeypatch.setattr(generation, "get_generator", lambda _id: provider)


@pytest.mark.asyncio
async def test_generation_publishes_a_managed_sidecar(
    db, tmp_path: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, row = await _fixture(db, tmp_path)
    output = tmp_path / "provider-out.srt"
    output.write_bytes(SRT)
    provider = StubProvider([ProviderState(state="produced", output_path=str(output))])
    _install(monkeypatch, provider)

    context = await _context(
        db, tmp_path, job_type="subtitle_generate",
        request={"media_file_id": row.id, "language_tag": "eng", "publish": "sidecar"},
    )
    result = await execute_subtitle_generate(context)

    assert result["outcome"] == "succeeded"
    assert result["provider"] == "stub-provider"
    assert result["generated"] is True
    # B15: a sidecar publish is explicitly not an embed.
    assert result["embedded"] is False
    assert result["generation_atomicity"]["published"] is True
    assert result["embed_atomicity"] is None
    assert result["actual_inventory"] is None
    sidecar = result["sidecar"]
    assert (data_dir / sidecar["storage_key"]).read_bytes() == SRT
    # B14: the job never rewrote its own request.
    assert context.request["publish"] == "sidecar"
    # The source is untouched by a sidecar generation.
    assert path.exists()


@pytest.mark.asyncio
async def test_generation_can_embed_with_backup_and_authoritative_rescan(
    db, tmp_path: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, row = await _fixture(db, tmp_path)
    output = tmp_path / "provider-embed.srt"
    output.write_bytes(SRT)
    provider = StubProvider([ProviderState(state="produced", output_path=str(output))])
    _install(monkeypatch, provider)

    context = await _context(
        db,
        tmp_path,
        job_type="subtitle_generate",
        request={"media_file_id": row.id, "language_tag": "eng", "publish": "embed"},
    )
    result = await execute_subtitle_generate(context)

    assert result["outcome"] == "succeeded"
    assert result["reason_code"] == "generated_and_embedded"
    assert result["generated"] is True
    assert result["embedded"] is True
    assert result["generation_atomicity"]["published"] is True
    assert result["embed_atomicity"]["published"] is True
    assert result["backup"]["checksum"]
    assert result["actual_inventory"] is not None
    assert len(result["actual_inventory"]["entries"]) == len(result["before_inventory"]["entries"]) + 1
    assert (data_dir / result["sidecar"]["storage_key"]).read_bytes() == SRT
    assert context.request["publish"] == "embed"
    assert path.exists()


@pytest.mark.asyncio
async def test_rejected_submission_publishes_nothing(
    db, tmp_path: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _path, row = await _fixture(db, tmp_path)
    provider = StubProvider([], accepted=False, detail="queue is full")
    _install(monkeypatch, provider)

    result = await execute_subtitle_generate(
        await _context(
            db, tmp_path, job_type="subtitle_generate",
            request={"media_file_id": row.id, "language_tag": "eng"},
        )
    )
    assert result["outcome"] == "failed"
    assert result["target_outcomes"][0]["reason_code"] == "provider_rejected"
    assert result["generated"] is False
    assert result["sidecar"] is None
    assert result["atomicity"]["published"] is False


@pytest.mark.asyncio
async def test_invalid_provider_output_is_generated_but_not_published(
    db, tmp_path: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B15: "generated but nothing published" must be distinguishable."""
    _path, row = await _fixture(db, tmp_path)
    output = tmp_path / "bad.srt"
    output.write_bytes(b"no cue timings here")
    provider = StubProvider([ProviderState(state="produced", output_path=str(output))])
    _install(monkeypatch, provider)

    result = await execute_subtitle_generate(
        await _context(
            db, tmp_path, job_type="subtitle_generate",
            request={"media_file_id": row.id, "language_tag": "eng"},
        )
    )
    assert result["outcome"] == "failed"
    assert result["generated"] is True  # the provider did produce something
    assert result["embedded"] is False
    assert result["sidecar"] is None  # but nothing was published
    assert result["target_outcomes"][0]["stage"] == "validate"


@pytest.mark.asyncio
async def test_late_output_after_cancellation_cannot_publish(
    db, tmp_path: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B19/§6.3: output arriving after cancellation must not be published."""
    _path, row = await _fixture(db, tmp_path)
    output = tmp_path / "late.srt"
    output.write_bytes(SRT)
    provider = StubProvider([ProviderState(state="produced", output_path=str(output))])
    _install(monkeypatch, provider)

    context = await _context(
        db, tmp_path, job_type="subtitle_generate",
        request={"media_file_id": row.id, "language_tag": "eng"},
    )
    # Cancellation observed after the provider produced output but before publish.
    calls = {"n": 0}

    def _late_cancel() -> bool:
        # Call 1 is the poll-loop check (still running); call 2 is the handler's
        # pre-publish check, which must observe the cancellation.
        calls["n"] += 1
        return calls["n"] > 1

    context.cancellation = SimpleNamespace(is_cancelled=_late_cancel)
    result = await execute_subtitle_generate(context)

    assert result["outcome"] == "failed"
    assert result["target_outcomes"][0]["reason_code"] == "cancelled"
    assert result["generated"] is True
    assert result["sidecar"] is None
    managed = data_dir / "jmc5/managed-subtitles"
    assert not managed.exists() or not [p for p in managed.iterdir() if not p.name.startswith(".")]


@pytest.mark.asyncio
async def test_missing_provider_output_path_fails_at_download(
    db, tmp_path: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _path, row = await _fixture(db, tmp_path)
    provider = StubProvider([ProviderState(state="produced", output_path=None)])
    _install(monkeypatch, provider)

    result = await execute_subtitle_generate(
        await _context(
            db, tmp_path, job_type="subtitle_generate",
            request={"media_file_id": row.id, "language_tag": "eng"},
        )
    )
    assert result["outcome"] == "failed"
    assert result["target_outcomes"][0]["stage"] == "download"
    assert result["sidecar"] is None
