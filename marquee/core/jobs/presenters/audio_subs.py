"""Audio and subtitle presenter family.

Renders requested selectors, before/after track inventory, per-track language,
codec, channels, title, default/forced/hearing-impaired flags,
embedded/external state, per-target outcomes with the failed stage,
generated/extracted artifacts, rescan results, and atomicity.  For an
all-or-nothing remux failure, every requested embedded target is reported as
`not_applied` — the presentation never implies a partial mutation that did not
commit.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from marquee.core.jobs.presentation import (
    BadgeValue,
    BeforeAfterRow,
    BeforeAfterSection,
    ChangeItem,
    ChangeListSection,
    Fact,
    FactsSection,
    NoticeSection,
    NumberValue,
    PresentationAction,
    PresentationSection,
    TextValue,
    TrackRow,
    TrackTableSection,
)
from marquee.core.jobs.presenters.base import JobPresenter, PresenterContext

AUDIO_SUBS_JOB_TYPES = (
    "subtitle_scan",
    "subtitle_scan_all",
    "audio_subs_deep_scan",
    "audio_remove",
    "track_remove",
    "subtitle_remove",
    "subtitle_embed",
    "subtitle_metadata",
    "audio_reorder",
    "subtitle_extract",
    "subtitle_generate",
    "subtitle_policy_audit",
    "subtitle_policy",
    "subtitle_restore",
)

_HEADLINES = {
    "subtitle_scan": "Scan tracks in this file",
    "subtitle_scan_all": "Scan the whole library for track changes",
    "audio_subs_deep_scan": "Deep-scan audio and subtitle metadata",
    "audio_remove": "Remove audio tracks",
    "track_remove": "Remove tracks",
    "subtitle_remove": "Remove subtitle tracks",
    "subtitle_embed": "Embed subtitles into the container",
    "subtitle_metadata": "Update subtitle metadata",
    "audio_reorder": "Reorder audio streams",
    "subtitle_extract": "Extract subtitles to a sidecar file",
    "subtitle_generate": "Generate subtitles from audio",
    "subtitle_policy_audit": "Audit subtitle policy",
    "subtitle_policy": "Apply the subtitle policy",
    "subtitle_restore": "Restore tracks from backup",
}

_MUTATING = {
    "audio_remove",
    "track_remove",
    "subtitle_remove",
    "subtitle_embed",
    "subtitle_metadata",
    "audio_reorder",
    "subtitle_extract",
    "subtitle_policy",
    "subtitle_restore",
}


def _count_headline(base: str, ctx: PresenterContext) -> str:
    subtitle_count = ctx.summary_value("subtitle_targets", int)
    audio_count = ctx.summary_value("audio_targets", int)
    parts: list[str] = []
    if isinstance(subtitle_count, int) and subtitle_count > 0:
        parts.append(
            f"{subtitle_count} subtitle track{'s' if subtitle_count != 1 else ''}"
        )
    if isinstance(audio_count, int) and audio_count > 0:
        parts.append(f"{audio_count} audio track{'s' if audio_count != 1 else ''}")
    if parts:
        return f"Remove {' and '.join(parts)}"
    return base


class AudioSubsPresenter(JobPresenter):
    def action(self, ctx: PresenterContext) -> PresentationAction:
        headline = _HEADLINES[self.job_type]
        if self.job_type in {"audio_remove", "track_remove", "subtitle_remove"}:
            headline = _count_headline(headline, ctx)
        elif self.job_type == "subtitle_generate":
            language = ctx.summary_value("language", str)
            source = ctx.summary_value("source_track", str)
            if language:
                headline = f"Generate {language} subtitles"
                if source:
                    headline = f"{headline} from {source}"
        explanation = None
        selectors = ctx.summary_value("requested_selectors", str)
        if selectors:
            explanation = f"Requested selection: {selectors}"
        return PresentationAction(headline=headline, explanation=explanation)

    def _track_rows(self, ctx: PresenterContext) -> tuple[TrackRow, ...]:
        raw = ctx.summary.get("tracks")
        if raw is None:
            return ()
        if not isinstance(raw, list):
            ctx.warn("malformed_evidence", "Stored track evidence has the wrong shape.")
            return ()
        rows: list[TrackRow] = []
        malformed = False
        for item in raw[:200]:
            if not isinstance(item, dict):
                malformed = True
                continue
            try:
                rows.append(TrackRow.model_validate(item))
            except ValidationError:
                malformed = True
        if malformed:
            ctx.warn(
                "malformed_evidence",
                "Some stored track evidence could not be validated and was omitted.",
            )
        return tuple(rows)

    def sections(self, ctx: PresenterContext) -> tuple[PresentationSection, ...]:
        sections: list[PresentationSection] = []
        facts: list[Fact] = []

        for key, label in (
            ("container", "Container"),
            ("provider", "Provider"),
            ("model", "Model"),
            ("output_name", "Output file"),
        ):
            value = ctx.summary_value(key, str)
            if value:
                facts.append(Fact(label=label, value=TextValue(text=value)))
        atomic = ctx.summary_value("atomic", bool)
        if atomic is not None:
            facts.append(
                Fact(
                    label="All-or-nothing remux",
                    value=BadgeValue(
                        text="Atomic" if atomic else "Independent targets",
                        tone="neutral",
                    ),
                )
            )
        for key, label in (
            ("backup_created", "Backup"),
            ("rescan_ok", "Post-change rescan"),
        ):
            value = ctx.summary_value(key, bool)
            if value is not None:
                facts.append(
                    Fact(
                        label=label,
                        value=BadgeValue(
                            text="OK" if value else "Not confirmed",
                            tone="positive" if value else "warning",
                        ),
                    )
                )
        if facts:
            sections.append(FactsSection(title="Operation", facts=tuple(facts)))

        rows: list[BeforeAfterRow] = []
        for kind_label, before_key, after_key in (
            ("Audio tracks", "audio_before", "audio_after"),
            ("Embedded subtitles", "subtitle_before", "subtitle_after"),
            ("External subtitles", "external_before", "external_after"),
        ):
            before = ctx.summary_value(before_key, int)
            after = ctx.summary_value(after_key, int)
            if isinstance(before, int) or isinstance(after, int):
                rows.append(
                    BeforeAfterRow(
                        label=kind_label,
                        before=(
                            NumberValue(value=before) if isinstance(before, int) else None
                        ),
                        after=NumberValue(value=after) if isinstance(after, int) else None,
                        changed=before != after,
                    )
                )
        if rows:
            sections.append(BeforeAfterSection(title="Track inventory", rows=tuple(rows)))

        tracks = self._track_rows(ctx)
        if tracks:
            sections.append(TrackTableSection(title="Tracks", tracks=tracks))

        changes = self._change_items(ctx, tracks)
        if changes:
            sections.append(ChangeListSection(title="Requested changes", items=changes))

        if (
            self.job_type in _MUTATING
            and ctx.job.outcome == "failed"
            and any(track.outcome == "not_applied" for track in tracks)
        ):
            sections.append(
                NoticeSection(
                    tone="info",
                    message=(
                        "The remux is all-or-nothing: because it failed, none of the "
                        "requested embedded changes were applied to the file."
                    ),
                )
            )
        return tuple(sections)

    def _change_items(
        self, ctx: PresenterContext, tracks: tuple[TrackRow, ...]
    ) -> tuple[ChangeItem, ...]:
        items: list[ChangeItem] = []
        for index, track in enumerate(tracks):
            if track.requested is None or track.outcome is None:
                continue
            label_bits: list[Any] = [track.language]
            if track.codec:
                label_bits.append(track.codec)
            if track.title:
                label_bits.append(track.title)
            items.append(
                ChangeItem(
                    target_key=f"track:{index}",
                    target_label=" · ".join(str(bit) for bit in label_bits),
                    requested=track.requested,
                    outcome=track.outcome,
                    reason=track.reason,
                )
            )
        return tuple(items[:200])
