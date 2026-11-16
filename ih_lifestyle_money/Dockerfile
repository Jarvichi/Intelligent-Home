ARG BUILD_FROM=python:3.12-slim
FROM ${BUILD_FROM}
COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh
CMD ["/run.sh"]
