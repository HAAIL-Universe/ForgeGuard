"""One-off migration runner."""
import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()

url = os.getenv("DATABASE_URL")
conn = psycopg2.connect(url)
conn.autocommit = True
cur = conn.cursor()

sql = open("db/migrations/001_initial_schema.sql").read()
cur.execute(sql)

cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
for row in cur.fetchall():
    print(f"  ok: {row[0]}")

cur.close()
conn.close()
print("Migration complete!")
