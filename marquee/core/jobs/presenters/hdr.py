"""HDR / Dolby Vision presenter family.

Renders source/target Dolby Vision and HDR state, profile/level, codec, bit
depth, color metadata, RPU handling, encoder/hardware path and fallback,
input/output size, speed, validation/preservation results, backup and atomic
publish outcomes.  Failures are attributed to the analysis, conversion,
preservation, validation, backup, or publish stage through the safe error
document's `stage` diagnostic.
"""

from __future__ import annotations

from marquee.core.jobs.presentation import (
    BadgeValue,
    BeforeAfterRow,
    BeforeAfterSection,
    BytesValue,
    Fact,
    FactsSection,
    MetricCard,
    MetricCardsSection,
    NumberValue,
    PresentationAction,
    PresentationSection,
    TextValue,
)
from marquee.core.jobs.presenters.base import JobPresenter, PresenterContext

HDR_JOB_TYPES = (
    "dovi_analyze",
    "dovi_convert",
    "dovi_publish",
    "dovi_restore",
    "dovi_discard",
)


class HdrPresenter(JobPresenter):
    def action(self, ctx: PresenterContext) -> PresentationAction:
        if self.job_type == "dovi_analyze":
            return PresentationAction(
                headline="Analyze Dolby Vision and HDR metadata",
                explanation=(
                    "Probes the video stream for Dolby Vision profile, HDR format, "
                    "bit depth, and color metadata."
                ),
            )
        if self.job_type == "dovi_publish":
            return PresentationAction(
                headline="Publish the Dolby Vision candidate",
                explanation="Atomically deploys a verified candidate after saving the original.",
            )
        if self.job_type == "dovi_restore":
            return PresentationAction(
                headline="Restore the pre-conversion media file",
                explanation="Restores and rescans the canonical pre-publication backup.",
            )
        if self.job_type == "dovi_discard":
            return PresentationAction(
                headline="Discard the Dolby Vision candidate",
                explanation="Deletes only the unreferenced candidate from confined storage.",
            )
        kind = ctx.summary_value("kind", str)
        if kind == "p5_to_p81":
            source, target = "P5", "P8.1"
        elif kind == "p7_strip_el":
            source, target = "P7", "P8.1"
        else:
            source_value = ctx.summary_value("source_profile", (str, int))
            target_value = ctx.summary_value("target_profile", (str, int))
            source = f"P{source_value}" if isinstance(source_value, int) else source_value
            target = f"P{target_value}" if isinstance(target_value, int) else target_value
        if source and target:
            headline = f"Convert Dolby Vision {source} to {target}"
        else:
            headline = "Convert Dolby Vision profile"
        return PresentationAction(
            headline=headline,
            explanation=(
                "Re-encodes or remuxes the file so its Dolby Vision layer plays on "
                "more devices, preserving HDR metadata."
            ),
        )

    def sections(self, ctx: PresenterContext) -> tuple[PresentationSection, ...]:
        sections: list[PresentationSection] = []
        facts: list[Fact] = []

        for key, label in (
            ("source_profile", "Source Dolby Vision profile"),
            ("source_level", "Source level"),
            ("target_profile", "Target profile"),
            ("hdr_format", "HDR format"),
            ("codec", "Video codec"),
            ("color_primaries", "Color primaries"),
            ("color_transfer", "Color transfer"),
            ("rpu_action", "RPU handling"),
            ("encoder", "Encoder"),
            ("hardware_path", "Hardware path"),
        ):
            value = ctx.summary_value(key, str)
            if value:
                facts.append(Fact(label=label, value=TextValue(text=value)))
        bit_depth = ctx.summary_value("bit_depth", int)
        if isinstance(bit_depth, int) and bit_depth > 0:
            facts.append(Fact(label="Bit depth", value=NumberValue(value=bit_depth, unit="bit")))
        fallback = ctx.summary_value("fallback_reason", str)
        if fallback:
            facts.append(
                Fact(
                    label="Hardware fallback",
                    value=BadgeValue(text=fallback, tone="warning"),
                )
            )
        for key, label in (
            ("validated", "Output validation"),
            ("preserved", "HDR preservation"),
            ("backup_created", "Backup"),
            ("published", "Atomic publish"),
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
            sections.append(FactsSection(title="Video", facts=tuple(facts)))

        before_state = ctx.summary_value("state_before", str)
        after_state = ctx.summary_value("state_after", str)
        if before_state or after_state:
            sections.append(
                BeforeAfterSection(
                    title="Dolby Vision state",
                    rows=(
                        BeforeAfterRow(
                            label="State",
                            before=TextValue(text=before_state) if before_state else None,
                            after=TextValue(text=after_state) if after_state else None,
                            changed=before_state != after_state,
                        ),
                    ),
                )
            )

        cards: list[MetricCard] = []
        input_bytes = ctx.summary_value("input_bytes", int)
        if isinstance(input_bytes, int) and input_bytes >= 0:
            cards.append(MetricCard(label="Input size", value=BytesValue(bytes=input_bytes)))
        output_bytes = ctx.summary_value("output_bytes", int)
        if isinstance(output_bytes, int) and output_bytes >= 0:
            cards.append(MetricCard(label="Output size", value=BytesValue(bytes=output_bytes)))
        speed = ctx.summary_value("speed", float | int)
        if isinstance(speed, int | float) and float(speed) >= 0:
            cards.append(
                MetricCard(
                    label="Speed",
                    value=NumberValue(value=float(speed), unit="x realtime"),
                )
            )
        if cards:
            sections.append(MetricCardsSection(title="Processing", cards=tuple(cards)))
        return tuple(sections)
