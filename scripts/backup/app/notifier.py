"""Уведомления — Telegram Bot API (расширяется до Slack/Email).

SOLID: Dependency Inversion — сервис бэкапа зависит от абстракции NotifierPort,
а не от конкретного Telegram.
"""

from __future__ import annotations

import structlog

from .config import AppConfig
from .entities import BackupResult, RestoreRequest

logger = structlog.get_logger(__name__)


class TelegramNotifier:
    """Отправляет уведомления в Telegram.

    Attributes:
        config: Конфигурация с токеном и chat_id.
    """

    def __init__(self, config: AppConfig) -> None:
        """Инициализирует нотификатор.

        Args:
            config: Конфиг приложения.
        """
        self.config = config
        self._enabled = config.is_telegram_enabled
        if self._enabled:
            logger.info("telegram_notifier_enabled", chat_id=config.telegram_chat_id)
        else:
            logger.info("telegram_notifier_disabled")

    async def notify_success(self, result: BackupResult) -> None:
        """Уведомляет об успешном бэкапе.

        Args:
            result: Результат бэкапа.
        """
        if not self._enabled:
            return
        text = (
            f"✅ Odoo backup OK\n"
            f"📁 {result.metadata.filename}\n"
            f"📦 {result.metadata.size_human} in {result.metadata.duration_seconds:.1f}s\n"
            f"🔗 S3: {'yes' if result.s3_uploaded else 'no (local only)'}\n"
        )
        if result.warnings:
            text += f"⚠️ Warnings: {'; '.join(result.warnings)}\n"
        await self._send(text)

    async def notify_failure(self, error: Exception, context: str) -> None:
        """Уведомляет об ошибке.

        Args:
            error: Исключение.
            context: Контекст.
        """
        if not self._enabled:
            return
        text = f"❌ Odoo backup FAILED [{context}]\n{type(error).__name__}: {error}\n"
        # details если есть
        details = getattr(error, "details", None)
        if details:
            text += f"\n{str(details)[:1000]}"
        await self._send(text)

    async def notify_restore(self, request: RestoreRequest, success: bool) -> None:  # noqa: ARG002
        """Уведомляет о восстановлении.

        Args:
            request: Запрос.
            success: Успех ли.
        """
        if not self._enabled:
            return
        icon = "✅" if success else "❌"
        text = f"{icon} Odoo restore {'OK' if success else 'FAILED'}\n📁 {request.backup_path.name}\n🎯 {request.target_db}"
        await self._send(text)

    async def _send(self, text: str) -> None:
        """Отправляет сообщение через Telegram Bot API.

        Args:
            text: Текст сообщения.
        """
        if not self.config.telegram_bot_token or not self.config.telegram_chat_id:
            return
        token = self.config.telegram_bot_token.get_secret_value()
        chat_id = self.config.telegram_chat_id
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            import httpx  # type: ignore[import-untyped]

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
                if resp.status_code != 200:
                    logger.warning("telegram_send_failed", status=resp.status_code, body=resp.text[:500])
                else:
                    logger.info("telegram_sent", chat_id=chat_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("telegram_error", error=str(exc))
