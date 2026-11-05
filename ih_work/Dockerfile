ARG BUILD_FROM=python:3.12-slim
FROM ${BUILD_FROM}
# tzdata pinned so Europe/London works on a slim image; NOT build-tested here.
RUN pip install --no-cache-dir tzdata==2025.2
COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh
CMD ["/run.sh"]
