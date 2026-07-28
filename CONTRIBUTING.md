# Contributing to Marquee

Marquee is a pre-1.0 personal project under active development. Bug reports, design feedback, and
focused pull requests are welcome, but the public API and database schema may still change between
minor releases.

## Development workflow

1. Open or reference an issue before starting a large behavioral change.
2. Create a short-lived branch from the latest `main`.
3. Open a draft pull request early so the design and progress stay visible.
4. Keep commits focused and use short, imperative summaries.
5. Update tests, the OpenAPI contract, and design documentation with the implementation.

The poster-selection product is the current scope. HDR/Dolby Vision management, letterbox
processing, and audio/subtitle mutation were deliberately retired; proposals to restore them need a
separate design discussion.

## Local setup

Use Python 3.12 or 3.13, PostgreSQL 16 or newer, and Node.js 22 or newer. Follow the root
[`README.md`](README.md#getting-started) for installation and configuration.

The backend suite provisions an isolated schema inside the database named by `DB_URL`. Some backup
and migration tests also create disposable databases, so the test role needs `CREATEDB`.

## Quality gates

Run the relevant focused tests while iterating, then run the complete checks before requesting
review:

```bash
ruff check marquee tests scripts
ruff format --check marquee tests scripts
pytest -q --cov=marquee --cov-fail-under=70
python scripts/export_openapi.py --check
alembic check

cd frontend
npm ci
npm run check
npm run lint
npm run test:unit
npm run build
npm run bundle:check
npm run test:e2e
```

Model weights, a GPU, and the heavy ML extras are not required for the automated suites. Tests that
exercise real models or a live library must remain explicitly opt-in.

When a backend route changes, regenerate both committed contracts:

```bash
python scripts/export_openapi.py
cd frontend
npm run api:generate
```

## Releases

Release tags are cut only from a clean, CI-green `main`. Marquee follows semantic `vMAJOR.MINOR.PATCH`
tags while it is pre-1.0: patch releases are compatible fixes, and minor releases are feature
checkpoints that may still include breaking changes. Update `marquee/__init__.py` (the Python
package's version source) and the private frontend package version together for a product release.

Create an annotated tag, publish a GitHub Release with release notes, and mark it as a prerelease
until Marquee has a stable supported contract. Never move or reuse a published version tag; ship a
new patch version for corrections.

## Pull requests

Describe the user-visible effect, the design tradeoffs, and every verification command you ran.
Include screenshots for UI changes and representative API output for contract changes. Never commit
credentials, `.env` files, databases, media paths, generated model weights, or operator data.
