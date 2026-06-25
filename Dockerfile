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

# Copy agent source files and universe data
COPY *.py .
COPY *.json .

# Create directories for persistent logs and trade data
RUN mkdir -p /app/logs /app/data

# Set timezone and ensure unbuffered Python output (important for Docker logs)
ENV TZ=${TZ:-America/New_York}
ENV PYTHONUNBUFFERED=1

# Health check: verify the agent has started logging
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
  CMD test -f /app/logs/agent.log

CMD ["python", "agent.py"]
