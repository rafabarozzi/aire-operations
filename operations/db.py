import os
from pathlib import Path
import psycopg
from psycopg.rows import dict_row

def connect():
    return psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row)

def migrate():
    with connect() as db:
        db.execute("SELECT pg_advisory_xact_lock(40773001)")
        db.execute("CREATE TABLE IF NOT EXISTS operations_migrations (version text PRIMARY KEY)")
        for path in sorted((Path(__file__).resolve().parents[1]/'migrations').glob('*.sql')):
            if db.execute("SELECT 1 FROM operations_migrations WHERE version=%s",(path.name,)).fetchone():
                continue
            db.execute(path.read_text())
            db.execute("INSERT INTO operations_migrations VALUES (%s)",(path.name,))

if __name__ == '__main__':
    migrate()
