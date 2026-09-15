"""CLI для ручного управления бэкапами.

Использование:
    python -m app.cli backup --verbose
    python -m app.cli restore --file /backups/odoo_20240115_030000.sql.gz
    python -m app.cli list
    python -m app.cli healthcheck

SOLID: CLI — тонкий адаптер, вся логика в PostgresBackupService.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import structlog

from .backup_service import PostgresBackupService
from .config import get_config
from .entities import RestoreRequest
from .notifier import TelegramNotifier
from .storage import LocalStorage, S3Storage

logger = structlog.get_logger(__name__)


def _build_service() -> PostgresBackupService:
    """Собирает сервис с зависимостями из конфига.

    Returns:
        Настроенный PostgresBackupService.
    """
    config = get_config()
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


def cmd_backup(args: argparse.Namespace) -> int:
    """Выполняет бэкап.

    Args:
        args: Аргументы CLI.

    Returns:
        Код выхода (0 — успех).
    """
    service = _build_service()
    notifier = TelegramNotifier(service.config)
    try:
        result = service.create_backup()
        print(f"✓ Backup created: {result.metadata.filename} ({result.metadata.size_human})")
        if result.s3_uploaded:
            print(f"  S3: {result.s3_key}")
        for w in result.warnings:
            print(f"  ⚠ {w}", file=sys.stderr)

        # Асинхронное уведомление (best-effort)
        try:
            asyncio.run(notifier.notify_success(result))
        except Exception:  # noqa: BLE001
            pass

        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"✗ Backup failed: {exc}", file=sys.stderr)
        if hasattr(exc, "details") and exc.details:  # type: ignore[attr-defined]
            print(str(exc.details)[:2000], file=sys.stderr)  # type: ignore[attr-defined]
        try:
            asyncio.run(notifier.notify_failure(exc, "manual backup"))
        except Exception:  # noqa: BLE001
            pass
        return 1


def cmd_restore(args: argparse.Namespace) -> int:
    """Восстанавливает из бэкапа.

    Args:
        args: Аргументы CLI.

    Returns:
        Код выхода.
    """
    service = _build_service()
    notifier = TelegramNotifier(service.config)
    backup_path = Path(args.file)
    # Если передан только имя — ищем в backup_dir
    if not backup_path.is_absolute() and not backup_path.exists():
        candidate = service.config.backup_dir / backup_path.name
        if candidate.exists():
            backup_path = candidate

    req = RestoreRequest(
        backup_path=backup_path,
        target_db=args.target_db or service.config.postgres_db,
    )
    try:
        service.restore_backup(req)
        print(f"✓ Restore done: {backup_path} -> {req.target_db}")
        try:
            asyncio.run(notifier.notify_restore(req, True))
        except Exception:  # noqa: BLE001
            pass
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"✗ Restore failed: {exc}", file=sys.stderr)
        try:
            asyncio.run(notifier.notify_restore(req, False))
            asyncio.run(notifier.notify_failure(exc, "restore"))
        except Exception:  # noqa: BLE001
            pass
        return 1


def cmd_list(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Список бэкапов.

    Args:
        args: Аргументы CLI.

    Returns:
        Код выхода.
    """
    service = _build_service()
    backups = service.list_backups()
    if not backups:
        print("No backups found.")
        return 0
    print(f"{'Filename':<45} {'Size':<12} {'Created (UTC)':<22} {'SHA256':<12}")
    print("-" * 100)
    for b in backups:
        sha = (b.checksum_sha256 or "")[:12]
        print(f"{b.filename:<45} {b.size_human:<12} {b.created_at.isoformat():<22} {sha:<12}")
    return 0


def cmd_healthcheck(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Проверка здоровья (для Docker HEALTHCHECK).

    Args:
        args: Аргументы CLI.

    Returns:
        0 если здоров, 1 если нет.
    """
    service = _build_service()
    healthy = service.healthcheck()
    if healthy:
        print("healthcheck: OK")
        return 0
    print("healthcheck: FAIL — no recent backup", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    """Строит парсер аргументов.

    Returns:
        Настроенный ArgumentParser.
    """
    parser = argparse.ArgumentParser(prog="odoo-backup", description="Odoo PostgreSQL backup CLI")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    sub = parser.add_subparsers(dest="command", required=True)

    p_backup = sub.add_parser("backup", help="Create a new backup")
    p_backup.set_defaults(func=cmd_backup)

    p_restore = sub.add_parser("restore", help="Restore from backup")
    p_restore.add_argument("--file", "-f", required=True, help="Path to .sql or .sql.gz backup")
    p_restore.add_argument("--target-db", help="Target DB name (default: from .env)")
    p_restore.set_defaults(func=cmd_restore)

    p_list = sub.add_parser("list", help="List backups")
    p_list.set_defaults(func=cmd_list)

    p_health = sub.add_parser("healthcheck", help="Check if recent backup exists")
    p_health.set_defaults(func=cmd_healthcheck)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI.

    Args:
        argv: Аргументы (по умолчанию sys.argv[1:]).

    Returns:
        Код выхода.
    """
    # Настройка structlog для CLI
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer() if "--verbose" not in (argv or sys.argv) else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
    )

    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
