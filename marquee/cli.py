"""The `marquee` command: one entry point for everything an operator runs."""

from __future__ import annotations

import argparse
import logging
import warnings
from collections.abc import Sequence

from marquee import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="marquee", description="Poster artwork for Radarr and Sonarr."
    )
    parser.add_argument("--version", action="version", version=f"marquee {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    doctor = commands.add_parser("doctor", help="check that this machine can run Marquee")
    doctor.add_argument("--json", action="store_true", help="print the report as JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "doctor":
        from marquee import doctor
        from marquee.logging import setup_logging

        # The checks load ONNX Runtime and PaddleOCR, which are chatty; keep the
        # report readable and leave only real warnings on stderr.
        setup_logging(level="ERROR")
        logging.captureWarnings(True)
        warnings.simplefilter("ignore")
        return doctor.main(as_json=args.json)
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
