"""reduce the schema to the poster product scope

Drops the tables and columns owned by the letterbox, HDR/Dolby Vision, and
audio/subtitle features, and retires their configuration keys.

Configuration is versioned and immutable, so removed keys are not edited out of
history. Instead a new revision is appended carrying only the keys the reduced
catalog still knows, and ``configuration_current`` is repointed at it. Without
this, ``read_current_configuration`` rejects the live revision as containing an
unknown key and the API fails to start.

Revision ID: 0016_poster_scope
Revises: 0015_jmc7b
"""

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0016_poster_scope"
down_revision: str | None = "0015_jmc7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Key prefixes the reduced catalog no longer defines.
_RETIRED_KEY_PREFIXES = ("SUBTITLE_", "SUBGEN_", "AUDIO_SUBS_", "LETTERBOX_")
_RETIRED_KEYS = frozenset({"JOB_DOVI_CONVERSION_CERTIFIED"})


def _purge_retired_configuration_keys() -> None:
    """Append a revision whose values only contain keys the reduced catalog knows."""
    connection = op.get_bind()
    row = connection.execute(
        sa.text(
            "SELECT r.version, r.values FROM configuration_current c "
            "JOIN configuration_revisions r ON r.version = c.current_version "
            "WHERE c.singleton_id = 1"
        )
    ).one_or_none()
    if row is None:
        return

    current_version, values = row
    values = values if isinstance(values, dict) else json.loads(values or "{}")
    kept = {
        key: value
        for key, value in values.items()
        if key not in _RETIRED_KEYS and not key.startswith(_RETIRED_KEY_PREFIXES)
    }
    if kept == values:
        return

    canonical = json.dumps(kept, sort_keys=True, separators=(",", ":"), allow_nan=False)
    checksum = hashlib.sha256(canonical.encode()).hexdigest()
    next_version = current_version + 1
    connection.execute(
        sa.text(
            "INSERT INTO configuration_revisions "
            "(version, values, checksum, schema_version, actor, trigger) "
            "VALUES (:version, CAST(:values AS json), :checksum, 1, "
            "CAST(:actor AS json), 'poster_scope_reduction')"
        ),
        {
            "version": next_version,
            "values": canonical,
            "checksum": checksum,
            "actor": json.dumps({"kind": "system", "id": "migration:0016_poster_scope"}),
        },
    )
    connection.execute(
        sa.text(
            "UPDATE configuration_current SET current_version = :version WHERE singleton_id = 1"
        ),
        {"version": next_version},
    )


def upgrade() -> None:
    _purge_retired_configuration_keys()

    # Indexes disappear with their table; dropping tables child-first keeps every
    # foreign key satisfied at each step (autogenerate does not order these).
    for table in (
        # FK holders
        "sonarr_profile_format_items",
        "radarr_profile_format_items",
        "movie_custom_format_scores",
        "sonarr_overlay_profile_preferences",
        "radarr_overlay_profile_preferences",
        "subtitle_policy_bindings",
        "managed_subtitle_bindings",
        "subtitle_tracks",
        # referenced parents
        "sonarr_custom_formats",
        "sonarr_quality_profiles",
        "radarr_custom_formats",
        "radarr_quality_profiles",
        "subtitle_policies",
        "managed_subtitle_assets",
        "subtitle_inventories",
        # media-subject state
        "letterbox_events",
        "letterbox_state",
        "dovi_state",
    ):
        op.drop_table(table)

    op.drop_column('episodes', 'has_hdr')
    op.drop_column('episodes', 'hdr_type_raw')
    op.drop_column('episodes', 'has_dv')
    op.drop_column('episodes', 'audio_languages_json')
    op.drop_column('episodes', 'subtitle_languages_json')
    op.drop_column('movies', 'has_hdr')
    op.drop_column('movies', 'quality_profile_id')
    op.drop_column('movies', 'preferred_audio_languages_json')
    op.drop_column('movies', 'hdr_type_raw')
    op.drop_column('movies', 'has_dv')
    op.drop_column('movies', 'current_cf_score')
    op.drop_column('movies', 'preferred_subtitle_languages_json')
    op.drop_column('movies', 'quality_cutoff_met')
    op.drop_column('series', 'preferred_subtitle_languages_json')
    op.drop_column('series', 'quality_profile_id')
    op.drop_column('series', 'preferred_audio_languages_json')


def downgrade() -> None:
    """Recreate the removed structures. Their data is not recoverable from here —
    restore from a backup taken before the upgrade if the rows matter."""
    op.add_column('series', sa.Column('preferred_audio_languages_json', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True))
    op.add_column('series', sa.Column('quality_profile_id', sa.INTEGER(), autoincrement=False, nullable=True))
    op.add_column('series', sa.Column('preferred_subtitle_languages_json', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True))
    op.add_column('movies', sa.Column('quality_cutoff_met', sa.BOOLEAN(), autoincrement=False, nullable=True, comment='NULL=unknown, True=current file meets Radarr cutoff, False=below cutoff'))
    op.add_column('movies', sa.Column('preferred_subtitle_languages_json', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True))
    op.add_column('movies', sa.Column('current_cf_score', sa.INTEGER(), autoincrement=False, nullable=True, comment="Raw aggregate custom-format score from Radarr's current movieFile payload"))
    op.add_column('movies', sa.Column('has_dv', sa.BOOLEAN(), autoincrement=False, nullable=True, comment='NULL=not checked, True=has DV, False=missing DV'))
    op.add_column('movies', sa.Column('hdr_type_raw', sa.VARCHAR(length=64), autoincrement=False, nullable=True, comment='Raw Radarr dynamic-range descriptor; prefers videoDynamicRangeType and falls back to videoDynamicRange'))
    op.add_column('movies', sa.Column('preferred_audio_languages_json', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True))
    op.add_column('movies', sa.Column('quality_profile_id', sa.INTEGER(), autoincrement=False, nullable=True))
    op.add_column('movies', sa.Column('has_hdr', sa.BOOLEAN(), autoincrement=False, nullable=True, comment='NULL=not checked, True=has HDR, False=missing HDR'))
    op.add_column('episodes', sa.Column('subtitle_languages_json', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True))
    op.add_column('episodes', sa.Column('audio_languages_json', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True))
    op.add_column('episodes', sa.Column('has_dv', sa.BOOLEAN(), autoincrement=False, nullable=True, comment='NULL=not checked, True=has DV, False=missing DV'))
    op.add_column('episodes', sa.Column('hdr_type_raw', sa.VARCHAR(length=64), autoincrement=False, nullable=True, comment='Raw Sonarr dynamic-range descriptor; prefers videoDynamicRangeType and falls back to videoDynamicRange'))
    op.add_column('episodes', sa.Column('has_hdr', sa.BOOLEAN(), autoincrement=False, nullable=True, comment='NULL=not checked, True=has HDR, False=missing HDR'))
    op.create_table('dovi_state',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('movie_id', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('media_type', sa.VARCHAR(length=10), server_default=sa.text("'movie'::character varying"), autoincrement=False, nullable=False),
    sa.Column('episode_id', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('status', sa.VARCHAR(length=16), server_default=sa.text("'unknown'::character varying"), autoincrement=False, nullable=False),
    sa.Column('dovi_profile', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('dovi_level', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('el_present', sa.BOOLEAN(), autoincrement=False, nullable=True),
    sa.Column('el_type', sa.VARCHAR(length=4), autoincrement=False, nullable=True),
    sa.Column('bl_signal_compatibility_id', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('source_codec', sa.VARCHAR(length=16), autoincrement=False, nullable=True),
    sa.Column('rpu_summary_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('error_reason', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('last_analyzed_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=True),
    sa.Column('source_signature', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('source_fence_token', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('source_hdr_base', sa.VARCHAR(length=32), autoincrement=False, nullable=True),
    sa.Column('source_bit_depth', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('color_primaries', sa.VARCHAR(length=32), autoincrement=False, nullable=True),
    sa.Column('color_transfer', sa.VARCHAR(length=32), autoincrement=False, nullable=True),
    sa.Column('color_space', sa.VARCHAR(length=32), autoincrement=False, nullable=True),
    sa.Column('rpu_present', sa.BOOLEAN(), autoincrement=False, nullable=True),
    sa.Column('bl_present', sa.BOOLEAN(), autoincrement=False, nullable=True),
    sa.Column('analysis_depth', sa.VARCHAR(length=16), autoincrement=False, nullable=True),
    sa.Column('analysis_supported', sa.BOOLEAN(), autoincrement=False, nullable=True),
    sa.Column('warnings_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('validation_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.CheckConstraint("media_type::text = 'movie'::text AND movie_id IS NOT NULL OR media_type::text = 'episode'::text AND episode_id IS NOT NULL", name=op.f('ck_dovi_state_subject')),
    sa.ForeignKeyConstraint(['episode_id'], ['episodes.id'], name=op.f('dovi_state_episode_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['movie_id'], ['movies.id'], name=op.f('dovi_state_movie_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('dovi_state_pkey'))
    )
    op.create_table('letterbox_state',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('movie_id', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('media_type', sa.VARCHAR(length=10), server_default=sa.text("'movie'::character varying"), autoincrement=False, nullable=False),
    sa.Column('episode_id', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('status', sa.VARCHAR(length=24), server_default=sa.text("'prefilter_candidate'::character varying"), autoincrement=False, nullable=False),
    sa.Column('confidence', sa.VARCHAR(length=8), autoincrement=False, nullable=True),
    sa.Column('eligible', sa.BOOLEAN(), server_default=sa.text('true'), autoincrement=False, nullable=False),
    sa.Column('ineligible_reason', sa.VARCHAR(length=120), autoincrement=False, nullable=True),
    sa.Column('source_width', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('source_height', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('prefilter_bucket', sa.VARCHAR(length=24), autoincrement=False, nullable=True),
    sa.Column('prefilter_reason', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('prefilter_aspect_ratio', sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=True),
    sa.Column('last_prefiltered_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('recommended_crop_top', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('recommended_crop_bottom', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('aspect_label', sa.VARCHAR(length=12), autoincrement=False, nullable=True),
    sa.Column('applied_crop_top', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('applied_crop_bottom', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('detect_method', sa.VARCHAR(length=16), autoincrement=False, nullable=True),
    sa.Column('samples_json', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('reviewed', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=False),
    sa.Column('last_detected_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('last_applied_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('resolved_by', sa.VARCHAR(length=16), autoincrement=False, nullable=True),
    sa.Column('resolved_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('original_crop_top', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('original_crop_bottom', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('original_aspect_label', sa.VARCHAR(length=12), autoincrement=False, nullable=True),
    sa.Column('error', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('variable_ar', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=False),
    sa.Column('variable_ar_note', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=True),
    sa.CheckConstraint("media_type::text = 'movie'::text AND movie_id IS NOT NULL AND episode_id IS NULL OR media_type::text = 'episode'::text AND episode_id IS NOT NULL AND movie_id IS NULL", name=op.f('ck_letterbox_state_subject')),
    sa.ForeignKeyConstraint(['episode_id'], ['episodes.id'], name=op.f('letterbox_state_episode_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['movie_id'], ['movies.id'], name=op.f('letterbox_state_movie_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('letterbox_state_pkey'))
    )
    op.create_table('letterbox_events',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('movie_id', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('media_type', sa.VARCHAR(length=10), server_default=sa.text("'movie'::character varying"), autoincrement=False, nullable=False),
    sa.Column('episode_id', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('action', sa.VARCHAR(length=20), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=20), autoincrement=False, nullable=False),
    sa.Column('detail', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('subject_snapshot', postgresql.JSON(astext_type=sa.Text()), server_default=sa.text("'{}'::json"), autoincrement=False, nullable=False),
    sa.CheckConstraint("media_type::text = 'movie'::text AND movie_id IS NOT NULL AND episode_id IS NULL OR media_type::text = 'episode'::text AND episode_id IS NOT NULL AND movie_id IS NULL OR movie_id IS NULL AND episode_id IS NULL", name=op.f('ck_letterbox_event_subject')),
    sa.ForeignKeyConstraint(['episode_id'], ['episodes.id'], name=op.f('letterbox_events_episode_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['movie_id'], ['movies.id'], name=op.f('letterbox_events_movie_id_fkey'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('letterbox_events_pkey'))
    )
    op.create_table('subtitle_inventories',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('media_file_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('file_signature', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('container', sa.VARCHAR(length=20), autoincrement=False, nullable=True),
    sa.Column('duration_seconds', sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=True),
    sa.Column('audio_streams_json', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True),
    sa.Column('chapters_count', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('attachments_count', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('coverage_json', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True),
    sa.Column('probe_tool_versions_json', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True),
    sa.Column('scanned_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('error', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('source_job_id', sa.VARCHAR(length=32), autoincrement=False, nullable=True),
    sa.Column('source_attempt_id', sa.BIGINT(), autoincrement=False, nullable=True),
    sa.Column('source_fence_token', sa.BIGINT(), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['media_file_id'], ['media_files.id'], name=op.f('subtitle_inventories_media_file_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('subtitle_inventories_pkey'))
    )
    op.create_table('managed_subtitle_assets',
    sa.Column('id', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('cache_path', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('content_sha256', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('language_tag', sa.VARCHAR(length=40), autoincrement=False, nullable=False),
    sa.Column('title', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('kind', sa.VARCHAR(length=20), autoincrement=False, nullable=False),
    sa.Column('is_default', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('is_forced', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('is_sdh', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('is_commentary', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=20), autoincrement=False, nullable=False),
    sa.Column('provenance_json', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True),
    sa.Column('restore_on_replacement', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=False),
    sa.Column('active', sa.BOOLEAN(), server_default=sa.text('true'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('last_restored_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('managed_subtitle_assets_pkey'))
    )
    op.create_table('subtitle_policies',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('name', sa.VARCHAR(length=120), autoincrement=False, nullable=False),
    sa.Column('enabled', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=False),
    sa.Column('revision', sa.INTEGER(), server_default=sa.text('1'), autoincrement=False, nullable=False),
    sa.Column('mode', sa.VARCHAR(length=12), autoincrement=False, nullable=False),
    sa.Column('languages_json', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True),
    sa.Column('unknown_action', sa.VARCHAR(length=10), autoincrement=False, nullable=False),
    sa.Column('protect_forced', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('protect_default', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('protect_last_full_dialogue', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('include_external', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('auto_apply', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=False),
    sa.Column('audit_only', sa.BOOLEAN(), server_default=sa.text('true'), autoincrement=False, nullable=False),
    sa.Column('hardlink_action', sa.VARCHAR(length=12), autoincrement=False, nullable=False),
    sa.Column('backup_mode', sa.VARCHAR(length=16), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('subtitle_policies_pkey'))
    )
    op.create_table('radarr_quality_profiles',
    sa.Column('id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('name', sa.VARCHAR(length=255), autoincrement=False, nullable=False),
    sa.Column('upgrade_allowed', sa.BOOLEAN(), autoincrement=False, nullable=True),
    sa.Column('cutoff_format_score', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('min_format_score', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('synced_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('radarr_quality_profiles_pkey'))
    )
    op.create_table('radarr_custom_formats',
    sa.Column('id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('name', sa.VARCHAR(length=255), autoincrement=False, nullable=False),
    sa.Column('include_when_renaming', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('specifications_json', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True),
    sa.Column('synced_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('radarr_custom_formats_pkey'))
    )
    op.create_table('sonarr_quality_profiles',
    sa.Column('id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('name', sa.VARCHAR(length=255), autoincrement=False, nullable=False),
    sa.Column('upgrade_allowed', sa.BOOLEAN(), autoincrement=False, nullable=True),
    sa.Column('cutoff_format_score', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('min_format_score', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('synced_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('sonarr_quality_profiles_pkey'))
    )
    op.create_table('sonarr_custom_formats',
    sa.Column('id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('name', sa.VARCHAR(length=255), autoincrement=False, nullable=False),
    sa.Column('include_when_renaming', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('specifications_json', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True),
    sa.Column('synced_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('sonarr_custom_formats_pkey'))
    )
    op.create_table('subtitle_tracks',
    sa.Column('id', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('inventory_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=10), autoincrement=False, nullable=False),
    sa.Column('stream_index', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('tool_track_id', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('external_path', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('paired_path', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('codec', sa.VARCHAR(length=40), autoincrement=False, nullable=True),
    sa.Column('kind', sa.VARCHAR(length=20), autoincrement=False, nullable=False),
    sa.Column('language_raw', sa.VARCHAR(length=40), autoincrement=False, nullable=True),
    sa.Column('language_tag', sa.VARCHAR(length=40), autoincrement=False, nullable=False),
    sa.Column('language_source', sa.VARCHAR(length=20), autoincrement=False, nullable=False),
    sa.Column('title', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('is_default', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('is_forced', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('is_sdh', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('is_commentary', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('is_generated', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('size_bytes', sa.BIGINT(), autoincrement=False, nullable=True),
    sa.Column('content_sha256', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('metadata_json', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['inventory_id'], ['subtitle_inventories.id'], name=op.f('subtitle_tracks_inventory_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('subtitle_tracks_pkey'))
    )
    op.create_table('managed_subtitle_bindings',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('asset_id', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('owner_type', sa.VARCHAR(length=10), autoincrement=False, nullable=True),
    sa.Column('owner_id', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('media_file_id', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('source_job_id', sa.VARCHAR(length=32), autoincrement=False, nullable=True),
    sa.Column('source_attempt_id', sa.BIGINT(), autoincrement=False, nullable=True),
    sa.Column('source_fence_token', sa.BIGINT(), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['asset_id'], ['managed_subtitle_assets.id'], name=op.f('managed_subtitle_bindings_asset_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['media_file_id'], ['media_files.id'], name=op.f('fk_managed_subtitle_bindings_media_file_id'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('managed_subtitle_bindings_pkey'))
    )
    op.create_table('subtitle_policy_bindings',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('policy_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('scope_type', sa.VARCHAR(length=20), autoincrement=False, nullable=False),
    sa.Column('scope_id', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['policy_id'], ['subtitle_policies.id'], name=op.f('subtitle_policy_bindings_policy_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('subtitle_policy_bindings_pkey'))
    )
    op.create_table('radarr_overlay_profile_preferences',
    sa.Column('profile_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('meet_target', sa.VARCHAR(length=32), autoincrement=False, nullable=True),
    sa.Column('exceed_target', sa.VARCHAR(length=32), autoincrement=False, nullable=True),
    sa.Column('excluded_targets', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['profile_id'], ['radarr_quality_profiles.id'], name=op.f('radarr_overlay_profile_preferences_profile_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('profile_id', name=op.f('radarr_overlay_profile_preferences_pkey'))
    )
    op.create_table('sonarr_overlay_profile_preferences',
    sa.Column('profile_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('meet_target', sa.VARCHAR(length=32), autoincrement=False, nullable=True),
    sa.Column('exceed_target', sa.VARCHAR(length=32), autoincrement=False, nullable=True),
    sa.Column('excluded_targets', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['profile_id'], ['sonarr_quality_profiles.id'], name=op.f('sonarr_overlay_profile_preferences_profile_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('profile_id', name=op.f('sonarr_overlay_profile_preferences_pkey'))
    )
    op.create_table('movie_custom_format_scores',
    sa.Column('movie_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('custom_format_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('score', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('synced_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['custom_format_id'], ['radarr_custom_formats.id'], name=op.f('movie_custom_format_scores_custom_format_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['movie_id'], ['movies.id'], name=op.f('movie_custom_format_scores_movie_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('movie_id', 'custom_format_id', name=op.f('movie_custom_format_scores_pkey'))
    )
    op.create_table('radarr_profile_format_items',
    sa.Column('profile_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('custom_format_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('score', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['custom_format_id'], ['radarr_custom_formats.id'], name=op.f('radarr_profile_format_items_custom_format_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['profile_id'], ['radarr_quality_profiles.id'], name=op.f('radarr_profile_format_items_profile_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('profile_id', 'custom_format_id', name=op.f('radarr_profile_format_items_pkey'))
    )
    op.create_table('sonarr_profile_format_items',
    sa.Column('profile_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('custom_format_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('score', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['custom_format_id'], ['sonarr_custom_formats.id'], name=op.f('sonarr_profile_format_items_custom_format_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['profile_id'], ['sonarr_quality_profiles.id'], name=op.f('sonarr_profile_format_items_profile_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('profile_id', 'custom_format_id', name=op.f('sonarr_profile_format_items_pkey'))
    )
    op.create_index(op.f('ix_dovi_state_source_signature'), 'dovi_state', ['source_signature'], unique=False)
    op.create_index(op.f('ix_dovi_state_movie_id'), 'dovi_state', ['movie_id'], unique=True)
    op.create_index(op.f('ix_dovi_state_media_type'), 'dovi_state', ['media_type'], unique=False)
    op.create_index(op.f('ix_dovi_state_episode_id'), 'dovi_state', ['episode_id'], unique=True)

    op.create_index(op.f('ix_letterbox_state_movie_id'), 'letterbox_state', ['movie_id'], unique=True)
    op.create_index(op.f('ix_letterbox_state_media_type'), 'letterbox_state', ['media_type'], unique=False)
    op.create_index(op.f('ix_letterbox_state_episode_id'), 'letterbox_state', ['episode_id'], unique=True)
    op.create_index(op.f('ix_letterbox_events_movie_id'), 'letterbox_events', ['movie_id'], unique=False)
    op.create_index(op.f('ix_letterbox_events_media_type'), 'letterbox_events', ['media_type'], unique=False)
    op.create_index(op.f('ix_letterbox_events_episode_id'), 'letterbox_events', ['episode_id'], unique=False)
    op.create_index(op.f('ix_subtitle_inventories_source_job_id'), 'subtitle_inventories', ['source_job_id'], unique=False)
    op.create_index(op.f('ix_subtitle_inventories_media_file_id'), 'subtitle_inventories', ['media_file_id'], unique=True)
    op.create_index(op.f('ix_managed_subtitle_assets_content_sha256'), 'managed_subtitle_assets', ['content_sha256'], unique=False)
    op.create_index(op.f('ix_subtitle_tracks_inventory_id'), 'subtitle_tracks', ['inventory_id'], unique=False)
    op.create_index(op.f('ix_managed_subtitle_bindings_source_job_id'), 'managed_subtitle_bindings', ['source_job_id'], unique=False)
    op.create_index(op.f('ix_managed_subtitle_bindings_owner_id'), 'managed_subtitle_bindings', ['owner_id'], unique=False)
    op.create_index(op.f('ix_managed_subtitle_bindings_media_file_id'), 'managed_subtitle_bindings', ['media_file_id'], unique=False)
    op.create_index(op.f('ix_managed_subtitle_bindings_asset_id'), 'managed_subtitle_bindings', ['asset_id'], unique=False)
    op.create_index(op.f('ix_subtitle_policy_bindings_policy_id'), 'subtitle_policy_bindings', ['policy_id'], unique=False)
