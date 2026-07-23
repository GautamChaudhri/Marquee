"""Letterbox presenter family.

Renders detection scope and samples, movie/show/season/episode context, source
dimensions and aspect, detected crop, confidence and variability, the
requested operation, tag/re-encode/revert results, output dimensions,
encoder/fallback, before/after size, and validation evidence.  Batch progress
distinguishes the overall scope from the current show/season/episode/sample
through the progress contract's overall/current scopes.
"""

from __future__ import annotations

from marquee.core.jobs.letterbox_mutation_documents import LetterboxMutationResultV1
from marquee.core.jobs.presentation import (
    BadgeValue,
    BeforeAfterRow,
    BeforeAfterSection,
    BytesValue,
    Fact,
    FactsSection,
    MetricCard,
    MetricCardsSection,
    NoticeSection,
    NumberValue,
    PresentationAction,
    PresentationSection,
    TextValue,
)
from marquee.core.jobs.presenters.base import JobPresenter, PresenterContext

LETTERBOX_JOB_TYPES = (
    "letterbox_detect",
    "letterbox_detect_episode",
    "letterbox_detect_tv_scope",
    "letterbox_preview",
    "letterbox_apply",
    "letterbox_remove",
    "letterbox_apply_tv_scope",
    "letterbox_revert_tv_scope",
    "letterbox_reencode",
    "letterbox_reencode_publish",
    "letterbox_reencode_restore",
    "letterbox_reencode_discard",
    "letterbox_heal",
)

_HEADLINES = {
    "letterbox_detect": "Detect letterbox bars",
    "letterbox_detect_episode": "Detect letterbox bars in this episode",
    "letterbox_detect_tv_scope": "Detect letterbox bars across this show",
    "letterbox_preview": "Render a letterbox preview",
    "letterbox_apply": "Apply the letterbox crop tag",
    "letterbox_remove": "Remove the letterbox crop tag",
    "letterbox_apply_tv_scope": "Apply letterbox crop tags across this show",
    "letterbox_revert_tv_scope": "Revert letterbox changes across this show",
    "letterbox_reencode": "Re-encode to remove letterbox bars",
    "letterbox_reencode_publish": "Publish the letterbox re-encode candidate",
    "letterbox_reencode_restore": "Restore the pre-publication media file",
    "letterbox_reencode_discard": "Discard the letterbox re-encode candidate",
    "letterbox_heal": "Heal letterbox state for this file",
}


def _crop_text(top: int | None, bottom: int | None) -> str | None:
    parts = []
    if isinstance(top, int) and top >= 0:
        parts.append(f"{top} px from the top")
    if isinstance(bottom, int) and bottom >= 0:
        parts.append(f"{bottom} px from the bottom")
    return " and ".join(parts) if parts else None


class LetterboxPresenter(JobPresenter):
    def action(self, ctx: PresenterContext) -> PresentationAction:
        headline = _HEADLINES[self.job_type]
        explanation = None
        request = ctx.request
        thorough = getattr(request, "thorough", None)
        if self.job_type.startswith("letterbox_detect") and thorough is not None:
            mode = "thorough" if thorough else "fast"
            headline = f"{headline} using {mode} analysis"
        if self.job_type == "letterbox_apply":
            crop = _crop_text(
                getattr(request, "crop_top", None), getattr(request, "crop_bottom", None)
            )
            if crop:
                headline = f"Apply the letterbox crop tag: {crop}"
        if self.job_type == "letterbox_reencode":
            crop = _crop_text(
                ctx.summary_value("crop_top", int), ctx.summary_value("crop_bottom", int)
            )
            if crop:
                headline = f"Re-encode to crop {crop}"
            explanation = (
                "Permanently removes the black bars by re-encoding the video stream."
            )
        return PresentationAction(headline=headline, explanation=explanation)

    def sections(self, ctx: PresenterContext) -> tuple[PresentationSection, ...]:
        sections: list[PresentationSection] = []
        facts: list[Fact] = []

        scope = ctx.summary_value("scope", str)
        if scope:
            facts.append(Fact(label="Scope", value=TextValue(text=scope)))
        samples = ctx.summary_value("samples", int)
        if isinstance(samples, int) and samples >= 0:
            facts.append(Fact(label="Samples analyzed", value=NumberValue(value=samples)))
        source_w = ctx.summary_value("source_width", int)
        source_h = ctx.summary_value("source_height", int)
        if isinstance(source_w, int) and isinstance(source_h, int):
            facts.append(
                Fact(label="Source dimensions", value=TextValue(text=f"{source_w}×{source_h}"))
            )
        aspect = ctx.summary_value("aspect_ratio", str)
        if aspect:
            facts.append(Fact(label="Aspect ratio", value=TextValue(text=aspect)))
        crop = _crop_text(
            ctx.summary_value("crop_top", int), ctx.summary_value("crop_bottom", int)
        )
        if isinstance(ctx.result, LetterboxMutationResultV1) and ctx.result.actual_probe is not None:
            crop = (
                _crop_text(
                    ctx.result.actual_probe.crop_top,
                    ctx.result.actual_probe.crop_bottom,
                )
                if ctx.result.actual_probe.crop_present
                else "Confirmed absent"
            )
        if crop:
            label = (
                "Verified crop metadata"
                if isinstance(ctx.result, LetterboxMutationResultV1)
                else "Detected crop"
            )
            facts.append(Fact(label=label, value=TextValue(text=crop)))
        operation = ctx.summary_value("operation", str)
        if operation:
            facts.append(Fact(label="Requested operation", value=TextValue(text=operation)))
        output_w = ctx.summary_value("output_width", int)
        output_h = ctx.summary_value("output_height", int)
        if isinstance(output_w, int) and isinstance(output_h, int):
            facts.append(
                Fact(label="Output dimensions", value=TextValue(text=f"{output_w}×{output_h}"))
            )
        encoder = ctx.summary_value("encoder", str)
        if encoder:
            facts.append(Fact(label="Encoder", value=TextValue(text=encoder)))
        fallback = ctx.summary_value("fallback_reason", str)
        if fallback:
            facts.append(
                Fact(label="Hardware fallback", value=BadgeValue(text=fallback, tone="warning"))
            )
        validated = ctx.summary_value("validated", bool)
        if validated is not None:
            facts.append(
                Fact(
                    label="Validation",
                    value=BadgeValue(
                        text="OK" if validated else "Not confirmed",
                        tone="positive" if validated else "warning",
                    ),
                )
            )
        preserved = ctx.summary_value("hdr_preserved", bool)
        if preserved is not None:
            facts.append(
                Fact(
                    label="HDR preservation",
                    value=BadgeValue(
                        text="OK" if preserved else "Not confirmed",
                        tone="positive" if preserved else "warning",
                    ),
                )
            )
        if facts:
            sections.append(FactsSection(title="Letterbox", facts=tuple(facts)))

        cards: list[MetricCard] = []
        confidence = ctx.summary_value("confidence", float | int)
        if isinstance(confidence, int | float) and 0 <= float(confidence) <= 1:
            cards.append(
                MetricCard(
                    label="Confidence",
                    value=NumberValue(value=round(100 * float(confidence), 1), unit="%"),
                )
            )
        variability = ctx.summary_value("variability", float | int)
        if isinstance(variability, int | float) and float(variability) >= 0:
            cards.append(
                MetricCard(label="Sample variability", value=NumberValue(value=float(variability)))
            )
        if cards:
            sections.append(MetricCardsSection(title="Detection", cards=tuple(cards)))

        size_before = ctx.summary_value("input_bytes", int)
        size_after = ctx.summary_value("output_bytes", int)
        if isinstance(size_before, int) or isinstance(size_after, int):
            sections.append(
                BeforeAfterSection(
                    title="File size",
                    rows=(
                        BeforeAfterRow(
                            label="Size",
                            before=(
                                BytesValue(bytes=size_before)
                                if isinstance(size_before, int) and size_before >= 0
                                else None
                            ),
                            after=(
                                BytesValue(bytes=size_after)
                                if isinstance(size_after, int) and size_after >= 0
                                else None
                            ),
                            changed=size_before != size_after,
                        ),
                    ),
                )
            )

        no_bars = ctx.summary_value("no_bars", bool)
        if no_bars:
            sections.append(
                NoticeSection(
                    tone="success",
                    message="No letterbox bars were detected, so nothing needed to change.",
                )
            )
        return tuple(sections)
