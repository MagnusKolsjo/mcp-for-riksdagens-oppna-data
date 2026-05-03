# Ändringslogg

Alla betydande ändringar dokumenteras här.
Formatet följer [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versionshanteringen följer [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
