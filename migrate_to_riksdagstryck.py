#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Magnus Kolsjö
"""
migrate_to_riksdagstryck.py — Migrerar arbetsström 3:s data till den gemensamma databasen.

Vad skriptet gör:
  1. Ansluter till källdatabasen (riksdag_rag) och måldatabasen (riksdagstryck)
  2. Skapar schema riksdag_api och tabellerna dokument + chunks i riksdagstryck
  3. Kopierar all data: public.documents → riksdag_api.dokument
                        public.chunks   → riksdag_api.chunks
  4. Verifierar att radantalet stämmer
  5. Skriver ut instruktioner för nästa steg

Kör skriptet från repots rot:
  python3 migrate_to_riksdagstryck.py

Kräver psycopg2: pip install psycopg2-binary
"""

import sys
import psycopg2
import psycopg2.extras

# ---------------------------------------------------------------------------
# Konfiguration — anpassa vid behov
# ---------------------------------------------------------------------------

# Källdatabas: riksdag_rag (arbetsström 3:s gamla databas)
KALLA_URL = "postgresql://DB_ANVANDARE_BORTTAGET:LOSENORD_BORTTAGET@localhost:5432/riksdag_rag"

# Måldatabas: riksdagstryck (den gemensamma databasen)
MAL_URL = "postgresql://DB_ANVANDARE_BORTTAGET:LOSENORD_BORTTAGET@localhost:5432/riksdagstryck"

# ---------------------------------------------------------------------------


def anslut(url: str, namn: str) -> psycopg2.extensions.connection:
    """Ansluter till en PostgreSQL-databas och returnerar connection-objektet."""
    try:
        conn = psycopg2.connect(url)
        conn.autocommit = False
        print(f"  \u2713 Ansluten till {namn}")
        return conn
    except psycopg2.OperationalError as e:
        print(f"  \u2717 Kunde inte ansluta till {namn}: {e}")
        sys.exit(1)


def skapa_schema_och_tabeller(mal: psycopg2.extensions.connection) -> None:
    """Skapar riksdag_api-schema och tabeller i måldatabasen om de saknas."""
    with mal.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        cur.execute("CREATE SCHEMA IF NOT EXISTS riksdag_api;")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS riksdag_api.dokument (
                dok_id              TEXT        PRIMARY KEY,
                doktyp              TEXT        NOT NULL,
                titel               TEXT,
                datum               DATE,
                rm                  TEXT,
                status              TEXT,
                url_riksdagen       TEXT,
                inledning           TEXT,
                cachad_vid           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                aktuellt_riksmote  BOOLEAN     NOT NULL DEFAULT FALSE,
                relaterat_tips       TEXT
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_doktyp ON riksdag_api.dokument (doktyp);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_rm     ON riksdag_api.dokument (rm);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_datum  ON riksdag_api.dokument (datum);")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS riksdag_api.chunks (
                id          BIGSERIAL   PRIMARY KEY,
                dok_id      TEXT        NOT NULL REFERENCES riksdag_api.dokument (dok_id) ON DELETE CASCADE,
                chunk_index INTEGER     NOT NULL,
                text        TEXT        NOT NULL,
                tecken_start  INTEGER,
                tecken_slut    INTEGER,
                embedding   vector(768)
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_dok_id ON riksdag_api.chunks (dok_id);")
        mal.commit()
    print("  \u2713 Schema riksdag_api och tabeller skapade (eller redan befintliga)")


def rakna_rader(conn: psycopg2.extensions.connection, tabell: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {tabell};")
        return cur.fetchone()[0]


def _batchar(cursor, storlek: int):
    while True:
        rader = cursor.fetchmany(storlek)
        if not rader:
            break
        yield rader


def kopiera_documents(kalla, mal) -> int:
    with kalla.cursor(name="kalla_documents", cursor_factory=psycopg2.extras.DictCursor) as src:
        src.execute("""
            SELECT dok_id, doktyp, titel, datum, rm, status, url_riksdagen,
                   inledning, cachad_vid, aktuellt_riksmote, relaterat_tips
            FROM public.documents;
        """)
        kopierade = 0
        with mal.cursor() as dst:
            for batch in _batchar(src, 500):
                psycopg2.extras.execute_values(
                    dst,
                    """
                    INSERT INTO riksdag_api.dokument
                        (dok_id, doktyp, titel, datum, rm, status, url_riksdagen,
                         inledning, cachad_vid, aktuellt_riksmote, relaterat_tips)
                    VALUES %s
                    ON CONFLICT (dok_id) DO NOTHING;
                    """,
                    [(r["dok_id"], r["doktyp"], r["titel"], r["datum"], r["rm"],
                      r["status"], r["url_riksdagen"], r["inledning"], r["cachad_vid"],
                      r["aktuellt_riksmote"], r["relaterat_tips"]) for r in batch],
                )
                kopierade += len(batch)
                print(f"    {kopierade} dokument kopierade...", end="\r", flush=True)
        mal.commit()
    print()
    return kopierade


def kopiera_chunks(kalla, mal) -> int:
    with kalla.cursor(name="kalla_chunks", cursor_factory=psycopg2.extras.DictCursor) as src:
        src.execute("""
            SELECT dok_id, chunk_index, text, tecken_start, tecken_slut, embedding::text
            FROM public.chunks;
        """)
        kopierade = 0
        with mal.cursor() as dst:
            for batch in _batchar(src, 200):
                psycopg2.extras.execute_values(
                    dst,
                    """
                    INSERT INTO riksdag_api.chunks
                        (dok_id, chunk_index, text, tecken_start, tecken_slut, embedding)
                    VALUES %s
                    ON CONFLICT DO NOTHING;
                    """,
                    [(r["dok_id"], r["chunk_index"], r["text"], r["tecken_start"],
                      r["tecken_slut"], r["embedding"]) for r in batch],
                    template="(%s, %s, %s, %s, %s, %s::vector)",
                )
                kopierade += len(batch)
                print(f"    {kopierade} chunks kopierade...", end="\r", flush=True)
        mal.commit()
    print()
    return kopierade


def main() -> None:
    print("\n=== Migration: riksdag_rag \u2192 riksdagstryck (riksdag_api) ===\n")

    print("Ansluter till databaser...")
    kalla = anslut(KALLA_URL, "riksdag_rag (k\u00e4lla)")
    mal   = anslut(MAL_URL,   "riksdagstryck (m\u00e5l)")

    print("\nKontrollerar befintlig data i k\u00e4llan...")
    ant_docs   = rakna_rader(kalla, "public.documents")
    ant_chunks = rakna_rader(kalla, "public.chunks")
    print(f"  K\u00e4llan inneh\u00e5ller: {ant_docs} dokument, {ant_chunks} chunks")

    if ant_docs == 0:
        print("\nIngen data att migrera. Avslutar.")
        kalla.close(); mal.close()
        return

    print("\nSkapar schema och tabeller i riksdagstryck...")
    skapa_schema_och_tabeller(mal)

    print("\nKopierar dokument...")
    kopiera_documents(kalla, mal)

    print("Kopierar chunks...")
    kopiera_chunks(kalla, mal)

    print("\nVerifierar...")
    ok = True
    for tabell_src, tabell_dst in [("public.documents", "riksdag_api.dokument"),
                                    ("public.chunks",    "riksdag_api.chunks")]:
        n_src = rakna_rader(kalla, tabell_src)
        n_dst = rakna_rader(mal,   tabell_dst)
        if n_dst >= n_src:
            print(f"  \u2713 {tabell_dst}: {n_dst} rader (k\u00e4lla: {n_src})")
        else:
            print(f"  \u2717 {tabell_dst}: {n_dst} rader men k\u00e4lla hade {n_src} — kontrollera!")
            ok = False

    kalla.close(); mal.close()

    if ok:
        print("""
=== Migration klar ===

N\u00e4sta steg:
  1. Uppdatera DATABASE_URL i b\u00e5da .env-filer till:
       DATABASE_URL=postgresql://DB_ANVANDARE_BORTTAGET:LOSENORD_BORTTAGET@localhost:5432/riksdagstryck

     Filer att uppdatera:
       - stream-03-riksdagens-oppna-data/.env  (om den finns)
       - ~/MCP-Servers/riksdag-oppna-data/.env

  2. Starta om MCP-servern f\u00f6r arbetsstr\u00f6m 3.

  3. N\u00e4r allt verifierats kan riksdag_rag-databasen tas bort:
       dropdb -U DB_ANVANDARE_BORTTAGET riksdag_rag
""")
    else:
        print("\n\u26a0 Kontrollera felen ovan innan du uppdaterar .env-filen.")


if __name__ == "__main__":
    main()
