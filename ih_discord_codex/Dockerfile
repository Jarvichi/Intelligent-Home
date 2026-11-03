ARG BUILD_FROM=python:3.12-slim
FROM ${BUILD_FROM}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ARG SOURCE_REVISION=unknown
RUN apt-get update && apt-get install -y --no-install-recommends bubblewrap git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY ih_discord_codex/requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt && rm /requirements.txt
COPY ih_discord_codex/app /app
COPY ih_discord_codex/run.sh /run.sh
COPY . /opt/ih-source
RUN printf '%s' "$SOURCE_REVISION" > /opt/ih-source-revision \
    && chmod 0755 /run.sh && mkdir -p /config/workspace /config/codex-home \
    && chmod 0700 /config/workspace /config/codex-home
CMD ["/run.sh"]
