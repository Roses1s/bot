"""Реализации хранилищ — Local и S3.

SOLID:
- Single Responsibility: каждый класс отвечает за один тип хранилища.
- Open/Closed: легко добавить GCS/Azure без изменения клиентов.
- Liskov: взаимозаменяемы через StoragePort.
"""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path

import structlog

from .entities import BackupMetadata, StorageType
from .exceptions import BackupStorageError

logger = structlog.get_logger(__name__)


def _sha256(path: Path) -> str:
    """Считает SHA256 файла потоково.

    Args:
        path: Путь к файлу.

    Returns:
        Hex-строка хеша.
    """
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_backup_date(filename: str) -> datetime | None:
    """Парсит дату из имени odoo_YYYYMMDD_HHMMSS.sql.gz.

    Args:
        filename: Имя файла.

    Returns:
        datetime в UTC или None если формат не совпал.
    """
    try:
        # odoo_20240115_030000.sql.gz -> 20240115_030000
        stem = filename.replace(".sql.gz", "").replace(".dump", "")
        parts = stem.split("_")
        if len(parts) >= 3:
            dt_str = f"{parts[-2]}_{parts[-1]}"
            return datetime.strptime(dt_str, "%Y%m%d_%H%M%S").replace(
                tzinfo=timezone.utc
            )
    except (ValueError, IndexError):
        return None
    return None


class LocalStorage:
    """Локальное файловое хранилище бэкапов.

    Attributes:
        base_dir: Корневая директория.
        db_name: Имя БД для метаданных.
    """

    def __init__(self, base_dir: Path, db_name: str = "odoo") -> None:
        """Инициализирует хранилище.

        Args:
            base_dir: Директория для хранения.
            db_name: Имя БД.

        Raises:
            BackupStorageError: Если директория не существует и не создаётся.
        """
        self.base_dir = base_dir
        self.db_name = db_name
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BackupStorageError(f"Cannot create backup dir {base_dir}: {exc}") from exc
        logger.info("local_storage_initialized", path=str(self.base_dir))

    def save(self, source_path: Path, destination_name: str) -> Path:
        """Сохраняет файл (копирует если source != dest).

        Args:
            source_path: Исходный файл.
            destination_name: Имя в хранилище.

        Returns:
            Путь к сохранённому файлу.

        Raises:
            BackupStorageError: При ошибке копирования.
        """
        dest = self.base_dir / destination_name
        try:
            if source_path.resolve() != dest.resolve():
                shutil.copy2(source_path, dest)
                logger.info("file_saved", src=str(source_path), dest=str(dest))
            else:
                logger.info("file_already_in_place", path=str(dest))
            return dest
        except OSError as exc:
            raise BackupStorageError(f"Failed to save {destination_name}: {exc}") from exc

    def load(self, name: str, destination: Path) -> Path:
        """Загружает файл (копирует).

        Args:
            name: Имя в хранилище.
            destination: Куда скопировать.

        Returns:
            Путь к копии.

        Raises:
            BackupStorageError: Если файла нет.
        """
        src = self.base_dir / name
        if not src.exists():
            raise BackupStorageError(f"Backup not found: {name}")
        try:
            shutil.copy2(src, destination)
            return destination
        except OSError as exc:
            raise BackupStorageError(f"Failed to load {name}: {exc}") from exc

    def list(self, pattern: str = "*.sql.gz") -> list[BackupMetadata]:
        """Список бэкапов.

        Args:
            pattern: Glob-паттерн.

        Returns:
            Отсортированный по дате (новые первые) список метаданных.
        """
        files = sorted(self.base_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        result: list[BackupMetadata] = []
        for f in files:
            if not f.is_file():
                continue
            stat = f.stat()
            # Парсим дату из имени, fallback — mtime
            created = _parse_backup_date(f.name) or datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            )
            try:
                checksum = _sha256(f)
            except OSError:
                checksum = None
            result.append(
                BackupMetadata(
                    filename=f.name,
                    path=f,
                    size_bytes=stat.st_size,
                    created_at=created,
                    db_name=self.db_name,
                    storage_type=StorageType.LOCAL,
                    checksum_sha256=checksum,
                )
            )
        logger.info("listed_backups", count=len(result), pattern=pattern)
        return result

    def delete(self, name: str) -> None:
        """Удаляет файл.

        Args:
            name: Имя файла.

        Raises:
            BackupStorageError: При ошибке удаления.
        """
        target = self.base_dir / name
        try:
            target.unlink(missing_ok=False)
            logger.info("backup_deleted", file=name)
        except FileNotFoundError as exc:
            raise BackupStorageError(f"Backup not found: {name}") from exc
        except OSError as exc:
            raise BackupStorageError(f"Failed to delete {name}: {exc}") from exc

    def exists(self, name: str) -> bool:
        """Проверяет существование.

        Args:
            name: Имя файла.

        Returns:
            True если файл есть.
        """
        return (self.base_dir / name).exists()

    def cleanup_old(self, retention_days: int) -> list[str]:
        """Удаляет бэкапы старше retention_days.

        Args:
            retention_days: Сколько дней хранить.

        Returns:
            Список удалённых имён.

        Raises:
            BackupStorageError: При ошибке удаления (частично может удалить).
        """
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        deleted: list[str] = []
        for meta in self.list():
            if meta.created_at < cutoff:
                try:
                    self.delete(meta.filename)
                    deleted.append(meta.filename)
                except BackupStorageError as exc:
                    logger.warning("cleanup_failed", file=meta.filename, error=str(exc))
        if deleted:
            logger.info("cleanup_done", deleted=len(deleted), retention_days=retention_days)
        return deleted


class S3Storage:
    """S3-совместимое хранилище (AWS S3, MinIO, Yandex Object Storage).

    Использует boto3. Лениво инициализирует клиент.
    """

    def __init__(
        self,
        bucket: str,
        region: str = "eu-central-1",
        prefix: str = "odoo-backups/",
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
    ) -> None:
        """Инициализирует S3 хранилище.

        Args:
            bucket: Имя bucket.
            region: Регион.
            prefix: Префикс ключей.
            aws_access_key_id: Ключ (опционально, берётся из env/IAM).
            aws_secret_access_key: Секрет.
        """
        self.bucket = bucket
        self.region = region
        self.prefix = prefix.rstrip("/") + "/" if prefix else ""
        self._access_key = aws_access_key_id
        self._secret_key = aws_secret_access_key
        self._client = None  # lazy
        logger.info("s3_storage_initialized", bucket=bucket, region=region, prefix=self.prefix)

    def _get_client(self):  # type: ignore[no-untyped-def]
        """Лениво создаёт boto3 клиент.

        Returns:
            S3 client.

        Raises:
            BackupStorageError: Если boto3 не установлен или нет кредов.
        """
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore[import-untyped]
        except ImportError as exc:
            raise BackupStorageError("boto3 not installed, cannot use S3") from exc

        try:
            session_kwargs: dict[str, str] = {}
            if self._access_key and self._secret_key:
                session_kwargs["aws_access_key_id"] = self._access_key
                session_kwargs["aws_secret_access_key"] = self._secret_key
            self._client = boto3.client("s3", region_name=self.region, **session_kwargs)  # type: ignore[arg-type]
            # Проверка доступа
            self._client.head_bucket(Bucket=self.bucket)
            return self._client
        except Exception as exc:  # noqa: BLE001
            raise BackupStorageError(f"S3 init failed for bucket {self.bucket}: {exc}") from exc

    def save(self, source_path: Path, destination_name: str) -> Path:
        """Загружает файл в S3.

        Args:
            source_path: Локальный файл.
            destination_name: Имя в S3.

        Returns:
            Виртуальный путь s3://bucket/key.

        Raises:
            BackupStorageError: При ошибке загрузки.
        """
        client = self._get_client()
        key = self.prefix + destination_name
        try:
            client.upload_file(str(source_path), self.bucket, key)
            logger.info("s3_uploaded", bucket=self.bucket, key=key, size=source_path.stat().st_size)
            return Path(f"s3://{self.bucket}/{key}")
        except Exception as exc:  # noqa: BLE001
            raise BackupStorageError(f"S3 upload failed {key}: {exc}") from exc

    def load(self, name: str, destination: Path) -> Path:
        """Скачивает из S3.

        Args:
            name: Имя файла.
            destination: Куда сохранить.

        Returns:
            Путь к файлу.

        Raises:
            BackupStorageError: При ошибке скачивания.
        """
        client = self._get_client()
        key = self.prefix + name
        try:
            client.download_file(self.bucket, key, str(destination))
            logger.info("s3_downloaded", bucket=self.bucket, key=key)
            return destination
        except Exception as exc:  # noqa: BLE001
            raise BackupStorageError(f"S3 download failed {key}: {exc}") from exc

    def list(self, pattern: str = "*.sql.gz") -> list[BackupMetadata]:
        """Список объектов в S3 (фильтруется по суффиксу).

        Args:
            pattern: Игнорируется частично — фильтрует по расширению.

        Returns:
            Список метаданных.
        """
        import fnmatch

        client = self._get_client()
        try:
            paginator = client.get_paginator("list_objects_v2")
            result: list[BackupMetadata] = []
            for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
                for obj in page.get("Contents", []):
                    key: str = obj["Key"]
                    filename = key.removeprefix(self.prefix)
                    if not fnmatch.fnmatch(filename, pattern):
                        continue
                    result.append(
                        BackupMetadata(
                            filename=filename,
                            path=Path(f"s3://{self.bucket}/{key}"),
                            size_bytes=obj["Size"],
                            created_at=obj["LastModified"],
                            db_name="odoo",
                            storage_type=StorageType.S3,
                        )
                    )
            result.sort(key=lambda m: m.created_at, reverse=True)
            logger.info("s3_listed", count=len(result))
            return result
        except Exception as exc:  # noqa: BLE001
            raise BackupStorageError(f"S3 list failed: {exc}") from exc

    def delete(self, name: str) -> None:
        """Удаляет объект в S3.

        Args:
            name: Имя файла.
        """
        client = self._get_client()
        key = self.prefix + name
        try:
            client.delete_object(Bucket=self.bucket, Key=key)
            logger.info("s3_deleted", key=key)
        except Exception as exc:  # noqa: BLE001
            raise BackupStorageError(f"S3 delete failed {key}: {exc}") from exc

    def exists(self, name: str) -> bool:
        """Проверяет существование ключа.

        Args:
            name: Имя файла.

        Returns:
            True если есть.
        """
        client = self._get_client()
        key = self.prefix + name
        try:
            client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:  # noqa: BLE001
            return False
