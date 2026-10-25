ARG BUILD_FROM=ghcr.io/home-assistant/base-python:latest
FROM ${BUILD_FROM}
# pinned: paho-mqtt 1.6.1 (callback API v1). tzdata: zoneinfo for named time zones. NOT build-tested here.
RUN pip install --no-cache-dir paho-mqtt==1.6.1 tzdata==2024.2
COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh
CMD ["/run.sh"]
