ARG BUILD_FROM=python:3.12-slim
FROM ${BUILD_FROM}
# pinned like the orchestrator: paho-mqtt 1.6.1 (callback API v1). NOT build-tested here.
RUN pip install --no-cache-dir paho-mqtt==1.6.1
COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh
CMD ["/run.sh"]
