#!/bin/sh
# E-ink draw helper for the existing device refresh cadence.
set -eu
: "${IH_DEVICE_DIR:?}"
picture=${1:?}
command=${IH_EIPS:-eips}
mkdir -p "$IH_DEVICE_DIR"
case "${IH_DEVICE_CHARGING:-false}" in true) every=${MAINS_FULL_REFRESH_EVERY:-30} ;; *) every=${FULL_REFRESH_EVERY:-6} ;; esac
case "$every" in ''|*[!0-9]*|0) exit 1 ;; esac
count=$(cat "$IH_DEVICE_DIR/draw-count" 2>/dev/null || printf '%s' "$every")
case "$count" in ''|*[!0-9]*) count=$every ;; esac
if [ "$count" -ge "$every" ]; then
  "$command" -f -g "$picture" >/dev/null 2>&1
  count=0
else
  "$command" -g "$picture" >/dev/null 2>&1
fi
printf '%s\n' "$((count+1))" > "$IH_DEVICE_DIR/draw-count.new"
mv -f "$IH_DEVICE_DIR/draw-count.new" "$IH_DEVICE_DIR/draw-count"
