"""Sort-title utility: strip leading articles so *The Dark Knight* sorts
under D and *A Beautiful Mind* under B — matching Plex/Jellyfin/IMDb convention.

``sort_title(title)`` is the pure-Python version for in-process sorting.
``title_sort_expr()`` returns a SQLAlchemy ``case`` expression suitable for
``order_by(…)`` clauses (works on both SQLite and PostgreSQL).

To add more articles, extend ``SORT_TITLE_ARTICLES``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.sql.elements import ColumnElement

# Leading articles stripped for sorting purposes.  Ordered longest-first so
# multi-word articles ("Los") match before their prefix ("Lo").
SORT_TITLE_ARTICLES: frozenset[str] = frozenset(
    {
        "The",
        "A",
        "An",  # English
        "Le",
        "La",
        "Les",  # French
        "El",
        "Los",
        "Las",  # Spanish
        "Der",
        "Die",
        "Das",  # German
        "Il",
        "Lo",
        "Gli",  # Italian
        "O",
        "Os",
        "As",  # Portuguese
    }
)

# Pre-sorted by length descending so longer articles match first.
_ARTICLES_SORTED: tuple[str, ...] = tuple(sorted(SORT_TITLE_ARTICLES, key=len, reverse=True))

# Pattern: optional article + space at the start, case-insensitive.
_ARTICLE_RE = re.compile(
    r"^(?:" + "|".join(re.escape(a) for a in _ARTICLES_SORTED) + r") ",
    re.IGNORECASE,
)


def sort_title(title: str) -> str:
    """Return a sort-friendly form of *title* with the leading article moved to
    the end: ``"The Dark Knight"`` → ``"Dark Knight, The"``."""
    m = _ARTICLE_RE.match(title)
    if m is None:
        return title
    article = m.group(0).strip()
    rest = title[m.end() :]
    return f"{rest}, {article}"


def title_sort_expr(title_column=None):
    """Return a SQLAlchemy column expression suitable for ``order_by()``.

    If *title_column* is not provided, defaults to ``Movie.title``.

    Usage::

        from marquee.core.sort_title import title_sort_expr
        query = query.order_by(title_sort_expr())
        # or with Series.title:
        query = query.order_by(title_sort_expr(Series.title))
    """
    if title_column is None:
        title_column = _movie_title()
    return _build_expr(title_column)


def _build_expr(title_column):
    """Build a SQL ``CASE`` expression that strips leading articles."""
    from sqlalchemy import case as sa_case, func

    clauses: list = []
    for article in _ARTICLES_SORTED:
        prefix = f"{article} "
        clauses.append(
            (
                func.lower(title_column).like(func.lower(f"{prefix}%")),
                func.substr(title_column, len(prefix) + 1),
            )
        )
    return sa_case(*clauses, else_=title_column)


def _movie_title():
    """Return the ``Movie.title`` column (lazy import to avoid circular deps)."""
    from marquee.models.movie import Movie

    return Movie.title
