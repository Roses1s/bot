FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    cron \
    tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
RUN chown -R appuser:appuser /app

# Директория для бэкапов
RUN mkdir -p /backups && chown appuser:appuser /backups

USER appuser

# Healthcheck — проверяем что последний бэкап не старше 48ч
HEALTHCHECK --interval=1h --timeout=30s --retries=3 \
  CMD python -m app.cli healthcheck || exit 1

# Запуск: cron-планировщик + FastAPI (опционально)
# CMD можно переопределить в docker-compose:
#   command: uvicorn app.api:app --host 0.0.0.0 --port 8000  (для API)
#   command: python -m app.scheduler                          (для cron, дефолт)
EXPOSE 8000
CMD ["python", "-m", "app.scheduler"]
