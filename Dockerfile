FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy agent source files, the agents package, and universe data
COPY *.py .
COPY agents/ ./agents/
COPY *.json .

# Create directories for persistent logs and trade data
RUN mkdir -p /app/logs /app/data

# Set timezone and ensure unbuffered Python output (important for Docker logs)
ENV TZ=${TZ:-America/New_York}
ENV PYTHONUNBUFFERED=1

# Health check: fresh heartbeat key in Redis for the agent named by HC_AGENT
# (exits 0 when HC_AGENT is unset). Informational — the orchestrator is the
# actor that restarts wedged agents.
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
  CMD python -m agents.healthcheck

# Default service is the trader; other agents override `command:` in compose.
CMD ["python", "-m", "agents.trader"]
