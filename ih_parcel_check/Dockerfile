FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates zstd && rm -rf /var/lib/apt/lists/*
# Official Ollama release, pinned version + checksum (aarch64 only; never "latest")
ARG OLLAMA_VERSION=0.40.16
ARG OLLAMA_SHA256=e45b8e6dcb86cb9f3057396351655079c9a4879b4ced2678c9350d375dcbc224
RUN curl -fsSL -o /tmp/ollama.tar.zst "https://github.com/ollama/ollama/releases/download/v${OLLAMA_VERSION}/ollama-linux-arm64.tar.zst" \
 && echo "${OLLAMA_SHA256}  /tmp/ollama.tar.zst" | sha256sum -c - \
 && tar --zstd -C /usr/local -xf /tmp/ollama.tar.zst && rm /tmp/ollama.tar.zst
ENV OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS=/data/ollama OLLAMA_KEEP_ALIVE=30s OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_NUM_PARALLEL=1
COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh
CMD ["/run.sh"]
