"""AI poster presenter family.

Renders candidate/source counts, pipeline stage, rejection gates, selected
candidate preview, score/confidence with interpretation, model/profile
versions, previous-versus-selected poster, deployment/reset results, and
review-required reasons from stored result-summary evidence.  Batch completion
is presented separately from the current movie/source/scoring work through the
progress contract's overall/current scopes.
"""

from __future__ import annotations

from marquee.core.jobs.presentation import (
    BadgeValue,
    BeforeAfterRow,
    BeforeAfterSection,
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

POSTER_JOB_TYPES = (
    "poster_pipeline",
    "poster_pipeline_batch",
    "poster_pipeline_tv_batch",
    "poster_deploy",
    "poster_restore",
    "poster_reset",
    "poster_backup_subject",
    "poster_heal",
    "poster_deploy_reset",
    "poster_rescan",
    "poster_backup_all",
    "poster_maintenance",
)

_HEADLINES = {
    "poster_pipeline": "Select the best poster",
    "poster_pipeline_batch": "Select posters across the movie library",
    "poster_pipeline_tv_batch": "Select posters across the TV library",
    "poster_deploy": "Deploy selected poster artwork",
    "poster_restore": "Restore recorded poster artwork",
    "poster_reset": "Reset deployed poster artwork",
    "poster_backup_subject": "Back up deployed poster artwork",
    "poster_heal": "Heal missing or broken posters",
    "poster_deploy_reset": "Reset deployed posters",
    "poster_rescan": "Rescan poster candidates",
    "poster_backup_all": "Back up all deployed posters",
    "poster_maintenance": "Clean up poster caches and archives",
}

_EXPLANATIONS = {
    "poster_pipeline": "Fetches candidates, filters junk, and ranks the survivors with the taste profile.",
    "poster_pipeline_batch": "Runs the poster pipeline for each selected movie.",
    "poster_pipeline_tv_batch": "Runs the poster pipeline for each selected show and season.",
    "poster_deploy": "Validates, backs up, and atomically publishes selected artwork.",
    "poster_restore": "Restores validated bytes from the recorded backup or cache chain.",
    "poster_reset": "Backs up and atomically removes one deployed poster.",
    "poster_backup_subject": "Copies one deployed poster into recoverable storage.",
    "poster_heal": "Re-deploys artwork where the expected poster file is missing.",
    "poster_deploy_reset": "Restores the previously deployed artwork state.",
    "poster_rescan": "Refreshes stored candidate metadata without re-ranking.",
    "poster_backup_all": "Copies every deployed poster into the backup area.",
    "poster_maintenance": "Removes stale cache entries and orphaned archives.",
}


def _score_interpretation(score: float) -> str:
    if score >= 0.8:
        return "Strong match for the taste profile"
    if score >= 0.5:
        return "Reasonable match for the taste profile"
    return "Weak match for the taste profile"


class PosterPresenter(JobPresenter):
    def action(self, ctx: PresenterContext) -> PresentationAction:
        headline = _HEADLINES[self.job_type]
        candidate_count = ctx.summary_value("candidate_count", int)
        if self.job_type == "poster_pipeline" and isinstance(candidate_count, int):
            headline = f"Select a poster from {candidate_count} candidates"
        return PresentationAction(headline=headline, explanation=_EXPLANATIONS.get(self.job_type))

    def sections(self, ctx: PresenterContext) -> tuple[PresentationSection, ...]:
        sections: list[PresentationSection] = []
        facts: list[Fact] = []

        candidate_count = ctx.summary_value("candidate_count", int)
        if isinstance(candidate_count, int) and candidate_count >= 0:
            facts.append(Fact(label="Candidates", value=NumberValue(value=candidate_count)))
        source_count = ctx.summary_value("source_count", int)
        if isinstance(source_count, int) and source_count >= 0:
            facts.append(Fact(label="Sources", value=NumberValue(value=source_count)))
        rejected = ctx.summary_value("rejected_count", int)
        if isinstance(rejected, int) and rejected >= 0:
            facts.append(Fact(label="Rejected by gates", value=NumberValue(value=rejected)))
        ranked = ctx.summary_value("ranked_count", int)
        if isinstance(ranked, int) and ranked >= 0:
            facts.append(Fact(label="Ranked", value=NumberValue(value=ranked)))
        selected_source = ctx.summary_value("selected_source", str)
        if selected_source:
            facts.append(Fact(label="Selected source", value=TextValue(text=selected_source)))
        model_version = ctx.summary_value("model_version", str)
        if model_version:
            facts.append(Fact(label="Model", value=TextValue(text=model_version)))
        profile_version = ctx.summary_value("profile_version", str)
        if profile_version:
            facts.append(Fact(label="Taste profile", value=TextValue(text=profile_version)))
        deployed = ctx.summary_value("deployed", bool)
        if deployed is not None:
            facts.append(
                Fact(
                    label="Deployed",
                    value=BadgeValue(
                        text="Deployed" if deployed else "Not deployed",
                        tone="positive" if deployed else "neutral",
                    ),
                )
            )
        if facts:
            sections.append(FactsSection(title="Selection", facts=tuple(facts)))

        cards: list[MetricCard] = []
        score = ctx.summary_value("score", float | int)
        if isinstance(score, int | float):
            cards.append(
                MetricCard(
                    label="Score",
                    value=NumberValue(value=float(score)),
                    interpretation=_score_interpretation(float(score)),
                )
            )
        confidence = ctx.summary_value("confidence", float | int)
        if isinstance(confidence, int | float) and 0 <= float(confidence) <= 1:
            cards.append(
                MetricCard(
                    label="Confidence",
                    value=NumberValue(value=round(100 * float(confidence), 1), unit="%"),
                )
            )
        if cards:
            sections.append(MetricCardsSection(title="Ranking", cards=tuple(cards)))

        previous_poster = ctx.summary_value("previous_poster", str)
        selected_poster = ctx.summary_value("selected_poster", str)
        if previous_poster or selected_poster:
            sections.append(
                BeforeAfterSection(
                    title="Poster",
                    rows=(
                        BeforeAfterRow(
                            label="Deployed poster",
                            before=(TextValue(text=previous_poster) if previous_poster else None),
                            after=(TextValue(text=selected_poster) if selected_poster else None),
                            changed=previous_poster != selected_poster,
                        ),
                    ),
                )
            )

        review_required = ctx.summary_value("review_required", bool)
        if review_required:
            reason = ctx.summary_value("review_reason", str)
            sections.append(
                NoticeSection(
                    tone="warning",
                    message=reason or "This selection needs manual review before deployment.",
                )
            )
        selected = ctx.summary_value("selected", bool)
        if selected is False and ctx.job.outcome == "no_change":
            sections.append(
                NoticeSection(
                    tone="info",
                    message="Analysis succeeded but no candidate beat the current poster.",
                )
            )
        return tuple(sections)
