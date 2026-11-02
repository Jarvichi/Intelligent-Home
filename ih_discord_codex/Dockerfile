ARG BUILD_FROM=python:3.12-slim
FROM ${BUILD_FROM}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt && rm /requirements.txt
COPY app /app
COPY run.sh /run.sh
RUN chmod 0755 /run.sh && mkdir -p /config/workspace /config/codex-home && chmod 0700 /config/workspace /config/codex-home
CMD ["/run.sh"]
