FROM python:3.12-slim
# No AI engine in here: this add-on asks the Intelligent Home AI layer (ih_ai_layer).
COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh
CMD ["/run.sh"]
