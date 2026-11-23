"""Human-readable translations of pipeline reason codes and scorer features.

The UI must never show a raw code like ``ocr_text_heavy`` or ``knn_sim``
without a plain-language label next to it. This module is the single place
those translations live, so the frontend stays a dumb renderer.
"""

from __future__ import annotations

# Gate / rejection reason code -> compact, media-neutral UI tag.  Keep the
# stable reason codes themselves unchanged: feedback, summaries, and archived
# runs use those codes as identifiers.
REJECTION_LABELS: dict[str, str] = {
    "text_heavy": "Text heavy",
    "ocr_text_heavy": "Text heavy",
    "no_text": "No text found",
    "ocr_no_text": "No text found",
    "no_title": "Title not matched",
    "ocr_no_title": "Title not matched",
    "format_blocklist": "Format badge",
    "has_title": "Title detected",
    "ocr_error": "OCR error",
    "ocr_rejected": "OCR rejected",
}


# Gate / rejection reason code -> plain-language detail sentence.
REJECTION_EXPLANATIONS: dict[str, str] = {
    "resolution_floor": "Image resolution is below the minimum width.",
    "style_aesthetic_floor": "Production quality is below the acceptable floor.",
    "off_style_floor": "Visual style is too far from your taste profile.",
    "ocr_text_heavy": "Too much non-title text was detected.",
    "text_heavy": "Too much non-title text was detected.",
    "ocr_no_text": "No text found.",
    "ocr_no_title": "Title not matched in detected text.",
    "no_text": "No text found.",
    "no_title": "Title not matched in detected text.",
    "format_blocklist": "Format badge detected.",
    "has_title": "Title text is excluded by the active profile.",
    "ocr_error": "OCR could not process this poster.",
    "ocr_rejected": "OCR rejected this poster.",
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


def rejection_label(reason: str | None) -> str | None:
    """Return a concise UI tag for a known rejection reason, if one exists."""
    if not reason:
        return None
    return REJECTION_LABELS.get(reason.split(":", 1)[0])


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
    "text_heavy": "Many posters were rejected for extra text — consider raising "
    "OCR_MAX_RESIDUAL_BOXES to tolerate taglines.",
    "ocr_no_text": "Many posters had no readable text — the title typography may be defeating OCR.",
    "no_text": "Many posters had no readable text — the title typography may be defeating OCR.",
    "ocr_no_title": "The title couldn't be matched on several posters — check the expected "
    "title spelling or OCR fuzzy cutoff.",
    "no_title": "The title couldn't be matched on several posters — check the expected "
    "title spelling or OCR fuzzy cutoff.",
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
