"""Helpers for Radarr overlay HDR, custom-format, and preference evaluation."""

from __future__ import annotations

from collections.abc import Iterable

HDR_TAG_ORDER = ("hdr", "hdr10", "hdr10p", "dovi")
DISPLAY_HDR_TAG_ORDER = ("hdr", "hdr10", "hdr10p", "dovi", "dovi_no_fallback")
PREFERENCE_TARGET_ORDER = (
    "sdr",
    "hdr",
    "hdr10",
    "hdr10p",
    "dovi_no_fallback",
    "dovi_fallback",
)
PREFERENCE_STATUS_ORDER = (
    "below_target",
    "meets_target",
    "exceeds_target",
    "no_hdr_target",
)

HDR_KEYWORDS: dict[str, tuple[str, ...]] = {
    "hdr10p": ("HDR10PLUS", "HDR10+", "HDR10P"),
    "hdr10": ("HDR10",),
    "dovi": ("DOLBY VISION", "DOLBY", "DV"),
    "hdr": ("HLG", "PQ", "HDR"),
}


def _normalized(value: str | None) -> str:
    return (value or "").upper().strip()


def classify_hdr_tags(raw_value: str | None) -> list[str]:
    """Map Radarr dynamic-range strings into overlay HDR tags."""
    raw = _normalized(raw_value)
    if not raw or raw == "SDR":
        return []

    has_dovi = any(token in raw for token in HDR_KEYWORDS["dovi"])
    has_hdr10p = any(token in raw for token in HDR_KEYWORDS["hdr10p"])
    has_hdr10 = "HDR10" in raw and not has_hdr10p
    has_generic_hdr = any(token in raw for token in ("HLG", "PQ")) or (
        "HDR" in raw and not has_hdr10 and not has_hdr10p
    )

    tags: list[str] = []
    if has_dovi:
        tags.append("dovi")
    if has_hdr10p:
        tags.append("hdr10p")
    elif has_hdr10:
        tags.append("hdr10")
    elif has_generic_hdr:
        tags.append("hdr")
    if has_dovi and not any(tag in tags for tag in ("hdr", "hdr10", "hdr10p")):
        tags.append("dovi_no_fallback")
    return tags


def classify_hdr_flags(raw_value: str | None) -> tuple[bool | None, bool | None]:
    """Return legacy ``(has_hdr, has_dv)`` flags from raw Radarr truth."""
    raw = _normalized(raw_value)
    if not raw:
        return None, None
    if raw == "SDR":
        return False, False

    tags = classify_hdr_tags(raw)
    if not tags:
        return False, False
    has_hdr = any(tag in tags for tag in ("hdr", "hdr10", "hdr10p"))
    has_dv = "dovi" in tags
    return has_hdr, has_dv


def legacy_hdr_label(
    raw_value: str | None,
    has_hdr: bool | None,
    has_dv: bool | None,
) -> str | None:
    """Single badge label for legacy movie list/detail surfaces."""
    tags = classify_hdr_tags(raw_value)
    if "dovi" in tags:
        return "dovi"
    if "hdr10p" in tags:
        return "hdr10p"
    if "hdr10" in tags:
        return "hdr10"
    if "hdr" in tags:
        return "hdr"
    if raw_value and _normalized(raw_value) == "SDR":
        return "sdr"

    if has_dv:
        return "dovi"
    if has_hdr:
        return "hdr10"
    if has_hdr is False:
        return "sdr"
    return None


def legacy_hdr_tags(raw_value: str | None, has_hdr: bool | None, has_dv: bool | None) -> list[str]:
    """Fallback-friendly tag list for legacy movie surfaces."""
    tags = classify_hdr_tags(raw_value)
    if tags:
        return tags
    label = legacy_hdr_label(raw_value, has_hdr, has_dv)
    return [label] if label else []


def overlay_bucket(raw_value: str | None) -> str:
    """Primary HDR bucket for overlay filtering/distribution fallbacks."""
    raw = _normalized(raw_value)
    if not raw:
        return "unknown"
    tags = classify_hdr_tags(raw)
    if "dovi" in tags:
        return "dovi"
    if "hdr10p" in tags:
        return "hdr10p"
    if "hdr10" in tags:
        return "hdr10"
    if "hdr" in tags:
        return "hdr"
    return "sdr"


def distribution_keys(raw_value: str | None) -> list[str]:
    """Distribution buckets emitted for one movie."""
    raw = _normalized(raw_value)
    if not raw:
        return ["unknown"]
    tags = classify_hdr_tags(raw)
    return tags or ["sdr"]


def classify_custom_format_tags(name: str | None, specifications: list[dict] | None) -> set[str]:
    """Classify a Radarr custom format into one or more HDR target tags."""
    matched: set[str] = set()

    def scan_text(text: str | None) -> None:
        value = _normalized(text)
        if not value:
            return
        if any(token in value for token in HDR_KEYWORDS["dovi"]):
            matched.add("dovi")
        if any(token in value for token in HDR_KEYWORDS["hdr10p"]):
            matched.add("hdr10p")
        elif "HDR10" in value:
            matched.add("hdr10")
        elif any(token in value for token in ("HLG", "PQ")) or "HDR" in value:
            matched.add("hdr")

    scan_text(name)
    for spec in specifications or []:
        for field in spec.get("fields", []):
            if field.get("name") == "value":
                scan_text(str(field.get("value") or ""))
    return matched


def profile_hdr_targets(
    format_items: Iterable[object],
    cf_classifications: dict[int, set[str]],
) -> set[str]:
    """Return all HDR tags actively targeted by a quality profile."""
    targets: set[str] = set()
    for item in format_items:
        cf_id = getattr(item, "custom_format_id", None)
        score = getattr(item, "score", None)
        if cf_id is None or score is None or score <= 0:
            continue
        targets.update(cf_classifications.get(cf_id, set()) & set(HDR_TAG_ORDER))
    return targets


def movie_cf_score(rows: Iterable[object]) -> int:
    """Total custom-format score for one movie file."""
    return sum(int(getattr(row, "score", 0) or 0) for row in rows)


def preference_target_choices(profile_targets: Iterable[str]) -> list[str]:
    """Return user-facing preference targets. SDR is always available as the baseline."""
    targets = set(profile_targets)
    allowed: set[str] = {"sdr"}
    for tag in ("hdr", "hdr10", "hdr10p"):
        if tag in targets:
            allowed.add(tag)
    if "dovi" in targets:
        allowed.update({"dovi_no_fallback", "dovi_fallback"})
    return [choice for choice in PREFERENCE_TARGET_ORDER if choice in allowed]


def default_profile_preference(available_targets: Iterable[str]) -> tuple[str | None, str | None]:
    """Seed defaults for one profile when no saved preference row exists."""
    choices = list(available_targets)
    if not choices:
        return None, None

    # Default meet to the lowest-ranked target (typically "sdr")
    meet_target = choices[0]

    meet_rank = preference_rank(meet_target)
    stricter = [choice for choice in choices if preference_rank(choice) > meet_rank]
    if "dovi_fallback" in stricter:
        exceed_target = "dovi_fallback"
    else:
        exceed_target = stricter[0] if stricter else None
    return meet_target, exceed_target


def preference_rank(choice: str | None) -> int:
    """Relative strictness rank for preference validation and defaults."""
    if choice is None:
        return -1
    try:
        return PREFERENCE_TARGET_ORDER.index(choice)
    except ValueError:
        return -1


def is_valid_preference_pair(meet_target: str | None, exceed_target: str | None) -> bool:
    """Return whether a meet/exceed pair is internally consistent."""
    if meet_target is None or exceed_target is None:
        return True
    return preference_rank(exceed_target) > preference_rank(meet_target)


def preference_target_matches(choice: str, file_tags: set[str]) -> bool:
    """Return whether a file satisfies one preference target predicate."""
    if choice == "sdr":
        return not file_tags  # file has no HDR tags at all
    if choice == "hdr":
        return any(tag in file_tags for tag in ("hdr", "hdr10", "hdr10p"))
    if choice == "hdr10":
        return any(tag in file_tags for tag in ("hdr10", "hdr10p"))
    if choice == "hdr10p":
        return "hdr10p" in file_tags
    if choice == "dovi_no_fallback":
        return "dovi" in file_tags
    if choice == "dovi_fallback":
        return "dovi" in file_tags and "dovi_no_fallback" not in file_tags
    return False


def highest_matching_allowed_choice(file_tags: set[str], excluded_targets: set[str]) -> str | None:
    """Return the highest-ranked preference choice matching the file's HDR tags, ignoring excluded targets."""
    # Check in reverse order (highest rank first)
    for choice in reversed(PREFERENCE_TARGET_ORDER):
        if choice in excluded_targets:
            continue
        if preference_target_matches(choice, file_tags):
            return choice
    return None


def preference_status(
    *,
    file_tags: set[str],
    profile_targets: set[str],
    meet_target: str | None,
    exceed_target: str | None,
    excluded_targets: Iterable[str] | None = None,
) -> str:
    """Compare one file's HDR truth against the chosen preference targets."""
    if not profile_targets:
        return "no_hdr_target"
    if meet_target is None and exceed_target is None:
        return "meets_target"

    excluded = set(excluded_targets or [])
    effective_choice = highest_matching_allowed_choice(file_tags, excluded)
    if effective_choice is None:
        return "below_target"

    choice_rank = preference_rank(effective_choice)
    meet_rank = preference_rank(meet_target)
    exceed_rank = preference_rank(exceed_target)

    if exceed_rank >= 0 and choice_rank >= exceed_rank:
        return "exceeds_target"
    if meet_rank >= 0 and choice_rank >= meet_rank:
        return "meets_target"
    if meet_target is None and exceed_target is not None:
        # Exceeds-only mode: anything below exceed_target meets target
        return "meets_target"

    return "below_target"


def ordered_tags(tags: Iterable[str]) -> list[str]:
    """Stable display order for HDR target/tag chips."""
    tag_set = set(tags)
    return [tag for tag in DISPLAY_HDR_TAG_ORDER if tag in tag_set]
