# Marquee — SvelteKit Developer Handoff

> Drop this file in your SvelteKit repo root. Reference it when building components and API routes.  
> The living visual spec is `Marquee.dc.html` — open it in a browser alongside your editor.

---

## 1. Design Tokens

Paste these into `src/app.css` (or your Tailwind `globals.css`). The app ships dark-first with a `[data-theme="light"]` override.

```css
:root {
  /* surfaces */
  --ink:        #0c0d11;
  --ink2:       #0f1116;
  --panel:      #15171e;
  --panel2:     #1c1f28;

  /* borders */
  --line:       #272b36;
  --line2:      #323744;

  /* text */
  --text:       #e8e9ef;
  --muted:      #8a909f;
  --faint:      #5b6170;
  --faint2:     #3f4452;

  /* brand accent */
  --gold:       #ffc24b;
  --gold-deep:  #e3a01f;
  --gold-soft:  rgba(255,194,75,.14);
  --on-gold:    #241a04;

  /* semantic */
  --good:       #46d18a;
  --warn:       #fbbf24;
  --low:        #fb923c;
  --bad:        #f87171;
  --info:       #5ca8fb;
  --dovi:       #b794f6;

  /* system */
  --cpu:        #4a8cf0;
  --gpu:        #5ad17a;

  --shadow:        rgba(0,0,0,.7);
  --poster-shade:  rgba(0,0,0,.34);
}

[data-theme="light"] {
  --ink:        #e7eaf0;
  --ink2:       #eef1f6;
  --panel:      #ffffff;
  --panel2:     #f1f3f7;
  --line:       #dce0e8;
  --line2:      #c6ccd7;
  --text:       #191d25;
  --muted:      #5c6473;
  --faint:      #8b93a3;
  --faint2:     #aab1bf;
  --gold:       #b9810c;
  --gold-deep:  #9c6c08;
  --gold-soft:  rgba(185,129,12,.13);
  --on-gold:    #2a1d02;
  --good:       #1f9d57;
  --warn:       #b9810c;
  --low:        #d2731a;
  --bad:        #d4453f;
  --info:       #2a6fd4;
  --dovi:       #7c52e0;
  --shadow:     rgba(20,28,48,.18);
  --cpu:        #2f6fd6;
  --gpu:        #1f9d57;
}
```

### Typography
System stack — no custom font needed:
```
font-family: ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
```
Monospace (used for numbers, badges, paths):
```
font-family: ui-monospace, 'SF Mono', Menlo, Monaco, 'Cascadia Code', Consolas, monospace;
```

### Poster gradient palette
Each film gets a deterministic gradient from this set (hash title → index):
```js
const GRADS = [
  ['#1a2744','#0a1019','#ffd166'],  // blue/gold
  ['#2a1535','#110a1b','#f472b6'],  // purple/pink
  ['#2b1a12','#130905','#fb923c'],  // brown/orange
  ['#0e2a25','#06130e','#5eead4'],  // teal
  ['#2a0f17','#130509','#fda4af'],  // dark red/rose
  ['#222428','#0b0c0e','#fbbf24'],  // near-black/gold
  ['#0f2a3a','#05121b','#67e8f9'],  // navy/cyan
  ['#28220c','#110e05','#fde047'],  // olive/yellow
];
```
Apply as `background: linear-gradient(165deg, ${g[0]}, ${g[1]})` on the poster thumbnail.  
Use `g[2]` as the title color over the poster.

### HDR badge colors
| Type | CSS var | Label |
|---|---|---|
| Dolby Vision | `--dovi` `#b794f6` | DoVi |
| HDR10+ | `--warn` `#fbbf24` | HDR10+ |
| HDR10 | `--info` `#5ca8fb` | HDR10 |
| SDR | `--faint` | SDR |

### Poster status colors
| Status | Color |
|---|---|
| Deployed / Approved | `--good` |
| Override | `--low` |
| Review pending | `--gold` |
| No poster | `--bad` |

---

## 2. Route Map

```
src/routes/
├── +layout.svelte          ← AppShell (sidebar + header)
├── +page.svelte            ← redirect to /dashboard
├── dashboard/
│   └── +page.svelte        ← Dashboard
├── films/
│   ├── +page.svelte        ← Film library (list + grid)
│   └── [id]/
│       └── +page.svelte    ← Film detail (tabbed hub)
├── shows/
│   └── +page.svelte        ← Shows (same pattern as Films)
├── pipeline/
│   └── +page.svelte        ← Review queue
├── taste/
│   └── +page.svelte        ← Taste map
├── hdr/
│   └── +page.svelte        ← HDR & Dolby Vision coverage
├── subtitles/
│   └── +page.svelte        ← Subtitles (tabbed: Inventory / Policies / Generation / Jobs)
├── letterbox/
│   └── +page.svelte        ← Letterbox pipeline
├── activity/
│   └── +page.svelte        ← Activity feed
└── settings/
    └── +page.svelte        ← Settings
```

---

## 3. Component Inventory

### Shell
| Component | Notes |
|---|---|
| `AppShell` | Outer flex container: Sidebar + Main column |
| `Sidebar` | Collapsible (stores state in localStorage). Width: ~220px expanded, ~52px collapsed. Groups: Library / Posters / Toolbox / System. Taste profile chip at the bottom. |
| `TopBar` | Breadcrumb + page subtitle + ⌘K search trigger + theme toggle + context action button |
| `CmdPalette` | Modal overlay, `⌘K` / `Esc`. Fuzzy-search over titles, features, actions. |
| `ReviewOverlay` | Full-height modal. Shows survivors + rejected posters for a single film. Approve/reject/re-run actions. |
| `Toast` | Bottom-center, animated in/out. |

### Shared UI atoms
| Component | Notes |
|---|---|
| `PosterThumb` | `aspect-ratio: 2/3`, gradient bg, title overlay, status dot, HDR badge |
| `HdrBadge` | Pill badge. Variant prop: `dovi` / `hdr10p` / `hdr10` / `sdr` |
| `StatusDot` | 7px circle, color from semantic vars |
| `ScoreBar` | Segmented bar, 8 color segments (one per scoring feature) |
| `ProgressBar` | Single-color fill bar, used for jobs |
| `TabBar` | Horizontal pill tabs with optional count badge |
| `SectionHeader` | Bold label + faint subtitle + optional right action link |
| `StatCard` | Metric card: label / big number / sub / optional bar |

### Dashboard
| Component | Notes |
|---|---|
| `TriageCards` | Auto-fill grid of attention cards (poster/HDR/subtitle/letterbox counts), left-border accent color |
| `CpuCard` | CPU sparkline chart + model/freq/temp/load |
| `GpuCard` | GPU utilization + VRAM bar + temp/power/enc |
| `SysStrip` | Row of `StatCard`s (RAM, disk, workers, uptime) |
| `RecentPicks` | `auto-fill minmax(118px,1fr)` poster grid, pending AI decisions |
| `LiveJobs` | SSE-driven job list with progress bars |
| `LibraryHealth` | Key/value list: missing posters, HDR gaps, subtitle gaps, LB candidates |

### Films / Shows
| Component | Notes |
|---|---|
| `FilmList` | Table: thumbnail / title+year+genre+res / poster status / HDR badge / subtitle / letterbox / chevron |
| `FilmGrid` | `auto-fill minmax(124px,1fr)` poster cards |
| `FilmDetailHub` | Sticky left rail (poster + metadata) + tabbed right pane |

### Film Detail Tabs
| Tab | Key Components |
|---|---|
| Poster | `ScoreBar`, feature breakdown table (8 rows), approve button |
| Video / HDR | Current vs target HDR cards, Radarr upgrade nudge |
| Subtitles | Track table (lang / source / codec / flags / size), generate button |
| Letterbox | Before/After 16:9 preview, apply crop / ignore buttons |
| Activity | Timeline of events (deployed / webhook / archived) |

### Review Overlay (Pipeline)
| Component | Notes |
|---|---|
| `SurvivorStrip` | Scrollable row of ranked poster cards, rank badge, score |
| `RejectedList` | Collapsible, grouped by rejection stage (OCR / Style / pHash) |
| `ScoreBreakdown` | Collapsible 8-row table: feature / bar / raw score / weight / contribution |

### Letterbox
A 5-stage pipeline with 5 kanban-style trays:

```
Candidates (yellow) → [analyze] → Not Letterboxed (red)
                               → Detected (blue) → [select fix + process] →
                                  Preview & Confirm (purple) → [confirm] → Done (green)
```

| Tray | Color var | Notes |
|---|---|---|
| Candidates | `--warn` | Resolution-probed suspects |
| Not Letterboxed | `--bad` | Frame analysis: no bars found |
| Detected | `--info` | Confirmed LB, awaiting fix selection (Quick=MKV tag / Permanent=re-encode) |
| Preview & Confirm | `--dovi` | Processed, show before/after |
| Done | `--good` | Fix applied |

Context-sensitive detail panel above trays updates when a film is selected.

### Subtitles Tabs
| Tab | Description |
|---|---|
| Inventory | Per-file track table, removal plan diff, apply button |
| Policies | Language-cleanup rules (keep langs list, protected flags, dry-run / apply) |
| Generation | Subgen (faster-whisper) status, language gap list, per-film generate |
| Jobs | Durable job queue, progress, restore backup / cancel |

---

## 4. API Contract

All endpoints are under `/api`. FastAPI on Python backend.  
SvelteKit `load()` functions call these; same-origin, httpOnly cookie auth.

### System
```
GET  /api/system
→ { cpu: { model, avg, cores, freq, temp, load, history[] },
    gpu: { model, util, vramUsed, vramTotal, temp, power, enc },
    ram: { used, total, pct },
    disk: { used, total, pct },
    workers: { active, queued },
    uptime: string }
```

### Films
```
GET  /api/films
→ Film[]

GET  /api/films/{id}
→ Film & { subtitleTracks: Track[], letterbox: LbState, activity: Event[] }

Film {
  id, title, year, genre, runtime, res,
  hdr: 'dovi'|'hdr10p'|'hdr10'|'sdr',
  hdrWant: same,
  poster: 'deployed'|'approved'|'override'|'review'|'missing',
  posterScore: float,
  subtitleStatus: 'ok'|'gap',
  subtitleLangs: string,
  letterboxStatus: 'none'|'candidate'|'tagged'|'unsafe',
  filePath: string
}
```

### Shows
```
GET  /api/shows
→ Show[]   (same shape as Film at series level)

GET  /api/shows/{id}
→ Show & { seasons: Season[] }
```

### Poster Pipeline
```
GET  /api/pipeline
→ { pending: Film[], decided: Film[] }

GET  /api/pipeline/{filmId}/candidates
→ { survivors: Candidate[], rejected: RejectedCandidate[] }

Candidate {
  id, rank, score: float,
  features: { style, aesthetic, cleanliness, faceAbsence,
               colorfulness, sharpness, resolution, provenance },
  imageUrl: string
}

RejectedCandidate {
  id, stage: 'OCR'|'Style'|'pHash', reason: string, detail: string
}

POST /api/pipeline/{filmId}/approve        body: { candidateId }
POST /api/pipeline/{filmId}/reject         (reject all, keep current)
POST /api/pipeline/{filmId}/override       body: { candidateId }
POST /api/pipeline/{filmId}/rerun
```

### Taste Map
```
GET  /api/taste
→ { exemplars: Exemplar[], candidates: Exemplar[] }

Exemplar { id, title, x: float, y: float, genre, cluster: int, isCandidate, rank }
```

### HDR
```
GET  /api/hdr
→ { films: Film[], distribution: { dovi: int, hdr10p: int, hdr10: int, sdr: int } }

POST /api/hdr/{filmId}/request-upgrade     (flags Radarr for upgrade search)
```

### Subtitles
```
GET  /api/subtitles/inventory/{filmId}
→ { tracks: Track[], removalPlan: DiffLine[] }

Track { n, lang, source: 'Embedded'|'External', codec, kind, size, flags }

GET  /api/subtitles/gaps
→ { films: (Film & { wantLang: string })[] }

POST /api/subtitles/{filmId}/generate      body: { lang }

GET  /api/subtitles/policies
→ Policy[]

POST /api/subtitles/policies               body: Policy
POST /api/subtitles/policies/{id}/audit    → DryRunResult
POST /api/subtitles/policies/{id}/apply
POST /api/subtitles/inventory/{filmId}/apply-plan
```

### Letterbox
```
GET  /api/letterbox
→ { candidates, notLb, detected, preview, done: Film[] }

POST /api/letterbox/scan                   (queue library scan job)
POST /api/letterbox/{filmId}/analyze       (frame analysis)
POST /api/letterbox/{filmId}/skip

POST /api/letterbox/{filmId}/fix           body: { method: 'quick'|'permanent' }
POST /api/letterbox/{filmId}/confirm
DELETE /api/letterbox/{filmId}/tag         (remove MKV crop tag)
POST /api/letterbox/{filmId}/reprocess
POST /api/letterbox/{filmId}/move-to-candidate

POST /api/letterbox/batch-fix              body: { method: 'quick'|'permanent' }
POST /api/letterbox/confirm-all
POST /api/letterbox/heal                   (fix drifted tags)
```

### Jobs
```
GET  /api/jobs
→ Job[]

Job { id, op, title, status, pct: int, detail, color }

POST /api/jobs/{id}/cancel
POST /api/jobs/{id}/restore-backup
```

### Activity
```
GET  /api/activity
→ Event[]

Event { id, level: 'ok'|'info'|'warn', message, detail, ts }
```

### Settings
```
GET  /api/settings
→ Settings

POST /api/settings   body: Partial<Settings>

Settings {
  radarrUrl, radarrKey,
  sonarrUrl, sonarrKey,
  subgenUrl,
  posterOutputDir,
  keyArtEngineThreshold: float,
  qualityProfile: string,
  preferredLanguages: string[],
  ...
}
```

---

## 5. Real-time (SSE)

```
GET /api/events    (text/event-stream)
```

Event types pushed from server:

| `event:` name | Payload | Used by |
|---|---|---|
| `job.progress` | `{ id, pct, detail }` | Live Jobs panel, subtitle jobs |
| `job.done` | `{ id, status }` | Job list update |
| `system.metrics` | `{ cpu, gpu, ram }` | Dashboard CPU/GPU cards |
| `pipeline.new` | `{ filmId }` | Review queue badge |
| `letterbox.analyzed` | `{ filmId, state }` | Letterbox tray update |
| `poster.deployed` | `{ filmId }` | Film list poster status |

Consume in SvelteKit with a layout-level store:

```js
// src/lib/sse.js
import { writable } from 'svelte/store';

export const sseEvents = writable(null);

export function connectSSE() {
  const es = new EventSource('/api/events');
  ['job.progress','job.done','system.metrics',
   'pipeline.new','letterbox.analyzed','poster.deployed']
    .forEach(type => {
      es.addEventListener(type, e => {
        sseEvents.set({ type, data: JSON.parse(e.data) });
      });
    });
  return () => es.close();
}
```

---

## 6. Auth

- Cookie-based, httpOnly. Python sets the cookie on login; SvelteKit reads it in server `load()`.
- Protect all routes with a `+layout.server.js` that checks the session and redirects to `/login` if absent.
- No JWTs. No tokens in localStorage.

```js
// src/routes/+layout.server.js
export async function load({ cookies, fetch }) {
  const session = cookies.get('session');
  if (!session) throw redirect(303, '/login');
  const user = await fetch('/api/auth/me').then(r => r.json());
  return { user };
}
```

---

## 7. Animations & Transitions

| Element | Spec |
|---|---|
| Sidebar collapse | `width` transition, 180ms ease |
| Page entry | `translateY(12px) → 0`, opacity 0→1, 200ms cubic-bezier(.2,.7,.3,1) |
| ⌘K palette open | Same rise + `backdrop-filter: blur(3px)` fade, 140ms |
| Review overlay open | Rise 200ms |
| Toast | `translateX(-50%) translateY(10px) → 0`, 200ms |
| Marquee logo dots | 4 dots, `opacity` 1→.28→1, 2.4s, staggered 0 / .3s / .6s / .9s |
| Live pulse dot | `opacity` .55→1→.55, 1.6s |

---

## 8. Key UX Behaviours

1. **⌘K palette** — global keyboard shortcut, searches across film titles, features (HDR, Subtitles, Letterbox), and actions (Review queue, Settings).
2. **Sidebar collapse** — toggle button at the bottom. State persisted to `localStorage('marquee:sidebarCollapsed')`. Collapsed shows icons only (no labels).
3. **Theme toggle** — `[data-theme]` attribute on the root `<div>`. Persisted to `localStorage('marquee:theme')`.
4. **Film list/grid toggle** — persisted to `localStorage('marquee:filmMode')`.
5. **Review overlay** — triggered from Film Detail → Poster tab, and from Review Queue cards. Closes on backdrop click or ✕.
6. **Letterbox detail panel** — single shared panel above the trays. Clicking any film in any tray loads that film's detail into the panel. The panel's content and actions change based on the tray the film is in (Candidate / Not LB / Detected / Preview / Done).
7. **Job durable queue** — jobs survive server restarts. Pre-mutation backups are taken automatically; every job row has "Restore backup" and "Cancel" buttons.
8. **Taste map** — CLIP embeddings projected to 2D. Dots are library films; diamonds are current pipeline candidates; gold diamond = rank 1. Clicking a dot opens the film detail.

---

## 9. Recommended Svelte Libraries

| Need | Library |
|---|---|
| Charts (CPU/GPU sparklines) | `layerchart` or `d3` |
| Drag & drop (Taste map) | `@neodrag/svelte` |
| Virtualised long lists | `svelte-virtual-list` |
| Date formatting | `date-fns` |
| HTTP client | native `fetch` (SvelteKit's `load`) |
| SSE | native `EventSource` |
| Icons | Inline SVG (24px viewBox, stroke-based — see mockup icon set) |

---

## 10. Docker / Deployment Notes

- Multi-stage build: Node compiles SvelteKit (`npm run build`), Python serves the API.
- SvelteKit adapter: **`@sveltejs/adapter-node`**.
- Python (FastAPI + uvicorn) serves `/api/*`. SvelteKit node server serves everything else.
- Nginx (or Caddy) in front: `/api/` → uvicorn, `/api/events` with `proxy_buffering off` for SSE, everything else → SvelteKit node server.
- One container, one port exposed. Matches the Immich deployment pattern.

---

*Generated from `Marquee.dc.html` — the authoritative visual spec. Open that file in a browser for interactive reference.*
