from __future__ import annotations

import pytest

from marquee.core.integration_urls import (
    IntegrationURLValidationError,
    normalize_integration_url,
    same_integration_origin,
)


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("HTTP://RADARR:80/", "http://radarr"),
        ("https://sonarr:443/base/", "https://sonarr/base"),
        ("http://127.0.0.1:7878/", "http://127.0.0.1:7878"),
        ("http://[::1]:8989/sonarr/", "http://[::1]:8989/sonarr"),
    ],
)
def test_normalize_integration_url_is_canonical(candidate: str, expected: str):
    assert normalize_integration_url(candidate) == expected


@pytest.mark.parametrize(
    "candidate",
    [
        "file:///etc/passwd",
        "http://user:password@radarr:7878",
        "http://radarr:7878/?apikey=secret",
        "http://radarr:7878/#fragment",
        "http://metadata.google.internal/computeMetadata/v1",
        "http://169.254.169.254/latest/meta-data",
        "http://2130706433:7878",
        "http://0177.0.0.1:7878",
        "http://0x7f.0.0.1:7878",
        "http://127.1:7878",
        "http://[::ffff:169.254.169.254]/",
        "http://radarr\\@example.test/",
    ],
)
def test_normalize_integration_url_rejects_ambiguous_or_prohibited_targets(candidate: str):
    with pytest.raises(IntegrationURLValidationError):
        normalize_integration_url(candidate)


def test_stored_credentials_are_scoped_to_canonical_origin():
    assert same_integration_origin("http://radarr:7878", "http://RADARR:7878/api")
    assert not same_integration_origin("http://radarr:7878", "http://sonarr:7878")
    assert not same_integration_origin("http://radarr:7878", "https://radarr:7878")
    assert not same_integration_origin("http://radarr", "http://radarr:7878")
