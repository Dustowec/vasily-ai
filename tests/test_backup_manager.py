"""Tests for TZ-025: byte-level BackupManager."""

from core.backup import BackupManager


def test_create_backup_copies_files_byte_for_byte(tmp_path):
    source_dir = tmp_path / "source"
    data_dir = source_dir / "data"
    data_dir.mkdir(parents=True)

    config_file = source_dir / "config.json"
    memory_file = data_dir / "hot_memory.json"
    config_file.write_bytes(b'{"a":1}')
    memory_file.write_bytes(b"\x00\x01binary")

    backup_root = tmp_path / "backups"
    manager = BackupManager(
        source_base=source_dir,
        source_paths=[config_file, memory_file],
        backup_root=backup_root,
    )

    backup_path = manager.create_backup()

    assert backup_path.exists()
    assert (backup_path / "config.json").read_bytes() == b'{"a":1}'
    assert (backup_path / "data" / "hot_memory.json").read_bytes() == b"\x00\x01binary"


def test_create_backup_skips_missing_source(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    existing = source_dir / "exists.txt"
    missing = source_dir / "missing.txt"
    existing.write_bytes(b"ok")

    backup_root = tmp_path / "backups"
    manager = BackupManager(
        source_base=source_dir,
        source_paths=[existing, missing],
        backup_root=backup_root,
    )

    backup_path = manager.create_backup()

    assert (backup_path / "exists.txt").read_bytes() == b"ok"
    assert not (backup_path / "missing.txt").exists()


def test_list_backups_sorted_newest_first(tmp_path):
    backup_root = tmp_path / "backups"
    manager = BackupManager(
        source_base=tmp_path,
        source_paths=[],
        backup_root=backup_root,
    )

    first = backup_root / "20260101_000000"
    second = backup_root / "20260102_000000"
    first.mkdir(parents=True)
    second.mkdir(parents=True)

    backups = manager.list_backups()

    assert backups == [second, first]
