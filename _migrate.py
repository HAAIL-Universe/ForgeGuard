"""One-off migration runner -- applies ALL migration files in order."""
import glob
import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()

url = os.getenv("DATABASE_URL")
conn = psycopg2.connect(url)
conn.autocommit = True
cur = conn.cursor()

migration_dir = os.path.join(os.path.dirname(__file__), "db", "migrations")
files = sorted(glob.glob(os.path.join(migration_dir, "*.sql")))

for path in files:
    name = os.path.basename(path)
    sql = open(path).read()
    try:
        cur.execute(sql)
        print(f"  ok: {name}")
    except Exception as exc:
        print(f"  skip: {name} ({exc})")

cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
for row in cur.fetchall():
    print(f"  table: {row[0]}")

cur.close()
conn.close()
print("Migration complete!")
