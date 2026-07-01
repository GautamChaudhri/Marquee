#!/bin/bash
# Auto-generated movie copy script
# Copies from PLUNDER → lab and biglab
# Generated: 2026-06-18

set -euo pipefail

LAB_DST_4K="/mnt/lab/movies/4K"
LAB_DST_1080P="/mnt/lab/movies/1080p"
BIGLAB_DST_4K="/mnt/biglab/movies/4K"
BIGLAB_DST_1080P="/mnt/biglab/movies/1080p"
PLUNDER_4K="/mnt/PLUNDER/Media/Movies/4K"
PLUNDER_1080P="/mnt/PLUNDER/Media/Movies/1080p"

# Create destination directories
mkdir -p "$LAB_DST_4K" "$LAB_DST_1080P" "$BIGLAB_DST_4K" "$BIGLAB_DST_1080P"

TOTAL_FILES=0
COPIED=0
FAILED=0
START_TIME=$(date +%s)

# Color output
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
CYAN="\033[36m"
RESET="\033[0m"

log() { echo -e "${CYAN}[$(date +%H:%M:%S)]${RESET} $*"; }
ok() { echo -e "${GREEN}[OK]${RESET} $*"; }
warn() { echo -e "${YELLOW}[WARN]${RESET} $*"; }
err() { echo -e "${RED}[ERROR]${RESET} $*"; }

copy_movie() {
  local src="$1" dst_dir="$2" label="$3"
  local folder_name=$(basename "$src")
  local dst="$dst_dir/$folder_name"
  
  if [ -d "$dst" ]; then
    warn "$label SKIP (exists): $folder_name"
    return 0
  fi
  
  TOTAL_FILES=$((TOTAL_FILES + 1))
  log "$label COPY [$TOTAL_FILES]: $folder_name"
  
  if rsync -av --progress "$src/" "$dst/" 2>&1 | tail -1; then
    COPIED=$((COPIED + 1))
    ok "$label DONE ($COPIED/$TOTAL_FILES): $folder_name"
  else
    FAILED=$((FAILED + 1))
    err "$label FAILED: $folder_name"
  fi
}

echo "=========================================="
echo "  MOVIE COPY — PLUNDER → lab + biglab"
echo "=========================================="
echo "  lab movies:    29"
echo "  biglab movies: 89"
echo "  Total:         118"
echo "=========================================="
echo ""

# === LAB COPIES ===
log "Starting lab copies (29 movies)..."

copy_movie "$PLUNDER_4K/The Martian (2015)" "$LAB_DST_4K" "LAB"
copy_movie "$PLUNDER_4K/Venom - Let There Be Carnage (2021)" "$LAB_DST_4K" "LAB"
copy_movie "$PLUNDER_4K/Twisters (2024)" "$LAB_DST_4K" "LAB"
copy_movie "$PLUNDER_4K/Thor (2011)" "$LAB_DST_4K" "LAB"
copy_movie "$PLUNDER_4K/Ant-Man (2015)" "$LAB_DST_4K" "LAB"
copy_movie "$PLUNDER_1080P/Million Dollar Arm (2014)" "$LAB_DST_1080P" "LAB"
copy_movie "$PLUNDER_1080P/High-Rise (2015)" "$LAB_DST_1080P" "LAB"
copy_movie "$PLUNDER_1080P/The Ides of March (2011)" "$LAB_DST_1080P" "LAB"
copy_movie "$PLUNDER_4K/Alien³ (1992)" "$LAB_DST_4K" "LAB"
copy_movie "$PLUNDER_4K/District 9 (2009)" "$LAB_DST_4K" "LAB"
copy_movie "$PLUNDER_1080P/Live Free or Die Hard (2007)" "$LAB_DST_1080P" "LAB"
copy_movie "$PLUNDER_4K/Soul (2020)" "$LAB_DST_4K" "LAB"
copy_movie "$PLUNDER_4K/Bugonia (2025)" "$LAB_DST_4K" "LAB"
copy_movie "$PLUNDER_1080P/Don't Breathe (2016)" "$LAB_DST_1080P" "LAB"
copy_movie "$PLUNDER_1080P/You'll Never Find Me (2024)" "$LAB_DST_1080P" "LAB"
copy_movie "$PLUNDER_1080P/Berserk - The Golden Age Arc II - The Battle for Doldrey (2012)" "$LAB_DST_1080P" "LAB"
copy_movie "$PLUNDER_1080P/Berserk - The Golden Age Arc I - The Egg of the King (2012)" "$LAB_DST_1080P" "LAB"
copy_movie "$PLUNDER_1080P/Berserk - The Golden Age Arc III - The Advent (2013)" "$LAB_DST_1080P" "LAB"
copy_movie "$PLUNDER_4K/Avatar Aang - The Last Airbender (2026)" "$LAB_DST_4K" "LAB"
copy_movie "$PLUNDER_1080P/Glorious (2022)" "$LAB_DST_1080P" "LAB"
copy_movie "$PLUNDER_1080P/The Blair Witch Project (1999)" "$LAB_DST_1080P" "LAB"
copy_movie "$PLUNDER_1080P/The Dark and the Wicked (2020)" "$LAB_DST_1080P" "LAB"
copy_movie "$PLUNDER_1080P/Primer (2004)" "$LAB_DST_1080P" "LAB"
copy_movie "$PLUNDER_1080P/Code 3 (2025)" "$LAB_DST_1080P" "LAB"
copy_movie "$PLUNDER_1080P/Mad God (2021)" "$LAB_DST_1080P" "LAB"
copy_movie "$PLUNDER_4K/War for the Planet of the Apes (2017)" "$LAB_DST_4K" "LAB"
copy_movie "$PLUNDER_4K/Sonic the Hedgehog 3 (2024)" "$LAB_DST_4K" "LAB"
copy_movie "$PLUNDER_1080P/Idiocracy (2006)" "$LAB_DST_1080P" "LAB"
copy_movie "$PLUNDER_4K/Free Guy (2021)" "$LAB_DST_4K" "LAB"

# === BIGLAB COPIES ===
log "Starting biglab copies (89 movies)..."

copy_movie "$PLUNDER_4K/Hardcore Henry (2015)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_4K/The Other Lamb (2020)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_1080P/Black Mountain Side (2016)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/The Lodge (2020)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_4K/Alien - Covenant (2017)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_1080P/The Strangers (2008)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_4K/Phone Booth (2003)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_4K/Prometheus (2012)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_1080P/Synchronic (2020)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_4K/The Punisher - One Last Kill (2026)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_1080P/The Imitation Game (2014)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/Enemy (2014)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/Psych 3 - This Is Gus (2021)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/Mandy (2018)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/The Lego Movie (2014)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/Psych - The Movie (2017)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/Monsters (2010)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/Safety Not Guaranteed (2012)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_4K/Spider-Man - Homecoming (2017)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_1080P/Psych 2 - Lassie Come Home (2020)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/28 Weeks Later (2007)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/Coherence (2014)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/Tucker and Dale vs. Evil (2010)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/Abigail (2024)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/The Invitation (2016)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/Better Watch Out (2017)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/The Guest (2014)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/Kiss Kiss Bang Bang (2005)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/28 Days Later (2002)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/True Grit (2010)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/Spotlight (2015)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/Killer Klowns from Outer Space (1988)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/Insomnia (1997)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/True History of the Kelly Gang (2019)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/Mountainhead (2025)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/Office Space (1999)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/Daddy's Head (2024)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/Stopmotion (2024)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/The Curious Case of Benjamin Button (2008)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/Birdman or (The Unexpected Virtue of Ignorance) (2014)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_4K/Ne Zha 2 (2025)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_1080P/Good Time (2017)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/Memento (2000)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_4K/Ant-Man and the Wasp (2018)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_4K/Significant Other (2022)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_1080P/The Big Short (2015)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_4K/Victoria (2015)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_1080P/The Girl with the Dragon Tattoo (2011)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_4K/Hokum (2026)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_4K/Thor - The Dark World (2013)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_4K/The Damned (2025)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_1080P/The Rover (2014)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_4K/Candyman (2021)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_1080P/This Is the End (2013)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_4K/Black Panther (2018)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_4K/Finding Nemo (2003)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_1080P/Bone Tomahawk (2015)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/Scarface (1983)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_4K/Shelby Oaks (2025)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_1080P/Palm Springs (2020)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_4K/Donnie Darko (2001)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_4K/Ash (2025)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_4K/Despicable Me 4 (2024)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_1080P/Flow (2024)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/Quarantine (2008)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/The Place Beyond the Pines (2013)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_4K/Apartment 7A (2024)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_4K/Big Hero 6 (2014)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_4K/Oddity (2024)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_4K/Run (2020)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_1080P/The Road (2009)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_4K/The Plague (2025)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_1080P/The Void (2016)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/The Death of Stalin (2017)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_4K/Ex Machina (2015)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_4K/Mission - Impossible (1996)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_4K/Deadpool (2016)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_1080P/Identity (2003)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_1080P/Jackie Brown (1997)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_4K/Possessor (2020)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_4K/The Angry Birds Movie (2016)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_4K/The Wild Robot (2024)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_1080P/The Hateful Eight (2015)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_4K/Prospect (2018)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_4K/Zootopia 2 (2025)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_4K/Smile (2022)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_4K/Presence (2025)" "$BIGLAB_DST_4K" "BIGLAB"
copy_movie "$PLUNDER_1080P/Death Proof (2007)" "$BIGLAB_DST_1080P" "BIGLAB"
copy_movie "$PLUNDER_4K/Finding Dory (2016)" "$BIGLAB_DST_4K" "BIGLAB"

# === SUMMARY ===
END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))
echo ""
echo "=========================================="
echo "  COPY COMPLETE"
echo "=========================================="
echo "  Total attempted: $TOTAL_FILES"
echo "  Copied:          $COPIED"
echo "  Failed:          $FAILED"
echo "  Elapsed:         ${ELAPSED}s"
echo "=========================================="

# Verify counts
echo "Expected lab:    29 movies"
echo "Expected biglab: 89 movies"
echo ""
echo "Lab 4K count:     $(ls "$LAB_DST_4K" 2>/dev/null | wc -l)"
echo "Lab 1080p count:  $(ls "$LAB_DST_1080P" 2>/dev/null | wc -l)"
echo "Biglab 4K count:  $(ls "$BIGLAB_DST_4K" 2>/dev/null | wc -l)"
echo "Biglab 1080p count: $(ls "$BIGLAB_DST_1080P" 2>/dev/null | wc -l)"
echo ""
echo "Lab usage:        $(du -sh /mnt/lab/movies/ 2>/dev/null | cut -f1)"
echo "Biglab usage:     $(du -sh /mnt/biglab/movies/ 2>/dev/null | cut -f1)"