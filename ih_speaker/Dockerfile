ARG BUILD_FROM=python:3.12-slim
FROM ${BUILD_FROM}
# ffmpeg (apt) turns the text-to-speech audio into ogg/opus for Discord voice messages. Python code is stdlib only: no pip packages (tzdata comes with the base image; if the timezone is missing the add-on falls back to UTC). NOT build-tested here.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh
CMD ["/run.sh"]
