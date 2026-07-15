"""Supporting presenter family: integrations, ML/taste, maintenance, system.

Supporting work uses typed presenters exactly like the four primary feature
areas — "supporting" never authorizes a raw JSON fallback.
"""

from __future__ import annotations

from marquee.core.jobs.presentation import (
    BadgeValue,
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

SUPPORTING_JOB_TYPES = (
    "library_sync",
    "radarr_upgrade",
    "taste_rebuild",
    "taste_map",
    "learned_head_train",
    "backup_create",
    "pipeline_cache_clear",
    "job_retention_purge",
    "system_metrics_purge",
    "system_noop",
)

_HEADLINES = {
    "library_sync": "Synchronize Radarr and Sonarr libraries",
    "radarr_upgrade": "Upgrade this movie in Radarr",
    "taste_rebuild": "Rebuild the taste profile",
    "taste_map": "Generate the taste map",
    "learned_head_train": "Train the learned ranking model",
    "backup_create": "Create a backup",
    "pipeline_cache_clear": "Clear pipeline caches",
    "job_retention_purge": "Purge expired job history",
    "system_metrics_purge": "Purge old system metrics",
    "system_noop": "Run a system health check",
}

_EXPLANATIONS = {
    "library_sync": "Fetches libraries from the configured services and updates Marquee's copy.",
    "taste_rebuild": "Re-embeds the training exemplars and rebuilds the k-NN taste profile.",
    "taste_map": "Projects the library and exemplars into a browsable 2-D taste map.",
    "learned_head_train": "Trains the learned ranking head from recorded feedback.",
    "backup_create": "Writes a checksummed backup of the selected data.",
    "pipeline_cache_clear": "Removes cached embeddings and staged pipeline files.",
    "job_retention_purge": "Deletes job history past the configured retention window.",
    "system_metrics_purge": "Deletes system metric samples past the retention window.",
    "system_noop": "Verifies that queueing, delivery, and completion work end to end.",
}

_SYNC_COUNT_FACTS = (
    ("created", "Created"),
    ("updated", "Updated"),
    ("retired", "Retired"),
    ("unchanged", "Unchanged"),
    ("skipped", "Skipped"),
    ("errors", "Errors"),
)

_ML_FACTS = (
    ("family", "Family"),
    ("version", "Version"),
    ("checksum", "Checksum"),
    ("namespace", "Library"),
    ("model_name", "Model"),
    ("profile_version", "Profile version"),
    ("head_version", "Head version"),
    ("device", "Device"),
    ("artifact", "New artifact"),
    ("previous_artifact", "Previous artifact"),
)

_MAINTENANCE_METRICS = (
    ("records_removed", "Records removed", None),
    ("files_removed", "Files removed", None),
    ("bytes_freed", "Space freed", "bytes"),
    ("planned_count", "Planned", None),
    ("processed_count", "Processed", None),
    ("deleted_count", "Deleted", None),
)


class SupportingPresenter(JobPresenter):
    def action(self, ctx: PresenterContext) -> PresentationAction:
        return PresentationAction(
            headline=_HEADLINES[self.job_type],
            explanation=_EXPLANATIONS.get(self.job_type),
        )

    def sections(self, ctx: PresenterContext) -> tuple[PresentationSection, ...]:
        sections: list[PresentationSection] = []
        facts: list[Fact] = []

        if self.job_type in {"library_sync", "radarr_upgrade"}:
            services = ctx.summary_value("services", str)
            if services:
                facts.append(Fact(label="Services", value=TextValue(text=services)))
            for key, label in _SYNC_COUNT_FACTS:
                value = ctx.summary_value(key, int)
                if isinstance(value, int) and value >= 0:
                    facts.append(Fact(label=label, value=NumberValue(value=value)))

        if self.job_type in {"taste_rebuild", "taste_map", "learned_head_train"}:
            for key, label in _ML_FACTS:
                value = ctx.summary_value(key, str)
                if value:
                    facts.append(Fact(label=label, value=TextValue(text=value)))
            for key, label in (
                ("active_generation", "Generation"),
                ("exemplar_count", "Exemplars"),
                ("negative_count", "Negative exemplars"),
                ("label_count", "Feedback labels"),
                ("epochs", "Epochs"),
            ):
                value = ctx.summary_value(key, int)
                if isinstance(value, int) and value >= 0:
                    facts.append(Fact(label=label, value=NumberValue(value=value)))
            metrics = ctx.summary_value("metrics", dict)
            if metrics:
                input_count = metrics.get("input_count")
                if isinstance(input_count, int) and not isinstance(input_count, bool):
                    facts.append(Fact(label="Inputs", value=NumberValue(value=input_count)))

        if self.definition_is_maintenance():
            subject = ctx.subject
            if subject.kind == "maintenance_scope":
                dry_run = ctx.summary_value("dry_run", bool)
                if dry_run is None:
                    dry_run = subject.dry_run
                facts.append(Fact(label="Scope", value=TextValue(text=subject.scope)))
                facts.append(
                    Fact(
                        label="Mode",
                        value=BadgeValue(
                            text="Dry run" if dry_run else "Applied",
                            tone="neutral" if dry_run else "positive",
                        ),
                    )
                )
            backup_id = ctx.summary_value("backup_id", str)
            if backup_id:
                facts.append(Fact(label="Backup", value=TextValue(text=backup_id)))
            retention_days = ctx.summary_value("retention_days", int)
            if isinstance(retention_days, int) and retention_days >= 0:
                facts.append(
                    Fact(
                        label="Retention window",
                        value=NumberValue(value=retention_days, unit="days"),
                    )
                )

        if facts:
            sections.append(FactsSection(title="Details", facts=tuple(facts)))

        cards: list[MetricCard] = []
        if self.definition_is_maintenance():
            for key, label, unit in _MAINTENANCE_METRICS:
                value = ctx.summary_value(key, int)
                if isinstance(value, int) and value >= 0:
                    cards.append(
                        MetricCard(
                            label=label,
                            value=(
                                BytesValue(bytes=value)
                                if unit == "bytes"
                                else NumberValue(value=value)
                            ),
                        )
                    )
        if cards:
            sections.append(MetricCardsSection(title="Effect", cards=tuple(cards)))
        return tuple(sections)

    def definition_is_maintenance(self) -> bool:
        return self.job_type in {
            "backup_create",
            "pipeline_cache_clear",
            "job_retention_purge",
            "system_metrics_purge",
        }


def build_supporting_presenters() -> dict[str, JobPresenter]:
    return {
        f"jobs.{job_type}": SupportingPresenter(job_type)
        for job_type in SUPPORTING_JOB_TYPES
    }
