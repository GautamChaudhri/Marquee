"""The `marquee` command: one entry point for everything an operator runs."""

from __future__ import annotations

import argparse
import logging
import sys
import warnings
from collections.abc import Sequence

from marquee import __version__

MODEL_ITEMS = ("clip", "dino", "zeroshot", "yolo", "ocr")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="marquee", description="Poster artwork for Radarr and Sonarr."
    )
    parser.add_argument("--version", action="version", version=f"marquee {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    doctor = commands.add_parser("doctor", help="check that this machine can run Marquee")
    doctor.add_argument("--json", action="store_true", help="print the report as JSON")

    models = commands.add_parser("models", help="build and check the model files")
    model_commands = models.add_subparsers(dest="models_command", required=True, metavar="command")
    fetch = model_commands.add_parser(
        "fetch",
        help="build every missing model and write the checksum manifest",
        description="Build every model that is missing, leave alone what is present, and "
        "write data/models/manifest.json. clip, dino, and zeroshot need the export tools.",
    )
    fetch.add_argument(
        "--force",
        action="append",
        default=[],
        choices=MODEL_ITEMS,
        metavar="ITEM",
        help=f"rebuild this item even if present (repeatable; one of {', '.join(MODEL_ITEMS)})",
    )

    taste = commands.add_parser("taste", help="build the taste profile")
    taste_commands = taste.add_subparsers(dest="taste_command", required=True, metavar="command")
    seed = taste_commands.add_parser(
        "seed",
        help="build the first taste profile from the library's poster.jpg files",
        description="Stage every movie folder's poster.jpg as a 500 px wide PNG exemplar at "
        "weight 1.0 and build the taste profile from them. Folders without a poster.jpg "
        "are ignored. Refuses to run again once a staged set exists unless told how.",
    )
    seed.add_argument(
        "--dry-run", action="store_true", help="scan and print the report; write nothing"
    )
    again = seed.add_mutually_exclusive_group()
    again.add_argument(
        "--rescan",
        action="store_true",
        help="scan the library again and replace the staged set and the profile",
    )
    again.add_argument(
        "--reuse",
        action="store_true",
        help="rebuild the profile from the staged set without reading the library",
    )
    return parser


def _quiet_logging(level: str = "ERROR") -> None:
    """Keep the console to the command's own output; only real problems reach stderr."""
    from marquee.logging import setup_logging

    setup_logging(level=level)
    logging.captureWarnings(True)
    warnings.simplefilter("ignore")


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "doctor":
        from marquee import doctor

        # The checks load ONNX Runtime and PaddleOCR, which are chatty.
        _quiet_logging()
        return doctor.main(as_json=args.json)
    if args.command == "models" and args.models_command == "fetch":
        from marquee import model_fetch

        _quiet_logging()
        return model_fetch.fetch(force=args.force)
    if args.command == "taste" and args.taste_command == "seed":
        from marquee.library.folders import LibraryRootError
        from marquee.taste import seed

        _quiet_logging()
        mode: seed.Mode = "rescan" if args.rescan else "reuse" if args.reuse else "first"
        try:
            return seed.run_seed(mode=mode, dry_run=args.dry_run)
        except (seed.SeedError, LibraryRootError) as exc:
            print(f"marquee taste seed: {exc}", file=sys.stderr)
            return 1
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
