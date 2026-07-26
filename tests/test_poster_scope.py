"""Guards that keep the product scoped to the poster pipeline.

Letterbox, HDR/Dolby Vision, and audio/subtitle management were removed. These
tests fail loudly if any of it creeps back — through a stray import, a
resurrected job type, a re-registered router, or an orphaned ORM table — so the
reduction cannot silently erode.

They assert shape, not behaviour. Adding a genuinely new poster capability means
updating the expected sets here on purpose, which is the point.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from marquee.core.jobs.delivery import EXECUTION_HANDLERS
from marquee.core.jobs.manifest import ENABLED_JOB_TYPES, JOB_DEFINITION_REGISTRY
from marquee.core.jobs.presenters import JOB_PRESENTER_REGISTRY
from marquee.database import Base
from marquee.main import app

ROOT = Path(__file__).resolve().parent.parent

# Vocabulary of the removed features. `dovi` is word-bounded so it cannot match
# inside unrelated identifiers.
RETIRED_TOKENS = re.compile(
    r"letterbox|subtitle|subgen|mkvmerge|mkvpropedit|ffmpeg|ffprobe|\bdovi\b",
    re.IGNORECASE,
)

# The OCR text gate legitimately rejects format-badge words printed on posters
# ("4K UHD HDR BLURAY DOLBY ATMOS"), so its blocklist is not scope creep.
SCOPE_SCAN_EXCLUSIONS = {"marquee/pipeline/ocr_filter.py"}


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _backend_sources() -> list[Path]:
    return [
        path
        for path in (ROOT / "marquee").rglob("*.py")
        if "__pycache__" not in path.parts
        and str(path.relative_to(ROOT)) not in SCOPE_SCAN_EXCLUSIONS
    ]


def test_backend_sources_are_free_of_retired_feature_vocabulary() -> None:
    """No import, identifier, or comment may reference a removed subsystem."""
    offenders: list[str] = []
    for path in _backend_sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if RETIRED_TOKENS.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert not offenders, "retired-feature references survive:\n" + "\n".join(offenders)


def test_job_catalogue_is_exactly_the_poster_scope() -> None:
    assert {definition.job_type for definition in JOB_DEFINITION_REGISTRY} == {
        # analysis
        "poster_pipeline",
        "poster_pipeline_batch",
        "poster_pipeline_tv_batch",
        "poster_rescan",
        # artwork mutation
        "poster_deploy",
        "poster_restore",
        "poster_reset",
        "poster_backup_subject",
        "poster_backup_all",
        "poster_deploy_reset",
        "poster_heal",
        # taste / ranking engine
        "taste_rebuild",
        "taste_map",
        "taste_enrich",
        "ranking_residual_train",
        # shared platform
        "library_sync",
        "backup_create",
        "poster_maintenance",
        "pipeline_cache_clear",
        "job_retention_purge",
        "system_metrics_purge",
        "system_noop",
        "radarr_upgrade",  # reserved, permanently dispatch-disabled
    }


def test_every_definition_is_executable_or_deliberately_not() -> None:
    """A dispatch-enabled type must have a handler; a parent must not."""
    for definition in JOB_DEFINITION_REGISTRY:
        if definition.job_type in ENABLED_JOB_TYPES:
            assert definition.job_type in EXECUTION_HANDLERS, definition.job_type
        elif definition.child_job_types:
            assert definition.job_type not in EXECUTION_HANDLERS, definition.job_type


def test_every_definition_has_a_dedicated_presenter() -> None:
    for definition in JOB_DEFINITION_REGISTRY:
        assert definition.presenter_key in JOB_PRESENTER_REGISTRY, definition.job_type


def test_router_surface_is_exactly_the_poster_scope() -> None:
    groups = {
        path.split("/")[2]
        for route in app.routes
        if (path := getattr(route, "path", "")).startswith("/api/")
    }
    assert groups == {
        "config",
        "feedback",
        "jobs",
        "library",
        "movies",
        "pipeline",
        "series",
        "settings",
        "sync",
        "system",
        "taste",
        "text-profiles",
        # DEBUG-only OCR label capture; conftest runs with DEBUG=True.
        "dev",
        # ONBOARDING_ENABLED is forced on for the suite (see conftest).
        "onboarding",
    }


@pytest.mark.parametrize(
    "path",
    [
        "/api/letterbox/status",
        "/api/hdr",
        "/api/hdr/summary",
        "/api/subtitle-policies",
        "/api/subtitle-generators",
        "/api/audio-subs/summary",
        "/api/system/status/generators",
    ],
)
async def test_removed_routes_are_unreachable(client, path: str) -> None:
    assert (await client.get(path)).status_code == 404


def test_orm_declares_no_retired_tables() -> None:
    retired = {
        "letterbox_state",
        "letterbox_events",
        "dovi_state",
        "subtitle_inventories",
        "subtitle_tracks",
        "subtitle_policies",
        "subtitle_policy_bindings",
        "managed_subtitle_assets",
        "managed_subtitle_bindings",
        "radarr_custom_formats",
        "radarr_quality_profiles",
        "radarr_profile_format_items",
        "radarr_overlay_profile_preferences",
        "movie_custom_format_scores",
        "sonarr_custom_formats",
        "sonarr_quality_profiles",
        "sonarr_profile_format_items",
        "sonarr_overlay_profile_preferences",
    }
    assert retired.isdisjoint(set(Base.metadata.tables))


def test_media_subject_models_carry_no_retired_columns() -> None:
    retired = {
        "has_hdr",
        "has_dv",
        "hdr_type_raw",
        "quality_profile_id",
        "quality_cutoff_met",
        "current_cf_score",
        "audio_languages_json",
        "subtitle_languages_json",
        "preferred_audio_languages_json",
        "preferred_subtitle_languages_json",
    }
    for name in ("movies", "series", "episodes"):
        columns = set(Base.metadata.tables[name].columns.keys())
        assert retired.isdisjoint(columns), f"{name} still declares {retired & columns}"


def test_no_retired_configuration_keys_remain() -> None:
    from marquee.core.configuration import CONFIGURATION_CATALOG

    offenders = [
        key
        for key in CONFIGURATION_CATALOG
        if key.startswith(("SUBTITLE_", "SUBGEN_", "AUDIO_SUBS_", "LETTERBOX_", "HDR_"))
    ]
    assert not offenders, offenders


def test_declared_dependencies_carry_no_retired_packages() -> None:
    manifest = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for package in ("pysubs2", "langcodes", "language_data", "faster-whisper", "stable-ts"):
        assert package not in manifest, package
    assert not (ROOT / "requirements.txt").exists(), "pyproject.toml is the sole manifest"
