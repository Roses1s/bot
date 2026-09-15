"""Сервис бэкапов — основная бизнес-логика.

Clean Architecture: Use Case / Service layer.
Зависит только от портов (интерфейсов), не от конкретных реализаций.
"""

from __future__ import annotations

import gzip
import hashlib
import shutil
import subprocess  # noqa: S404 — используется без shell, безопасно
import time
from datetime import datetime, timezone
from pathlib import Path

import structlog

from .config import AppConfig
from .entities import BackupMetadata, BackupResult, BackupStatus, RestoreRequest, StorageType
from .exceptions import BackupCreationError, BackupRestoreError, BackupValidationError
from .storage import LocalStorage

logger = structlog.get_logger(__name__)


class PostgresBackupService:
    """Сервис для создания и восстановления PostgreSQL дампов.

    Attributes:
        config: Конфигурация.
        local_storage: Локальное хранилище.
        s3_storage: Опциональное S3 хранилище.
    """

    def __init__(
        self,
        config: AppConfig,
        local_storage: LocalStorage,
        s3_storage: object | None = None,
    ) -> None:
        """Инициализирует сервис.

        Args:
            config: Конфигурация приложения.
            local_storage: Локальное хранилище.
            s3_storage: S3 хранилище (если настроено).
        """
        self.config = config
        self.local_storage = local_storage
        self.s3_storage = s3_storage
        logger.info(
            "backup_service_initialized",
            db=config.postgres_db,
            backup_dir=str(config.backup_dir),
            s3_enabled=s3_storage is not None,
        )

    # ------------------------------------------------------------------ helpers
    def _backup_filename(self, compressed: bool = True) -> str:
        """Генерирует имя файла бэкапа.

        Args:
            compressed: Сжимать ли gzip.

        Returns:
            Имя вида odoo_20240115_030000.sql.gz
        """
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        ext = ".sql.gz" if compressed else ".sql"
        return f"{self.config.postgres_db}_{ts}{ext}"

    def _pg_env(self) -> dict[str, str]:
        """Переменные окружения для pg_dump/psql.

        Returns:
            Словарь с PGPASSWORD.
        """
        import os

        env = os.environ.copy()
        env["PGPASSWORD"] = self.config.postgres_password.get_secret_value()
        return env

    def _run_pg_dump(self, output_path: Path) -> float:
        """Запускает pg_dump с оптимизациями для Odoo.

        Args:
            output_path: Путь для записи (уже сжатый .gz или обычный .sql).

        Returns:
            Длительность в секундах.

        Raises:
            BackupCreationError: Если pg_dump завершился с ошибкой.
        """
        is_gz = output_path.suffix == ".gz"
        # pg_dump опции: --no-owner --clean --if-exists — удобно для восстановления
        cmd = [
            "pg_dump",
            "-h", self.config.postgres_host,
            "-p", str(self.config.postgres_port),
            "-U", self.config.postgres_user,
            "-d", self.config.postgres_db,
            "--no-owner",
            "--no-acl",
            "--clean",
            "--if-exists",
            "--format=plain",
            "--verbose",
        ]

        logger.info("pg_dump_start", db=self.config.postgres_db, output=str(output_path))
        start = time.monotonic()
        try:
            if is_gz:
                # pg_dump | gzip
                with gzip.open(output_path, "wb", compresslevel=self.config.backup_compression_level) as gz:
                    result = subprocess.run(  # noqa: S603
                        cmd,
                        env=self._pg_env(),
                        stdout=gz,  # type: ignore[arg-type]
                        stderr=subprocess.PIPE,
                        check=False,
                    )
            else:
                with output_path.open("wb") as f:
                    result = subprocess.run(  # noqa: S603
                        cmd,
                        env=self._pg_env(),
                        stdout=f,
                        stderr=subprocess.PIPE,
                        check=False,
                    )

            duration = time.monotonic() - start

            if result.returncode != 0:
                stderr = result.stderr.decode(errors="replace") if result.stderr else ""
                # Удаляем битый файл
                try:
                    output_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise BackupCreationError(
                    f"pg_dump failed with code {result.returncode}",
                    details=stderr[-2000:],
                )

            logger.info("pg_dump_done", duration=f"{duration:.1f}s", size=output_path.stat().st_size)
            return duration

        except FileNotFoundError as exc:
            raise BackupCreationError("pg_dump not found — is postgresql-client installed?") from exc
        except OSError as exc:
            raise BackupCreationError(f"pg_dump OS error: {exc}") from exc

    # ----------------------------------------------------------------- public API
    def create_backup(self, filename: str | None = None) -> BackupResult:
        """Создаёт бэкап: pg_dump -> gzip -> локально -> S3 (если настроен).

        Args:
            filename: Опциональное имя файла (для тестов).

        Returns:
            Результат с метаданными.

        Raises:
            BackupCreationError: При ошибке дампа.
            BackupValidationError: При невалидном имени.
        """
        if filename is not None and ("/" in filename or "\\" in filename):
            raise BackupValidationError("filename must not contain path separators")

        name = filename or self._backup_filename(compressed=True)
        output_path = self.config.backup_dir / name
        self.config.backup_dir.mkdir(parents=True, exist_ok=True)

        warnings: list[str] = []
        duration = self._run_pg_dump(output_path)

        # Метаданные
        stat = output_path.stat()
        # SHA256 для проверки целостности
        sha256 = hashlib.sha256()
        with output_path.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)

        metadata = BackupMetadata(
            filename=name,
            path=output_path,
            size_bytes=stat.st_size,
            created_at=datetime.now(timezone.utc),
            db_name=self.config.postgres_db,
            storage_type=StorageType.LOCAL,
            checksum_sha256=sha256.hexdigest(),
            duration_seconds=duration,
            status=BackupStatus.SUCCESS,
        )

        # S3 загрузка (best-effort)
        s3_key: str | None = None
        s3_uploaded = False
        if self.s3_storage is not None:
            try:
                # Используем duck-typing — любой объект с .save()
                result_path = self.s3_storage.save(output_path, name)  # type: ignore[attr-defined]
                s3_uploaded = True
                s3_key = str(result_path)
                logger.info("s3_backup_uploaded", key=s3_key)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"S3 upload failed: {exc}")
                logger.warning("s3_upload_failed", error=str(exc))

        # Ротация локальных файлов
        try:
            deleted = self.local_storage.cleanup_old(self.config.backup_retention_days)
            if deleted:
                logger.info("rotation_done", deleted=deleted)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Rotation failed: {exc}")
            logger.warning("rotation_failed", error=str(exc))

        result = BackupResult(
            metadata=metadata,
            s3_uploaded=s3_uploaded,
            s3_key=s3_key,
            warnings=warnings,
        )
        logger.info(
            "backup_created",
            file=name,
            size=metadata.size_human,
            duration=f"{duration:.1f}s",
            s3_uploaded=s3_uploaded,
        )
        return result

    def restore_backup(self, request: RestoreRequest) -> None:
        """Восстанавливает БД из дампа.

        Args:
            request: Параметры восстановления.

        Raises:
            BackupRestoreError: При ошибке восстановления.
            BackupValidationError: Если файл не найден.
        """
        if not request.backup_path.exists():
            raise BackupValidationError(f"Backup file not found: {request.backup_path}")

        logger.info("restore_start", file=str(request.backup_path), target_db=request.target_db)

        # Определяем сжатый ли файл
        is_gz = request.backup_path.suffix == ".gz" or str(request.backup_path).endswith(".sql.gz")

        # Команда восстановления: gunzip -c file | psql  или  psql < file
        psql_cmd = [
            "psql",
            "-h", self.config.postgres_host,
            "-p", str(self.config.postgres_port),
            "-U", self.config.postgres_user,
            "-d", request.target_db,
            "-v", "ON_ERROR_STOP=1",
        ]

        try:
            if is_gz:
                # gunzip + psql pipeline
                gz_proc = subprocess.Popen(  # noqa: S603
                    ["gzip", "-dc", str(request.backup_path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                psql_proc = subprocess.run(  # noqa: S603
                    psql_cmd,
                    env=self._pg_env(),
                    stdin=gz_proc.stdout,
                    capture_output=True,
                    check=False,
                )
                gz_proc.stdout.close()  # type: ignore[union-attr]
                gz_proc.wait(timeout=30)
                if gz_proc.returncode != 0:
                    raise BackupRestoreError(f"gzip failed: {gz_proc.stderr.read().decode(errors='replace')}")
                if psql_proc.returncode != 0:
                    raise BackupRestoreError(
                        f"psql restore failed ({psql_proc.returncode})",
                        details=psql_proc.stderr.decode(errors="replace")[-3000:],
                    )
            else:
                with request.backup_path.open("rb") as f:
                    result = subprocess.run(  # noqa: S603
                        psql_cmd,
                        env=self._pg_env(),
                        stdin=f,
                        capture_output=True,
                        check=False,
                    )
                if result.returncode != 0:
                    raise BackupRestoreError(
                        f"psql restore failed ({result.returncode})",
                        details=result.stderr.decode(errors="replace")[-3000:],
                   )

            logger.info("restore_done", file=str(request.backup_path))

        except FileNotFoundError as exc:
            raise BackupRestoreError("psql or gzip not found") from exc
        except OSError as exc:
            raise BackupRestoreError(f"Restore OS error: {exc}") from exc

    def healthcheck(self) -> bool:
        """Проверяет что последний бэкап свежий.

        Returns:
            True если есть бэкап не старше 2 * retention или 48ч (минимум).
        """
        from datetime import timedelta

        backups = self.local_storage.list()
        if not backups:
            logger.warning("healthcheck_no_backups")
            return False
        latest = backups[0]
        age = datetime.now(timezone.utc) - latest.created_at
        max_age = timedelta(hours=48)
        healthy = age < max_age
        logger.info("healthcheck", latest=str(latest.filename), age=str(age), healthy=healthy)
        return healthy

    def list_backups(self) -> list[BackupMetadata]:
        """Список всех бэкапов.

        Returns:
            Список метаданных.
        """
        return self.local_storage.list()
