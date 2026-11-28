ARG BUILD_FROM=python:3.12-slim
FROM ${BUILD_FROM}
# The only third-party package is pypolestar (read-only Polestar ID client). NOT build-tested here.
RUN pip install --no-cache-dir pypolestar
COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh
CMD ["/run.sh"]
