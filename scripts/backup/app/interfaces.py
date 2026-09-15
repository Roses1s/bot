"""Интерфейсы (порты) Clean Architecture.

Определяют контракты для внешних зависимостей — хранилищ, нотификаций, БД.
Реализации находятся в storage.py, notifier.py и т.д.
Зависимости инвертированы (D из SOLID): домен зависит от абстракций.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Protocol, runtime_checkable

from .entities import BackupMetadata, BackupResult, RestoreRequest


@runtime_checkable
class StoragePort(Protocol):
    """Порт хранилища бэкапов.

    Любое хранилище (локальное, S3, GCS) должно реализовать этот протокол.
    """

    def save(self, source_path: Path, destination_name: str) -> Path:
        """Сохраняет файл в хранилище.

        Args:
            source_path: Локальный путь к файлу.
            destination_name: Имя файла в хранилище.

        Returns:
            Путь/ключ в хранилище.

        Raises:
            BackupStorageError: При ошибке сохранения.
        """
        ...

    def load(self, name: str, destination: Path) -> Path:
        """Загружает файл из хранилища.

        Args:
            name: Имя файла в хранилище.
            destination: Куда сохранить локально.

        Returns:
            Путь к загруженному файлу.
        """
        ...

    def list(self, pattern: str = "*.sql.gz") -> list[BackupMetadata]:
        """Список бэкапов в хранилище.

        Args:
            pattern: Glob-паттерн для фильтрации.

        Returns:
            Список метаданных, отсортированный по дате (новые первые).
        """
        ...

    def delete(self, name: str) -> None:
        """Удаляет бэкап.

        Args:
            name: Имя файла.
        """
        ...

    def exists(self, name: str) -> bool:
        """Проверяет существование бэкапа.

        Args:
            name: Имя файла.

        Returns:
            True если существует.
        """
        ...


class NotifierPort(ABC):
    """Абстрактный нотификатор (Telegram, Slack, Email)."""

    @abstractmethod
    async def notify_success(self, result: BackupResult) -> None:
        """Уведомляет об успешном бэкапе.

        Args:
            result: Результат бэкапа.
        """
        ...

    @abstractmethod
    async def notify_failure(self, error: Exception, context: str) -> None:
        """Уведомляет об ошибке.

        Args:
            error: Исключение.
            context: Контекст операции.
        """
        ...

    @abstractmethod
    async def notify_restore(self, request: RestoreRequest, success: bool) -> None:
        """Уведомляет о восстановлении.

        Args:
            request: Запрос восстановления.
            success: Успешно ли прошло.
        """
        ...


class DatabasePort(ABC):
    """Порт для операций с БД (дамп/восстановление)."""

    @abstractmethod
    def dump(self, output_path: Path) -> BackupMetadata:
        """Создаёт дамп БД.

        Args:
            output_path: Куда сохранить дамп.

        Returns:
            Метаданные созданного дампа.

        Raises:
            BackupCreationError: При ошибке pg_dump.
        """
        ...

    @abstractmethod
    def restore(self, request: RestoreRequest) -> None:
        """Восстанавливает БД из дампа.

        Args:
            request: Параметры восстановления.

        Raises:
            BackupRestoreError: При ошибке восстановления.
        """
        ...

    @abstractmethod
    def healthcheck(self) -> bool:
        """Проверяет доступность БД.

        Returns:
            True если БД отвечает.
        """
        ...
