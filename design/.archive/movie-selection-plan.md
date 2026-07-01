# Movie Selection Plan — Writeable Lab Stress Test
# 
# Goal: Copy a diverse set of movies from the read-only /mnt/PLUNDER to the 
# writeable /mnt/lab (500 GB) to stress-test ALL Marquee endpoints with 
# write-capable media.
#
# Date: 2026-06-16
# Status: ✅ COMPLETED — all 23 movies (442 GB) copied to /mnt/lab/movies/.
# Next step: Re-sync Marquee with `RADARR_MEDIA_PATH=/mnt/lab/movies`,
#            then run the comprehensive endpoint stress test.

---

## Data Sources

- DB: 473 movies with active media files
- 144 letterbox-detected, 329 not-candidates, 0 pending candidates (all prefiltered)
- Only 1 movie (Hereditary, id=34) had a subtitle inventory from the last read-only test
- 14 MP4 movies (all others are MKV)
- 370 4K movies, 102 1080p, 1 other
- Total library size: ~8,700 GB
- Last endpoint test (20260616T063920Z) used 20 pipeline movies + Hereditary for subtitles

---

## Variations Being Tested

| # | Variation | Why it matters |
|---|---|---|
| 1 | Embedded subs: **many languages** (30+) | Policy blacklist/whitelist testing |
| 2 | Embedded subs: **few languages** (1-2) | Basic inventory path |
| 3 | Embedded subs: **text only** (subrip/SRT) | Text preview endpoint |
| 4 | Embedded subs: **bitmap only** (PGS) | Bitmap preview limitation |
| 5 | Embedded subs: **mixed text+bitmap** | Codec diversity in inventory |
| 6 | Embedded subs: **ASS codec** | ASS parsing/handling |
| 7 | Embedded subs: **mov_text (MP4)** | MP4 subtitle adapter |
| 8 | Embedded subs: **forced tracks** | Forced-track protection testing |
| 9 | Embedded subs: **SDH tracks** | SDH handling |
| 10 | **No embedded subs** + no external | Subgen generation path |
| 11 | **No embedded subs**, has external .srt | Embedding workflow |
| 12 | Container: **MP4** | Different mutation adapter (mkvmerge vs ffmpeg) |
| 13 | Container: **MKV** | Primary path |
| 14 | Letterbox: **detected, significant crop** | Detection, preview, apply, heal |
| 15 | Letterbox: **detected, crop=(0,0)** | False positive handling |
| 16 | Letterbox: **not candidate** (prefilter:skip) | Skip path |
| 17 | Audio: **single language (eng)** | Basic case |
| 18 | Audio: **multi-language (11 tracks)** | Audio track selection |
| 19 | Audio: **non-English (Korean, Japanese, German)** | Language detection, non-eng generation |
| 20 | Resolution: **4K** | Large file I/O |
| 21 | Resolution: **1080p** | Standard file I/O |
| 22 | Size: **large (>30 GB)** | I/O stress |
| 23 | Size: **small (<10 GB)** | Fast test iteration |
| 24 | Previously pipeline-tested | Existing data reuse |
| 25 | Never pipeline-tested | Cold start path |

---

## Selected Movies (23 movies, **Option A** — drop Blade Runner 2049 & Edge of Tomorrow)

| # | ID | Title | Size | Res | Container | Subs | Codecs | Forced | Audio | Letterbox | Why this one |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 2001: A Space Odyssey | 17.4 GB | 4K | MKV | 9 | text+bitmap | 1 | eng | crop=(0,0) | forced sub + false positive letterbox |
| 2 | 2 | A Quiet Place: Day One | 14.2 GB | 4K | MKV | 40 | text+bitmap | 0 | eng | detected | massive language diversity (32 langs) |
| 3 | 3 | Alien: Romulus | 13.2 GB | 4K | MKV | 32 | text only | 1 | eng+tur | detected | 32 subs, text-only, forced, dual audio |
| 4 | 5 | Arrival | 49.6 GB | 4K | MKV | 40 | bitmap only | 1 | 11 langs | crop=(276,276) | 11 audio tracks, bitmap-only, large |
| 5 | 12 | Captain America: BNW | 16.9 GB | 4K | MKV | 38 | text+bitmap | 2 | eng | crop=(276,276) | 2 forced subs, SDH variants |
| 6 | 15 | Color Out of Space | 12.8 GB | 4K | **MP4** | **0** | — | 0 | eng | detected | MP4 + no subs + letterbox |
| 7 | 25 | Dune: Part Two | 67.8 GB | 4K | MKV | 4 | text+bitmap | 0 | eng+ita | crop=(276,276) | large, dual audio, SDH |
| 8 | 31 | Furiosa | 26.0 GB | 4K | MKV | 6 | text only | 0 | eng | detected | 6 subs, text-only, moderate |
| 9 | 36 | Hot Fuzz | 27.5 GB | 4K | MKV | 6 | **bitmap only** | 0 | eng | crop=(263,263) | single-lang bitmap, simple test |
| 10 | 52 | Moana 2 | 20.9 GB | 4K | MKV | 35 | text only | 0 | eng | detected | text-only, 35 langs, no bitmap |
| 11 | 61 | Reservoir Dogs | 11.5 GB | 4K | **MP4** | **0** | — | 0 | eng | detected | MP4 no subs + letterbox |
| 12 | 73 | Cabin in the Woods | 11.1 GB | 4K | **MP4** | **0** | — | 0 | eng | detected | MP4 no subs + letterbox |
| 13 | 148 | In the Mouth of Madness | 5.7 GB | 1080p | **MP4** | **0** | — | 0 | eng | not candidate | small MP4, no subs, **has external .srt** |
| 14 | 155 | Leave No Trace | 14.2 GB | 1080p | MKV | 5 | **ASS+SRT** | 0 | eng | not candidate | ASS codec, SDH, 1080p |
| 15 | 204 | The Game | 16.6 GB | 1080p | MKV | 2 | text only | 0 | eng | not candidate | ultra-minimal (2 subs), SDH, commentary audio |
| 16 | 219 | The Sixth Sense | 13.2 GB | 4K | **MP4** | 1 | **mov_text** | 0 | eng+spa | not candidate | only MP4 with subs, dual audio |
| 17 | 224 | The Zone of Interest | 13.5 GB | 1080p | MKV | 23 | text only | 0 | **ger** | not candidate | German audio, 1080p |
| 18 | 314 | Knives Out | 15.3 GB | 4K | **MP4** | **0** | — | 0 | eng | not candidate | MP4 no subs (generation test) |
| 19 | 338 | Catch Me If You Can | 18.7 GB | 1080p | MKV | 41 | text+bitmap | 0 | eng | not candidate | massive langs, SDH, 1080p |
| 20 | 434 | Exit 8 | 13.9 GB | 1080p | MKV | 12 | text+bitmap | 0 | **jpn** | not candidate | Japanese audio, SDH, 1080p |
| 21 | 435 | Lincoln | 19.1 GB | 1080p | MKV | **0** | — | 0 | eng | crop=(140,140) | no embedded, **has external .srt**, 1080p |
| 22 | 439 | Burning | 15.2 GB | 1080p | MKV | 4 | text+bitmap | 0 | **kor** | not candidate | Korean audio + external .srt |
| 23 | 443 | The Borderlands | 6.0 GB | 1080p | MKV | 1 | text only | 0 | eng | crop=(0,0) | false letterbox positive + single sub |

**Total: 23 movies, ~442 GB** — leaves 49 GB free for temp files during testing.

---

## Coverage Matrix (endpoint families vs selected movies)

| Endpoint family | Movies that exercise it |
|---|---|
| **Sync / Library** | All 23 — verified library browse, detail, pagination |
| **Pipeline (poster)** | 2001, Arrival, Dune 2, Hot Fuzz, Moana 2, Furiosa — diverse poster styles |
| **Subtitle inspect** | All — every combination of embedded/external subs |
| **Subtitle preview (text)** | 2001, Alien: Romulus, Leave No Trace (ASS), The Sixth Sense (mov_text) |
| **Subtitle preview (bitmap)** | Arrival, Hot Fuzz — verifies limitation message |
| **Subtitle plan (remove)** | A Quiet Place (40 tracks), Catch Me If You Can (41) — policy stress |
| **Subtitle plan (embed)** | Lincoln, In the Mouth of Madness, Burning — external SRT embedding |
| **Subtitle generation** | Color Out of Space (MP4/0), Reservoir Dogs (MP4/0), Lincoln (0 subs), Knives Out (MP4/0) |
| **Subtitle policies (audit)** | A Quiet Place + Catch Me If You Can — blacklist/whitelist on 30+ lang movies |
| **Subtitle policies (apply)** | All with embedded subs — verify mutation on writeable media |
| **Letterbox detect** | Lincoln, Dune 2, Hot Fuzz, Arrival — varied crops |
| **Letterbox preview** | Dune 2, Hot Fuzz, Arrival — large crops for visible difference |
| **Letterbox apply** | Lincoln, Arrival, Captain America — test MKV tag write |
| **Letterbox false positive** | 2001, The Borderlands — crop=(0,0) |
| **Feedback (deploy)** | Any pipeline-run movie — deploy poster to writeable folder |
| **Webhook restore** | Any — simulate Radarr upgrade + poster/subtitle restore |
| **Media job queue** | All — sequence jobs, verify concurrency |
| **MP4 adapter** | Color Out of Space, Reservoir Dogs, Cabin in the Woods, The Sixth Sense, In the Mouth of Madness, Knives Out |
| **Multi-audio** | Arrival (11 tracks), Alien: Romulus (2), Dune 2 (2), The Sixth Sense (2) |
| **Non-English audio** | Burning (kor), Exit 8 (jpn), The Zone of Interest (ger) |
| **Subtitle generation + no subs** | Color Out of Space, Reservoir Dogs, Cabin in the Woods, Knives Out, In the Mouth of Madness, Lincoln |

---

## Final Directory Structure (`/mnt/lab/movies/`)

```
/mnt/lab/movies/
├── 4K/
│   ├── 2001 - A Space Odyssey (1968)/
│   │   └── 2001 - A Space Odyssey (1968) {imdbid-tt0062622} - [Bluray-2160p - HDR + DOVI (mine)].mkv                       (17.4 GB)
│   ├── A Quiet Place - Day One (2024)/
│   │   └── A Quiet Place - Day One (2024) {imdbid-tt13433802} - [Bluray-2160p - HDR + DOVI (mine)].mkv                       (14.2 GB)
│   ├── Alien - Romulus (2024)/
│   │   └── Alien - Romulus (2024) {imdbid-tt18412256} - [WEBDL-2160p - HDR + DOVI (mine)].mkv                                (13.2 GB)
│   ├── Arrival (2016)/
│   │   └── Arrival (2016) {imdbid-tt2543164} - [Remux-2160p - HDR + DOVI (mine)].mkv                                        (49.6 GB)
│   ├── Captain America - Brave New World (2025)/
│   │   └── Captain America - Brave New World (2025) {imdbid-tt14513804} - IMAX [WEBDL-2160p - HDR + DOVI (mine) IMAX].mkv    (16.9 GB)
│   ├── Color Out of Space (2020)/    
│   │   ├── Color Out of Space (2020) {imdbid-tt5073642} - [Bluray-2160p].mp4                                                 (12.8 GB)
│   │   └── Color Out of Space (2020) {imdbid-tt5073642} - [Bluray-2160p].en.srt                                               (ext sub)
│   ├── Dune - Part Two (2024)/
│   │   └── Dune - Part Two (2024) {imdbid-tt15239678} - [Remux-2160p Proper - HDR + DOVI].mkv                                (67.8 GB)
│   ├── Furiosa - A Mad Max Saga (2024)/
│   │   └── Furiosa - A Mad Max Saga (2024) {imdbid-tt12037194} - [WEBDL-2160p Proper - HDR + DOVI (mine)].mkv                (26.0 GB)
│   ├── Hot Fuzz (2007)/
│   │   └── Hot Fuzz (2007) {imdbid-tt0425112} - [Bluray-2160p - HDR].mkv                                                     (27.5 GB)
│   ├── Knives Out (2019)/
│   │   ├── Knives Out (2019) {imdbid-tt8946378} - [Bluray-2160p - HDR + DOVI (mine)].mp4                                     (15.3 GB)
│   │   └── Knives Out (2019) {imdbid-tt8946378} - [Bluray-2160p - HDR + DOVI (mine)].en.srt                                  (ext sub)
│   ├── Moana 2 (2024)/
│   │   └── Moana 2 (2024) {imdbid-tt13622970} - [WEBDL-2160p - DOVI (mine)].mkv                                               (20.9 GB)
│   ├── Reservoir Dogs (1992)/
│   │   ├── Reservoir Dogs (1992) {imdbid-tt0105236} - [Bluray-2160p Proper - HDR + DOVI (mine)].mp4                          (11.5 GB)
│   │   └── Reservoir Dogs (1992) {imdbid-tt0105236} - [Bluray-2160p Proper - HDR + DOVI (mine)].en.srt                       (ext sub)
│   ├── The Cabin in the Woods (2012)/
│   │   ├── The Cabin in the Woods (2012) {imdbid-tt1259521} - [Bluray-2160p - HDR + DOVI (mine)].mp4                         (11.1 GB)
│   │   └── The Cabin in the Woods (2012) {imdbid-tt1259521} - [Bluray-2160p - HDR + DOVI (mine)].en.hi.srt                   (ext sub)
│   └── The Sixth Sense (1999)/
│       └── The Sixth Sense (1999) {imdbid-tt0167404} - [Bluray-2160p - HDR + DOVI].mp4                                        (13.2 GB)
│
└── 1080p/
    ├── Burning (2018)/
    │   ├── Burning (2018) {imdbid-tt7282468} - [Bluray-1080p].mkv                                                             (15.2 GB)
    │   └── Burning (2018) {imdbid- - [Bluray-1080p].en.srt                                                                   (ext sub)
    ├── Catch Me If You Can (2002)/
    │   └── Catch Me If You Can (2002) {imdbid-tt0264464} - [Bluray-1080p - HDR + DOVI (mine)].mkv                             (18.7 GB)
    ├── Exit 8 (2025)/
    │   └── Exit 8 (2025) {imdbid-tt35222590} - [Bluray-1080p].mkv                                                             (13.9 GB)
    ├── In the Mouth of Madness (1995)/
    │   ├── In the Mouth of Madness (1995) {imdbid-tt0113409} - [Bluray-1080p].mp4                                              (5.7 GB)
    │   └── In the Mouth of Madness (1995) {imdbid-tt0113409} - [Bluray-1080p].en.hi.srt                                       (ext sub)
    ├── Leave No Trace (2018)/
    │   └── Leave No Trace (2018) {imdbid-tt3892172} - [Bluray-1080p].mkv                                                       (14.2 GB)
    ├── Lincoln (2012)/
    │   ├── Lincoln (2012) {imdbid-tt0443272} - [Bluray-1080p].MKV                                                             (19.1 GB)
    │   └── Lincoln (2012) {imdbid-tt0443272} - [Bluray-1080p].en.hi.srt                                                       (ext sub)
    ├── The Borderlands (2014)/
    │   └── The Borderlands (2014) {imdbid-tt2781832} - [WEBDL-1080p].mkv                                                       (6.0 GB)
    ├── The Game (1997)/
    │   └── The Game (1997) {imdbid-tt0119174} - Remastered [Bluray-1080p].mkv                                                  (16.6 GB)
    └── The Zone of Interest (2023)/
        └── The Zone of Interest (2023) {imdbid-tt7160372} - [Bluray-1080p Proper].mkv                                          (13.5 GB)
```

---

## `.env` Update

The Marquee `.env` file at `/forge/Marquee/.env` has been updated:

| Setting | Value |
|---|---|
| `RADARR_PATH_PREFIX` | `/plunder/movies` (unchanged) |
| `RADARR_MEDIA_PATH` | **`/mnt/lab/movies`** ← writeable lab |
| `SONARR_PATH_PREFIX` | `/plunder/tv` (unchanged) |
| `SONARR_MEDIA_PATH` | `/mnt/PLUNDER/Media/TV` (unchanged — no TV in lab) |

---

## Next Steps

1. ✅ Fix `/mnt/lab` permissions (chown'd to quartermaster)
2. ✅ Review and approve movie list (Option A approved)
3. ✅ Execute the copy (23 movies, 442 GB copied)
4. ✅ Update Marquee's `.env` to point media paths at `/mnt/lab`
5. ⬜ Re-sync Marquee (`POST /api/sync/all`)
6. ⬜ Run the comprehensive endpoint stress test against writeable media
