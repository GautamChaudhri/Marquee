# Movie Selection Plan — Phase 2 (Additional ~300 GB)
#
# Goal: Add ~300 GB of diverse movies to /mnt/lab to expand the writeable
# stress-test library. These complement the 23 movies (~487 GB) already in
# Phase 1, targeting attribute gaps and increasing test coverage breadth.
#
# Date: 2026-06-18
# Status: 🔬 RESEARCH COMPLETE — 18 movies (~299 GB) selected, awaiting copy.
# Prerequisite: Storage must be increased (currently 487G/500G used on /mnt/lab).

---

## Data Sources

- DB: 473 movies with active media files (Phase 1 used 23)
- 403 movies have resolved paths on /mnt/PLUNDER
- 55 movies probed (ffprobe) for subtitle/audio attributes
- Phase 1 coverage analyzed for gaps
- PLUNDER library: ~8,700 GB total (380 4K movies, 97 1080p movies)

---

## Gaps vs Phase 1 Coverage

Phase 1 (23 movies) covers these variations — gaps are noted:

| Variation | Phase 1 Status | Gap |
|---|---|---|
| Embedded subs: many languages (30+) | Covered (3 movies: 40, 40, 41) | Need bitmap-only massive, text-only massive |
| Embedded subs: few (1-2) | Covered (4 movies) | Need more variety |
| Embedded subs: text-only (subrip) | Covered (4 movies) | Add text-only with forced subs |
| Embedded subs: bitmap-only (PGS) | Covered (2 movies) | Add massive bitmap-only |
| Embedded subs: mixed text+bitmap | Covered (4 movies) | Add extreme counts (50+) |
| Embedded subs: ASS codec | Covered (1) | OK |
| Embedded subs: mov_text (MP4) | Covered (1) | OK |
| Embedded subs: forced tracks | Covered (3 movies) | Add more forced + text-only combos |
| Embedded subs: SDH tracks | Covered (1) | OK |
| No embedded subs + no external | Covered (5) | Add more |
| No embedded subs, has external .srt | Covered (3) | Add 4K MP4 + external SRT |
| Container: MP4 | Covered (6) | Add 4K MP4, 1080p MP4 with ext subs |
| Container: MKV | Primary path | OK |
| Letterbox: detected, significant crop | Covered (5) | Add extreme crop (384px), unique crops (42px) |
| Letterbox: detected, crop=(0,0) | Covered (2) | OK |
| Letterbox: not candidate | Covered (8) | OK |
| Audio: single language (eng) | Covered | OK |
| Audio: multi-language (3+ tracks) | Covered (Arrival 11) | Add 5-track, 9-track variants |
| Audio: non-English (Korean, Japanese, German) | Covered (3) | Add Russian-language variant |
| Resolution: 4K | Covered (15) | Add more |
| Resolution: 1080p | Covered (8) | Add small 1080p, edge cases |
| Size: large (>30 GB) | Covered (6) | Add one more large |
| Size: small (<10 GB) | Covered (3) | Add ultra-small (3-7 GB) |
| Previously pipeline-tested | Covered (6) | All new = cold start |

---

## Selected Movies (18 movies, ~299 GB)

| # | ID | Title | Size | Res | Container | Subs | Codecs | Forced | Audio | Letterbox | Why this one |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 174 | Overlord | 14.9 GB | 4K | MKV | 58 | bitmap+text | 0 | eng (2) | crop=(280,280) | **Record sub count** (58), mixed codec, moderate size |
| 2 | 131 | Elysium | 53.2 GB | 4K | MKV | 43 | **bitmap only** | 0 | eng (2) | crop=(280,280) | Massive bitmap-only, large I/O stress |
| 3 | 259 | Drop | 19.0 GB | 4K | MKV | 41 | **text only** | 0 | eng (3) | crop=(276,276) | Massive text-only, 3 audio tracks |
| 4 | 300 | Superman | 23.1 GB | 4K | MKV | 36 | text only | **1** | eng (1) | crop=(68,68) | Text-only massive + forced sub, unique crop |
| 5 | 413 | Die Hard 2 | 23.3 GB | 4K | MKV | 28 | text only | **1** | eng (2) | crop=(278,278) | Text-only, forced, dual audio |
| 6 | 280 | How to Train Your Dragon | 18.3 GB | 4K | MKV | 5 | bitmap only | 0 | **chi,eng (5)** | crop=(70,70) | **5 audio tracks**, Chinese+English, unique crop |
| 7 | 106 | 28 Days Later | 7.1 GB | **1080p** | MKV | 37 | mixed | 0 | eng (2) | crop=(20,20) | 1080p massive subs, small file, unique crop |
| 8 | 488 | Glass Onion | 20.7 GB | 4K | MKV | 44 | text only | 0 | eng (1) | crop=(42,42) | Text-only massive, **unique crop=42** |
| 9 | 285 | 28 Years Later | 21.4 GB | 4K | MKV | 23 | text only | **1** | eng (1) | **crop=(384,384)** | **Extreme letterbox crop**, forced sub |
| 10 | 82 | The Hunt | 18.3 GB | 4K | MKV | 10 | mixed | 0 | eng (1) | crop=(278,278) | **5 external SRTs**, moderate subs |
| 11 | 241 | Significant Other | 8.8 GB | 4K | MKV | 3 | text only | 0 | **eng,rus (2)** | crop=(42,42) | **Russian audio variant**, small |
| 12 | 99 | Uncut Gems | 16.8 GB | **4K** | **MP4** | **0** | — | 0 | eng (1) | prefilter_skip | **4K MP4 + external SRT**, no embedded |
| 13 | 114 | Bad Times at the El Royale | 16.3 GB | 1080p | **MP4** | 0 | — | 0 | eng (1) | prefilter_skip | 1080p MP4 **+ external SRT** |
| 14 | 390 | Captain America: The Winter Soldier | 16.0 GB | 1080p | **MP4** | 0 | — | 0 | eng (1) | prefilter_skip | 1080p MP4 **+ external SRT** |
| 15 | 199 | The Blair Witch Project | 3.4 GB | 1080p | MKV | 1 | text only | 0 | eng (1) | not_letterboxed | **Ultra-small**, minimal subs, fast iteration |
| 16 | 207 | The Imitation Game | 5.0 GB | 1080p | MKV | 1 | text only | 0 | eng (1) | crop=(140,140) | Small, minimal subs, letterbox crop |
| 17 | 447 | The Lego Movie | 5.7 GB | 1080p | MKV | 2 | text only | 0 | eng (1) | crop=(140,140) | Small, minimal subs, unique aspect |
| 18 | 190 | Spotlight | 7.3 GB | 1080p | MKV | 3 | text only | 0 | eng (1) | crop=(20,20) | 1080p, few subs, unique crop |

**Total: 18 movies, ~299 GB**

---

## Coverage Matrix (endpoint families vs new movies)

| Endpoint family | New movies that exercise it |
|---|---|
| **Sync / Library** | All 18 — additional library browse/detail/pagination stress |
| **Pipeline (poster)** | Elysium, Overlord, 28 Years Later — diverse poster styles, extreme crops |
| **Subtitle inspect** | All 15 with embedded subs — codec diversity (bitmap-only, text-only, mixed) |
| **Subtitle preview (text)** | Overlord (mixed), Drop (text-only), Superman (text+forced), Glass Onion |
| **Subtitle preview (bitmap)** | Elysium, How to Train Your Dragon — bitmap-only verification |
| **Subtitle plan (remove)** | Overlord (58 tracks!), Glass Onion (44) — policy stress on massive track lists |
| **Subtitle plan (embed)** | Uncut Gems (4K MP4 + ext SRT), Bad Times at El Royale, Winter Soldier — embedding workflow |
| **Subtitle generation** | Uncut Gems (4K MP4/0), Bad Times (1080p MP4/0), Winter Soldier (1080p MP4/0) |
| **Subtitle policies (audit)** | Overlord (58), Glass Onion (44), Drop (41) — blacklist/whitelist on massive lang sets |
| **Subtitle policies (apply)** | All with embedded subs — mutation on writeable media with diverse codecs |
| **Letterbox detect** | 28 Years Later (crop=384!), Overlord (280), Elysium (280), Glass Onion (42) |
| **Letterbox preview** | 28 Years Later — extreme visible difference; Glass Onion (42) — subtle difference |
| **Letterbox apply** | 28 Years Later, Overlord, Drop, Superman — MKV tag write test |
| **Letterbox false positive** | Blair Witch Project — not_letterboxed status |
| **Letterbox variable unsafe** | None in this batch (Oppenheimer excluded — too large) |
| **Feedback (deploy)** | Any pipeline-run movie — deploy poster to writeable folder |
| **Webhook restore** | Any — simulate Radarr upgrade + poster/subtitle restore |
| **Media job queue** | All — sequence jobs, verify concurrency on larger library |
| **MP4 adapter** | Uncut Gems (4K MP4), Bad Times (1080p MP4), Winter Soldier (1080p MP4) |
| **Multi-audio** | How to Train Your Dragon (5 tracks: chi,eng,...), Significant Other (eng+rus) |
| **Non-English audio** | Significant Other (Russian track), How to Train Your Dragon (Chinese track) |
| **Forced subs** | Superman (1 forced), Die Hard 2 (1 forced), 28 Years Later (1 forced) |
| **External subtitles** | The Hunt (5 external SRTs!), Uncut Gems (ext SRT), Bad Times (ext SRT), Winter Soldier (ext SRT) |
| **Small file fast iteration** | Blair Witch (3.4G), Imitation Game (5.0G), Lego Movie (5.7G), 28 Days Later (7.1G), Spotlight (7.3G) |

---

## Unique Attributes Added (vs Phase 1)

| Attribute | Phase 1 max/range | Phase 2 adds |
|---|---|---|
| Max subtitle tracks | 41 (Catch Me If You Can) | **58** (Overlord) |
| Max audio tracks | 11 (Arrival) | **9** (Oppenheimer excluded), **5** (HTTYD) |
| Largest letterbox crop | 277 (Arrival) | **384** (28 Years Later) |
| Smallest letterbox crop | 0 (false positives) | **20, 42** (new unique values) |
| Most external SRTs | 1 per movie | **5** (The Hunt) |
| 4K MP4 container | 6 (Phase 1, all 1080p or 4K) | **1 new 4K MP4** (Uncut Gems) |
| Non-English audio primary | Korean, Japanese, German | **Russian** (Significant Other), **Chinese** (HTTYD) |
| Small files for fast iteration | 5.7 GB (In the Mouth of Madness) | **3.4 GB** (Blair Witch) |

---

## Final Directory Structure (to create under `/mnt/lab/movies/`)

```
/mnt/lab/movies/
├── 4K/
│   ├── (existing Phase 1 movies)...
│   ├── Overlord (2018)/
│   │   └── ...mkv                                                                        (14.9 GB, 58 subs)
│   ├── Elysium (2013)/
│   │   └── ...mkv                                                                        (53.2 GB, 43 subs bitmap-only)
│   ├── Drop (2025)/
│   │   └── ...mkv                                                                        (19.0 GB, 41 subs text-only)
│   ├── Superman (2025)/
│   │   └── ...mkv                                                                        (23.1 GB, 36 subs, forced)
│   ├── Die Hard 2 (1990)/
│   │   └── ...mkv                                                                        (23.3 GB, 28 subs, forced)
│   ├── How to Train Your Dragon (2025)/
│   │   └── ...mkv                                                                        (18.3 GB, 5 subs, 5 audio)
│   ├── Glass Onion - A Knives Out Mystery (2022)/
│   │   └── ...mkv                                                                        (20.7 GB, 44 subs text-only)
│   ├── 28 Years Later (2025)/
│   │   └── ...mkv                                                                        (21.4 GB, crop=384)
│   ├── The Hunt (2020)/
│   │   ├── ...mkv                                                                        (18.3 GB, 10 subs)
│   │   └── *.en.srt, *.es.srt, ...                                                       (5 external SRTs)
│   ├── Significant Other (2022)/
│   │   └── ...mkv                                                                        (8.8 GB, eng+rus audio)
│   └── Uncut Gems (2019)/
│       ├── ...mp4                                                                        (16.8 GB, 4K MP4, 0 subs)
│       └── ...en.hi.srt                                                                  (external SRT)
│
└── 1080p/
    ├── (existing Phase 1 movies)...
    ├── 28 Days Later (2002)/
    │   └── ...mkv                                                                        (7.1 GB, 37 subs)
    ├── Bad Times at the El Royale (2018)/
    │   ├── ...mp4                                                                        (16.3 GB, MP4, 0 subs)
    │   └── ...en.srt                                                                    (external SRT)
    ├── Captain America - The Winter Soldier (2014)/
    │   ├── ...mp4                                                                        (16.0 GB, MP4, 0 subs)
    │   └── ...en.srt                                                                    (external SRT)
    ├── The Blair Witch Project (1999)/
    │   └── ...mkv                                                                        (3.4 GB, 1 sub)
    ├── The Imitation Game (2014)/
    │   └── ...mkv                                                                        (5.0 GB, 1 sub)
    ├── The Lego Movie (2014)/
    │   └── ...mkv                                                                        (5.7 GB, 2 subs)
    └── Spotlight (2015)/
        └── ...mkv                                                                        (7.3 GB, 3 subs)
```

---

## Storage Requirement

- **Current lab usage**: 487 GB / 500 GB (18 GB free)
- **Phase 2 addition**: ~299 GB
- **Required capacity**: ~786 GB minimum (~800 GB recommended for temp files)
- **Action**: Increase /mnt/lab storage before copying

---

## Next Steps

1. ⬜ Increase /mnt/lab storage to ≥800 GB
2. ⬜ Review and approve this movie list
3. ⬜ Execute the copy (18 movies, ~299 GB)
4. ⬜ Re-sync Marquee (`POST /api/sync/all`)
5. ⬜ Run comprehensive endpoint stress test against expanded library
