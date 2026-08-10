# Ändringslogg

Alla betydande ändringar dokumenteras här.
Formatet följer [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versionshanteringen följer [Semantic Versioning](https://semver.org/).

## [3.1.0] — 2026-08-10

### Tillagt

- **`rd_get_chunk(dok_id, chunk_index, kontext, max_tecken, fran_tecken)`** — hämtar ett
  textstycke på position i stället för på sökrelevans. Tidigare gick dokumentets text
  bara att nå via semantisk sökning, vilket gjorde att en passage som inte matchade
  någon fråga var oåtkomlig — och att ett citat inte kunde kontrolleras mot sin
  omgivning. `kontext=1` tar med grannstyckena när en mening löper över en styckegräns.
  Felmeddelandet skiljer ocachat dokument från giltigt dokument med okänt styckenummer.
- **`max_tecken`** i `rd_search_in_document` och **`max_tecken`/`fran_tecken`** i
  `rd_get_chunk`. Kapade svar bär `trunkerad`, `tecken_totalt`, `tecken_visade` och
  `fortsatt_fran_tecken`, och kapas på ordgräns.
- **`antal_stycken`, `inledning_ar_utdrag` och `las_vidare`** i `rd_get_document`.
  Fältet `inledning` är 500 tecken ur ett dokument som kan ha över tusen textstycken;
  utan de nya fälten framstod det som dokumentets innehåll.
- **`instructions`-sträng på servern** — beskriver hur stora dokument läses, hur citat
  kontrolleras och vad trunkeringsfälten betyder. Servern saknade tidigare en
  beskrivning på servernivå.
- Nya metoder `hamta_chunkar()` och `antal_chunkar()` i `document_store.py`.

### Bakgrund

Genomför projektets svarskontrakt (`00-las-forst.md` → "Svarskontraktet — storlek,
trunkering, adressering och sökning") i den här servern. Inga ändringar i databasschemat
— kolumnerna `chunk_index`, `tecken_start` och `tecken_slut` fanns redan men exponerades
bara via semantisk sökning.

**OBS vid uppgradering:** `rd_get_chunk` är ett nytt verktyg i en befintlig server.
MCP-klienter som cachelägger verktygsindexet per servernamn kan behöva ett nytt
servernamn i konfigurationen för att se det.

---

## [3.0.1] — 2026-05-22

Publicerad 2026-05-22 (commit `30d5f60`, tagg `v3.0.1`). Posten skrevs in i
efterhand 2026-08-10 — versionen taggades utan motsvarande CHANGELOG-post.

### Ändrat

- `.DS_Store` tillagd i `.gitignore`, och filen borttagen ur repots historik.
  Ingen kodändring; inga API- eller beteendeförändringar.

---

## [3.0.0] — 2026-05-21

### Brytande ändringar

**`rd_search`** returnerar nu en dict i stället för en lista:
```json
{"antal_traffar": 42, "antal_returnerade": 10, "traffar": [...]}
```
Klienter som itererade direkt över svaret måste uppdateras till att läsa `.traffar`.

Varje träff i `traffar` innehåller tre nya fält: `notis` (sammanfattning),
`organ` (handläggande organ/utskott) och `pdf_url` (länk till PDF-bilaga om sådan finns).

**`rd_get_anforanden`** returnerar nu en dict i stället för en lista:
```json
{"antal_returnerade": ..., "anforanden": [...]}
```
Varje anförande innehåller fem nya fält: `dok_id`, `anforande_nummer`, `iid`
(talarens intressent_id), `rel_dok_id` och `kammaraktivitet`. Fältet
`anforandetext` levereras nu som ren text (HTML-strippad).
Fältet `antal_traffar` finns **inte** — API:et `/anforandelista/` exponerar inte
totalantalet (se Buggfixar NB1).

**`rd_get_voteringar`** returnerar nu en dict i stället för en lista:
```json
{"antal_returnerade": ..., "voteringar": [...]}
```
Ny parameter `sz` (standard 100, max 100).
Fälten `antal_traffar` och `pagination_hint` finns **inte** — API:et
`/voteringlista/` exponerar inte totalantalet (se Buggfixar NB1/NB3).

**`rd_search_in_document`** returnerar nu en dict i stället för en lista:
```json
{"antal_returnerade": 5, "traffar": [...]}
```

**`rd_list_riksmoten`** returnerar nu en dict i stället för en lista:
```json
{"antal_returnerade": ..., "riksmoten": [...]}
```

**`rd_resolve_sfs`** returnerar nu en dict i stället för en lista:
```json
{"antal_returnerade": ..., "traffar": [...]}
```

**`rd_search_ledamoter`** returnerar nu en dict i stället för en lista:
```json
{"antal_traffar": ..., "antal_returnerade": ..., "ledamoter": [...]}
```

**`rd_get_ledamot`**: fälten `from_ar` och `tom_ar` i uppdragslistan bytta mot
`from_datum` och `tom_datum` (innehåller nu datumsträng `"ÅÅÅÅ-MM-DD HH:MM:SS"`
i stället för enbart år).

**`rd_get_document`** returnerar inte längre `relaterat_tips`. Relationsdata
hämtas alltid färsk via `rd_get_context` och ska inte läsas ur dokumentcachen.

**`rd_list_riksmoten`** genereras nu programmatiskt och täcker hela perioden
1867–idag. Formaten är: kalenderår 1867–1975, övergångssession `"1975/76"`,
brutet format `"1976/77"` och framåt. Tidigare returnerades bara de riksmöten
som råkade ingå i de senaste 200 propositionerna i API-svaret.

**Miljövariabler** med nya primärnamn (gamla namn stöds parallellt):
- `EMBEDDING_MODEL` → `EMBEDDING_MODELL`
- `CACHE_TTL_CURRENT_SESSION_DAYS` → `CACHE_TTL_AKTUELLT_RIKSMOTE_DAGAR`

### Tillagt

- LRU-eviction implementerad: kolumnen `senast_anvand` spårar senaste läsningstidpunkt
  per dokument. `_stada_cache()` i `document_store.py` tar bort 20 äldst-använda
  dokument när cachen överstiger `CACHE_MAX_SIZE_GB`.
- Idempotent schema-init anropas automatiskt vid serveruppstart (`__main__` i
  `mcp_server.py`) — servern startar även om databasen är nere vid uppstart.
- `db/init_db.py` exponerar `initiera_schema(url)` som publik funktion.

### Ändrat

- `document_store.py`: per-call-anslutningar ersätter den globala
  `self._conn`-instansen. Varje databasoperation öppnar och stänger sin
  egen anslutning — eliminerar problem med inaktuella anslutningar vid långa
  vilotider. Hjälparmetoderna `_ar_postgres()`, `_hamta_db()`, `_ph()` och
  `_prefix()` kapslar in all backend-specifik logik.
- Alla engelska funktionsnamn i `mcp_server.py` och `document_store.py`
  ersatta med svenska identifierare (Konv 10):
  - `_get_json` → `_hamta_json`, `_normalize_docs` → `_normalisera_dokument`,
    `_format_doc` → `_formatera_dokument`, `_extract_sfs_nr` → `_extrahera_sfs_nr`,
    `_normalize_persons` → `_normalisera_personer`, `_format_person` → `_formatera_person`,
    `get_store` → `_hamta_store` (mcp_server.py)
  - `get_document` → `hamta_dokument`, `search_in_document` → `sok_i_dokument`,
    `get_related` → `hamta_relaterade`, `_fetch_from_api` → `_hamta_fran_api`,
    `_fetch_dokumentstatus` → `_hamta_dokumentstatus`, `_index_and_store` → `_indexera_och_lagra`,
    `_get_model` → `_hamta_modell`, `_embed` → `_badda_in`, `_is_valid_cache` → `_giltig_cache`,
    `_fetch_cache_meta` → `_hamta_cache_metadata`, `_load_from_cache` → `_las_fran_cache`,
    `_store_postgres` → `_lagra_postgres`, `_vector_search` → `_vektor_sok`,
    `_store_sqlite` → `_lagra_sqlite`, `_chunk_text` → `_dela_text_i_chunks`,
    `_strip_html` → `_strippa_html`, `_current_riksmote` → `_aktuellt_riksmote`,
    `_build_riksdagen_url` → `_bygg_riksdagen_url` (document_store.py)
- `db/schema_postgres.sql` och `db/schema_sqlite.sql` strukturerade med explicit
  BASELINE v1.0-block (låst) och MIGRATIONER-block för framtida ändringar.
- `migrate_add_related_hints()` borttagen ur `db/init_db.py` (genomförd migration
  från v1.2.0, numera onödig).
- README.md: variabelnamnen uppdaterade, LRU-eviction dokumenterad, repo-URL korrigerad.

### Säkerhet

- Bearer-tokenvalidering använder nu `secrets.compare_digest` (konstant exekveringstid)
  i stället för `==`. Eliminerar timing-attack-möjlighet vid HTTP-transport (B3).

### Buggfixar

- **B1 — `antal_traffar` alltid 0**: varje API-endpoint använder olika JSON-nycklar
  för antalet träffar (`@traffar`, `@antal`). Alla tre berörda endpoints läser nu
  rätt nyckel.
- **B2 — OCR-varning missade 1971–1994-material**: material inskannat 1971–1994 har
  `htmlformat='skanning2007'` men `status='importerad'`. Normaliseras nu till
  `status='ocr'` vid indexering, så att cachen kan flagga utan tillgång till
  `htmlformat`. OCR-varning kontrollerar nu båda fälten.
- **B4 — SQLite LRU-loop**: SQLite-filen krymper inte efter `DELETE` utan explicit
  `VACUUM`. `_stada_cache()` kör nu `VACUUM` efter eviction, vilket stoppar loopen
  där storlekskontrollen triggade eviction om och om igen.
- **B5 — `chunk_embeddings` läcker**: `vec0`-virtuella tabellen har ingen FK-kaskad,
  vilket lät orphan-embeddings ackumuleras vid re-indexering och LRU-eviction.
  Manuell `DELETE FROM chunk_embeddings WHERE chunk_id IN (SELECT id FROM chunks
  WHERE dok_id = ?)` körs nu i båda kodvägarna.
- **B6 — dubbel-fetch av dokumentstatus**: `/dokument/{id}/text` och
  `/dokumentstatus/{id}` returnerar identisk XML. Den nya hjälpmetoden
  `_extrahera_relationer_ur_xml(root)` extraherar relationsdata ur redan-parsad XML,
  vilket eliminerar ett extra HTTP-anrop per indexering.
- **B7 — inkompatibla `_strippa_html`**: `mcp_server.py` hade en lokal regex-variant
  som inte avkodade HTML-entiteter (`&amp;`, `&lt;` m.fl.). Importerar nu
  `_strippa_html` från `document_store.py` (BeautifulSoup) i hela servern.
- **NB1/NB3 — `antal_traffar` vilseledande i `rd_get_anforanden` och
  `rd_get_voteringar`**: API:et `/anforandelista/` och `/voteringlista/` exponerar
  inte totalantalet — `@antal` är alltid lika med antal returnerade poster.
  Fältet `antal_traffar` (alltid = `antal_returnerade`) och den döda koden
  `pagination_hint` (villkoret var aldrig sant) är borttagna ur båda verktygen.
  Klienter som läser `antal_traffar` ur dessa svar måste uppdateras.
- **NB2 — OCR-varning missade söksvar för 1971–1994-material**: `_formatera_dokument`
  kontrollerade `htmlformat='skanning2007'`, men det fältet finns inte i
  `/dokumentlista/`-svar. Ny heuristik: om `status='importerad'` och riksmötet
  är före 1995 flaggas dokumentet som inskannat material. Hjälpfunktionen
  `_rm_ar_fore_1995(rm)` hanterar både kalenderårsformat och brutet format.

### Förbättringar

- **Bg2**: `rd_get_voteringar` har ny `sz`-parameter.
- **Bg4**: `SOU_HAMTNING_AKTIV=false` kontrollerar nu cachen innan HTTP-preflight,
  vilket undviker ett nätverksanrop för redan-cachade dokument.
- **Bg5**: `_stada_cache()` dokumenterar nu att rader med `senast_anvand IS NULL`
  hoppas över och att cachen kan överstiga gränsen tillfälligt efter migration.
- **K3**: `DATABASE_URL` har inget längre något default-värde varken i
  `document_store.py` eller `db/init_db.py` — ett saknat värde ger tydligt
  felmeddelande i stället för att tyst välja SQLite.
- **K5**: `migrate_to_riksdagstryck.py` (engångsskript från 2026-05-03) flyttat till
  `legacy/` för att inte förväxlas med installationsskript.
- **K7**: `_RELEVANTA_RELATIONSTYPER` i `document_store.py` har nu en tydlig varning
  om att jämförelsen är case-känslig och att stavning aldrig ska ändras utan empirisk
  verifiering mot live-API.
- **K8**: `_dela_beteckning` har ett kommentarblock om att prefixer kräver mellanslag
  (`"prop. 2024/25"` fungerar, `"prop.2024/25"` utan mellanslag faller igenom).
- **K10**: `_hamta_cache_storlek_gb` loggar nu `WARNING` vid fel i stället för att
  tyst returnera `0.0`.
- README.md: repo-URL korrigerad till `mcp-for-riksdagens-oppna-data`, databasnamnet
  korrigerat till `riksdagstryck`, licenslänk uppdaterad, OCR-beskrivning utökad
  med 1971–1994-perioden, verifieringsskript markerade som dev-verktyg (K1, K6, B2).
- `install.sh`: `riksdag_rag` ersatt med `riksdagstryck` (K2); SQLite-filnamnet i
  exempel-hint korrigerat från `riksdagstryck.db` till `riksdag_api.db` (NK3).
- `requirements.txt`: tydligare instruktion om att avkommentera `sqlite-vec` (K4).
- README.md: `DATABASE_URL`-standardvärde i konfigurationstabellen ändrat från
  `sqlite:///riksdag_api.db` till `(måste sättas)` — värdet saknades i koden
  men tabellen gav sken av ett fungerande default (NK2).
- README.md: Licenslänk korrigerad från `../LICENSE.md` till `LICENSE.md` (NK1).
- `hamta_relaterade` docstring korrigerad: XML-storleken är inte "10–50 KB" utan
  kan nå 6 MB för stora propositioner med många följdmotioner (Bg1).

### Uppgradering från v2.x

1. Kör `python db/init_db.py` för att lägga till `senast_anvand`-kolumnen.
2. Uppdatera `.env`: byt `EMBEDDING_MODEL` → `EMBEDDING_MODELL` och
   `CACHE_TTL_CURRENT_SESSION_DAYS` → `CACHE_TTL_AKTUELLT_RIKSMOTE_DAGAR`
   (gamla namn fungerar fortfarande).
3. Uppdatera klienter som läser direkt ur svaren från `rd_search`,
   `rd_get_anforanden`, `rd_get_voteringar`, `rd_search_in_document`,
   `rd_list_riksmoten`, `rd_resolve_sfs`, `rd_search_ledamoter`
   (se Brytande ändringar ovan).
4. Ta bort eventuella läsningar av `relaterat_tips` ur `rd_get_document`-svaret;
   använd `rd_get_context` i stället.
5. Uppdatera läsningar av `from_ar`/`tom_ar` i `rd_get_ledamot`-svaret till
   `from_datum`/`tom_datum`.

## [2.2.1] - 2026-05-18

### Säkerhet

- `migrate_to_riksdagstryck.py`: hårdkodade databasuppgifter ersatta med
  läsning från miljövariablerna `RIKSDAG_DB_URL_KALLA` och `RIKSDAG_DB_URL_MAL`
  via `.env` (python-dotenv). Validering tillagd så att skriptet avslutas
  med tydligt felmeddelande om variablerna saknas. Filen är ett
  engångsmigrationsskript (genomfört 2026-05-03) och påverkar inte
  MCP-serverns normala drift.

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
