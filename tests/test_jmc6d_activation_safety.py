"""JMC6D D0 release-blocking activation-safety regressions."""

from marquee.api.auth import _EXEMPT_PATHS
from marquee.main import app


def test_deferred_webhooks_are_unmounted_and_not_auth_exempt() -> None:
    mounted_paths = {route.path for route in app.routes}
    openapi_paths = set(app.openapi()["paths"])

    assert not any(path.startswith("/api/webhooks") for path in mounted_paths)
    assert not any(path.startswith("/api/webhooks") for path in openapi_paths)
    assert "/api/webhooks/subgen" not in _EXEMPT_PATHS
