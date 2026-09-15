#!/usr/bin/env bash
# =============================================================================
# install.sh — авто-установка Odoo 17 на SpaceWeb Cloud VPS (Ubuntu 22.04/24.04)
# Запускать по SSH от root на чистом VPS SpaceWeb
#   ssh root@185.XXX.XXX.XXX
#   curl -fsSL https://raw.githubusercontent.com/Roses1s/bot/arena/01a0a63e-bot/deploy/sweb/install.sh | bash
# Или:
#   git clone https://github.com/Roses1s/bot.git /opt/odoo-crm && cd /opt/odoo-crm && bash deploy/sweb/install.sh
# =============================================================================
set -euo pipefail

REPO_URL="https://github.com/Roses1s/bot.git"
BRANCH="arena/01a0a63e-bot"
APP_DIR="/opt/odoo-crm"
DOMAIN="${1:-}"
EMAIL="${2:-}"

echo "=== SpaceWeb VPS — установка Odoo 17 CRM ==="
echo "APP_DIR=$APP_DIR  DOMAIN=$DOMAIN  EMAIL=$EMAIL"
echo ""

# Проверка root
if [[ $EUID -ne 0 ]]; then echo "Запустите от root: sudo bash $0"; exit 1; fi

# 1. Обновление и Docker
echo "[1/7] Установка Docker..."
apt-get update -qq
apt-get install -y -qq curl git make ufw ca-certificates gnupg

if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
fi
docker --version
docker compose version || (apt-get install -y docker-compose-plugin && docker compose version)

# 2. Клонирование
echo "[2/7] Клонирование репозитория..."
if [[ -d "$APP_DIR/.git" ]]; then
  echo "  уже есть $APP_DIR — pull"
  git -C "$APP_DIR" fetch origin "$BRANCH" && git -C "$APP_DIR" checkout "$BRANCH" && git -C "$APP_DIR" pull origin "$BRANCH"
else
  git clone -b "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

# 3. .env
echo "[3/7] Настройка .env..."
if [[ ! -f .env ]]; then
  cp .env.example .env
  # Генерируем пароли
  PG_PASS=$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 32)
  ADMIN_PASS=$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 32)
  # Подставляем
  sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$PG_PASS/" .env
  sed -i "s/^ODOO_ADMIN_PASSWD=.*/ODOO_ADMIN_PASSWD=$ADMIN_PASS/" .env
  if [[ -n "$DOMAIN" ]]; then sed -i "s/^DOMAIN=.*/DOMAIN=$DOMAIN/" .env; fi
  if [[ -n "$EMAIL" ]]; then sed -i "s/^EMAIL=.*/EMAIL=$EMAIL/" .env; fi
  echo "  Сгенерирован POSTGRES_PASSWORD и ODOO_ADMIN_PASSWD (см. .env)"
  echo "  ВАЖНО: смените DOMAIN и EMAIL в .env если не передали аргументами!"
  grep -E "^(DOMAIN|EMAIL|POSTGRES_PASSWORD|ODOO_ADMIN_PASSWD)=" .env | sed 's/POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=***/;s/ODOO_ADMIN_PASSWD=.*/ODOO_ADMIN_PASSWD=***/'
else
  echo "  .env уже существует — пропускаем генерацию"
fi

# 4. Firewall
echo "[4/7] Настройка UFW..."
ufw --force enable || true
ufw allow 22/tcp || true
ufw allow 80/tcp || true
ufw allow 443/tcp || true
ufw status

# 5. Запуск (облегчённый для SpaceWeb)
echo "[5/7] Запуск Docker Compose (sweb overlay)..."
# Используем облегчённый конфиг для 1-2 ГБ RAM
docker compose -f docker-compose.yml -f deploy/sweb/docker-compose.sweb.yml pull || true
docker compose -f docker-compose.yml -f deploy/sweb/docker-compose.sweb.yml up -d --build
echo "  Ждём 90 сек пока Odoo прогреется..."
sleep 90
docker compose -f docker-compose.yml -f deploy/sweb/docker-compose.sweb.yml ps
docker compose -f docker-compose.yml -f deploy/sweb/docker-compose.sweb.yml logs --tail=100 odoo || true

# 6. Проверка health
echo "[6/7] Проверка health..."
curl -fsS http://localhost/health && echo "  nginx health OK" || echo "  nginx не отвечает — смотрите logs"
curl -fsS http://localhost:8069/web/health && echo "  odoo direct OK" || echo "  odoo ещё стартует"
docker compose -f docker-compose.yml -f deploy/sweb/docker-compose.sweb.yml exec -T db pg_isready -U odoo && echo "  postgres OK" || true

# 7. SSL (если передали домен)
if [[ -n "$DOMAIN" && -n "$EMAIL" ]]; then
  echo "[7/7] Выпуск Let's Encrypt для $DOMAIN..."
  # Домен должен уже указывать на IP VPS!
  if make ssl-init DOMAIN="$DOMAIN" EMAIL="$EMAIL"; then
    echo "  SSL OK: https://$DOMAIN/health"
  else
    echo "  SSL не выпустился — проверьте DNS A-запись: dig $DOMAIN должен указывать на $(curl -s ifconfig.me)"
    echo "  Попробуйте позже: make ssl-init DOMAIN=$DOMAIN EMAIL=$EMAIL"
  fi
else
  echo "[7/7] SSL пропущен — укажите DOMAIN и EMAIL:"
  echo "  make ssl-init DOMAIN=crm.example.com EMAIL=admin@example.com"
  echo "  Или запустите: bash deploy/sweb/install.sh crm.example.com admin@example.com"
fi

echo ""
echo "=== Готово ==="
echo "Odoo: http://$(curl -s ifconfig.me || echo YOUR_IP)  (до выпуска SSL)  или https://$DOMAIN"
echo "Логи: docker compose -f docker-compose.yml -f deploy/sweb/docker-compose.sweb.yml logs -f odoo"
echo "Бэкап: make backup && curl https://$DOMAIN/backup-api/docs"
echo "Документация: cat docs/step1_infrastructure.md && cat deploy/sweb/README_SWEB.md"
