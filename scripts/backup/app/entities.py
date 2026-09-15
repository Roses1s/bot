"""Доменные сущности (Clean Architecture — Entities).

Не зависят от фреймворков, БД или внешних сервисов.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class BackupStatus(str, Enum):
    """Статус бэкапа."""

    SUCCESS = "success"
    FAILED = "failed"
    IN_PROGRESS = "in_progress"


class StorageType(str, Enum):
    """Тип хранилища."""

    LOCAL = "local"
    S3 = "s3"


@dataclass(frozen=True, slots=True)
class BackupMetadata:
    """Метаданные одного бэкапа.

    Attributes:
        filename: Имя файла (напр. odoo_20240115_030000.sql.gz).
        path: Полный путь к файлу.
        size_bytes: Размер в байтах.
        created_at: Время создания (UTC).
        db_name: Имя базы данных.
        storage_type: Где хранится.
        checksum_sha256: SHA256 хеш файла (для проверки целостности).
        duration_seconds: Сколько длился pg_dump.
        status: Статус операции.
    """

    filename: str
    path: Path
    size_bytes: int
    created_at: datetime
    db_name: str
    storage_type: StorageType = StorageType.LOCAL
    checksum_sha256: str | None = None
    duration_seconds: float | None = None
    status: BackupStatus = BackupStatus.SUCCESS

    def __post_init__(self) -> None:
        """Валидирует поля после создания."""
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be >= 0")
        if not self.filename:
            raise ValueError("filename must not be empty")

    @property
    def size_human(self) -> str:
        """Человеко-читаемый размер.

        Returns:
            Строка вида '1.42 MB'.
        """
        size = float(self.size_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if abs(size) < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} TB"

    @property
    def is_expired(self) -> bool:
        """Проверяет просрочен ли бэкап относительно retention.

        Note:
            Логика retention — в сервисе, здесь заглушка.
            Метод оставлен для расширения доменной логики.

        Returns:
            False по умолчанию (проверяется в сервисе).
        """
        return False


@dataclass(slots=True)
class BackupResult:
    """Результат операции бэкапа.

    Attributes:
        metadata: Метаданные созданного бэкапа.
        s3_uploaded: Был ли загружен в S3.
        s3_key: Ключ в S3 если загружен.
        warnings: Предупреждения (напр. S3 недоступен, но локально OK).
    """

    metadata: BackupMetadata
    s3_uploaded: bool = False
    s3_key: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def is_success(self) -> bool:
        """Успешен ли бэкап.

        Returns:
            True если статус SUCCESS.
        """
        return self.metadata.status == BackupStatus.SUCCESS


@dataclass(frozen=True, slots=True)
class RestoreRequest:
    """Запрос на восстановление.

    Attributes:
        backup_path: Путь к файлу бэкапа.
        target_db: Целевая БД (по умолчанию та же).
        drop_existing: Удалять ли существующие соединения перед восстановлением.
    """

    backup_path: Path
    target_db: str
    drop_existing: bool = True
    clean: bool = True
