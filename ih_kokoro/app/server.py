"""Kokoro text-to-speech as a Wyoming server (Home Assistant's Wyoming integration sees it like Piper).
Local only: no network calls at run time, text never leaves the add-on."""
import argparse, asyncio, json, logging, re, time
from functools import partial

VOICE = re.compile(r"^[a-z]{2}_[a-z]+$")
DEFAULTS = {"default_voice": "bm_george", "speed": 1.0}
LANG = {"a": "en-us", "b": "en-gb"}          # first letter of a Kokoro voice name
MAX_CHARS = 600
_LOG = logging.getLogger("ih_kokoro")


def load_options(path="/data/options.json"):
    try:
        with open(path) as f:
            o = json.load(f)
    except (OSError, ValueError):
        o = {}
    o = {**DEFAULTS, **(o if isinstance(o, dict) else {})}
    if not (isinstance(o["default_voice"], str) and VOICE.match(o["default_voice"])):
        o["default_voice"] = DEFAULTS["default_voice"]
    try:
        o["speed"] = min(1.5, max(0.5, float(o["speed"])))
    except (TypeError, ValueError):
        o["speed"] = DEFAULTS["speed"]
    return o


def pick_voice(requested, available, default):
    """Requested voice if the pack has it, else the default if it has that, else the first one."""
    for v in (requested, default):
        if isinstance(v, str) and v in available:
            return v
    return sorted(available)[0]


def lang_of(voice):
    return LANG.get(voice[:1], "en-us")


def to_pcm16(samples):
    import numpy as np
    return (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def build_info(voices):
    from wyoming.info import Attribution, Info, TtsProgram, TtsVoice
    attr = Attribution(name="Kokoro", url="https://github.com/hexgrad/kokoro")
    langs = lambda v: [lang_of(v).replace("-", "_")]
    return Info(tts=[TtsProgram(
        name="kokoro", description="Kokoro local voices", attribution=attr, installed=True, version="1.0",
        voices=[TtsVoice(name=v, description=v, attribution=attr, installed=True, version="1.0", languages=langs(v)) for v in sorted(voices)])])


def main():
    from wyoming.audio import AudioChunk, AudioStart, AudioStop
    from wyoming.event import Event
    from wyoming.info import Describe
    from wyoming.server import AsyncEventHandler, AsyncServer
    from wyoming.tts import Synthesize
    from kokoro_onnx import Kokoro

    ap = argparse.ArgumentParser()
    ap.add_argument("--uri", default="tcp://0.0.0.0:10210")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    opts = load_options()
    kokoro = Kokoro("/models/kokoro.onnx", "/models/voices.bin")
    voices = set(kokoro.get_voices())
    info = build_info(voices)
    lock = asyncio.Lock()           # one synthesis at a time: the Pi has few cores

    class Handler(AsyncEventHandler):
        async def handle_event(self, event: Event) -> bool:
            if Describe.is_type(event.type):
                await self.write_event(info.event())
                return True
            if not Synthesize.is_type(event.type):
                return True
            req = Synthesize.from_event(event)
            text = (req.text or "").strip()[:MAX_CHARS]
            voice = pick_voice(req.voice.name if req.voice else None, voices, opts["default_voice"])
            if not text:
                return True
            async with lock:
                t0 = time.time()
                samples, rate = await asyncio.get_running_loop().run_in_executor(
                    None, partial(kokoro.create, text, voice=voice, speed=opts["speed"], lang=lang_of(voice)))
                took = time.time() - t0
            pcm = to_pcm16(samples)
            secs = len(samples) / float(rate)
            _LOG.info("spoke %d chars as %s: %.1fs audio in %.1fs (%.2fx real time)", len(text), voice, secs, took, secs / took if took else 0)
            await self.write_event(AudioStart(rate=rate, width=2, channels=1).event())
            step = rate * 2 // 2            # half a second per chunk
            for i in range(0, len(pcm), step):
                await self.write_event(AudioChunk(audio=pcm[i:i + step], rate=rate, width=2, channels=1).event())
            await self.write_event(AudioStop().event())
            return True

    _LOG.info("Kokoro ready: %d voices, default %s, listening on %s", len(voices), opts["default_voice"], args.uri)
    asyncio.run(AsyncServer.from_uri(args.uri).run(Handler))


if __name__ == "__main__":
    main()
