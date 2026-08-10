"""Byte-level backup manager for TZ-025.

Encryption-agnostic: copies files as raw bytes without interpreting content.
"""

from datetime import datetime
from pathlib import Path

from core.logging_config import get_logger

logger = get_logger("core", "BackupManager")


class BackupManager:
    """Creates byte-level backups of selected files."""

    def __init__(
        self,
        source_base: Path,
        source_paths: list[Path],
        backup_root: Path,
    ):
        self.source_base = Path(source_base)
        self.source_paths = [Path(p) for p in source_paths]
        self.backup_root = Path(backup_root)

    def create_backup(self) -> Path:
        """Create a timestamped backup folder and copy files byte-for-byte.

        Missing source files are skipped silently.
        Returns the path to the created backup folder.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = self.backup_root / timestamp
        backup_dir.mkdir(parents=True, exist_ok=True)

        copied = 0
        skipped = 0

        for source_path in self.source_paths:
            if not source_path.exists():
                skipped += 1
                logger.warning(
                    "Backup source missing, skipping",
                    path=str(source_path),
                )
                continue

            relative = source_path.relative_to(self.source_base)
            destination = backup_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)

            data = source_path.read_bytes()
            destination.write_bytes(data)
            copied += 1

        logger.info(
            "Backup created",
            backup_dir=str(backup_dir),
            copied=copied,
            skipped=skipped,
        )
        return backup_dir

    def list_backups(self) -> list[Path]:
        """List existing backup folders, newest first."""
        if not self.backup_root.exists():
            return []

        folders = [p for p in self.backup_root.iterdir() if p.is_dir()]
        folders.sort(key=lambda p: p.name, reverse=True)
        return folders
