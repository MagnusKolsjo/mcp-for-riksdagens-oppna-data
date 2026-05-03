"""
db/init_db.py — Initierar databasen för arbetsström 3

Läser DATABASE_URL från .env och skapar rätt tabeller beroende på databastyp:
  - postgresql://...  →  PostgreSQL + pgvector  (schema_postgres.sql)
  - sqlite:///...     →  SQLite + sqlite-vec    (schema_sqlite.sql)

Körning:
    python db/init_db.py

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

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///riksdag_api.db")
SCHEMA_DIR = Path(__file__).parent


def init_postgres(url: str) -> None:
    """Initierar PostgreSQL-databas med pgvector."""
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
    """Initierar SQLite-databas med sqlite-vec."""
    try:
        import sqlite_vec
    except ImportError:
        print("Fel: sqlite-vec saknas. Kör: pip install sqlite-vec", file=sys.stderr)
        sys.exit(1)

    # Extrahera filsökväg ur URL (sqlite:///path/to/file.db)
    db_path = url.replace("sqlite:///", "")

    schema = (SCHEMA_DIR / "schema_sqlite.sql").read_text(encoding="utf-8")

    conn = sqlite3.connect(db_path)
    sqlite_vec.load(conn)          # Laddar sqlite-vec-tillägget
    conn.executescript(schema)

    # sqlite-vec virtual table skapas separat (kräver att tillägget är laddat)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS chunk_embeddings USING vec0(
            chunk_id  INTEGER PRIMARY KEY,
            embedding float[768]
        )
    """)
    conn.commit()
    conn.close()

    print(f"SQLite-databas initierad: {db_path}")




def migrate_add_related_hints(url: str) -> None:
    """
    Lägger till kolumnen related_hints om den saknas (migrering av befintlig databas).
    Säker att köra flera gånger — kolumnen läggs inte till om den redan finns.
    """
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        try:
            import psycopg2
            conn = psycopg2.connect(url)
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("""
                    ALTER TABLE riksdag_api.documents
                    ADD COLUMN IF NOT EXISTS related_hints TEXT
                """)
            conn.close()
            print("PostgreSQL: related_hints kolumn OK.")
        except Exception as e:
            print(f"PostgreSQL-migrering misslyckades: {e}", file=sys.stderr)
    elif url.startswith("sqlite:///"):
        try:
            import sqlite3 as _sq
            db_path = url.replace("sqlite:///", "")
            conn = _sq.connect(db_path)
            # SQLite stöder inte IF NOT EXISTS i ALTER TABLE — kontrollera manuellt
            cols = [r[1] for r in conn.execute("PRAGMA table_info(documents)").fetchall()]
            if "related_hints" not in cols:
                conn.execute("ALTER TABLE documents ADD COLUMN related_hints TEXT")
                conn.commit()
                print("SQLite: related_hints kolumn tillagd.")
            else:
                print("SQLite: related_hints kolumn finns redan.")
            conn.close()
        except Exception as e:
            print(f"SQLite-migrering misslyckades: {e}", file=sys.stderr)

def main() -> None:
    print(f"DATABASE_URL: {DATABASE_URL}")

    migrate_add_related_hints(DATABASE_URL)

    if DATABASE_URL.startswith("postgresql://") or DATABASE_URL.startswith("postgres://"):
        init_postgres(DATABASE_URL)
    elif DATABASE_URL.startswith("sqlite:///"):
        init_sqlite(DATABASE_URL)
    else:
        print(f"Fel: okänt URL-format: {DATABASE_URL}", file=sys.stderr)
        print("Ange antingen postgresql://... eller sqlite:///...", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
