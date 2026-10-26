
ARG BUILD_FROM=python:3.12-slim
FROM ${BUILD_FROM}
RUN pip install --no-cache-dir paho-mqtt==1.6.1 websockets==13.1
COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh
CMD ["/run.sh"]


