# Marquee

AI-powered automatic poster finder for Plex and Jellyfin media servers.

**Status: Pre-alpha — Phase 0 (project scaffold)**

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn marquee.main:app --reload
```

Open http://localhost:3165/health

## Design

See `design/` for the project specification and migration notes.
