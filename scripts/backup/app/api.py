"""REST API для управления бэкапами — FastAPI + OpenAPI/Swagger.

Предоставляет:
- GET  /health      — проверка здоровья (для Docker HEALTHCHECK и мониторинга)
- GET  /backups     — список бэкапов
- POST /backups     — создать бэкап
- POST /restore     — восстановить из бэкапа

Автоматически генерирует OpenAPI схему на /docs (Swagger UI) и /redoc.

Clean Architecture: тонкий HTTP-адаптер, делегирует в PostgresBackupService.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import structlog
from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from .backup_service import PostgresBackupService
from .config import AppConfig, get_config
from .entities import RestoreRequest
from .exceptions import BackupCreationError, BackupRestoreError, BackupValidationError
from .storage import LocalStorage, S3Storage

logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------- schemas
class BackupResponse(BaseModel):
    """Ответ после создания бэкапа."""

    filename: str = Field(description="Имя файла бэкапа")
    size_bytes: int = Field(description="Размер в байтах")
    size_human: str = Field(description="Человеко-читаемый размер")
    checksum_sha256: str | None = Field(default=None, description="SHA256 хеш")
    duration_seconds: float | None = Field(default=None, description="Длительность pg_dump")
    s3_uploaded: bool = Field(description="Загружен ли в S3")
    s3_key: str | None = Field(default=None, description="Ключ в S3")
    warnings: list[str] = Field(default_factory=list, description="Предупреждения")


class BackupListItem(BaseModel):
    """Элемент списка бэкапов."""

    filename: str
    size_bytes: int
    size_human: str
    created_at: str
    storage_type: str
    checksum_sha256: str | None = None


class RestoreRequestBody(BaseModel):
    """Тело запроса восстановления."""

    filename: str = Field(description="Имя файла в backup_dir, напр. odoo_20240101_030000.sql.gz")
    target_db: str | None = Field(default=None, description="Целевая БД (по умолчанию из .env)")


class HealthResponse(BaseModel):
    """Ответ healthcheck."""

    status: str = Field(description="ok или fail")
    healthy: bool
    latest_backup: str | None = None
    backups_count: int


# --------------------------------------------------------------- dependencies
def get_app_config() -> AppConfig:
    """DI: возвращает конфиг.

    Returns:
        AppConfig.
    """
    return get_config()


def get_backup_service(config: Annotated[AppConfig, Depends(get_app_config)]) -> PostgresBackupService:
    """DI: собирает сервис бэкапов.

    Args:
        config: Конфиг.

    Returns:
        PostgresBackupService.
    """
    local = LocalStorage(config.backup_dir, db_name=config.postgres_db)
    s3 = None
    if config.is_s3_enabled:
        s3 = S3Storage(
            bucket=config.aws_s3_bucket or "",
            region=config.aws_region,
            aws_access_key_id=config.aws_access_key_id,
            aws_secret_access_key=config.aws_secret_access_key.get_secret_value() if config.aws_secret_access_key else None,
        )
    return PostgresBackupService(config=config, local_storage=local, s3_storage=s3)


# ---------------------------------------------------------------------- app
app = FastAPI(
    title="Odoo Backup Service API",
    version="1.0.0",
    description="Управление бэкапами PostgreSQL для Odoo 17 — создание, список, восстановление, healthcheck. "
    "Используется Nginx reverse-proxy и Docker. Swagger доступен на /docs.",
    contact={"name": "DevOps", "email": "admin@example.com"},
    license_info={"name": "MIT"},
)


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Healthcheck",
    tags=["ops"],
)
def healthcheck(service: Annotated[PostgresBackupService, Depends(get_backup_service)]) -> HealthResponse:
    """Проверяет что есть свежий бэкап.

    Args:
        service: Сервис бэкапов (DI).

    Returns:
        HealthResponse с флагом healthy.
    """
    healthy = service.healthcheck()
    backups = service.list_backups()
    return HealthResponse(
        status="ok" if healthy else "fail",
        healthy=healthy,
        latest_backup=backups[0].filename if backups else None,
        backups_count=len(backups),
    )


@app.get(
    "/backups",
    response_model=list[BackupListItem],
    summary="Список бэкапов",
    tags=["backups"],
)
def list_backups(service: Annotated[PostgresBackupService, Depends(get_backup_service)]) -> list[BackupListItem]:
    """Возвращает список всех локальных бэкапов.

    Args:
        service: Сервис.

    Returns:
        Список бэкапов, новые первые.
    """
    items = service.list_backups()
    return [
        BackupListItem(
            filename=b.filename,
            size_bytes=b.size_bytes,
            size_human=b.size_human,
            created_at=b.created_at.isoformat(),
            storage_type=b.storage_type.value,
            checksum_sha256=b.checksum_sha256,
        )
        for b in items
    ]


@app.post(
    "/backups",
    response_model=BackupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать бэкап",
    tags=["backups"],
)
def create_backup(service: Annotated[PostgresBackupService, Depends(get_backup_service)]) -> BackupResponse:
    """Запускает pg_dump и сохраняет бэкап.

    Args:
        service: Сервис.

    Returns:
        Метаданные созданного бэкапа.

    Raises:
        HTTPException: 500 при ошибке pg_dump.
    """
    try:
        result = service.create_backup()
        m = result.metadata
        logger.info("api_backup_created", file=m.filename)
        return BackupResponse(
            filename=m.filename,
            size_bytes=m.size_bytes,
            size_human=m.size_human,
            checksum_sha256=m.checksum_sha256,
            duration_seconds=m.duration_seconds,
            s3_uploaded=result.s3_uploaded,
            s3_key=result.s3_key,
            warnings=result.warnings,
        )
    except BackupCreationError as exc:
        logger.error("api_backup_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("api_backup_unexpected", error=str(exc), exc_info=True)
        raise HTTPException(status_code=500, detail="Unexpected error") from exc


@app.post(
    "/restore",
    summary="Восстановить из бэкапа",
    tags=["backups"],
)
def restore_backup(
    body: RestoreRequestBody,
    service: Annotated[PostgresBackupService, Depends(get_backup_service)],
) -> dict[str, str]:
    """Восстанавливает БД из указанного файла.

    Args:
        body: Тело запроса с именем файла.
        service: Сервис.

    Returns:
        Сообщение об успехе.

    Raises:
        HTTPException: 404 если файла нет, 500 при ошибке восстановления.
    """
    backup_path = service.config.backup_dir / body.filename
    if not backup_path.exists():
        raise HTTPException(status_code=404, detail=f"Backup not found: {body.filename}")

    # Защита: не позволяем path traversal
    try:
        backup_path.resolve().relative_to(service.config.backup_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid filename") from exc

    target_db = body.target_db or service.config.postgres_db
    req = RestoreRequest(backup_path=backup_path, target_db=target_db)

    try:
        service.restore_backup(req)
        logger.info("api_restore_done", file=body.filename, target_db=target_db)
        return {"status": "ok", "message": f"Restored {body.filename} to {target_db}"}
    except (BackupValidationError, BackupRestoreError) as exc:
        logger.error("api_restore_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc
