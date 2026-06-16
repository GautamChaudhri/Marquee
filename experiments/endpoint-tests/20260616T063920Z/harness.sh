#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Test harness — sourced by the test script
# ---------------------------------------------------------------------------
set -euo pipefail

export BASE_URL="http://192.168.4.199:3165"
export TEST_RUN_ID="20260616T063920Z"
export TEST_ROOT="/forge/Marquee/experiments/endpoint-tests/${TEST_RUN_ID}"

request_json() {
  local name="$1" method="$2" url="$3" body="${4:-}"
  local slug
  slug="$(printf '%s' "$name" | tr -cs 'A-Za-z0-9._-' '_' | sed 's/_$//')"
  local h="$TEST_ROOT/headers/${slug}.headers"
  local out="$TEST_ROOT/responses/${slug}.json"
  local meta="$TEST_ROOT/responses/${slug}.meta.json"

  printf '{"ts":"%s","name":"%s","method":"%s","url":"%s","body":%s}\n' \
    "$(date -u +%FT%TZ)" "$name" "$method" "$url" "${body:-null}" >> "$TEST_ROOT/requests.jsonl"

  if [ -n "$body" ]; then
    curl -sS --max-time 600 -D "$h" -o "$out" \
      -w '{"http_code":%{http_code},"time_total":%{time_total},"size_download":%{size_download}}\n' \
      -X "$method" "$BASE_URL$url" -H 'Content-Type: application/json' --data "$body" > "$meta"
  else
    curl -sS --max-time 600 -D "$h" -o "$out" \
      -w '{"http_code":%{http_code},"time_total":%{time_total},"size_download":%{size_download}}\n' \
      -X "$method" "$BASE_URL$url" > "$meta"
  fi

  jq -c --arg name "$name" --arg headers "$h" --arg body_path "$out" \
    '. + {name:$name, headers:$headers, body_path:$body_path}' "$meta" >> "$TEST_ROOT/responses.jsonl"

  # Return the body (stdout) for direct use in the caller
  cat "$out"
}

request_binary() {
  local name="$1" method="$2" url="$3" body="${4:-}"
  local slug
  slug="$(printf '%s' "$name" | tr -cs 'A-Za-z0-9._-' '_' | sed 's/_$//')"
  local h="$TEST_ROOT/headers/${slug}.headers"
  local out="$TEST_ROOT/binaries/${slug}.bin"
  local meta="$TEST_ROOT/responses/${slug}.meta.json"

  printf '{"ts":"%s","name":"%s","method":"%s","url":"%s","body":%s}\n' \
    "$(date -u +%FT%TZ)" "$name" "$method" "$url" "${body:-null}" >> "$TEST_ROOT/requests.jsonl"

  if [ -n "$body" ]; then
    curl -sS --max-time 600 -D "$h" -o "$out" \
      -w '{"http_code":%{http_code},"time_total":%{time_total},"size_download":%{size_download}}\n' \
      -X "$method" "$BASE_URL$url" -H 'Content-Type: application/json' --data "$body" > "$meta"
  else
    curl -sS --max-time 600 -D "$h" -o "$out" \
      -w '{"http_code":%{http_code},"time_total":%{time_total},"size_download":%{size_download}}\n' \
      -X "$method" "$BASE_URL$url" > "$meta"
  fi

  jq -c --arg name "$name" --arg headers "$h" --arg body_path "$out" \
    '. + {name:$name, headers:$headers, body_path:$body_path}' "$meta" >> "$TEST_ROOT/responses.jsonl"
  
  # Verify: not empty, type check
  local sz=0
  [ -f "$out" ] && sz=$(stat -c%s "$out")
  local ftype=""
  [ -f "$out" ] && ftype=$(file -b "$out" 2>/dev/null | head -c50)

  printf '  binary: size=%d  type=%s\n' "$sz" "$ftype" >&2
}

request_sse() {
  local name="$1" url="$2" timeout="${3:-300}"
  local slug
  slug="$(printf '%s' "$name" | tr -cs 'A-Za-z0-9._-' '_' | sed 's/_$//')"
  local sse_log="$TEST_ROOT/sse/${slug}.log"
  printf '{"ts":"%s","name":"%s","method":"GET","url":"%s"}\n' \
    "$(date -u +%FT%TZ)" "$name" "$url" >> "$TEST_ROOT/requests.jsonl"
  curl -sS -N --max-time "$timeout" "$BASE_URL$url" > "$sse_log" || true
  printf '  sse: %d lines written\n' "$(wc -l < "$sse_log")" >&2
}

log_pass() {
  local test_name="$1" detail="${2:-}"
  printf '  ✓ PASS | %s | %s\n' "$test_name" "$detail"
}

log_fail() {
  local test_name="$1" detail="${2:-}"
  printf '  ✗ FAIL | %s | %s\n' "$test_name" "$detail"
}

log_skip() {
  local test_name="$1" reason="${2:-}"
  printf '  — SKIP | %s | %s\n' "$test_name" "$reason"
}
