"""Build the bundled taste test from curated source posters (design 20).

Reads ``taste_test/source/<Movie Title (Year)>/*.jpg|png``, computes the
scorer's normalized features for each poster against the current taste profile,
copies the images into ``taste_test/images/``, and writes ``manifest.json`` —
the bundle the onboarding taste test consumes.

Run on a host with the CLIP/DINO models exported and a taste profile present
(it needs the real feature extractor)::

    python -m marquee.onboarding.build_taste_test

Optionally drop a ``source/genres.json`` mapping ``"<Movie Title (Year)>"`` →
list-of-genres for nicer UI metadata.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

from marquee.core.pipeline_config import pipeline_settings

_YEAR = re.compile(r"\((\d{4})\)")
_IMAGE_GLOBS = ("*.jpg", "*.jpeg", "*.png", "*.webp")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=pipeline_settings.ONBOARDING_TASTE_TEST_DIR)
    args = parser.parse_args()

    base: Path = args.dir
    source = base / "source"
    images = base / "images"
    if not source.is_dir():
        raise SystemExit(
            f"[ABORT] no source dir at {source} — drop curated posters into "
            "source/<Movie Title (Year)>/ first"
        )

    # Heavy ML imports are lazy so importing this module stays cheap.
    from marquee.pipeline.retro_features import (  # noqa: PLC0415
        candidate_from_image,
        compute_full_features,
    )
    from marquee.pipeline.run_manager import run_manager  # noqa: PLC0415

    genres_map: dict[str, list[str]] = {}
    genres_file = source / "genres.json"
    if genres_file.exists():
        genres_map = json.loads(genres_file.read_text())

    extractor = run_manager._ensure_extractor()
    images.mkdir(parents=True, exist_ok=True)

    movies: list[dict] = []
    for movie_dir in sorted(p for p in source.iterdir() if p.is_dir()):
        label = movie_dir.name
        match = _YEAR.search(label)
        year = int(match.group(1)) if match else None
        title = _YEAR.sub("", label).strip()

        poster_files: list[Path] = []
        for pattern in _IMAGE_GLOBS:
            poster_files.extend(sorted(movie_dir.glob(pattern)))

        posters: list[dict] = []
        for image_path in poster_files:
            candidate = candidate_from_image(image_path)
            features = compute_full_features(
                image_path=image_path,
                candidate=candidate,
                movie_title=title,
                extractor=extractor,
            )
            dest_name = f"{_slug(label)}_{image_path.name}"
            shutil.copy2(image_path, images / dest_name)
            posters.append({"file": dest_name, "normalized_features": features.normalized})

        if posters:
            movies.append(
                {
                    "id": f"tt_{_slug(label)}",
                    "title": title,
                    "year": year,
                    "genres": genres_map.get(label, []),
                    "posters": posters,
                }
            )
            print(f"[INFO] {label}: {len(posters)} posters")

    manifest = {"model_name": pipeline_settings.AI_MODEL, "movies": movies}
    (base / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"[INFO] wrote {len(movies)} movies → {base / 'manifest.json'}")


if __name__ == "__main__":
    main()
