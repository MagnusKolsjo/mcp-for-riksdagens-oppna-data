-- ============================================================
-- stream-03-riksdagens-oppna-data: Migration v2.0.0 — ASCII-svenska
-- ============================================================
-- Brytande migration. Renamar tabeller och kolumner i riksdag_api-schemat
-- så att Python-koden i stream-03 v2.0.0 kan köra mot databasen.
--
-- Idempotent: hjälpfunktionerna kontrollerar mot information_schema och
-- hoppar tysta över rename som redan är applicerade. Säker att köra om.
--
-- Förutsättning: Claude Desktop ska vara stängt så att MCP-servern inte
-- läser/skriver mot tabellerna under transaktionen.
--
-- Backup ska tas FÖRE körning (eller använd tidigare backup om den är fräsch).
-- ============================================================

\set ON_ERROR_STOP on

BEGIN;

-- ----------------------------------------------------------
-- Hjälpfunktioner (skapas i pg_temp — försvinner efter session)
-- ----------------------------------------------------------

CREATE OR REPLACE FUNCTION pg_temp.byt_tabell(
    p_schema TEXT, p_gammal TEXT, p_ny TEXT
) RETURNS VOID AS $func$
DECLARE
    finns_gammal BOOLEAN;
    finns_ny     BOOLEAN;
BEGIN
    SELECT EXISTS(SELECT 1 FROM information_schema.tables
        WHERE table_schema = p_schema AND table_name = p_gammal) INTO finns_gammal;
    SELECT EXISTS(SELECT 1 FROM information_schema.tables
        WHERE table_schema = p_schema AND table_name = p_ny) INTO finns_ny;
    IF finns_gammal AND NOT finns_ny THEN
        EXECUTE format('ALTER TABLE %I.%I RENAME TO %I', p_schema, p_gammal, p_ny);
        RAISE NOTICE 'Bytte tabell %.% -> %', p_schema, p_gammal, p_ny;
    ELSIF finns_ny AND NOT finns_gammal THEN
        RAISE NOTICE 'Tabell %.% -> % redan applicerad — hoppar', p_schema, p_gammal, p_ny;
    ELSIF NOT finns_ny AND NOT finns_gammal THEN
        RAISE EXCEPTION 'Varken tabell % eller % finns i schema %', p_gammal, p_ny, p_schema;
    ELSE
        RAISE EXCEPTION 'BÅDA tabellerna % och % finns i %, manuell utredning kravs', p_gammal, p_ny, p_schema;
    END IF;
END;
$func$ LANGUAGE plpgsql;


CREATE OR REPLACE FUNCTION pg_temp.byt_kolumn(
    p_schema TEXT, p_tabell TEXT, p_gammal TEXT, p_ny TEXT
) RETURNS VOID AS $func$
DECLARE
    finns_gammal BOOLEAN;
    finns_ny     BOOLEAN;
BEGIN
    SELECT EXISTS(SELECT 1 FROM information_schema.columns
        WHERE table_schema = p_schema AND table_name = p_tabell AND column_name = p_gammal)
        INTO finns_gammal;
    SELECT EXISTS(SELECT 1 FROM information_schema.columns
        WHERE table_schema = p_schema AND table_name = p_tabell AND column_name = p_ny)
        INTO finns_ny;
    IF finns_gammal AND NOT finns_ny THEN
        EXECUTE format('ALTER TABLE %I.%I RENAME COLUMN %I TO %I',
                       p_schema, p_tabell, p_gammal, p_ny);
        RAISE NOTICE 'Bytte kolumn %.%.% -> %', p_schema, p_tabell, p_gammal, p_ny;
    ELSIF finns_ny AND NOT finns_gammal THEN
        RAISE NOTICE 'Kolumn %.%.% -> % redan applicerad — hoppar', p_schema, p_tabell, p_gammal, p_ny;
    ELSIF NOT finns_ny AND NOT finns_gammal THEN
        RAISE EXCEPTION 'Varken kolumn % eller % finns i %.%', p_gammal, p_ny, p_schema, p_tabell;
    ELSE
        RAISE EXCEPTION 'BÅDA kolumnerna % och % finns i %.%, manuell utredning kravs',
                        p_gammal, p_ny, p_schema, p_tabell;
    END IF;
END;
$func$ LANGUAGE plpgsql;


-- ----------------------------------------------------------
-- 1) Tabellrenamn
-- ----------------------------------------------------------
SELECT pg_temp.byt_tabell('riksdag_api', 'documents', 'dokument');

-- chunks-tabellen behålls oförändrad (chunks är vedertagen AI-vokabulär).

-- ----------------------------------------------------------
-- 2) Kolumnrenamn i riksdag_api.dokument
-- ----------------------------------------------------------
SELECT pg_temp.byt_kolumn('riksdag_api', 'dokument', 'cached_at',          'cachad_vid');
SELECT pg_temp.byt_kolumn('riksdag_api', 'dokument', 'is_current_session', 'aktuellt_riksmote');
SELECT pg_temp.byt_kolumn('riksdag_api', 'dokument', 'related_hints',      'relaterat_tips');

-- ----------------------------------------------------------
-- 3) Kolumnrenamn i riksdag_api.chunks
-- ----------------------------------------------------------
SELECT pg_temp.byt_kolumn('riksdag_api', 'chunks', 'char_start', 'tecken_start');
SELECT pg_temp.byt_kolumn('riksdag_api', 'chunks', 'char_end',   'tecken_slut');

COMMIT;

-- Efterkontroll (kör utanför transaktionen):
-- \dt riksdag_api.*       ska visa: dokument, chunks
-- \d riksdag_api.dokument ska visa kolumner: dok_id, doktyp, titel, datum,
--                          rm, status, url_riksdagen, inledning, cachad_vid,
--                          aktuellt_riksmote, relaterat_tips
-- \d riksdag_api.chunks   ska visa kolumner: id, dok_id, chunk_index, text,
--                          tecken_start, tecken_slut, embedding
