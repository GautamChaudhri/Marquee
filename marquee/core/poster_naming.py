"""Bounded poster-template grammar shared by settings, sync, and serving."""

from __future__ import annotations

from string import Formatter
from typing import Literal

from marquee.core.path_utils import PathValidationError
from marquee.core.poster_files import sanitize_poster_filename

PosterKind = Literal["movie", "series", "season"]
_MAX_TEMPLATE_CHARS = 128
_FORMATTER = Formatter()


def normalize_poster_template(kind: PosterKind, value: str) -> str:
    """Validate a deliberately tiny template language and normalize JPEG suffixes."""

    template = (value or "").strip()
    if not template or len(template) > _MAX_TEMPLATE_CHARS:
        raise PathValidationError(
            f"Poster template must contain 1-{_MAX_TEMPLATE_CHARS} characters"
        )
    try:
        parts = list(_FORMATTER.parse(template))
    except ValueError as exc:
        raise PathValidationError("Poster template contains malformed braces") from exc
    fields = [(field, spec, conversion) for _, field, spec, conversion in parts if field]
    allowed_field = {"movie": "movie_basename", "season": "season"}.get(kind)
    if kind == "series" and fields:
        raise PathValidationError("Series poster template cannot contain placeholders")
    if kind == "movie":
        if len(fields) > 1 or any(field != allowed_field for field, _, _ in fields):
            raise PathValidationError(
                "Movie poster template allows at most one {movie_basename} placeholder"
            )
        if any(spec or conversion for _, spec, conversion in fields):
            raise PathValidationError("Movie poster placeholder cannot use a format or conversion")
    if kind == "season":
        if len(fields) != 1 or fields[0][0] != allowed_field:
            raise PathValidationError(
                "Season poster template requires exactly one {season} placeholder"
            )
        _, spec, conversion = fields[0]
        if spec not in {"", "02d"} or conversion:
            raise PathValidationError("Season placeholder format must be {season} or {season:02d}")

    sample = render_poster_filename(
        kind,
        template,
        movie_basename="Example Movie",
        season=1,
        normalize=False,
    )
    if sample.endswith(".jpg") and not template.lower().endswith((".jpg", ".jpeg")):
        template += ".jpg"
    return template


def render_poster_filename(
    kind: PosterKind,
    template: str,
    *,
    movie_basename: str | None = None,
    season: int | None = None,
    normalize: bool = True,
) -> str:
    """Render a template into one bounded bare JPEG basename."""

    normalized = normalize_poster_template(kind, template) if normalize else template
    try:
        if kind == "movie":
            rendered = normalized.format(movie_basename=movie_basename or "movie")
        elif kind == "season":
            if season is None or season < 0:
                raise PathValidationError("Season number must be a non-negative integer")
            rendered = normalized.format(season=season)
        else:
            rendered = normalized.format()
    except (IndexError, KeyError, ValueError) as exc:
        raise PathValidationError("Poster template could not be rendered") from exc
    return sanitize_poster_filename(rendered)
