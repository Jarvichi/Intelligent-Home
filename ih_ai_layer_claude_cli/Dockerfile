ARG BUILD_FROM=node:22-bookworm-slim
FROM ${BUILD_FROM}
# UNVERIFIED / NOT build-tested: the Claude Code CLI installed with npm, plus python3 for the agent (stdlib only).
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g @anthropic-ai/claude-code
COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh
CMD ["/run.sh"]
