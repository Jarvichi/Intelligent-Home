ARG BUILD_FROM=python:3.12-slim
FROM ${BUILD_FROM}
# pinned: paho-mqtt 1.6.1 (callback API v1) like the orchestrator; websockets for the HA backup/info call; Pillow 11.0.0 (the photo plugins; has binary wheels for Python 3.12 on aarch64 and amd64, so no compiler is needed). NOT build-tested here.
# If Pillow is ever missing the photo plugins log a clear line and do nothing.
RUN pip install --no-cache-dir paho-mqtt==1.6.1 websockets==13.1 Pillow==11.0.0
COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh
CMD ["/run.sh"]
