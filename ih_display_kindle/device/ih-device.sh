#!/bin/sh
# Optional device-side frame client. The existing touch listener must call
# `tap <opaque-zone-id>` and use the installed zones file. No settings or
# current device files are changed by installing this script.
set -eu
umask 077
: "${IH_DEVICE_URL:?Set IH_DEVICE_URL}"
: "${IH_DEVICE_CURL_CONFIG:?Set IH_DEVICE_CURL_CONFIG}"
: "${IH_DEVICE_DIR:?Set IH_DEVICE_DIR}"
: "${IH_DEVICE_DRAW:?Set IH_DEVICE_DRAW to an executable accepting a PNG path}"
mkdir -p "$IH_DEVICE_DIR"
lock="$IH_DEVICE_DIR/lock"
if [ "${1:-}" = prefetch ]; then lock="$IH_DEVICE_DIR/prefetch-lock"; fi
if ! mkdir "$lock" 2>/dev/null; then exit 1; fi
owns_lock=true
pending=
trap '[ "$owns_lock" != true ] || rm -rf "$lock"; [ -z "$pending" ] || rm -f "$pending"' EXIT HUP INT TERM
fetch() { curl --fail --silent --show-error --max-time 20 --config "$IH_DEVICE_CURL_CONFIG" "$@"; }
case "${1:-}" in
  refresh)
    screen=${2:-main}; page=${3:-0}; eat=${4:-asap}
    case "$screen" in main|menu|dinner|home|birthdays) ;; *) exit 2 ;; esac
    case "$eat" in asap|1230|1330|1830) ;; *) exit 2 ;; esac
    case "$page" in ''|*[!0-9]*) exit 2 ;; esac
    [ "$page" -le 999 ] || exit 2
    # A failed or interrupted draw is deliberately not tappable.
    rm -f "$IH_DEVICE_DIR/current"
    fetch "$IH_DEVICE_URL/device/v1/frame?screen=$screen&page=$page&eat=$eat" > "$lock/manifest"
    frame=$(awk '$1=="frame" && NF==2 {print $2}' "$lock/manifest")
    digest=$(awk '$1=="sha256" && NF==2 {print $2}' "$lock/manifest")
    case "$frame" in *[!0-9a-f]*|'') exit 1 ;; esac
    case "$digest" in *[!0-9a-f]*|'') exit 1 ;; esac
    [ "${#frame}" -eq 32 ] && [ "${#digest}" -eq 64 ] || exit 1
    awk 'BEGIN {ok=1} $1=="zone" {if(NF!=8 || length($2)!=32 || $2 !~ /^[0-9a-f]+$/ || $3 !~ /^[0-9]+$/ || $4 !~ /^[0-9]+$/ || $5 !~ /^[0-9]+$/ || $6 !~ /^[0-9]+$/ || $3 >= $5 || $4 >= $6 || $5 > 1072 || $6 > 1448 || ($7!="battery" && $7!="mains") || ($8!="none" && $8!="dinner" && $8!="home" && $8!="birthdays")) ok=0; else print $2, $3, $4, $5, $6, $7, $8} END {if(!ok) exit 1}' "$lock/manifest" > "$lock/zones"
    fetch "$IH_DEVICE_URL/device/v1/picture/$frame" > "$lock/picture.png"
    actual=$(sha256sum "$lock/picture.png" | awk '{print $1}')
    [ "$actual" = "$digest" ] || exit 1
    "$IH_DEVICE_DRAW" "$lock/picture.png"
    fetch -X POST --data "frame=$frame&sha256=$digest" "$IH_DEVICE_URL/device/v1/activate" > "$lock/result"
    grep -Eq '^\{"ok":[[:space:]]*true\}$' "$lock/result" || exit 1
    if [ "$screen" = menu ]; then
      cp "$lock/picture.png" "$IH_DEVICE_DIR/menu.png.new"
      cp "$lock/zones" "$IH_DEVICE_DIR/menu.zones.new"
      printf '%s\n' "$frame" > "$IH_DEVICE_DIR/menu.frame.new"
      mv -f "$IH_DEVICE_DIR/menu.png.new" "$IH_DEVICE_DIR/menu.png"
      mv -f "$IH_DEVICE_DIR/menu.zones.new" "$IH_DEVICE_DIR/menu.zones"
      mv -f "$IH_DEVICE_DIR/menu.frame.new" "$IH_DEVICE_DIR/menu.frame"
    fi
    # Retain the last shown picture for offline, read-only popup return.
    cp "$lock/picture.png" "$IH_DEVICE_DIR/active.png.new"
    mv -f "$IH_DEVICE_DIR/active.png.new" "$IH_DEVICE_DIR/active.png"
    # current is published last; taps remain blocked until zones are installed.
    cp "$lock/zones" "$IH_DEVICE_DIR/zones.new"
    mv -f "$IH_DEVICE_DIR/zones.new" "$IH_DEVICE_DIR/zones"
    printf '%s\n' "$screen" > "$IH_DEVICE_DIR/screen.new"
    mv -f "$IH_DEVICE_DIR/screen.new" "$IH_DEVICE_DIR/screen"
    printf '%s\n' "$frame" > "$IH_DEVICE_DIR/current.new"
    mv -f "$IH_DEVICE_DIR/current.new" "$IH_DEVICE_DIR/current"
    ;;
  prefetch)
    # The listener supplies this observed power state; never wake Wi-Fi on battery.
    [ "${IH_DEVICE_CHARGING:-}" = true ] || exit 1
    screen=${2:-all}; failed=0
    case "$screen" in all) screens='dinner home birthdays' ;; dinner|home|birthdays) screens=$screen ;; *) exit 2 ;; esac
    for item in $screens; do
      if ! fetch "$IH_DEVICE_URL/device/v1/frame?screen=$item&page=0&eat=asap" > "$lock/manifest"; then failed=1; continue; fi
      frame=$(awk '$1=="frame" && NF==2 {print $2}' "$lock/manifest")
      digest=$(awk '$1=="sha256" && NF==2 {print $2}' "$lock/manifest")
      case "$frame" in *[!0-9a-f]*|'') failed=1; continue ;; esac
      case "$digest" in *[!0-9a-f]*|'') failed=1; continue ;; esac
      if [ "${#frame}" -ne 32 ] || [ "${#digest}" -ne 64 ]; then failed=1; continue; fi
      if ! fetch "$IH_DEVICE_URL/device/v1/picture/$frame" > "$lock/cache.png"; then failed=1; continue; fi
      actual=$(sha256sum "$lock/cache.png" | awk '{print $1}')
      if [ "$actual" != "$digest" ] || [ "$(od -An -tx1 -N8 "$lock/cache.png" | tr -d ' \n')" != 89504e470d0a1a0a ]; then failed=1; continue; fi
      # Keep the PNG and checksum together; a torn update fails closed on read.
      cp "$lock/cache.png" "$IH_DEVICE_DIR/cache-$item.png.new"
      printf '%s\n' "$digest" > "$IH_DEVICE_DIR/cache-$item.sha256.new"
      mv -f "$IH_DEVICE_DIR/cache-$item.png.new" "$IH_DEVICE_DIR/cache-$item.png"
      mv -f "$IH_DEVICE_DIR/cache-$item.sha256.new" "$IH_DEVICE_DIR/cache-$item.sha256"
    done
    [ "$failed" -eq 0 ]
    ;;
  show-cached)
    [ "${IH_DEVICE_CHARGING:-}" = true ] || exit 1
    screen=${2:-}
    case "$screen" in dinner|home|birthdays) ;; *) exit 2 ;; esac
    [ -s "$IH_DEVICE_DIR/current" ] && [ -s "$IH_DEVICE_DIR/active.png" ] && [ -f "$IH_DEVICE_DIR/zones" ] || exit 1
    IFS= read -r frame < "$IH_DEVICE_DIR/current"
    case "$frame" in *[!0-9a-f]*|'') exit 1 ;; esac
    [ "${#frame}" -eq 32 ] || exit 1
    [ -s "$IH_DEVICE_DIR/cache-$screen.png" ] && [ -s "$IH_DEVICE_DIR/cache-$screen.sha256" ] || exit 1
    IFS= read -r expected < "$IH_DEVICE_DIR/cache-$screen.sha256"
    actual=$(sha256sum "$IH_DEVICE_DIR/cache-$screen.png" | awk '{print $1}')
    [ "$actual" = "$expected" ] || exit 1
    cp "$IH_DEVICE_DIR/active.png" "$IH_DEVICE_DIR/return.png"
    cp "$IH_DEVICE_DIR/zones" "$IH_DEVICE_DIR/return.zones"
    cp "$IH_DEVICE_DIR/screen" "$IH_DEVICE_DIR/return.screen"
    printf '%s\n' "$frame" > "$IH_DEVICE_DIR/return.frame"
    printf '%s\n' "$screen" > "$IH_DEVICE_DIR/cache-screen"
    rm -f "$IH_DEVICE_DIR/current"
    "$IH_DEVICE_DRAW" "$IH_DEVICE_DIR/cache-$screen.png"
    case "$screen" in home) printf 'cache_back 140 1010 932 1114 mains none\n' ;; *) printf 'cache_back 48 1240 1024 1344 mains none\n' ;; esac > "$IH_DEVICE_DIR/zones.new"
    mv -f "$IH_DEVICE_DIR/zones.new" "$IH_DEVICE_DIR/zones"
    printf 'local-cache\n' > "$IH_DEVICE_DIR/current.new"
    mv -f "$IH_DEVICE_DIR/current.new" "$IH_DEVICE_DIR/current"
    ;;
  tap|tap-xy|tap-cached)
    if [ "$1" = tap-xy ]; then
      x=${2:-}; y=${3:-}
      case "$x" in ''|*[!0-9]*) exit 2 ;; esac
      case "$y" in ''|*[!0-9]*) exit 2 ;; esac
      [ "$x" -lt 1072 ] && [ "$y" -lt 1448 ] || exit 2
      [ -f "$IH_DEVICE_DIR/zones" ] || exit 1
      zone=$(awk -v x="$x" -v y="$y" 'x >= $2 && y >= $3 && x < $4 && y < $5 {print $1; exit}' "$IH_DEVICE_DIR/zones")
    else zone=${2:-}; fi
    if [ "$zone" = cache_back ]; then
      [ -s "$IH_DEVICE_DIR/current" ] && [ "$(cat "$IH_DEVICE_DIR/current")" = local-cache ] || exit 1
      [ -s "$IH_DEVICE_DIR/return.png" ] && [ -f "$IH_DEVICE_DIR/return.zones" ] && [ -s "$IH_DEVICE_DIR/return.frame" ] || exit 1
      rm -f "$IH_DEVICE_DIR/current"
      "$IH_DEVICE_DRAW" "$IH_DEVICE_DIR/return.png"
      cp "$IH_DEVICE_DIR/return.zones" "$IH_DEVICE_DIR/zones.new"
      mv -f "$IH_DEVICE_DIR/zones.new" "$IH_DEVICE_DIR/zones"
      cp "$IH_DEVICE_DIR/return.frame" "$IH_DEVICE_DIR/current.new"
      mv -f "$IH_DEVICE_DIR/current.new" "$IH_DEVICE_DIR/current"
      cp "$IH_DEVICE_DIR/return.screen" "$IH_DEVICE_DIR/screen.new"
      mv -f "$IH_DEVICE_DIR/screen.new" "$IH_DEVICE_DIR/screen"
      exit 0
    fi
    if [ "$zone" = wifi_back ]; then
      [ -s "$IH_DEVICE_DIR/current" ] && [ "$(cat "$IH_DEVICE_DIR/current")" = local-wifi ] || exit 1
      [ -s "$IH_DEVICE_DIR/menu.png" ] && [ -f "$IH_DEVICE_DIR/menu.zones" ] && [ -s "$IH_DEVICE_DIR/menu.frame" ] || exit 1
      rm -f "$IH_DEVICE_DIR/current"
      "$IH_DEVICE_DRAW" "$IH_DEVICE_DIR/menu.png"
      cp "$IH_DEVICE_DIR/menu.zones" "$IH_DEVICE_DIR/zones.new"
      mv -f "$IH_DEVICE_DIR/zones.new" "$IH_DEVICE_DIR/zones"
      cp "$IH_DEVICE_DIR/menu.frame" "$IH_DEVICE_DIR/current.new"
      mv -f "$IH_DEVICE_DIR/current.new" "$IH_DEVICE_DIR/current"
      printf 'menu\n' > "$IH_DEVICE_DIR/screen.new"
      mv -f "$IH_DEVICE_DIR/screen.new" "$IH_DEVICE_DIR/screen"
      exit 0
    fi
    case "$zone" in *[!0-9a-f]*|'') exit 2 ;; esac
    [ "${#zone}" -eq 32 ] && [ -s "$IH_DEVICE_DIR/current" ] && [ -f "$IH_DEVICE_DIR/zones" ] || exit 1
    if [ "$1" = tap-cached ]; then
      [ "$(cat "$IH_DEVICE_DIR/current")" = local-cache ] && [ -s "$IH_DEVICE_DIR/return.frame" ] && [ -f "$IH_DEVICE_DIR/return.zones" ] && [ -s "$IH_DEVICE_DIR/cache-screen" ] || exit 1
      IFS= read -r cache_screen < "$IH_DEVICE_DIR/cache-screen"
      awk -v id="$zone" -v screen="$cache_screen" '$1==id && $7==screen {found=1} END {exit !found}' "$IH_DEVICE_DIR/return.zones" || exit 1
      IFS= read -r frame < "$IH_DEVICE_DIR/return.frame"
    else
      awk -v id="$zone" '$1==id {found=1} END {exit !found}' "$IH_DEVICE_DIR/zones" || exit 1
      IFS= read -r frame < "$IH_DEVICE_DIR/current"
    fi
    if [ "$1" = tap-cached ]; then
      # Leave local BACK responsive during a slow network request. If BACK
      # changed the screen, discard the late response without drawing it.
      pending="$IH_DEVICE_DIR/.result-$zone"
      rm -rf "$lock"
      owns_lock=false
      if fetch -X POST --data "frame=$frame&zone=$zone" "$IH_DEVICE_URL/device/v1/tap" > "$pending"; then rc=0; else rc=$?; fi
      mkdir "$lock" 2>/dev/null || exit 1
      owns_lock=true
      [ "$rc" -eq 0 ] && [ -s "$IH_DEVICE_DIR/current" ] && [ "$(cat "$IH_DEVICE_DIR/current")" = local-cache ] && [ "$(cat "$IH_DEVICE_DIR/return.frame")" = "$frame" ] || exit 1
      mv -f "$pending" "$lock/result"
      pending=
    else
      fetch -X POST --data "frame=$frame&zone=$zone" "$IH_DEVICE_URL/device/v1/tap" > "$lock/result"
    fi
    if grep -q '^navigate ' "$lock/result"; then
      read -r command screen page eat < "$lock/result"
      case "$screen" in main|menu|dinner|home|birthdays|wifi) ;; *) exit 1 ;; esac
      case "$page" in ''|*[!0-9]*) exit 1 ;; esac
      case "$eat" in asap|1230|1330|1830) ;; *) exit 1 ;; esac
      if [ "$screen" = wifi ]; then
        # The private QR is read only on this device, never sent to the server.
        case "${IH_DEVICE_WIFI_IMAGE:-}" in /*) ;; *) exit 1 ;; esac
        [ -f "$IH_DEVICE_WIFI_IMAGE" ] && [ -r "$IH_DEVICE_WIFI_IMAGE" ] || exit 1
        [ -s "$IH_DEVICE_DIR/menu.png" ] && [ -f "$IH_DEVICE_DIR/menu.zones" ] && [ -s "$IH_DEVICE_DIR/menu.frame" ] || exit 1
        [ "$(cat "$IH_DEVICE_DIR/menu.frame")" = "$frame" ] || exit 1
        [ "$(od -An -tx1 -N8 "$IH_DEVICE_WIFI_IMAGE" | tr -d ' \n')" = 89504e470d0a1a0a ] || exit 1
        rm -f "$IH_DEVICE_DIR/current"
        "$IH_DEVICE_DRAW" "$IH_DEVICE_WIFI_IMAGE"
        printf 'wifi_back 0 0 1072 1448 mains none\n' > "$IH_DEVICE_DIR/zones.new"
        mv -f "$IH_DEVICE_DIR/zones.new" "$IH_DEVICE_DIR/zones"
        printf 'local-wifi\n' > "$IH_DEVICE_DIR/current.new"
        mv -f "$IH_DEVICE_DIR/current.new" "$IH_DEVICE_DIR/current"
        printf 'wifi\n' > "$IH_DEVICE_DIR/screen.new"
        mv -f "$IH_DEVICE_DIR/screen.new" "$IH_DEVICE_DIR/screen"
        exit 0
      fi
      rm -rf "$lock"
      trap - EXIT HUP INT TERM
      exec sh "$0" refresh "$screen" "$page" "$eat"
    fi
    if grep -Eq '^\{"ok":[[:space:]]*true\}$' "$lock/result"; then
      rm -rf "$lock"
      trap - EXIT HUP INT TERM
      exec sh "$0" refresh main 0 asap
    fi
    ;;
  *) exit 2 ;;
esac
