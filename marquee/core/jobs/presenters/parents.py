"""Parent batch presenter family.

Parents present the sealed scope, aggregate terminal counts, and a link to the
server-paginated child list — never an unbounded embedded child graph.  Live
child counts may enrich the presentation; a terminal parent renders the same
summary from its stored result evidence.
"""

from __future__ import annotations

from typing import Any

from marquee.core.jobs.presentation import (
    ChildrenSection,
    LinkValue,
    PresentationAction,
    PresentationSection,
)
from marquee.core.jobs.presenters.base import JobPresenter, PresenterContext

PARENT_JOB_TYPES = (
    "subtitle_generate_batch",
    "subtitle_policy_batch",
    "dovi_analyze_batch",
    "letterbox_detect_tv_batch",
    "letterbox_detect_batch",
    "letterbox_apply_batch",
    "letterbox_reencode_tv_batch",
    "letterbox_reencode_publish_batch",
)

_HEADLINES = {
    "subtitle_generate_batch": "Generate subtitles across the selection",
    "subtitle_policy_batch": "Apply the subtitle policy across the selection",
    "dovi_analyze_batch": "Analyze Dolby Vision across the selection",
    "letterbox_detect_tv_batch": "Detect TV letterbox bars across the selection",
    "letterbox_detect_batch": "Detect letterbox bars across the selection",
    "letterbox_apply_batch": "Apply letterbox crops across the selection",
    "letterbox_reencode_tv_batch": "Re-encode TV letterbox bars across the selection",
    "letterbox_reencode_publish_batch": "Publish letterbox candidates across the selection",
}

_CHILD_NOUNS = {
    "subtitle_generate_batch": "files",
    "subtitle_policy_batch": "files",
    "dovi_analyze_batch": "movies",
    "letterbox_detect_tv_batch": "episodes",
    "letterbox_detect_batch": "movies",
    "letterbox_apply_batch": "movies",
    "letterbox_reencode_tv_batch": "episodes",
    "letterbox_reencode_publish_batch": "files",
}

_COUNT_KEYS = ("total", "queued", "running", "succeeded", "no_change", "failed", "cancelled")


class ParentBatchPresenter(JobPresenter):
    def action(self, ctx: PresenterContext) -> PresentationAction:
        headline = _HEADLINES[self.job_type]
        subject = ctx.subject
        child_count = getattr(subject, "child_count", None)
        if isinstance(child_count, int) and child_count >= 0:
            noun = _CHILD_NOUNS[self.job_type]
            headline = f"{headline.split(' across ')[0]} across {child_count} {noun}"
        return PresentationAction(
            headline=headline,
            explanation="Runs one child job per target and aggregates their outcomes.",
        )

    def _counts(self, ctx: PresenterContext) -> dict[str, int] | None:
        """Prefer live child counts; fall back to stored terminal evidence."""
        raw: Any = ctx.live.get("children")
        source = "live"
        if not isinstance(raw, dict):
            raw = ctx.summary.get("children")
            source = "stored"
        if raw is None:
            return None
        if not isinstance(raw, dict):
            ctx.warn("malformed_evidence", "Stored child summary has the wrong shape.")
            return None
        counts: dict[str, int] = {}
        for key in _COUNT_KEYS:
            value = raw.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                if key == "total":
                    ctx.warn(
                        "malformed_evidence",
                        f"The {source} child summary could not be validated.",
                    )
                    return None
                value = 0
            counts[key] = value
        sealed = raw.get("sealed")
        counts["sealed"] = 1 if (sealed is True or sealed is None) else 0
        return counts

    def sections(self, ctx: PresenterContext) -> tuple[PresentationSection, ...]:
        counts = self._counts(ctx)
        if counts is None:
            return ()
        return (
            ChildrenSection(
                total=counts["total"],
                queued=counts["queued"],
                running=counts["running"],
                succeeded=counts["succeeded"],
                no_change=counts["no_change"],
                failed=counts["failed"],
                cancelled=counts["cancelled"],
                sealed=bool(counts["sealed"]),
                children_link=LinkValue(
                    href=f"/api/jobs/{ctx.job.id}/children",
                    label="View all children",
                ),
            ),
        )


def build_parent_presenters() -> dict[str, JobPresenter]:
    return {
        f"jobs.{job_type}": ParentBatchPresenter(job_type) for job_type in PARENT_JOB_TYPES
    }
