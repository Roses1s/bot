"""Pytest fixtures для тестов инфраструктуры и бэкапов."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts.backup.app.config import AppConfig, clear_config_cache
from scripts.backup.app.storage import LocalStorage


@pytest.fixture
def tmp_backup_dir(tmp_path: Path) -> Path:
    """Временная директория для бэкапов.

    Args:
        tmp_path: Pytest tmp_path.

    Returns:
        Путь к директории.
    """
    d = tmp_path / "backups"
    d.mkdir()
    return d


@pytest.fixture
def app_config(tmp_backup_dir: Path) -> AppConfig:
    """Конфиг для тестов (без S3/Telegram).

    Args:
        tmp_backup_dir: Временная директория.

    Returns:
        AppConfig с тестовыми значениями.
    """
    clear_config_cache()
    return AppConfig(
        postgres_host="localhost",
        postgres_port=5432,
        postgres_db="odoo_test",
        postgres_password="test_password",  # type: ignore[arg-type]
        backup_dir=tmp_backup_dir,
        backup_cron="0 3 * * *",
        backup_retention_days=30,
    )


@pytest.fixture
def local_storage(tmp_backup_dir: Path) -> LocalStorage:
    """Локальное хранилище для тестов.

    Args:
        tmp_backup_dir: Директория.

    Returns:
        LocalStorage.
    """
    return LocalStorage(tmp_backup_dir, db_name="odoo_test")


@pytest.fixture
def mock_s3_storage() -> MagicMock:
    """Мок S3 хранилища.

    Returns:
        MagicMock с методами save/list/delete.
    """
    mock = MagicMock()
    mock.save.return_value = Path("s3://bucket/odoo-backups/test.sql.gz")
    mock.list.return_value = []
    mock.exists.return_value = True
    return mock
