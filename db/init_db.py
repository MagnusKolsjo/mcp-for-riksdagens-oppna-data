"""
db/init_db.py — Initierar och migrerar databasen för arbetsström 3

Läser DATABASE_URL från .env och skapar/migrerar rätt tabeller beroende på
databastyp:
  - postgresql://...  →  PostgreSQL + pgvector  (schema_postgres.sql)
  - sqlite:///...     →  SQLite + sqlite-vec    (schema_sqlite.sql)

Körning (engångsinitiering eller migration):
    python db/init_db.py

Anropas även automatiskt vid MCP-serveruppstart via mcp_server.py — alla
funktioner är idempotenta och säkra att köra upprepade gånger.

Krav (PostgreSQL):
    pip install psycopg2-binary pgvector

Krav (SQLite):
    pip install sqlite-vec
"""

import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env')

# DATABASE_URL utan default — ett odefinierat val ger tydligt felmeddelande i main().
DATABASE_URL = os.getenv("DATABASE_URL", "")
SCHEMA_DIR   = Path(__file__).parent


def init_postgres(url: str) -> None:
    """Initierar PostgreSQL-databas med baseline-schema och kör migrationer."""
    try:
        import psycopg2
    except ImportError:
        print("Fel: psycopg2 saknas. Kör: pip install psycopg2-binary", file=sys.stderr)
        sys.exit(1)

    schema = (SCHEMA_DIR / "schema_postgres.sql").read_text(encoding="utf-8")

    conn = psycopg2.connect(url)
    conn.autocommit = True
    with conn.cursor() as cur:
        # Säkerställ att schemat finns innan schema_postgres.sql körs.
        # Schemat skapas även i SQL-filen, men detta skyddar mot race conditions
        # om init körs parallellt mot en annan process.
        cur.execute("CREATE SCHEMA IF NOT EXISTS riksdag_api")
        cur.execute(schema)
    conn.close()

    print("PostgreSQL-databas initierad.")
    print("OBS: IVFFlat-indexet för embeddings skapas manuellt efter att data laddats in.")
    print("  Se kommentaren i schema_postgres.sql.")


def init_sqlite(url: str) -> None:
    """Initierar SQLite-databas med baseline-schema och kör migrationer."""
    try:
        import sqlite_vec
    except ImportError:
        print("Fel: sqlite-vec saknas. Kör: pip install sqlite-vec", file=sys.stderr)
        sys.exit(1)

    db_path = url.replace("sqlite:///", "")

    # Baseline-schemat (CREATE TABLE/INDEX IF NOT EXISTS — idempotent)
    schema = (SCHEMA_DIR / "schema_sqlite.sql").read_text(encoding="utf-8")

    conn = sqlite3.connect(db_path)
    sqlite_vec.load(conn)
    conn.executescript(schema)

    # sqlite-vec virtual table skapas separat (kräver att tillägget är laddat)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS chunk_embeddings USING vec0(
            chunk_id  INTEGER PRIMARY KEY,
            embedding float[768]
        )
    """)
    conn.commit()

    # Kör migrationer
    _migrera_sqlite_v3_0_0(conn)

    conn.close()

    print(f"SQLite-databas initierad: {db_path}")


def _migrera_sqlite_v3_0_0(conn: sqlite3.Connection) -> None:
    """
    Migration v3.0.0: lägger till senast_anvand för LRU-eviction.
    Säker att köra flera gånger — kolumnen läggs bara till om den saknas.
    """
    cols = [r[1] for r in conn.execute("PRAGMA table_info(dokument)").fetchall()]
    if "senast_anvand" not in cols:
        conn.execute("ALTER TABLE dokument ADD COLUMN senast_anvand INTEGER")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dokument_senast_anvand "
            "ON dokument (senast_anvand ASC)"
        )
        conn.commit()


def initiera_schema(url: str) -> None:
    """
    Idempotent schema-init och migration.

    Anropas från mcp_server.py vid uppstart. Alla operationer är omslutna
    i try/except i anroparen så att servern startar även om databasen är nere.

    Anropas även direkt via main() när skriptet körs manuellt.
    """
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        init_postgres(url)
    elif url.startswith("sqlite:///"):
        init_sqlite(url)
    else:
        raise ValueError(
            f"Okänt DATABASE_URL-format: {url!r}\n"
            "Ange antingen postgresql://... eller sqlite:///..."
        )


def main() -> None:
    if not DATABASE_URL:
        print(
            "Fel: DATABASE_URL saknas. Ange anslutningsstrang i .env:\n"
            "  postgresql://anvandare:losenord@localhost:5432/riksdagstryck\n"
            "  sqlite:///riksdag_api.db",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"DATABASE_URL: {DATABASE_URL}")
    try:
        initiera_schema(DATABASE_URL)
    except ValueError as e:
        print(f"Fel: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
