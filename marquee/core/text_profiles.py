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

VALID_MODES = ("title_only", "textless", "custom")
MAX_NAME_LENGTH = 64
MAX_RESIDUAL_BOXES_CAP = 20


@dataclass
class TextProfileSettings:
    mode: str = "title_only"  # title_only | textless | custom
    allow_title: bool = True
    allow_director: bool = False
    allow_studio: bool = False
    allow_rating: bool = False
    allow_tagline: bool = False
    allow_billing: bool = False
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

        Built-ins pin only the mode: the residual/require-title knobs stay
        governed by the raw ``pipeline_settings`` so the advanced settings
        page keeps working when a preset is active. Custom profiles are fully
        explicit — every knob comes from the profile.
        """
        if self.builtin:
            return {"mode": self.settings.mode}
        return asdict(self.settings)


def _builtin_profiles() -> dict[str, TextProfile]:
    """Fresh built-in instances — always code defaults, never persisted."""
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


BUILTIN_PROFILE_IDS = frozenset(_builtin_profiles())
FALLBACK_PROFILE_ID = "title_only"


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
        max_residual_boxes=boxes,
        max_residual_area_fraction=area,
        require_title=bool(data.get("require_title", defaults.require_title)),
    )


def _read_raw() -> dict:
    if not _PROFILES_PATH.exists():
        return {}
    try:
        data = json.loads(_PROFILES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        logger.warning("Unreadable text profiles file %s — using built-ins", _PROFILES_PATH)
        return {}


def _write_raw(data: dict) -> None:
    _PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PROFILES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, _PROFILES_PATH)


def _load_unlocked() -> tuple[dict[str, TextProfile], str]:
    """All profiles (built-ins + customs) and the default id."""
    raw = _read_raw()
    profiles = _builtin_profiles()
    for entry in raw.get("profiles", []):
        if not isinstance(entry, dict):
            continue
        profile_id = str(entry.get("id", ""))
        if not profile_id or profile_id in BUILTIN_PROFILE_IDS:
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
    default_id = str(raw.get("_default_profile", FALLBACK_PROFILE_ID))
    if default_id not in profiles:
        default_id = FALLBACK_PROFILE_ID
    for profile in profiles.values():
        profile.is_default = profile.id == default_id
    return profiles, default_id


def _save_unlocked(profiles: dict[str, TextProfile], default_id: str) -> None:
    """Persist custom profiles + default pointer (built-ins stay in code)."""
    _write_raw(
        {
            "_default_profile": default_id,
            "profiles": [
                {"id": p.id, "name": p.name, "settings": asdict(p.settings)}
                for p in profiles.values()
                if not p.builtin
            ],
        }
    )


def load_profiles() -> dict[str, TextProfile]:
    with _lock:
        profiles, _ = _load_unlocked()
        return profiles


def get_default_profile_id() -> str:
    with _lock:
        _, default_id = _load_unlocked()
        return default_id


def get_active_profile(override_id: str | None = None) -> TextProfile:
    """Effective profile: override → global default → title_only."""
    with _lock:
        profiles, default_id = _load_unlocked()
        if override_id and override_id in profiles:
            return replace(profiles[override_id])
        return replace(profiles.get(default_id) or profiles[FALLBACK_PROFILE_ID])


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


def create_profile(name: str, settings: dict) -> TextProfile:
    with _lock:
        profiles, default_id = _load_unlocked()
        name = _validate_name(name, profiles)
        profile_id = _slugify(name)
        if profile_id in profiles:
            raise TextProfileError(f"A profile with id {profile_id!r} already exists")
        profile = TextProfile(id=profile_id, name=name, settings=settings_from_dict(settings))
        profiles[profile_id] = profile
        _save_unlocked(profiles, default_id)
        return profile


def update_profile(profile_id: str, *, name: str | None = None, settings: dict | None = None):
    with _lock:
        profiles, default_id = _load_unlocked()
        profile = profiles.get(profile_id)
        if profile is None:
            raise TextProfileError(f"Unknown text profile {profile_id!r}")
        if profile.builtin:
            raise TextProfileError("Built-in profiles cannot be modified")
        if name is not None:
            profile.name = _validate_name(name, profiles, skip_id=profile_id)
        if settings is not None:
            profile.settings = settings_from_dict(settings)
        _save_unlocked(profiles, default_id)
        return profile


def delete_profile(profile_id: str) -> None:
    with _lock:
        profiles, default_id = _load_unlocked()
        profile = profiles.get(profile_id)
        if profile is None:
            raise TextProfileError(f"Unknown text profile {profile_id!r}")
        if profile.builtin:
            raise TextProfileError("Built-in profiles cannot be deleted")
        del profiles[profile_id]
        if default_id == profile_id:
            default_id = FALLBACK_PROFILE_ID
        _save_unlocked(profiles, default_id)


def set_default_profile(profile_id: str) -> str:
    with _lock:
        profiles, _ = _load_unlocked()
        if profile_id not in profiles:
            raise TextProfileError(f"Unknown text profile {profile_id!r}")
        _save_unlocked(profiles, profile_id)
        return profile_id


@dataclass
class OcrGateContext:
    """Per-movie inputs for the OCR text gate, resolved once per run.

    Carries the TMDB-enriched metadata (director/studios/tagline) and the
    effective text profile so both pipeline engines construct the
    ``PosterTextFilter`` identically.
    """

    director: str | None = None
    studios: list[str] | None = None
    tagline: str | None = None
    profile: TextProfile | None = None

    @classmethod
    def from_movie(cls, movie) -> OcrGateContext:
        """Snapshot the gate inputs from a Movie row (detached-safe scalars).

        Uses getattr so it works before the enrichment/override columns exist
        in a deployment that hasn't migrated yet.
        """
        return cls(
            director=getattr(movie, "director", None),
            studios=getattr(movie, "production_companies_json", None),
            tagline=getattr(movie, "tagline", None),
            profile=get_active_profile(getattr(movie, "text_profile_id", None)),
        )

    @classmethod
    def default(cls) -> OcrGateContext:
        """Global-default profile, no movie metadata (direct/test paths)."""
        return cls(profile=get_active_profile(None))
