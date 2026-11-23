"""Canonical integration URL validation and origin comparison.

Arr endpoints may intentionally live on private or loopback networks, so the
policy blocks ambiguous URL spellings and infrastructure-only address classes
without applying a blanket public-network requirement.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit


class IntegrationURLValidationError(ValueError):
    """Raised when an integration URL is unsafe or ambiguous."""


@dataclass(frozen=True, slots=True)
class IntegrationOrigin:
    scheme: str
    host: str
    port: int


_NUMERIC_HOST = re.compile(r"(?:[0-9]+|0[xX][0-9a-fA-F]+)(?:\.(?:[0-9]+|0[xX][0-9a-fA-F]+))*")
_PROHIBITED_HOSTS = {
    "instance-data",
    "metadata.aws.internal",
    "metadata.azure.internal",
    "metadata.google.internal",
}


def _canonical_host(
    parsed: SplitResult,
) -> tuple[str, ipaddress.IPv4Address | ipaddress.IPv6Address | None]:
    raw_host = parsed.hostname
    if not raw_host:
        raise IntegrationURLValidationError("Service URL must include a host.")
    try:
        host = raw_host.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as exc:
        raise IntegrationURLValidationError("Service URL contains an invalid host name.") from exc
    if not host or len(host) > 253 or any(len(label) > 63 for label in host.split(".")):
        raise IntegrationURLValidationError("Service URL contains an invalid host name.")
    if "%" in host:
        raise IntegrationURLValidationError("Service URL cannot contain an IPv6 zone identifier.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
        try:
            socket.inet_aton(host)
        except OSError:
            legacy_numeric = bool(_NUMERIC_HOST.fullmatch(host))
        else:
            legacy_numeric = True
        if legacy_numeric:
            raise IntegrationURLValidationError(
                "Service URL uses a non-canonical numeric address."
            ) from None
    if host in _PROHIBITED_HOSTS or host.endswith(".metadata.google.internal"):
        raise IntegrationURLValidationError("Service URL targets a prohibited host.")
    if (
        address is not None
        and not address.is_loopback
        and (
            address.is_link_local
            or address.is_multicast
            or address.is_unspecified
            or address.is_reserved
        )
    ):
        raise IntegrationURLValidationError("Service URL targets a prohibited address.")
    return host, address


def normalize_integration_url(value: str) -> str:
    """Return a canonical HTTP(S) service URL or fail closed."""

    candidate = value.strip()
    if not candidate or any(ord(char) < 0x20 for char in candidate):
        raise IntegrationURLValidationError("Service URL contains invalid characters.")
    if "\\" in candidate:
        raise IntegrationURLValidationError("Service URL cannot contain backslashes.")
    parsed = urlsplit(candidate)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.netloc:
        raise IntegrationURLValidationError("Service URL must be an absolute HTTP(S) URL.")
    if parsed.username is not None or parsed.password is not None:
        raise IntegrationURLValidationError("Service URL cannot contain credentials.")
    if parsed.query or parsed.fragment:
        raise IntegrationURLValidationError(
            "Service URL cannot contain a query string or fragment."
        )
    host, address = _canonical_host(parsed)
    try:
        explicit_port = parsed.port
    except ValueError as exc:
        raise IntegrationURLValidationError("Service URL contains an invalid port.") from exc
    default_port = 443 if scheme == "https" else 80
    port = explicit_port or default_port
    if not 1 <= port <= 65535:
        raise IntegrationURLValidationError("Service URL contains an invalid port.")

    display_host = f"[{host}]" if address is not None and address.version == 6 else host
    netloc = display_host if port == default_port else f"{display_host}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))


def integration_origin(value: str) -> IntegrationOrigin:
    """Return the canonical authority used to scope a stored credential."""

    normalized = normalize_integration_url(value)
    parsed = urlsplit(normalized)
    scheme = parsed.scheme
    return IntegrationOrigin(
        scheme=scheme,
        host=parsed.hostname or "",
        port=parsed.port or (443 if scheme == "https" else 80),
    )


def same_integration_origin(left: str, right: str) -> bool:
    return integration_origin(left) == integration_origin(right)
