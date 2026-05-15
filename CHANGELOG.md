# Ändringslogg

Alla betydande ändringar dokumenteras här.
Formatet följer [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versionshanteringen följer [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [2.2.0] — 2026-05-15

### Tillagt — SOU_SOKNING_AKTIV och SOU_HAMTNING_AKTIV

Två nya miljövariabler styr om SOU-sökning resp. SOU-hämtning/lagring
exponeras. Standard: `true` (fullt funktionell som fristående server).
Satt till `false` i installationer där `liu-sou`-servern (ström 4) hanterar
SOU för att undvika att SOU-fulltext lagras i två databaser.

**`SOU_SOKNING_AKTIV=false`** — Guard-clause i `rd_search`: om
`doktyp="sou"` och flaggan är False returneras ett felmeddelande direkt.
Generella sökningar utan explicit `doktyp` påverkas inte.

**`SOU_HAMTNING_AKTIV=false`** — Guard-clause i `rd_get_document`: ett
lättviktsanrop (`/dokumentlista/?id={dok_id}&sz=1`) kontrollerar doktypen
innan store-anropet. Om dokumentet är en SOU returneras ett felmeddelande
och indexeringen hoppas över. Om metadatakollen misslyckas faller servern
igenom till normal hämtning (fail-open).

Ingen förändring för installationer som kör med standardvärdena (`true`).

## [2.1.1] — 2026-05-15

### Ändrat

- `rd_get_anforanden`: hårdtaket på `sz` sänkt från 100 till 75 för att
  undvika MCP-protokollets ~4-minutersgräns. Vid 75 anföranden och 1–3 s
  per HTTP-anrop till riksdagens API ryms hela körningen inom ca 225 s —
  en säkerhetsmarginal på drygt 15 sekunder. Ingen förändring i
  API-gränssnittet; standardvärdet `sz=20` är oförändrat.

## [2.1.0] — 2026-05-06

### Tillagt — exakt beteckningsfiltrering i `rd_search`

`rd_search` har två nya parametrar och tre nya returfält som tillsammans
löser problemet med att hitta dokument via deras formella citationsbeteckning
(t.ex. `SOU 2025:106`, `prop. 2024/25:158`, `bet. 2024/25:FiU6`).

**Nya parametrar:**

- `beteckning` — exakt formell dokumentreferens. Splittras internt till
  `rm` + `nummer`. Accepterar både kort form (`"2025:106"`) och prefixad
  (`"SOU 2025:106"`, `"prop. 2024/25:158"`).
- `nummer` — exakt nummer/beteckning inom ett rm. Används tillsammans med
  `rm` när användaren redan har dem som separata värden. Skickas till
  riksdagens API som `bet=` för doktyp `bet` (alfanumeriska beteckningar
  som FiU6) och annars som `nr=`.

**Nya returfält i `rd_search`-svar (och allt som använder `_format_doc`,
inkl. `rd_get_ledamot_aktivitet`):**

- `beteckning` — rådata från riksdagens API (`"106"`, `"FiU6"`, …)
- `nummer` — synonym till `beteckning` (riksdagens API exponerar både)
- `referens` — formaterad citering (`"SOU 2025:106"`, `"prop. 2024/25:158"`).
  Härleds från doktyp + rm + beteckning enligt en intern prefixmapping.

**rm-formatets dokumenttypsberoende dokumenterat i docstring:**

- `prop`, `mot`, `bet`, `prot` använder riksmötesformat (`"2024/25"`)
- `sou`, `ds`, `dir` använder kalenderår (`"2025"`)

Detta var tidigare odokumenterat och en källa till misslyckade sökningar.

**Inga brytande ändringar.** De nya returfälten läggs till; befintliga
fält är oförändrade. Rena fritextsökningar (`query="..."`) fungerar exakt
som tidigare.

## [2.0.0] — 2026-05-06

### Brytande ändringar — databas och MCP-svarsformat

**Databas-rename i schemat `riksdag_api`** — kräver migration via
`db/migration_v2_0_0.sql`. Skriptet är idempotent och säkert att köra om.

Tabeller:
- `documents` → `dokument`

(Tabellen `chunks` behålls — vedertagen AI-vokabulär.)

Kolumner i `riksdag_api.dokument`:
- `cached_at` → `cachad_vid`
- `is_current_session` → `aktuellt_riksmote`
- `related_hints` → `relaterat_tips`

Kolumner i `riksdag_api.chunks`:
- `char_start` → `tecken_start`
- `char_end` → `tecken_slut`

**MCP-svarsformat** — JSON-fältnamn i verktygens svar matchar nu kolumnnamnen.
Klienter som tidigare läste `cached_at`, `related_hints`, `char_start`, `char_end`
i svaren måste uppdateras till `cachad_vid`, `relaterat_tips`, `tecken_start`,
`tecken_slut`.

**Python-identifierare** — 7 unika identifierare med å/ä/ö flyttade till
ASCII-svenska. Berörda filer: `01_explore_api.py`, `02_verify_coverage.py`,
`03_explore_dokumentstatus.py` (utforskningsskript). Bland byten:
- `hämta_dokumentstatus_xml` → `hamta_dokumentstatus_xml`
- `skriv_fält_rekursivt` → `skriv_falt_rekursivt`
- `test_fritextsökning` → `test_fritextsokning`
- `forväntat` → `forvantat`, `träffar`/`träffar2` → `traffar`/`traffar2`, `värden` → `varden`

Kärnkoden (`mcp_server.py`, `document_store.py`, `db/init_db.py`,
`migrate_to_riksdagstryck.py`) hade inga identifierare med å/ä/ö —
bara SQL-strängar och returvärden behövde uppdateras.

**MCP-tool-parametrar** — alla redan ASCII-svenska eller engelska. Stream 03
har aldrig varit drabbat av JSON Schema-hypotesen som gällde stream 09.
Den separata "rd_search saknas"-rapporten har annan orsak (utreds separat).

### Tekniskt

- Ny `db/migration_v2_0_0.sql` med PL/pgSQL-helperfunktioner
  `pg_temp.byt_tabell` och `pg_temp.byt_kolumn` (idempotent).
- `db/schema_postgres.sql` och `db/schema_sqlite.sql` uppdaterade —
  nya installationer skapas direkt med svenska namn.
- `migrate_to_riksdagstryck.py` orörd vad gäller `public.documents` —
  det är en historisk källtabell från pre-1.2.0 som inte ska renas.


## [1.2.1] - 2026-05-03

### Fixed
- `mcp_server.py`, `document_store.py`, `db/init_db.py`: Laddar nu `.env` via
  `Path(__file__).parent / '.env'` i stället för `load_dotenv()` utan argument.
  Servern hittar sin konfiguration korrekt oavsett arbetskatalog vid uppstart.
- `document_store.py`: Borttagen referens till den gamla databasen `riksdag_rag`
  i docstring-exempel; uppdaterad till `riksdagstryck`.
- `config.example.env`: SQLite-fallback döpt om från `riksdag_rag.db` till
  `riksdag_api.db` för konsekvens med PostgreSQL-schemats namn.

## [1.2.0] — 2026-05-03

### Ändrat
- PostgreSQL-tabeller flyttade till schema `riksdag_api` — isolerar tabellerna
  från andra arbetsströmmar som delar samma PostgreSQL-instans. SQLite-kodvägar
  är oförändrade. Migration av befintlig databas: kör `db/init_db.py` (skapar
  schema och tabeller om de saknas) och flytta ev. data manuellt med
  `INSERT INTO riksdag_api.documents SELECT * FROM documents`.
- `_RELEVANTA_RELATIONSTYPER` i `document_store.py` korrigerad baserat på
  empirisk verifiering mot live-API:
  - `svar` och `interpellationssvar` borttagna (existerar inte som referenstyper)
  - `fraga` rättad till `fråga` (felstavad)
  - `frågesvar` tillagd (skriftlig fråga → formellt svar, fr → frs)
  - `ipsvarid` tillagd (interpellation → kammarprotokoll med svar)
  - `GemensamtBesvarad` och `GemensamtSvar` tillagda (gemensamt besvarade IP:n)
- Docstring för `rd_get_context` uppdaterad med korrekt beskrivning av
  interpellationssvar (muntligt i kammaren, inte separat dokument)

### Tillagt
- Stöd för HTTP-transport via `MCP_TRANSPORT=http` i `.env`
- Bearer-token-autentisering via `MCP_API_KEY` i HTTP-läget
- Embedding-modellen preladdas vid uppstart i HTTP-läget
- `starlette` och `uvicorn` tillagda i `requirements.txt`
- Transportkonfiguration tillagd i `config.example.env`
- SPDX-licensrubrik tillagd i `mcp_server.py`

## [1.1.0] — 2026-05-02

### Tillagt
- Ledamötsverktyg: `rd_search_ledamoter`, `rd_get_ledamot`,
  `rd_get_ledamot_aktivitet`
- `rd_get_context` med relationsdata från dokumentstatus-API
- `related_hints` i `rd_get_document` — lista av relaterade dokument

## [1.0.0] — 2026-05-02

### Tillagt
- Initial release med 11 MCP-verktyg:
  `rd_search`, `rd_get_document`, `rd_search_in_document`, `rd_get_context`,
  `rd_get_anforanden`, `rd_get_voteringar`, `rd_list_riksmoten`,
  `rd_resolve_sfs`, `rd_search_ledamoter`, `rd_get_ledamot`,
  `rd_get_ledamot_aktivitet`
- RAG-arkitektur med PostgreSQL+pgvector och SQLite+sqlite-vec
- Semantisk sökning med KBLab/sentence-bert-swedish-cased
- Tiered TTL-cache: permanent för historiska dokument, 7 dagar för
  innevarande riksmöte
