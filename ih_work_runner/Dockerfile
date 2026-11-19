ARG BUILD_FROM=node:22-bookworm-slim
FROM ${BUILD_FROM}
# UNVERIFIED / NOT build-tested: node + Claude Code CLI (npm), python3 (the worker scripts use the standard library), git and the GitHub CLI
# (to push the ih/work branches and open pull requests). The worker scripts themselves are NOT in the image: they are read from the mapped
# /homeassistant/claude_scripts/work_queue folder, so there is one copy only.
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates git gh curl \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g @anthropic-ai/claude-code
COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh
CMD ["/run.sh"]
