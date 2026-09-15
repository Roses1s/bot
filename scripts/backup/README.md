# Backup Service — Odoo PostgreSQL

Clean Architecture сервис для бэкапов: `pg_dump → gzip → local → S3 → Telegram → ротация`.

## Архитектура

```
config (pydantic-settings)
  → entities (dataclasses)
  → ports (Protocol/ABC: StoragePort, NotifierPort, DatabasePort)
  → adapters (LocalStorage, S3Storage, TelegramNotifier)
  → service (PostgresBackupService)
  → entrypoints (CLI, Scheduler, FastAPI)
```

## Запуск

```bash
# Локально
pip install -r requirements.txt
cp ../../.env.example .env  # заполните POSTGRES_PASSWORD

# CLI
python -m app.cli backup --verbose
python -m app.cli list
python -m app.cli restore --file /backups/odoo_20240101_030000.sql.gz
python -m app.cli healthcheck

# Scheduler (cron)
python -m app.scheduler  # читает BACKUP_CRON из .env

# API + Swagger
uvicorn app.api:app --reload --port 8000
open http://localhost:8000/docs
open http://localhost:8000/redoc
```

## Конфигурация (.env)

| Переменная | Обязат. | Пример |
|------------|---------|--------|
| `POSTGRES_PASSWORD` | ✅ | `openssl rand -base64 32` |
| `BACKUP_CRON` | — | `0 3 * * *` |
| `BACKUP_RETENTION_DAYS` | — | `30` |
| `AWS_S3_BUCKET` | — | `my-odoo-backups` |
| `TELEGRAM_BOT_TOKEN` | — | `123:ABC` |
| `TELEGRAM_CHAT_ID` | — | `12345` |

## Тесты

```bash
pytest -v --cov=app
ruff check app
mypy app
```

## Docker

```bash
docker build -t odoo-backup .
docker run --env-file .env -v $(pwd)/../../backups:/backups odoo-backup python -m app.cli backup
# Или через compose:
docker compose up backup backup-api
```

## OpenAPI

Схема генерируется FastAPI: `GET /openapi.json`, Swagger на `/docs`. Экспортированная: `../../docs/api/openapi.{json,yaml}`.
