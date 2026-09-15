"""Тесты LocalStorage и S3Storage.

Покрытие: save/load/list/delete/exists/cleanup_old, обработка ошибок.
"""

from __future__ import annotations

import gzip
import time
from pathlib import Path

import pytest

from scripts.backup.app.exceptions import BackupStorageError
from scripts.backup.app.storage import LocalStorage


class TestLocalStorage:
    """Тесты локального хранилища."""

    def test_save_and_exists(self, local_storage: LocalStorage, tmp_backup_dir: Path) -> None:
        """save копирует файл и exists возвращает True."""
        src = tmp_backup_dir / "src.sql.gz"
        src.write_bytes(b"test data")

        dest = local_storage.save(src, "backup.sql.gz")

        assert dest.exists()
        assert dest.read_bytes() == b"test data"
        assert local_storage.exists("backup.sql.gz") is True
        assert local_storage.exists("missing.sql.gz") is False

    def test_save_same_file_no_copy(self, local_storage: LocalStorage, tmp_backup_dir: Path) -> None:
        """save не падает если source и dest — один файл."""
        f = tmp_backup_dir / "same.sql.gz"
        f.write_bytes(b"hello")
        # Сохраняем тот же файл под тем же именем (resolve совпадёт)
        result = local_storage.save(f, "same.sql.gz")
        assert result.exists()

    def test_load(self, local_storage: LocalStorage, tmp_backup_dir: Path) -> None:
        """load копирует из хранилища в destination."""
        src = tmp_backup_dir / "orig.sql.gz"
        src.write_bytes(b"orig")
        local_storage.save(src, "stored.sql.gz")

        dest = tmp_backup_dir / "loaded.sql.gz"
        local_storage.load("stored.sql.gz", dest)

        assert dest.read_bytes() == b"orig"

    def test_load_not_found_raises(self, local_storage: LocalStorage, tmp_backup_dir: Path) -> None:
        """load кидает BackupStorageError если файла нет."""
        with pytest.raises(BackupStorageError, match="not found"):
            local_storage.load("ghost.sql.gz", tmp_backup_dir / "out.sql.gz")

    def test_list_sorted(self, local_storage: LocalStorage, tmp_backup_dir: Path) -> None:
        """list возвращает файлы отсортированные по дате (новые первые)."""
        for i in range(3):
            p = tmp_backup_dir / f"odoo_2024010{i+1}_030000.sql.gz"
            p.write_bytes(b"x")
            # Делаем разное mtime
            time.sleep(0.01)

        items = local_storage.list()

        assert len(items) == 3
        # Новые первые — значит последний созданный первый в списке
        assert items[0].filename == "odoo_20240103_030000.sql.gz"

    def test_list_empty(self, local_storage: LocalStorage) -> None:
        """list на пустой директории возвращает []."""
        assert local_storage.list() == []

    def test_delete(self, local_storage: LocalStorage, tmp_backup_dir: Path) -> None:
        """delete удаляет файл."""
        p = tmp_backup_dir / "todel.sql.gz"
        p.write_bytes(b"bye")
        local_storage.save(p, "todel.sql.gz")

        local_storage.delete("todel.sql.gz")

        assert not local_storage.exists("todel.sql.gz")

    def test_delete_not_found_raises(self, local_storage: LocalStorage) -> None:
        """delete кидает ошибку если файла нет."""
        with pytest.raises(BackupStorageError, match="not found"):
            local_storage.delete("404.sql.gz")

    def test_cleanup_old(self, local_storage: LocalStorage, tmp_backup_dir: Path) -> None:
        """cleanup_old удаляет файлы старше retention_days."""
        old = tmp_backup_dir / "odoo_20200101_030000.sql.gz"
        old.write_bytes(b"old")
        recent = tmp_backup_dir / "odoo_20990101_030000.sql.gz"
        recent.write_bytes(b"recent")

        # Ставим retention 30 дней — старый должен удалиться, будущий — нет
        deleted = local_storage.cleanup_old(retention_days=30)

        assert "odoo_20200101_030000.sql.gz" in deleted
        assert "odoo_20990101_030000.sql.gz" not in deleted
        assert not (tmp_backup_dir / "odoo_20200101_030000.sql.gz").exists()

    def test_checksum_calculated(self, local_storage: LocalStorage, tmp_backup_dir: Path) -> None:
        """list считает checksum."""
        p = tmp_backup_dir / "odoo_20240101_030000.sql.gz"
        p.write_bytes(b"checksum test")

        items = local_storage.list()

        assert items[0].checksum_sha256 is not None
        assert len(items[0].checksum_sha256) == 64
