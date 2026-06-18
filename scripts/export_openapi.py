#!/usr/bin/env python3
"""Dump the live OpenAPI schema to design/api-schema.json.

Run from the project root:
    python scripts/export_openapi.py

Commit the result — it is the reviewable, diffable API contract.
CI fails if the committed file diverges from what the app generates.
"""

import json
import os
import sys
from pathlib import Path

# Enable docs generation (openapi() is a no-op when docs_url=None).
os.environ.setdefault("DEBUG", "true")
sys.path.insert(0, str(Path(__file__).parent.parent))

from marquee.main import app  # noqa: E402

schema = app.openapi()
out = Path(__file__).parent.parent / "design" / "api-schema.json"
out.write_text(json.dumps(schema, indent=2) + "\n")
print(f"Written {out}  ({len(schema['paths'])} paths)")
