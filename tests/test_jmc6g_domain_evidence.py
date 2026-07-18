"""JMC6G fenced domain projection and tracked-probe closure."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from marquee.core.jobs.media_mutation_support import (
    TrackMutationError,
    bind_managed_subtitle,
    persist_post_mutation_inventory,
    probe_inventory,
)
from marquee.core.jobs.track_selectors import TrackEntryV1, TrackFactsV1, TrackInventoryV1
from marquee.database import _get_session_factory
from marquee.models import (
    ManagedSubtitleAsset,
    ManagedSubtitleBinding,
    MediaFile,
    SubtitleInventory,
    SubtitleTrack,
)


class _Writer:
    def __init__(self, owns: bool = True) -> None:
        self.owns = owns

    async def owns_current_attempt(self, _session) -> bool:
        return self.owns


def _context(*, owns: bool = True, launcher=None):
    return SimpleNamespace(
        delivery=SimpleNamespace(canonical_job_id="a" * 32),
        attempt=SimpleNamespace(attempt_id=41, fence_token=7),
        writer=_Writer(owns),
        session_factory=_get_session_factory(),
        process_launcher=launcher,
    )


async def _media(db) -> MediaFile:
    row = MediaFile(
        source="standalone",
        source_key=f"standalone:{uuid4().hex}",
        path="/media/example.mkv",
        is_active=True,
        is_present=True,
    )
    db.add(row)
    await db.commit()
    return row


async def test_post_mutation_inventory_is_fenced_and_ui_authoritative(db) -> None:
    media = await _media(db)
    media_id = media.id
    inventory = TrackInventoryV1(
        signature="sha256:" + "b" * 64,
        container="matroska",
        entries=(
            TrackEntryV1(
                track_key="audio-main",
                stream_index=1,
                facts=TrackFactsV1(
                    kind="audio",
                    source="embedded",
                    language_tag="en",
                    codec="aac",
                    channels=2,
                    is_default=True,
                ),
            ),
            TrackEntryV1(
                track_key="subtitle-main",
                stream_index=2,
                tool_track_id=3,
                facts=TrackFactsV1(
                    kind="subtitle",
                    source="embedded",
                    language_tag="en",
                    codec="subrip",
                    title="English",
                ),
            ),
        ),
    )

    await persist_post_mutation_inventory(
        _context(), media_file_id=media_id, inventory=inventory
    )
    await db.rollback()
    row = await db.scalar(
        select(SubtitleInventory).where(SubtitleInventory.media_file_id == media_id)
    )
    assert row is not None
    assert row.file_signature == inventory.signature
    assert row.source_job_id == "a" * 32
    assert row.source_attempt_id == 41
    assert row.source_fence_token == 7
    assert row.audio_streams_json[0]["codec"] == "aac"
    tracks = (
        await db.scalars(select(SubtitleTrack).where(SubtitleTrack.inventory_id == row.id))
    ).all()
    assert [(track.language_tag, track.codec) for track in tracks] == [("en", "subrip")]

    with pytest.raises(TrackMutationError, match="lost ownership"):
        await persist_post_mutation_inventory(
            _context(owns=False),
                media_file_id=media_id,
            inventory=inventory.model_copy(update={"signature": "sha256:" + "c" * 64}),
        )
    await db.rollback()
    assert (
        await db.scalar(
            select(SubtitleInventory.file_signature).where(
                    SubtitleInventory.media_file_id == media_id
            )
        )
        == inventory.signature
    )


async def test_managed_sidecar_binding_preserves_job_provenance_when_media_retires(db) -> None:
    media = await _media(db)
    media_id = media.id
    asset = ManagedSubtitleAsset(
        id=uuid4().hex,
        cache_path="jmc5/managed-subtitles/example.srt",
        content_sha256="d" * 64,
        language_tag="en",
        source="generated",
        active=True,
    )
    db.add(asset)
    await db.commit()
    asset_id = asset.id

    await bind_managed_subtitle(_context(), asset_id=asset_id, media_file_id=media_id)
    await db.rollback()
    binding = await db.scalar(
        select(ManagedSubtitleBinding).where(ManagedSubtitleBinding.asset_id == asset_id)
    )
    assert binding is not None
    assert binding.media_file_id == media_id
    assert binding.source_job_id == "a" * 32
    assert binding.source_attempt_id == 41
    assert binding.source_fence_token == 7

    await db.execute(delete(MediaFile).where(MediaFile.id == media_id))
    await db.commit()
    db.expire_all()
    binding = await db.scalar(
        select(ManagedSubtitleBinding).where(ManagedSubtitleBinding.asset_id == asset_id)
    )
    assert binding is not None
    assert binding.media_file_id is None
    assert binding.source_job_id == "a" * 32


async def test_mutation_probe_uses_tracked_launcher_for_all_native_children(tmp_path) -> None:
    calls: list[str] = []

    class _Process:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

        async def wait(self):
            return SimpleNamespace(
                exit_code=0,
                stdout=SimpleNamespace(captured=self.payload),
            )

    class _Launcher:
        async def launch(self, tool: str, _args: list[str]):
            calls.append(tool)
            payload = (
                b'{"format":{"format_name":"matroska"},"streams":[],"chapters":[]}'
                if tool == "ffprobe"
                else b'{"container":{"properties":{}},"tracks":[]}'
            )
            return _Process(payload)

    result = await probe_inventory(
        _context(launcher=_Launcher()), tmp_path / "candidate.mkv", "sha256:" + "e" * 64
    )
    assert result.container == "matroska"
    assert calls == ["ffprobe", "mkvmerge"]
