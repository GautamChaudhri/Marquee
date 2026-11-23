#!/usr/bin/env python3
"""Dump the live OpenAPI schema to design/api-schema.json.

Run from the project root:
    python scripts/export_openapi.py

Commit the result — it is the reviewable, diffable API contract.
CI fails if the committed file diverges from what the app generates.
"""

import argparse
import difflib
import json
import os
import sys
from pathlib import Path

# Enable docs generation (openapi() is a no-op when docs_url=None).
os.environ["DEBUG"] = "true"
# Export the complete contract, including flows an operator may have gated off, so the
# schema (and the TypeScript client generated from it) does not depend on local .env.
os.environ["ONBOARDING_ENABLED"] = "true"
sys.path.insert(0, str(Path(__file__).parent.parent))

from marquee.main import app  # noqa: E402

ROOT = Path(__file__).parent.parent
OUTPUT = ROOT / "design" / "api-schema.json"


def render_schema() -> str:
    """Return the canonical byte representation of the live application contract."""
    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when the committed schema differs from the live application",
    )
    args = parser.parse_args()
    rendered = render_schema()
    if args.check:
        existing = OUTPUT.read_text() if OUTPUT.exists() else ""
        if existing != rendered:
            diff = difflib.unified_diff(
                existing.splitlines(),
                rendered.splitlines(),
                fromfile=str(OUTPUT),
                tofile="live OpenAPI",
                lineterm="",
            )
            print("\n".join(diff))
            return 1
        print(f"OpenAPI contract is current ({len(app.openapi()['paths'])} paths)")
        return 0
    OUTPUT.write_text(rendered)
    print(f"Written {OUTPUT} ({len(app.openapi()['paths'])} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
