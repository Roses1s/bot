"""Планировщик бэкапов по cron.

Запускается как основной процесс контейнера backup:
    python -m app.scheduler

Каждую минуту проверяет cron-выражение и запускает бэкап если время совпало.
Логирует через structlog, шлёт уведомления в Telegram.
"""

from __future__ import annotations

import asyncio
import signal
import time
from datetime import datetime, timezone

import structlog
from croniter import croniter

from .backup_service import PostgresBackupService
from .config import get_config
from .notifier import TelegramNotifier
from .storage import LocalStorage, S3Storage

logger = structlog.get_logger(__name__)


def _setup_logging() -> None:
    """Настраивает structlog для JSON-логов в Docker."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),
        cache_logger_on_first_use=True,
    )


class BackupScheduler:
    """Планировщик, который выполняет бэкапы по расписанию.

    Attributes:
        service: Сервис бэкапов.
        notifier: Нотификатор.
        cron_expr: Cron-выражение.
    """

    def __init__(
        self,
        service: PostgresBackupService,
        notifier: TelegramNotifier,
        cron_expr: str,
    ) -> None:
        """Инициализирует планировщик.

        Args:
            service: Сервис бэкапов.
            notifier: Нотификатор.
            cron_expr: Cron-выражение.
        """
        self.service = service
        self.notifier = notifier
        self.cron_expr = cron_expr
        self._running = False
        logger.info("scheduler_initialized", cron=cron_expr)

    def _should_run_now(self, now: datetime) -> bool:
        """Проверяет нужно ли запускать бэкап в эту минуту.

        Использует croniter: берёт предыдущее срабатывание и смотрит
        попадает ли now в ту же минуту.

        Args:
            now: Текущее время (UTC).

        Returns:
            True если пора делать бэкап.
        """
        # Округляем до минуты
        now_minute = now.replace(second=0, microsecond=0)
        # croniter: ищем предыдущее срабатывание перед now_minute + 1 мин
        itr = croniter(self.cron_expr, now_minute + _one_minute())
        prev = itr.get_prev(datetime)
        # Если prev == now_minute — значит сейчас время бэкапа
        return prev.replace(tzinfo=timezone.utc) == now_minute.replace(tzinfo=timezone.utc)

    async def run_once(self) -> None:
        """Выполняет один бэкап с обработкой ошибок и уведомлениями."""
        logger.info("scheduled_backup_start")
        try:
            result = self.service.create_backup()
            logger.info(
                "scheduled_backup_success",
                file=result.metadata.filename,
                size=result.metadata.size_human,
            )
            await self.notifier.notify_success(result)
        except Exception as exc:  # noqa: BLE001
            logger.error("scheduled_backup_failed", error=str(exc), exc_info=True)
            await self.notifier.notify_failure(exc, "scheduled backup")

    async def run_forever(self) -> None:
        """Основной цикл — проверяет каждую минуту.

        Завершается по SIGTERM/SIGINT.
        """
        self._running = True
        logger.info("scheduler_started", cron=self.cron_expr)

        # Сразу проверяем, не пора ли делать бэкап (например, при рестарте)
        # — но только если последний бэкап старый, чтобы не дублировать
        # (логика: если healthcheck fails — делаем бэкап сразу)
        if not self.service.healthcheck():
            logger.info("no_recent_backup_on_startup — running initial backup")
            await self.run_once()

        while self._running:
            now = datetime.now(timezone.utc)
            if self._should_run_now(now):
                await self.run_once()
                # Спим 61 сек чтобы не сработать дважды в ту же минуту
                await asyncio.sleep(61)
            else:
                await asyncio.sleep(30)

    def stop(self) -> None:
        """Останавливает планировщик."""
        logger.info("scheduler_stopping")
        self._running = False


def _one_minute() -> "timedelta":  # type: ignore[name-defined]
    """Возвращает timedelta 1 минута (ленивый импорт)."""
    from datetime import timedelta

    return timedelta(minutes=1)


def _build_service() -> tuple[PostgresBackupService, TelegramNotifier, str]:
    """Собирает зависимости.

    Returns:
        Кортеж (service, notifier, cron_expr).
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
    service = PostgresBackupService(config=config, local_storage=local, s3_storage=s3)
    notifier = TelegramNotifier(config)
    return service, notifier, config.backup_cron


async def _amain() -> None:
    """Асинхронная точка входа."""
    _setup_logging()
    service, notifier, cron_expr = _build_service()
    scheduler = BackupScheduler(service, notifier, cron_expr)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, scheduler.stop)
        except NotImplementedError:
            # Windows — сигналы не поддерживаются
            pass

    await scheduler.run_forever()


def main() -> None:
    """Синхронная обёртка для Docker CMD."""
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        logger.info("scheduler_interrupted")


if __name__ == "__main__":
    main()
