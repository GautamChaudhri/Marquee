#!/usr/bin/env bash
# Gauntlet runner wrapper — all env vars pre-configured for v3 output.
# Source: cd /forge/Marquee && bash gauntlet-output-v3/run.sh

set -euo pipefail

cd /forge/Marquee
source .venv/bin/activate

# Load base config from .env
set -a
source .env
set +a

# Gauntlet-specific overrides
export MARQUEE_API_KEY="${API_KEY}"   # gauntlet runner expects MARQUEE_API_KEY
export GAUNTLET_OUTPUT_DIR=/forge/Marquee/gauntlet-output-v3
export GAUNTLET_DB_PATH=/forge/Marquee/data/marquee.db
export MARQUEE_BASE_URL="${MARQUEE_BASE_URL:-http://localhost:3165}"
export SUBGEN_URL="${SUBGEN_URL:-http://localhost:9000}"
export GAUNTLET_PIPELINE_SCOPE="${GAUNTLET_PIPELINE_SCOPE:-representative}"
export GAUNTLET_PIPELINE_TIMEOUT_SECONDS="${GAUNTLET_PIPELINE_TIMEOUT_SECONDS:-1800}"
export GAUNTLET_SUBGEN_TIMEOUT_SECONDS="${GAUNTLET_SUBGEN_TIMEOUT_SECONDS:-1800}"
export GAUNTLET_CONFIRM_SUBTITLE_MUTATIONS="${GAUNTLET_CONFIRM_SUBTITLE_MUTATIONS:-0}"
export GAUNTLET_APPLY_LETTERBOX="${GAUNTLET_APPLY_LETTERBOX:-1}"

echo "=== Gauntlet v3 ==="
echo "Output dir:   $GAUNTLET_OUTPUT_DIR"
echo "Marquee URL:  $MARQUEE_BASE_URL"
echo "Subgen URL:   $SUBGEN_URL"
echo "API key:      ${MARQUEE_API_KEY:+configured}"
echo "Scope:        $GAUNTLET_PIPELINE_SCOPE"
echo ""

exec python3 gauntlet-output/gauntlet_runner.py
