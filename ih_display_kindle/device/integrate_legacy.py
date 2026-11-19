#!/usr/bin/env python3
"""Generate an opt-in patched copy of a verified kindle-dash script.

Does not install files or access a device. The input must be the current device
copy and the output must be a different new path; keep the original for rollback.
"""
import argparse
import os
from pathlib import Path

BOOT = r'''# IH device adapter: opt in through a device-local configuration file.
IH_DEVICE_ENABLED=false
IH_DEVICE_CONFIG="$BASE/ih-device/env.sh"
[ ! -f "$IH_DEVICE_CONFIG" ] || . "$IH_DEVICE_CONFIG"
IH_DEVICE_CLIENT=${IH_DEVICE_CLIENT:-"$BASE/ih-device/ih-device.sh"}
IH_DEVICE_DIR=${IH_DEVICE_DIR:-"$TMP/ih-device"}
IH_DEVICE_DRAW=${IH_DEVICE_DRAW:-"$BASE/ih-device/ih-draw.sh"}
ih_enabled() { [ "${IH_DEVICE_ENABLED:-false}" = true ]; }
ih_run() {
  if charging; then IH_DEVICE_CHARGING=true; else IH_DEVICE_CHARGING=false; fi
  export IH_DEVICE_CHARGING IH_DEVICE_URL IH_DEVICE_CURL_CONFIG IH_DEVICE_DIR IH_DEVICE_DRAW IH_DEVICE_WIFI_IMAGE FULL_REFRESH_EVERY MAINS_FULL_REFRESH_EVERY
  sh "$IH_DEVICE_CLIENT" "$@"
}
ih_sync_zones() {
  : >"$TMP/zones.new"
  if [ -s "$IH_DEVICE_DIR/current" ] && [ -f "$IH_DEVICE_DIR/zones" ]; then
    if charging; then awk 'NF==7 {print $1,$2,$3,$4,$5}' "$IH_DEVICE_DIR/zones" >"$TMP/zones.new"
    else awk 'NF==7 && $6=="battery" {print $1,$2,$3,$4,$5}' "$IH_DEVICE_DIR/zones" >"$TMP/zones.new"; fi
  fi
  mv -f "$TMP/zones.new" "$TMP/zones"
}
ih_tap() {
  ih_line=$(awk -v x="$1" -v y="$2" '$2<=x && x<$4 && $3<=y && y<$5 {print;exit}' "$TMP/zones" 2>/dev/null)
  [ -n "$ih_line" ] || return
  set -- $ih_line
  ih_hit=$1; shift
  ih_hint=$(awk -v id="$ih_hit" '$1==id {print $7;exit}' "$IH_DEVICE_DIR/zones")
  case "$ih_hint" in dinner|home|birthdays) ;; *) ih_hint=none ;; esac
  if ! charging; then
    awk -v id="$ih_hit" '$1==id && $6=="battery" {found=1} END {exit !found}' "$IH_DEVICE_DIR/zones" || return
  fi
  [ "$ih_hit" = "$last_zone" ] && [ $(($(now) - last_t)) -lt "$TOUCH_DEBOUNCE" ] && return
  last_zone=$ih_hit; last_t=$(now)
  echo $(($(now) + 120)) >"$TMP/hold_until"
  # A local feedback flash contains no household data or action identifiers.
  case "$ih_hit" in cache_back|wifi_back) ;; *)
    [ ! -x "$FBINK" ] || "$FBINK" -q -w -W GC16 -B BLACK -k "top=$2,left=$1,width=$(($3-$1)),height=$(($4-$2))" >/dev/null 2>&1 ;;
  esac
  setsid /bin/sh "$BASE/dash.sh" ih-send "$ih_hit" "$ih_hint" </dev/null >/dev/null 2>&1 &
}
ih_send() {
  ih_zone=$1; ih_hint=${2:-none}; ih_cached=false
  case "$ih_hint" in dinner|home|birthdays)
    if charging && ih_run show-cached "$ih_hint"; then ih_cached=true; ih_sync_zones; fi ;;
  esac
  case "$ih_zone" in cache_back|wifi_back) ;;
    *)
      [ "$(lipc-get-prop com.lab126.cmd wirelessEnable 2>/dev/null)" = 1 ] || lipc-set-prop com.lab126.cmd wirelessEnable 1 >/dev/null 2>&1
      if ! wait_wifi 40; then
        if [ "$ih_cached" = true ]; then log "Offline: showing cached screen"; status_line "Offline: cached screen, back only"; return 1; fi
        status_line "Tap not sent (no Wi-Fi)"; rm -f "$TMP/hold_until"; return 1
      fi ;;
  esac
  if [ "$ih_cached" = true ]; then ih_command=tap-cached; else ih_command=tap; fi
  if ih_run "$ih_command" "$ih_zone"; then
    ih_sync_zones
    ih_screen=$(cat "$IH_DEVICE_DIR/screen" 2>/dev/null)
    [ "$ih_screen" != main ] || rm -f "$TMP/hold_until"
  else
    if [ "$ih_cached" = true ]; then log "Showing cached screen; touch action unavailable"; status_line "Cached screen: back only"; return 1; fi
    status_line "Tap not sent"
    rm -f "$TMP/hold_until"
    echo $(($(now) + TOUCH_REFETCH)) >"$TMP/refetch_at"
    return 1
  fi
}
'''


def replace_once(source, needle, replacement):
    if source.count(needle) != 1:
        raise ValueError('The device script is not the expected version: '+needle.splitlines()[0])
    return source.replace(needle, replacement, 1)


def patch(source):
    # The conditions are deliberately exact: reject unknown device versions.
    if not source.startswith('#!/bin/sh\n'):raise ValueError('The device script must start with its shell shebang')
    if '# IH device adapter:' in source:raise ValueError('The device script is already patched')
    for anchor in ('# kindle-dash v2:', 'TOUCH_MAX_PER_DAY=', 'listen_allowed() {',
                   'wait_until() {', 'is_quiet() {', 'touch_on() {', 'suspend_until() {'):
        if source.count(anchor) != 1:
            raise ValueError('The device script is not the expected version: '+anchor)
    source=replace_once(source,'# ---------------------------------------------------------------- fetch / draw',BOOT+'\n# ---------------------------------------------------------------- fetch / draw')
    source=replace_once(source,'fetch() {\n', '''fetch() {
  if ih_enabled; then
    h=$(cat "$TMP/hold_until" 2>/dev/null)
    [ -n "$h" ] && [ "$(now)" -lt "$h" ] 2>/dev/null && return 0
    ih_run refresh main 0 asap || return 1
    cp -f "$IH_DEVICE_DIR/active.png" "$TMP/dash.png" || return 1
    return 0
  fi
''')
    source=replace_once(source,'draw() {\n', 'draw() {\n  ih_enabled && return 0\n')
    source=replace_once(source,'update_zones() {\n', 'update_zones() {\n  if ih_enabled; then ih_sync_zones; return; fi\n')
    source=replace_once(source,'on_tap() {\n', 'on_tap() {\n  if ih_enabled; then ih_tap "$@"; return; fi\n')
    source=replace_once(source,'  send) send_main "$2"; exit 0 ;;\n', '  send) send_main "$2"; exit 0 ;;\n  ih-send) ih_enabled && ih_send "$2" "$3"; exit 0 ;;\n')
    needle='  charging && TMP="$TMP" FBINK="$FBINK" DASH_URL="$DASH_URL" ZONES_URL="$ZONES_URL" setsid sh /mnt/us/dashboard/local/dinner.sh prefetch </dev/null >/dev/null 2>&1 &'
    source=replace_once(source,needle,'''  if ih_enabled; then
    if charging; then ih_run prefetch >/dev/null 2>&1 & fi
  else
'''+needle+'''
  fi''')
    return source


def main():
    parser=argparse.ArgumentParser();parser.add_argument('source');parser.add_argument('output');args=parser.parse_args()
    src=Path(args.source);dst=Path(args.output)
    if os.path.realpath(src)==os.path.realpath(dst):parser.error('output must differ from source')
    result=patch(src.read_text(encoding='utf-8'))
    fd=os.open(dst,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as handle:handle.write(result)

if __name__=='__main__':main()
