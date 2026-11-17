# Intelligent Home - Kokoro

## What it does

Runs [Kokoro](https://github.com/hexgrad/kokoro) (an open-source voice model, int8 version) as a Wyoming text-to-speech server. Home Assistant adds it as a speech engine, so `tts.speak` can use it just like Piper. The model and voices are inside the add-on image: no download at start and no network calls while it speaks. The port is **not published** on your network; Home Assistant reaches it at `32c8114c-ih-kokoro:10210`.

## Requires

- Home Assistant OS or Supervised.
- Disk: about 0.6 GB for the image.
- Memory: about 0.5 GB while speaking.
- Speed: on a Raspberry Pi 5 a short line may take a few seconds. Each spoken line logs "x.xx real time" (above 1.0 means faster than speaking speed). Short fixed greetings are fine; long text is slower.

## Set-up

1. Add the Intelligent Home repository and install **Intelligent Home - Kokoro**. Set start on boot, watchdog and auto update on. Start it. The log says `Kokoro ready: N voices`.
2. In Home Assistant: Settings, Devices and services, Add integration, **Wyoming Protocol**. Host `32c8114c-ih-kokoro`, port `10210`.
3. A `tts.kokoro` entity appears. Try it: Developer tools, Actions, `tts.speak`, entity `tts.kokoro`, a media player, a message and (optionally) `options: voice: bm_george`.

## Options

| Option | Required? | What it is |
|---|---|---|
| `default_voice` | No | Voice used when a request names none. British male `bm_george` is the default; other examples: `bm_lewis`, `bf_emma`, `af_heart`, `am_adam`. |
| `speed` | No | Speaking speed, 0.5 to 1.5. Default 1.0. |

## Troubleshooting

- The add-on log lists the voice count at start. An unknown voice name falls back to `default_voice`.
- Text is cut at 600 characters.
- If speech sounds clipped or slow, check CPU in the add-on's Info tab; Kokoro uses all cores while it speaks.
