#!/bin/sh
# Guest folder clean-up. Usage: cleanup.sh <dir> <max_days> <cap_gb>
# 1) delete files older than max_days  2) delete oldest files until size <= cap  3) remove empty folders
DIR="$1"; DAYS="$2"; CAP_GB="$3"
[ -d "$DIR" ] || { echo "no such dir: $DIR" >&2; exit 1; }
case "$DIR" in /|""|/share|/share/) echo "refusing: $DIR" >&2; exit 1;; esac
CAP_BYTES=$(( CAP_GB * 1073741824 ))
[ -n "$CAP_BYTES_OVERRIDE" ] && CAP_BYTES="$CAP_BYTES_OVERRIDE"
find "$DIR" -type f -mtime +"$DAYS" -exec rm -f {} +
used() { find "$DIR" -type f -exec stat -c '%s' {} + 2>/dev/null | awk '{s+=$1} END{print s+0}'; }
U=$(used)
if [ "$U" -gt "$CAP_BYTES" ]; then
  find "$DIR" -type f -exec stat -c '%Y %s %n' {} + | sort -n | while read -r _t S F; do
    [ "$U" -le "$CAP_BYTES" ] && break
    rm -f -- "$F" && U=$(( U - S ))
  done
fi
find "$DIR" -mindepth 1 -type d -empty -delete
echo "cleanup done: $(used) bytes left in $DIR"
