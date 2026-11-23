"""Canonical immutable taste exemplars and poster preference evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from marquee.database import Base


class PosterPreferenceEvent(Base):
    """Append-only, bounded evidence from one explicit poster interaction."""

    __tablename__ = "poster_preference_events"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_preference_events_version"),
        CheckConstraint(
            "namespace IN ('global', 'movies', 'tv')", name="ck_preference_events_namespace"
        ),
        CheckConstraint(
            "action IN ('selection', 'approval', 'override', 'rank', 'hate', 'undo', 'revoke')",
            name="ck_preference_events_action",
        ),
        CheckConstraint(
            "confidence IN ('explicit', 'strong', 'medium', 'weak')",
            name="ck_preference_events_confidence",
        ),
        Index("ix_preference_events_namespace_created", "namespace", "created_at"),
        Index("ix_preference_events_subject", "subject_kind", "subject_reference"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    namespace: Mapped[str] = mapped_column(String(12), nullable=False)
    subject_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    subject_reference: Mapped[str] = mapped_column(String(96), nullable=False)
    subject_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    pipeline_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("pipeline_runs.run_id", ondelete="RESTRICT"), nullable=True
    )
    candidate_artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_artifacts.id", ondelete="RESTRICT"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    exposed_candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    presentation_order: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    training_context: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    confidence: Mapped[str] = mapped_column(String(12), nullable=False)
    initiator: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    supersedes_event_id: Mapped[str | None] = mapped_column(
        ForeignKey("poster_preference_events.id", ondelete="RESTRICT"), nullable=True
    )
    revoked_event_id: Mapped[str | None] = mapped_column(
        ForeignKey("poster_preference_events.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TasteExemplar(Base):
    """Immutable preference evidence projected into an explicit lifecycle."""

    __tablename__ = "taste_exemplars"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_taste_exemplars_version"),
        CheckConstraint(
            "namespace IN ('global', 'movies', 'tv')", name="ck_taste_exemplars_namespace"
        ),
        CheckConstraint("polarity IN ('positive', 'negative')", name="ck_taste_exemplars_polarity"),
        CheckConstraint(
            "status IN ('pending_deploy', 'active', 'revoked', 'invalid')",
            name="ck_taste_exemplars_status",
        ),
        CheckConstraint(
            "evidence_weight > 0 AND evidence_weight <= 1",
            name="ck_taste_exemplars_weight",
        ),
        CheckConstraint(
            "checksum IS NULL OR length(checksum) = 64", name="ck_taste_exemplars_checksum"
        ),
        CheckConstraint(
            "status <> 'active' OR retained_artifact_id IS NOT NULL",
            name="ck_taste_exemplars_active_asset",
        ),
        Index("ix_taste_exemplars_namespace_status", "namespace", "status"),
        Index("ix_taste_exemplars_subject", "subject_kind", "subject_reference"),
        Index(
            "uq_taste_exemplars_active_positive_subject",
            "namespace",
            "subject_kind",
            "subject_reference",
            unique=True,
            postgresql_where=text("status = 'active' AND polarity = 'positive'"),
            sqlite_where=text("status = 'active' AND polarity = 'positive'"),
        ),
        Index(
            "uq_taste_exemplars_active_positive_content",
            "checksum",
            unique=True,
            postgresql_where=text(
                "status = 'active' AND polarity = 'positive' AND checksum IS NOT NULL"
            ),
            sqlite_where=text(
                "status = 'active' AND polarity = 'positive' AND checksum IS NOT NULL"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    namespace: Mapped[str] = mapped_column(String(12), nullable=False)
    polarity: Mapped[str] = mapped_column(String(8), nullable=False)
    evidence_weight: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_source: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    subject_reference: Mapped[str] = mapped_column(String(96), nullable=False)
    subject_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    pipeline_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("pipeline_runs.run_id", ondelete="RESTRICT"), nullable=True
    )
    candidate_artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_artifacts.id", ondelete="RESTRICT"), nullable=True
    )
    retained_artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_artifacts.id", ondelete="RESTRICT"), nullable=True
    )
    preference_event_id: Mapped[str] = mapped_column(
        ForeignKey("poster_preference_events.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    deployment_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=True
    )
    deployment_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    initiator: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    asset_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    perceptual_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_identity: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    revision_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    supersedes_exemplar_id: Mapped[str | None] = mapped_column(
        ForeignKey("taste_exemplars.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OnboardingAnalysisSuccessor(Base):
    """Canonical analysis lineage for one recoverable onboarding subject."""

    __tablename__ = "onboarding_analysis_successors"
    __table_args__ = (
        CheckConstraint(
            "subject_kind IN ('movie', 'series', 'season')",
            name="ck_onboarding_analysis_successors_subject_kind",
        ),
        CheckConstraint(
            "predecessor_job_id IS NULL OR predecessor_job_id <> job_id",
            name="ck_onboarding_analysis_successors_not_self",
        ),
        Index(
            "ix_onboarding_analysis_successors_subject_created",
            "subject_kind",
            "subject_reference",
            "created_at",
        ),
    )

    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), primary_key=True
    )
    subject_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    subject_reference: Mapped[str] = mapped_column(String(96), nullable=False)
    predecessor_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TasteDeploymentSuccessor(Base):
    """Append-only canonical deployment successors owned by one positive exemplar."""

    __tablename__ = "taste_deployment_successors"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_taste_deployment_successors_job"),
        UniqueConstraint("exemplar_id", "ordinal", name="uq_taste_deployment_successors_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_taste_deployment_successors_ordinal"),
        CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'no_change', 'failed', "
            "'cancelled', 'superseded', 'unsafe', 'dead_letter')",
            name="ck_taste_deployment_successors_state",
        ),
        CheckConstraint(
            "predecessor_job_id IS NULL OR predecessor_job_id <> job_id",
            name="ck_taste_deployment_successors_not_self",
        ),
        Index("ix_taste_deployment_successors_exemplar", "exemplar_id", "ordinal"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    exemplar_id: Mapped[str] = mapped_column(
        ForeignKey("taste_exemplars.id", ondelete="RESTRICT"), nullable=False
    )
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False)
    predecessor_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    post_effect_validation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TasteProfileRevision(Base):
    """Frozen exemplar revision and its profile publication/reload outcome."""

    __tablename__ = "taste_profile_revisions"
    __table_args__ = (
        CheckConstraint(
            "state IN ('eligible', 'building', 'published', 'personalized', 'failed', 'superseded')",
            name="ck_taste_profile_revisions_state",
        ),
        CheckConstraint("positive_subjects >= 0", name="ck_taste_profile_revisions_subjects"),
    )

    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    exemplar_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    exemplar_checksums: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    exemplar_manifest: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    positive_subjects: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    build_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=True
    )
    movie_generation: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tv_generation: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    movie_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tv_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    consumer_reload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    failure: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MlConsumerAcknowledgement(Base):
    """Durable evidence that a real runtime consumer loaded one active artifact."""

    __tablename__ = "ml_consumer_acknowledgements"
    __table_args__ = (
        CheckConstraint("generation >= 1", name="ck_ml_consumer_ack_generation"),
        CheckConstraint("length(checksum) = 64", name="ck_ml_consumer_ack_checksum"),
        UniqueConstraint(
            "family",
            "consumer_role",
            "instance_id",
            "generation",
            "checksum",
            name="uq_ml_consumer_ack_identity",
        ),
        Index("ix_ml_consumer_ack_family_generation", "family", "generation"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    family: Mapped[str] = mapped_column(String(64), nullable=False)
    consumer_role: Mapped[str] = mapped_column(String(64), nullable=False)
    instance_id: Mapped[str] = mapped_column(String(128), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_id: Mapped[int] = mapped_column(
        ForeignKey("job_artifacts.id", ondelete="RESTRICT"), nullable=False
    )
    load_result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    loaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TasteProfileBuild(Base):
    """One immutable movie or TV profile-build lineage owned by its coordinator."""

    __tablename__ = "taste_profile_builds"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_taste_profile_builds_job"),
        CheckConstraint("library IN ('movies', 'tv')", name="ck_taste_profile_builds_library"),
        CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'no_change', 'superseded', 'failed', 'cancelled')",
            name="ck_taste_profile_builds_state",
        ),
        CheckConstraint(
            "expected_generation >= 0", name="ck_taste_profile_builds_expected_generation"
        ),
        CheckConstraint(
            "result_generation IS NULL OR result_generation >= 0",
            name="ck_taste_profile_builds_result_generation",
        ),
        CheckConstraint(
            "result_checksum IS NULL OR length(result_checksum) = 64",
            name="ck_taste_profile_builds_result_checksum",
        ),
        CheckConstraint(
            "consumer_reload_checksum IS NULL OR length(consumer_reload_checksum) = 64",
            name="ck_taste_profile_builds_reload_checksum",
        ),
        Index("ix_taste_profile_builds_library_created", "library", "created_at"),
        Index("ix_taste_profile_builds_revision", "revision_digest"),
        Index(
            "uq_taste_profile_builds_inflight_library",
            "library",
            unique=True,
            postgresql_where=text("state IN ('queued', 'running')"),
            sqlite_where=text("state IN ('queued', 'running')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    revision_digest: Mapped[str] = mapped_column(
        ForeignKey("taste_profile_revisions.digest", ondelete="RESTRICT"), nullable=False
    )
    library: Mapped[str] = mapped_column(String(8), nullable=False)
    expected_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    result_generation: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    result_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    consumer_reload_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    supersedes_build_id: Mapped[str | None] = mapped_column(
        ForeignKey("taste_profile_builds.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TasteProfileCoordinator(Base):
    """The durable desired-revision and in-flight cursor for one native library."""

    __tablename__ = "taste_profile_coordinators"
    __table_args__ = (
        CheckConstraint(
            "library IN ('movies', 'tv')", name="ck_taste_profile_coordinators_library"
        ),
        CheckConstraint(
            "desired_generation IS NULL OR desired_generation >= 0",
            name="ck_taste_profile_coordinators_desired_generation",
        ),
    )

    library: Mapped[str] = mapped_column(String(8), primary_key=True)
    desired_revision_digest: Mapped[str | None] = mapped_column(
        ForeignKey("taste_profile_revisions.digest", ondelete="RESTRICT"), nullable=True
    )
    desired_generation: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    inflight_build_id: Mapped[str | None] = mapped_column(
        ForeignKey("taste_profile_builds.id", ondelete="RESTRICT"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
