# Media Selection Plan — Phase 3: 2+2 TB Labmerge Collection
## Goal
Populate `/mnt/labmerge` (mergerfs union) with ~2 TB of movies and ~2 TB of TV shows
from `/mnt/PLUNDER/Media` for comprehensive Marquee testing at scale.

## Date
2026-06-30

---

## Source Library
- **Movies**: 479 movies, 8,830 GB total (382 4K, 97 1080p)
- **TV Shows**: 110 shows, 13,598 GB total across 4K and 1080p
- **Location**: `/mnt/PLUNDER/Media`

---

## Selection Principles

This collection builds on Phases 1-2 but scales to 2 TB each for movies and TV,
prioritizing variety across every axis:

| Dimension | Movie Targets | TV Targets |
|---|---|---|
| Resolution | Mix of 4K and 1080p | Mix of 4K and 1080p |
| File size | All bands: tiny (<5 GB) through huge (50+ GB) | Small through huge shows |
| Container | MKV + MP4 | MKV (default for TV) |
| Language | English + non-English audio | English + non-English audio |
| Genre | Action, sci-fi, horror, drama, comedy, animation, documentary, foreign | Same spread |
| Era | 1968–2026 | 1960s–2026 |
| Subtitle complexity | Zero-subs, text-only, bitmap-only, mixed, forced, SDH, massive count | Varies by source |
| Seasons | N/A | 1-3 whole seasons per show (never split) |

---

## MOVIES: 155 movies, 2006.3 GB

### Size Distribution

| Band | Count | Total GB | Range |
|---|---|---|---|
| Tiny (<5 GB) | 7 | 26.0 | 2.7–5.0 GB |
| Small (5-10 GB) | 47 | 370.1 | 5.0–9.7 GB |
| Medium (10-20 GB) | 74 | 892.7 | 10.2–13.8 GB |
| Large (20-30 GB) | 18 | 369.4 | 20.1–21.1 GB |
| XL (30-50 GB) | 6 | 191.4 | 30.0–34.8 GB |
| Huge (50+ GB) | 3 | 156.7 | 51.3–53.2 GB |

| | 4K | 1080p |
|---|---|---|
| Count | 93 | 62 |
| GB | 1460.2 | 546.1 |

### Full Movie List

Paths are relative to `/mnt/PLUNDER/Media`.

#### 4K

| # | Size (GB) | Path |
|---|---|---|
| 1 | 53.2 | `Movies/4K/Elysium (2013)` |
| 2 | 52.2 | `Movies/4K/Guardians of the Galaxy Vol. 3 (2023)` |
| 3 | 51.3 | `Movies/4K/Dunkirk (2017)` |
| 4 | 34.8 | `Movies/4K/Wind River (2017)` |
| 5 | 33.4 | `Movies/4K/Prisoners (2013)` |
| 6 | 32.0 | `Movies/4K/Doctor Sleep (2019)` |
| 7 | 30.7 | `Movies/4K/No Time to Die (2021)` |
| 8 | 30.5 | `Movies/4K/Gone Girl (2014)` |
| 9 | 30.0 | `Movies/4K/Mission - Impossible - The Final Reckoning (2025)` |
| 10 | 21.1 | `Movies/4K/Sonic the Hedgehog 2 (2022)` |
| 11 | 21.1 | `Movies/4K/The Old Guard 2 (2025)` |
| 12 | 20.9 | `Movies/4K/Captain America - Civil War (2016)` |
| 13 | 20.9 | `Movies/4K/Ocean's Eleven (2001)` |
| 14 | 20.8 | `Movies/4K/Panic Room (2002)` |
| 15 | 20.7 | `Movies/4K/A Good Day to Die Hard (2013)` |
| 16 | 20.7 | `Movies/4K/Materialists (2025)` |
| 17 | 20.6 | `Movies/4K/The Hunger Games (2012)` |
| 18 | 20.5 | `Movies/4K/Under the Skin (2014)` |
| 19 | 20.3 | `Movies/4K/Pulp Fiction (1994)` |
| 20 | 20.3 | `Movies/4K/Eternity (2025)` |
| 21 | 20.3 | `Movies/4K/Alien Resurrection (1997)` |
| 22 | 20.3 | `Movies/4K/Ghosted (2023)` |
| 23 | 20.2 | `Movies/4K/Spider-Man - Across the Spider-Verse (2023)` |
| 24 | 20.2 | `Movies/4K/Underwater (2020)` |
| 25 | 20.1 | `Movies/4K/Looper (2012)` |
| 26 | 20.1 | `Movies/4K/Wake Up Dead Man - A Knives Out Mystery (2025)` |
| 27 | 20.1 | `Movies/4K/Signs (2002)` |
| 28 | 13.8 | `Movies/4K/The Black Phone (2022)` |
| 29 | 13.7 | `Movies/4K/How to Train Your Dragon - The Hidden World (2019)` |
| 30 | 13.7 | `Movies/4K/The Marvels (2023)` |
| 31 | 13.6 | `Movies/4K/F1 (2025)` |
| 32 | 13.5 | `Movies/4K/How to Train Your Dragon 2 (2014)` |
| 33 | 13.5 | `Movies/4K/Get Out (2017)` |
| 34 | 13.3 | `Movies/4K/Guy Ritchie's The Covenant (2023)` |
| 35 | 13.3 | `Movies/4K/Kill Bill - Vol. 1 (2003)` |
| 36 | 13.3 | `Movies/4K/The Report (2019)` |
| 37 | 13.2 | `Movies/4K/Alien - Romulus (2024)` |
| 38 | 13.2 | `Movies/4K/Mission - Impossible - Ghost Protocol (2011)` |
| 39 | 13.2 | `Movies/4K/Inside Out 2 (2024)` |
| 40 | 13.1 | `Movies/4K/Mission - Impossible II (2000)` |
| 41 | 13.1 | `Movies/4K/Mission - Impossible III (2006)` |
| 42 | 13.0 | `Movies/4K/Conclave (2024)` |
| 43 | 12.9 | `Movies/4K/How to Train Your Dragon (2010)` |
| 44 | 12.8 | `Movies/4K/Airplane! (1980)` |
| 45 | 12.8 | `Movies/4K/Civil War (2024)` |
| 46 | 12.8 | `Movies/4K/Pearl (2022)` |
| 47 | 12.8 | `Movies/4K/The Ministry of Ungentlemanly Warfare (2024)` |
| 48 | 12.7 | `Movies/4K/Upgrade (2018)` |
| 49 | 12.6 | `Movies/4K/They Will Kill You (2026)` |
| 50 | 12.6 | `Movies/4K/Ad Astra (2019)` |
| 51 | 12.6 | `Movies/4K/The Watchers (2024)` |
| 52 | 12.5 | `Movies/4K/Incredibles 2 (2018)` |
| 53 | 12.3 | `Movies/4K/Longlegs (2024)` |
| 54 | 12.2 | `Movies/4K/High Life (2018)` |
| 55 | 12.2 | `Movies/4K/Late Night with the Devil (2024)` |
| 56 | 12.0 | `Movies/4K/The Witch (2016)` |
| 57 | 12.0 | `Movies/4K/Men (2022)` |
| 58 | 12.0 | `Movies/4K/The Incredibles (2004)` |
| 59 | 11.9 | `Movies/4K/Ne Zha (2019)` |
| 60 | 11.9 | `Movies/4K/The Age of Disclosure (2025)` |
| 61 | 11.7 | `Movies/4K/Finding Dory (2016)` |
| 62 | 11.7 | `Movies/4K/Presence (2025)` |
| 63 | 11.5 | `Movies/4K/Smile (2022)` |
| 64 | 11.4 | `Movies/4K/Zootopia 2 (2025)` |
| 65 | 11.4 | `Movies/4K/Prospect (2018)` |
| 66 | 11.4 | `Movies/4K/The Wild Robot (2024)` |
| 67 | 11.4 | `Movies/4K/Possessor (2020)` |
| 68 | 11.4 | `Movies/4K/The Angry Birds Movie (2016)` |
| 69 | 11.2 | `Movies/4K/Deadpool (2016)` |
| 70 | 11.0 | `Movies/4K/Mission - Impossible (1996)` |
| 71 | 11.0 | `Movies/4K/Ex Machina (2015)` |
| 72 | 10.7 | `Movies/4K/The Plague (2025)` |
| 73 | 10.6 | `Movies/4K/Oddity (2024)` |
| 74 | 10.6 | `Movies/4K/Big Hero 6 (2014)` |
| 75 | 10.5 | `Movies/4K/Run (2020)` |
| 76 | 10.5 | `Movies/4K/Apartment 7A (2024)` |
| 77 | 10.4 | `Movies/4K/Despicable Me 4 (2024)` |
| 78 | 10.3 | `Movies/4K/Ash (2025)` |
| 79 | 10.2 | `Movies/4K/Donnie Darko (2001)` |
| 80 | 9.7 | `Movies/4K/Finding Nemo (2003)` |
| 81 | 9.6 | `Movies/4K/Black Panther (2018)` |
| 82 | 9.4 | `Movies/4K/Candyman (2021)` |
| 83 | 9.3 | `Movies/4K/The Damned (2025)` |
| 84 | 9.2 | `Movies/4K/Thor - The Dark World (2013)` |
| 85 | 9.2 | `Movies/4K/Hokum (2026)` |
| 86 | 9.1 | `Movies/4K/Victoria (2015)` |
| 87 | 8.8 | `Movies/4K/Significant Other (2022)` |
| 88 | 8.8 | `Movies/4K/Ant-Man and the Wasp (2018)` |
| 89 | 8.5 | `Movies/4K/Ne Zha 2 (2025)` |
| 90 | 6.0 | `Movies/4K/Spider-Man - Homecoming (2017)` |
| 91 | 4.9 | `Movies/4K/The Punisher - One Last Kill (2026)` |
| 92 | 4.2 | `Movies/4K/Alien - Covenant (2017)` |
| 93 | 3.2 | `Movies/4K/Avatar Aang - The Last Airbender (2026)` |

#### 1080p

| # | Size (GB) | Path |
|---|---|---|
| 1 | 13.5 | `Movies/1080p/2010 (1984)` |
| 2 | 13.5 | `Movies/1080p/Sunshine (2007)` |
| 3 | 13.3 | `Movies/1080p/MadS (2024)` |
| 4 | 13.0 | `Movies/1080p/Nocturnal Animals (2016)` |
| 5 | 12.9 | `Movies/1080p/The Machinist (2004)` |
| 6 | 12.8 | `Movies/1080p/Now You See Me - Now You Don't (2025)` |
| 7 | 12.5 | `Movies/1080p/The Descent (2005)` |
| 8 | 12.3 | `Movies/1080p/Zathura - A Space Adventure (2005)` |
| 9 | 11.8 | `Movies/1080p/Sorry to Bother You (2018)` |
| 10 | 11.8 | `Movies/1080p/The Neon Demon (2016)` |
| 11 | 11.8 | `Movies/1080p/The Visit (2015)` |
| 12 | 11.7 | `Movies/1080p/Die Hard (1988)` |
| 13 | 11.7 | `Movies/1080p/Death Proof (2007)` |
| 14 | 11.3 | `Movies/1080p/The Hateful Eight (2015)` |
| 15 | 11.3 | `Movies/1080p/Identity (2003)` |
| 16 | 11.2 | `Movies/1080p/Jackie Brown (1997)` |
| 17 | 10.9 | `Movies/1080p/The Death of Stalin (2017)` |
| 18 | 10.7 | `Movies/1080p/The Void (2016)` |
| 19 | 10.5 | `Movies/1080p/The Road (2009)` |
| 20 | 10.5 | `Movies/1080p/The Place Beyond the Pines (2013)` |
| 21 | 10.4 | `Movies/1080p/Quarantine (2008)` |
| 22 | 10.4 | `Movies/1080p/Flow (2024)` |
| 23 | 9.7 | `Movies/1080p/Scarface (1983)` |
| 24 | 9.7 | `Movies/1080p/Bone Tomahawk (2015)` |
| 25 | 9.4 | `Movies/1080p/This Is the End (2013)` |
| 26 | 9.3 | `Movies/1080p/The Rover (2014)` |
| 27 | 9.1 | `Movies/1080p/The Girl with the Dragon Tattoo (2011)` |
| 28 | 8.8 | `Movies/1080p/The Big Short (2015)` |
| 29 | 8.7 | `Movies/1080p/Psych 2 - Lassie Come Home (2020)` |
| 30 | 8.7 | `Movies/1080p/Good Time (2017)` |
| 31 | 8.7 | `Movies/1080p/Memento (2000)` |
| 32 | 8.5 | `Movies/1080p/Psych 3 - This Is Gus (2021)` |
| 33 | 8.4 | `Movies/1080p/Psych - The Movie (2017)` |
| 34 | 8.3 | `Movies/1080p/Birdman or (The Unexpected Virtue of Ignorance) (2014)` |
| 35 | 8.2 | `Movies/1080p/The Curious Case of Benjamin Button (2008)` |
| 36 | 8.1 | `Movies/1080p/Stopmotion (2024)` |
| 37 | 8.1 | `Movies/1080p/Daddy's Head (2024)` |
| 38 | 7.9 | `Movies/1080p/Office Space (1999)` |
| 39 | 7.9 | `Movies/1080p/Mountainhead (2025)` |
| 40 | 7.8 | `Movies/1080p/True History of the Kelly Gang (2019)` |
| 41 | 7.6 | `Movies/1080p/Insomnia (1997)` |
| 42 | 7.5 | `Movies/1080p/Killer Klowns from Outer Space (1988)` |
| 43 | 7.3 | `Movies/1080p/Spotlight (2015)` |
| 44 | 7.2 | `Movies/1080p/True Grit (2010)` |
| 45 | 7.1 | `Movies/1080p/28 Days Later (2002)` |
| 46 | 7.1 | `Movies/1080p/Kiss Kiss Bang Bang (2005)` |
| 47 | 6.8 | `Movies/1080p/The Guest (2014)` |
| 48 | 6.6 | `Movies/1080p/Better Watch Out (2017)` |
| 49 | 6.5 | `Movies/1080p/The Invitation (2016)` |
| 50 | 6.4 | `Movies/1080p/28 Weeks Later (2007)` |
| 51 | 6.4 | `Movies/1080p/Abigail (2024)` |
| 52 | 6.2 | `Movies/1080p/Tucker and Dale vs. Evil (2010)` |
| 53 | 6.2 | `Movies/1080p/Coherence (2014)` |
| 54 | 5.9 | `Movies/1080p/Monsters (2010)` |
| 55 | 5.8 | `Movies/1080p/Safety Not Guaranteed (2012)` |
| 56 | 5.7 | `Movies/1080p/The Lego Movie (2014)` |
| 57 | 5.7 | `Movies/1080p/Mandy (2018)` |
| 58 | 5.0 | `Movies/1080p/Enemy (2014)` |
| 59 | 5.0 | `Movies/1080p/The Imitation Game (2014)` |
| 60 | 3.1 | `Movies/1080p/Berserk - The Golden Age Arc III - The Advent (2013)` |
| 61 | 3.0 | `Movies/1080p/Berserk - The Golden Age Arc II - The Battle for Doldrey (2012)` |
| 62 | 2.7 | `Movies/1080p/Berserk - The Golden Age Arc I - The Egg of the King (2012)` |

---

## TV SHOWS: 71 seasons from 47 shows, 2013.3 GB

### Selection Strategy

- **1-3 whole seasons per show** — seasons never split apart
- Mix of resolutions (4K and 1080p)
- Mix of genres: drama, comedy, sci-fi, animation, action, horror, documentary
- Mix of sizes: from tiny (2.6 GB season) to huge (116 GB season)
- International content: anime (Berserk, Chainsaw Man, Attack on Titan), foreign language

### TV Show List

Paths are relative to `/mnt/PLUNDER/Media`. Each season is the complete folder.

| # | Size (GB) | Show | Res | Season | Path |
|---|---|---|---|---|---|
| 1 | 116.3 | Ted Lasso | 4K | Season 3 | `TV/4K/Ted Lasso/Season 3` |
| 2 | 86.8 | Band of Brothers | 1080p | Season 1 | `TV/1080p/Band of Brothers/Season 1` |
| 3 | 83.8 | Dark Matter (2024) | 4K | Season 1 | `TV/4K/Dark Matter (2024)/Season 1` |
| 4 | 83.6 | Watchmen | 1080p | Season 1 | `TV/1080p/Watchmen/Season 1` |
| 5 | 81.0 | Marvel's The Punisher | 4K | Season 2 | `TV/4K/Marvel's The Punisher/Season 2` |
| 6 | 69.6 | Marvel's The Punisher | 4K | Season 1 | `TV/4K/Marvel's The Punisher/Season 1` |
| 7 | 68.6 | The Man in the High Castle | 4K | Season 1 | `TV/4K/The Man in the High Castle/Season 1` |
| 8 | 60.6 | Community | 4K | Season 1 | `TV/4K/Community/Season 1` |
| 9 | 52.9 | Peacemaker | 4K | Season 1 | `TV/4K/Peacemaker/Season 1` |
| 10 | 45.8 | Stick | 4K | Season 1 | `TV/4K/Stick/Season 1` |
| 11 | 45.6 | The Falcon and The Winter Soldier | 4K | Season 1 | `TV/4K/The Falcon and The Winter Soldier/Season 1` |
| 12 | 45.4 | The Bear | 4K | Season 2 | `TV/4K/The Bear/Season 2` |
| 13 | 45.3 | Alien - Earth | 4K | Season 1 | `TV/4K/Alien - Earth/Season 1` |
| 14 | 44.7 | Devs (2020) | 4K | Season 1 | `TV/4K/Devs (2020)/Season 1` |
| 15 | 44.6 | PONIES | 4K | Season 1 | `TV/4K/PONIES/Season 1` |
| 16 | 43.8 | Daredevil - Born Again | 4K | Season 1 | `TV/4K/Daredevil - Born Again/Season 1` |
| 17 | 43.3 | Murderbot | 4K | Season 1 | `TV/4K/Murderbot/Season 1` |
| 18 | 41.1 | The Testaments | 4K | Season 1 | `TV/4K/The Testaments/Season 1` |
| 19 | 40.0 | Barry | 4K | Season 4 | `TV/4K/Barry/Season 4` |
| 20 | 38.6 | Daredevil - Born Again | 4K | Season 2 | `TV/4K/Daredevil - Born Again/Season 2` |
| 21 | 38.5 | Barry | 4K | Season 1 | `TV/4K/Barry/Season 1` |
| 22 | 37.6 | The Bear | 4K | Season 3 | `TV/4K/The Bear/Season 3` |
| 23 | 36.4 | Spider-Noir (Colorized) {tvdb-450033} | 4K | Season 1 | `TV/4K/Spider-Noir (Colorized) {tvdb-450033}/Season 1` |
| 24 | 34.2 | Moon Knight | 1080p | Season 1 | `TV/1080p/Moon Knight/Season 1` |
| 25 | 32.9 | Peacemaker | 4K | Season 2 | `TV/4K/Peacemaker/Season 2` |
| 26 | 31.7 | The Paper (2025) | 4K | Season 1 | `TV/4K/The Paper (2025)/Season 1` |
| 27 | 31.6 | Hawkeye (2021) | 4K | Season 1 | `TV/4K/Hawkeye (2021)/Season 1` |
| 28 | 25.1 | Berserk (2016) | 1080p | Season 1 | `TV/1080p/Berserk (2016)/Season 1` |
| 29 | 24.9 | Wonder Man | 4K | Season 1 | `TV/4K/Wonder Man/Season 1` |
| 30 | 23.5 | Mad Men | 1080p | Season 7 | `TV/1080p/Mad Men/Season 7` |
| 31 | 22.2 | Mad Men | 1080p | Season 2 | `TV/1080p/Mad Men/Season 2` |
| 32 | 20.8 | A Knight of the Seven Kingdoms - The Hedge Knight | 4K | Season 1 | `TV/4K/A Knight of the Seven Kingdoms - The Hedge Knight/Season 1` |
| 33 | 20.5 | Side Quest | 4K | Season 1 | `TV/4K/Side Quest/Season 1` |
| 34 | 19.4 | Legion | 1080p | Season 1 | `TV/1080p/Legion/Season 1` |
| 35 | 16.9 | Berserk (2016) | 1080p | Season 2 | `TV/1080p/Berserk (2016)/Season 2` |
| 36 | 15.5 | Modern Family | 1080p | Season 1 | `TV/1080p/Modern Family/Season 1` |
| 37 | 15.5 | Modern Family | 1080p | Season 3 | `TV/1080p/Modern Family/Season 3` |
| 38 | 15.3 | 30 Rock | 1080p | Season 5 | `TV/1080p/30 Rock/Season 5` |
| 39 | 15.1 | 30 Rock | 1080p | Season 6 | `TV/1080p/30 Rock/Season 6` |
| 40 | 14.4 | 30 Rock | 1080p | Season 1 | `TV/1080p/30 Rock/Season 1` |
| 41 | 14.3 | It's Always Sunny in Philadelphia | 1080p | Season 6 | `TV/1080p/It's Always Sunny in Philadelphia/Season 6` |
| 42 | 14.0 | New Girl | 1080p | Season 2 | `TV/1080p/New Girl/Season 2` |
| 43 | 14.0 | New Girl | 1080p | Season 1 | `TV/1080p/New Girl/Season 1` |
| 44 | 14.0 | New Girl | 1080p | Season 4 | `TV/1080p/New Girl/Season 4` |
| 45 | 13.6 | Berserk | 1080p | Season 1 | `TV/1080p/Berserk/Season 1` |
| 46 | 13.3 | Halt and Catch Fire | 1080p | Season 4 | `TV/1080p/Halt and Catch Fire/Season 4` |
| 47 | 13.0 | It's Always Sunny in Philadelphia | 1080p | Season 7 | `TV/1080p/It's Always Sunny in Philadelphia/Season 7` |
| 48 | 12.8 | Halt and Catch Fire | 1080p | Season 3 | `TV/1080p/Halt and Catch Fire/Season 3` |
| 49 | 12.6 | Primal | 1080p | Season 2 | `TV/1080p/Primal/Season 2` |
| 50 | 12.5 | Legion | 1080p | Season 2 | `TV/1080p/Legion/Season 2` |
| 51 | 12.2 | How I Met Your Mother | 1080p | Season 4 | `TV/1080p/How I Met Your Mother/Season 4` |
| 52 | 12.1 | Atlanta | 1080p | Season 3 | `TV/1080p/Atlanta/Season 3` |
| 53 | 12.0 | Animal Control | 1080p | Season 1 | `TV/1080p/Animal Control/Season 1` |
| 54 | 11.7 | Attack on Titan | 1080p | Season 1 | `TV/1080p/Attack on Titan/Season 1` |
| 55 | 11.7 | Attack on Titan | 1080p | Season 4 | `TV/1080p/Attack on Titan/Season 4` |
| 56 | 11.4 | How I Met Your Mother | 1080p | Season 8 | `TV/1080p/How I Met Your Mother/Season 8` |
| 57 | 11.4 | Atlanta | 1080p | Season 2 | `TV/1080p/Atlanta/Season 2` |
| 58 | 11.2 | How I Met Your Mother | 1080p | Season 7 | `TV/1080p/How I Met Your Mother/Season 7` |
| 59 | 11.0 | Animal Control | 1080p | Season 4 | `TV/1080p/Animal Control/Season 4` |
| 60 | 10.7 | Chainsaw Man | 1080p | Season 1 | `TV/1080p/Chainsaw Man/Season 1` |
| 61 | 10.5 | Futurama | 1080p | Season 6 | `TV/1080p/Futurama/Season 6` |
| 62 | 10.0 | Futurama | 1080p | Season 7 | `TV/1080p/Futurama/Season 7` |
| 63 | 9.9 | It's Always Sunny in Philadelphia | 1080p | Season 11 | `TV/1080p/It's Always Sunny in Philadelphia/Season 11` |
| 64 | 9.7 | Berserk - The Golden Age Arc - Memorial Edition | 1080p | Season 1 | `TV/1080p/Berserk - The Golden Age Arc - Memorial Edition/Season 1` |
| 65 | 9.6 | Twisted Metal | 1080p | Season 2 | `TV/1080p/Twisted Metal/Season 2` |
| 66 | 8.8 | The Legend of Korra | 1080p | Season 2 | `TV/1080p/The Legend of Korra/Season 2` |
| 67 | 7.6 | The Legend of Korra | 1080p | Season 4 | `TV/1080p/The Legend of Korra/Season 4` |
| 68 | 7.5 | Avatar - The Last Airbender | 1080p | Season 1 | `TV/1080p/Avatar - The Last Airbender/Season 1` |
| 69 | 7.3 | Kim's Convenience | 1080p | Season 2 | `TV/1080p/Kim's Convenience/Season 2` |
| 70 | 7.2 | Kim's Convenience | 1080p | Season 3 | `TV/1080p/Kim's Convenience/Season 3` |
| 71 | 2.6 | Dream Corp LLC | 1080p | Season 2 | `TV/1080p/Dream Corp LLC/Season 2` |

### TV Stats

| Resolution | Seasons | GB |
|---|---|---|
| 4K | 27 | 1286.0 |
| 1080p | 44 | 727.4 |

| Metric | Value |
|---|---|
| Shows | 47 |
| Seasons | 71 |
| Total GB | 2013.3 |

---

## Grand Total

| Media | Count | GB |
|---|---|---|
| Movies | 155 movies | 2006.3 |
| TV Shows | 71 seasons from 47 shows | 2013.3 |
| **Combined** | **226 items** | **4019.7** |
