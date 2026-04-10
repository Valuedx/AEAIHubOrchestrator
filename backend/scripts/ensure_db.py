"""One-off: ensure ae_orchestrator database exists (local dev)."""
import os
import sys

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

url = os.environ.get(
    "ORCHESTRATOR_DATABASE_URL",
    "postgresql://postgres:root@localhost:5432/ae_orchestrator",
)
# Connect to maintenance DB
base = url.rsplit("/", 1)[0] + "/postgres"
name = url.rsplit("/", 1)[-1].split("?")[0]

try:
    conn = psycopg2.connect(base)
except Exception as e:
    print(f"Failed to connect to PostgreSQL ({base}): {e}", file=sys.stderr)
    sys.exit(1)

conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
cur = conn.cursor()
cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
if not cur.fetchone():
    cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    print(f"Created database {name}")
else:
    print(f"Database {name} already exists")
cur.close()
conn.close()
