"""Subgen generator adapter.

Two upstream gotchas matter enough to document here:

1. ``SKIP_STARTUP_SCAN=True`` on the Subgen side makes ``/batch`` a silent no-op.
2. Subgen's ``SKIP_IF_*`` environment toggles can accept a submission but skip
   the work without surfacing a hard error. Marquee therefore pre-checks for
   already-covered targets and keeps a basename glob fallback during reconcile.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from marquee.core.subtitles.config import subtitle_settings
from marquee.core.subtitles.generators.base import (
    GenerationRequest,
    GeneratorCapabilities,
    GeneratorHealth,
    ProviderState,
    ProviderSubmission,
)
from marquee.core.subtitles.languages import UNDETERMINED, normalize
from marquee.vendor.subgen.language_code import LanguageCode

logger = logging.getLogger(__name__)


def translate_local_to_remote(local_path: str) -> str:
    """Map a Marquee-local path into Subgen's namespace via configured prefixes."""
    local_prefix = subtitle_settings.SUBGEN_LOCAL_PATH_PREFIX
    remote_prefix = subtitle_settings.SUBGEN_REMOTE_PATH_PREFIX
    if local_prefix and remote_prefix and local_path.startswith(local_prefix):
        return remote_prefix + local_path[len(local_prefix) :]
    return local_path


def translate_remote_to_local(remote_path: str) -> str:
    """Inverse mapping for paths reported in Subgen's completion callback."""
    local_prefix = subtitle_settings.SUBGEN_LOCAL_PATH_PREFIX
    remote_prefix = subtitle_settings.SUBGEN_REMOTE_PATH_PREFIX
    if local_prefix and remote_prefix and remote_path.startswith(remote_prefix):
        return local_prefix + remote_path[len(remote_prefix) :]
    return remote_path


def _language_code_for_subgen(language_tag: str | None) -> LanguageCode:
    if not language_tag or language_tag == UNDETERMINED:
        return LanguageCode.NONE
    return LanguageCode.from_string(language_tag)


def _subtitle_language_token(language_tag: str | None) -> str:
    language = _language_code_for_subgen(language_tag)
    naming = subtitle_settings.SUBGEN_NAMING_TYPE
    if language is LanguageCode.NONE:
        return "und"
    if naming == "ISO_639_1":
        return language.to_iso_639_1() or "und"
    if naming == "ISO_639_2_T":
        return language.to_iso_639_2_t() or "und"
    if naming == "NAME":
        return language.to_name() or "und"
    if naming == "NATIVE":
        return language.to_name(in_english=False) or "und"
    return language.to_iso_639_2_b() or "und"


def expected_output_srt(local_media_path: str, language_tag: str | None, model_label: str) -> Path:
    """Predict Subgen's real output filename.

    Upstream naming is ``{base}[.subgen][.model].{lang}.srt``.
    """
    media = Path(local_media_path)
    suffixes = []
    if subtitle_settings.SUBGEN_NAME_INCLUDES_SUBGEN:
        suffixes.append("subgen")
    if subtitle_settings.SUBGEN_NAME_INCLUDES_MODEL and model_label:
        suffixes.append(model_label)
    suffixes.append(_subtitle_language_token(language_tag))
    return media.with_suffix("." + ".".join(suffixes) + ".srt")


def parse_status_version(version: str | None) -> dict[str, str | None]:
    if not version:
        return {
            "subgen_version": None,
            "stable_ts_version": None,
            "faster_whisper_version": None,
            "runtime": None,
        }
    text = version.strip()
    prefix, runtime = (text.rsplit("(", 1) + [""])[:2]
    runtime = runtime.rstrip(")") or None
    parts = [item.strip() for item in prefix.split(",")]
    parsed = {
        "subgen_version": None,
        "stable_ts_version": None,
        "faster_whisper_version": None,
        "runtime": runtime,
    }
    if parts:
        parsed["subgen_version"] = parts[0].removeprefix("Subgen ").strip() or None
    if len(parts) > 1:
        parsed["stable_ts_version"] = parts[1].removeprefix("stable-ts ").strip() or None
    if len(parts) > 2:
        parsed["faster_whisper_version"] = parts[2].removeprefix("faster-whisper ").strip() or None
    return parsed


def _health_dict(body: dict[str, Any]) -> dict[str, Any]:
    version = str(body.get("version") or body.get("status") or "ok")
    return {
        "version_string": version,
        **parse_status_version(version),
    }


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
        if not subtitle_settings.subgen_url:
            return GeneratorHealth(healthy=False, detail="Subgen is disabled")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{subtitle_settings.subgen_url.rstrip('/')}/status")
                resp.raise_for_status()
                body = (
                    resp.json()
                    if resp.headers.get("content-type", "").startswith("application/json")
                    else {}
                )
            parsed = _health_dict(body)
            return GeneratorHealth(
                healthy=True,
                version=parsed["version_string"],
                detail=json.dumps(parsed),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "SUBGEN | Connection health check failed to URL %s: %s",
                subtitle_settings.subgen_url,
                str(exc),
                exc_info=True,
            )
            return GeneratorHealth(healthy=False, detail=str(exc))

    async def submit(self, request: GenerationRequest) -> ProviderSubmission:
        if not subtitle_settings.subgen_url:
            return ProviderSubmission(accepted=False, submitted_at="", detail="Subgen is disabled")
        remote = translate_local_to_remote(request.local_media_path)
        params: dict[str, str] = {"directory": remote}
        if request.language_hint:
            params["forceLanguage"] = request.language_hint
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{subtitle_settings.subgen_url.rstrip('/')}/batch",
                    params=params,
                )
                resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "SUBGEN | Submission failed to URL %s for file %s: %s",
                subtitle_settings.subgen_url,
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

    async def submit_asr(
        self,
        request: GenerationRequest,
        *,
        audio_path: str,
        output_path: str,
    ) -> ProviderSubmission:
        if not subtitle_settings.subgen_url:
            return ProviderSubmission(accepted=False, submitted_at="", detail="Subgen is disabled")
        params = {
            "task": request.task,
            "output": "srt",
            "encode": "false",
        }
        if request.language_hint:
            params["language"] = request.language_hint
        try:
            async with httpx.AsyncClient(timeout=subtitle_settings.SUBGEN_TIMEOUT_MINUTES * 60) as client:
                with Path(audio_path).open("rb") as handle:
                    resp = await client.post(
                        f"{subtitle_settings.subgen_url.rstrip('/')}/asr",
                        params=params,
                        files={"audio_file": (Path(audio_path).name, handle, "audio/wav")},
                    )
                resp.raise_for_status()
                Path(output_path).write_bytes(resp.content)
        except Exception as exc:  # noqa: BLE001
            logger.error("SUBGEN | /asr failed for %s: %s", request.local_media_path, exc, exc_info=True)
            return ProviderSubmission(accepted=False, submitted_at="", detail=str(exc))
        return ProviderSubmission(
            accepted=True,
            submitted_at=datetime.now(UTC).isoformat(),
            expected_output_dir=str(Path(output_path).parent),
        )

    async def detect_language(self, sample_path: str) -> dict[str, Any]:
        if not subtitle_settings.subgen_url:
            raise RuntimeError("Subgen is disabled")
        async with httpx.AsyncClient(timeout=30.0) as client:
            with Path(sample_path).open("rb") as handle:
                resp = await client.post(
                    f"{subtitle_settings.subgen_url.rstrip('/')}/detect-language",
                    files={"audio_file": (Path(sample_path).name, handle, "audio/mpeg")},
                )
            resp.raise_for_status()
            return resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}

    async def reconcile(self, request: GenerationRequest) -> ProviderState:
        """Filesystem reconciliation — prefer the exact predicted output, then glob."""
        expected = expected_output_srt(
            request.local_media_path,
            request.language_hint,
            subtitle_settings.SUBGEN_WHISPER_MODEL or subtitle_settings.SUBGEN_MODEL_LABEL,
        )
        if expected.is_file():
            return ProviderState(state="produced", output_path=str(expected))
        media = Path(request.local_media_path)
        for candidate in sorted(media.parent.glob(f"{media.stem}*.srt")):
            if candidate.is_file():
                logger.info(
                    "SUBGEN | Exact output prediction missed for %s; falling back to %s",
                    request.local_media_path,
                    candidate.name,
                )
                return ProviderState(state="produced", output_path=str(candidate))
        return ProviderState(state="pending")


def build_asr_sidecar_path(local_media_path: str, language_hint: str | None, *, forced: bool = False) -> Path:
    media = Path(local_media_path)
    tag, _ = normalize(language_hint)
    suffix = f".{tag if tag != UNDETERMINED else 'und'}"
    if forced:
        suffix += ".forced"
    return media.with_suffix(f"{suffix}.srt")


def temp_audio_wav_path(local_media_path: str) -> Path:
    temp_dir = subtitle_settings.SUBTITLE_MUTATION_TEMP_DIR or tempfile.gettempdir()
    return Path(temp_dir) / f"{Path(local_media_path).stem}.{os.getpid()}.subgen.wav"
