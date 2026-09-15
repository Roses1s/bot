"""Тесты конфигурации."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scripts.backup.app.config import AppConfig, clear_config_cache


class TestAppConfig:
    """Тесты AppConfig."""

    def test_valid_config(self, tmp_path) -> None:
        """Валидный конфиг создаётся."""
        cfg = AppConfig(
            postgres_password="secret",  # type: ignore[arg-type]
            backup_dir=tmp_path,
        )
        assert cfg.postgres_host == "db"
        assert cfg.backup_retention_days == 30

    def test_invalid_cron_raises(self, tmp_path) -> None:
        """Невалидный cron кидает ValidationError."""
        with pytest.raises(ValidationError, match="Invalid cron"):
            AppConfig(
                postgres_password="secret",  # type: ignore[arg-type]
                backup_dir=tmp_path,
                backup_cron="not a cron",
            )

    def test_compression_level_validation(self, tmp_path) -> None:
        """Уровень сжатия должен быть 1-9."""
        with pytest.raises(ValidationError):
            AppConfig(
                postgres_password="secret",  # type: ignore[arg-type]
                backup_dir=tmp_path,
                backup_compression_level=99,  # type: ignore[arg-type]
            )

    def test_is_s3_enabled(self, tmp_path) -> None:
        """is_s3_enabled True только если всё заполнено."""
        cfg_no_s3 = AppConfig(postgres_password="secret", backup_dir=tmp_path)  # type: ignore[arg-type]
        assert cfg_no_s3.is_s3_enabled is False

        cfg_s3 = AppConfig(
            postgres_password="secret",  # type: ignore[arg-type]
            backup_dir=tmp_path,
            aws_s3_bucket="my-bucket",
            aws_access_key_id="AKIA...",
            aws_secret_access_key="secret123",  # type: ignore[arg-type]
        )
        assert cfg_s3.is_s3_enabled is True

    def test_is_telegram_enabled(self, tmp_path) -> None:
        """is_telegram_enabled проверяет токен и chat_id."""
        cfg = AppConfig(postgres_password="secret", backup_dir=tmp_path)  # type: ignore[arg-type]
        assert cfg.is_telegram_enabled is False

        cfg2 = AppConfig(
            postgres_password="secret",  # type: ignore[arg-type]
            backup_dir=tmp_path,
            telegram_bot_token="123:ABC",  # type: ignore[arg-type]
            telegram_chat_id="12345",
        )
        assert cfg2.is_telegram_enabled is True

    def test_postgres_dsn_masks_password(self, tmp_path) -> None:
        """DSN не содержит пароль в открытом виде."""
        cfg = AppConfig(postgres_password="mysecret", backup_dir=tmp_path)  # type: ignore[arg-type]
        assert "mysecret" not in cfg.postgres_dsn
        assert "***" in cfg.postgres_dsn
