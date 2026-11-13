#!/bin/sh
# Options are in /data/options.json. Pictures are read-only at /media.
ollama serve >/data/ollama_serve.log 2>&1 &
i=0
until curl -sf http://127.0.0.1:11434/api/tags >/dev/null; do
  i=$((i+1)); [ "$i" -gt 60 ] && echo "ollama did not start" && break; sleep 2
done
M=$(python3 -c "import json;print(json.load(open('/data/options.json')).get('model','moondream'))")
ollama list | grep -q "^$M" || ollama pull "$M" || echo "model pull failed; will retry on next start"
exec python3 /app/main.py
