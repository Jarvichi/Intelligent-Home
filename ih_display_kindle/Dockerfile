ARG BUILD_FROM=python:3.12-slim
FROM ${BUILD_FROM}
RUN apt-get update && apt-get install -y --no-install-recommends librsvg2-bin fonts-dejavu-core fonts-symbola && rm -rf /var/lib/apt/lists/*
COPY app /app
COPY fonts /usr/local/share/fonts/kindle
COPY device /app/device
COPY run.sh /run.sh
RUN chmod +x /run.sh && python3 /app/render_selftest.py
CMD ["/run.sh"]
