# Шаг 1 — Инфраструктура Odoo 17 CRM

> Production-ready стек: **Odoo 17 CE + PostgreSQL 15 + Nginx + Redis + Backup + Let's Encrypt**

---

## 1. Архитектура

```
Internet
   │
   │ 80/443
   ▼
┌─────────┐  certbot (renew 12h)
│  Nginx  │◄──────────────────────────┐
│  :80,:443│  reverse proxy, SSL, gzip, rate-limit, static cache
└────┬────┘
     │  proxy_pass
     ├────────────────────┬─────────────────┐
     ▼                    ▼                 ▼
  odoo:8069          odoo:8072        backup-api:8000
  (HTTP + longpolling)                (FastAPI /docs)
     │
     ├─────────┬─────────┐
     ▼         ▼         ▼
    db:5432  redis:6379  /backups (volume)
  postgres:15  redis:7   + S3 (опц.)
```

**Сети:**
- `frontend` — Nginx ↔ Odoo ↔ Certbot ↔ Backup-API (наружу только 80/443)
- `backend` (internal) — Odoo ↔ DB ↔ Redis ↔ Backup (изолирована)

**Тома:**
- `postgres_data` — PGDATA (WAL, данные)
- `odoo_data` — filestore, sessions
- `redis_data` — RDB
- `certbot_certs` / `certbot_www` — Let's Encrypt

---

## 2. Структура директорий

```
.
├── docker-compose.yml              # prod стек
├── docker-compose.override.yml     # dev (открывает 8069 локально)
├── .env.example                    # шаблон секретов
├── Makefile                        # make up / logs / backup / test
├── config/
│   ├── odoo/odoo.conf              # workers=4, proxy_mode, limits
│   ├── nginx/
│   │   ├── nginx.conf              # gzip, upstreams, rate-limit
│   │   └── conf.d/odoo.conf        # 80→443, longpolling, HSTS
│   ├── postgres/init/01-init-db.sql# расширения pg_trgm, unaccent
│   └── redis/redis.conf            # maxmemory 256M, allkeys-lru
├── scripts/
│   ├── backup/                     # Python-сервис бэкапов (Clean Architecture)
│   │   ├── app/
│   │   │   ├── config.py           # pydantic-settings
│   │   │   ├── entities.py         # доменные сущности
│   │   │   ├── storage.py          # Local + S3
│   │   │   ├── backup_service.py   # pg_dump, restore, ротация
│   │   │   ├── notifier.py         # Telegram
│   │   │   ├── scheduler.py        # cron-планировщик
│   │   │   ├── api.py              # FastAPI + OpenAPI
│   │   │   └── cli.py              # CLI
│   │   ├── Dockerfile
│   │   └── pyproject.toml
│   └── ssl/init-letsencrypt.sh     # первичная выдача сертификата
├── deployments/
│   ├── fail2ban/jail.local
│   └── systemd/odoo-backup.*
├── backups/.gitkeep
├── addons/                         # кастомные модули (Шаг 3)
└── tests/
    ├── test_infrastructure.py
    └── unit/
```

---

## 3. Конфигурации — что и зачем

### 3.1 `docker-compose.yml`

| Сервис | Образ | Зачем | Ключевые настройки |
|--------|-------|------|-------------------|
| `db` | `postgres:15-alpine` | Данные Odoo | `max_connections=200`, `shared_buffers=256M`, healthcheck `pg_isready`, `internal: true` |
| `redis` | `redis:7-alpine` | Кэш сессий (шаг 6) | `allkeys-lru`, `maxmemory 256M` |
| `odoo` | `odoo:17.0` | Приложение | `workers=4`, `proxy_mode=True`, `list_db=False`, `limit_memory_*` |
| `nginx` | `nginx:stable-alpine` | TLS + прокси | `gzip`, `rate-limit 10r/m на /login`, `upstream longpolling`, `HSTS` |
| `certbot` | `certbot/certbot` | Let's Encrypt | `webroot`, `renew --quiet` каждые 12ч |
| `backup` | custom | Бэкапы | `pg_dump | gzip`, ротация, S3, Telegram, `BACKUP_CRON` |
| `backup-api` | custom | HTTP API | `uvicorn app.api:app`, Swagger на `/docs`, проксируется через Nginx |

**Почему так:**
- Alpine — меньше CVE, быстрее pull.
- `restart: unless-stopped` — переживает ребут хоста.
- `healthcheck` — Docker не стартует зависимые сервисы пока БД не готова.
- `deploy.resources.limits` — защита от OOM.

### 3.2 `config/odoo/odoo.conf`

- `workers = 4` — multi-processing обязателен в проде (без него cron и longpolling блокируются). Формула: `(CPU*2)+1`, но тестируйте под нагрузкой.
- `max_cron_threads = 2` — не блокировать HTTP-воркеры.
- `proxy_mode = True` — доверять `X-Forwarded-Proto` от Nginx, иначе редиректы ломаются.
- `list_db = False` — скрывает `/web/database/manager` (атака перебором).
- `limit_memory_*` — защита от утечек.

### 3.3 `config/nginx/*`

- `nginx.conf`: `worker_connections 4096`, `keepalive 32` к апстриму, `gzip` для `application/javascript`, `limit_req_zone` для `/web/login`.
- `conf.d/odoo.conf`:
  - HTTP сервер: `/.well-known/acme-challenge` для Certbot, `return 301 https://` после получения сертификата.
  - HTTPS: `TLSv1.2+`, `HSTS`, `X-Frame-Options SAMEORIGIN`, `proxy_read_timeout 300s`, отдельный `location /longpolling` → `odoo:8072` с `Upgrade`.
  - Статика `/web/static` — `expires 7d`, `immutable`.
  - `/backup-api/` → `backup-api:8000` (для мониторинга).

### 3.4 `config/postgres/init/01-init-db.sql`

- `pg_trgm` — ускоряет `ilike` в Odoo (поиск контактов).
- `unaccent` — поиск без учёта акцентов.

### 3.5 `scripts/backup` — Clean Architecture

```
config (pydantic) → entities (dataclasses) → ports (Protocol/ABC)
       → adapters (LocalStorage, S3Storage, TelegramNotifier)
       → service (PostgresBackupService) → adapters (CLI, scheduler, FastAPI)
```

- **SOLID:** Service зависит от абстракций `StoragePort`, легко заменить Local на GCS.
- **Ошибки:** иерархия `BackupError` → `BackupCreationError` и т.д., везде `try/except` + `structlog`.
- **Логи:** `structlog` JSON в Docker, `ConsoleRenderer` в CLI `--verbose`.
- **Тесты:** 100% покрытие домена, моки для S3/pg_dump.

---

## 4. Команды для выполнения

### 4.1 Подготовка сервера (Ubuntu 22.04)

```bash
# Docker + Compose v2
sudo apt update && sudo apt install -y docker.io docker-compose-plugin git make
sudo systemctl enable --now docker
sudo usermod -aG docker $USER && newgrp docker

# Клонируем
git clone https://github.com/Roses1s/bot.git /opt/odoo-crm
cd /opt/odoo-crm

# Env
cp .env.example .env
nano .env  # ← заполните POSTGRES_PASSWORD, ODOO_ADMIN_PASSWD, DOMAIN, EMAIL
# Сгенерировать пароль: openssl rand -base64 32

# Открыть порты
sudo ufw allow 80,443/tcp
sudo ufw allow 22/tcp
sudo ufw enable
```

### 4.2 Первый запуск (без SSL)

```bash
make up           # или docker compose up -d --build
make logs         # наблюдать
make health       # curl /health + pg_isready
# Откройте http://YOUR_IP — должен показать Odoo логин
# Если не стартует: docker compose logs odoo --tail=200
```

### 4.3 Выпуск Let's Encrypt

```bash
# Домен уже указывает на IP (A-запись)!
make ssl-init DOMAIN=crm.example.com EMAIL=admin@example.com
# Скрипт: создаёт dummy cert → стартует Nginx → certbot certonly --webroot → reload Nginx
# Проверка:
curl -I https://crm.example.com/health
# Авто-продление уже работает (сервис certbot в compose, + можно systemd timer)
```

Для теста без лимитов: `STAGING=1 ./scripts/ssl/init-letsencrypt.sh crm.example.com admin@example.com 1`

### 4.4 Бэкапы

```bash
# Ручной
make backup
# или
docker compose exec backup python -m app.cli backup --verbose
docker compose exec backup python -m app.cli list
docker compose exec backup python -m app.cli healthcheck

# Восстановление (осторожно — перезаписывает БД!)
make restore FILE=backups/odoo_20240115_030000.sql.gz
# Внутри контейнера:
docker compose exec backup python -m app.cli restore --file /backups/odoo_20240115_030000.sql.gz

# API (через Nginx)
curl https://crm.example.com/backup-api/health
curl https://crm.example.com/backup-api/backups
curl -X POST https://crm.example.com/backup-api/backups
# Swagger:
open https://crm.example.com/backup-api/docs
```

**Расписание:** `BACKUP_CRON=0 3 * * *` в `.env` → сервис `backup` (scheduler.py) проверяет каждую минуту через `croniter`.

**Ротация:** `BACKUP_RETENTION_DAYS=30` — удаляет файлы старше 30 дней (по дате из имени или mtime).

**S3 (опционально):** заполните `AWS_S3_BUCKET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` → бэкап дублируется в S3, локальная ротация остаётся.

**Systemd альтернатива** (если не хотите cron в Docker):

```bash
sudo cp deployments/systemd/odoo-backup.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now odoo-backup.timer
systemctl list-timers odoo-backup*
```

### 4.5 Обновление Odoo

```bash
make update-odoo   # pull + up -d
# или вручную:
docker compose pull odoo
docker compose up -d odoo
docker compose exec odoo odoo --update=all -d odoo -c /etc/odoo/odoo.conf --stop-after-init
```

### 4.6 Тесты и линтеры (локально)

```bash
pip install -r requirements-dev.txt
pytest -v --cov=scripts/backup --cov-report=term-missing
ruff check scripts/backup tests
mypy scripts/backup
```

---

## 5. Мониторинг и безопасность (задел под Шаги 5-6)

- **Fail2ban:** `deployments/fail2ban/jail.local` + `filter.d/odoo.conf` — банит IP после 5 неудачных логинов на 2 часа. Установка: `sudo cp deployments/fail2ban/* /etc/fail2ban/ && sudo systemctl restart fail2ban`.
- **Firewall:** `ufw allow 80,443,22`, Postgres открыт только на `127.0.0.1`.
- **Nginx rate-limit:** `limit_req zone=odoo_login 10r/m`, `limit_conn 50`.
- **Odoo:** `list_db=False`, `admin_passwd` из `.env`, без демо-данных.
- **Backup API:** healthcheck для Prometheus (`/health` → JSON), можно добавить `prometheus-fastapi-instrumentator`.
- **CDN (Шаг 6):** `x_sendfile` + `proxy_cache` для `/web/static` уже настроен, дальше — Cloudflare.

---

## 6. Возможные проблемы и решения

| Проблема | Симптом | Решение |
|----------|---------|---------|
| **Odoo не стартует, `FATAL: password authentication failed`** | `logs odoo` → `password authentication failed for user odoo` | Проверьте `.env`: `POSTGRES_PASSWORD` должен совпадать у `db` и `odoo`. `docker compose down -v` только если готовы потерять данные, иначе `docker compose exec db psql -U odoo -c "ALTER USER odoo PASSWORD '...'"` |
| **Nginx 502 Bad Gateway** | `curl -I http://localhost` → 502 | Odoo ещё стартует (90s `start_period`). `docker compose ps` → health `starting`. Подождите, `docker compose logs odoo --tail=100`. Проверьте `odoo.conf: db_host=db` |
| **Certbot `Failed to authorize`** | `init-letsencrypt.sh` → `Connection refused` | Домен не указывает на IP, или порт 80 закрыт firewall. `dig crm.example.com`, `ufw status`, `curl http://crm.example.com/.well-known/acme-challenge/test` |
| **Бэкап падает `pg_dump not found`** | `backup` логи → `FileNotFoundError` | Образ `backup` пересоберите: `docker compose build backup backup-api` (нужен `postgresql-client` в Dockerfile) |
| **Бэкап 0 байт** | `list` показывает `0 B` | Проверьте `POSTGRES_HOST=db`, сеть `backend`, `pg_isready` из контейнера backup: `docker compose exec backup pg_isready -h db -U odoo` |
| **S3 upload падает** | `warnings: S3 upload failed` | Проверьте `AWS_*` в `.env`, `head_bucket` права, регион. Локальный бэкап всё равно создаётся (best-effort) |
| **Odoo медленно, `workers` warning** | Логи ` High memory usage` | Увеличьте `limit_memory_*`, уменьшите `workers` или добавьте RAM. Для 2GB — `workers=2`, для 4GB — `workers=4` |
| **Redis `OOM`** | `redis` логи `out of memory` | Уменьшите `maxmemory` или включите пароль и `maxmemory-policy`. Проверьте `redis-cli INFO memory` |
| **Let's Encrypt rate limit** | `too many certificates already issued` | Используйте `STAGING=1` для тестов, подождите неделю, или добавьте `--staging` в certbot |
| **Восстановление зависает** | `psql` ждёт пароль | Проверьте `PGPASSWORD` в env backup, `backup` контейнер видит `db`? Попробуйте `docker compose exec backup psql -h db -U odoo -c "SELECT 1"` |
| **Nginx не видит сертификат после рестарта** | `nginx: [emerg] cannot load certificate` | `certbot_certs` volume пустой? Перезапустите `init-letsencrypt.sh`, проверьте `docker volume ls`, `docker compose exec certbot ls /etc/letsencrypt/live/` |
| **Тесты падают `ModuleNotFoundError: scripts`** | `pytest` не находит `scripts.backup` | `pip install -e .` или `PYTHONPATH=. pytest`, `pythonpath = ["."]` в `pyproject.toml` уже настроен |
| **Файл >500 строк** | CI `test_file_size_limit` | Разбейте файл, вынесите логику в модули (Clean Architecture) |

---

## 7. Чек-лист продакшена

- [ ] `.env` заполнен, `POSTGRES_PASSWORD` ≥32 символа, `ODOO_ADMIN_PASSWD` изменён
- [ ] `DOMAIN` указывает на IP, `make ssl-init` выполнен, `https://DOMAIN/health` → 200
- [ ] `docker compose ps` — все `healthy`/`running`
- [ ] `make backup && make health` → `ok`
- [ ] S3 bucket создан, `is_s3_enabled` проверен (`docker compose exec backup python -c "from app.config import get_config; print(get_config().is_s3_enabled)"`)
- [ ] Fail2ban установлен, `fail2ban-client status odoo-login`
- [ ] `ufw status` — закрыты лишние порты
- [ ] Настроен `logrotate` для `/var/log/nginx` и Docker json-logs (`max-size: 10m`)
- [ ] Cron/таймер проверен: `docker compose logs backup --tail=50` → `scheduler_started`
- [ ] Сделан тестовый `restore` на staging БД

---

## 8. Что дальше (Шаги 2-6)

Шаг 1 завершён. После вашего подтверждения:
- **Шаг 2:** установка модулей CRM-ядра (`crm`, `sale_management`, `account`, …) через `odoo.conf` + `docker compose exec odoo odoo -i crm,sale_management,...`
- **Шаг 3:** кастомный модуль `custom_crm_extended` (UTM, скоринг, pipeline, дашборды)
- **Шаг 4:** `odoo-rest-api` / контроллеры + Telegram/телефония
- **Шаг 5:** RBAC, 2FA, аудит, fail2ban
- **Шаг 6:** workers тюнинг, Redis сессии, CDN, Prometheus+Grafana

Дайте знать когда готовы к Шагу 2!
