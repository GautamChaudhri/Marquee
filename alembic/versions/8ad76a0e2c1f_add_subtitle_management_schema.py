"""add subtitle management schema

Revision ID: 8ad76a0e2c1f
Revises: fd3dee2ea0da
Create Date: 2026-06-15 15:04:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8ad76a0e2c1f"
down_revision: str | None = "fd3dee2ea0da"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "media_files",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("source_key", sa.String(length=200), nullable=False),
        sa.Column("source_file_id", sa.Integer(), nullable=True),
        sa.Column("movie_id", sa.Integer(), nullable=True),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("container", sa.String(length=20), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_resolved_path", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["movie_id"], ["movies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_media_files_movie_id"), "media_files", ["movie_id"], unique=False)
    op.create_index(
        op.f("ix_media_files_source_key"), "media_files", ["source_key"], unique=True
    )

    op.create_table(
        "episode_media_files",
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("media_file_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_file_id"], ["media_files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("episode_id", "media_file_id"),
    )
    op.create_index(
        op.f("ix_episode_media_files_media_file_id"),
        "episode_media_files",
        ["media_file_id"],
        unique=False,
    )

    op.create_table(
        "subtitle_inventories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("media_file_id", sa.Integer(), nullable=False),
        sa.Column("file_signature", sa.String(length=128), nullable=True),
        sa.Column("container", sa.String(length=20), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("audio_streams_json", sa.JSON(), nullable=True),
        sa.Column("chapters_count", sa.Integer(), nullable=False),
        sa.Column("attachments_count", sa.Integer(), nullable=False),
        sa.Column("coverage_json", sa.JSON(), nullable=True),
        sa.Column("probe_tool_versions_json", sa.JSON(), nullable=True),
        sa.Column(
            "scanned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["media_file_id"], ["media_files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_subtitle_inventories_media_file_id"),
        "subtitle_inventories",
        ["media_file_id"],
        unique=True,
    )

    op.create_table(
        "subtitle_tracks",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("inventory_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("stream_index", sa.Integer(), nullable=True),
        sa.Column("tool_track_id", sa.Integer(), nullable=True),
        sa.Column("external_path", sa.Text(), nullable=True),
        sa.Column("paired_path", sa.Text(), nullable=True),
        sa.Column("codec", sa.String(length=40), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("language_raw", sa.String(length=40), nullable=True),
        sa.Column("language_tag", sa.String(length=40), nullable=False),
        sa.Column("language_source", sa.String(length=20), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("is_forced", sa.Boolean(), nullable=False),
        sa.Column("is_sdh", sa.Boolean(), nullable=False),
        sa.Column("is_commentary", sa.Boolean(), nullable=False),
        sa.Column("is_generated", sa.Boolean(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["inventory_id"], ["subtitle_inventories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_subtitle_tracks_inventory_id"),
        "subtitle_tracks",
        ["inventory_id"],
        unique=False,
    )

    op.create_table(
        "managed_subtitle_assets",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("cache_path", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("language_tag", sa.String(length=40), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("is_forced", sa.Boolean(), nullable=False),
        sa.Column("is_sdh", sa.Boolean(), nullable=False),
        sa.Column("is_commentary", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("provenance_json", sa.JSON(), nullable=True),
        sa.Column(
            "restore_on_replacement", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("last_restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_managed_subtitle_assets_content_sha256"),
        "managed_subtitle_assets",
        ["content_sha256"],
        unique=False,
    )

    op.create_table(
        "managed_subtitle_bindings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("asset_id", sa.String(length=32), nullable=False),
        sa.Column("owner_type", sa.String(length=10), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["managed_subtitle_assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_managed_subtitle_bindings_asset_id"),
        "managed_subtitle_bindings",
        ["asset_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_managed_subtitle_bindings_owner_id"),
        "managed_subtitle_bindings",
        ["owner_id"],
        unique=False,
    )

    op.create_table(
        "media_batches",
        sa.Column("batch_id", sa.String(length=32), nullable=False),
        sa.Column("operation", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'planned'"), nullable=False),
        sa.Column("requested_count", sa.Integer(), nullable=False),
        sa.Column("completed_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("paused", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("batch_id"),
    )

    op.create_table(
        "media_jobs",
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("batch_id", sa.String(length=32), nullable=True),
        sa.Column("media_file_id", sa.Integer(), nullable=True),
        sa.Column("operation", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'planned'"), nullable=False),
        sa.Column("stage", sa.String(length=30), nullable=True),
        sa.Column("progress_done", sa.Integer(), nullable=False),
        sa.Column("progress_total", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(length=12), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=True),
        sa.Column("plan_json", sa.JSON(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error_json", sa.JSON(), nullable=True),
        sa.Column("input_signature", sa.String(length=128), nullable=True),
        sa.Column("plan_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["media_batches.batch_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["media_file_id"], ["media_files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index(op.f("ix_media_jobs_batch_id"), "media_jobs", ["batch_id"], unique=False)
    op.create_index(op.f("ix_media_jobs_media_file_id"), "media_jobs", ["media_file_id"], unique=False)
    op.create_index(op.f("ix_media_jobs_status"), "media_jobs", ["status"], unique=False)
    op.create_index(
        op.f("ix_media_jobs_idempotency_key"),
        "media_jobs",
        ["idempotency_key"],
        unique=True,
    )

    op.create_table(
        "media_job_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=30), nullable=True),
        sa.Column("state", sa.String(length=20), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("progress_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["job_id"], ["media_jobs.job_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_media_job_events_job_id"),
        "media_job_events",
        ["job_id"],
        unique=False,
    )

    op.create_table(
        "media_backups",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=True),
        sa.Column("media_file_id", sa.Integer(), nullable=True),
        sa.Column("original_path", sa.Text(), nullable=False),
        sa.Column("backup_path", sa.Text(), nullable=False),
        sa.Column("original_signature", sa.String(length=128), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=12), server_default=sa.text("'available'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["job_id"], ["media_jobs.job_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["media_file_id"], ["media_files.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_media_backups_job_id"), "media_backups", ["job_id"], unique=False)
    op.create_index(
        op.f("ix_media_backups_media_file_id"),
        "media_backups",
        ["media_file_id"],
        unique=False,
    )

    op.create_table(
        "subtitle_policies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("mode", sa.String(length=12), nullable=False),
        sa.Column("languages_json", sa.JSON(), nullable=True),
        sa.Column("unknown_action", sa.String(length=10), nullable=False),
        sa.Column("protect_forced", sa.Boolean(), nullable=False),
        sa.Column("protect_default", sa.Boolean(), nullable=False),
        sa.Column("protect_last_full_dialogue", sa.Boolean(), nullable=False),
        sa.Column("include_external", sa.Boolean(), nullable=False),
        sa.Column("auto_apply", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("audit_only", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("hardlink_action", sa.String(length=12), nullable=False),
        sa.Column("backup_mode", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "subtitle_policy_bindings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=False),
        sa.Column("scope_type", sa.String(length=20), nullable=False),
        sa.Column("scope_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["policy_id"], ["subtitle_policies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_subtitle_policy_bindings_policy_id"),
        "subtitle_policy_bindings",
        ["policy_id"],
        unique=False,
    )

    op.execute(
        sa.text(
            """
            INSERT INTO media_files
                (source, source_key, movie_id, path, relative_path, container, is_active, last_seen_at)
            SELECT
                'radarr',
                'radarr:movie:' || id,
                id,
                rtrim(folder_path, '/') || '/' || movie_file_path,
                movie_file_path,
                container,
                TRUE,
                CURRENT_TIMESTAMP
            FROM movies
            WHERE movie_file_path IS NOT NULL AND movie_file_path != ''
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO media_files
                (source, source_key, path, relative_path, is_active, last_seen_at)
            SELECT
                'sonarr',
                'sonarr:path:' || min(e.id),
                rtrim(s.series_path, '/') || '/' || e.episode_file_path,
                min(e.episode_file_path),
                TRUE,
                CURRENT_TIMESTAMP
            FROM episodes e
            JOIN series s ON s.id = e.series_id
            WHERE e.episode_file_path IS NOT NULL AND e.episode_file_path != ''
            GROUP BY rtrim(s.series_path, '/') || '/' || e.episode_file_path
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO episode_media_files (episode_id, media_file_id)
            SELECT e.id, mf.id
            FROM episodes e
            JOIN series s ON s.id = e.series_id
            JOIN media_files mf
              ON mf.source = 'sonarr'
             AND mf.path = rtrim(s.series_path, '/') || '/' || e.episode_file_path
            WHERE e.episode_file_path IS NOT NULL AND e.episode_file_path != ''
            """
        )
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_subtitle_policy_bindings_policy_id"), table_name="subtitle_policy_bindings")
    op.drop_table("subtitle_policy_bindings")
    op.drop_table("subtitle_policies")
    op.drop_index(op.f("ix_media_backups_media_file_id"), table_name="media_backups")
    op.drop_index(op.f("ix_media_backups_job_id"), table_name="media_backups")
    op.drop_table("media_backups")
    op.drop_index(op.f("ix_media_job_events_job_id"), table_name="media_job_events")
    op.drop_table("media_job_events")
    op.drop_index(op.f("ix_media_jobs_idempotency_key"), table_name="media_jobs")
    op.drop_index(op.f("ix_media_jobs_status"), table_name="media_jobs")
    op.drop_index(op.f("ix_media_jobs_media_file_id"), table_name="media_jobs")
    op.drop_index(op.f("ix_media_jobs_batch_id"), table_name="media_jobs")
    op.drop_table("media_jobs")
    op.drop_table("media_batches")
    op.drop_index(op.f("ix_managed_subtitle_bindings_owner_id"), table_name="managed_subtitle_bindings")
    op.drop_index(op.f("ix_managed_subtitle_bindings_asset_id"), table_name="managed_subtitle_bindings")
    op.drop_table("managed_subtitle_bindings")
    op.drop_index(op.f("ix_managed_subtitle_assets_content_sha256"), table_name="managed_subtitle_assets")
    op.drop_table("managed_subtitle_assets")
    op.drop_index(op.f("ix_subtitle_tracks_inventory_id"), table_name="subtitle_tracks")
    op.drop_table("subtitle_tracks")
    op.drop_index(op.f("ix_subtitle_inventories_media_file_id"), table_name="subtitle_inventories")
    op.drop_table("subtitle_inventories")
    op.drop_index(op.f("ix_episode_media_files_media_file_id"), table_name="episode_media_files")
    op.drop_table("episode_media_files")
    op.drop_index(op.f("ix_media_files_source_key"), table_name="media_files")
    op.drop_index(op.f("ix_media_files_movie_id"), table_name="media_files")
    op.drop_table("media_files")
