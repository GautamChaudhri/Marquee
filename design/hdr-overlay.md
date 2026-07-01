# HDR Overlay

## Overview

The HDR overlay is a read-focused surface for Radarr-managed movie files. It
collects HDR tags, custom-format scores, quality-profile targets, and Dolby
Vision analysis into one browseable page and detail view so users can see where
their library sits relative to their HDR preferences. The API is implemented in
`marquee/api/routes/hdr.py`, the classification helpers live in
`marquee/core/radarr_overlay.py`, and the UI lives under
`frontend/src/routes/hdr/`.

## Implemented Scope

Implemented:

- Overlay index and per-movie detail API
- HDR tag classification with a five-bucket distribution
- Profile-derived target detection and preference status
- Custom-format score visibility
- Stored profile preference overrides
- Dolby Vision eligibility and conversion job entry points

Not implemented or intentionally limited:

- Bulk mutation of Radarr or Marquee movie records from the overlay page
- TV overlay support
- A separate persistence model beyond the overlay-specific profile preference
  rows and synced reference data

## Data Sources

The overlay combines:

- `Movie`, `MediaFile`, `LetterboxState`, and `DoviState` rows from
  `marquee/models/`
- Synced Radarr reference rows such as `RadarrQualityProfile`,
  `RadarrCustomFormat`, `RadarrProfileFormatItem`, and
  `RadarrOverlayProfilePreference`
- Conversion eligibility from `marquee/core/dovi_analysis.py`

Reference data is refreshed during sync in `marquee/core/sync_service.py`,
especially `_sync_radarr_overlay_reference_data()` and
`_replace_movie_custom_format_scores()`.

## HDR Classification

`marquee/core/radarr_overlay.py` normalizes Radarr’s dynamic-range strings into
overlay tags:

- `hdr`
- `hdr10`
- `hdr10p`
- `dovi`
- `dovi_no_fallback`

The distribution view uses these tags plus an implicit SDR bucket. A Dolby
Vision file with no HDR fallback layer is explicitly separated as
`dovi_no_fallback`, which matters for preference evaluation when
`HDR_OVERLAY_DOVI_REQUIRE_FALLBACK=true`.

## Profile Targets And Preference Status

Quality-profile target logic is derived from positive-scoring Radarr custom
formats. `profile_hdr_targets()` identifies which HDR targets a profile is
actually asking for, and the overlay then exposes user-facing target choices
such as:

- `sdr`
- `hdr`
- `hdr10`
- `hdr10p`
- `dovi_no_fallback`
- `dovi_fallback`

Each movie is classified into a preference status:

- `below_target`
- `meets_target`
- `exceeds_target`
- `no_hdr_target`

Saved per-profile preference overrides are managed through
`PUT /api/hdr/preferences`.

## API And UI Surface

Core routes in `marquee/api/routes/hdr.py`:

- `GET /api/hdr`
- `PUT /api/hdr/preferences`
- `GET /api/hdr/{movie_id}`
- `POST /api/hdr/{movie_id}/analyze`
- `POST /api/hdr/{movie_id}/convert`
- `POST /api/hdr/analyze`

The page supports filtering by HDR tags, profile, custom-format score range,
preference status, and a dedicated `dovi_no_fallback` flag. It also supports
sorting by title, year, custom-format score, and preference status.

## Read Model Behavior

The overlay is primarily observational. It does not directly rewrite Radarr
quality profiles, custom formats, or movie metadata. The mutable behavior that
does exist is limited to:

- saving Marquee-side profile preference overrides
- queueing HDR analysis and conversion jobs

That separation keeps the page safe to use as a visibility layer while the
heavy lifting stays inside the job platform and the sync service.

## Important Configuration

The main overlay-specific runtime knob is
`HDR_OVERLAY_DOVI_REQUIRE_FALLBACK=true` in
`marquee/core/pipeline_config.py`. More generally, the overlay depends on the
Radarr client being configured through `RADARR_URL` and `RADARR_API_KEY` in
`marquee/config.py`.

## Cross References

- `library.md` for sync behavior and movie/media-file persistence
- `job-platform.md` for queued HDR analysis and conversion jobs
- `timeline.md` for deferred TV parity and future HDR follow-up work
