"""Odoo Backup Service package.

Предоставляет сервисы для автоматического бэкапа PostgreSQL,
ротации, загрузки в S3 и уведомлений в Telegram.
Спроектирован по принципам Clean Architecture и SOLID.
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = ["__version__"]
