ARG BUILD_FROM=python:3.12-slim
FROM ${BUILD_FROM}
# stdlib only: no pip packages. NOT build-tested here.
COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh
CMD ["/run.sh"]
