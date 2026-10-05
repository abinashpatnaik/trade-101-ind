FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app/uk_growth_bot
RUN mkdir -p /app/data

ENV TZ=Europe/London \
    PYTHONUNBUFFERED=1 \
    UK_DATA_DIR=/app/data

CMD ["python", "-m", "uk_growth_bot.main"]
