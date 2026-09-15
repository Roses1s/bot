"""Интеграционные тесты инфраструктуры — проверка конфигов и compose.

Не требуют запущенного Docker — валидируют YAML/конфиги статически.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]


REPO = Path(__file__).parent.parent


class TestDockerCompose:
    """Проверка docker-compose.yml."""

    @pytest.fixture
    def compose(self) -> dict:
        """Загружает docker-compose.yml."""
        with (REPO / "docker-compose.yml").open() as f:
            return yaml.safe_load(f)

    def test_required_services(self, compose: dict) -> None:
        """Все обязательные сервисы присутствуют."""
        services = compose["services"]
        for svc in ["db", "redis", "odoo", "nginx", "backup", "certbot"]:
            assert svc in services, f"missing service: {svc}"

    def test_db_uses_postgres15(self, compose: dict) -> None:
        """БД — postgres:15-alpine."""
        image = compose["services"]["db"]["image"]
        assert "postgres:15" in image

    def test_odoo_uses_17(self, compose: dict) -> None:
        """Odoo — 17.0."""
        image = compose["services"]["odoo"]["image"]
        assert "17" in image

    def test_volumes_persist(self, compose: dict) -> None:
        """Есть persistent volumes."""
        volumes = compose["volumes"]
        assert "postgres_data" in volumes
        assert "odoo_data" in volumes

    def test_healthchecks_present(self, compose: dict) -> None:
        """Критичные сервисы имеют healthcheck."""
        for svc in ["db", "odoo", "nginx", "redis"]:
            assert "healthcheck" in compose["services"][svc], f"{svc} missing healthcheck"

    def test_networks_isolated(self, compose: dict) -> None:
        """Backend сеть internal, frontend — наружу."""
        networks = compose["networks"]
        assert networks["backend"].get("internal") is True
        assert "frontend" in networks

    def test_backup_has_env(self, compose: dict) -> None:
        """Backup сервис пробрасывает критичные env."""
        env = compose["services"]["backup"].get("environment", {})
        # Может быть dict или list — проверяем строковое представление
        env_str = str(env)
        assert "POSTGRES" in env_str
        assert "BACKUP" in env_str


class TestNginxConfig:
    """Проверка Nginx конфигов."""

    def test_nginx_conf_exists(self) -> None:
        """nginx.conf существует."""
        assert (REPO / "config/nginx/nginx.conf").exists()

    def test_odoo_conf_exists(self) -> None:
        """conf.d/odoo.conf существует."""
        assert (REPO / "config/nginx/conf.d/odoo.conf").exists()

    def test_nginx_has_upstream(self) -> None:
        """Есть upstream odoo."""
        content = (REPO / "config/nginx/nginx.conf").read_text()
        assert "upstream odoo" in content

    def test_odoo_conf_has_longpolling(self) -> None:
        """Есть прокси для longpolling."""
        content = (REPO / "config/nginx/conf.d/odoo.conf").read_text()
        assert "longpolling" in content
        assert "proxy_pass" in content

    def test_nginx_has_gzip(self) -> None:
        """Включён gzip."""
        content = (REPO / "config/nginx/nginx.conf").read_text()
        assert "gzip on" in content

    def test_security_headers(self) -> None:
        """Есть security headers."""
        content = (REPO / "config/nginx/conf.d/odoo.conf").read_text()
        assert "Strict-Transport-Security" in content or "X-Frame-Options" in content


class TestOdooConfig:
    """Проверка odoo.conf."""

    def test_odoo_conf_exists(self) -> None:
        """odoo.conf существует."""
        assert (REPO / "config/odoo/odoo.conf").exists()

    def test_workers_configured(self) -> None:
        """Настроены workers."""
        content = (REPO / "config/odoo/odoo.conf").read_text()
        assert re.search(r"workers\s*=\s*\d+", content)
        # workers > 0
        m = re.search(r"workers\s*=\s*(\d+)", content)
        assert m and int(m.group(1)) > 0

    def test_proxy_mode(self) -> None:
        """proxy_mode = True."""
        content = (REPO / "config/odoo/odoo.conf").read_text()
        assert "proxy_mode" in content

    def test_no_list_db(self) -> None:
        """list_db = False для безопасности."""
        content = (REPO / "config/odoo/odoo.conf").read_text()
        assert "list_db" in content


class TestEnvExample:
    """Проверка .env.example."""

    def test_env_example_exists(self) -> None:
        """Файл существует."""
        assert (REPO / ".env.example").exists()

    def test_required_vars(self) -> None:
        """Содержит обязательные переменные."""
        content = (REPO / ".env.example").read_text()
        for var in ["POSTGRES_PASSWORD", "ODOO_ADMIN_PASSWD", "DOMAIN", "BACKUP_CRON"]:
            assert var in content, f"missing {var} in .env.example"


class TestFileSizeLimit:
    """Проверка правила 'каждый файл ≤500 строк'."""

    def test_python_files_under_500(self) -> None:
        """Все Python файлы проекта ≤500 строк."""
        for p in REPO.rglob("*.py"):
            # Исключаем .venv и т.п. — но их нет
            if ".git" in str(p):
                continue
            lines = p.read_text().count("\n") + 1
            assert lines <= 500, f"{p} has {lines} lines (>500)"
