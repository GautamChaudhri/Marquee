"""Human-readable translations of pipeline reason codes and scorer features.

The UI must never show a raw code like ``ocr_text_heavy`` or ``knn_sim``
without a plain-language label next to it. This module is the single place
those translations live, so the frontend stays a dumb renderer.
"""

from __future__ import annotations

# Gate / rejection reason code -> plain-language sentence.
REJECTION_EXPLANATIONS: dict[str, str] = {
    "resolution_floor": "Image resolution is below the minimum width.",
    "style_aesthetic_floor": "Production quality is below the acceptable floor.",
    "off_style_floor": "Visual style is too far from your taste profile.",
    "ocr_text_heavy": "Poster carries non-title text (taglines, credits, billing).",
    "ocr_no_text": "No readable text was found on the poster.",
    "ocr_no_title": "The movie title could not be matched in the poster text.",
    "no_text": "No readable text was found on the poster.",
    "no_title": "The movie title could not be matched in the poster text.",
    "dedup_sha256": "Byte-for-byte duplicate of another candidate.",
    "dedup_phash": "Visually near-identical to another candidate.",
    "fan_junk": "Flagged as low-quality fan art by the combined junk gate.",
}

# Top-level scorer feature -> short, positive plain-language label.
FEATURE_LABELS: dict[str, str] = {
    "knn_sim": "Style match to your taste profile",
    "aesthetic": "Production quality",
    "title_colorfulness": "Colored stylized title text",
    "face_area": "Restrained use of faces",
    "text_residual": "Title-only text (little clutter)",
    "provenance": "Community-rated on TMDB",
    "sharpness": "Image sharpness",
    "resolution": "High resolution",
    "lang_match": "Matches your preferred language",
    "dino_knn": "Texture/medium match to your taste",
    "taste_typicality": "Typical of posters you like",
    "quality_artifacts": "Free of compression artifacts",
    "official_family": "Official key-art family",
}


def explain_rejection(reason: str | None) -> str:
    """Map a rejection reason code to a sentence (best-effort, prefix-aware)."""
    if not reason:
        return "Rejected."
    # Reasons may carry a ``code: detail`` suffix (e.g. feature_error: ...).
    base = reason.split(":", 1)[0]
    if base in REJECTION_EXPLANATIONS:
        return REJECTION_EXPLANATIONS[base]
    if reason in REJECTION_EXPLANATIONS:
        return REJECTION_EXPLANATIONS[reason]
    if base.startswith("feature_error") or base.startswith("download_error"):
        return "Could not be processed (image or download error)."
    return reason.replace("_", " ").capitalize() + "."


def explain_top_contributions(
    contributions: dict[str, float] | None,
    *,
    top_n: int = 3,
) -> list[str]:
    """The top-N positive contributing factors, phrased for a human."""
    if not contributions:
        return []
    ranked = sorted(
        ((name, value) for name, value in contributions.items() if value > 0),
        key=lambda item: item[1],
        reverse=True,
    )
    return [
        FEATURE_LABELS.get(name, name.replace("_", " ").capitalize())
        for name, _value in ranked[:top_n]
    ]


# Rejection-summary reason -> a one-line actionable suggestion (scenario D).
REJECTION_SUGGESTIONS: dict[str, str] = {
    "ocr_text_heavy": "Many posters were rejected for extra text — consider raising "
    "OCR_MAX_RESIDUAL_BOXES to tolerate taglines.",
    "ocr_no_text": "Many posters had no readable text — the title typography may be "
    "defeating OCR.",
    "ocr_no_title": "The title couldn't be matched on several posters — check the "
    "movie title spelling or OCR fuzzy cutoff.",
    "style_aesthetic_floor": "Several posters fell below the aesthetic floor — "
    "consider lowering GATE_MIN_AESTHETIC.",
    "off_style_floor": "Several posters were off-style — the candidate pool may not "
    "match your taste profile.",
    "resolution_floor": "Several posters were below the resolution floor — there may "
    "be few high-resolution options for this title.",
}


def suggest_for_summary(rejection_summary: dict[str, int]) -> str | None:
    """Pick the dominant rejection reason and return its suggestion, if any."""
    if not rejection_summary:
        return None
    dominant = max(rejection_summary.items(), key=lambda item: item[1])
    return REJECTION_SUGGESTIONS.get(dominant[0])
