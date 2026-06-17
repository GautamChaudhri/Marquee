# Monitoring the Gauntlet Integration Test

## Overview

The gauntlet runner (`gauntlet-output/gauntlet_runner.py`) exercises Marquee's full public API surface against a running server and the lab movie set. It records results, errors, DB deltas, and a final summary into an output directory.

## Output Directory Structure

Each run gets a dedicated directory (e.g. `gauntlet-output-v3/`) containing:

| File | Purpose |
|---|---|
| `gauntlet_results.jsonl` | Every API call — one JSON object per line (seq, method, url, status, ok, expected, elapsed_s, label, body, error) |
| `gauntlet_summary.json` | **Final report** (generated only at the end) — passes/sections, pipeline run IDs, DB before/after, unexpected errors |
| `gauntlet_errors.jsonl` | Lines from results where `ok=false` |
| `endpoint_inventory.json` | Snapshot of the OpenAPI spec at start |
| `db_baseline.json` | DB table row counts at start |
| `db_delta_report.json` | DB rows created by the test |
| `gpu_snapshots.jsonl` | nvidia-smi readings during GPU-heavy phases |

## Checking Progress (Running Test)

### 1. Quick health — is the runner still alive?

```bash
process(action="poll", session_id="<proc_id>")
```

Returns `status: "running"` or `"exited"`.

### 2. How many calls completed?

```bash
cd /forge/Marquee/gauntlet-output-v3
wc -l gauntlet_results.jsonl
```

Compare against the v2 baseline of **507 total calls** (representative scope). The runner logs one line per API call.

### 3. How many errors so far?

```bash
cd /forge/Marquee/gauntlet-output-v3
wc -l gauntlet_errors.jsonl
```

Read the errors:
```bash
python3 -c "
import json
with open('gauntlet_errors.jsonl') as f:
    for line in f:
        e = json.loads(line)
        print(f\"{e['seq']} | {e['method']} {e['label']} | HTTP {e['status']} (expected {e['expected']}) | {e.get('body','')[:120]}\")
"
```

### 4. Pass/fail breakdown by section

```bash
cd /forge/Marquee/gauntlet-output-v3
python3 -c "
import json
from collections import Counter
sections = {}
with open('gauntlet_results.jsonl') as f:
    for line in f:
        r = json.loads(line)
        label = r.get('label','?').rsplit('-',1)[0]
        if label not in sections:
            sections[label] = {'total':0,'ok':0,'errors':0}
        sections[label]['total'] += 1
        if r.get('ok'):
            sections[label]['ok'] += 1
        else:
            sections[label]['errors'] += 1
for sec, d in sorted(sections.items()):
    bar = 'PASS' if d['ok'] == d['total'] else 'FAIL' if d['errors'] > 0 else '?'
    print(f'{bar:4s} {sec:<35s} {d[\"ok\"]}/{d[\"total\"]}')
print(f'\nTotal: {sum(d[\"total\"] for d in sections.values())}')
"
```

### 5. What's the current stdout output?

```bash
process(action="log", session_id="<proc_id>")
```

Shows the last 200 lines of the runner's stdout — OK/EXP lines for each API call.

### 6. Pipeline status check

If pipeline tests are running, look for labels matching `pipeline-<movie_id>-status-N`. The runner polls each run start → terminal/completed status, then fetches results, poster, and rescore.

Check the results file for recent pipeline entries:
```bash
grep pipeline-31-status gauntlet_results.jsonl | tail -3 | python3 -c "
import json,sys
for line in sys.stdin:
    r = json.loads(line)
    print(f\"seq={r['seq']} status={r['status']} elapsed_ms={r['elapsed_s']*1000:.0f}\")
"
```

## Expected Run Sequence (Representative Scope)

Based on v2 output (507 total calls, ~89 min total):

| Phase | Calls | Time | Notes |
|---|---|---|---|
| **Baseline** | 5 | ~0.1s | DB snapshot, endpoint inventory |
| **Library** | 44 | ~0.1s | Movies, series, episodes, validation errors |
| **Sync / Webhooks** | 5 | ~0.1s | Sync all, Radarr/Sonarr payloads |
| **Poster Pipeline** | 214 | ~82 min | 6 movies × (start + poll-until-done + results + poster + rescore) |
| **Feedback** | 12 | ~1.5s | Upvote, flag, undo |
| **Taste** | 68 | ~5 min | Status, retrain, map, candidates (GPU-heavy) |
| **Subtitle Inventory** | 50 | ~3.3s | Scan, list, preview, download |
| **Subtitle Plans/Jobs** | 32 | ~0.1s | Plans CRUD, policy CRUD, job lifecycle |
| **Subtitle Generation** | 9 | ~15s | Generate for movie and media file |
| **Letterbox** | 57 | ~2 min | Detect, status, candidates, apply |
| **System / Config** | 11 | ~0.1s | Heal, release-GPU, config get/put |

Long pole: **Poster Pipeline**. Each movie takes ~8-14 min depending on GPU load and whether poster downloads are needed.

## Parsing the Final Summary

`gauntlet_summary.json` top-level keys:

| Key | Type | Meaning |
|---|---|---|
| `total_calls` | int | Total API calls made |
| `successful_or_expected` | int | Calls where `ok=true` (all passes including expected 4xx) |
| `unexpected_errors` | int | Calls where `ok=false` (need investigation) |
| `expected_non_2xx` | int | Deliberately validated 4xx/5xx responses |
| `passes` | dict | Per-phase breakdown: calls, unexpected_errors, elapsed_s |
| `completed_pipeline_runs` | list[str] | Run IDs that finished |
| `failed_pipeline_runs` | list[str] | Run IDs that errored |
| `db_before` / `db_after` | dict | Row counts per table before and after the test |
| `config` | dict | Pipeline scope, timeouts, feature flags |

## Interpreting Errors

An error in `gauntlet_errors.jsonl` is **not necessarily a regression** — the runner tests boundary conditions. Check:

1. **Label prefix** — does this section usually have some expected failures? (e.g. `movies-invalid-*`, `series-invalid-*`)
2. **`expected` field** — if `expected` matches `status`, the error was planned.
3. **Body** — contains the server's error detail.
4. **`unexpected_errors` in summary** — this is the true regression indicator. v2 had 7 unexpected errors (mostly Subgen-related — no callback token configured).

## Common False Positives

| Error | Cause | Mitigation |
|---|---|---|
| `POST /api/webhooks/subgen → 401` | No SUBGEN_CALLBACK_TOKEN configured | Add token to .env, or accept as known missing feature |
| `POST /api/feedback → 422` | Test sends intentionally invalid feedback | Expected validation error |
| Pipeline status polling 404 | Run completed/deleted between poll cycles | Rare race — runner marks as warning |

## Process Management Commands

```bash
# Check if still running
process("poll", session_id="<id>")

# See recent stdout
process("log", session_id="<id>")

# Wait for completion (blocking)
process("wait", session_id="<id>", timeout=3600)

# Kill if stuck
process("kill", session_id="<id>")
```
