"""The `marquee` command: one entry point for everything an operator runs."""

from __future__ import annotations

import argparse
import logging
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
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
