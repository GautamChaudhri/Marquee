"""CLIP zero-shot style axes — free style scalars from the existing embedding.

Each axis is a unit direction in CLIP space built from two prompt
*ensembles* (e.g. "an illustrated movie poster" vs "a photographic movie
poster"). At runtime the axis score is one dot product against the image
embedding the pipeline has already computed — zero added inference.

The old design removed CLIP text prompts because they were unreliable as a
HARD GATE (a misfire deleted a poster). Here they are soft rank features
fed through exemplar calibration: the taste profile's distribution of each
axis tells the scorer which side — and how far along it — the user actually
prefers, so no manual "I like illustrated" config is ever needed.

Build the artifact once (needs torch + transformers, the export-only deps):

    python -m marquee.ml.zeroshot

At runtime only numpy is needed; a missing artifact disables the axes with
a logged warning instead of failing the run.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from marquee.core.pipeline_config import pipeline_settings
from marquee.ml.artifact_codec import (
    decode_unicode_list,
    decode_unicode_scalar,
    ensure_safe_artifact,
    load_npz_safe,
    save_npz_atomic,
    unicode_array,
    unicode_scalar,
)

logger = logging.getLogger(__name__)

_HF_MODEL_ID = "openai/clip-vit-base-patch32"

# Axis name -> (positive prompts, negative prompts). Ensembles of phrasings
# stabilize the direction against prompt-wording noise.
AXIS_PROMPTS: dict[str, tuple[list[str], list[str]]] = {
    "axis_illustrated": (
        [
            "an illustrated movie poster",
            "a hand-drawn movie poster",
            "a painted movie poster artwork",
            "a graphic design movie poster",
        ],
        [
            "a photographic movie poster",
            "a movie poster made from photographs of actors",
            "a photo collage movie poster",
        ],
    ),
    "axis_minimalist": (
        [
            "a minimalist movie poster",
            "a simple clean movie poster with lots of empty space",
            "a minimal flat design movie poster",
        ],
        [
            "a busy cluttered movie poster",
            "a movie poster crowded with many characters and details",
            "a complex detailed movie poster collage",
        ],
    ),
    "axis_vintage": (
        [
            "a vintage retro movie poster",
            "a classic old-fashioned movie poster from decades ago",
            "a retro styled movie poster with aged textures",
        ],
        [
            "a modern contemporary movie poster",
            "a sleek modern digital movie poster",
            "a current-era blockbuster movie poster",
        ],
    ),
}


class ZeroShotAxes:
    """Runtime evaluator: image embedding -> per-axis scalar via dot product."""

    def __init__(self, axis_names: list[str], directions: np.ndarray, model_name: str):
        self.axis_names = axis_names
        self.directions = directions.astype(np.float32)  # (A, 512), unit rows
        self.model_name = model_name

    @classmethod
    def load(
        cls,
        path: str | Path | None = None,
        *,
        expected_model_name: str | None = None,
    ) -> ZeroShotAxes | None:
        """Load the artifact; None (with a warning) if absent or mismatched."""
        artifact = Path(path or pipeline_settings.ZEROSHOT_AXES_PATH)
        expected = expected_model_name or pipeline_settings.AI_MODEL
        if not artifact.exists():
            logger.warning(
                "ZEROSHOT | axes artifact missing (%s) — style axes disabled. "
                "Build it once with: python -m marquee.ml.zeroshot",
                artifact,
            )
            return None
        ensure_safe_artifact(artifact, "zeroshot_axes")
        with load_npz_safe(artifact) as data:
            stored_model = decode_unicode_scalar(data["model_name"])
            # int8-quantized variants share the fp32 text tower's space
            # closely enough for soft rank features.
            if not expected.startswith(stored_model) and not stored_model.startswith(
                expected
            ):
                logger.warning(
                    "ZEROSHOT | axes artifact model %r does not match %r — "
                    "style axes disabled. Rebuild with: python -m marquee.ml.zeroshot",
                    stored_model,
                    expected,
                )
                return None
            names = decode_unicode_list(data["axis_names"])
            directions = np.asarray(data["directions"], dtype=np.float32)
        logger.info("ZEROSHOT | loaded %d style axes from %s", len(names), artifact.name)
        return cls(names, directions, stored_model)

    def scores(self, embedding: np.ndarray) -> dict[str, float]:
        """All axis scores for one L2-normalized image embedding."""
        values = self.directions @ np.asarray(embedding, dtype=np.float32).reshape(512)
        return {
            name: float(value)
            for name, value in zip(self.axis_names, values, strict=True)
        }


# ---------------------------------------------------------------------------
# One-time artifact build (torch + transformers required)
# ---------------------------------------------------------------------------


def build_axes(output: Path | None = None) -> Path:
    import torch
    from transformers import CLIPModel, CLIPProcessor

    output_path = Path(output or pipeline_settings.ZEROSHOT_AXES_PATH)
    print(f"[INFO] Loading {_HF_MODEL_ID} text tower ...")
    model = CLIPModel.from_pretrained(_HF_MODEL_ID).eval()
    processor = CLIPProcessor.from_pretrained(_HF_MODEL_ID)

    def embed_prompts(prompts: list[str]) -> np.ndarray:
        inputs = processor(text=prompts, return_tensors="pt", padding=True)
        with torch.no_grad():
            features = model.get_text_features(**inputs)
        vectors = features.numpy().astype(np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors.mean(axis=0)  # ensemble mean

    names: list[str] = []
    directions: list[np.ndarray] = []
    for name, (positive, negative) in AXIS_PROMPTS.items():
        direction = embed_prompts(positive) - embed_prompts(negative)
        direction /= np.linalg.norm(direction)
        names.append(name)
        directions.append(direction)
        print(f"[INFO] Built {name} from {len(positive)}+{len(negative)} prompts")

    save_npz_atomic(
        output_path,
        {
            "axis_names": unicode_array(names),
            "directions": np.stack(directions),
            "model_name": unicode_scalar("clip-vit-b-32"),
        },
    )
    print(f"[INFO] Saved {len(names)} axes to {output_path}")
    return output_path


if __name__ == "__main__":
    build_axes()
