"""Build the shipped starter taste profile (design 20).

A tiny generic profile so a fresh install can rank before onboarding completes.
Built by running the normal taste-profile builder over the bundled taste-test
poster images (a generic "good posters" set). Run on a host with the CLIP/DINO
models exported::

    python -m marquee.onboarding.build_seed_profile
"""

from __future__ import annotations

import argparse
from pathlib import Path

from marquee.core.pipeline_config import pipeline_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--images",
        type=Path,
        default=pipeline_settings.ONBOARDING_TASTE_TEST_DIR / "images",
        help="positive poster images to seed the starter profile from",
    )
    parser.add_argument(
        "--output", type=Path, default=pipeline_settings.ONBOARDING_SEED_PROFILE_PATH
    )
    args = parser.parse_args()

    if not args.images.is_dir():
        raise SystemExit(
            f"[ABORT] no images at {args.images} — build the taste test first "
            "(python -m marquee.onboarding.build_taste_test)"
        )

    from marquee.ml.taste_trainer import rebuild_profile  # noqa: PLC0415

    args.output.parent.mkdir(parents=True, exist_ok=True)
    path = rebuild_profile(training_dir=args.images, output=args.output)
    print(f"[INFO] starter profile written → {path}")


if __name__ == "__main__":
    main()
