# Changelog

## [0.1.0] - 2026-11-13
- First version: Ollama as its own add-on so other Intelligent Home add-ons share one local AI engine. Official Ollama v0.40.16 (aarch64), pinned by version and sha256 (the digest GitHub publishes for the release asset). Models listed in the options are pulled in the background on start into /data/ollama. The API (port 11434) is reachable by other add-ons at `http://32c8114c-ih-ollama:11434` and is NOT published on the network.
