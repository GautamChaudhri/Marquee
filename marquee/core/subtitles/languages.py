"""Language-tag normalization (design §20.4, §17.1).

Normalize whatever a container or filename gives us (``eng``, ``en``, ``fre``,
``pt-BR``, ``Brazilian Portuguese``, junk) into a canonical BCP-47 tag plus a
human display name. Undetermined input collapses to ``und``. Pure + cheap so it
can be unit-tested exhaustively.
"""

from __future__ import annotations

import logging

import langcodes

logger = logging.getLogger(__name__)

UNDETERMINED = "und"

# Tags that explicitly mean "no/unknown language" — keep as und.
_UND_LITERALS = {"", "und", "unknown", "unk", "mis", "zxx", "mul"}


def normalize(raw: str | None) -> tuple[str, str]:
    """Return ``(bcp47_tag, display_name)`` for a raw language string.

    Never raises. Unrecognized input yields ``("und", "Undetermined")``.
    """
    if not raw:
        return UNDETERMINED, "Undetermined"
    token = raw.strip()
    if token.lower() in _UND_LITERALS:
        return UNDETERMINED, "Undetermined"

    # First: treat it as a tag/subtag (eng, en, pt-BR, ...).
    try:
        lang = langcodes.Language.get(token)
        if lang.is_valid() and lang.language:
            tag = lang.to_tag()
            return tag, _display(lang)
    except (langcodes.LanguageTagError, ValueError):
        pass

    # Second: treat it as a human name ("Brazilian Portuguese", "Japanese").
    try:
        lang = langcodes.find(token)
        if lang.language:
            return lang.to_tag(), _display(lang)
    except LookupError:
        pass

    logger.debug("Could not normalize language %r → und", raw)
    return UNDETERMINED, "Undetermined"


def _display(lang: langcodes.Language) -> str:
    try:
        return lang.display_name()
    except Exception:  # noqa: BLE001 — display data may be unavailable
        return lang.to_tag()


def display_name(tag: str | None) -> str:
    """Human display name for an already-normalized tag."""
    if not tag or tag == UNDETERMINED:
        return "Undetermined"
    return normalize(tag)[1]


def same_language(a: str | None, b: str | None) -> bool:
    """True when two tags denote the same language (ignoring region/script)."""
    na, _ = normalize(a)
    nb, _ = normalize(b)
    if na == UNDETERMINED or nb == UNDETERMINED:
        return na == nb
    return langcodes.Language.get(na).language == langcodes.Language.get(nb).language
