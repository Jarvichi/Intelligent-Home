#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CHECK=0
[ "${1:-}" != "--check" ] || CHECK=1
found=0
for dir in "$ROOT"/ih_*/app/ui; do
  [ -d "$dir" ] || continue
  found=1
  for pair in "tokens.css:ih-ui.css" "components.js:ih-ui.js"; do
    src=${pair%%:*}; dst=${pair#*:}
    if [ "$CHECK" -eq 1 ]; then
      cmp -s "$ROOT/ih_ui/$src" "$dir/$dst" || { echo "stale or missing: ${dir#"$ROOT"/}/$dst"; exit 1; }
    else
      cp "$ROOT/ih_ui/$src" "$dir/$dst"
    fi
  done
done
[ "$found" -eq 1 ] || { echo "no add-on app/ui targets found"; exit 1; }
echo "IH UI assets $([ "$CHECK" -eq 1 ] && echo checked || echo synced)"
