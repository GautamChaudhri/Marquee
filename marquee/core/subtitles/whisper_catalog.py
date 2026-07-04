"""Curated Whisper model catalog + hardware-aware recommendation helpers.

The verdicts here are intentionally approximate and operator-facing: we label
fit based on published or parameter-scaled footprints so the UI can steer users
toward sane defaults before the first model download.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

GB = 1024**3


@dataclass(frozen=True)
class WhisperModelSpec:
    id: str
    params: str
    vram_fp16_gb: float | None
    vram_int8_gb: float | None
    multilingual: bool | None
    can_translate: bool | None
    notes: str
    cpu_ram_int8_gb: float | None = None


@dataclass(frozen=True)
class HardwareGpu:
    index: int
    name: str
    vram_total: int
    vram_free: int


@dataclass(frozen=True)
class HardwareSnapshot:
    gpus: list[HardwareGpu]
    cpu_count: int
    ram_total: int


CATALOG: tuple[WhisperModelSpec, ...] = (
    WhisperModelSpec("tiny", "39M", 0.5, 0.3, True, True, "last resort"),
    WhisperModelSpec("base", "74M", 0.7, 0.4, True, True, "weak CPUs"),
    WhisperModelSpec(
        "small",
        "244M",
        1.2,
        0.8,
        True,
        True,
        "CPU default",
        cpu_ram_int8_gb=1.5,
    ),
    WhisperModelSpec("medium", "769M", 2.6, 1.6, True, True, "legacy mid-size"),
    WhisperModelSpec(
        "distil-large-v3",
        "756M",
        1.9,
        1.2,
        False,
        False,
        "fastest for English libraries",
    ),
    WhisperModelSpec(
        "large-v3-turbo",
        "809M",
        1.9,
        1.3,
        True,
        False,
        "best speed/accuracy; default GPU pick",
    ),
    WhisperModelSpec("large-v3", "1550M", 4.7, 3.0, True, True, "max accuracy"),
    WhisperModelSpec("custom", "-", None, None, None, None, "free-text HF CT2 repo id or local path"),
)


def catalog_dicts() -> list[dict]:
    return [asdict(spec) for spec in CATALOG]


def _gpu_budget_bytes(gpu: HardwareGpu | None) -> int:
    if gpu is None:
        return 0
    return max(0, gpu.vram_total - int(1.5 * GB))


def _cpu_too_big(spec: WhisperModelSpec, hw: HardwareSnapshot) -> bool:
    if spec.id == "custom" or spec.vram_int8_gb is None:
        return False
    footprint = int((spec.cpu_ram_int8_gb or spec.vram_int8_gb) * GB)
    return footprint > hw.ram_total * 0.5


def per_model_verdicts(
    hw: HardwareSnapshot, mode: str, *, english_only_preferred: bool = False
) -> list[dict]:
    gpu = max(hw.gpus, key=lambda item: item.vram_total, default=None)
    budget = _gpu_budget_bytes(gpu)
    verdicts: list[dict] = []
    for spec in CATALOG:
        verdict = "custom" if spec.id == "custom" else "fits_int8"
        fits_fp16 = None if spec.vram_fp16_gb is None else spec.vram_fp16_gb * GB <= budget
        fits_int8 = None if spec.vram_int8_gb is None else spec.vram_int8_gb * GB <= budget
        if gpu is None:
            if spec.id != "custom":
                if mode == "translate" and spec.can_translate is False:
                    verdict = "unavailable_translate"
                elif spec.id == "large-v3":
                    verdict = "impractical_cpu"
                elif _cpu_too_big(spec, hw):
                    verdict = "too_big"
                else:
                    verdict = "fits_int8"
        else:
            if spec.id != "custom":
                if mode == "translate" and spec.can_translate is False:
                    verdict = "unavailable_translate"
                elif fits_fp16:
                    verdict = "fits_fp16"
                elif fits_int8:
                    verdict = "fits_int8"
                else:
                    verdict = "too_big"
        verdicts.append(
            {
                **asdict(spec),
                "fits_fp16": fits_fp16,
                "fits_int8": fits_int8,
                "verdict": verdict,
                "recommended_for_english_only": bool(
                    english_only_preferred and spec.id == "distil-large-v3"
                ),
            }
        )
    return verdicts


def recommend(
    hw: HardwareSnapshot, mode: str, *, english_only_preferred: bool = False
) -> dict:
    gpu = max(hw.gpus, key=lambda item: item.vram_total, default=None)
    verdicts = per_model_verdicts(hw, mode, english_only_preferred=english_only_preferred)
    by_id = {item["id"]: item for item in verdicts}
    if gpu is None:
        model = "base" if hw.cpu_count < 4 else "small"
        if by_id[model]["verdict"] in {"too_big", "impractical_cpu"}:
            model = "tiny"
        return {
            "device": "cpu",
            "gpu_index": None,
            "compute_type": "int8",
            "model_id": model,
            "reason": "No CUDA GPU detected; CPU mode uses int8 and prefers small/base footprints.",
            "device_note": "No CUDA GPU detected; integrated GPUs are not usable by CTranslate2.",
        }

    if mode == "translate":
        if by_id["large-v3"]["verdict"] in {"fits_fp16", "fits_int8"}:
            model = "large-v3"
        elif by_id["medium"]["verdict"] in {"fits_fp16", "fits_int8"}:
            model = "medium"
        else:
            model = "small"
    elif english_only_preferred and by_id["distil-large-v3"]["verdict"] in {
        "fits_fp16",
        "fits_int8",
    }:
        model = "distil-large-v3"
    elif by_id["large-v3-turbo"]["verdict"] in {"fits_fp16", "fits_int8"}:
        model = "large-v3-turbo"
    elif by_id["distil-large-v3"]["verdict"] in {"fits_fp16", "fits_int8"}:
        model = "distil-large-v3"
    else:
        model = "small"

    verdict = by_id[model]
    compute_type = "float16" if verdict["verdict"] == "fits_fp16" else "int8"
    return {
        "device": "cuda",
        "gpu_index": gpu.index,
        "compute_type": compute_type,
        "model_id": model,
        "reason": (
            f"Selected {model} on GPU {gpu.index} ({gpu.name}) "
            f"within ~{round(_gpu_budget_bytes(gpu) / GB, 1)} GB usable VRAM."
        ),
        "device_note": None,
    }
