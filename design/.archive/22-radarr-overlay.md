# 22 — Radarr Overlay Page

**Status:** Vertical slice built (2026-06-24). Tier ladder UX implemented for preference targets.
**Created:** 2026-06-24
**Depends on:** Radarr API v3 configured (already wired in `config.py`)

## Overview

The **Radarr Overlay** page replaces the placeholder HDR page at `/hdr`. Its purpose is to
surface Radarr-managed metadata that Radarr's own UI doesn't expose in a consolidated view —
custom format scores at a glance, HDR/DOVI status across all movies, and quality-profile-aware
HDR-target analysis.

The page lives under the **Toolbox** nav group (renamed from "HDR" to "Radarr Overlay").
It is read-only — no mutations to Radarr or the Marquee DB (aside from syncing the data in).

### UX Decision — Tier Ladder for Preference Targets

The per-profile meet/exceed target selector uses a **vertical tier ladder** instead of `<select>` dropdowns.
This reinforces the hierarchy (HDR → HDR10 → HDR10+ → DoVi any → DoVi+HDR) visually:

- **Green zone** — tiers at or above the meet target (but below exceed). File qualifies as "meets target."
- **Gold zone** — tiers at or above the exceed target. File "exceeds target."
- **Gray zone** — tiers below meet target. Automatically excluded.
- **Collapsible** — after setting preferences, the ladder collapses to a compact summary. Click to edit.
- **Click to set** — click any tier to set meet; click a higher tier to set exceed; click current boundary to clear.

`below_target` status text in the movie table uses `--bad` (red) for immediate visual identification.

---

## Targets

1. **Rename & Restructure** — Sidebar "HDR" → "Radarr Overlay", replace `<Stub>` with a real page
2. **HDR Tag Upgrade (3 → 5 groups)** — Distinguish HDR, HDR10, HDR10+, DoVi, DoVi (no HDR fallback)
3. **HDR Distribution & Filtering** — Stacked bar of HDR tags across all movies, filterable/sortable list
4. **Custom Format Score Visibility** — Show per-movie CF score and quality-profile cutoff at a glance
5. **HDR Target Detection** — Determine which HDR types each movie's quality profile targets
6. **HDR Target Status Column** — Met target / below target / no HDR target / DoVi-no-fallback warning
7. **Settings Toggle** — `HDR_OVERLAY_DOVI_REQUIRE_FALLBACK` knob (default: true)

---

## Target 1 — Rename & Restructure

- **Sidebar:** Change label from "HDR" → "Radarr Overlay". Keep the `/hdr` route for now
  (or migrate to `/radarr-overlay` — decide during implementation).
- **Page:** Replace the `<Stub>` placeholder with a real page.
- **Router:** `marquee/api/routes/hdr.py` remains the backend module; the router prefix
  stays `/api/hdr` for backward compatibility (or rename to `/api/radarr-overlay`).

### Files to touch

`frontend/src/lib/components/Sidebar.svelte`, `frontend/src/routes/hdr/+page.svelte`,
`marquee/api/routes/hdr.py`

---

## Target 2 — HDR Tag Upgrade (3 → 5 groups)

### Current state

The sync service extracts HDR info from Radarr's `movieFile.mediaInfo` into two booleans:

```
_extract_hdr() in sync_service.py:584:
  has_dv = bool  ("DV" or "DOLBY" in videoDynamicRangeType)
  has_hdr = bool (anything: HDR10, HDR10+, HLG, PQ, or generic "HDR")
  → Two booleans → three badges: dovi | hdr10 | sdr
```

The frontend `HdrKind` type reserves `hdr10p` but it's never populated; the fifth type
(DoVi without HDR fallback) doesn't exist. Radarr's `videoDynamicRangeType` field already
carries the distinguishing information — we're just not using it.

### Target state — 5 HDR groups

| Group | Label | Description |
|---|---|---|
| `hdr` | HDR | Generic/unspecified HDR (HLG, PQ, or plain "HDR" with no subtype) |
| `hdr10` | HDR10 | Standard HDR10 |
| `hdr10p` | HDR10+ | HDR10+ (dynamic metadata) |
| `dovi` | DoVi | Dolby Vision (any profile) |
| `dovi_no_fallback` | DoVi− | Dolby Vision WITHOUT an HDR10/HDR10+/HDR base layer fallback |

A single movie file can carry multiple tags (e.g. a file can be both HDR10 and DoVi
when DoVi has an HDR10 fallback layer). The `dovi_no_fallback` tag applies only when
DoVi is present AND no HDR fallback was detected.

### Implementation approach

**Recommended:** Add a `hdr_type_raw: str | None` column to `movies` storing the original
`videoDynamicRangeType` value from Radarr's media info. Classify into the 5 groups at read
time via a pure function. This preserves the raw data for future re-classification.

Keep `has_hdr`/`has_dv` for backward compatibility (deprecate later). The existing
`hdr_label()` in serializers becomes `hdr_tags()` returning a list.

**Alternative (rejected):** Replace booleans with a JSON array column of active tags
(e.g. `["hdr10", "dovi"]`). Simpler for querying but loses the raw source string and
makes re-classification impossible without re-syncing from Radarr.

### Classification logic

```python
def classify_hdr_tags(video_dynamic_range_type: str) -> list[str]:
    """Map Radarr's videoDynamicRangeType to the 5 HDR tag groups."""
    t = video_dynamic_range_type.upper().strip()
    tags = []

    # Dolby Vision detection
    has_dv = "DV" in t or "DOLBY" in t
    has_hdr_fallback = any(x in t for x in ("HDR10", "HDR", "HLG", "PQ"))

    if has_dv:
        tags.append("dovi")
        if not has_hdr_fallback:
            tags.append("dovi_no_fallback")

    # HDR subtypes
    if "HDR10PLUS" in t or "HDR10+" in t:
        tags.append("hdr10p")
    elif "HDR10" in t:
        tags.append("hdr10")
    elif any(x in t for x in ("HLG", "PQ")) or "HDR" in t:
        if not has_dv:  # only tag as generic HDR if not already DoVi
            tags.append("hdr")

    return tags if tags else ["sdr"]
```

### Re-sync required

After the schema change, all movies must be re-synced to populate the new column.
The sync service already pulls `movieFile.mediaInfo.videoDynamicRangeType` — only the
storage and classification change.

### Radarr source field

The raw data comes from `movieFile.mediaInfo.videoDynamicRangeType` in the Radarr
movie endpoint response. Example values seen in the wild:

- `"DV HDR10"` → DoVi with HDR10 fallback
- `"DV"` → DoVi with no HDR fallback (streaming Profile 5)
- `"HDR10Plus"` → HDR10+
- `"HDR10"` → standard HDR10
- `"HLG"` → Hybrid Log-Gamma
- `"PQ"` → Perceptual Quantizer (generic HDR)
- `""` or missing → SDR / unknown

### Files to touch

`marquee/models/movie.py`, `marquee/core/sync_service.py`, `marquee/api/library_serializers.py`,
`alembic/versions/`, `frontend/src/lib/api/types.ts`, `frontend/src/lib/components/HdrBadge.svelte`,
`frontend/src/lib/components/FilmGrid.svelte`, `frontend/src/lib/components/FilmList.svelte`

---

## Target 3 — HDR Distribution & Filtering

### Distribution bar

A horizontal stacked bar showing counts for each of the 5 HDR groups + SDR + unknown.
Clicking a segment filters the movie list below to that tag.

### Movie list columns

| Column | Source |
|---|---|
| Title + Year | `movies` table (existing) |
| HDR Tags | New `classify_hdr_tags()` — multi-badge display |
| Resolution | `video_width` → label via `resolution_label()` (existing) |
| Quality Profile | `quality_profile_id` → profile name (needs lookup from Target 4) |
| CF Score | Sum of custom format scores from the movie's current file (Target 4) |
| CF Cutoff | The `cutoffFormatScore` for the movie's quality profile (Target 4) |
| HDR Target Met? | Boolean from Target 5/6 |

### Filters

- **HDR tag filter:** Multi-select checkboxes for the 5 HDR groups + SDR + unknown
- **HDR target status:** "Met target" / "Below target" / "No HDR target"
- **Custom format score:** Range slider or min/max inputs
- **Quality profile:** Dropdown of profile names
- **DOVI fallback:** Filter to "DOVI without HDR fallback" movies

### Sorting

Sortable columns: Title, Year, CF Score, HDR status. Default: CF Score descending.

### Backend endpoint

`GET /api/hdr` already exists and returns paginated movies with HDR distribution.
Extend it with:
- Additional filter params (`hdr_tags`, `cf_score_min`, `cf_score_max`, `profile_id`,
  `hdr_target_status`, `dovi_no_fallback`)
- Sort params (`sort_by`, `sort_dir`)
- New response fields: `hdr_tags`, `cf_score`, `cf_cutoff`, `profile_name`,
  `hdr_target_status`, `hdr_targets`, `dovi_no_fallback`

### Files to touch

`marquee/api/routes/hdr.py`, `marquee/api/library_serializers.py`,
`frontend/src/routes/hdr/+page.svelte`

---

## Target 4 — Custom Format Score Visibility

### What Radarr already provides

The movie endpoint (`GET /api/v3/movie`) returns `movieFile.customFormats` — an array of
`{id, name, score}` objects representing the custom formats that matched the current file
and their scores within the movie's quality profile. Radarr also returns
`movieFile.qualityCutoffNotMet: bool` — whether the file has reached the profile's cutoff.

Currently, Marquee's sync ignores `movieFile.customFormats` completely. The `score` values
are profile-specific — the same custom format can have different scores in different profiles.

### Data to sync from Radarr

Three sources need to be pulled:

```
GET /api/v3/customformat  → all custom format definitions
  [{id, name, includeCustomFormatWhenRenaming, specifications: [...]}, ...]

GET /api/v3/qualityprofile → all quality profiles
  [{id, name, upgradeAllowed, cutoff, cutoffFormatScore, minFormatScore,
    formatItems: [{id, format (custom_format_id), name, score}, ...]}, ...]

GET /api/v3/movie (already synced) → per-movie movieFile.customFormats
  [{id, name, score}, ...]
```

### Storage approach

**Recommended (Option A — normalized tables):**

```sql
CREATE TABLE radarr_custom_formats (
    id INTEGER PRIMARY KEY,  -- Radarr's custom format ID
    name TEXT NOT NULL,
    include_when_renaming BOOLEAN DEFAULT FALSE,
    synced_at TIMESTAMP
);

CREATE TABLE radarr_quality_profiles (
    id INTEGER PRIMARY KEY,  -- Radarr's quality profile ID
    name TEXT NOT NULL,
    upgrade_allowed BOOLEAN,
    cutoff_format_score INTEGER,
    min_format_score INTEGER,
    synced_at TIMESTAMP
);

CREATE TABLE radarr_profile_format_items (
    profile_id INTEGER REFERENCES radarr_quality_profiles(id),
    custom_format_id INTEGER REFERENCES radarr_custom_formats(id),
    score INTEGER NOT NULL,
    PRIMARY KEY (profile_id, custom_format_id)
);

CREATE TABLE movie_custom_format_scores (
    movie_id INTEGER REFERENCES movies(id),
    custom_format_id INTEGER REFERENCES radarr_custom_formats(id),
    score INTEGER NOT NULL,
    synced_at TIMESTAMP,
    PRIMARY KEY (movie_id, custom_format_id)
);
```

This enables indexed queries for "movies with CF score ≥ X" and joins for filtering.

**Alternative (Option B — JSON blob):** Store `movieFile.customFormats` as a JSON column
on `movies`. Simpler to implement but requires full table scans for CF-score filtering.

### Display

Each movie row shows:
- **CF Score:** Sum of all custom format scores for the current file
- **CF Cutoff:** The quality profile's `cutoffFormatScore`
- **Cutoff Met?:** `movieFile.qualityCutoffNotMet == false` → green check
- **Visual indicator:** Green if CF Score ≥ cutoff, yellow if within 20%, red if far below

### Files to touch

`marquee/models/movie.py`, `marquee/core/sync_service.py`,
`marquee/core/arr_clients/radarr_client.py` (already has the methods),
`alembic/versions/`, `marquee/api/routes/hdr.py`

---

## Target 5 — HDR Target Detection

### The problem

Each movie has a `quality_profile_id`. Inside that quality profile, Radarr uses
**custom formats** with assigned scores to influence which releases are preferred.
We need to know:

1. **Is HDR being targeted** by the movie's quality profile?
2. **Which HDR types** are targeted (generic HDR? HDR10? HDR10+? DoVi?)?
3. **Has the current file met** the HDR target?

### Radarr API data flow

```
Movie (GET /api/v3/movie)
├── qualityProfileId → int
├── movieFile
│   ├── customFormats: [{id, name, score}, ...]  ← CFs that matched this file
│   └── mediaInfo.videoDynamicRangeType: str     ← e.g. "DV HDR10"
│
Quality Profile (GET /api/v3/qualityprofile/{id})
├── name: str
├── cutoffFormatScore: int            ← score at which upgrading stops
├── minFormatScore: int               ← minimum score to consider
├── formatItems: [
│     {id, format: custom_format_id, name: str, score: int}, ...
│   ]
│
Custom Format (GET /api/v3/customformat)
├── id, name: str
├── specifications: [
│     {name, implementation, fields: [{name, value}], ...}, ...
│   ]
```

### Step 1 — Classify all custom formats by HDR type

Fetch all custom format definitions from Radarr. For each, determine if it matches
one of the 5 HDR types by inspecting BOTH the name and the specification rules.

**(a) Name-based classification** — TRaSH guide formats follow predictable naming:
- `"HDR"`, `"HDR (undefined)"` → `hdr`
- `"HDR10"`, `"HDR10+"`, `"HDR10Plus"` → `hdr10` / `hdr10p`
- `"DV"`, `"Dolby Vision"`, `"DV HDR10"`, `"DV HDR10+"` → `dovi`
- `"DV (WEBDL)"`, `"DV (WEBDL) (HDR10)"` → `dovi`

**(b) Specification-based classification (fallback)** — Custom format specifications
contain `fields` arrays with pattern-matching rules. For example, a TRaSH "HDR10+"
custom format has:

```json
{
  "name": "HDR10+",
  "implementation": "ReleaseTitleSpecification",
  "fields": [{"name": "value", "value": "\\b(HDR10PLUS|HDR10P\\b|HDR10(\\+|\\b))"}]
}
```

We can parse `specifications[].fields[].value` to extract regex patterns and match
them against known HDR keywords. This handles user-renamed formats where the name
alone is ambiguous.

**Keyword hierarchy** (longest-match-first to avoid "HDR10+" matching as "HDR10"):

```python
HDR_KEYWORDS = {
    "hdr10p":  ["HDR10PLUS", "HDR10+"],
    "hdr10":   ["HDR10"],
    "dovi":    ["DV", "DOLBY", "DOLBY VISION"],
    "hdr":     ["HLG", "PQ", "HDR"],
}

def classify_custom_format(name: str, specs: list[dict]) -> str | None:
    """Return HDR tag group if this CF targets a specific HDR type."""
    name_upper = name.upper()

    # Name-based: check longest keywords first
    for tag in ("hdr10p", "hdr10", "dovi", "hdr"):
        for kw in HDR_KEYWORDS[tag]:
            if kw in name_upper:
                # Guard: "HDR10+" contains "HDR10" — don't misclassify
                if tag == "hdr10" and any(k in name_upper for k in HDR_KEYWORDS["hdr10p"]):
                    continue
                return tag

    # Fallback: parse specification regex values for HDR keywords
    for spec in specs:
        for field in spec.get("fields", []):
            if field.get("name") == "value":
                pattern = field.get("value", "").upper()
                for tag in ("hdr10p", "hdr10", "dovi", "hdr"):
                    if any(kw in pattern for kw in HDR_KEYWORDS[tag]):
                        return tag
    return None
```

### Step 2 — Map quality profiles to HDR targets

For each quality profile, examine its `formatItems`. Any custom format classified
as an HDR type in Step 1, with a **positive score**, indicates that profile targets
that HDR type. A score of 0 or negative means the CF is present in the profile but
not actively sought.

```python
def get_hdr_targets(profile_id: int, cf_classifications: dict[int, str],
                     profile_format_items: dict[int, list[dict]]) -> set[str]:
    """Return the set of HDR types this profile targets."""
    targets = set()
    for item in profile_format_items.get(profile_id, []):
        cf_id = item["format"]
        score = item["score"]
        hdr_type = cf_classifications.get(cf_id)
        if hdr_type and score > 0:
            targets.add(hdr_type)
    return targets
```

**Example:** A profile with `formatItems` containing:
- `{format: 15, name: "Dolby Vision", score: 15}` → adds `dovi` to targets
- `{format: 20, name: "HDR10+", score: 10}` → adds `hdr10p` to targets
- `{format: 30, name: "AAC Audio", score: 0}` → ignored (not HDR)
- `{format: 25, name: "HDR10", score: -5}` → ignored (negative score = penalized)

Result: `{"dovi", "hdr10p"}` — this profile targets Dolby Vision and HDR10+.

### Step 3 — Determine if target is met

For each movie, compare the file's actual HDR tags (from Target 2's `classify_hdr_tags()`)
against the profile's HDR targets (from Step 2):

```
For each targeted HDR type:
  "hdr" in targets    → "hdr" in file_tags      → hdr met
  "hdr10" in targets  → "hdr10" in file_tags    → hdr10 met
  "hdr10p" in targets → "hdr10p" in file_tags   → hdr10p met
  "dovi" in targets   → "dovi" in file_tags AND
    (not require_fallback OR "dovi_no_fallback" NOT in file_tags) → dovi met

target_met = ALL targeted types are met
```

**DOVI fallback handling:** When `HDR_OVERLAY_DOVI_REQUIRE_FALLBACK=true` (default),
a file with DoVi but no HDR fallback (Profile 5 streaming DoVi) does NOT count as
"dovi target met." When the toggle is off, any DoVi satisfies the target.

### The `cutoffFormatScore` distinction

The `cutoffFormatScore` is the overall "stopping point" for Radarr upgrades — the
total CF score at which Radarr stops looking for better releases. It is NOT
HDR-specific. A movie can reach cutoff without HDR (scoring high on audio/bitrate CFs),
or have HDR but be below cutoff (missing other scored CFs). These are separate metrics:

- **HDR target met:** Does the file have the HDR types the profile wants? (quality check)
- **At/above cutoff:** Has the file reached the total score threshold? (upgrade check)

Both are useful on the overlay — shown as separate columns.

### `qualityCutoffNotMet` from Radarr

Radarr already computes whether the current file meets the cutoff and returns
`movieFile.qualityCutoffNotMet: bool`. Sync this boolean directly rather than
reimplementing the scoring logic. Expose it as the "Cutoff Met?" column.

### Data freshness

Custom format definitions and quality profiles change infrequently (user edits in
Radarr). Sync them on the same schedule as the movie sync (every 15 min by default)
and on manual sync trigger. Cache the CF classification map in memory (dictionary,
rebuilt on sync).

### Edge cases

- **Movie has no file yet** (monitored but not downloaded): HDR target unknown, show
  status as "No file" with the profile's HDR targets listed as "targeting: HDR10, DoVi"
- **Multiple movie files** (Radarr supports multiple editions): Use the primary/active file
- **Custom format renamed in Radarr:** Re-classification on next sync handles this
- **Quality profile changed for a movie:** Synced on next pass; show "stale" indicator if
  sync hasn't run recently
- **DOVI Profile 5** (streaming DoVi, no HDR fallback): `videoDynamicRangeType: "DV"` with
  no HDR10 — gets the `dovi_no_fallback` tag

### Open questions for review

1. **Denormalized or computed?** Store HDR target status on the movie row or compute at
   query time? **Recommendation:** Compute at query time. The data is small (one quality
   profile per movie, ~100 custom formats total). No performance concern for libraries
   under 10,000 movies.

2. **Multi-HDR-tag files:** A file with "DV HDR10" gets both `dovi` and `hdr10` tags.
   **Yes** — showing both is accurate. The file literally has both layers.

3. **Same CF, different scores:** A "Dolby Vision" CF might be +15 in one profile and +5
   in another. Correct — each profile scores independently, and we track per-profile.

### Files to touch

`marquee/models/movie.py`, `marquee/core/sync_service.py`,
`marquee/core/arr_clients/radarr_client.py`, `marquee/api/library_serializers.py`,
`alembic/versions/`

---

## Target 6 — HDR Target Status Column

Once HDR targets are determined per movie (Target 5), expose the status in the overlay
and on individual movie views:

| Status | Condition |
|---|---|
| **Met target** | Movie's current file has all HDR tags the profile targets |
| **Below target** | Profile targets HDR but the current file doesn't have it |
| **No HDR target** | Profile doesn't target any HDR format |
| **No file** | Movie monitored but no file downloaded yet |
| **DOVI no fallback** ⚠ | Always shown when DoVi is present without HDR fallback, regardless of profile |

The DOVI-no-fallback warning is a visual flag — it doesn't block "target met" unless
`HDR_OVERLAY_DOVI_REQUIRE_FALLBACK=true` AND the profile targets DoVi.

### Files to touch

`marquee/api/routes/hdr.py`, `marquee/api/library_serializers.py`,
`frontend/src/routes/hdr/+page.svelte`

---

## Target 7 — Settings Toggle

Add a pipeline/config knob:

```
HDR_OVERLAY_DOVI_REQUIRE_FALLBACK: bool = True  (default)
```

- **`True` (default):** "DOVI target met" requires an HDR10/HDR10+/HDR base layer
  fallback. DoVi Profile 5 files without fallback are flagged but do not satisfy
  the DoVi target.
- **`False`:** Any Dolby Vision is considered good enough. The DoVi-no-fallback
  warning still appears but does not block target satisfaction.

### Implementation

Add to `PipelineSettings` in `marquee/core/pipeline_config.py` with the standard
knob registration (field + `KNOB_GROUPS` + `KNOB_META` + validation). Hot-update
safe — read at call time. The toggle affects the logic in `get_hdr_targets()` /
target-met comparison.

### Files to touch

`marquee/core/pipeline_config.py`, `marquee/core/pipeline_config_meta.py`

---

## Implementation Order

1. **Schema + sync:** Add `hdr_type_raw` column, new CF/profile tables, migration
2. **Backend classification:** `classify_hdr_tags()`, update serializers and `_extract_hdr`
3. **Re-sync:** Populate new columns for all movies
4. **Frontend types:** Update `HdrKind`, add CF score types
5. **HDR badge component:** Support 5 groups with visually distinct badges
6. **Overlay page:** Distribution bar, movie list with filters/sort
7. **HDR target detection:** Sync custom formats + quality profiles, classify CFs, compute targets
8. **HDR target column:** Met/below/no-target indicators
9. **Settings toggle:** `HDR_OVERLAY_DOVI_REQUIRE_FALLBACK` knob

---

## Appendix A — Files to Touch (master list)

| File | Change |
|---|---|
| `marquee/models/movie.py` | Add `hdr_type_raw`, new CF/profile tables |
| `marquee/core/sync_service.py` | Sync CFs, quality profiles, movie file CFs; update `_extract_hdr` |
| `marquee/core/arr_clients/radarr_client.py` | Already has `get_custom_formats`, `get_quality_profiles` |
| `marquee/core/pipeline_config.py` | Add `HDR_OVERLAY_DOVI_REQUIRE_FALLBACK` knob |
| `marquee/core/pipeline_config_meta.py` | Register knob in KNOB_GROUPS + KNOB_META |
| `marquee/api/library_serializers.py` | Replace `hdr_label()` with 5-group classifier; update `hdr_filter` |
| `marquee/api/routes/hdr.py` | Extend endpoint with new filters, CF scores, HDR targets |
| `alembic/versions/` | Migration for `hdr_type_raw` + CF/profile tables |
| `frontend/src/lib/api/types.ts` | Update `HdrKind`, add CF score types |
| `frontend/src/lib/components/HdrBadge.svelte` | Support 5 groups, multi-badge |
| `frontend/src/lib/components/Sidebar.svelte` | "HDR" → "Radarr Overlay" |
| `frontend/src/routes/hdr/+page.svelte` | Full page implementation |
| `frontend/src/lib/components/FilmGrid.svelte` | Update HDR badge usage |
| `frontend/src/lib/components/FilmList.svelte` | Update HDR badge usage |

## Appendix B — Radarr API Response Shapes

### Quality Profile (`GET /api/v3/qualityprofile/{id}`)
```json
{
  "id": 1,
  "name": "HD-1080p",
  "upgradeAllowed": true,
  "cutoff": 1000,
  "minFormatScore": 0,
  "cutoffFormatScore": 100,
  "minUpgradeFormatScore": 1,
  "formatItems": [
    {"id": 1, "format": 15, "name": "Dolby Vision", "score": 15},
    {"id": 2, "format": 20, "name": "HDR10+", "score": 10},
    {"id": 3, "format": 25, "name": "HDR10", "score": 5}
  ],
  "items": [...]
}
```

### Custom Format (`GET /api/v3/customformat`)
```json
{
  "id": 15,
  "name": "Dolby Vision",
  "includeCustomFormatWhenRenaming": true,
  "specifications": [
    {
      "name": "DV",
      "implementation": "ReleaseTitleSpecification",
      "implementationName": "Release Title",
      "fields": [{"name": "value", "value": "\\b(DV|DOLBY[ .]?VISION)\\b"}]
    }
  ]
}
```

### Movie File Custom Formats (from `GET /api/v3/movie`)
```json
"movieFile": {
  "customFormats": [
    {"id": 15, "name": "Dolby Vision", "score": 15},
    {"id": 20, "name": "HDR10+", "score": 10}
  ],
  "qualityCutoffNotMet": false
}
```
