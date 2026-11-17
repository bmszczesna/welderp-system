"""Start the isolated local demo, preserving its existing data and adding missing examples."""
from datetime import datetime
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PROJECT = 'welderp-magazyn'
COMPOSE = ['docker', 'compose', '-p', PROJECT, '-f', str(ROOT / 'docker-compose.preview.yml')]
DB_CONTAINER = PROJECT + '-db-1'
DB_USER, DB_NAME = 'warehouse_test', 'warehouse_preview'
VOLUME = PROJECT + '_warehouse_preview_data'


def run(args, **options):
    return subprocess.run(args, cwd=ROOT, check=True, **options)


def existing_database():
    result = subprocess.run(['docker','inspect',DB_CONTAINER], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        return None
    info = json.loads(result.stdout)[0]
    if info['Config']['Labels'].get('com.docker.compose.project') != PROJECT:
        raise SystemExit('Unexpected container owner; no database was changed.')
    return info


def backup_database():
    directory = ROOT / 'backups'
    directory.mkdir(exist_ok=True)
    path = directory / f'preview_before_setup_{datetime.now():%Y%m%d_%H%M%S_%f}.sql'
    with path.open('xb') as output:
        run(['docker','exec',DB_CONTAINER,'pg_dump','-U',DB_USER,'-d',DB_NAME,'--no-owner','--no-acl'], stdout=output)
    if b'PostgreSQL database dump complete' not in path.read_bytes():
        raise SystemExit('Incomplete backup; setup stopped.')
    print('Backup:',path,flush=True)
    return path


def main():
    info = existing_database()
    backup = None
    migrate_volume = False
    if info:
        if info['State']['Status'] != 'running':
            run(['docker','start',DB_CONTAINER])
        # Wait for PostgreSQL via Compose's existing health check before dumping.
        for _ in range(30):
            ready = subprocess.run(['docker','exec',DB_CONTAINER,'pg_isready','-U',DB_USER,'-d',DB_NAME],
                                   cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if ready.returncode == 0:
                break
            import time
            time.sleep(1)
        else:
            raise SystemExit('Preview database is not ready; setup stopped.')
        # Stop only this preview backend to prevent writes during a volume upgrade.
        run(COMPOSE+['stop','backend'])
        backup = backup_database()
        mounts = [mount for mount in info['Mounts'] if mount['Destination'] == '/var/lib/postgresql/data']
        migrate_volume = bool(mounts and mounts[0].get('Name') != VOLUME)
    run(COMPOSE+['up','-d','--wait','--wait-timeout','90','db'])
    if migrate_volume:
        count = run(COMPOSE+['exec','-T','db','psql','-U',DB_USER,'-d',DB_NAME,'-tAc',
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"], stdout=subprocess.PIPE).stdout.strip()
        if count != b'0':
            raise SystemExit('Target preview volume is not empty; backup retained, automatic restore stopped.')
        run(COMPOSE+['exec','-T','db','psql','-U',DB_USER,'-d',DB_NAME,'-v','ON_ERROR_STOP=1','--single-transaction','-q'],
            input=backup.read_bytes())
        print('Previous preview data restored to the persistent volume.',flush=True)
    run(COMPOSE+['up','--build','-d','--wait','--wait-timeout','90','backend'])
    if backup is None:
        backup = backup_database()
    run(COMPOSE+['exec','-T','backend','python','-m','backend.seed_demo','--backup-file',
        '/app/backups/'+backup.name,'--with-warehouse'])
    print('Ready: http://127.0.0.1:8016/')
    print('Manager demo: kierownik_demo / KierownikDemo123!')
    print('Operator demo: operator_demo / OperatorDemo123!')


if __name__ == '__main__':
    main()
