#!/usr/bin/env bash
# letterbox-v2.sh — detect & apply MKV pixel-crop tags for letterboxed media.
#
# Status: reference prototype, not application code. Keep behavior aligned with
# design/more-features/04-letterbox-cropping.md when editing.
#
# Production-grade rewrite of 04-letterbox-script.sh (design 04-letterbox §23).
# Default detection uses ffmpeg `cropdetect` (accurate, fast, no temp files);
# `--method trim` keeps the ImageMagick fallback for faint/color-cast bars.
#
# Safe by default: --detect NEVER writes; --apply applies; --remove clears.
# Operates only on regular, writable .mkv files that contain a video track.
#
# NOTE: no `set -e` — per-file failures are isolated and the batch continues.
set -uo pipefail

# ── tunables (mirror marquee config LETTERBOX_*) ───────────────────────────
NOISE_PX=4 MIN_BAR_PX=8 AGREE_PX=2 MEDIUM_SPREAD_PX=20
WINDOW=2 LIMIT=24 HDR_LIMIT=80 ROUND=2
TRIM_FUZZ=(5 15 25)

SYMMETRIC=1 JSON=0 METHOD="cropdetect" MODE="" TARGET="" APPLY_TOP="" APPLY_BOTTOM=""
SAMPLES=()   # filled after parsing (movie default vs --tv)
CROP_LIMIT="$LIMIT"

die(){ printf '❌ %s\n' "$*" >&2; exit 1; }
log(){ printf '%s\n' "$*" >&2; }

usage(){ cat >&2 <<EOF
Usage: $0 (--detect|--apply [--crop N]|--remove|--show) [options] <file|dir>
  --detect            Detect letterbox crop (READ-ONLY); print recommendation.
  --apply             Apply detected (or --crop N) pixel-crop tags top & bottom.
  --remove            Remove any pixel-crop tags.
  --show              Show current pixel dimensions & crop tags (mkvmerge -J).
Options:
  --tv                TV sampling (5,10,15 min) instead of movie (5..60 by 5).
  --method M          Detection backend: cropdetect (default) | trim.
  --cropdetect-limit N      cropdetect black threshold for SDR (default: 24).
  --cropdetect-hdr-limit N  cropdetect black threshold for HDR/PQ/HLG (default: 80).
  --crop N            Force N px crop (with --apply); skips detection.
  --symmetric         Force symmetric crop even if bars are uneven (default).
  --json              Emit one JSON object per file (detect/apply).
  -h, --help          This help.
EOF
exit 1; }

# ── parse args ─────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do case "$1" in
  --detect) MODE=detect;;  --apply) MODE=apply;;  --remove) MODE=remove;;  --show) MODE=show;;
  --tv) SAMPLES=(5 10 15);;
  --method) shift; METHOD="${1:-}";;
  --cropdetect-limit) shift; [[ "${1:-}" =~ ^[0-9]+$ ]] || die "--cropdetect-limit needs a number"; LIMIT="$1";;
  --cropdetect-hdr-limit) shift; [[ "${1:-}" =~ ^[0-9]+$ ]] || die "--cropdetect-hdr-limit needs a number"; HDR_LIMIT="$1";;
  --crop) shift; [[ "${1:-}" =~ ^[0-9]+$ ]] || die "--crop needs a number"; APPLY_TOP="$1"; APPLY_BOTTOM="$1";;
  --symmetric) SYMMETRIC=1;;
  --json) JSON=1;;
  -h|--help) usage;;
  --*) die "unknown option: $1";;
  *) [[ -z "$TARGET" ]] && TARGET="$1" || die "multiple targets given";;
esac; shift; done
[[ -n "$MODE" && -n "$TARGET" ]] || usage
[[ "$METHOD" == cropdetect || "$METHOD" == trim ]] || die "--method must be cropdetect or trim"
(( ${#SAMPLES[@]} )) || SAMPLES=( $(seq 5 5 60) )   # movie default

# ── preflight: required tools ──────────────────────────────────────────────
for bin in ffmpeg ffprobe mkvpropedit mkvmerge; do
  command -v "$bin" >/dev/null 2>&1 || die "missing required tool: $bin"
done
if [[ "$METHOD" == trim ]]; then
  command -v convert >/dev/null 2>&1 || die "--method trim needs ImageMagick 'convert'"
fi

# ── build file list (NUL-safe; mkv only) ───────────────────────────────────
FILES=()
if [[ -d "$TARGET" ]]; then
  while IFS= read -r -d '' f; do FILES+=("$f"); done \
    < <(find "$TARGET" -type f -iname '*.mkv' -print0)
elif [[ -f "$TARGET" ]]; then FILES=("$TARGET")
else die "'$TARGET' is not a file or directory"; fi
(( ${#FILES[@]} )) || die "no .mkv files found under '$TARGET'"

# ── eligibility: regular, writable, mkv w/ video track ─────────────────────
eligible(){ local f="$1"
  [[ "${f,,}" == *.mkv ]]     || { log "skip (not mkv): $f"; return 1; }
  [[ -f "$f" ]]               || { log "skip (missing): $f"; return 1; }
  if [[ "$MODE" == apply || "$MODE" == remove ]]; then
    [[ -w "$f" ]]             || { log "skip (read-only; $MODE needs write access): $f"; return 1; }
  fi
  mkvmerge -J "$f" 2>/dev/null | grep -q '"type": *"video"' \
    || { log "skip (no video track): $f"; return 1; }
}

probe_height(){ ffprobe -v error -select_streams v:0 -show_entries stream=height \
  -of csv=p=0 "$1" 2>/dev/null | tr -cd '0-9'; }
probe_duration(){ ffprobe -v error -show_entries format=duration \
  -of default=nokey=1:noprint_wrappers=1 "$1" 2>/dev/null | cut -d. -f1; }
probe_transfer(){ ffprobe -v error -select_streams v:0 -show_entries stream=color_transfer \
  -of csv=p=0 "$1" 2>/dev/null | head -n1 | tr -d '\r,'; }

cropdetect_limit_for(){ local transfer
  transfer="$(probe_transfer "$1")"
  case "${transfer,,}" in
    smpte2084|arib-std-b67) printf '%s\n' "$HDR_LIMIT";;
    *) printf '%s\n' "$LIMIT";;
  esac
}

ts_of(){ printf '%02d:%02d:00' $(( $1/60 )) $(( $1%60 )); }

# ── backend: cropdetect → echoes "TOP BOTTOM" px (or nothing on failure) ────
win_cropdetect(){ local f="$1" m="$2" fh="$3" line W H X Y
  line=$(ffmpeg -hide_banner -nostats -ss "$(ts_of "$m")" -i "$f" \
                -an -sn -t "$WINDOW" \
                -vf "cropdetect=limit=${CROP_LIMIT}:round=${ROUND}:reset=1" \
                -f null - 2>&1 | grep -oE 'crop=[0-9]+:[0-9]+:[0-9]+:[0-9]+' | tail -n1)
  [[ -n "$line" ]] || return 1
  IFS=: read -r W H X Y <<<"${line#crop=}"
  echo "$Y $(( fh - H - Y ))"
}

# ── backend: ImageMagick trim → median over fuzz levels ────────────────────
win_trim(){ local f="$1" m="$2" fh="$3" tmp dims H Y tops=() bots=()
  tmp=$(mktemp --suffix=.png) || return 1
  if ! ffmpeg -y -hide_banner -loglevel error -ss "$(ts_of "$m")" -i "$f" \
        -frames:v 1 -q:v 2 "$tmp" 2>/dev/null || [[ ! -s "$tmp" ]]; then
    rm -f "$tmp"; return 1; fi
  for fz in "${TRIM_FUZZ[@]}"; do
    dims=$(convert "$tmp" -fuzz "${fz}%" -trim +repage -format "%h+%Y" info: 2>/dev/null) || continue
    H="${dims%%+*}"; Y="${dims##*+}"
    [[ "$H" =~ ^[0-9]+$ && "$Y" =~ ^[0-9]+$ ]] || continue
    tops+=("$Y"); bots+=( $(( fh - H - Y )) )
  done
  rm -f "$tmp"
  (( ${#tops[@]} )) || return 1
  # median (sorted middle element)
  local st sb; st=$(printf '%s\n' "${tops[@]}" | sort -n); sb=$(printf '%s\n' "${bots[@]}" | sort -n)
  mapfile -t ST <<<"$st"; mapfile -t SB <<<"$sb"
  echo "${ST[${#ST[@]}/2]} ${SB[${#SB[@]}/2]}"
}

measure(){ if [[ "$METHOD" == trim ]]; then win_trim "$@"; else win_cropdetect "$@"; fi; }

# ── analyze one file → STATUS CONF REC_TOP REC_BOTTOM ASPECT ───────────────
analyze(){ local f="$1" fh dur bars=() tops=() bots=() zero=0 nonzero=0 noprog=0 t b bar
  fh=$(probe_height "$f"); [[ "$fh" =~ ^[0-9]+$ ]] || { STATUS=errored CONF=none; return 1; }
  dur=$(probe_duration "$f"); [[ "$dur" =~ ^[0-9]+$ ]] || dur=0
  for m in "${SAMPLES[@]}"; do
    (( dur>0 && m*60>dur )) && continue
    read -r t b < <(measure "$f" "$m" "$fh") || { log "   (measure fail @ ${m}m)"; continue; }
    [[ -z "${t:-}" || -z "${b:-}" ]] && continue
    (( t<0 )) && t=0; (( b<0 )) && b=0
    bar=$(( (t + b) / 2 )); bars+=("$bar"); tops+=("$t"); bots+=("$b")
    if (( bar <= NOISE_PX )); then zero=1; noprog=$((noprog+1)); else nonzero=1; noprog=0; fi
    (( noprog>=3 )) && break    # early stop on repeated no-bar windows
  done
  (( ${#bars[@]} )) || { STATUS=errored CONF=none; return 1; }
  local sorted mn mx med n; mapfile -t S < <(printf '%s\n' "${bars[@]}" | sort -n)
  n=${#S[@]}; mn=${S[0]}; mx=${S[n-1]}; med=${S[n/2]}
  if   (( med <= NOISE_PX ));        then STATUS=not_letterboxed CONF=none REC_TOP=0 REC_BOTTOM=0
  elif (( zero && nonzero ));        then STATUS=variable_unsafe CONF=low  REC_TOP=0 REC_BOTTOM=0
  elif (( mx - mn > AGREE_PX )); then
    STATUS=candidate REC_TOP=$mn REC_BOTTOM=$mn        # conservative: smallest bar
    (( mx-mn <= MEDIUM_SPREAD_PX )) && CONF=medium || CONF=low
  else
    STATUS=candidate CONF=high REC_TOP=$med REC_BOTTOM=$med
  fi
  ASPECT=$(awk -v fh="$fh" -v c="$REC_TOP" 'BEGIN{ e=fh-2*c; if(e>0) printf "%.2f:1",(16.0/9.0)*fh/e; else print "?"}')
}

apply_tags(){ mkvpropedit "$1" --edit track:v1 \
  --set pixel-crop-top="$2" --set pixel-crop-bottom="$3" \
  --set pixel-crop-left=0 --set pixel-crop-right=0 >/dev/null 2>&1; }
remove_tags(){ mkvpropedit "$1" --edit track:v1 \
  --delete pixel-crop-top --delete pixel-crop-bottom \
  --delete pixel-crop-left --delete pixel-crop-right >/dev/null 2>&1 || true; }

# ── main loop ──────────────────────────────────────────────────────────────
for f in "${FILES[@]}"; do
  eligible "$f" || continue
  limit_note=""
  if [[ "$METHOD" == cropdetect ]]; then
    CROP_LIMIT="$(cropdetect_limit_for "$f")"
    limit_note=" limit=$CROP_LIMIT"
  fi
  case "$MODE" in
    show) log "🔎 $f"; mkvmerge -J "$f" | grep -iE 'crop|pixel_dimensions|display_dimensions' || log "   (no crop info)";;
    remove) log "🧹 $f"; remove_tags "$f"; log "   ✅ crop tags cleared";;
    detect|apply)
      SECONDS=0
      log "🔍 $f  [method=$METHOD${limit_note}]"
      if ! analyze "$f"; then log "   ⚠️ detection failed"; continue; fi
      log "   status=$STATUS confidence=$CONF crop(top/bottom)=${REC_TOP:-0}/${REC_BOTTOM:-0} (${ASPECT:-?})  ⏱ ${SECONDS}s"
      (( JSON )) && printf '{"file":"%s","method":"%s","status":"%s","confidence":"%s","top":%s,"bottom":%s,"aspect":"%s"}\n' \
                    "$f" "$METHOD" "$STATUS" "$CONF" "${REC_TOP:-0}" "${REC_BOTTOM:-0}" "${ASPECT:-?}"
      if [[ "$MODE" == apply ]]; then
        top="${APPLY_TOP:-$REC_TOP}" bot="${APPLY_BOTTOM:-$REC_BOTTOM}"
        if [[ "$STATUS" == variable_unsafe ]]; then log "   ⛔ unsafe (16:9 scenes present) — not applied"; continue; fi
        if (( ${top:-0} <= NOISE_PX )); then log "   ℹ️ not letterboxed — nothing to apply"; continue; fi
        if apply_tags "$f" "$top" "$bot"; then log "   ✅ applied ${top}/${bot}px"; else log "   ❌ mkvpropedit failed"; fi
      fi;;
  esac
done
