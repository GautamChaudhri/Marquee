# 02 — Implement server-side library list filters

**Type:** Code change
**Drift item:** #4 of `design/todos.md` → "API design drift follow-ups"
**Effort:** Moderate (query params + Python predicate filtering over coverage)

---

## Why

`design/more-features/03-subtitle-management.md` §25.1 (lines 1414–1416):

> Library list filters are server-side query parameters, not frontend scans:
> `language`, `missing_language`, `source`, `forced_only`, `generated`,
> `policy_violation`, `min_tracks`, and `inventory_state`.

`marquee/api/routes/library.py` `list_movies` ([library.py:45](marquee/api/routes/library.py)) exposes only `page`/`page_size`. Every movie already carries `subtitle_coverage` built by `_coverage_by_media_file` ([library.py:32](marquee/api/routes/library.py)) from `SubtitleInventory.coverage_json`. This task adds the 8 server-side filters so the frontend filters from persisted data without downloading the whole library.

---

## The coverage schema (authoritative — from `marquee/core/subtitles/coverage.py:compute_coverage`)

`coverage_json` is exactly these keys (do **not** invent others):

```python
{
  "audio_languages": [str],              # e.g. ["eng","kor"]
  "full_dialogue_languages": [str],      # non-forced, non-commentary subtitle langs
  "forced_only_languages": [str],        # langs that appear only as forced
  "sdh_languages": [str],
  "commentary_present": bool,
  "external_present": bool,              # has an external (.srt) track
  "embedded_present": bool,
  "generated_present": bool,             # has a machine-generated track
  "unknown_present": bool,
  "missing_preferred_languages": [str],  # preferred set minus full coverage
  "track_count": int,
}
```

A movie with **no inventory** has `subtitle_coverage = None` (no `SubtitleInventory` row for its active media file).

---

## Filter → predicate mapping

Add each as `Query(None)` on `GET /api/library/movies`. Apply only the filters that are provided (None = ignore).

| Param | Type | Predicate (over a movie's coverage dict `cov`) |
|---|---|---|
| `language` | str | `language in cov["full_dialogue_languages"]` |
| `missing_language` | str | `missing_language not in cov["full_dialogue_languages"]` (movie lacks full coverage in that lang) |
| `source` | enum `embedded\|external\|generated` | the matching `*_present` flag is True |
| `forced_only` | bool | `bool(cov["forced_only_languages"])` matches the requested boolean |
| `generated` | bool | `cov["generated_present"]` matches |
| `min_tracks` | int (≥0) | `cov["track_count"] >= min_tracks` |
| `inventory_state` | enum `scanned\|unscanned` | see note below — derived from inventory presence, **not** coverage |
| `policy_violation` | — | **NOT SUPPORTED** by current data — return `400` (see note) |

### Notes on the two special filters

- **`inventory_state`** is not in `coverage_json`. Derive it from whether the active media file has a `SubtitleInventory` row:
  - `scanned` → movie has an inventory (`subtitle_coverage is not None`).
  - `unscanned` → no inventory (`subtitle_coverage is None`).
  - Do **not** implement a `stale` value in this task (it requires comparing inventory timestamp vs file mtime, which means a filesystem stat per row). If `inventory_state=stale` is requested, return `400` with a message that `stale` is not yet supported. Document this in `03-doc-reconciliation.md`.
- **`policy_violation`** has no backing field — coverage carries no policy-evaluation result. Return `400 {"detail": "policy_violation filter is not supported: coverage data carries no policy evaluation"}`. Record this limitation in `03-doc-reconciliation.md` so the design doc footnotes it. (Do not silently ignore it — a silent no-op would make the frontend show wrong results.)

For any `None`-coverage movie, treat coverage-based predicates as **not matching** (a movie with no inventory cannot satisfy `language=eng`), except `inventory_state=unscanned` which specifically selects them, and `missing_language` which logically *does* match (it has no full coverage) — decide and document one consistent rule; recommended: `None` coverage matches only `inventory_state=unscanned` and `missing_language`, and is excluded by all positive-presence filters.

---

## Implementation approach

Because `coverage_json` is an opaque JSON blob, **filter in Python before pagination** (SQLite JSON1 over a serialized blob is brittle here, and the library is ~473 movies — trivial in memory):

1. Validate params: `source ∈ {embedded,external,generated}` else `422`; `inventory_state ∈ {scanned,unscanned}` else `400` (stale) / `422` (garbage); `min_tracks >= 0` else `422`; `policy_violation` present → `400`.
2. If **any** filter is provided, load the full candidate set (all movies, their active `MediaFile`, and the coverage map via the existing `_coverage_by_media_file` helper — extend it to cover all movies, not just one page). Reuse the existing active-media-file query shape from `list_movies`.
3. Build the per-movie predicate from the provided filters (AND across all provided filters). Apply it to produce `filtered`.
4. Paginate `filtered` with `limit=page_size`, `offset=(page-1)*page_size`. Set `total = len(filtered)`.
5. If **no** filter is provided, keep the current fast path (DB-side `LIMIT/OFFSET`, `total = count(Movie)`) — do not regress the unfiltered performance.

Keep the existing response shape (`{"total", "page", "page_size", "items": [...]}`) and per-item keys unchanged; `total` simply reflects the filtered count when filters are active.

**Series:** `list_series` has no per-series coverage aggregation today. Do not bolt filters onto it in this task — scope the filters to `/api/library/movies` and note in `03-doc-reconciliation.md` that series filters are movie-only for now.

---

## Tests

Add to the library routes test module (find the existing `list_movies` test; mirror its fixtures). Seed a few movies with varied `SubtitleInventory.coverage_json` (one with `full_dialogue_languages:["eng","fre"]`, one forced-only, one generated, one with no inventory).

1. `language=eng` → only movies with eng full coverage.
2. `missing_language=fre` → movies lacking fre full coverage (incl. no-inventory per the documented rule).
3. `forced_only=true` → only forced-only movies.
4. `generated=true` → only generated.
5. `min_tracks=2` → only `track_count>=2`.
6. `source=external` → only `external_present`.
7. `inventory_state=unscanned` → only no-inventory movies.
8. Combined `language=eng&min_tracks=2` + pagination → AND semantics + correct `total`/paging.
9. `policy_violation=true` → `400`. `inventory_state=stale` → `400`. `source=bogus` → `422`.
10. No filters → unchanged behavior + DB-side pagination path.

---

## Verification

- `ruff check marquee tests`; `pytest tests/ -k "library and filter"`.
- Manual smoke against live DB (port 3165):
  `GET /api/library/movies?language=eng&min_tracks=2`,
  `GET /api/library/movies?source=external`,
  `GET /api/library/movies?policy_violation=true` (expect 400).

---

## Doc follow-up (in `03-doc-reconciliation.md`)

Footnote §25.1: `policy_violation` and `inventory_state=stale` are not yet supported (return 400); filters are movie-only. Tick item #4 in `design/todos.md`.
