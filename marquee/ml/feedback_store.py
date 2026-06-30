"""Labels v2 store — self-contained feedback records in JSONL.

Each label embeds the candidate's full feature vectors *at feedback time*, so
training never depends on a run's working directory surviving a re-run (the
v1 format joined ``(title, orig_filename)`` against ``pipeline_run.json``,
which is overwritten when a movie is re-run). The trainer still reads v1 rows
for backward compatibility (the ~78 reconstructed labels) by falling back to
the run-JSON join.

The file is the single training source of truth: append-only, hand-editable,
one JSON object per line. Writes are atomic (a single ``os.write`` under
``O_APPEND``); event removal rewrites via a temp file + ``os.replace``.
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from math import ceil
from pathlib import Path

from marquee.core.pipeline_config import pipeline_settings

logger = logging.getLogger(__name__)

# Gate rejection reason -> the config knob that controls that gate. Used for
# the "you've overridden gate X N times at its current threshold" alert.
GATE_REASON_KNOBS: dict[str, str] = {
    "ocr_text_heavy": "OCR_MAX_RESIDUAL_BOXES",
    "style_aesthetic_floor": "GATE_MIN_AESTHETIC",
    "off_style_floor": "GATE_MIN_KNN_SIM",
    "resolution_floor": "GATE_MIN_WIDTH",
    "no_title": "OCR_REQUIRE_TITLE",
    "no_text": "OCR_ACCEPT_NO_TEXT",
}


def labels_path() -> Path:
    return Path(pipeline_settings.FEEDBACK_LABELS_PATH)


def gate_snapshot() -> dict[str, object]:
    """Current values of every gate knob referenced by an override alert."""
    return {
        knob: getattr(pipeline_settings, knob) for knob in sorted(set(GATE_REASON_KNOBS.values()))
    }


def append_labels(records: list[dict]) -> None:
    """Atomically append label rows (single write under O_APPEND)."""
    if not records:
        return
    path = labels_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, blob.encode("utf-8"))
    finally:
        os.close(fd)


def read_all() -> list[dict]:
    """Parse every label row (v1 + v2), skipping malformed lines."""
    path = labels_path()
    if not path.exists():
        return []
    rows: list[dict] = []
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            logger.warning("labels.jsonl line %d skipped: %s", line_number, exc)
    return rows


def remove_event(event_id: str) -> list[dict]:
    """Remove all rows for an event_id; return the removed rows (for undo)."""
    path = labels_path()
    if not path.exists():
        return []
    kept: list[str] = []
    removed: list[dict] = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError:
            kept.append(line)  # preserve unparseable lines untouched
            continue
        if row.get("event_id") == event_id:
            removed.append(row)
        else:
            kept.append(stripped)

    if not removed:
        return []

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(line + "\n" for line in kept), encoding="utf-8")
    os.replace(tmp, path)
    return removed


# ---------------------------------------------------------------------------
# Derived stats
# ---------------------------------------------------------------------------


def _movie_key(row: dict) -> object:
    return row.get("movie_id") if row.get("movie_id") is not None else row.get("title")


def positive_exemplar_count(n_orderable: int) -> int:
    """How many top-of-final-order posters become positive exemplars/pseudo-
    labels for one ranking submission (live or taste-test). Shared by the
    feedback route's staging logic, the pairwise trainer's pseudo-label
    builder, and this module's own stats so all three agree.

    ``min(3, 20% of the list)``, floor 1 — small slates still stage their #1;
    long slates don't stage an unbounded chunk from a single session.
    """
    if n_orderable <= 0:
        return 0
    return max(1, min(3, ceil(0.2 * n_orderable)))


def summary() -> dict:
    """Label-derived stats (movie/genre augmentation happens in the route).

    Counts label-equivalent signals: a v1/v2 row is one label; a v4 ranking
    event contributes one positive per staged exemplar (the top slice of its
    final order — see ``positive_exemplar_count``) and one negative per hated
    poster (everything else in the order is an untouched relative position,
    not a stated preference, and isn't counted). Pre-cutover v3 ranking rows
    are ignored — see design/30 and the v4 cutover.
    """
    rows = read_all()
    movies = {_movie_key(row) for row in rows if _movie_key(row) is not None}
    positives = 0
    negatives = 0
    total = 0
    for row in rows:
        if row.get("type") == "ranking" and row.get("v") == 4:
            order = row.get("order") or []
            hated = row.get("hated") or []
            favorites = positive_exemplar_count(len(order))
            positives += favorites
            negatives += len(hated)
            total += favorites + len(hated)
        elif row.get("label") is not None:
            total += 1
            if row.get("label") == 1:
                positives += 1
            else:
                negatives += 1
    return {
        "total": total,
        "movies": len(movies),
        "positives": positives,
        "negatives": negatives,
        "movie_keys": sorted(str(m) for m in movies),
    }


def gate_override_alerts() -> list[dict]:
    """Per-gate override counts, filtered to labels whose snapshot still
    matches the current knob value (decision 3 — changing a threshold zeroes
    its counter automatically)."""
    from marquee.api.explanations import REJECTION_SUGGESTIONS  # noqa: PLC0415

    rows = read_all()
    current = {knob: getattr(pipeline_settings, knob) for knob in set(GATE_REASON_KNOBS.values())}

    def _tally(reason_raw: str | None, snapshot: dict) -> None:
        reason = (reason_raw or "").split(":", 1)[0]
        knob = GATE_REASON_KNOBS.get(reason)
        if knob is None:
            return
        # Count only if the override was recorded at the *current* knob value.
        if (snapshot or {}).get(knob) == current[knob]:
            counts[reason] += 1

    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("type") == "ranking" and row.get("v") == 4:
            # A staged-exemplar poster the pipeline had gate-rejected would be
            # an override of that gate, same as a v2 override pick. In
            # practice `order` only ever contains already-ranked (gate-passed)
            # candidates today, so this rarely fires — kept for symmetry/
            # forward-compat if ranking ever exposes gate-rejected candidates.
            snapshot = row.get("gate_snapshot") or {}
            order = row.get("order") or []
            for candidate in order[: positive_exemplar_count(len(order))]:
                if candidate.get("rejection_reason"):
                    _tally(candidate.get("rejection_reason"), snapshot)
            continue
        if row.get("type") == "ranking":
            continue  # pre-cutover v3 ranking row — ignored
        if row.get("role") != "user_pick" or row.get("action") != "override":
            continue
        _tally(row.get("rejection_reason"), row.get("gate_snapshot") or {})

    threshold = pipeline_settings.FEEDBACK_GATE_ALERT_THRESHOLD
    alerts = []
    for reason, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        knob = GATE_REASON_KNOBS[reason]
        alerts.append(
            {
                "gate": reason,
                "overrides": count,
                "threshold_knob": knob,
                "current_value": current[knob],
                "active": count >= threshold,
                "suggestion": REJECTION_SUGGESTIONS.get(reason),
            }
        )
    return alerts
