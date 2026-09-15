"""Тесты доменных сущностей."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.backup.app.entities import BackupMetadata, BackupResult, BackupStatus, StorageType


class TestBackupMetadata:
    """Тесты BackupMetadata."""

    def test_create_valid(self) -> None:
        """Валидная сущность создаётся."""
        m = BackupMetadata(
            filename="odoo_20240101_030000.sql.gz",
            path=Path("/backups/odoo_20240101_030000.sql.gz"),
            size_bytes=1024,
            created_at=datetime.now(timezone.utc),
            db_name="odoo",
        )
        assert m.size_human == "1.00 KB"
        assert m.status == BackupStatus.SUCCESS

    def test_negative_size_raises(self) -> None:
        """Отрицательный размер — ошибка."""
        with pytest.raises(ValueError):
            BackupMetadata(
                filename="x.sql.gz",
                path=Path("/tmp/x.sql.gz"),
                size_bytes=-1,
                created_at=datetime.now(timezone.utc),
                db_name="odoo",
            )

    def test_empty_filename_raises(self) -> None:
        """Пустое имя — ошибка."""
        with pytest.raises(ValueError):
            BackupMetadata(
                filename="",
                path=Path("/tmp/x.sql.gz"),
                size_bytes=0,
                created_at=datetime.now(timezone.utc),
                db_name="odoo",
            )

    def test_size_human_units(self) -> None:
        """Проверка форматирования размеров."""
        cases = [
            (500, "500.00 B"),
            (2048, "2.00 KB"),
            (1024 * 1024, "1.00 MB"),
            (1024 * 1024 * 1024, "1.00 GB"),
        ]
        for size, expected in cases:
            m = BackupMetadata(
                filename="x.sql.gz",
                path=Path("/tmp/x.sql.gz"),
                size_bytes=size,
                created_at=datetime.now(timezone.utc),
                db_name="odoo",
            )
            assert m.size_human == expected


class TestBackupResult:
    """Тесты BackupResult."""

    def test_is_success(self) -> None:
        """is_success отражает статус метаданных."""
        meta_ok = BackupMetadata(
            filename="x.sql.gz",
            path=Path("/tmp/x.sql.gz"),
            size_bytes=100,
            created_at=datetime.now(timezone.utc),
            db_name="odoo",
            status=BackupStatus.SUCCESS,
        )
        meta_fail = BackupMetadata(
            filename="x.sql.gz",
            path=Path("/tmp/x.sql.gz"),
            size_bytes=100,
            created_at=datetime.now(timezone.utc),
            db_name="odoo",
            status=BackupStatus.FAILED,
        )
        assert BackupResult(metadata=meta_ok).is_success is True
        assert BackupResult(metadata=meta_fail).is_success is False
