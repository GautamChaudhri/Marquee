"""Tests for path_utils — the 4-layer path validation guard.

Tests cover:
  - Layer 1: Null byte rejection
  - Layer 2: Per-source translation (radarr vs sonarr)
  - Layer 2: Untranslated path warning when prefix doesn't match
  - Layer 3: Canonical resolution (.. collapse)
  - Layer 4: MEDIA_ROOTS enforcement (with auto-derived roots)
  - No-op mode (no mapping configured, no MEDIA_ROOTS → allow all)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from marquee.core.path_utils import PathValidationError, safe_translate_and_validate


# ---------------------------------------------------------------------------
# Layer 1 — Null byte rejection
# ---------------------------------------------------------------------------


def test_rejects_null_byte():
    with pytest.raises(PathValidationError, match="null byte"):
        safe_translate_and_validate("/movies/\0etc")


# ---------------------------------------------------------------------------
# Layer 2 — Per-source translation
# ---------------------------------------------------------------------------


class TestRadarrTranslation:
    """Radarr paths use RADARR_PATH_PREFIX → RADARR_MEDIA_PATH."""

    def test_translates_radarr_path(self):
        settings = _mock_settings(
            radarr_path_prefix="/plunder/movies",
            radarr_media_path="/Volumes/PLUNDER/Media/Movies",
        )
        with patch("marquee.core.path_utils.settings", settings):
            result = safe_translate_and_validate(
                "/plunder/movies/Dune (2021)", source="radarr"
            )
        assert result == Path("/Volumes/PLUNDER/Media/Movies/Dune (2021)")

    def test_passthrough_when_no_radarr_mapping(self):
        """Without mapping, radarr paths pass through untranslated."""
        settings = _mock_settings()  # no path mapping
        with patch("marquee.core.path_utils.settings", settings):
            result = safe_translate_and_validate(
                "/plunder/movies/Dune (2021)", source="radarr"
            )
        assert result == Path("/plunder/movies/Dune (2021)")

    def test_passthrough_when_prefix_doesnt_match(self):
        settings = _mock_settings(
            radarr_path_prefix="/plunder/movies",
            radarr_media_path="/Volumes/PLUNDER/Media/Movies",
            media_roots=["/"],  # allow-all for this test
        )
        with patch("marquee.core.path_utils.settings", settings):
            result = safe_translate_and_validate(
                "/some/other/path/Dune", source="radarr"
            )
        # Passes through untranslated (caller is expected to log a warning)
        assert str(result) == "/some/other/path/Dune"


class TestSonarrTranslation:
    """Sonarr paths use SONARR_PATH_PREFIX → SONARR_MEDIA_PATH."""

    def test_translates_sonarr_path(self):
        settings = _mock_settings(
            sonarr_path_prefix="/plunder/tv",
            sonarr_media_path="/Volumes/PLUNDER/Media/TV",
        )
        with patch("marquee.core.path_utils.settings", settings):
            result = safe_translate_and_validate(
                "/plunder/tv/Breaking Bad", source="sonarr"
            )
        assert result == Path("/Volumes/PLUNDER/Media/TV/Breaking Bad")

    def test_passthrough_when_no_sonarr_mapping(self):
        settings = _mock_settings()
        with patch("marquee.core.path_utils.settings", settings):
            result = safe_translate_and_validate(
                "/plunder/tv/Breaking Bad", source="sonarr"
            )
        assert result == Path("/plunder/tv/Breaking Bad")

    def test_passthrough_when_prefix_doesnt_match(self):
        settings = _mock_settings(
            sonarr_path_prefix="/plunder/tv",
            sonarr_media_path="/Volumes/PLUNDER/Media/TV",
            media_roots=["/"],  # allow-all for this test
        )
        with patch("marquee.core.path_utils.settings", settings):
            result = safe_translate_and_validate(
                "/other/tv/Breaking Bad", source="sonarr"
            )
        assert str(result) == "/other/tv/Breaking Bad"


# ---------------------------------------------------------------------------
# Layer 2 — Logging on untranslated path
# ---------------------------------------------------------------------------


def test_logs_warning_when_prefix_mismatches(caplog):
    """When mapping IS configured but path doesn't match, log a warning."""
    settings = _mock_settings(
        radarr_path_prefix="/plunder/movies",
        radarr_media_path="/Volumes/PLUNDER/Media/Movies",
        media_roots=["/"],  # allow-all so Layer 4 doesn't block the test
    )
    with patch("marquee.core.path_utils.settings", settings):
        safe_translate_and_validate("/alien/path/Movie", source="radarr")

    assert "did not match configured radarr path prefix" in caplog.text


def test_no_warning_when_not_configured(caplog):
    """No warning when no mapping is configured (passthrough is expected)."""
    settings = _mock_settings()  # nothing configured
    with patch("marquee.core.path_utils.settings", settings):
        safe_translate_and_validate("/anything/movie", source="radarr")

    assert "did not match configured" not in caplog.text


# ---------------------------------------------------------------------------
# Layer 3 — Canonical resolution
# ---------------------------------------------------------------------------


def test_collapses_dot_dot():
    """../../etc/passwd should resolve outside media — caught by Layer 4."""
    settings = _mock_settings(media_roots=["/Volumes/PLUNDER/Media/Movies"])
    with patch("marquee.core.path_utils.settings", settings):
        with pytest.raises(PathValidationError, match="not within any allowed"):
            safe_translate_and_validate(
                "/Volumes/PLUNDER/Media/Movies/../../etc/passwd",
                source="radarr",
            )


def test_resolves_symlinks():
    """Resolved path (not input path) is what gets checked against roots."""
    # Create a temp dir with a symlink, verify the resolved path is used
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        media = Path(tmpdir) / "media"
        media.mkdir()
        link = Path(tmpdir) / "link_to_media"
        link.symlink_to(media)

        settings = _mock_settings(media_roots=[str(media.resolve())])
        with patch("marquee.core.path_utils.settings", settings):
            # /tmp is a symlink to /private/tmp on macOS — both path and
            # root get resolved, so compare against the resolved form
            result = safe_translate_and_validate(
                str(link / "Dune"), source="radarr"
            )
            assert str(result).startswith(str(media.resolve()))


# ---------------------------------------------------------------------------
# Layer 4 — MEDIA_ROOTS enforcement
# ---------------------------------------------------------------------------


def test_rejects_path_outside_media_roots():
    settings = _mock_settings(media_roots=["/Volumes/PLUNDER/Media/Movies"])
    with patch("marquee.core.path_utils.settings", settings):
        with pytest.raises(PathValidationError, match="not within any allowed"):
            safe_translate_and_validate("/etc/hosts", source="radarr")


def test_accepts_path_within_media_roots():
    settings = _mock_settings(media_roots=["/Volumes/PLUNDER/Media/Movies"])
    with patch("marquee.core.path_utils.settings", settings):
        result = safe_translate_and_validate(
            "/Volumes/PLUNDER/Media/Movies/Dune (2021)", source="radarr"
        )
        assert "Dune" in str(result)


def test_multiple_roots_pass():
    settings = _mock_settings(
        media_roots=[
            "/Volumes/PLUNDER/Media/Movies",
            "/Volumes/PLUNDER/Media/TV",
        ]
    )
    with patch("marquee.core.path_utils.settings", settings):
        result = safe_translate_and_validate(
            "/Volumes/PLUNDER/Media/TV/Breaking Bad", source="sonarr"
        )
        assert "Breaking" in str(result)


def test_no_enforcement_when_roots_empty():
    """Empty MEDIA_ROOTS + no mapping = allow all (dev/trusted mode)."""
    settings = _mock_settings(media_roots=[])  # empty
    with patch("marquee.core.path_utils.settings", settings):
        result = safe_translate_and_validate("/any/path/at/all", source="radarr")
    assert result == Path("/any/path/at/all")


# ---------------------------------------------------------------------------
# Auto-derived MEDIA_ROOTS (effective_media_roots)
# ---------------------------------------------------------------------------


def test_auto_derived_roots_from_radarr_media_path():
    """RADARR_MEDIA_PATH is automatically included in effective_media_roots."""
    settings = _mock_settings(
        radarr_media_path="/Volumes/PLUNDER/Media/Movies",
        media_roots=[],  # no manual roots
    )
    with patch("marquee.core.path_utils.settings", settings):
        result = safe_translate_and_validate(
            "/Volumes/PLUNDER/Media/Movies/Dune (2021)", source="radarr"
        )
        assert "Dune" in str(result)


def test_auto_derived_roots_from_sonarr_media_path():
    """SONARR_MEDIA_PATH is automatically included."""
    settings = _mock_settings(
        sonarr_media_path="/Volumes/PLUNDER/Media/TV",
        media_roots=[],
    )
    with patch("marquee.core.path_utils.settings", settings):
        result = safe_translate_and_validate(
            "/Volumes/PLUNDER/Media/TV/Breaking Bad", source="sonarr"
        )
        assert "Breaking" in str(result)


def test_auto_derived_roots_reject_wrong_path():
    """Auto-derived root for movies should reject TV paths."""
    settings = _mock_settings(
        radarr_media_path="/Volumes/PLUNDER/Media/Movies",
        media_roots=[],
    )
    with patch("marquee.core.path_utils.settings", settings):
        with pytest.raises(PathValidationError):
            safe_translate_and_validate(
                "/Volumes/PLUNDER/Media/TV/Breaking Bad", source="sonarr"
            )


def test_manual_and_auto_roots_combined():
    """Manual MEDIA_ROOTS + auto-derived roots both apply."""
    settings = _mock_settings(
        radarr_media_path="/Volumes/PLUNDER/Media/Movies",
        media_roots=["/some/manual/path"],
    )
    with patch("marquee.core.path_utils.settings", settings):
        # Auto-derived pass
        safe_translate_and_validate(
            "/Volumes/PLUNDER/Media/Movies/Dune", source="radarr"
        )
        # Manual pass
        safe_translate_and_validate(
            "/some/manual/path/extra", source="radarr"
        )


# ---------------------------------------------------------------------------
# Full integration — translate + resolve + validate
# ---------------------------------------------------------------------------


def test_full_flow_radarr_happy_path():
    """Radarr path: translated, resolved, passes root check."""
    settings = _mock_settings(
        radarr_path_prefix="/plunder/movies",
        radarr_media_path="/Volumes/PLUNDER/Media/Movies",
    )
    with patch("marquee.core.path_utils.settings", settings):
        result = safe_translate_and_validate(
            "/plunder/movies/Dune (2021)", source="radarr"
        )
    assert result == Path("/Volumes/PLUNDER/Media/Movies/Dune (2021)")


def test_full_flow_sonarr_happy_path():
    """Sonarr path: translated, resolved, passes root check."""
    settings = _mock_settings(
        sonarr_path_prefix="/plunder/tv",
        sonarr_media_path="/Volumes/PLUNDER/Media/TV",
    )
    with patch("marquee.core.path_utils.settings", settings):
        result = safe_translate_and_validate(
            "/plunder/tv/Breaking Bad", source="sonarr"
        )
    assert result == Path("/Volumes/PLUNDER/Media/TV/Breaking Bad")


def test_full_flow_unresolvable_path():
    """Unresolvable paths should raise."""
    settings = _mock_settings()
    with patch("marquee.core.path_utils.settings", settings):
        with patch("pathlib.Path.resolve", side_effect=OSError("disk missing")):
            with pytest.raises(PathValidationError, match=r"Could not resolve"):
                safe_translate_and_validate("/some/path", source="radarr")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_settings(
    *,
    radarr_path_prefix: str | None = None,
    radarr_media_path: str | None = None,
    sonarr_path_prefix: str | None = None,
    sonarr_media_path: str | None = None,
    media_roots: list[str] | None = None,
):
    """Build a mock settings object with minimal attributes used by path_utils."""

    class MockSettings:
        RADARR_PATH_PREFIX = radarr_path_prefix
        RADARR_MEDIA_PATH = radarr_media_path
        SONARR_PATH_PREFIX = sonarr_path_prefix
        SONARR_MEDIA_PATH = sonarr_media_path
        MEDIA_ROOTS = media_roots if media_roots is not None else []

        @property
        def radarr_path_configured(self):
            return self.RADARR_PATH_PREFIX is not None and self.RADARR_MEDIA_PATH is not None

        @property
        def sonarr_path_configured(self):
            return self.SONARR_PATH_PREFIX is not None and self.SONARR_MEDIA_PATH is not None

        def translate_radarr_path(self, arr_path: str) -> str:
            if not arr_path or not self.RADARR_PATH_PREFIX:
                return arr_path
            if arr_path.startswith(self.RADARR_PATH_PREFIX):
                return self.RADARR_MEDIA_PATH + arr_path[len(self.RADARR_PATH_PREFIX):]
            return arr_path

        def translate_sonarr_path(self, arr_path: str) -> str:
            if not arr_path or not self.SONARR_PATH_PREFIX:
                return arr_path
            if arr_path.startswith(self.SONARR_PATH_PREFIX):
                return self.SONARR_MEDIA_PATH + arr_path[len(self.SONARR_PATH_PREFIX):]
            return arr_path

        @property
        def effective_media_roots(self) -> list[Path]:
            roots: set[Path] = set()
            for raw in self.MEDIA_ROOTS:
                try:
                    roots.add(Path(raw).resolve())
                except (OSError, RuntimeError):
                    pass
            if self.RADARR_MEDIA_PATH:
                try:
                    roots.add(Path(self.RADARR_MEDIA_PATH).resolve())
                except (OSError, RuntimeError):
                    pass
            if self.SONARR_MEDIA_PATH:
                try:
                    roots.add(Path(self.SONARR_MEDIA_PATH).resolve())
                except (OSError, RuntimeError):
                    pass
            return sorted(roots)

    return MockSettings()
