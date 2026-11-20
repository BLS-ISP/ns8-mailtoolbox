"""Online SQLite backup CLI helper for Restic module-dump-state.

Usage:
    python -m app.backend.backup_db /app/data/toolbox-backup.sqlite
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
import sys

# Support direct script execution (`python /app/backend/backup_db.py ...`)
# as well as module execution (`python -m app.backend.backup_db ...`).
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.backend.db import get_db_path, init_db  # noqa: E402


def backup_database(dest_path: Path, source_path: Path | None = None) -> Path:
    """Create a consistent online snapshot of toolbox.sqlite using sqlite3.Connection.backup()."""
    src = init_db(get_db_path(source_path))
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_dest = dest.with_name(f"{dest.name}.tmp.{os.getpid()}")

    for stale in (tmp_dest, Path(f"{tmp_dest}-wal"), Path(f"{tmp_dest}-shm")):
        if stale.exists():
            stale.unlink()

    src_conn = sqlite3.connect(str(src), timeout=10.0)
    dst_conn = sqlite3.connect(str(tmp_dest), timeout=10.0)
    try:
        src_conn.backup(dst_conn)
        dst_conn.execute("PRAGMA journal_mode=DELETE;")
        dst_conn.commit()
    finally:
        dst_conn.close()
        src_conn.close()

    for wal_sidecar in (Path(f"{dest}-wal"), Path(f"{dest}-shm")):
        if wal_sidecar.exists():
            wal_sidecar.unlink()

    os.replace(str(tmp_dest), str(dest))
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create an online SQLite backup of toolbox.sqlite")
    parser.add_argument("dest_path", type=Path, help="Destination snapshot file path")
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Optional source SQLite database path (defaults to ${DATA_DIR}/toolbox.sqlite)",
    )
    args = parser.parse_args(argv)
    backup_database(dest_path=args.dest_path, source_path=args.source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
