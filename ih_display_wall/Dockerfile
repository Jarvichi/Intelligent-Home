ARG BUILD_FROM=python:3.12-slim
FROM ${BUILD_FROM}
RUN apt-get update && apt-get install -y --no-install-recommends librsvg2-bin fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh && python3 -c "import sys; sys.path.insert(0, '/app'); import main; image=main.png(b'<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"2\" height=\"2\"><rect width=\"2\" height=\"2\" fill=\"red\"/></svg>'); assert image.startswith(b'\\x89PNG\\r\\n\\x1a\\n')"
CMD ["/run.sh"]
