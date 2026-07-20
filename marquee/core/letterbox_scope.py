"""Pure validation shared by canonical TV letterbox submissions."""

ALLOWED_CONFIDENCE_LEVELS = frozenset({"high", "medium", "low", "variable", "all"})
DEFAULT_CONFIDENCE_LEVELS = ("high",)


def normalize_confidence_levels(levels: list[str] | None) -> list[str] | None:
    values = list(DEFAULT_CONFIDENCE_LEVELS if levels is None else levels)
    invalid = [value for value in values if value not in ALLOWED_CONFIDENCE_LEVELS]
    if invalid:
        raise ValueError(f"Invalid confidence level: {invalid[0]}")
    return None if "all" in values else values
