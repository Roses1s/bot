# Odoo 17 CRM — Production Stack

**Шаги 1-6:** развёртывание полнофункциональной CRM на базе **Odoo Community Edition 17** + кастомизация, интеграции, безопасность и мониторинг.

> **Статус: ✅ Шаг 1 — Инфраструктура завершён.** Жду подтверждения для перехода к Шагу 2.

---

## 🚀 Быстрый старт (Шаг 1)

```bash
# 1. Клонируйте
git clone https://github.com/Roses1s/bot.git /opt/odoo-crm
cd /opt/odoo-crm

# 2. Настройте секреты
cp .env.example .env
nano .env  # заполните POSTGRES_PASSWORD, ODOO_ADMIN_PASSWD, DOMAIN, EMAIL
# Генерация пароля: openssl rand -base64 32

# 3. Запустите
docker compose up -d --build
docker compose logs -f odoo   # подождите 60-90 сек до healthy

# 4. Выпустите SSL (домен должен указывать на IP)
make ssl-init DOMAIN=crm.example.com EMAIL=admin@example.com
curl -I https://crm.example.com/health  # 200 OK

# 5. Проверьте бэкапы
make backup
curl https://crm.example.com/backup-api/docs  # Swagger
```

Подробнее: [`docs/step1_infrastructure.md`](docs/step1_infrastructure.md)

---

## 📦 Что входит в Шаг 1

| Компонент | Версия | Назначение | Порт |
|-----------|--------|------------|------|
| **Odoo** | 17.0 CE | CRM / ERP | 8069 (HTTP), 8072 (longpolling) |
| **PostgreSQL** | 15-alpine | База данных | 5432 (только 127.0.0.1) |
| **Nginx** | stable-alpine | Reverse proxy, SSL, gzip, rate-limit | 80, 443 |
| **Redis** | 7-alpine | Кэш сессий (задел под Шаг 6) | 6379 (internal) |
| **Certbot** | latest | Let's Encrypt авто-продление | — |
| **Backup** | Python 3.11 | `pg_dump | gzip`, ротация, S3, Telegram | — |
| **Backup API** | FastAPI | HTTP + OpenAPI/Swagger | 8000 → `/backup-api/` |

**Ключевые фичи инфраструктуры:**

- ✅ **Docker Compose** с healthchecks, isolated networks (`frontend`/`backend`), persistent volumes, resource limits
- ✅ **Nginx**: HTTP→HTTPS, HSTS, gzip, `proxy_cache` для `/web/static`, longpolling `/websocket`, `limit_req` на `/web/login`
- ✅ **PostgreSQL** tuning: `shared_buffers 256M`, `max_connections 200`, `pg_trgm`/`unaccent` расширения
- ✅ **Odoo**: `workers=4` (multiprocessing), `proxy_mode=True`, `list_db=False`, `max_cron_threads=2`
- ✅ **SSL**: `scripts/ssl/init-letsencrypt.sh` (dummy cert → webroot → certonly → reload), Certbot renew 12h
- ✅ **Бэкапы**: Clean Architecture (SOLID), `structlog`, `pydantic-settings`, `croniter`, S3 best-effort, Telegram, ротация `BACKUP_RETENTION_DAYS`, CLI + scheduler + REST API + OpenAPI
- ✅ **Безопасность**: `fail2ban` jail для `odoo-login`, `ufw` 80/443, Postgres только локально, `admin_passwd` из `.env`
- ✅ **Тесты**: `pytest` 51 тест, `ruff` + `mypy`, файлы ≤500 строк, `make test` / `make lint`

---

## 📁 Структура

```
.
├── docker-compose.yml              # prod
├── docker-compose.override.yml     # dev (открывает 8069)
├── .env.example
├── Makefile
├── config/
│   ├── odoo/odoo.conf
│   ├── nginx/{nginx.conf,conf.d/odoo.conf}
│   ├── postgres/init/01-init-db.sql
│   └── redis/redis.conf
├── scripts/
│   ├── backup/                     # Clean Architecture + FastAPI
│   └── ssl/init-letsencrypt.sh
├── deployments/
│   ├── fail2ban/
│   └── systemd/odoo-backup.{service,timer}
├── docs/step1_infrastructure.md    # полный гайд Шага 1
├── tests/                          # pytest + инфраструктурные тесты
├── backups/                        # volume (в .gitignore)
└── addons/                         # кастомные модули (Шаг 3)
```

---

## 🛠️ Команды

```bash
make help          # все команды
make up            # docker compose up -d --build
make down          # остановить
make logs          # логи всех
make logs-odoo     # только Odoo
make ps            # статус
make health        # curl /health + pg_isready + redis ping
make backup        # ручной бэкап
make restore FILE=backups/xxx.sql.gz
make ssl-init DOMAIN=... EMAIL=...
make test          # pytest --cov
make lint          # ruff + mypy
make psql          # psql -U odoo
make odoo-shell    # odoo shell
```

---

## 🔐 Переменные окружения (`.env`)

См. [`.env.example`](.env.example). Обязательные:

- `POSTGRES_PASSWORD` — ≥32 символа
- `ODOO_ADMIN_PASSWD` — мастер-пароль `/web/database/manager`
- `DOMAIN` — `crm.example.com`
- `EMAIL` — для Let's Encrypt

Опциональные: `AWS_S3_BUCKET` / `AWS_*` (S3), `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`, `BACKUP_CRON`, `BACKUP_RETENTION_DAYS`.

---

## 📊 API и Swagger

Backup сервис экспонирует REST API (проксируется через Nginx):

- `GET /backup-api/health` — healthcheck (для Docker + Prometheus)
- `GET /backup-api/backups` — список бэкапов
- `POST /backup-api/backups` — создать
- `POST /backup-api/restore` — восстановить
- `GET /backup-api/docs` — Swagger UI (OpenAPI 3.1)
- `GET /backup-api/redoc` — ReDoc
- `GET /backup-api/openapi.json` — сырая схема

Пример:

```bash
curl https://crm.example.com/backup-api/health | jq
curl -X POST https://crm.example.com/backup-api/backups | jq
```

Код: [`scripts/backup/app/api.py`](scripts/backup/app/api.py) — FastAPI с `Pydantic` схемами, DI, `structlog`.

---

## 🧪 Качество кода

Проект следует правилам:

1. **Production-ready + type hints** — везде `from __future__ import annotations`, строгий `mypy`
2. **100% docstrings** — каждый модуль/класс/функция
3. **Тесты pytest** — 51 тест, `conftest.py` фикстуры, моки `pg_dump`/`S3`
4. **≤500 строк/файл** — проверяется `test_file_size_limit`
5. **SOLID + Clean Architecture** — `entities` → `ports` → `adapters` → `service` → `api/cli/scheduler`
6. **Обработка ошибок** — иерархия `BackupError`, `try/except`, `details`
7. **structlog** — JSON в Docker, Console в CLI
8. **OpenAPI/Swagger** — FastAPI автогенерация на `/docs`

```bash
pytest --cov=scripts/backup --cov-report=term-missing  # 51 passed
ruff check scripts/backup tests
mypy scripts/backup
```

---

## 🐛 FAQ и Troubleshooting

См. раздел «Возможные проблемы» в [`docs/step1_infrastructure.md`](docs/step1_infrastructure.md) (12 сценариев: 502, pg auth failed, certbot, S3, OOM, …).

Быстрые проверки:

```bash
docker compose ps                    # все healthy?
docker compose logs odoo --tail=100  # ошибки Odoo?
docker compose exec db pg_isready -U odoo
docker compose exec redis redis-cli ping
curl -f http://localhost/health && echo ok
curl -f http://localhost:8069/web/health && echo odoo ok
```

---

## 🗺️ Дорожная карта

| Шаг | Статус | Содержание |
|-----|--------|------------|
| **1** | ✅ **Готов** | Инфраструктура: Docker, Postgres 15, Nginx + SSL, бэкапы |
| **2** | ⏳ Ожидает подтверждения | Модули CRM-ядра: `crm`, `sale_management`, `account`, `purchase`, `project`, `website`, `calendar`, `contacts`, `marketing_automation`, `documents` |
| **3** | 📋 Запланирован | Кастом `custom_crm_extended`: UTM, скоринг, воронки, дашборды, email, Telegram |
| **4** | 📋 | API: XML-RPC/JSON-RPC, REST (`odoo-rest-api`), телефония Asterisk, IMAP/SMTP |
| **5** | 📋 | Безопасность: RBAC, 2FA, аудит-логи, firewall, fail2ban |
| **6** | 📋 | Производительность: workers, Redis сессии, CDN, Prometheus+Grafana |

> **Дайте знать когда готовы к Шагу 2** — установлю и настрою все CRM-модули.

---

## 📄 Лицензия

MIT — см. `LICENSE` (если отсутствует — считается MIT).

---

## 🤝 Контакты

- DevOps: `admin@example.com`
- Документация Шага 1: [`docs/step1_infrastructure.md`](docs/step1_infrastructure.md)
- Swagger: `https://crm.example.com/backup-api/docs`
