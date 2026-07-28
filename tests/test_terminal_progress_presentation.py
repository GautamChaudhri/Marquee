"""A finished job must not present itself as still working.

The progress writer deliberately keeps the last measured values on
terminalization — a failure has to show where it stopped rather than jump to
100% — and marks the document ``freshness=terminal``. That makes the retained
values evidence, not live telemetry, so anything rendering them has to read the
freshness. These cover the presentation side of that contract.
"""

from __future__ import annotations

from marquee.core.jobs.presenters.base import _display_label


def test_stage_label_keys_are_not_shown_to_users():
    """ProgressPolicy stages carry translation keys; the writer stores one as the
    headline, so the raw key reached the UI verbatim."""
    assert _display_label("jobs.poster_pipeline.progress.finalizing") == "Finalizing"
    assert _display_label("jobs.library_sync.progress.sync_movies") == "Sync movies"


def test_real_headlines_pass_through_untouched():
    assert _display_label("Select a poster from 47 candidates") == (
        "Select a poster from 47 candidates"
    )
    assert _display_label("Downloading 3 of 8") == "Downloading 3 of 8"
    # A bare stage key is already display text, not a dotted catalogue key.
    assert _display_label("finalizing") == "finalizing"
    assert _display_label(None) is None
