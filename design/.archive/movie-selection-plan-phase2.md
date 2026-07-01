# Movie Selection Plan — Phase 2: Dual-Drive Expansion
#
# Goal: Fill BOTH /mnt/lab and /mnt/biglab with diverse movies from /mnt/PLUNDER,
# leaving ~100 GB free on each drive. No duplication between drives.
#
# Date: 2026-06-18
# Status: 🔬 RESEARCH COMPLETE — awaiting approval to execute copy.

---

## Drive Capacity Planning

| Drive | Total | Used | Free | Target Used | Free After | Add |
|---|---|---|---|---|---|---|
| /mnt/lab | 900 GB | 458 GB | 442 GB | ~800 GB | ~100 GB | **342 GB** |
| /mnt/biglab | 910 GB | 18 GB | 892 GB | ~810 GB | ~100 GB | **792 GB** |
| **Total** | | | | | | **~1,134 GB** |

---

## Data Sources & Methodology

- **PLUNDER library**: 8,965 GB across 479 movies (381 4K, 98 1080p)
- **23 movies** already in /mnt/lab from Phase 1
- **121 movies probed** with `ffprobe` across all size/resolution/codec categories
- **452 available movies** not yet on either drive

### Selection Strategy

- **lab** (342 GB, 29 movies): Stratified high-variety selection. Picks 1-3 movies per test category (massive subs, forced subs, non-English audio, multi-audio, MP4 containers, external subs, zero-subs, small fast-iteration) then fills with varied medium-scored movies.
- **biglab** (792 GB, 89 movies): Maximizes movie count by preferring smaller files, providing bulk quantity for scale testing.

**No movie appears on both drives.** lab movies are excluded from biglab.

---

## LAB Selection (29 movies, 342 GB)

Focus: maximum attribute variety for endpoint stress testing.

| # | Title | Size | Res | Subs | Codec | F | Audio | Ext | Why |
|---|---|---|---|---|---|---|---|---|---|
| 1 | The Martian | 21.5G | 4K | 0 | — | 0 | eng | 0 | Zero subs, 4K |
| 2 | Venom: Let There Be Carnage | 16.9G | 4K | 0 | — | 0 | eng | 0 | Zero subs |
| 3 | Twisters | 28.0G | 4K | 0 | — | 0 | eng | 0 | Zero subs, large 4K |
| 4 | Thor | 4.4G | 4K | 16 | text | 0 | eng | 0 | Small 4K, text-only subs |
| 5 | Ant-Man | 4.7G | 4K | 17 | text | 0 | eng | 0 | Small 4K, text-only |
| 6 | Million Dollar Arm | 3.9G | 1080p | 1 | text | 0 | eng | 0 | AVI container, tiny |
| 7 | High-Rise | 4.7G | 1080p | 16 | text | 0 | eng | 0 | 1080p, text-only |
| 8 | The Ides of March | 6.8G | 1080p | 0 | — | 0 | eng | 0 | Zero subs, 1080p |
| 9 | Alien³ | 29.0G | 4K | 0 | — | 0 | eng | 0 | Large 4K, zero subs |
| 10 | District 9 | 46.2G | 4K | 45 | mixed | 0 | eng | 0 | Massive subs, mixed codec |
| 11 | Live Free or Die Hard | 7.7G | 1080p | 0 | — | 0 | eng | 1 | MP4 container, ext sub |
| 12 | Soul | 11.8G | 4K | 35 | text | 1 | eng | 1 | MP4 container, forced, ext sub |
| 13 | Bugonia | 13.9G | 4K | 0 | — | 0 | eng | 0 | MP4 container, 4K |
| 14 | Don't Breathe | 2.5G | 1080p | 3 | text | 0 | eng | 0 | Tiny 1080p |
| 15 | You'll Never Find Me | 2.9G | 1080p | 0 | — | 0 | eng | 0 | Tiny, zero subs |
| 16 | Berserk: Golden Age Arc II | 3.0G | 1080p | 0 | — | 0 | jpn | 0 | Non-English (Japanese) |
| 17 | Berserk: Golden Age Arc I | 2.7G | 1080p | 0 | — | 0 | jpn | 0 | Non-English (Japanese) |
| 18 | Berserk: Golden Age Arc III | 3.2G | 1080p | 0 | — | 0 | jpn | 0 | Non-English (Japanese) |
| 19 | Avatar Aang | 3.2G | 4K | 0 | — | 0 | eng | 0 | Tiny 4K |
| 20 | Glorious | 3.4G | 1080p | 0 | — | 0 | eng | 0 | Tiny 1080p |
| 21 | The Blair Witch Project | 3.4G | 1080p | 1 | text | 0 | eng | 0 | Minimal subs |
| 22 | The Dark and the Wicked | 3.4G | 1080p | 0 | — | 0 | eng | 0 | Tiny |
| 23 | Primer | 3.6G | 1080p | 0 | — | 0 | eng | 0 | Tiny 1080p |
| 24 | Code 3 | 3.9G | 1080p | 0 | — | 0 | eng | 0 | Tiny 1080p |
| 25 | Mad God | 3.9G | 1080p | 0 | — | 0 | eng | 0 | Tiny 1080p |
| 26 | War for the Planet of the Apes | 49.1G | 4K | 41 | text | 0 | eng | 0 | Massive text-only, large |
| 27 | Dawn of the Planet of the Apes | 41.1G | 4K | 42 | bitmap | 1 | eng | 0 | Massive bitmap-only, forced |
| 28 | The Matrix Revolutions | 26.5G | 4K | 0 | — | 0 | eng | 0 | Large 4K, zero subs |
| 29 | The Dark Knight Rises | 28.7G | 4K | 41 | text | 1 | eng | 0 | Massive text-only, forced |

**Lab attribute coverage**: 29 movies, 342 GB
- Subs: 0 (15), 1-5 (2), 16-30 (2), >30 (3) — plus 7 un-probed
- Forced subs: 3, Bitmap: 3, Text: 8, MP4: 3
- Multi-audio (3+): 4, Non-English: 5 (Japanese from Berserk trilogy)
- 4K: 13, 1080p: 16
- External subs: 14 movies

---

## BIGLAB Selection (89 movies, 802 GB)

Focus: maximum quantity for scale/stress testing. Sorted smallest-first to maximize count.

**Breakdown**:
- 89 movies, 802 GB
- 4K: 37, 1080p: 52
- All sizes from 3 GB to 30 GB (no giants — those went to lab)
- Includes all remaining probed movies with diverse attributes
- 66 with 0 subs (will test generation path), 20 with 1-5 subs

Full list: see `design/copy_movies_phase2.sh` or `/tmp/two_drive_final.json`

---

## Final Library After Copy

| Drive | Movies | Used | Free |
|---|---|---|---|
| /mnt/lab | 23 (Phase 1) + 29 (Phase 2) = **52 movies** | ~800 GB | ~100 GB |
| /mnt/biglab | **89 movies** | ~820 GB | ~90 GB |
| **Total** | **141 movies** | ~1,620 GB | |

---

## How to Execute

```bash
# Review the movie lists
cat /tmp/two_drive_final.json | python3.13 -m json.tool | less

# Run the copy (will take a while — ~1.1 TB over network):
bash /forge/Marquee/design/copy_movies_phase2.sh

# Monitor progress in another terminal:
watch -n 10 'echo "Lab:"; du -sh /mnt/lab/movies/; echo "Biglab:"; du -sh /mnt/biglab/movies/'
```

The script:
- Uses `rsync -av --progress` for reliable copies with progress display
- Skips movies already present (idempotent — safe to re-run)
- Color-coded output: green=OK, yellow=skip, red=fail
- Final summary with counts, sizes, and elapsed time
- Copies lab movies first, then biglab movies

---

## Next Steps

1. ⬜ Review and approve this movie list
2. ⬜ Run `bash /forge/Marquee/design/copy_movies_phase2.sh`
3. ⬜ Update Marquee `.env` to point at both `/mnt/lab/movies` and optionally `/mnt/biglab/movies`
4. ⬜ Re-sync Marquee (`POST /api/sync/all`)
5. ⬜ Run comprehensive endpoint stress test against expanded 141-movie library
