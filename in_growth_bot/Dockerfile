FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app/in_growth_bot
RUN mkdir -p /app/data /app/shared

ENV TZ=Asia/Kolkata \
    PYTHONUNBUFFERED=1 \
    IN_DATA_DIR=/app/data \
    IN_SHARED_DIR=/app/shared

CMD ["python", "-m", "in_growth_bot.main"]
