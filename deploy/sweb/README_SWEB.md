# Развёртывание Odoo 17 на SpaceWeb (sweb.ru)

> **Коротко: напрямую на *виртуальном (shared) хостинге* SpaceWeb Odoo запустить НЕЛЬЗЯ.**
> Shared даёт только PHP 5-8.4 + MySQL 8 / PostgreSQL 14 для PHP-сайтов, без Docker, без root, без долгоживущих Python-процессов и без `workers`. Odoo требует Python 3.11, PostgreSQL, Redis, долгоживущий `odoo:8069/8072` и Nginx.
>
> **Правильный путь на SpaceWeb — Cloud VPS (от 291₽/мес).** Shared оставьте для лендинга/почты, а CRM ставьте на VPS и прокиньте поддомен.

---

## Вариант A — Рекомендуемый: Cloud VPS SpaceWeb (5 минут)

Подходит для всех тарифов Cloud: **Promo (1 vCPU/1 ГБ/10 ГБ, ~329₽)** → **Lite (2 vCPU/2 ГБ/15 ГБ)** → **Plus (2/4/40)**. Наш `docker-compose.sweb.yml` уже оптимизирован под 1-2 ГБ RAM (`workers=2`).

### 1. Заказать VPS в панели SpaceWeb

1. https://sweb.ru → **Хостинг → Облачные серверы (VPS)** → **Заказать**
2. Выберите **Cloud Promo/Lite** (для старта хватит Promo), ОС **Ubuntu 22.04** или **24.04**, добавьте SSH-ключ (или пароль root)
3. Оплатите, дождитесь IP (например `185.XXX.XXX.XXX`) в письме и в панели **Серверы → Управление**
4. В **Домены → DNS** создайте `A` запись `crm.yourdomain.ru → 185.XXX.XXX.XXX` (или в панели регистратора домена)

### 2. Подключиться и установить в 1 команду

```bash
ssh root@185.XXX.XXX.XXX
# Вариант 1 — авто-установщик (клонирует, генерит .env, ставит Docker, запускает, выпускает SSL):
curl -fsSL https://raw.githubusercontent.com/Roses1s/bot/arena/01a0a63e-bot/deploy/sweb/install.sh | bash -s crm.yourdomain.ru admin@yourdomain.ru

# Вариант 2 — вручную:
git clone -b arena/01a0a63e-bot https://github.com/Roses1s/bot.git /opt/odoo-crm
cd /opt/odoo-crm
cp .env.example .env && nano .env  # заполните DOMAIN, EMAIL, POSTGRES_PASSWORD
make up-sweb   # см. Makefile ниже
make ssl-init DOMAIN=crm.yourdomain.ru EMAIL=admin@yourdomain.ru
```

**Что делает `install.sh`:**
- ставит `docker.io + compose-plugin`, `ufw allow 80,443,22`
- генерит `POSTGRES_PASSWORD`/`ODOO_ADMIN_PASSWD` (`openssl rand -base64 32`)
- запускает `docker compose -f docker-compose.yml -f deploy/sweb/docker-compose.sweb.yml up -d`
- ждёт 90с, проверяет `curl /health`, `pg_isready`
- выпускает Let's Encrypt через `scripts/ssl/init-letsencrypt.sh`

### 3. Проверка

```bash
docker compose -f docker-compose.yml -f deploy/sweb/docker-compose.sweb.yml ps  # все healthy?
curl -I https://crm.yourdomain.ru/health   # 200
curl https://crm.yourdomain.ru/backup-api/docs  # Swagger
make backup  # ручной бэкап
```

Логи: `docker compose -f docker-compose.yml -f deploy/sweb/docker-compose.sweb.yml logs -f odoo --tail=200`

### 4. Makefile для SpaceWeb (добавьте в корень `Makefile` если нет)

```make
up-sweb:
	docker compose -f docker-compose.yml -f deploy/sweb/docker-compose.sweb.yml up -d --build
down-sweb:
	docker compose -f docker-compose.yml -f deploy/sweb/docker-compose.sweb.yml down
logs-sweb:
	docker compose -f docker-compose.yml -f deploy/sweb/docker-compose.sweb.yml logs -f
```

### 5. Обновление

```bash
cd /opt/odoo-crm && git pull
docker compose -f docker-compose.yml -f deploy/sweb/docker-compose.sweb.yml pull
docker compose -f docker-compose.yml -f deploy/sweb/docker-compose.sweb.yml up -d
```

### 6. Бэкапы на SpaceWeb

- Локально: `./backups/` на VPS (NVMe) + ротация `BACKUP_RETENTION_DAYS=30`
- В панели SpaceWeb: **Серверы → Бэкапы → ежедневные** (входит в тариф 30 ГБ бэкап-диска) — включите!
- S3: укажите в `.env` `AWS_S3_BUCKET` (можно Yandex Object Storage / SpaceWeb S3) — дублирует
- Проверка: `docker compose exec backup python -m app.cli healthcheck`

### 7. Сколько ресурсов нужно?

| Тариф SpaceWeb | RAM | Рекомендовано `workers` | Подходит? |
|----------------|-----|-------------------------|-----------|
| Cloud Promo 1/1/10 | 1 ГБ | `workers=2`, `max_cron_threads=1` | ✅ минимум, медленно при 5+ юзерах |
| Cloud Lite 2/2/15 | 2 ГБ | `workers=2` | ✅ оптимальный старт |
| Cloud Plus 2/4/40 | 4 ГБ | `workers=3-4` | ✅ комфорт + S3 + мониторинг |
| Shared Старт/Взлёт/Космос | — | — | ❌ нельзя (только PHP) |

Если у вас уже Promo 1 ГБ и Odoo падает по OOM — уменьшите в `deploy/sweb/odoo.sweb.conf` `limit_memory_soft=768M`.

---

## Вариант B — У вас уже оплачен Shared и не хотите VPS?

Odoo всё равно нужен VPS, но shared можно использовать умно:

**Схема:**
```
yourdomain.ru (shared, PHP, лендинг WordPress/Bitrix)
crm.yourdomain.ru (A → IP VPS, Odoo 17)
mail.yourdomain.ru (shared, почта — остаётся)
```

**Шаги:**
1. Закажите самый дешёвый Cloud Promo (329₽) только под Odoo (см. Вариант A)
2. В панели SpaceWeb **Домены → crm → A → IP VPS**
3. На shared оставьте сайт: `public_html/` — ваш лендинг, `public_html/crm/.htaccess` — не нужен, т.к. CRM на поддомене
4. Если хотите чтобы `yourdomain.ru/crm` открывал Odoo — положите в `public_html/crm/.htaccess` наш [deploy/sweb/.htaccess](.htaccess) и попросите ТП SpaceWeb включить `mod_proxy` (не на всех тарифах дают; если не дают — делайте 301 редирект на `crm.yourdomain.ru` — см. комментарии в файле)

**Почему нельзя Odoo в `public_html`:** shared убивает процессы через 30-60с, нет `systemd`, нет Docker, `pip install` ограничен, PostgreSQL 14 на shared — для PHP, а Odoo нужен `CREATE EXTENSION pg_trgm`, `workers` и `longpolling 8072`.

---

## Вариант C — Попытка без Docker (не рекомендуется, для справки)

Если ТП SpaceWeb дала вам SSH на shared и вы хотите рискнуть:

```bash
# На shared (через SSH, если дали)
python3 -m venv ~/odoo-venv && source ~/odoo-venv/bin/activate
pip install odoo==17.0
# PostgreSQL возьмите из панели SpaceWeb: Базы данных → PostgreSQL 14 → создать odoo, запомните host/user/pass
# Но: Odoo упадёт через минуту — shared киляет долгоживущие процессы, нет 8072, нет workers
```

**Итог:** потратите день и упрётесь в лимиты. Берите VPS.

---

## SSL на SpaceWeb

- **На VPS:** наш `certbot` сам выпускает Let's Encrypt (см. `make ssl-init`). Альтернатива — в панели SpaceWeb **SSL → Let's Encrypt** → выпустить для `crm.*` и проксировать, но проще нашим скриптом.
- **На shared:** **Хостинг → SSL → Let's Encrypt → выпустить** для `yourdomain.ru` в 1 клик (авто-продление).

---

## Частые вопросы SpaceWeb

**Можно ли MySQL вместо PostgreSQL?** Нет, Odoo поддерживает только PostgreSQL.

**Дадут ли root на shared?** Нет, только на VPS.

**Перенос домена?** В панели **Домены → Перенести**, SpaceWeb даёт +4 месяца при оплате за 6 мес.

**Техподдержка:** `support@sweb.ru`, чат в панели — попросите включить `mod_proxy` если нужен `.htaccess` прокси.

---

## Что дальше?

После успешной установки на VPS SpaceWeb сообщите — перейду к **Шагу 2** (модули CRM: `crm`, `sale_management`, `account` и т.д.) уже с учётом `deploy/sweb` оверлея.
