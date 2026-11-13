"""Turn /data/options.json into shell settings for run.sh (stdlib only). Values are checked, never passed through raw."""
import json, re, sys

DEFAULTS = {"models": ["moondream"], "keep_alive": "30s", "max_loaded_models": 1, "num_parallel": 1}
MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,80}$")      # e.g. moondream, nomic-embed-text, qwen2.5:1.5b
DURATION = re.compile(r"^(-1|0|\d{1,4}(s|m|h))$")               # e.g. 30s, 5m, 0, -1


def load(path="/data/options.json"):
    try:
        with open(path) as f:
            o = json.load(f)
    except (OSError, ValueError):
        o = {}
    return {**DEFAULTS, **(o if isinstance(o, dict) else {})}


def models(o):
    out = [m for m in (o.get("models") or []) if isinstance(m, str) and MODEL.match(m)]
    return out or list(DEFAULTS["models"])


def env_lines(o):
    ka = str(o.get("keep_alive"))
    ka = ka if DURATION.match(ka) else DEFAULTS["keep_alive"]
    def num(k, lo, hi):
        try:
            return min(hi, max(lo, int(o.get(k))))
        except (TypeError, ValueError):
            return DEFAULTS[k]
    return ["export OLLAMA_KEEP_ALIVE=%s" % ka, "export OLLAMA_MAX_LOADED_MODELS=%d" % num("max_loaded_models", 1, 3),
            "export OLLAMA_NUM_PARALLEL=%d" % num("num_parallel", 1, 4)]


if __name__ == "__main__":
    opts = load()
    print("\n".join(models(opts)) if "--models" in sys.argv else "\n".join(env_lines(opts)))
