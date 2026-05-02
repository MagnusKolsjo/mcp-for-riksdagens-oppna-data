-- =============================================================================
-- Arbetsström 3 — Riksdagens öppna data API
-- Databasschema: SQLite + sqlite-vec
-- Kräver: pip install sqlite-vec
-- sqlite-vec laddas i Python via: import sqlite_vec; sqlite_vec.load(conn)
-- =============================================================================

-- ---------------------------------------------------------------------------
-- documents: ett cachat dokument från riksdagens API
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
    dok_id              TEXT    PRIMARY KEY,
    doktyp              TEXT    NOT NULL,           -- prop, mot, bet, prot, sou, ds, dir
    titel               TEXT,
    datum               TEXT,                       -- ISO-datum som text, t.ex. "2024-03-15"
    rm                  TEXT,                       -- riksmöte, t.ex. "2024/25"
    status              TEXT,                       -- 'ocr' för inskannat material, annars NULL
    url_riksdagen       TEXT,                       -- länk till www.riksdagen.se
    inledning           TEXT,                       -- de första ~500 tecknen av fulltexten
    cached_at           INTEGER NOT NULL,           -- unix-timestamp (seconds)
    is_current_session  INTEGER NOT NULL DEFAULT 0, -- 1 = innevarande riksmöte (har TTL)
    related_hints       TEXT                            -- JSON: lista av relaterade dokument från dokumentstatus-endpointen
);

CREATE INDEX IF NOT EXISTS idx_documents_doktyp ON documents (doktyp);
CREATE INDEX IF NOT EXISTS idx_documents_rm     ON documents (rm);
CREATE INDEX IF NOT EXISTS idx_documents_datum  ON documents (datum);

-- ---------------------------------------------------------------------------
-- chunks: textstycken (~800 tecken) ur ett cachat dokument
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dok_id      TEXT    NOT NULL REFERENCES documents (dok_id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,                   -- ordningsnummer inom dokumentet
    text        TEXT    NOT NULL,
    char_start  INTEGER,
    char_end    INTEGER
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
