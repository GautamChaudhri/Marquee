"""Named poster text profiles (design/plans/03 §Group B).

A text profile bundles the OCR text-gate knobs (mode, per-category allow
toggles, residual tolerances, title requirement) under a stable id so the UI
can switch behaviours with one click. Built-in presets (``title_only``,
``textless``) always exist and mirror the code defaults; custom profiles are
user-created and persisted in ``data/text_profiles.json`` alongside the
``_default_profile`` pointer. The pipeline resolves the effective profile per
movie: per-movie override → global default → ``title_only``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_PROFILES_PATH = _PROJECT_ROOT / "data" / "text_profiles.json"

_lock = threading.Lock()

SCOPES = ("movie", "show", "season")
VALID_MODES = ("title_only", "textless", "custom")
MAX_NAME_LENGTH = 64
MAX_RESIDUAL_BOXES_CAP = 20

FALLBACK_PROFILE_IDS = {
    "movie": "title_only",
    "show": "title_only",
    "season": "title_and_season",
}


@dataclass
class TextProfileSettings:
    mode: str = "title_only"  # title_only | textless | custom
    allow_title: bool = True
    allow_director: bool = False
    allow_studio: bool = False
    allow_rating: bool = False
    allow_tagline: bool = False
    allow_billing: bool = False
    allow_season: bool = False
    max_residual_boxes: int = 0
    max_residual_area_fraction: float = 0.04
    require_title: bool = True


@dataclass
class TextProfile:
    id: str
    name: str
    builtin: bool = False
    is_default: bool = False
    settings: TextProfileSettings = field(default_factory=TextProfileSettings)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "builtin": self.builtin,
            "is_default": self.is_default,
            "settings": asdict(self.settings),
        }

    def gate_payload(self) -> dict:
        """Settings dict injected into the OCR gate (pickled into workers).

        Built-ins whose mode is title_only/textless pin only the mode.
        The title_and_season built-in (mode custom) returns the full dict.
        """
        if self.builtin and self.settings.mode in ("title_only", "textless"):
            return {"mode": self.settings.mode}
        return asdict(self.settings)


def _builtin_profiles(scope: str) -> dict[str, TextProfile]:
    """Fresh built-in instances per scope — always code defaults, never persisted."""
    if scope == "movie" or scope == "show":
        return {
            "title_only": TextProfile(
                id="title_only",
                name="Title Only",
                builtin=True,
                settings=TextProfileSettings(mode="title_only"),
            ),
            "textless": TextProfile(
                id="textless",
                name="Textless",
                builtin=True,
                settings=TextProfileSettings(
                    mode="textless",
                    allow_title=False,
                    require_title=False,
                ),
            ),
        }
    elif scope == "season":
        return {
            "title_and_season": TextProfile(
                id="title_and_season",
                name="Title and Season",
                builtin=True,
                settings=TextProfileSettings(
                    mode="custom",
                    allow_title=True,
                    allow_season=True,
                    require_title=False,
                ),
            ),
            "title_only": TextProfile(
                id="title_only",
                name="Title Only",
                builtin=True,
                settings=TextProfileSettings(mode="title_only"),
            ),
            "textless": TextProfile(
                id="textless",
                name="Textless",
                builtin=True,
                settings=TextProfileSettings(
                    mode="textless",
                    allow_title=False,
                    require_title=False,
                ),
            ),
        }
    else:
        raise TextProfileError(f"Unknown scope: {scope!r}")


class TextProfileError(ValueError):
    """Invalid profile operation (bad name, unknown id, builtin mutation)."""


def settings_from_dict(data: dict) -> TextProfileSettings:
    """Build validated settings from a plain dict, rejecting bad values."""
    defaults = TextProfileSettings()
    mode = str(data.get("mode", defaults.mode))
    if mode not in VALID_MODES:
        raise TextProfileError(f"mode must be one of {VALID_MODES}, got {mode!r}")
    boxes = int(data.get("max_residual_boxes", defaults.max_residual_boxes))
    if not 0 <= boxes <= MAX_RESIDUAL_BOXES_CAP:
        raise TextProfileError(f"max_residual_boxes must be 0–{MAX_RESIDUAL_BOXES_CAP}")
    area = float(data.get("max_residual_area_fraction", defaults.max_residual_area_fraction))
    if not 0.0 <= area <= 1.0:
        raise TextProfileError("max_residual_area_fraction must be 0–1")
    return TextProfileSettings(
        mode=mode,
        allow_title=bool(data.get("allow_title", defaults.allow_title)),
        allow_director=bool(data.get("allow_director", defaults.allow_director)),
        allow_studio=bool(data.get("allow_studio", defaults.allow_studio)),
        allow_rating=bool(data.get("allow_rating", defaults.allow_rating)),
        allow_tagline=bool(data.get("allow_tagline", defaults.allow_tagline)),
        allow_billing=bool(data.get("allow_billing", defaults.allow_billing)),
        allow_season=bool(data.get("allow_season", defaults.allow_season)),
        max_residual_boxes=boxes,
        max_residual_area_fraction=area,
        require_title=bool(data.get("require_title", defaults.require_title)),
    )


def _read_raw() -> dict:
    if not _PROFILES_PATH.exists():
        return {}
    try:
        data = json.loads(_PROFILES_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        # If it's v2:
        if "scopes" in data:
            return data
        # Migrate v1 to v2:
        return {
            "version": 2,
            "scopes": {
                "movie": {
                    "_default_profile": data.get("_default_profile", "title_only"),
                    "profiles": data.get("profiles", []),
                },
                "show": {
                    "_default_profile": "title_only",
                    "profiles": [],
                },
                "season": {
                    "_default_profile": "title_and_season",
                    "profiles": [],
                },
            },
        }
    except (OSError, json.JSONDecodeError):
        logger.warning("Unreadable text profiles file %s — using built-ins", _PROFILES_PATH)
        return {}


def _write_raw(data: dict) -> None:
    _PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PROFILES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, _PROFILES_PATH)


def _load_unlocked(scope: str) -> tuple[dict[str, TextProfile], str]:
    """All profiles (built-ins + customs) and the default id for a scope."""
    if scope not in SCOPES:
        raise TextProfileError(f"Unknown scope: {scope!r}")
    raw = _read_raw()
    if "scopes" not in raw:
        raw = {
            "version": 2,
            "scopes": {
                "movie": {"_default_profile": "title_only", "profiles": []},
                "show": {"_default_profile": "title_only", "profiles": []},
                "season": {"_default_profile": "title_and_season", "profiles": []},
            },
        }
    scope_data = raw["scopes"].get(scope) or {}
    profiles = _builtin_profiles(scope)
    builtin_ids = frozenset(profiles)
    fallback_id = FALLBACK_PROFILE_IDS[scope]

    for entry in scope_data.get("profiles", []):
        if not isinstance(entry, dict):
            continue
        profile_id = str(entry.get("id", ""))
        if not profile_id or profile_id in builtin_ids:
            continue
        try:
            settings = settings_from_dict(entry.get("settings") or {})
        except TextProfileError:
            logger.warning("Skipping text profile %s with invalid settings", profile_id)
            continue
        profiles[profile_id] = TextProfile(
            id=profile_id,
            name=str(entry.get("name", profile_id))[:MAX_NAME_LENGTH],
            settings=settings,
        )
    default_id = str(scope_data.get("_default_profile", fallback_id))
    if default_id not in profiles:
        default_id = fallback_id
    for profile in profiles.values():
        profile.is_default = profile.id == default_id
    return profiles, default_id


def _save_unlocked(scope: str, profiles: dict[str, TextProfile], default_id: str) -> None:
    """Persist custom profiles + default pointer for a scope (built-ins stay in code)."""
    raw = _read_raw()
    if "scopes" not in raw:
        raw = {
            "version": 2,
            "scopes": {
                "movie": {"_default_profile": "title_only", "profiles": []},
                "show": {"_default_profile": "title_only", "profiles": []},
                "season": {"_default_profile": "title_and_season", "profiles": []},
            },
        }
    raw["scopes"][scope] = {
        "_default_profile": default_id,
        "profiles": [
            {"id": p.id, "name": p.name, "settings": asdict(p.settings)}
            for p in profiles.values()
            if not p.builtin
        ],
    }
    _write_raw(raw)


def load_profiles(scope: str = "movie") -> dict[str, TextProfile]:
    if scope not in SCOPES:
        raise TextProfileError(f"Unknown scope: {scope!r}")
    with _lock:
        profiles, _ = _load_unlocked(scope)
        return profiles


def get_default_profile_id(scope: str = "movie") -> str:
    if scope not in SCOPES:
        raise TextProfileError(f"Unknown scope: {scope!r}")
    with _lock:
        _, default_id = _load_unlocked(scope)
        return default_id


def get_active_profile(scope: str = "movie", override_id: str | None = None) -> TextProfile:
    if scope not in SCOPES:
        override_id = scope
        scope = "movie"
    with _lock:
        profiles, default_id = _load_unlocked(scope)
        if override_id and override_id in profiles:
            return replace(profiles[override_id])
        fallback_id = FALLBACK_PROFILE_IDS[scope]
        return replace(profiles.get(default_id) or profiles[fallback_id])


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not slug:
        raise TextProfileError("Profile name must contain letters or digits")
    return slug


def _validate_name(name: str, profiles: dict[str, TextProfile], *, skip_id: str | None = None):
    name = name.strip()
    if not name:
        raise TextProfileError("Profile name cannot be empty")
    if len(name) > MAX_NAME_LENGTH:
        raise TextProfileError(f"Profile name must be ≤ {MAX_NAME_LENGTH} characters")
    for profile in profiles.values():
        if profile.id != skip_id and profile.name.lower() == name.lower():
            raise TextProfileError(f"A profile named {name!r} already exists")
    return name


def create_profile(
    scope: str = "movie", name: str = "", settings: dict | None = None
) -> TextProfile:
    if scope not in SCOPES:
        settings = name
        name = scope
        scope = "movie"
    with _lock:
        profiles, default_id = _load_unlocked(scope)
        name = _validate_name(name, profiles)
        profile_id = _slugify(name)
        if profile_id in profiles:
            raise TextProfileError(f"A profile with id {profile_id!r} already exists")
        profile = TextProfile(id=profile_id, name=name, settings=settings_from_dict(settings or {}))
        profiles[profile_id] = profile
        _save_unlocked(scope, profiles, default_id)
        return profile


def update_profile(
    scope: str,
    profile_id: str | None = None,
    *,
    name: str | None = None,
    settings: dict | None = None,
) -> TextProfile:
    if scope not in SCOPES:
        profile_id = scope
        scope = "movie"
    with _lock:
        profiles, default_id = _load_unlocked(scope)
        profile = profiles.get(profile_id)
        if profile is None:
            raise TextProfileError(f"Unknown text profile {profile_id!r}")
        if profile.builtin:
            raise TextProfileError("Built-in profiles cannot be modified")
        if name is not None:
            profile.name = _validate_name(name, profiles, skip_id=profile_id)
        if settings is not None:
            profile.settings = settings_from_dict(settings)
        _save_unlocked(scope, profiles, default_id)
        return profile


def delete_profile(scope: str, profile_id: str | None = None) -> None:
    if scope not in SCOPES:
        profile_id = scope
        scope = "movie"
    with _lock:
        profiles, default_id = _load_unlocked(scope)
        profile = profiles.get(profile_id)
        if profile is None:
            raise TextProfileError(f"Unknown text profile {profile_id!r}")
        if profile.builtin:
            raise TextProfileError("Built-in profiles cannot be deleted")
        del profiles[profile_id]
        fallback_id = FALLBACK_PROFILE_IDS[scope]
        if default_id == profile_id:
            default_id = fallback_id
        _save_unlocked(scope, profiles, default_id)


def set_default_profile(scope: str, profile_id: str | None = None) -> str:
    if scope not in SCOPES:
        profile_id = scope
        scope = "movie"
    with _lock:
        profiles, _ = _load_unlocked(scope)
        if profile_id not in profiles:
            raise TextProfileError(f"Unknown text profile {profile_id!r}")
        _save_unlocked(scope, profiles, profile_id)
        return profile_id


@dataclass
class OcrGateContext:
    """Per-movie/series/season inputs for the OCR text gate, resolved once per run.

    Carries the TMDB-enriched metadata (director/studios/tagline) and the
    effective text profile so both pipeline engines construct the
    ``PosterTextFilter`` identically.
    """

    director: str | None = None
    studios: list[str] | None = None
    tagline: str | None = None
    profile: TextProfile | None = None
    scope: str = "movie"
    season_number: int | None = None

    @classmethod
    def from_movie(cls, movie) -> OcrGateContext:
        """Snapshot the gate inputs from a Movie row (detached-safe scalars)."""
        return cls(
            director=getattr(movie, "director", None),
            studios=getattr(movie, "production_companies_json", None),
            tagline=getattr(movie, "tagline", None),
            profile=get_active_profile("movie", getattr(movie, "text_profile_id", None)),
            scope="movie",
        )

    @classmethod
    def from_series(cls, series) -> OcrGateContext:
        """Snapshot the gate inputs from a Series row."""
        return cls(
            director=getattr(series, "director", None),
            studios=getattr(series, "production_companies_json", None),
            tagline=getattr(series, "tagline", None),
            profile=get_active_profile("show", getattr(series, "show_text_profile_id", None)),
            scope="show",
        )

    @classmethod
    def from_season(cls, season, series) -> OcrGateContext:
        """Snapshot the gate inputs from a Season row, joining Series metadata."""
        return cls(
            director=getattr(series, "director", None),
            studios=getattr(series, "production_companies_json", None),
            tagline=getattr(series, "tagline", None),
            profile=get_active_profile("season", getattr(series, "season_text_profile_id", None)),
            scope="season",
            season_number=season.season_number,
        )

    @classmethod
    def default(cls) -> OcrGateContext:
        """Global-default profile, no movie metadata (direct/test paths)."""
        return cls(profile=get_active_profile("movie", None), scope="movie")
