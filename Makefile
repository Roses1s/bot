.PHONY: help up down restart logs ps clean backup restore ssl-init test lint

# Переменные
COMPOSE ?= docker compose
ENV_FILE ?= .env

help:  ## Показать помощь
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

up:  ## Запустить весь стек (prod)
	$(COMPOSE) up -d --build
	@echo "✓ Stack started. Logs: make logs"

up-dev:  ## Запустить в dev-режиме (без nginx/certbot/backup)
	$(COMPOSE) up -d db redis odoo

down:  ## Остановить стек
	$(COMPOSE) down

restart:  ## Перезапустить Odoo
	$(COMPOSE) restart odoo
	$(COMPOSE) logs -f odoo

logs:  ## Логи всех сервисов
	$(COMPOSE) logs -f --tail=200

logs-odoo:  ## Логи только Odoo
	$(COMPOSE) logs -f odoo

ps:  ## Статус контейнеров
	$(COMPOSE) ps
	docker volume ls | grep odoo || true

clean:  ## Остановить и удалить volumes (ОПАСНО: удаляет БД!)
	@echo "⚠️  This will DELETE all data! Press Ctrl+C to cancel, Enter to continue"; read x
	$(COMPOSE) down -v

backup:  ## Ручной бэкап БД
	$(COMPOSE) exec backup python -m app.cli backup --verbose

restore:  ## Восстановление (укажите FILE=backups/xxx.sql.gz)
	@if [ -z "$(FILE)" ]; then echo "Usage: make restore FILE=backups/odoo_20240101.sql.gz"; exit 1; fi
	$(COMPOSE) exec backup python -m app.cli restore --file $(FILE)

ssl-init:  ## Инициализация Let's Encrypt (требует DOMAIN и EMAIL)
	@if [ -z "$(DOMAIN)" ] || [ -z "$(EMAIL)" ]; then echo "Usage: make ssl-init DOMAIN=example.com EMAIL=admin@example.com"; exit 1; fi
	bash scripts/ssl/init-letsencrypt.sh $(DOMAIN) $(EMAIL)

test:  ## Запустить тесты
	pytest -v --cov=scripts/backup --cov-report=term-missing

lint:  ## Линт + типы
	ruff check scripts/backup tests
	mypy scripts/backup

odoo-shell:  ## Odoo shell
	$(COMPOSE) exec odoo odoo shell -c /etc/odoo/odoo.conf

psql:  ## Подключиться к PostgreSQL
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-odoo} $${POSTGRES_DB:-odoo}

health:  ## Проверка здоровья сервисов
	curl -f http://localhost/health && echo "nginx OK" || echo "nginx FAIL"
	curl -f http://localhost:8069/web/health && echo "odoo OK" || echo "odoo FAIL (direct)"
	$(COMPOSE) exec db pg_isready -U $${POSTGRES_USER:-odoo} && echo "postgres OK"
	$(COMPOSE) exec redis redis-cli ping

update-odoo:  ## Обновить Odoo образ и перезапустить
	$(COMPOSE) pull odoo db nginx redis
	$(COMPOSE) up -d

# --- SpaceWeb Cloud VPS (sweb.ru) ---
up-sweb:  ## Запустить на SpaceWeb VPS (облегчённый, 1-2 ГБ RAM)
	docker compose -f docker-compose.yml -f deploy/sweb/docker-compose.sweb.yml up -d --build

down-sweb:  ## Остановить SpaceWeb стек
	docker compose -f docker-compose.yml -f deploy/sweb/docker-compose.sweb.yml down

logs-sweb:  ## Логи SpaceWeb стека
	docker compose -f docker-compose.yml -f deploy/sweb/docker-compose.sweb.yml logs -f --tail=200

ps-sweb:
	docker compose -f docker-compose.yml -f deploy/sweb/docker-compose.sweb.yml ps

install-sweb:  ## Авто-установка на SpaceWeb VPS (требует DOMAIN и EMAIL)
	@if [ -z "$(DOMAIN)" ] || [ -z "$(EMAIL)" ]; then echo "Usage: make install-sweb DOMAIN=crm.example.com EMAIL=admin@example.com"; exit 1; fi
	bash deploy/sweb/install.sh $(DOMAIN) $(EMAIL)
