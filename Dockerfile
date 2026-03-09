FROM python:3.12-slim

LABEL maintainer="fixitblock"
LABEL description="FixItBlock — Proxmox Maintenance Agent"

# System deps (for paramiko/cryptography)
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App files
COPY agent/ .

# Create log dir
RUN mkdir -p /var/log

EXPOSE 7070

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:7070/api/status || exit 1

# Default: run web server (CLI mode: override CMD)
CMD ["python", "server.py"]
