"""Конфигурация сервиса бэкапов.

Загружается из переменных окружения (.env) через pydantic-settings.
Валидирует обязательные поля и предоставляет типизированный доступ.

SOLID: Single Responsibility — только хранение и валидация настроек.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    """Типизированная конфигурация приложения.

    Attributes:
        postgres_host: Хост PostgreSQL.
        postgres_port: Порт PostgreSQL.
        postgres_db: Имя базы данных.
        postgres_user: Пользователь БД.
        postgres_password: Пароль БД (секрет).
        backup_dir: Директория для локальных бэкапов.
        backup_cron: Cron-выражение расписания.
        backup_retention_days: Сколько дней хранить бэкапы.
        backup_compression_level: Уровень сжатия gzip (1-9).
        aws_s3_bucket: S3 bucket (опционально).
        aws_access_key_id: AWS ключ (опционально).
        aws_secret_access_key: AWS секрет (опционально).
        aws_region: Регион AWS.
        telegram_bot_token: Токен Telegram бота (опционально).
        telegram_chat_id: Chat ID для уведомлений (опционально).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    postgres_host: str = Field(default="db", description="PostgreSQL host")
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = Field(default="odoo", min_length=1)
    postgres_user: str = Field(default="odoo", min_length=1)
    postgres_password: SecretStr = Field(..., description="PostgreSQL password")

    backup_dir: Path = Field(default=Path("/backups"), description="Backup directory")
    backup_cron: str = Field(default="0 3 * * *", description="Cron expression")
    backup_retention_days: int = Field(default=30, ge=1, le=3650)
    backup_compression_level: int = Field(default=6, ge=1, le=9)

    aws_s3_bucket: str | None = Field(default=None)
    aws_access_key_id: str | None = Field(default=None)
    aws_secret_access_key: SecretStr | None = Field(default=None)
    aws_region: str = Field(default="eu-central-1")

    telegram_bot_token: SecretStr | None = Field(default=None)
    telegram_chat_id: str | None = Field(default=None)

    log_level: str = Field(default="INFO")

    @field_validator("backup_dir")
    @classmethod
    def validate_backup_dir(cls, v: Path) -> Path:
        """Валидирует и создаёт директорию бэкапов если нужно.

        Args:
            v: Путь к директории.

        Returns:
            Абсолютный путь к директории.
        """
        # Не создаём в тестах автоматически — только валидируем
        return v.resolve() if v.is_absolute() else (Path.cwd() / v).resolve()

    @field_validator("backup_cron")
    @classmethod
    def validate_cron(cls, v: str) -> str:
        """Проверяет что cron-выражение парсится.

        Args:
            v: Cron строка.

        Returns:
            Исходная строка если валидна.

        Raises:
            ValueError: Если выражение невалидно.
        """
        from croniter import croniter

        if not croniter.is_valid(v):
            raise ValueError(f"Invalid cron expression: {v!r}")
        return v

    @property
    def postgres_dsn(self) -> str:
        """DSN для подключения к PostgreSQL.

        Returns:
            Строка подключения в формате postgresql://user:***@host:port/db
        """
        return (
            f"postgresql://{self.postgres_user}:***"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def is_s3_enabled(self) -> bool:
        """Проверяет настроен ли S3.

        Returns:
            True если указаны bucket и ключи.
        """
        return bool(
            self.aws_s3_bucket
            and self.aws_access_key_id
            and self.aws_secret_access_key
        )

    @property
    def is_telegram_enabled(self) -> bool:
        """Проверяет настроен ли Telegram.

        Returns:
            True если указаны токен и chat_id.
        """
        return bool(self.telegram_bot_token and self.telegram_chat_id)


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Возвращает синглтон конфигурации (кэшируется).

    Returns:
        Экземпляр AppConfig, загруженный из окружения.
    """
    return AppConfig()  # type: ignore[call-arg]


def clear_config_cache() -> None:
    """Сбрасывает кэш конфигурации (для тестов).

    Используется в тестах чтобы перечитать .env.
    """
    get_config.cache_clear()
