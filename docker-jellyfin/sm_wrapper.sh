#!/bin/bash

REAL_FFMPEG=/usr/lib/jellyfin-ffmpeg/ffmpeg.orig
AF_FILE=/opt/spacemaster/peq_af.txt
LOG=/opt/spacemaster/wrapper.log

ENGINE_BIN=""
if [ -x /usr/local/bin/sm_dsp_engine ]; then
  ENGINE_BIN=/usr/local/bin/sm_dsp_engine
elif [ -x /opt/spacemaster/sm_dsp_engine ]; then
  ENGINE_BIN=/opt/spacemaster/sm_dsp_engine
fi

if [ -f /opt/spacemaster/downmix.env ]; then
  . /opt/spacemaster/downmix.env
fi

SM_DOWNMIX=${SM_DOWNMIX:-1}
SM_FL=${SM_FL:-1.00}
SM_CENTER=${SM_CENTER:-0.707}
SM_SURR=${SM_SURR:-0.707}
SM_LFE=${SM_LFE:-0.707}
SM_MAKEUP=${SM_MAKEUP:-6}
SM_LOUD=${SM_LOUD:-0}

echo "$(date '+%F %T') WRAP args=$*" >> "$LOG"

AF=""
if [ -s "$AF_FILE" ]; then
  AF=$(cat "$AF_FILE")
fi
DELAY=""
DELAY_FILE=/opt/spacemaster/peq_delay.txt
if [ -s "$DELAY_FILE" ]; then
  DELAY=$(cat "$DELAY_FILE")
fi
BALANCE=""
BALANCE_FILE=/opt/spacemaster/peq_balance.txt
if [ -s "$BALANCE_FILE" ]; then
  BALANCE=$(cat "$BALANCE_FILE")
fi
SDELAY=""
SDELAY_FILE=/opt/spacemaster/peq_sdelay.txt
if [ -s "$SDELAY_FILE" ]; then
  SDELAY=$(cat "$SDELAY_FILE")
fi

if [ -z "$AF" ] && [ -z "$DELAY" ] && [ -z "$BALANCE" ] && [ -z "$SDELAY" ]; then
  exec "$REAL_FFMPEG" "$@"
fi

args=("$@")

for a in "$@"; do
  if [ "$a" = "-an" ]; then
    exec "$REAL_FFMPEG" "$@"
  fi
done

last_i=-1
for ((i=0;i<${#args[@]};i++)); do
  if [ "${args[i]}" = "-i" ]; then last_i=$i; fi
done
if [ $last_i -lt 0 ]; then
  exec "$REAL_FFMPEG" "$@"
fi

INPUT="${args[last_i+1]}"
CH=0; LAYOUT=""
FFPROBE_BIN=""
if [ -x /usr/lib/jellyfin-ffmpeg/ffprobe ]; then
  FFPROBE_BIN=/usr/lib/jellyfin-ffmpeg/ffprobe
elif command -v ffprobe >/dev/null 2>&1; then
  FFPROBE_BIN=ffprobe
fi
if [ -n "$FFPROBE_BIN" ]; then
  PROBE_IN="$INPUT"
  case "$PROBE_IN" in
    file:*) PROBE_IN="${PROBE_IN#file:}" ;;
  esac
  PROBE=$("$FFPROBE_BIN" -v error -select_streams a:0 -show_entries stream=channels,channel_layout -of csv=p=0 "$PROBE_IN" 2>/dev/null)
  CH=$(echo "$PROBE" | head -1 | cut -d, -f1)
  LAYOUT=$(echo "$PROBE" | head -1 | cut -d, -f2)
fi
[ -z "$CH" ] && CH=0
echo "$(date '+%F %T') WRAP input channels=$CH layout=$LAYOUT" >> "$LOG"

APPLY=0; DOWNMIX21=0; KEEP_MULTI=0
if [ "$CH" -gt 2 ]; then
  case "$SM_DOWNMIX" in
    1) APPLY=1 ;;
    2) DOWNMIX21=1 ;;
    *) KEEP_MULTI=1 ;;
  esac
fi

if [ -z "$ENGINE_BIN" ]; then
  echo "$(date '+%F %T') WRAP engine binary missing -> transparent passthrough (no DSP)" >> "$LOG"
  exec "$REAL_FFMPEG" "$@"
fi

json_escape() { local s="$1"; s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; printf '%s' "$s"; }
AF_J=$(json_escape "$AF")
DELAY_J=$(json_escape "$DELAY")
BAL_J=$(json_escape "$BALANCE")
LAY_J=$(json_escape "$LAYOUT")
SDELAY_J=$(json_escape "$SDELAY")

JSON=$(cat <<EOF
{"ch":$CH,"layout":"$LAY_J","sm_downmix":$SM_DOWNMIX,"sm_fl":$SM_FL,"sm_center":$SM_CENTER,"sm_surr":$SM_SURR,"sm_lfe":$SM_LFE,"sm_makeup":$SM_MAKEUP,"sm_loud":$SM_LOUD,"sdelay":"$SDELAY_J","peq_af":"$AF_J","delay":"$DELAY_J","balance":"$BAL_J"}
EOF
)

AF_OUT=$("$ENGINE_BIN" build-filter "$JSON" 2>>"$LOG")
rc=$?
if [ $rc -ne 0 ] || [ -z "$AF_OUT" ]; then
  echo "$(date '+%F %T') WRAP engine build-filter failed (rc=$rc) -> passthrough" >> "$LOG"
  exec "$REAL_FFMPEG" "$@"
fi
AF="$AF_OUT"
echo "$(date '+%F %T') WRAP engine build-filter ok af_len=${#AF}" >> "$LOG"

REENC=0
for ((i=0;i<${#args[@]};i++)); do
  case "${args[i]}" in
    -c:a|-c:a:*|-codec:a|-codec:a:*|-acodec|-acodec:*)
      v="${args[i+1]}"
      case "$v" in
        copy|aac|aac_latm|mp3|ac3|libmp3lame|libfaac|libfdk_aac)
          args[i+1]="eac3"
          REENC=1
          ;;
      esac
      ;;
  esac
done
if [ "$REENC" -eq 1 ]; then
  echo "$(date '+%F %T') WRAP forced audio re-encode ->eac3 for PEQ" >> "$LOG"
fi
[ -n "$AF" ] && REENC=1

case "$CH" in
  2) BR=640000 ;;
  6) BR=1536000 ;;
  *) [ "$CH" -ge 8 ] && BR=2048000 || BR=1536000 ;;
esac
EAC3_OPTS=(-b:a "$BR" -dialnorm -31 -center_mixlev 0.707 -surround_mixlev 0.707 -dmix_mode loro)
echo "$(date '+%F %T') WRAP eac3 target=${BR}bps downmix=std(-3/-3) dialnorm=off" >> "$LOG"

has_af=0
for ((i=0;i<${#args[@]};i++)); do
  if [ "${args[i]}" = "-af" ]; then has_af=1; break; fi
done

if [ $has_af -eq 1 ]; then
  for ((i=0;i<${#args[@]};i++)); do
    if [ "${args[i]}" = "-af" ]; then
      args[i+1]="${args[i+1]},${AF}"
      break
    fi
  done
  echo "$(date '+%F %T') WRAP merged AF into existing -af" >> "$LOG"
  [ "$REENC" -eq 1 ] && args+=("${EAC3_OPTS[@]}")
  if [ "$APPLY" -eq 1 ]; then
    args+=(-ac 2)
  elif [ "$DOWNMIX21" -eq 1 ]; then
    ac_set=0
    for ((i=0;i<${#args[@]};i++)); do
      case "${args[i]}" in
        -ac|-ac:a|-ac:a:*) args[i+1]="3"; ac_set=1 ;;
      esac
    done
    [ "$ac_set" -eq 0 ] && args+=(-ac 3)
  elif [ "$KEEP_MULTI" -eq 1 ]; then
    if [ "$CH" -gt 6 ]; then
      for ((i=0;i<${#args[@]};i++)); do
        case "${args[i]}" in
          -ac|-ac:a|-ac:a:*) args[i+1]="6" ;;
        esac
      done
      echo "$(date '+%F %T') WRAP keep-multi: 7.1+ source forced to 5.1(6ch)" >> "$LOG"
    else
      for ((i=0;i<${#args[@]};i++)); do
        case "${args[i]}" in
          -ac|-ac:a|-ac:a:*) [ "${args[i+1]}" = "2" ] && args[i+1]="$CH" ;;
        esac
      done
    fi
  fi
  exec "$REAL_FFMPEG" "${args[@]}"
else
  new=("${args[@]:0:last_i+2}" -af "$AF" "${args[@]:last_i+2}")
  [ "$REENC" -eq 1 ] && new+=("${EAC3_OPTS[@]}")
  if [ "$APPLY" -eq 1 ]; then
    new+=(-ac 2)
  elif [ "$DOWNMIX21" -eq 1 ]; then
    ac_set=0
    for ((i=0;i<${#new[@]};i++)); do
      case "${new[i]}" in
        -ac|-ac:a|-ac:a:*) new[i+1]="3"; ac_set=1 ;;
      esac
    done
    [ "$ac_set" -eq 0 ] && new+=(-ac 3)
  elif [ "$KEEP_MULTI" -eq 1 ]; then
    if [ "$CH" -gt 6 ]; then
      for ((i=0;i<${#new[@]};i++)); do
        case "${new[i]}" in
          -ac|-ac:a|-ac:a:*) new[i+1]="6" ;;
        esac
      done
    else
      for ((i=0;i<${#new[@]};i++)); do
        case "${new[i]}" in
          -ac|-ac:a|-ac:a:*) [ "${new[i+1]}" = "2" ] && new[i+1]="$CH" ;;
        esac
      done
    fi
  fi
  echo "$(date '+%F %T') WRAP injected AF" >> "$LOG"
  exec "$REAL_FFMPEG" "${new[@]}"
fi

