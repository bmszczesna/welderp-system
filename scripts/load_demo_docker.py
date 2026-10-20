"""Back up the Compose PostgreSQL database, then add demo data to that database."""
from datetime import datetime
from pathlib import Path
import subprocess


def main():
    root = Path(__file__).resolve().parents[1]
    directory = root / "backups"
    directory.mkdir(exist_ok=True)
    backup = directory / f"before_demo_{datetime.now():%Y%m%d_%H%M%S_%f}.sql"
    with backup.open("xb") as output:
        result = subprocess.run([
            "docker", "compose", "exec", "-T", "db", "pg_dump",
            "-U", "welderp_user", "-d", "welderp_db", "--no-owner", "--no-acl",
        ], cwd=root, stdout=output, stderr=subprocess.PIPE)
    if result.returncode:
        raise SystemExit("Backup failed; no demo data was added. " + result.stderr.decode("utf-8", errors="replace"))
    contents = backup.read_bytes()
    if b"PostgreSQL database dump" not in contents or b"PostgreSQL database dump complete" not in contents:
        raise SystemExit("Backup incomplete; no demo data was added.")
    print(f"PostgreSQL backup: {backup}", flush=True)
    subprocess.run([
        "docker", "compose", "exec", "-T", "backend", "python", "-m", "backend.seed_demo",
        "--backup-file", "/app/backups/" + backup.name,
    ], cwd=root, check=True)


if __name__ == "__main__":
    main()
