"""Subgen path-based generator adapter (design §24).

Subgen is an external, separately-deployed service. Marquee translates the
validated local media path into Subgen's namespace, calls ``POST /batch`` for
that file, and reconciles completion via the expected output file (the
completion webhook is an optimization, not the sole source of truth — §24.4).

Path translation (local↔remote) is pure and unit-tested; the HTTP calls require
a running Subgen and are exercised on the box.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import httpx

from marquee.core.subtitles.config import subtitle_settings
from marquee.core.subtitles.generators.base import (
    GenerationRequest,
    GeneratorCapabilities,
    GeneratorHealth,
    ProviderState,
    ProviderSubmission,
)

logger = logging.getLogger(__name__)


def translate_local_to_remote(local_path: str) -> str:
    """Map a Marquee-local path into Subgen's namespace via configured prefixes."""
    local_prefix = subtitle_settings.SUBGEN_LOCAL_PATH_PREFIX
    remote_prefix = subtitle_settings.SUBGEN_REMOTE_PATH_PREFIX
    if local_prefix and remote_prefix and local_path.startswith(local_prefix):
        return remote_prefix + local_path[len(local_prefix):]
    return local_path


def translate_remote_to_local(remote_path: str) -> str:
    """Inverse mapping for paths reported in Subgen's completion callback."""
    local_prefix = subtitle_settings.SUBGEN_LOCAL_PATH_PREFIX
    remote_prefix = subtitle_settings.SUBGEN_REMOTE_PATH_PREFIX
    if local_prefix and remote_prefix and remote_path.startswith(remote_prefix):
        return local_prefix + remote_path[len(remote_prefix):]
    return remote_path


def expected_output_srt(local_media_path: str, language_tag: str | None) -> Path:
    """Where Subgen is expected to drop the generated SRT (next to the media)."""
    media = Path(local_media_path)
    if language_tag and language_tag != "und":
        return media.with_suffix(f".{language_tag}.srt")
    return media.with_suffix(".srt")


class SubgenPathGenerator:
    id = "subgen-default"

    def capabilities(self) -> GeneratorCapabilities:
        return GeneratorCapabilities(
            id=self.id,
            name=subtitle_settings.SUBGEN_PROFILE_NAME,
            provider="subgen",
            mode=subtitle_settings.SUBGEN_MODE,
            model_label=subtitle_settings.SUBGEN_MODEL_LABEL,
            supports_language_hint=True,
            supports_per_request_model=False,
            supports_percent_progress=False,
            transport="shared_path",
        )

    async def health(self) -> GeneratorHealth:
        if not subtitle_settings.SUBGEN_URL:
            return GeneratorHealth(healthy=False, detail="SUBGEN_URL not configured")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{subtitle_settings.SUBGEN_URL.rstrip('/')}/status")
                resp.raise_for_status()
                body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            return GeneratorHealth(healthy=True, version=str(body.get("version") or body.get("status") or "ok"))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "SUBGEN | Connection health check failed to URL %s: %s",
                subtitle_settings.SUBGEN_URL,
                str(exc),
                exc_info=True,
            )
            return GeneratorHealth(healthy=False, detail=str(exc))

    async def submit(self, request: GenerationRequest) -> ProviderSubmission:
        if not subtitle_settings.SUBGEN_URL:
            return ProviderSubmission(accepted=False, submitted_at="", detail="SUBGEN_URL not configured")
        remote = translate_local_to_remote(request.local_media_path)
        payload = {"path": remote}
        if request.language_hint:
            payload["forceLanguage"] = request.language_hint
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{subtitle_settings.SUBGEN_URL.rstrip('/')}/batch", json=payload
                )
                resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "SUBGEN | Submission failed to URL %s for file %s: %s",
                subtitle_settings.SUBGEN_URL,
                request.local_media_path,
                str(exc),
                exc_info=True,
            )
            return ProviderSubmission(accepted=False, submitted_at="", detail=str(exc))
        return ProviderSubmission(
            accepted=True,
            submitted_at=datetime.now(UTC).isoformat(),
            expected_output_dir=str(Path(request.local_media_path).parent),
        )

    async def reconcile(self, request: GenerationRequest) -> ProviderState:
        """Filesystem reconciliation — has the expected SRT appeared yet?"""
        expected = expected_output_srt(request.local_media_path, request.language_hint)
        if expected.is_file():
            return ProviderState(state="produced", output_path=str(expected))
        # Also accept any new .srt sharing the media basename.
        media = Path(request.local_media_path)
        for candidate in media.parent.glob(f"{media.stem}*.srt"):
            if candidate.is_file():
                return ProviderState(state="produced", output_path=str(candidate))
        return ProviderState(state="pending")
