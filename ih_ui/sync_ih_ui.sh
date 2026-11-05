#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CHECK=0
[ "${1:-}" != "--check" ] || CHECK=1
found=0
# copy_one SRC DST LABEL: copy, or in --check mode fail when DST is missing or differs.
copy_one() {
  if [ "$CHECK" -eq 1 ]; then
    cmp -s "$1" "$2" || { echo "stale or missing: $3"; exit 1; }
  else
    cp "$1" "$2"
  fi
}
for dir in "$ROOT"/ih_*/app/ui; do
  [ -d "$dir" ] || continue
  found=1
  rel=${dir#"$ROOT"/}
  for pair in "tokens.css:ih-ui.css" "components.js:ih-ui.js"; do
    src=${pair%%:*}; dst=${pair#*:}
    copy_one "$ROOT/ih_ui/$src" "$dir/$dst" "$rel/$dst"
  done
  # Pages marked "page": "status" in ui.json use the shared status page template.
  if [ -f "$dir/ui.json" ] && grep -q '"page"[[:space:]]*:[[:space:]]*"status"' "$dir/ui.json"; then
    for pair in "status-page.html:index.html" "status-page.js:ui.js" "status-page.css:ui.css" "patterns.css:ih-patterns.css"; do
      src=${pair%%:*}; dst=${pair#*:}
      copy_one "$ROOT/ih_ui/$src" "$dir/$dst" "$rel/$dst"
    done
  # Other optional patterns are adopted explicitly; do not alter unrelated add-ons.
  elif [ -f "$dir/ih-patterns.css" ]; then
    copy_one "$ROOT/ih_ui/patterns.css" "$dir/ih-patterns.css" "$rel/ih-patterns.css"
  fi
done
[ "$found" -eq 1 ] || { echo "no add-on app/ui targets found"; exit 1; }
echo "IH UI assets $([ "$CHECK" -eq 1 ] && echo checked || echo synced)"
