ARG BUILD_FROM=python:3.12-alpine
FROM ${BUILD_FROM}
# stdlib only; this add-on never runs Ollama itself, it only calls an Ollama server elsewhere on the home network.
COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh
CMD ["/run.sh"]
