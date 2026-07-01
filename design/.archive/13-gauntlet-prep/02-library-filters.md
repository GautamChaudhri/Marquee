# 02 — Planned Server-Side Library Filters

**Status:** `[PLANNED]` filters. Not implemented in the current codebase.

Current `GET /api/library/movies` accepts only:

- `page`
- `page_size`

It returns each movie with `media_file_id` and cached `subtitle_coverage` when
available.

## Planned Filters

Potential future query parameters:

- `language`
- `missing_language`
- `source`
- `forced_only`
- `generated`
- `policy_violation`
- `min_tracks`
- `inventory_state`

These should be grounded in the actual `coverage_json` shape produced by
`marquee/core/subtitles/coverage.py`.

## Contract Decision

- Unsupported filters are omitted from the public contract until implemented.
- Before documenting filters as shipped, add tests against
  `SubtitleInventory.coverage_json` so each query parameter maps to an actual
  coverage field.
