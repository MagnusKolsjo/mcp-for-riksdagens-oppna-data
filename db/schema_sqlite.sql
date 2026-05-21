-- =============================================================================
-- Arbetsström 3 — Riksdagens öppna data API
-- Databasschema: SQLite + sqlite-vec
-- Kräver: pip install sqlite-vec
-- sqlite-vec laddas i Python via: import sqlite_vec; sqlite_vec.load(conn)
-- =============================================================================

-- =============================================================================
-- BASELINE v1.0 — låst vid publicering 2026-05-02
-- Ändra ALDRIG detta block. Alla framtida schemaändringar läggs i migrationsblocket.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- dokument: ett cachat dokument från riksdagens API
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dokument (
    dok_id              TEXT    PRIMARY KEY,
    doktyp              TEXT    NOT NULL,           -- prop, mot, bet, prot, sou, ds, dir
    titel               TEXT,
    datum               TEXT,                       -- ISO-datum som text, t.ex. "2024-03-15"
    rm                  TEXT,                       -- riksmöte, t.ex. "2024/25"
    status              TEXT,                       -- 'ocr' för inskannat material, annars NULL
    url_riksdagen       TEXT,                       -- länk till www.riksdagen.se
    inledning           TEXT,                       -- de första ~500 tecknen av fulltexten
    cachad_vid          INTEGER NOT NULL,           -- unix-timestamp (seconds)
    aktuellt_riksmote   INTEGER NOT NULL DEFAULT 0, -- 1 = innevarande riksmöte (har TTL)
    relaterat_tips      TEXT                        -- JSON: lista av relaterade dokument från dokumentstatus-endpointen
);

CREATE INDEX IF NOT EXISTS idx_documents_doktyp ON dokument (doktyp);
CREATE INDEX IF NOT EXISTS idx_documents_rm     ON dokument (rm);
CREATE INDEX IF NOT EXISTS idx_documents_datum  ON dokument (datum);

-- ---------------------------------------------------------------------------
-- chunks: textstycken (~800 tecken) ur ett cachat dokument
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chunks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    dok_id       TEXT    NOT NULL REFERENCES dokument (dok_id) ON DELETE CASCADE,
    chunk_index  INTEGER NOT NULL,                  -- ordningsnummer inom dokumentet
    text         TEXT    NOT NULL,
    tecken_start INTEGER,
    tecken_slut  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_chunks_dok_id ON chunks (dok_id);

-- ---------------------------------------------------------------------------
-- chunk_embeddings: sqlite-vec virtual table för vektorsökning
-- Skapas i init_db.py (kräver att sqlite-vec är laddat i anslutningen)
-- Motsvarar: CREATE VIRTUAL TABLE chunk_embeddings USING vec0(
--                chunk_id INTEGER PRIMARY KEY,
--                embedding float[768]
--            );
-- ---------------------------------------------------------------------------

-- =============================================================================
-- MIGRATIONER — lägg nya schemaändringar som egna block nedan.
-- SQLite stöder inte ADD COLUMN IF NOT EXISTS i äldre versioner;
-- init_db.py kontrollerar manuellt om kolumnen finns (PRAGMA table_info).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Migration v3.0.0 — 2026-05-21
-- Lägger till senast_anvand för LRU-eviction av cache.
-- Tillämpas i init_db.py via _migrera_sqlite_v3_0_0().
-- ---------------------------------------------------------------------------
-- ALTER TABLE dokument ADD COLUMN senast_anvand INTEGER;   -- unix-timestamp
-- CREATE INDEX IF NOT EXISTS idx_dokument_senast_anvand ON dokument (senast_anvand ASC);
