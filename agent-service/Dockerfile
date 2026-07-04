# The Claude Agent SDK ships and drives a bundled Node-based Claude Code CLI.
# The CLI is bundled inside the pip package, so no separate install is required —
# but it is a Node program and needs a Node runtime on PATH. We copy the Node 20
# binary from the official image as cheap, deterministic insurance.
FROM node:20-slim AS node

FROM python:3.11-slim

# Node 20 runtime for the bundled CLI (node:20-slim and python:3.11-slim are both
# Debian bookworm, so the shared libs match).
COPY --from=node /usr/local/bin/node /usr/local/bin/node

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV PYTHONUNBUFFERED=1

# Always-on worker: no ports exposed (Socket Mode is an outbound websocket).
CMD ["python", "-m", "app.main"]
