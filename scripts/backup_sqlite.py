import shutil
from datetime import datetime
from pathlib import Path

DB_PATH = Path("data/nailsbot.db")
BACKUP_DIR = Path("data/backups")


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"Database file not found: {DB_PATH}")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = BACKUP_DIR / f"nailsbot_{stamp}.db"
    shutil.copy2(DB_PATH, target)
    print(f"Backup created: {target}")


if __name__ == "__main__":
    main()
