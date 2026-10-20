"""Export operational PostgreSQL data for version control, excluding user accounts."""
from pathlib import Path
import subprocess
import tempfile


TABLES = ('operators', 'orders', 'order_parts', 'process_steps', 'part_pdfs', 'order_status_events')


def main():
    root = Path(__file__).resolve().parents[1]
    destination = root / 'database' / 'welderp_data.sql'
    destination.parent.mkdir(exist_ok=True)
    command = [
        'docker', 'compose', 'exec', '-T', 'db', 'pg_dump',
        '-U', 'welderp_user', '-d', 'welderp_db',
        '--data-only', '--column-inserts', '--on-conflict-do-nothing', '--no-owner', '--no-acl',
    ]
    for table in TABLES:
        command.extend(['--table', 'public.' + table])
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            result = subprocess.run(command, cwd=root, stdout=output, stderr=subprocess.PIPE)
        if result.returncode:
            raise SystemExit('Export failed: ' + result.stderr.decode('utf-8', errors='replace'))
        contents = temporary.read_bytes()
        if b'PostgreSQL database dump complete' not in contents:
            raise SystemExit('Export is incomplete; existing snapshot was not replaced.')
        if any(token in contents for token in (b'INSERT INTO public.users ', b'COPY public.users ', b'hashed_password')):
            raise SystemExit('Account data detected; existing snapshot was not replaced.')
        temporary.write_bytes(contents.rstrip() + b'\n')
        temporary.replace(destination)
        print(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
