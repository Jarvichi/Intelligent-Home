ARG BUILD_FROM=python:3.12-slim
FROM ${BUILD_FROM}
# pinned; NOT build-tested here. PyYAML is only used to score the YAML question.
RUN pip install --no-cache-dir tzdata pyyaml
COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh
CMD ["/run.sh"]
