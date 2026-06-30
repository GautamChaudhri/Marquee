# 29 — Poster Pipeline UX Redesign

**Goal:** Overhaul the pipeline run-results page and review page with an inspector panel,
labeled score breakdown, poster maximize, review-page poster images, and a drag-and-drop
ranking model that replaces the current bucket-tier UX.

**Status:** Design — research phase for drag-and-drop ranking. Implementation not yet approved.

---

## Quick Reference — Targets

| # | Target | Group | Risk |
|---|--------|-------|------|
| 1 | Labeled score bar chart | Hero panel | Low |
| 2 | Click-to-inspect: hero panel tracks selected poster | Hero panel | Medium |
| 3 | Move approve/override/reject/rank actions into hero (remove popup) | Hero panel | Medium |
| 4 | Show extra poster metadata in hero when available | Hero panel | Low |
| 5 | Maximize button → large poster lightbox | Hero panel | Low |
| 6 | Review page: show actual top-pick poster images instead of gradients | Review page | Low |
| 7 | Drag-and-drop ranking (replaces bucket-tier UX) | Ranking | High |
| 8 | Keep existing bucket ranking as fallback | Ranking | Low |

---

## Group A — Hero Panel & Poster Inspection (Targets 1–5)

### Current State

The run-results page (`frontend/src/routes/pipeline/runs/[run_id]/+page.svelte`) has a
hero section (`div.hero`) at lines 627–691 that:
- Always shows the **auto-pick** poster image (line 635: `autoPick.poster_url`)
- Shows the auto-pick's score, explanations, and ScoreBar contributions (lines 654–668)
- Has three buttons: "Approve auto-pick" (opens `ConfirmDialog`), "Reject all" (opens
  `ConfirmDialog`), "Rank by taste" (shows `PosterRankingPanel` inline)

Clicking any poster in the grid calls `handlePosterSelect()` (line 398) → `openPick()`
(line 391) which sets `pickTarget` and opens a `ConfirmDialog` (lines 947–1019). That
dialog shows a small thumbnail, rank/score, and deploy toggle — but NO score breakdown,
NO explanations, NO contributions bar. Only the auto-pick hero gets those.

**The disconnect:** The hero is the rich info panel, but it only shows the auto-pick.
Clicking another poster opens a bare popup with almost no information.

### Target 1 — Labeled score bar

**File:** `frontend/src/lib/components/ScoreBar.svelte`

Current `ScoreBar` renders 8 colored vertical segments. Each segment has a `title`
tooltip with `label: value` but no visible inline label. The segments use an 8-color
palette with no legend.

**Change:**
- Add a legend row below the bar showing abbreviated feature names (e.g. "kNN", "Aes",
  "Color", "Clean", "Res", "Sharp", "Face", "Official") matching the palette colors.
- Each segment keeps its tooltip.
- Optional: consider grouping the ~13 features into 8 visual buckets, or expanding the
  bar to show all 13 segments with labels below.

**Research:** The backend sends `contributions: dict[str, float]` from the scorer
(`WeightedScorer.score()` in `marquee/pipeline/scorer.py`). The frontend converts these
to segments via `contribSegments()` (defined in the run page script, needs location
check). Labels should come from the contribution keys directly — no hardcoding needed
if the backend sends meaningful keys (which it does: `knn_sim`, `aesthetic`,
`title_colorfulness`, `text_residual`, `resolution`, `sharpness`, `face_absence`,
`provenance`, `lang_match`, `dino_knn`, `taste_typicality`, `quality_artifacts`,
`official_family`).

### Target 2 — Click-to-inspect: hero tracks selected poster

**Concept:** The hero panel becomes a live inspector. Instead of always showing the
auto-pick, it shows whichever poster the user most recently clicked.

**State change:**
- New `$state` variable: `inspectedPoster: CandidateView | null` (defaults to `autoPick`)
- `handlePosterSelect()` sets `inspectedPoster` instead of opening the pick dialog
- Hero image, score, explanations, ScoreBar all derive from `inspectedPoster`
- Visual feedback: the selected poster tile in the grid gets a highlighted border/glow
  to indicate "this is the one currently inspected"

**Caveat:** The "Approve" action should still default to the auto-pick, but the user
can also approve (or override to) the inspected poster. See Target 3.

### Target 3 — Integrate actions into hero (remove popup)

**Current:** Approve/override/reject all open `ConfirmDialog` modals. The pick dialog
(lines 947–1019) is a modal with thumbnail + deploy toggle + confirm.

**New design:**
- The hero actions bar (currently "Approve auto-pick", "Reject all", "Rank by taste")
  gains two more: an explicit "Set as pick" button and a "Maximize" button (Target 5).
- "Approve auto-pick" stays but changes text to "Approve auto-pick" when inspecting
  the auto-pick, or "Override with this pick" when inspecting a different poster.
- The deploy toggle moves into the hero body (next to the score, as a checkbox).
- The `ConfirmDialog` is retained only for destructive/reviewed states and for
  reject-all confirmation.
- When the user clicks "Approve"/"Override", a small inline confirmation step replaces
  the modal: the button changes to "Confirm" + "Cancel" for 3 seconds (or a single
  click with a subtle toast confirmation).

**Files touched:**
- `frontend/src/routes/pipeline/runs/[run_id]/+page.svelte` — hero template + state
- Remove `pickOpen`/`pickTarget`/`openPick`/`ConfirmDialog` (pick variant)
- `openPick()` function replaced by `inspectPoster()` setter

### Target 4 — Extra poster metadata in hero

When `inspectedPoster` has additional data not shown in the grid tile, display it in
the hero body. Candidates:

| Field | Source | Display |
|-------|--------|---------|
| Resolution | `CandidateView.width × height` | Small badge |
| Language | `CandidateView.lang_match` or iso code | Badge |
| Provenance | `CandidateView.provenance` (official/fan/etc) | Chip |
| Stage reached | (already in archive) | If not "ranked", show rejection reason |
| Stack membership | `stack_id` / `stack_pos` | "Design 3, variant B" |

### Target 5 — Maximize / lightbox

A "Maximize" button on the hero poster (or hero actions) opens a full-viewport lightbox
with the poster displayed as large as the viewport allows, dark backdrop, close on
click/escape.

**Implementation:**
- New small component or inline `<div class="lightbox">` in the run page
- `$state` toggle: `lightboxOpen: boolean`
- CSS: `position: fixed; inset: 0; background: rgba(0,0,0,0.92); z-index: 100`
- Image: `max-width: 90vw; max-height: 90vh; object-fit: contain`

---

## Group B — Review Page: Show Actual Posters (Target 6)

### Current State

The pipeline landing page (`frontend/src/routes/pipeline/+page.svelte`) Review tab
(lines 406–447) renders each movie as a `button.rev-card` containing a `PosterThumb`
component. `PosterThumb` shows a gradient + movie title/year/poster status — NOT the
pipeline's actual top-ranked poster.

### Change

On the Review page ONLY, each card should show the actual auto-pick poster image from
the pipeline run's archive, instead of the gradient placeholder.

**Data needed:** The review queue endpoint (`GET /api/pipeline/review-queue`) currently
returns `item.movie.poster_url` (which is the current deployed poster or null — almost
always null for movies in review). It does NOT return the archive's auto-pick poster
URL.

**Backend change required:**
- The review-queue response must include `item.run.auto_pick_poster_url` (the poster
  image URL for the run's rank-1 candidate).
- The auto-pick's `poster_url` is already in the run archive JSON (under
  `candidates[rank=1].poster_url`). The endpoint needs to extract and return it.
- OR: the frontend could fetch each run's results individually — too slow for 30+
  items.

**Frontend change:**
- `PosterThumb` already accepts `posterUrl` — pass the auto-pick URL when available.
- Keep the gradient as fallback when the URL is missing (runs predating the field).

**Files:**
- `marquee/api/routes/pipeline.py` — review-queue endpoint: include auto-pick poster URL
- `frontend/src/routes/pipeline/+page.svelte` — Review tab template
- `frontend/src/lib/api/types.ts` — update ReviewQueueItem type

---

## Group C — Drag-and-Drop Ranking (Targets 7–8)

**This is the biggest change and needs dedicated research before implementation.**

### Current Ranking UX (Bucket-Tier)

The `PosterRankingPanel` component (`frontend/src/lib/components/PosterRankingPanel.svelte`)
presents rankable items in a grid. Each item has:
- ♥ (Favorite) / – (Neutral) / ✕ (Hate) segment buttons
- If Favorite: −/+ tier buttons (1 is best, ties allowed)
- Submit sends `action: 'rank'` with `favorites: [[tier1...], [tier2...]]` and
  `hated: [...]`

The backend (`marquee/api/routes/feedback.py:420-511`):
1. Receives tiered favorites + hated lists
2. Backfills normalized features for all paired candidates
3. Stores a v3 ranking event with `bucket: "fav"|"hate"|"indiff"` per candidate
4. Adds tier-1 favorites to taste profile as positive exemplars
5. Adds hated high-ranked posters as hard negatives
6. Leaves unmarked ranked survivors as "indifferent"

The learned head trainer uses these buckets for **pairwise training**: favorites >
indifferent > hated, with tier ordering within favorites.

### Proposed Drag-and-Drop Model

**UX flow:**
1. User clicks "Rank by taste"
2. All posters appear in pipeline-ranked order (the starting state)
3. User drags posters to reorder them into their preferred ranking
4. A "trash" zone at the bottom for hated posters (drag to hate)
5. User clicks "Save ranking" when satisfied
6. The final order is submitted

**What the backend needs:** The new ordering must be translatable to the existing
v3 ranking event format. The backend currently expects:
- `favorites: list[list[str]]` — ordered tiers (ties share a sublist)
- `hated: list[str]`

A drag-and-drop order is a **total order** — every poster gets a position. To map
to the current format:
- **Option A:** Convert the total order to tiers. Posters in positions 1-N (user's
  top picks) become tier-1 favorites (ties), the rest are indifferent, and dragged-to-trash
  posters are hated. Simplest mapping — loses the tier granularity but preserves the
  important signal (top > rest > hated).
- **Option B:** Add a new `action` type (e.g. `action: 'reorder'`) that accepts a
  flat ordered list + hated list. The backend converts to v3 format internally.
  Cleaner but requires new API version.
- **Option C:** Change the ranking event schema to store a total order directly.
  The pairwise trainer already operates on relative preferences — a total order is
  actually richer data than buckets. This is the most forward-looking approach but
  touches more backend code.

### Research Needed (delegate to agent)

1. **Pairwise training data construction:** `marquee/ml/head_trainer.py` →
   `build_pairwise_training_data()` — how does it consume bucket/tier info? Can
   it consume a total order instead?

2. **Profile impact:** Currently tier-1 favorites are added as positive exemplars
   to the taste profile. With total order, should we add the top N positions?
   Configurable cutoff?

3. **Onboarding compatibility:** The onboarding flow (`marquee/api/routes/onboarding.py`)
   uses the same `favorites/hated` format for taste-test ranking. Would the drag-and-drop
   model apply there too?

4. **Undo compatibility:** The undo endpoint (`feedback.py:551`) removes exemplars
   and negatives. Works the same regardless of how the event was constructed —
   as long as the event record stores `favorites_exemplars` and `negatives_added`.

5. **Frontend library:** What drag-and-drop library to use?
   - `svelte-dnd-action` — lightweight, Svelte-native, well-maintained
   - `@dnd-kit/svelte` — modern, but heavier
   - Raw HTML5 drag-and-drop — no dependency, but worse UX (no animation)
   - Recommendation: `svelte-dnd-action` for Svelte 5 compatibility

6. **Mobile/tablet:** Drag-and-drop on touch devices needs special handling.
   Consider fallback to click-to-select-reorder for touch.

### Implementation Approach (Target 8 — Keep old ranking)

- Rename current `PosterRankingPanel` to `PosterRankingPanelLegacy` or wrap it behind
  a feature flag
- New component: `PosterDragRanking.svelte` implementing the drag-and-drop UX
- Pipeline config knob: `RANKING_UX_MODE = "drag" | "bucket"` (default `"drag"`)
- The "Rank by taste" button loads the appropriate component based on the knob
- Both components submit to the same `/api/feedback` endpoint (possibly with a new
  action type for drag mode, or mapped client-side to the existing format)

### Data Flow (Drag → Backend)

```
User drags posters into order [A, B, C, D, E], drags E to trash
  ↓
Frontend converts to:
  favorites: [[A, B, C, D]]   (one tier with all kept posters in order)
  hated: [E]
  ↓
POST /api/feedback  { action: "rank", favorites: [[A, B, C, D]], hated: [E] }
  ↓
Backend processes as v3 ranking event (unchanged)
  ↓
Pairwise trainer: A>B, A>C, A>D, B>C, B>D, C>D (within tier, order is preserved)
  + all favorites > indifferent > hated
```

**Key insight:** The current v3 format with `favorites` as `list[list[str]]` where
order within a tier IS significant already supports total ordering. A single tier
with all kept posters in order encodes the full drag-and-drop result. No backend
changes needed for the data format — only the frontend submission converter.

**This simplifies the change:** The drag-and-drop UI can map directly to the
existing API by sending a single favorites tier with the posters in user-chosen
order + a hated list. The learned head trainer already respects intra-tier order.

### Files Potentially Touched

| File | Change |
|------|--------|
| `frontend/src/lib/components/PosterDragRanking.svelte` | **New** — drag-and-drop ranking component |
| `frontend/src/lib/components/PosterRankingPanel.svelte` | Rename/keep as legacy |
| `frontend/src/routes/pipeline/runs/[run_id]/+page.svelte` | Switch ranking component based on config |
| `frontend/src/routes/onboarding/+page.svelte` | Possibly update for drag ranking too |
| `marquee/core/pipeline_config.py` | New knob `RANKING_UX_MODE` |
| `marquee/core/pipeline_config_meta.py` | Knob metadata for Settings UI |
| `package.json` | Add `svelte-dnd-action` dependency |

---

## Execution Order

1. **Group A (Hero Panel)** — targets 1–5: all frontend-only, no backend changes,
   can be implemented as a single batch. Natural order: 2→3→1→4→5 (inspect first,
   then wire up the rich display).

2. **Group B (Review Page)** — target 6: needs one small backend addition
   (auto-pick URL in review queue response) + frontend template change. Small scope,
   can ship independently.

3. **Group C (Drag-and-Drop)** — targets 7–8: needs research first (pairwise
   training compatibility, library selection). Implementation touches frontend +
   config. Keep old ranking as fallback. This is the largest change and should be
   a separate PR from Groups A/B.
