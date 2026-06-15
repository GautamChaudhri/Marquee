#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<EOF
Usage: $0 [--tv-detect-crop | --movie-detect-crop | --show | --vcrop <amount> | --tv | --movie | --remove-tag] <file-or-directory>
Options:
  --tv-detect-crop     Run the crop-detection pass for TV shows (samples at 5, 10, 15 minutes)
  --movie-detect-crop  Like --tv-detect-crop, but sample every 5 minutes from 00:05:00 through 01:00:00
  --show               Show original pixel dimensions and applied crop values via mkvinfo
  --vcrop <pixels>     Apply vertical crop tags (top & bottom) with the given pixel amount
  --tv                 Run tv-detect-crop → apply recommended vcrop → show (samples at 5, 10, 15 minutes)
  --movie              Like --tv, but sample every 5 minutes from 00:05:00 through 01:00:00
  --remove-tag         If crop metadata exists, remove pixel crop tags; then show verification output
  -h, --help           Show this help message and exit
EOF
    exit 1
}

# 1) Parse flags
DO_TV_DETECT_CROP=0
DO_MOVIE_DETECT_CROP=0
DO_SHOW=0
DO_VCROP=0
DO_TV=0
DO_MOVIE=0
DO_REMOVE_TAG=0
VCROP_AMOUNT=""
TARGET=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tv-detect-crop)    DO_TV_DETECT_CROP=1; shift ;;
        --movie-detect-crop) DO_MOVIE_DETECT_CROP=1; shift ;;
        --show)              DO_SHOW=1;   shift ;;
        --vcrop)
            DO_VCROP=1; shift
            if [[ $# -eq 0 ]] || [[ "$1" =~ ^- ]]; then
                echo "❌ --vcrop requires a pixel amount argument"
                usage
            fi
            VCROP_AMOUNT="$1"; shift
            ;;
        --tv)                DO_TV=1;     shift ;;
        --movie)             DO_MOVIE=1;  shift ;;
        --remove-tag)        DO_REMOVE_TAG=1; shift ;;
        -h|--help)           usage ;;
        *)
            if [[ -z "$TARGET" ]]; then
                TARGET="$1"; shift
            else
                usage
            fi
            ;;
    esac
done

# 2) Validate: exactly one primary mode must be chosen
if (( DO_TV_DETECT_CROP + DO_MOVIE_DETECT_CROP + DO_SHOW + DO_VCROP + DO_TV + DO_MOVIE + DO_REMOVE_TAG != 1 )) || [[ -z "$TARGET" ]]; then
    usage
fi

# Internal flow switches (keep core logic the same)
RUN_DETECT=0
MOVIE_SAMPLING=0

# If --tv (full workflow for TV), enable tv-detect-crop → vcrop → show
if (( DO_TV )); then
    RUN_DETECT=1
    DO_VCROP=1
    DO_SHOW=1
fi

# If --movie (full workflow for movies), enable detect → vcrop → show with extended sampling
if (( DO_MOVIE )); then
    RUN_DETECT=1
    DO_VCROP=1
    DO_SHOW=1
    MOVIE_SAMPLING=1
fi

# If --movie-detect-crop, only detection with extended sampling (no vcrop/show)
if (( DO_MOVIE_DETECT_CROP )); then
    RUN_DETECT=1
    MOVIE_SAMPLING=1
fi

# If --tv-detect-crop, only detection with TV sampling (no vcrop/show)
if (( DO_TV_DETECT_CROP )); then
    RUN_DETECT=1
fi

# 3) Build file list from target
if [[ -d "$TARGET" ]]; then
    mapfile -t FILES < <(find "$TARGET" -type f -iname '*.mkv')
elif [[ -f "$TARGET" ]]; then
    FILES=( "$TARGET" )
else
    echo "❌ '$TARGET' is not a file or directory"
    exit 1
fi

# Helper: choose sampling minutes
build_minutes() {
    if (( MOVIE_SAMPLING )); then
        # Movie sampling → every 5 minutes from 5 to 60 inclusive
        MINUTES=( $(seq 5 5 60) )
    else
        # TV sampling → 5, 10, 15 minutes
        MINUTES=(5 10 15)
    fi
}

# Assoc map to carry per-file crop into the vcrop step
declare -A VCROP_MAP

# 4) Detection logic for both TV and Movie detection modes (and full workflows)
if (( RUN_DETECT )); then
    build_minutes

    declare -A counts
    for file in "${FILES[@]}"; do
        echo
        echo "🔍 Processing: $file"
        base=$(basename "$file" .mkv)
        unset per_counts; declare -A per_counts

        # (Optional) get duration in seconds; used only for safe skipping
        dur_s=$(ffprobe -v error -show_entries format=duration -of default=nokey=1:noprint_wrappers=1 "$file" || echo 0)
        dur_s=${dur_s%.*}

        for M in "${MINUTES[@]}"; do
            # Build a valid HH:MM:SS from total minutes (e.g., 60 -> 01:00:00)
            ts=$(printf "%02d:%02d:00" "$((M/60))" "$((M%60))")

            # Skip if requested timestamp exceeds file duration (guard for short videos)
            if [[ -n "${dur_s}" && "${dur_s}" -gt 0 ]]; then
                (( M*60 > dur_s )) && { echo "   (skip ${ts}, past duration)"; continue; }
            fi

            echo " • Extracting frame at ${ts}"
            tmpf=$(mktemp --suffix=.png)
            ffmpeg -y -hide_banner -loglevel error \
                -ss "${ts}" -i "$file" \
                -frames:v 1 -q:v 2 "$tmpf"

            for fuzz in 5 15 25; do
                dims=$(magick "$tmpf" \
                    -fuzz "${fuzz}%" -trim +repage \
                    -format "%wx%h+%X+%Y" info:)
                dims="${dims//++/+}"
                echo "    [fuzz=${fuzz}%][${M}m] → $dims"
                if [[ -n $dims ]]; then
                    counts["$dims"]=$(( ${counts["$dims"]:-0} + 1 ))
                    per_counts["$dims"]=$(( ${per_counts["$dims"]:-0} + 1 ))
                fi
            done

            rm -f "$tmpf"
        done

        echo
        echo "📊 Detected crop rectangles for ${base}:"
        for d in "${!per_counts[@]}"; do
            printf "   %3d × %s\n" "${per_counts[$d]}" "$d"
        done

        # Pick best for THIS file
        best_file="" best_file_count=0
        for d in "${!per_counts[@]}"; do
            if (( per_counts["$d"] > best_file_count )); then
                best_file_count=${per_counts["$d"]}; best_file=$d
            fi
        done

        # Parse trimmed height and original height (robust), sanitize to digits, then compute crop safely
        H_raw=$(awk -F'[x+]' '{print $2}' <<< "$best_file")
        OH_raw=$(ffprobe -v error -select_streams v:0 -show_entries stream=height -of csv=p=0 "$file")

        # Sanitize to pure digits (remove spaces, CR/LF, etc.)
        H=$(printf "%s" "$H_raw" | tr -cd '0-9')
        OH=$(printf "%s" "$OH_raw" | tr -cd '0-9')

        if [[ -z "$H" || -z "$OH" ]]; then
            echo "⚠️  Could not parse heights for $file (best='$best_file', H='$H_raw', OH='$OH_raw'). Defaulting crop to 0."
            VCROP_MAP["$file"]=0
            vertical_crop_file=0
        else
            # Force base-10 and compute; clamp negatives to 0 (safety)
            vertical_crop_file=$(( (10#$OH - 10#$H) / 2 ))
            (( vertical_crop_file < 0 )) && vertical_crop_file=0
            VCROP_MAP["$file"]=$vertical_crop_file
        fi

        echo
        # Per-file vertical-only check: declare not letterboxed when vertical crop = 0
        if (( vertical_crop_file == 0 )); then
            echo "✅ ${base} is not letterboxed → no crop tags applied."
        else
            echo "✅ Recommended crop for ${base} (picked ${best_file_count}×): ${best_file}"
            echo "   Vertical crop amount (per-file): ${VCROP_MAP["$file"]}"
        fi
    done

    echo
    echo "📊 Detected crop rectangles and counts (all files):"
    for d in "${!counts[@]}"; do
        printf "   %3d × %s\n" "${counts[$d]}" "$d"
    done

    # Stop here in detect-only modes
    if (( DO_TV_DETECT_CROP )) || (( DO_MOVIE_DETECT_CROP )); then
        exit 0
    fi
fi

# 5) Apply vcrop (for full workflows or direct --vcrop)
if (( DO_VCROP )); then
    for file in "${FILES[@]}"; do
        if (( DO_TV || DO_MOVIE )); then
            amount="${VCROP_MAP[$file]:-}"
            if [[ -z "$amount" ]]; then
                echo "⚠️  No detected crop stored for $file; defaulting to 0"
                amount=0
            fi
            # NEW: In --tv/--movie, skip applying tags when not letterboxed (amount == 0)
            if (( amount == 0 )); then
                echo "ℹ️  Skipping crop tags for '$file' (not letterboxed)."
                continue
            fi
        else
            amount="$VCROP_AMOUNT"
        fi

        echo "🔧 Applying vertical crop (${amount}px) to: $file"
        mkvpropedit "$file" \
            --edit track:v1 \
            --set pixel-crop-top="${amount}" \
            --set pixel-crop-bottom="${amount}" \
            --set pixel-crop-left=0 \
            --set pixel-crop-right=0
    done
    if (( DO_VCROP && ! DO_TV && ! DO_MOVIE )); then exit 0; fi
fi

# 6) Remove crop tags if present, then show verification (same output as --show)
if (( DO_REMOVE_TAG )); then
    for file in "${FILES[@]}"; do
        echo
        echo "🧹 Checking crop tags for: $file"
        if mkvinfo "$file" | grep -qE 'Pixel crop (top|bottom|left|right)'; then
            echo "   Found crop metadata → removing"
            mkvpropedit "$file" \
              --edit track:v1 \
              --delete pixel-crop-top \
              --delete pixel-crop-bottom \
              --delete pixel-crop-left \
              --delete pixel-crop-right
            echo "   ✅ Removed pixel crop tags on track v1"
        else
            echo "   ℹ️ No crop metadata present"
        fi

        # Now print the same verification output as --show
        echo "🔎 Verification (post-remove):"
        mkvinfo "$file" \
          | sed -n '/Track type:.*video/,/Track number:/p' \
          | grep -E 'Pixel width|Pixel height|Pixel crop' || true
    done
    exit 0
fi

# 7) Show crop info
if (( DO_SHOW )); then
    for file in "${FILES[@]}"; do
        echo
        echo "🔎 Showing crop info for: $file"
        mkvinfo "$file" \
          | sed -n '/Track type:.*video/,/Track number:/p' \
          | grep -E 'Pixel width|Pixel height|Pixel crop'
    done
    exit 0
fi
