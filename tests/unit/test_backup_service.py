"""Тесты PostgresBackupService — создание, восстановление, ротация."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.backup.app.backup_service import PostgresBackupService
from scripts.backup.app.config import AppConfig
from scripts.backup.app.entities import RestoreRequest
from scripts.backup.app.exceptions import BackupCreationError, BackupValidationError
from scripts.backup.app.storage import LocalStorage


@pytest.fixture
def backup_service(app_config: AppConfig, local_storage: LocalStorage) -> PostgresBackupService:
    """Сервис без S3 для тестов.

    Args:
        app_config: Конфиг.
        local_storage: Хранилище.

    Returns:
        PostgresBackupService.
    """
    return PostgresBackupService(config=app_config, local_storage=local_storage, s3_storage=None)


class TestCreateBackup:
    """Тесты создания бэкапа."""

    @patch("scripts.backup.app.backup_service.PostgresBackupService._run_pg_dump")
    def test_create_backup_success(
        self, mock_dump: MagicMock, backup_service: PostgresBackupService, tmp_backup_dir: Path
    ) -> None:
        """Успешный бэкап создаёт файл и метаданные."""

        def fake_dump(path: Path) -> float:
            path.write_bytes(b"fake dump gz")
            return 1.23

        mock_dump.side_effect = fake_dump

        result = backup_service.create_backup(filename="odoo_20990115_030000.sql.gz")

        assert result.metadata.filename == "odoo_20990115_030000.sql.gz"
        assert result.metadata.size_bytes > 0
        assert result.metadata.checksum_sha256 is not None
        assert result.is_success
        assert (tmp_backup_dir / "odoo_20990115_030000.sql.gz").exists()

    def test_create_backup_invalid_filename(self, backup_service: PostgresBackupService) -> None:
        """Невалидное имя с / вызывает ValidationError."""
        with pytest.raises(BackupValidationError):
            backup_service.create_backup(filename="../evil.sql.gz")

    @patch("scripts.backup.app.backup_service.PostgresBackupService._run_pg_dump")
    def test_create_backup_with_s3(
        self, mock_dump: MagicMock, app_config: AppConfig, local_storage: LocalStorage, mock_s3_storage: MagicMock
    ) -> None:
        """При настроенном S3 — загружает туда (best-effort)."""

        def fake_dump(path: Path) -> float:
            path.write_bytes(b"data")
            return 0.5

        mock_dump.side_effect = fake_dump
        svc = PostgresBackupService(config=app_config, local_storage=local_storage, s3_storage=mock_s3_storage)

        result = svc.create_backup(filename="odoo_20240115_030000.sql.gz")

        mock_s3_storage.save.assert_called_once()
        assert result.s3_uploaded is True

    @patch("scripts.backup.app.backup_service.PostgresBackupService._run_pg_dump")
    def test_create_backup_s3_failure_is_warning(
        self, mock_dump: MagicMock, app_config: AppConfig, local_storage: LocalStorage
    ) -> None:
        """Падение S3 не валит бэкап — добавляется в warnings."""

        def fake_dump(path: Path) -> float:
            path.write_bytes(b"data")
            return 0.5

        mock_dump.side_effect = fake_dump
        bad_s3 = MagicMock()
        bad_s3.save.side_effect = Exception("S3 down")

        svc = PostgresBackupService(config=app_config, local_storage=local_storage, s3_storage=bad_s3)
        result = svc.create_backup(filename="odoo_20240115_030000.sql.gz")

        assert result.s3_uploaded is False
        assert any("S3" in w for w in result.warnings)
        # Локальный файл всё равно есть
        assert result.is_success

    @patch("subprocess.run")
    def test_run_pg_dump_failure_raises(
        self, mock_run: MagicMock, backup_service: PostgresBackupService, tmp_backup_dir: Path
    ) -> None:
        """pg_dump с ненулевым кодом кидает BackupCreationError."""
        mock_run.return_value = MagicMock(returncode=1, stderr=b"pg_dump: error")

        with pytest.raises(BackupCreationError, match="pg_dump failed"):
            backup_service.create_backup(filename="odoo_20240115_030000.sql.gz")


class TestRestoreBackup:
    """Тесты восстановления."""

    def test_restore_file_not_found(self, backup_service: PostgresBackupService, tmp_backup_dir: Path) -> None:
        """Несуществующий файл — ValidationError."""
        req = RestoreRequest(backup_path=tmp_backup_dir / "missing.sql.gz", target_db="odoo_test")
        with pytest.raises(BackupValidationError):
            backup_service.restore_backup(req)

    @patch("subprocess.run")
    def test_restore_plain_sql(
        self, mock_run: MagicMock, backup_service: PostgresBackupService, tmp_backup_dir: Path
    ) -> None:
        """Восстановление plain .sql вызывает psql."""
        f = tmp_backup_dir / "backup.sql"
        f.write_bytes(b"SELECT 1;")
        mock_run.return_value = MagicMock(returncode=0, stderr=b"", stdout=b"")

        req = RestoreRequest(backup_path=f, target_db="odoo_test")
        backup_service.restore_backup(req)

        mock_run.assert_called_once()
        assert "psql" in mock_run.call_args[0][0]

    @patch("subprocess.Popen")
    @patch("subprocess.run")
    def test_restore_gz(
        self, mock_run: MagicMock, mock_popen: MagicMock, backup_service: PostgresBackupService, tmp_backup_dir: Path
    ) -> None:
        """Восстановление .sql.gz идёт через gzip | psql."""
        f = tmp_backup_dir / "backup.sql.gz"
        f.write_bytes(b"gz")

        mock_proc = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr.read.return_value = b""
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")

        req = RestoreRequest(backup_path=f, target_db="odoo_test")
        backup_service.restore_backup(req)

        mock_popen.assert_called_once()
        mock_run.assert_called_once()


class TestHealthcheck:
    """Тесты healthcheck."""

    def test_no_backups_unhealthy(self, backup_service: PostgresBackupService) -> None:
        """Без бэкапов — нездоров."""
        assert backup_service.healthcheck() is False

    @patch("scripts.backup.app.backup_service.PostgresBackupService._run_pg_dump")
    def test_recent_backup_healthy(
        self, mock_dump: MagicMock, backup_service: PostgresBackupService
    ) -> None:
        """Свежий бэкап — здоров."""

        def fake_dump(path: Path) -> float:
            path.write_bytes(b"data")
            return 0.1

        mock_dump.side_effect = fake_dump
        backup_service.create_backup(filename="odoo_20990101_030000.sql.gz")

        assert backup_service.healthcheck() is True
