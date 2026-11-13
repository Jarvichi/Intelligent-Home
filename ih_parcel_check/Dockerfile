FROM python:3.12-slim
# No AI engine in here: this add-on calls the Intelligent Home - Ollama add-on (ih_ollama).
COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh
CMD ["/run.sh"]
