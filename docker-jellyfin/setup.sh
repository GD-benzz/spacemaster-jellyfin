#!/bin/bash
# spacemaster entrypoint for the OFFICIAL jellyfin/jellyfin image.
# Runs at container start: swaps Jellyfin's ffmpeg for our DSP wrapper,
# then execs the real Jellyfin. Our code lives in the mounted
# /opt/spacemaster volume (binary + wrapper + runtime config).
set -e

FFDIR=/usr/lib/jellyfin-ffmpeg
FF="$FFDIR/ffmpeg"
FF_ORIG="$FFDIR/ffmpeg.orig"
WRAP=/opt/spacemaster/sm_wrapper.sh
LOG=/opt/spacemaster/wrapper.log

if [ -f "$WRAP" ]; then
  # back up the real ffmpeg once
  if [ -f "$FF" ] && [ ! -f "$FF_ORIG" ]; then
    mv "$FF" "$FF_ORIG"
  fi
  # install our wrapper in ffmpeg's place
  cp "$WRAP" "$FF"
  chmod +x "$FF"
  echo "$(date '+%F %T') setup: ffmpeg wrapped by spacemaster" >> "$LOG"
else
  # no wrapper -> make sure stock ffmpeg is intact
  if [ ! -f "$FF" ] && [ -f "$FF_ORIG" ]; then
    mv "$FF_ORIG" "$FF"
  fi
  echo "$(date '+%F %T') setup: wrapper missing at $WRAP, running stock Jellyfin" >> "$LOG"
fi

# hand control to the real Jellyfin (official entrypoint)
exec /jellyfin/jellyfin "$@"
