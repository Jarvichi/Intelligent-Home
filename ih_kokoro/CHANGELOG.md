# Changelog

## [0.1.0] - 2026-11-14
- First version: Kokoro (v1.0 int8 model, kokoro-onnx 0.6.1) as a Wyoming text-to-speech server for Home Assistant. Model and voice files are baked into the image and checked by sha256. Not published on the network; Home Assistant reaches it at `32c8114c-ih-kokoro:10210`. Each spoken line logs how long it took compared with the length of the audio.
