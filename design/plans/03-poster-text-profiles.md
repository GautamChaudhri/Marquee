# 03 — Poster Text Profiles

> **For Hermes:** Use `subagent-driven-development` skill to implement this plan group-by-group.

**Goal:** Replace the raw `OCR_TEXT_MODE` dropdown on the settings page with a
first-class "Text Profiles" system on the poster pipeline landing page. Built-in
presets ("Title Only", "Textless") capture the current behaviour. Users create
named custom profiles with per-category toggles (title, director, studio,
tagline, rating, actor billing) plus controls for how aggressively residual text
is tolerated. Profiles can be set as a global default and overridden per-movie.
The OCR gate is extended with TMDB-sourced director/studio metadata for accurate
classification, and a top-strip band detector handles actor billing without
needing to store every actor name.

**Architecture:** A new `TextProfile` dataclass and persistence layer
(`data/text_profiles.json`) feed into the OCR decision logic in
`ocr_filter.py`. The director and studio names are fetched from TMDB during
the existing library sync and stored as new nullable columns on the `Movie`
table. The `PosterTextFilter` already accepts `director` tokens; the runner is
updated to pass them. The `classify_text_box` and `_decide` functions gain new
categories. The frontend gains a new "Text Profiles" section on the poster
pipeline landing page (`/pipeline`) with profile CRUD, a global-default
selector, and per-movie override wiring.

**Tech Stack:** FastAPI + SQLAlchemy async (backend), SvelteKit (frontend),
existing `PosterTextFilter` / `PipelineSettings` / `TMDBClient` /
`SyncService`.

---

## Pre-Research Summary

| Capability | Status | Location |
|---|---|---|
| `OCR_TEXT_MODE` enum (title_only / textless / custom) | Done | `marquee/core/pipeline_config.py::PipelineSettings` |
| `OCR_ALLOW_TITLE/DIRECTOR/STUDIO/RATING/TAGLINE` toggles | Done | Same — gated behind `OCR_TEXT_MODE=custom` |
| `classify_text_box()` — semantic box classification | Done | `marquee/pipeline/ocr_filter.py:582` |
| `_decide()` — custom-mode allow/deny logic | Done | `marquee/pipeline/ocr_filter.py:1177` |
| `PosterTextFilter(director=...)` — director token plumbing | Done | Constructor accepts it; runner passes `None` |
| Text gate UI on settings page (dropdown + toggles) | Done | `marquee/core/pipeline_config_meta.py` `text_gate` group |
| TMDB movie details endpoint available | Done | `TMDBClient.get_movie_primary_poster()` already hits `/movie/{id}` |
| `SyncService` — library sync from Radarr | Done | `marquee/core/sync_service.py` |
| Poster pipeline landing page (`/pipeline`) | Done | `frontend/src/routes/pipeline/+page.svelte` |

**What is genuinely NEW:**

1. Named, user-creatable custom text profiles (CRUD + persistence)
2. Global default profile + per-movie text profile override
3. Director and studio data enrichment from TMDB during sync → new Movie columns
4. Director text handling: accept the whole box when director name is the anchor
5. Actor-billing category via top-strip band detection (no actor-name DB)
6. Text profiles UI on the poster landing page (moved from settings)
7. Custom profile knobs beyond simple toggles: residual tolerance sliders per
   category, title-requirement logic

---

## Group A — Move Text Gate UI from Settings to Poster Landing Page

### Task A1: Remove `text_gate` knob group from the settings page

**Objective:** The raw `OCR_TEXT_MODE` dropdown and bare toggles no longer
appear on the settings page. The profile system replaces them.

**Files:**
- Modify: `marquee/core/pipeline_config_meta.py` — remove the `text_gate`
  group entry (lines 53–82)

**Steps:**
1. Remove the `text_gate` group dict from the `KNOB_GROUPS` list.
2. Keep the `KNOB_META` entries for the individual knobs — the config API
   still needs to serve and validate them (the custom mode still uses them).
3. Verify: `GET /api/config/pipeline` no longer returns `text_gate` in
   `groups`. The knobs themselves still appear in `values`/`defaults`.
4. Verify: settings page no longer shows the Text gate / OCR tab.

### Task A2: Add a "Text Profiles" section to `/pipeline` landing page

**Objective:** A new card/section on the poster pipeline landing page where the
user sees the active profile, switches between built-in presets, and manages
custom profiles.

**Files:**
- Create: `frontend/src/lib/components/pipeline/TextProfilePanel.svelte`
- Modify: `frontend/src/routes/pipeline/+page.svelte` — add the panel
- Create: `frontend/src/lib/api/text-profiles.ts` — API helpers

**UI specification:**

A card with header "Poster text profiles" and subtitle explaining that this
controls what text is allowed on posters.

Three distinct sections:

1. **Active profile display** — shows the current profile name with a color
   badge ("Title Only" = blue, "Textless" = gray, custom profile = gold).
   A dropdown to switch profiles.

2. **Built-in presets** (always visible, non-deletable):
   - **Title Only** — "Only the movie title is allowed. All other text
     (taglines, credits, billing blocks) rejects the poster." This is the
     current default behaviour.
   - **Textless** — "No text at all. Posters with any detected text are
     rejected. Use for clean, iconic key art."

3. **Custom profiles** — list of user-created profiles with edit/delete
   buttons. A "+ New profile" button at the bottom.

When a built-in preset is selected, show a read-only summary of its settings.
When a custom profile is selected, show the full editor (Group D).

### Task A3: Add frontend API helpers for text profiles

**Files:**
- Create: `frontend/src/lib/api/text-profiles.ts`

```typescript
// types
export interface TextProfile {
  id: string;            // slug: "title_only" | "textless" | user-chosen
  name: string;          // display name
  builtin: boolean;      // true for title_only / textless (non-deletable)
  is_default: boolean;
  settings: TextProfileSettings;
}

export interface TextProfileSettings {
  mode: "title_only" | "textless" | "custom";
  allow_title: boolean;
  allow_director: boolean;
  allow_studio: boolean;
  allow_rating: boolean;
  allow_tagline: boolean;
  allow_billing: boolean;
  max_residual_boxes: number;
  max_residual_area_fraction: number;
  require_title: boolean;
}

// API calls
export function listTextProfiles(fetchFn: Fetch): Promise<TextProfile[]>;
export function createTextProfile(fetchFn: Fetch, profile: {name: string; settings: TextProfileSettings}): Promise<TextProfile>;
export function updateTextProfile(fetchFn: Fetch, id: string, profile: Partial<TextProfile>): Promise<TextProfile>;
export function deleteTextProfile(fetchFn: Fetch, id: string): Promise<void>;
export function setDefaultProfile(fetchFn: Fetch, id: string): Promise<void>;
export function setMovieTextProfile(fetchFn: Fetch, movieId: number, profileId: string | null): Promise<void>;
```

---

## Group B — Text Profile Backend (CRUD + Persistence)

### Task B1: Implement text profile persistence

**Objective:** Profiles live in `data/text_profiles.json`. The built-in
presets are always present (derived from code defaults). Custom profiles are
user-created.

**Files:**
- Create: `marquee/core/text_profiles.py` — profile model + persistence

**Data model:**

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass
class TextProfileSettings:
    mode: str = "title_only"       # title_only | textless | custom
    allow_title: bool = True
    allow_director: bool = False
    allow_studio: bool = False
    allow_rating: bool = False
    allow_tagline: bool = False
    allow_billing: bool = False
    max_residual_boxes: int = 0
    max_residual_area_fraction: float = 0.04
    require_title: bool = True

@dataclass
class TextProfile:
    id: str
    name: str
    builtin: bool = False
    is_default: bool = False
    settings: TextProfileSettings = field(default_factory=TextProfileSettings)

BUILTIN_PROFILES: dict[str, TextProfile]  # title_only, textless
```

**Persistence:**
- `load_profiles() -> dict[str, TextProfile]` — reads `data/text_profiles.json`,
  merges with built-ins (built-ins always take code defaults).
- `save_profiles(profiles)` — writes custom profiles only (built-ins not persisted).
- `get_active_profile(movie_id: int | None = None) -> TextProfile` — resolves
  the effective profile: per-movie override → global default → "title_only".
- Default profile is tracked via a `_default_profile` key in the JSON.

### Task B2: Add text profile API endpoints

**Objective:** REST endpoints for profile CRUD, default selection, and
per-movie override.

**Files:**
- Create: `marquee/api/routes/text_profiles.py`

**Endpoints:**

```
GET    /api/text-profiles              → list all profiles (built-in + custom)
POST   /api/text-profiles              → create custom profile {name, settings}
PUT    /api/text-profiles/{id}         → update custom profile
DELETE /api/text-profiles/{id}         → delete custom profile (refuse built-ins)
PUT    /api/text-profiles/default/{id} → set global default
GET    /api/text-profiles/movie/{id}   → get per-movie override (null = use global)
PUT    /api/text-profiles/movie/{id}   → set per-movie override {profile_id: str|null}
```

**Validation:**
- Built-in profiles cannot be deleted or renamed.
- At least one profile must exist (if last custom is deleted with no built-in
  as default, fall back to "title_only").
- Profile names must be unique and ≤ 64 chars.

### Task B3: Wire profile into the OCR gate

**Objective:** When the pipeline runs OCR, it reads the active profile's
settings instead of raw `PipelineSettings` knobs. The profile is resolved at
OCR time per-movie so per-movie overrides take effect.

**Files:**
- Modify: `marquee/pipeline/ocr_filter.py` — `_decide()` function
- Modify: `marquee/pipeline/runner.py` — pass `movie_id` to `PosterTextFilter`
- Modify: `marquee/pipeline/batch_runner.py` — same

**Approach:**
Instead of reading `pipeline_settings.OCR_TEXT_MODE` / `OCR_ALLOW_*` directly,
`_decide()` and `_process_image()` read from an injected profile. The
`PosterTextFilter` constructor gains an optional `profile: TextProfile`
parameter. When None, it falls back to the current pipeline_settings knobs
(backward compat). The runner/batch-runner resolve the profile from
`get_active_profile(movie.id)`.

Minimal change: add a `_resolve_effective_profile(movie_id)` call before
constructing `PosterTextFilter`, then pass it in.

---

## Group C — Director & Studio Data Enrichment from TMDB

### Task C1: Add director and studio columns to Movie

**Objective:** Two new nullable text columns for data that is small, stable,
and fetched once per movie during sync.

**Files:**
- Modify: `marquee/models/movie.py` — add columns

```python
# ── TMDB-enriched metadata (for OCR text-gate classification) ──
director: Mapped[str | None] = mapped_column(
    String(500), nullable=True,
    comment="Director name from TMDB movie credits — used by the text gate "
            "to identify director-credit text on posters.",
)
production_companies_json: Mapped[list[str] | None] = mapped_column(
    JSON, nullable=True,
    comment="Production company names from TMDB — used by the text gate "
            "to identify studio branding text on posters (e.g. 'Marvel Studios').",
)
tagline: Mapped[str | None] = mapped_column(
    Text, nullable=True,
    comment="Movie tagline from TMDB — used by the text gate to allow "
            "matching promotional text on posters.",
)
```

### Task C2: Generate Alembic migration

```bash
cd /forge/Marquee
alembic revision --autogenerate -m "add_movie_director_studio_tagline"
alembic upgrade head
```

### Task C3: Add TMDB movie-details fetch to TMDBClient

**Objective:** A new method that returns director name, production companies,
and tagline from TMDB's `/movie/{id}` endpoint (with `append_to_response=credits`).

**Files:**
- Modify: `marquee/core/poster_sources/tmdb.py`

**New method:**

```python
@dataclass
class MovieDetails:
    director: str | None
    production_companies: list[str]
    tagline: str | None

async def get_movie_details(self, tmdb_id: int) -> MovieDetails:
    """Fetch director, studios, and tagline from TMDB movie + credits."""
    data = await self._get(f"/movie/{tmdb_id}", params={
        "append_to_response": "credits",
    })
    if not isinstance(data, dict):
        return MovieDetails(None, [], None)

    # Director: first crew member with job="Director"
    director = None
    credits = data.get("credits", {})
    if isinstance(credits, dict):
        for person in credits.get("crew", []):
            if person.get("job") == "Director":
                director = person.get("name")
                break

    # Production companies
    companies = [
        c["name"] for c in data.get("production_companies", [])
        if c.get("name")
    ]

    tagline = data.get("tagline") or None

    return MovieDetails(director=director, production_companies=companies, tagline=tagline)
```

### Task C4: Enrich movies during sync

**Objective:** After syncing movie metadata from Radarr, also fetch TMDB
details for director, studio, and tagline.

**Files:**
- Modify: `marquee/core/sync_service.py` — `_sync_movies()` method

**Implementation:**
After the existing movie loop that sets `movie.title`, `movie.year`, etc.,
add a call to enrich TMDB details when `self.tmdb` is configured and the
movie has a `tmdb_id`:

```python
# ── Enrich TMDB metadata (director, studio, tagline) ──
if self.tmdb and movie.tmdb_id:
    try:
        details = await self.tmdb.get_movie_details(movie.tmdb_id)
        movie.director = details.director
        movie.production_companies_json = details.production_companies or None
        movie.tagline = details.tagline
    except Exception:
        logger.warning(
            "Failed to enrich TMDB details for movie tmdb_id=%s",
            movie.tmdb_id, exc_info=True,
        )
```

**Rate limiting:** TMDB's rate limit is ~50 req/s. A typical library has
hundreds of movies. Add an `asyncio.Semaphore(5)` to limit concurrent TMDB
calls during sync. Failures are logged and don't block the sync.

**Performance:** This adds one extra API call per movie. On a 1,000-movie
library with semaphore(5), that's ~200 sequential calls × ~100ms each =
~20 seconds extra sync time. Acceptable for a once-per-sync enrichment.

---

## Group D — Smart Text Detection for Custom Profiles

### Task D1: Pass director/studio/tagline tokens into PosterTextFilter from DB

**Objective:** The runner and batch-runner read the movie's `director`,
`production_companies_json`, and `tagline` from the DB row and pass them to
`PosterTextFilter` as token sets.

**Files:**
- Modify: `marquee/pipeline/runner.py` — line 755
- Modify: `marquee/pipeline/batch_runner.py` — around line 591
- Modify: `marquee/pipeline/ocr_filter.py` — `PosterTextFilter.__init__()`

**Current code (runner.py:755):**
```python
ocr_results = PosterTextFilter(movie_title, director=None).filter_batch(...)
```

**New code:**
```python
director_tokens = set(movie.director.lower().split()) if movie.director else set()
studio_tokens = set()
if movie.production_companies_json:
    for name in movie.production_companies_json:
        studio_tokens.update(name.lower().split())
tagline_text = movie.tagline or ""
ocr_filter = PosterTextFilter(
    movie_title,
    director=director_tokens,
    studio_tokens=studio_tokens,
    tagline_text=tagline_text,
)
```

**PosterTextFilter changes:**
- Add `studio_tokens: set[str]` and `tagline_text: str` parameters
- Store them alongside `director_tokens`
- Pass them into worker processes via the task tuple (currently
  `(index, path, title_text, title_tokens, director_tokens)`)

### Task D2: Director text — accept surrounding words when director is the anchor

**Objective:** When a text box is classified as "director" (because it contains
the director's name), accept the ENTIRE box regardless of other words. The
director name is the "anchor" that validates the box. Surrounding phrases like
"Directed by..." or "A film by..." are part of the director credit and should
not cause rejection.

**Files:**
- Modify: `marquee/pipeline/ocr_filter.py` — `_decide()` and `classify_text_box()`

**Implementation:**

In `_decide()`'s custom-mode path, when a box is classified as "director",
it is always allowed through regardless of word count or other content. The
classification itself (`classify_text_box`) already returns "director" when
the box text fuzzy-matches against `director_tokens` OR when the raw text
matches `r"directed\s+by"`. We just need to ensure the custom-mode denier
doesn't reject it.

The current code already does this via the `allow_map` dict:
```python
allow_map = {"director": pipeline_settings.OCR_ALLOW_DIRECTOR, ...}
```
If `OCR_ALLOW_DIRECTOR` is true and the box classifies as "director", it's
already allowed. The fix is just ensuring `classify_text_box` correctly
identifies director boxes.

**Enhance `classify_text_box`** to detect director text when:
1. Any word matches a director token (already done)
2. The text matches `directed by` regex (already done)
3. NEW: The text contains a director token surrounded by ≤ 5 other words
   ("A film by Christopher Nolan" → "Christopher" and "Nolan" are present)

Implement as a helper `_contains_director_tokens(text, tokens)` that checks
if a significant portion of the director's name is present in the text.

### Task D3: Studio text — match production company names

**Objective:** When `OCR_ALLOW_STUDIO` is enabled, text boxes that contain
known studio names are accepted.

**Files:**
- Modify: `marquee/pipeline/ocr_filter.py` — `classify_text_box()`

**Current implementation:** `classify_text_box` already checks
`words & STUDIO_KEYWORDS` (line 618). This is a static keyword list.

**Enhancement:** Add a new check: if `studio_tokens` are provided (from DB),
also classify boxes whose words match against the movie-specific
production company names. This is more precise than the static keyword list.

```python
# Studio: known keyword OR production-company match.
if words & STUDIO_KEYWORDS:
    return "studio"
if studio_tokens and _matches_allowed(box.text, studio_tokens):
    return "studio"
```

### Task D4: Tagline text — match TMDB tagline

**Objective:** When `OCR_ALLOW_TAGLINE` is enabled, a text box that reads the
same as (or fuzzy-matches) the movie's TMDB tagline should be accepted.

**Files:**
- Modify: `marquee/pipeline/ocr_filter.py` — `classify_text_box()`

**Implementation:**
The current tagline heuristic (centered, upper-mid, short) is a decent
heuristic but unreliable. With the actual TMDB tagline available, we can
do a precise match:

```python
# Tagline: TMDB tagline match takes priority over heuristic.
if tagline_text and _normalise(tagline_text):
    tagline_compact = _compact_text(tagline_text)
    box_compact = _compact_text(box.text)
    if tagline_compact and box_compact:
        ratio = difflib.SequenceMatcher(None, box_compact, tagline_compact).ratio()
        if ratio >= 0.70:
            return "tagline"
# Fall back to heuristic...
```

### Task D5: Rating text handling

**Objective:** When `OCR_ALLOW_RATING` is enabled, MPAA rating boxes are
allowed. The current regex-based classification is already solid — verify it
works end-to-end in custom mode.

The `_RATING_PATTERN` regex is already comprehensive. No code changes needed
— just verify the custom-mode denier respects `allow_map["rating"]`.

---

## Group E — Actor Billing Smart Detection

### Task E1: Implement top-strip band detector for billing text

**Objective:** Without storing actor names, detect that the top ~18% of the
poster contains a band of actor-name text. The key heuristic: actor billing
in the top strip is almost always a horizontal band of text boxes with
consistent height, similar y-position, and regular spacing. Format-junk
words (4K, UHD, HDR, etc.) are still filtered out, but other words in the
same band are accepted.

**Files:**
- Modify: `marquee/pipeline/ocr_filter.py` — new function + `classify_text_box()`

**Implementation:**

Add `_detect_top_billing_bands(boxes, image_width, image_height) -> set[int]`:

```python
def _detect_top_billing_bands(
    boxes: list[_DetectedBox],
    *,
    image_width: int,
    image_height: int,
) -> set[int]:
    """Return box IDs that belong to a top-strip actor-billing band.

    A billing band is a horizontal row of text boxes in the top ~18% of the
    poster where:
      - At least 2 boxes (usually 3-5 actor names)
      - All boxes have similar height (within 30% of median)
      - All boxes have similar y-center (within one box-height)
      - No box matches the format blocklist (4K, HDR, etc.)
      - The band spans a meaningful fraction of the poster width (>15%)
    """
    strip_cutoff = image_height * TOP_STRIP_FRACTION
    strip_boxes = [
        b for b in boxes
        if b.geometry_valid
        and _box_center(b)[1] <= strip_cutoff
        and b.confidence >= pipeline_settings.OCR_STRIP_CONFIDENCE_THRESHOLD
    ]
    if len(strip_boxes) < 2:
        return set()

    # Group by horizontal band (same line)
    line_groups = _same_line_groups(strip_boxes, image_height)

    billing_ids: set[int] = set()
    for group in line_groups:
        if len(group) < 2:
            continue
        # Exclude format-blocklist boxes from billing classification
        meaningful = [
            b for b in group
            if not (set(_normalise(b.text).split()) & FORMAT_BLOCKLIST)
        ]
        if len(meaningful) < 2:
            continue
        # Check band width
        band_width = _bbox_width(_bbox_union(group))
        if band_width < image_width * 0.15:
            continue
        # Consistent height: all boxes within 30% of median
        heights = [_bbox_height(b.bbox) for b in group]
        med = sorted(heights)[len(heights) // 2]
        if any(abs(h - med) / max(med, 1) > 0.30 for h in heights):
            continue
        # All pass — classify as billing
        for b in group:
            billing_ids.add(id(b))

    return billing_ids
```

**Integration into `classify_text_box()`:**

Before the fallback `return "other"`, add:
```python
# Billing: top-strip band of actor-name text.
if billing_band_ids and id(box) in billing_band_ids:
    return "billing"
```

The `billing_band_ids` set is computed once per `_process_image` call and
passed into `classify_text_box` (add as a parameter, or compute before the
classification loop).

**Integration into `_decide()` custom mode:**
Add `"billing": pipeline_settings.OCR_ALLOW_BILLING` to the `allow_map`.

### Task E2: Add `OCR_ALLOW_BILLING` knob to PipelineSettings

**Files:**
- Modify: `marquee/core/pipeline_config.py` — add field
- Modify: `marquee/core/pipeline_config_meta.py` — add KNOB_META entry

```python
OCR_ALLOW_BILLING: bool = Field(default=False)
```

And in `KNOB_META`:
```python
"OCR_ALLOW_BILLING": {"kind": "bool"},
```

Note: This knob stays in `PipelineSettings` for the raw config API but is
managed through the text profile system in normal usage.

---

## Group F — Per-Movie Text Profile Override

### Task F1: Add `text_profile_id` to Movie model

**Objective:** Track which text profile overrides this specific movie.
NULL means "use the global default".

**Files:**
- Modify: `marquee/models/movie.py` — add column

```python
text_profile_id: Mapped[str | None] = mapped_column(
    String(64), nullable=True,
    comment="Override text profile for this movie. NULL = use global default.",
)
```

### Task F2: Generate Alembic migration

```bash
alembic revision --autogenerate -m "add_movie_text_profile_id"
alembic upgrade head
```

### Task F3: Wire per-movie override into the pipeline

**Objective:** The runner resolves the effective profile per-movie.

**Files:**
- Modify: `marquee/pipeline/runner.py` — `run_sync_stages()` or its caller
- Modify: `marquee/pipeline/batch_runner.py` — `_run_ocr_batch()`

**Resolution logic:**
```python
from marquee.core.text_profiles import get_active_profile

profile = get_active_profile(movie_id=movie.id)
ocr_filter = PosterTextFilter(
    movie_title,
    director=director_tokens,
    studio_tokens=studio_tokens,
    tagline_text=tagline_text,
    profile=profile,
)
```

### Task F4: Expose per-movie override in the frontend

**Objective:** On the movie detail/film page (`/films/{id}`), add a dropdown
to override the text profile for that individual movie.

**Files:**
- Modify: `frontend/src/routes/films/[id]/+page.svelte` — add override control

**UI:** A small section in the movie detail page:
- "Text profile: [default]" — shows the effective profile
- Dropdown to override with any profile or "Use default"
- Save button → `PUT /api/text-profiles/movie/{id}`

---

## Group G — Frontend: Custom Profile Editor

### Task G1: Build the custom profile editor UI

**Objective:** When a custom profile is selected in the TextProfilePanel, show
a full editor with toggles and sliders.

**Files:**
- Create: `frontend/src/lib/components/pipeline/TextProfileEditor.svelte`

**Editor layout:**

1. **Profile name** — text input (only for custom profiles, built-ins show
   name as read-only)

2. **Text categories** — toggle grid:
   ```
   [✓] Movie title      — allow the movie title on posters
   [ ] Director name     — allow director credit text
   [ ] Movie studio      — allow studio/company branding
   [ ] Rating badge      — allow MPAA rating (PG-13, R, etc.)
   [ ] Tagline           — allow promotional taglines
   [ ] Actor billing     — allow top-strip actor names
   ```
   Each toggle has a help tooltip explaining what it controls.

3. **Residual tolerance** — two sliders:
   - **Max residual boxes** (0–20): how many non-allowed text boxes are
     tolerated before rejecting. Default 0 = strict.
   - **Max residual area** (0–10%): maximum fraction of poster area that
     non-allowed text can cover. Default 4%.

4. **Title requirement** — toggle:
   - "Require movie title to be present" — when on, a poster without a
     detected title is rejected even if all other text is clean.

5. **Save / Delete buttons** (delete disabled for built-ins)

### Task G2: Wire the editor to the API

**Objective:** Save creates/updates profiles via the text-profiles API.
The active profile change immediately updates `pipeline_settings` (or marks
dirty for the next run).

**Flow:**
1. User edits profile → frontend shows "Unsaved changes" indicator
2. User clicks Save → `PUT /api/text-profiles/{id}` with updated settings
3. Backend validates and persists to `data/text_profiles.json`
4. If this is the active profile, apply settings to `pipeline_settings` for
   the next pipeline run
5. Toast: "Profile saved — applies on next pipeline run"

### Task G3: Profile preset factory

**Objective:** When the user wants to create a new custom profile, they can
start from a built-in preset.

**Flow:**
1. Click "+ New profile"
2. Modal: choose starting point — "Start from Title Only" or "Start from
   Textless" or "Blank"
3. Enter a name
4. Editor opens with pre-populated settings from the chosen preset

---

## Summary of All File Changes

| File | Action | Group |
|---|---|---|
| `marquee/core/text_profiles.py` | Create (profile model + persistence) | B |
| `marquee/api/routes/text_profiles.py` | Create (CRUD endpoints) | B |
| `marquee/core/pipeline_config_meta.py` | Remove `text_gate` group | A |
| `marquee/core/pipeline_config.py` | Add `OCR_ALLOW_BILLING` | E |
| `marquee/models/movie.py` | Add director, studio, tagline, text_profile_id columns | C, F |
| `alembic/versions/` | Two new migrations | C, F |
| `marquee/core/poster_sources/tmdb.py` | Add `get_movie_details()` | C |
| `marquee/core/sync_service.py` | Enrich movies with TMDB details during sync | C |
| `marquee/pipeline/ocr_filter.py` | Director anchor logic, studio/tagline matching, billing band detector, profile-driven `_decide()` | D, E |
| `marquee/pipeline/runner.py` | Pass director/studio/tagline tokens, resolve per-movie profile | D, F |
| `marquee/pipeline/batch_runner.py` | Same — pass tokens and resolve profile | D, F |
| `frontend/src/routes/pipeline/+page.svelte` | Add TextProfilePanel | A |
| `frontend/src/routes/pipeline/+page.ts` | Load text profiles | A |
| `frontend/src/lib/api/text-profiles.ts` | Create (API helpers) | A |
| `frontend/src/lib/components/pipeline/TextProfilePanel.svelte` | Create (profile list + selector) | A |
| `frontend/src/lib/components/pipeline/TextProfileEditor.svelte` | Create (profile editor) | G |
| `frontend/src/routes/films/[id]/+page.svelte` | Add per-movie profile override dropdown | F |

---

## Verification Checklist

1. **Built-in presets work:**
   - Select "Title Only" → pipeline rejects posters with taglines, credits,
     billing. Only title text passes.
   - Select "Textless" → pipeline rejects ALL posters with any detected text.
     Only genuinely text-free posters pass.

2. **Custom profiles — CRUD:**
   - Create a profile named "Director + Title", enable Director + Title only.
   - Save → persists across restart.
   - Edit → toggles update. Delete → removed from list.

3. **Custom profiles — director logic:**
   - Set profile allowing Director + Title.
   - Run pipeline on a movie with known director (e.g. "Christopher Nolan").
   - Poster with "Directed by Christopher Nolan" text passes OCR.
   - Poster with only tagline (no director text) still fails.

4. **Custom profiles — actor billing:**
   - Set profile allowing Actor billing.
   - Run pipeline on a movie whose posters have top-strip actor names.
   - Posters with top billing pass. Posters with format-junk still fail.

5. **TMDB enrichment:**
   - Run sync → check `SELECT director, production_companies_json, tagline FROM movies LIMIT 5`.
   - Director is populated, production companies is a JSON array, tagline is text.

6. **Global default:**
   - Set a custom profile as default. Run pipeline → that profile's rules apply.
   - Per-movie override → set a different profile on one movie → that movie uses
     its override, others use global.

7. **No regressions:**
   - `pytest` passes.
   - `ruff check marquee tests` clean.
   - Frontend `npm run build` succeeds.
   - Settings page no longer shows the Text gate tab.
   - Existing `OCR_TEXT_MODE` / `OCR_ALLOW_*` knobs still work via
     `PUT /api/config/pipeline` for backward compat.

---

## Design Decisions & Rationale

### Why DB-enrich director/studio instead of pure OCR heuristics?

Director and studio are small, stable metadata (one string each per movie).
Fetching them from TMDB during sync adds one API call per movie — negligible
at scale with a semaphore. The benefit is deterministic: OCR never has to guess
whether "Nolan" is the director or a random word. Studio names ("Marvel
Studios") are equally clear-cut. Contrast with actor billing, where storing
5–20 actor names per movie in the DB would be noisy and almost never updated —
there, OCR band detection is the right trade-off.

### Why named profiles instead of just toggling more knobs?

The user explicitly wants reusable named profiles ("Clean Title Only", "Full
Credits", "No Text At All") that can be set as global defaults and overridden
per-movie. This is a UX win: instead of remembering which combinations of 8
toggles produce the desired behavior, the user creates named profiles once and
switches between them with one click. The raw knobs remain available for
power users and backward compatibility.

### Why move from settings to the poster landing page?

The text gate is the single most impactful pipeline setting for poster output
— it directly determines which posters survive. It belongs on the pipeline
dashboard where the user configures poster behavior, not buried in a
settings-page tab among 100 other knobs. The settings page keeps the raw
knobs for advanced tuning; the landing page gets the user-friendly profile
system.

### Why not store every actor name?

A movie has 3–20 credited actors. Storing them all would bloat the DB and
require frequent updates. More importantly, actor billing on posters is
stylized — names are split across multiple text boxes, abbreviated, or
presented out of order. A pattern-based detector (consistent-height band
in the top 18%) is actually MORE reliable than matching against a list of
names, because it catches all billing regardless of which actors are listed.

---

# Letterbox — Cleared Previews, Thorough Re-Detect & All-Perm Encode

> **Note:** This section covers the letterbox page (`/letterbox`) and is
> independent of the poster text profiles above. It can be implemented in
> parallel.

**Current state (from code inspection):**

| Feature | Status | Location |
|---|---|---|
| Cleared tab shows `not_letterboxed` movies | Done | `letterbox/+page.svelte` — tab key `cleared`, col `notLetterboxed` |
| Cleared detail pane shows "No fix needed" + "Re-detect" button | Done | `LetterboxDetail.svelte:1375-1382` — `stage === 'clean'` path |
| Cleared detail preview URLs (before/after) | Partial | API only returns `preview_urls` when `!state.reviewed`, but for cleared movies these show a before/after pair which is misleading — there's no crop to compare |
| Sample frame rows shown in confidence expander | Done | `LetterboxDetail.svelte:1260-1283` — clickable minute rows |
| Batch detect (`letterbox_detect_batch`) with per-movie frame analysis | Done | `letterbox.py`, `builtin_handlers.py` |
| Single-movie detect endpoint (`POST /api/letterbox/movies/{id}/detect`) | Done | `letterbox.py:860+` |
| "All Quick" button on staging tab (Quick crop tag → preview) | Done | `letterbox/+page.svelte:817` |
| "All Perm" button on staging tab | **Disabled placeholder** | `letterbox/+page.svelte:818` — greyed-out `<span>`, tooltip says "Re-encode coming soon" |
| Per-movie permanent re-encode flow (plan → confirm → encode) | Done | `LetterboxDetail.svelte` `method === 'permanent'` path |

**What is genuinely NEW:**

1. Cleared movies show ALL sample frame thumbnails (not a before/after pair)
2. "Re-analyze (thorough)" button on cleared movies — runs detection with 3×
   frame samples, reclassifies to staging or cleared
3. "All Perm" button enabled on staging tab with a settings popup (basic +
   advanced encode settings)
4. Backend: `POST /api/letterbox/movies/{id}/detect?thorough=true` or
   equivalent to pass a higher frame count

---

## Group H — Cleared Section: Full Frame Previews

### Task H1: API — return sample URLs for cleared movies

**Objective:** The `/api/letterbox/movies/{movie_id}` endpoint returns a flat
list of preview frame URLs for each sample's `before` frame, not a single
before/after pair. For cleared movies there is no crop, so we only need the
raw frames.

**Files:**
- Modify: `marquee/api/routes/letterbox.py` — `movie_detail()` around line 545

**Current code (simplified):**
```python
# line 561-565
if not state.reviewed:
    detail["preview_urls"] = {
        "before": f"/api/letterbox/movies/{movie_id}/preview?mode=before&minute={preview_minute}",
        "after": f"/api/letterbox/movies/{movie_id}/preview?mode=after&minute={preview_minute}",
    }
```

**New behaviour for cleared movies (`status in {"not_letterboxed", "variable_unsafe", "skipped"}`):**

Add `sample_previews` — a flat list of preview URLs, one per sample frame, keyed
by minute:

```python
# Always include sample_previews for every status so the frontend can
# render a frame strip regardless of stage.
sample_previews = [
    {
        "minute": s["minute"],
        "ok": s.get("ok", False),
        "url": f"/api/letterbox/movies/{movie_id}/preview?mode=before&minute={s['minute']}",
    }
    for s in samples
]
detail["sample_previews"] = sample_previews
```

Keep the existing `preview_urls` for backward compat with the detected/preview
stages. The frontend will use `sample_previews` for the cleared/clean stage.

### Task H2: Frontend — render frame strip for cleared movies

**Objective:** In `LetterboxDetail.svelte`, the `stage === 'clean'` path
(currently a single-row grid with optional before/after images) is replaced
with a scrollable horizontal strip of sample frame thumbnails. No before/after
comparison — just the raw frames.

**Files:**
- Modify: `frontend/src/lib/components/LetterboxDetail.svelte` — the
  `{:else if stage === 'clean'}` branch (around line 1375)

**Current template for clean (lines 1341-1357):**
```svelte
<div class="preview">
    {#if stage === 'candidate'}
        ...candidate placeholder...
    {:else if detail.preview_urls}
        <img class="frame" src={detail.preview_urls.before} .../>
        <img class="frame good" src={detail.preview_urls.after} .../>
    {:else}
        <div class="frame unanalyzed">No preview available</div>
    {/if}
</div>
```

**New template for clean:**

```svelte
<!-- Clean: horizontal strip of all sample frames -->
<div class="preview">
    {#if detail.sample_previews && detail.sample_previews.length > 0}
        <div class="ptitle">
            {detail.sample_previews.length} sample frame{detail.sample_previews.length === 1 ? '' : 's'}
            · click to view full size
        </div>
        <div class="frame-strip">
            {#each detail.sample_previews as sp (sp.minute)}
                <button
                    class="strip-thumb"
                    class:strip-failed={!sp.ok}
                    onclick={() => (previewMinute = sp.minute)}
                    title="Frame at {sp.minute} min{#if !sp.ok} — detection failed{/if}"
                >
                    <img class="frame" src={sp.url} alt="{sp.minute} min" loading="lazy" />
                    <span class="strip-label mono">{sp.minute}m</span>
                    {#if !sp.ok}
                        <span class="strip-err">✕</span>
                    {/if}
                </button>
            {/each}
        </div>
        <!-- Large preview of selected frame -->
        {#if previewMinute != null && detail.sample_previews.some(sp => sp.minute === previewMinute)}
            <img
                class="frame selected"
                src="/api/letterbox/movies/{movieId}/preview?mode=before&minute={previewMinute}&exact=true"
                alt="Frame at {previewMinute} min"
                loading="lazy"
            />
        {:else if detail.sample_previews[0]}
            <img class="frame selected" src={detail.sample_previews[0].url} alt="Preview" loading="lazy" />
        {/if}
    {:else}
        <div class="frame unanalyzed"><span class="ph">No sample frames available</span></div>
    {/if}
</div>
```

**CSS additions (in LetterboxDetail.svelte `<style>` block):**

```css
.frame-strip {
    display: flex;
    gap: 8px;
    overflow-x: auto;
    padding-bottom: 6px;
    scrollbar-width: thin;
}
.strip-thumb {
    flex: none;
    width: 120px;
    border: none;
    background: none;
    padding: 0;
    cursor: pointer;
    position: relative;
    border-radius: var(--radius-sm);
    overflow: hidden;
    border: 2px solid transparent;
    transition: border-color 0.15s;
}
.strip-thumb:hover {
    border-color: var(--line2);
}
.strip-thumb.strip-failed {
    opacity: 0.5;
}
.strip-thumb .frame {
    width: 100%;
    aspect-ratio: 16/9;
    object-fit: cover;
    display: block;
}
.strip-label {
    position: absolute;
    bottom: 3px;
    left: 4px;
    font-size: 9px;
    color: #fff;
    background: rgba(0,0,0,0.65);
    padding: 1px 5px;
    border-radius: 3px;
}
.strip-err {
    position: absolute;
    top: 3px;
    right: 4px;
    font-size: 11px;
    color: var(--bad);
}
.frame.selected {
    width: 100%;
    margin-top: 8px;
    border-radius: var(--radius-sm);
}
```

### Task H3: Update cleared detail info panel

**Objective:** The metadata shown for cleared movies should be relevant:
show dimensions, aspect ratio, detection method, and status reason instead
of crop-related fields (which don't apply).

**Files:**
- Modify: `frontend/src/lib/components/LetterboxDetail.svelte` — the meta
  section for clean stage (lines 1320-1338)

**Current meta for clean (line 1320-1338):**
Already shows dimensions and aspect ratio. Add:

```svelte
{#if detail.status === 'variable_unsafe' && detail.variable_ar_note}
    <dt>Variable AR note</dt>
    <dd class="note warn">{detail.variable_ar_note}</dd>
{/if}
{#if detail.detect_method}
    <dt>Method</dt>
    <dd class="mono">{detail.detect_method}</dd>
{/if}
{#if detail.last_detected_at}
    <dt>Last analyzed</dt>
    <dd>{new Date(detail.last_detected_at).toLocaleDateString()}</dd>
{/if}
```

---

## Group I — Thorough Re-Detect for Cleared Movies

### Task I1: Backend — accept frame-count multiplier in detect endpoint

**Objective:** The single-movie detect endpoint accepts an optional
`thorough` flag. When set, it passes a higher frame-sampling count to
the detector (3× the default). After completion, the movie's
`LetterboxState` is updated, which naturally places it in staging
(candidate) or cleared (not_letterboxed) based on the result.

**Files:**
- Modify: `marquee/api/routes/letterbox.py` — `POST /movies/{id}/detect`
- Modify: `marquee/core/letterbox_service.py` — detector call (or wherever
  the frame count is configured)

**Implementation:**

The single-movie detect handler (`detect_letterbox` at line ~860) currently
enqueues a `letterbox_detect` job. Add a `thorough: bool = False` query
parameter. Store it in the job's input payload:

```python
@router.post("/movies/{movie_id}/detect")
async def detect_letterbox(
    movie_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    thorough: bool = Query(False, description="3× frame samples for deeper analysis"),
):
    ...
    job = await job_manager.create_job(
        db=db,
        type="letterbox_detect",
        resources=await _file_lock(db, movie),
        input={"movie_id": movie_id, "thorough": thorough},
    )
```

In the handler that actually runs detection (likely `marquee/core/jobs/builtin_handlers.py`),
read the `thorough` flag and multiply the frame count:

```python
thorough = job.input.get("thorough", False) if job.input else False
frame_count = 30 if thorough else 10  # default is ~10 frames
# Pass to the detector...
```

The detector in `letterbox_service.py` or `letterbox_reencode.py` already has
a configurable sample count. Verify where the frame count is configured and
multiply by 3 when thorough.

### Task I2: Frontend — "Re-analyze (thorough)" button on cleared movies

**Objective:** In the cleared movie detail actions, add a new button below
the existing "Re-detect" button. The thorough button runs detection with
3× frames and shows a progress indicator.

**Files:**
- Modify: `frontend/src/lib/components/LetterboxDetail.svelte` — the
  `stage === 'clean'` actions section (around line 1375)
- Modify: `frontend/src/lib/api/letterbox.ts` — add `thorough` param

**UI changes in LetterboxDetail.svelte (line 1375-1382):**

Replace the current clean actions block:
```svelte
{:else if stage === 'clean'}
    <div class="note">
        No fix needed.{#if detail.status === 'variable_unsafe'}
            Variable aspect ratio — unsafe to crop.{/if}
    </div>
    <button class="btn-sec" disabled={busy || detecting} onclick={startDetection}>
        {detecting ? 'Analyzing…' : 'Re-detect'}
    </button>
```

With:
```svelte
{:else if stage === 'clean'}
    <div class="note">
        {#if detail.status === 'variable_unsafe'}
            Variable aspect ratio — unsafe to crop.
        {:else if detail.status === 'not_letterboxed'}
            Verified clear — no letterboxing detected.
        {:else}
            {detail.status}
        {/if}
        {#if detail.detect_method}
            <span class="method-tag mono">{detail.detect_method}</span>
        {/if}
    </div>
    <button class="btn-sec" disabled={busy || detecting} onclick={startDetection}>
        {detecting ? 'Analyzing…' : 'Re-detect'}
    </button>
    <button
        class="btn-gold"
        disabled={busy || detecting || thoroughDetecting}
        onclick={startThoroughDetection}
    >
        {#if thoroughDetecting}
            <span class="spin">⟳</span> Analyzing (3× frames)…
        {:else}
            <Icon name="refresh" size={14} /> Re-analyze (thorough)
        {/if}
    </button>
    <span class="thorough-hint">Analyzes 3× more frames for a deeper check</span>
```

**New state + API call:**
```typescript
let thoroughDetecting = $state(false);

async function startThoroughDetection() {
    if (id == null || busy || detecting || thoroughDetecting) return;
    thoroughDetecting = true;
    try {
        // Same detect endpoint, but with thorough=true flag
        const job = await detectLetterbox(fetch, id, { thorough: true });
        trackDetection(job);
        toast('Thorough re-analysis queued — 3× frame samples', 'info');
    } catch (e) {
        toast(e instanceof Error ? e.message : 'Could not queue analysis', 'bad');
        thoroughDetecting = false;
    }
}
```

The `detectLetterbox` API helper gets a new `thorough` option:
```typescript
// In frontend/src/lib/api/letterbox.ts
export function detectLetterbox(
    fetchFn: Fetch,
    movieId: number,
    options?: { thorough?: boolean },
): Promise<JobRef> {
    const params = new URLSearchParams();
    if (options?.thorough) params.set('thorough', 'true');
    const qs = params.toString();
    return apiSend<JobRef>(fetchFn, 'POST',
        `/letterbox/movies/${movieId}/detect${qs ? '?' + qs : ''}`);
}
```

---

## Group J — "Batch Re-encode" Button with Settings Popup & Confidence Filter

### Task J1: Frontend — rename "All Perm" to "Batch Re-encode" and enable as button

**Objective:** The disabled `<span>` at line 818 becomes a real "🔧 Batch Re-encode" button that opens a settings modal. The existing re-encode infrastructure is already working for individual movies — we just need to batch it with user-configurable confidence filtering.

**Files:**
- Modify: `frontend/src/routes/letterbox/+page.svelte` — staging tab
  actions (around line 809-825)

**Current (line 817-818):**
```svelte
<span class="tb quick-active">⚡ All Quick</span>
<span class="tb perm-disabled" title="Re-encode coming soon">🔧 All Perm</span>
```

**Replace with:**
```svelte
<span class="tb quick-active">⚡ All Quick</span>
<button
    class="tb gold"
    disabled={processing || detectedTotal === 0 || perming}
    onclick={openPermModal}
>
    {perming ? 'Encoding…' : '🔧 Batch Re-encode'}
</button>
```

Add state:
```typescript
let perming = $state(false);
let permModalOpen = $state(false);
```

Note: the internal state variable names (`perming`, `permModal`, etc.) can stay as-is since they're internal — only the user-facing label changes to "Batch Re-encode".

### Task J2: Frontend — build the "Batch Re-encode" settings modal with confidence filter

**Objective:** A modal dialog that lets the user configure encode settings
AND choose which staging movies to include based on their detection
confidence level. Default is high-confidence only. The modal also offers
blanket "select all" for convenience.

**Files:**
- Create: `frontend/src/lib/components/letterbox/BatchReencodeModal.svelte`

**Confidence filter options (checkboxes, multi-select):**

```
[✓] High confidence   (always recommended — detector is sure)
[ ] Medium confidence  (detector is moderately sure)
[ ] Variable           (aspect ratio alternates — may be intentional)
[ ] Low confidence     (detector is unsure — review manually first)
```

At the top of the filter section, a "Select all" / "Deselect all" toggle.
By default, only "High" is checked. A live count updates as the user
changes selections: "12 of 15 staging movies match".

**Modal layout:**

```
┌─────────────────────────────────────────────────┐
│  Batch Re-encode Staging Movies                  │
│                                                   │
│  ── Which movies? ────────────────────────────── │
│  Apply to movies with confidence:                 │
│  [Select all] [Deselect all]                      │
│  [✓] High   [ ] Medium   [ ] Variable   [ ] Low   │
│  → 8 of 15 staging movies selected                │
│                                                   │
│  ── Encode settings ────────────────────────────  │
│  Quality profile:  [Speed] [Balanced] [Quality]    │
│                                                   │
│  ── Advanced ───────────────────────────────────  │
│  Encoder:  [Auto ▼]                               │
│  Quality (CRF/CQ):  [18        ]                  │
│  Preset:   [medium ▼]                             │
│  Codec:    [Preserve source ▼]                    │
│  ☐ Allow CPU fallback                              │
│                                                   │
│  Crop override (optional):                        │
│  Top:  [  0] px   Bottom: [  0] px                │
│                                                   │
│                    [Cancel]  [Start Encoding]      │
└─────────────────────────────────────────────────┘
```

**Reuse existing settings from LetterboxDetail:**
The modal imports and reuses the same profile constants (`PROFILE_SETTINGS`,
`KNOWN_ENCODERS`, `NVENC_PRESETS`, `CPU_PRESETS`, `PROFILE_META`) from the
shared `encodeSettings.ts` module (Task J3).

**Confidence filter logic (in the batch-reencode page component):**

The staging column items carry a `confidence` field (values: `high`,
`medium`, `variable`, `low`, `none`). Filter the movie IDs before POSTing:

```typescript
// In the modal or parent component:
function selectedMovieIds(cols: LetterboxColumns, selected: Set<string>): number[] {
    return cols.detected.items
        .filter(item => selected.has(item.confidence ?? 'none'))
        .map(item => item.movie_id);
}
```

### Task J3: Extract shared encode settings to a module

**Objective:** The encode profile settings, encoder lists, and preset
options are duplicated logic. Extract them to a shared module so both
`LetterboxDetail.svelte` and `BatchReencodeModal.svelte` can use them.

**Files:**
- Create: `frontend/src/lib/letterbox/encodeSettings.ts`
- Modify: `frontend/src/lib/components/LetterboxDetail.svelte` — import from
  shared module instead of defining inline

**Extracted module exports:**
```typescript
export const KNOWN_ENCODERS: string[];
export const NVENC_PRESETS: string[];
export const CPU_PRESETS: string[];
export const ENCODER_LABELS: Record<string, string>;
export type QualityProfile = 'speed' | 'balanced' | 'quality';
export const PROFILE_SETTINGS: Record<QualityProfile, Record<string, {...}>>;
export const PROFILE_META: Record<QualityProfile, {...}>;
export function profileFamilyKey(family: string, encoder: string): string;
export function prettyEncoder(enc: string): string;
```

### Task J4: Backend — batch permanent re-encode endpoint

**Objective:** A new endpoint that creates permanent re-encode plans for a
list of movie IDs, respecting the user's chosen encode settings. Each movie
gets its own `MediaJob` (letterbox_reencode operation), and jobs are
submitted through the job manager so they queue properly and respect
resource limits.

**Files:**
- Modify: `marquee/api/routes/letterbox.py` — add `POST /batch/reencode`

**Endpoint:**
```
POST /api/letterbox/batch/reencode
Body: {
    "movie_ids": [1, 2, 3],
    "settings": {
        "quality_profile": "balanced",
        "encoder": "auto",
        "quality": null,
        "preset": "",
        "codec": "preserve",
        "allow_cpu": true,
        "crop_top_override": null,
        "crop_bottom_override": null
    }
}
```

**Implementation:**
```python
@router.post("/batch/reencode")
async def batch_reencode(
    payload: BatchReencodeRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    job_ids = []
    for movie_id in payload.movie_ids:
        movie = await _load_movie(db, movie_id)
        state = await _load_state(db, movie_id)
        eligibility = await asyncio.to_thread(
            letterbox_service.check_eligibility, movie
        )
        if eligibility.path is None:
            continue

        # Create plan with user overrides
        plan = await letterbox_service.create_reencode_plan(
            db, movie, state, overrides=payload.settings,
        )
        # Confirm automatically (no per-movie review for batch)
        job = await letterbox_service.confirm_reencode(db, plan.job_id)
        job_ids.append(job.job_id)

    await db.commit()
    return {"job_ids": job_ids, "count": len(job_ids)}
```

The existing `POST /api/letterbox/movies/{id}/reencode/plan` and
`POST /api/letterbox/movies/{id}/reencode/confirm` already handle the
per-movie flow. The batch endpoint loops over movie IDs and calls the
same service methods with the user's settings applied as overrides.

**Important:** This must go through the job manager's resource reservation.
A batch of N permanent re-encodes on 40–80 GB files will saturate disk I/O
if all run concurrently. The `JOB_MEDIA_WRITE_SLOTS` setting (default 1)
already serializes them — each re-encode claims a `media_write` resource
before starting.

### Task J5: Frontend — wire modal to batch endpoint

**Objective:** When the user clicks "Start Encoding" in the modal, POST to
`/api/letterbox/batch/reencode` with the selected movie IDs and settings.
Show progress via the existing job SSE/poll mechanism.

**Files:**
- Modify: `frontend/src/lib/components/letterbox/BatchReencodeModal.svelte`
- Modify: `frontend/src/lib/api/letterbox.ts` — add `batchReencode()`

**Flow:**
1. User selects confidence levels + configures encode settings → clicks "Start Encoding"
2. Frontend filters staging movies to only those matching the selected confidence levels
3. POST `/api/letterbox/batch/reencode` with `{movie_ids, settings}`
4. Backend creates a plan for each movie, confirms, returns job_ids
5. Modal closes → toast: "Queued {N} permanent re-encodes"
6. Each movie's re-encode shows progress in its detail pane (existing
   infrastructure handles this)
7. Per-movie progress tracked via the existing `letterbox_reencode`
   MediaJob SSE events

**API helper:**
```typescript
export function batchReencode(
    fetchFn: Fetch,
    movieIds: number[],
    settings: Record<string, unknown>,
): Promise<{ job_ids: string[]; count: number }> {
    return apiSend(fetchFn, 'POST', '/letterbox/batch/reencode', {
        movie_ids: movieIds,
        settings,
    });
}
```

---

## Updated Summary of All File Changes

Add these rows to the existing table:

| File | Action | Group |
|---|---|---|
| `marquee/api/routes/letterbox.py` | Add `sample_previews` to detail, `thorough` detect flag, `POST /batch/reencode` | H, I, J |
| `marquee/core/letterbox_service.py` | Accept thorough frame-count multiplier | I |
| `marquee/core/jobs/builtin_handlers.py` | Read `thorough` from job input, multiply frame count | I |
| `frontend/src/lib/components/LetterboxDetail.svelte` | Frame strip for clean, thorough button, import shared encode module | H, I, J |
| `frontend/src/routes/letterbox/+page.svelte` | Enable Batch Re-encode button, modal wiring | J |
| `frontend/src/lib/components/letterbox/BatchReencodeModal.svelte` | Create — encode settings modal with confidence filter | J |
| `frontend/src/lib/letterbox/encodeSettings.ts` | Create — shared encode settings module | J |
| `frontend/src/lib/api/letterbox.ts` | Add `thorough` param to detect, add `batchReencode()` | I, J |

---

## Letterbox Verification Checklist

1. **Cleared previews:**
   - Navigate to Cleared tab → select a movie.
   - Detail pane shows a horizontal strip of all sample frame thumbnails.
   - Clicking a thumbnail shows the full-size preview below.
   - Failed frames are dimmed with an ✕ marker.

2. **Thorough re-detect:**
   - On a cleared movie, click "Re-analyze (thorough)".
   - Progress bar appears, SSE events stream.
   - After completion: if letterboxing is found → movie moves to Staging.
     If still clear → movie stays in Cleared with updated last_detected_at.

3. **Batch Re-encode button + modal:**
   - Navigate to Staging tab with detected movies.
   - "🔧 Batch Re-encode" button is enabled (was greyed out "All Perm").
   - Click → modal opens with confidence checkboxes and encode settings.
   - By default, only "High" is checked. See live count: "N of M staging movies selected."
   - Check "Medium" → count increases. Click "Select all" → all confidences selected.
   - Select "Balanced" profile → encode settings populate.
   - Click "Start Encoding" → modal closes, toast shows queued count.
   - Only movies matching the selected confidence levels get re-encode jobs.
   - Each movie's detail shows re-encode progress (existing SSE bars work).

4. **No regressions:**
   - `pytest` passes.
   - Existing "Re-detect" button still works on cleared movies.
   - Existing per-movie permanent re-encode flow still works.
   - Existing "All Quick" → Process → Confirm flow still works.
   - `ruff check marquee tests` clean.
   - Frontend `npm run build` succeeds.

---

## Letterbox Design Decisions & Rationale

### Why show all sample frames instead of a single preview?

Cleared movies have no crop to demonstrate. A single "before" frame tells
the user nothing about what the detector saw — it's just a random frame
from the film. Showing all sample frames gives the user full visibility into
what the detector analyzed and why it concluded "no letterboxing." Each
frame is a data point the detector evaluated. The horizontal strip pattern
is already used in the confidence expander — this just makes it the default
view for cleared movies.

### Why a separate "thorough" button instead of making it the default?

The standard detection uses ~10 frames as a fast triage. 30 frames would be
3× the ffmpeg decode time on large 4K files. The user explicitly asked for
thorough as an opt-in, deeper check — it's a separate action, not a
replacement.

### Why batch re-encode through the job manager instead of inline?

Permanent re-encodes on 40–80 GB files take hours each. Running N of them
concurrently would saturate disk I/O. The job manager's `media_write` slots
(default 1) serialize them safely. Each movie's progress streams over SSE
independently, and the user can cancel individual encodes. This is the same
architecture the subtitle mutation system uses for batch track-remove. No
new infrastructure needed.

### Why default to high-confidence only in the batch re-encode filter?

High-confidence detections are very likely correct — the detector found
consistent black bars across most sample frames. Medium and variable
confidence mean the detector saw conflicting evidence (some frames
letterboxed, some full-frame). Running a permanent re-encode on those is a
big disk operation the user should review individually. The default keeps the
batch safe: re-encode the sure bets in one click, review the uncertain ones
one-by-one. But the "Select all" toggle gives power users a fast path when
they trust the detector across all confidence levels.
