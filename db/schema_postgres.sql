-- =============================================================================
-- Arbetsström 3 — Riksdagens öppna data API
-- Databasschema: PostgreSQL + pgvector
-- Kräver: CREATE EXTENSION vector; (pgvector installerat)
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- documents: ett cachat dokument från riksdagens API
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
    dok_id              TEXT        PRIMARY KEY,
    doktyp              TEXT        NOT NULL,           -- prop, mot, bet, prot, sou, ds, dir
    titel               TEXT,
    datum               DATE,
    rm                  TEXT,                           -- riksmöte, t.ex. "2024/25"
    status              TEXT,                           -- 'ocr' för inskannat material, annars NULL
    url_riksdagen       TEXT,                           -- länk till www.riksdagen.se
    inledning           TEXT,                           -- de första ~500 tecknen av fulltexten
    cached_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_current_session  BOOLEAN     NOT NULL DEFAULT FALSE, -- TRUE = innevarande riksmöte (har TTL)
    related_hints       TEXT                                    -- JSON: lista av relaterade dokument från dokumentstatus-endpointen
);

CREATE INDEX IF NOT EXISTS idx_documents_doktyp ON documents (doktyp);
CREATE INDEX IF NOT EXISTS idx_documents_rm     ON documents (rm);
CREATE INDEX IF NOT EXISTS idx_documents_datum  ON documents (datum);

-- ---------------------------------------------------------------------------
-- chunks: textstycken (~800 tecken) ur ett cachat dokument
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chunks (
    id          BIGSERIAL   PRIMARY KEY,
    dok_id      TEXT        NOT NULL REFERENCES documents (dok_id) ON DELETE CASCADE,
    chunk_index INTEGER     NOT NULL,                   -- ordningsnummer inom dokumentet
    text        TEXT        NOT NULL,
    char_start  INTEGER,                                -- position i originaltexten
    char_end    INTEGER,
    embedding   vector(768)                             -- KBLab/sentence-bert-swedish-cased → 768 dim
);

CREATE INDEX IF NOT EXISTS idx_chunks_dok_id ON chunks (dok_id);

-- IVFFlat-index för approximate nearest neighbor-sökning.
-- Byggs efter att data laddats in (kräver minst några hundra rader).
-- Kör manuellt: CREATE INDEX idx_chunks_embedding ON chunks
--               USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
