-- 01-init-db.sql — инициализация PostgreSQL для Odoo
-- Выполняется один раз при создании volume postgres_data

-- Расширения, полезные для Odoo / мониторинга
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- для быстрого ilike поиска в Odoo

-- Отдельная схема для бэкап-метаданных (опционально)
-- CREATE SCHEMA IF NOT EXISTS ops;
-- CREATE TABLE IF NOT EXISTS ops.backup_log (...);

-- Настройки для Odoo — unaccent для поиска
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Пользователь уже создан через POSTGRES_USER, но можно добавить права
-- ALTER USER odoo WITH SUPERUSER; -- НЕ рекомендуется в продакшене; только если нужны расширения

-- Проверка
SELECT 'Postgres init OK' AS status, version();
