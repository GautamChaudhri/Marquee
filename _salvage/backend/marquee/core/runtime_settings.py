"""Typed effective application settings shared by API, workers, and runners.

The bootstrap ``Settings`` singleton remains the source for deployment-only
values. Revision-managed values are resolved through the configuration
provider, while restart-bound values are pinned when a process initializes so
that a saved revision cannot silently change a running service's behaviour.
"""

from __future__ import annotations

from typing import Any, cast

from pydantic import ValidationError

from marquee.config import Settings
from marquee.config import settings as bootstrap_settings
from marquee.core.configuration import CONFIGURATION_CATALOG
from marquee.core.configuration_cache import configuration_provider
from marquee.core.managed_secrets import managed_secret_provider

_restart_values: dict[str, Any] | None = None


def initialize_effective_settings() -> None:
    """Pin restart-bound values for the lifetime of the current process."""
    global _restart_values

    values = (
        configuration_provider.effective("app")
        if configuration_provider.initialized
        else bootstrap_settings.model_dump()
    )
    _restart_values = {
        key: values[key]
        for key, entry in CONFIGURATION_CATALOG.items()
        if entry.owner == "app" and entry.apply_mode == "restart" and key in values
    }


def effective_app_values() -> dict[str, Any]:
    """Return the process-effective application values as a plain mapping."""
    values = (
        configuration_provider.effective("app")
        if configuration_provider.initialized
        else bootstrap_settings.model_dump()
    )
    if configuration_provider.initialized and _restart_values is None:
        initialize_effective_settings()
    # Production pins restart-bound values for the process lifetime. DEBUG is
    # deliberately mutable so local hot reloads and the test suite's scoped
    # monkeypatches can replace filesystem roots and resource ceilings safely.
    if _restart_values is not None and not bootstrap_settings.DEBUG:
        values.update(_restart_values)
    if managed_secret_provider.started:
        values.update(managed_secret_provider.values())
    return values


def effective_app_settings() -> Settings:
    """Build the canonical typed view without mutating the environment singleton."""
    try:
        return Settings(**effective_app_values())
    except ValidationError:
        if bootstrap_settings.DEBUG:
            return bootstrap_settings
        raise


class _EffectiveSettingsProxy:
    """Attribute-compatible live view for legacy runtime consumers.

    Assignments intentionally target the bootstrap singleton. This preserves
    existing test monkeypatches while production code remains read-only by
    convention; public writes still go exclusively through the settings API.
    """

    def __getattr__(self, name: str) -> Any:
        if name in Settings.model_fields:
            return effective_app_values()[name]
        try:
            return getattr(effective_app_settings(), name)
        except ValidationError:
            if bootstrap_settings.DEBUG:
                return getattr(bootstrap_settings, name)
            raise

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(bootstrap_settings, name, value)

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return effective_app_settings().model_dump(*args, **kwargs)


effective_settings = cast(Settings, _EffectiveSettingsProxy())
