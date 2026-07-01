# 03 — Historical Doc-Reconciliation Checklist

**Status:** Superseded by the 2026-06-16 reconciliation pass.

This file used to describe a smaller drift cleanup. The current reconciliation
pass should be treated as the source of truth for design-doc accuracy.

Remaining current-code facts:

- `POST /api/media-files/{media_file_id}/subtitles/scan` is implemented as an
  inline forced rescan, not a queued job.
- `/api/subtitle-batches/...` routes are not implemented.
- Letterbox preview uses `minute=<int>`, not `t=<sec>`.
- `POST /api/letterbox/movies/{movie_id}/detect` is synchronous; batch detect
  is `POST /api/letterbox/detect` and returns `202`.
- Manual poster restore is deferred; library filters remain planned, not shipped.

## Needs Verification

- `[NEEDS VERIFICATION]` If planned routes are implemented later, update this
  folder and the relevant feature docs in the same change.
