"""Иерархия исключений сервиса бэкапов.

Позволяет централизованно обрабатывать ошибки и маппить их в коды выхода / HTTP-статусы.
"""

from __future__ import annotations


class BackupError(Exception):
    """Базовое исключение всех ошибок бэкапа."""

    def __init__(self, message: str, *, details: str | None = None) -> None:
        """Инициализирует исключение.

        Args:
            message: Человеко-читаемое описание.
            details: Технические детали (stderr процесса и т.п.).
        """
        super().__init__(message)
        self.details = details


class BackupCreationError(BackupError):
    """Ошибка создания дампа (pg_dump упал)."""


class BackupRestoreError(BackupError):
    """Ошибка восстановления из дампа."""


class BackupStorageError(BackupError):
    """Ошибка сохранения/загрузки (локальный диск или S3)."""


class BackupValidationError(BackupError):
    """Ошибка валидации входных данных (путь, имя файла)."""


class BackupNotFoundError(BackupError):
    """Запрошенный бэкап не найден."""


class ConfigurationError(BackupError):
    """Ошибка конфигурации (невалидный .env, cron и т.п.)."""
