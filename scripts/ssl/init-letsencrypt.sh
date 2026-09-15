#!/usr/bin/env bash
# =============================================================================
# init-letsencrypt.sh — первичная выдача Let's Encrypt сертификатов
# Использование: ./scripts/ssl/init-letsencrypt.sh example.com admin@example.com [staging]
# Требует: docker compose, установленный .env
# Источник: https://github.com/wmnnd/nginx-certbot (адаптирован)
# =============================================================================
set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"
STAGING="${3:-0}"

if [[ -z "$DOMAIN" || -z "$EMAIL" ]]; then
  echo "Usage: $0 <domain> <email> [staging: 0|1]"
  echo "Example: $0 crm.example.com admin@example.com"
  exit 1
fi

# Загружаем .env если есть
if [[ -f .env ]]; then
  set -a; source .env; set +a
fi

COMPOSE="docker compose"
RSA_KEY_SIZE=4096
DATA_PATH="./certbot"
NGINX_CONF="./config/nginx/conf.d/odoo.conf"

echo "### Init Let's Encrypt for $DOMAIN (email: $EMAIL, staging: $STAGING) ###"

# Проверка docker
if ! command -v docker &>/dev/null; then
  echo "ERROR: docker not found"
  exit 1
fi

# Создаём dummy сертификат чтобы Nginx стартовал
echo "### Creating dummy certificate for $DOMAIN ..."
mkdir -p "$DATA_PATH/conf/live/$DOMAIN"
if command -v openssl &>/dev/null; then
  openssl req -x509 -nodes -newkey rsa:$RSA_KEY_SIZE -days 1 \
    -keyout "$DATA_PATH/conf/live/$DOMAIN/privkey.pem" \
    -out "$DATA_PATH/conf/live/$DOMAIN/fullchain.pem" \
    -subj "/CN=localhost" 2>/dev/null || {
      echo "openssl failed, creating empty files"
      touch "$DATA_PATH/conf/live/$DOMAIN/privkey.pem" "$DATA_PATH/conf/live/$DOMAIN/fullchain.pem"
    }
else
  touch "$DATA_PATH/conf/live/$DOMAIN/privkey.pem" "$DATA_PATH/conf/live/$DOMAIN/fullchain.pem"
fi

echo "### Starting nginx ..."
$COMPOSE up -d --force-recreate nginx || $COMPOSE up -d nginx

echo "### Deleting dummy certificate ..."
docker compose run --rm --entrypoint "rm -Rf /etc/letsencrypt/live/$DOMAIN && rm -Rf /etc/letsencrypt/archive/$DOMAIN && rm -Rf /etc/letsencrypt/renewal/$DOMAIN.conf" certbot 2>/dev/null || true
rm -Rf "$DATA_PATH/conf/live/$DOMAIN" 2>/dev/null || true

echo "### Requesting Let's Encrypt certificate for $DOMAIN ..."
STAGING_ARG=""
if [[ "$STAGING" == "1" ]]; then
  STAGING_ARG="--staging"
fi

# Запрос сертификата
docker compose run --rm --entrypoint "\
  certbot certonly --webroot -w /var/www/certbot \
    $STAGING_ARG \
    --email $EMAIL \
    --rsa-key-size $RSA_KEY_SIZE \
    --agree-tos --force-renewal \
    -d $DOMAIN" certbot

echo "### Reloading nginx ..."
$COMPOSE exec nginx nginx -s reload || $COMPOSE restart nginx

echo "### Done! Certificate for $DOMAIN created."
echo "### Проверьте: https://$DOMAIN/health"
echo "### Авто-продление уже настроено через сервис certbot (каждые 12ч)."
