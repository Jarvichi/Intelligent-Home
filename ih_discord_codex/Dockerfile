ARG BUILD_FROM=python:3.12-slim
FROM ${BUILD_FROM} AS bwrap-build
ARG BWRAP_VERSION=0.13.1
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential meson ninja-build pkg-config libcap-dev ca-certificates \
    && python3 -c "import urllib.request; urllib.request.urlretrieve('https://github.com/containers/bubblewrap/releases/download/v${BWRAP_VERSION}/bubblewrap-${BWRAP_VERSION}.tar.xz', '/tmp/bubblewrap.tar.xz')" \
    && echo '0e85c8702248b8dd64144f594147553650cdd0e4c951690345faace79c217c4d  /tmp/bubblewrap.tar.xz' | sha256sum -c - \
    && mkdir /tmp/bubblewrap-src \
    && tar -xJf /tmp/bubblewrap.tar.xz -C /tmp/bubblewrap-src --strip-components=1 \
    && meson setup /tmp/bubblewrap-src/build /tmp/bubblewrap-src --prefix=/usr/local -Dman=disabled -Dtests=false -Dselinux=disabled \
    && meson compile -C /tmp/bubblewrap-src/build \
    && meson install -C /tmp/bubblewrap-src/build \
    && /usr/local/bin/bwrap --help 2>&1 | grep -q -- '--perms' \
    && /usr/local/bin/bwrap --help 2>&1 | grep -q -- '--as-pid-1' \
    && rm -rf /var/lib/apt/lists/* /tmp/bubblewrap-src /tmp/bubblewrap.tar.xz

FROM ${BUILD_FROM}
ENV PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ARG SOURCE_REVISION=unknown
COPY --from=bwrap-build /usr/local/bin/bwrap /usr/local/bin/bwrap
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
