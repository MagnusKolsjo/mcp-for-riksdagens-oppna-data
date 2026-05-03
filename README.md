# riksdag-oppna-data-mcp

En MCP-server som ger MCP-kompatibla AI-verktyg tillgång till riksdagens öppna data API,
med täckning av propositioner, motioner, betänkanden, protokoll, debattinlägg,
voteringar och riksdagsledamöter från 1867 fram till idag.

Dokument indexeras lokalt med semantiska embeddings, vilket möjliggör träffsäker
sökning inom enskilda dokument oavsett deras längd.

## Vad verktyget gör

- **Söker** riksdagsdokument på fritext, dokumenttyp, år eller riksmöte
- **Hämtar och cachar** enskilda dokument med semantisk chunkning och embedding
- **Söker inom dokument** — hittar relevanta stycken i en proposition på 400+ sidor
- **Hämtar kontextpaket** — relaterade dokument (följdmotioner, betänkande, protokoll) direkt från riksdagens dokumentstatusdata
- **Söker ledamöter** på namn, parti, valkrets eller status
- **Hämtar ledamötsprofiler** med uppdragshistorik
- **Hämtar debattinlägg** med fulltext
- **Hämtar voteringsdata** per riksmöte och betänkande
- **Slår upp SFS-nummer** — hittar författningsnumret för en lag givet dess namn

Äldre dokument (före ca 1960) innehåller OCR-skannad text. Servern flaggar
dessa och inkluderar alltid en länk till PDF-originalet.

## Krav

- Python 3.11 eller senare
- PostgreSQL med pgvector-tillägget (rekommenderas), eller SQLite med sqlite-vec
- Beroenden enligt `requirements.txt`

## Installation

Klona repot och installera beroenden:

```bash
git clone https://github.com/your-username/riksdag-oppna-data-mcp.git
cd riksdag-oppna-data-mcp
pip install -r requirements.txt
```

Kopiera konfigurationsmallen och fyll i dina värden:

```bash
cp config.example.env .env
```

Initiera databasen:

```bash
python db/init_db.py
```

## Konfiguration

All konfiguration sker via `.env`-filen. Kopiera `config.example.env` till `.env` och justera:

| Variabel | Standardvärde | Beskrivning |
|---|---|---|
| `RIKSDAG_API_BASE` | `https://data.riksdagen.se` | API-bas-URL |
| `RIKSDAG_PAGE_SIZE` | `20` | Träffar per API-anrop (max 100) |
| `DATABASE_URL` | `sqlite:///riksdag_rag.db` | PostgreSQL- eller SQLite-anslutning |
| `CACHE_MAX_SIZE_GB` | `2` | Maximal cachestorlek i GB |
| `CACHE_TTL_CURRENT_SESSION_DAYS` | `7` | Cachetid för dokument från innevarande riksmöte |
| `EMBEDDING_MODEL` | `KBLab/sentence-bert-swedish-cased` | Embeddingmodell |

**PostgreSQL** (rekommenderas):
```env
DATABASE_URL=postgresql://mitt_db_anvandare:byt_till_eget_starkt_losenord@localhost:5432/riksdag
```

Tabellerna placeras i PostgreSQL-schemat `riksdag_api`, isolerat från andra
arbetsströmmar som delar samma databasinstans. Schemat skapas automatiskt
av `db/init_db.py`.

**SQLite** (ingen serverinstallation krävs):
```env
DATABASE_URL=sqlite:///riksdag_rag.db
```
Avkommentera även `sqlite-vec` i `requirements.txt`. SQLite-filer ger
naturlig isolation — ingen schemalogik behövs.

## Konfiguration av MCP-klient

Servern fungerar med alla AI-verktyg som stöder MCP-protokollet.
Nedan visas ett konfigurationsexempel för Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "riksdag": {
      "command": "python",
      "args": ["/absolut/stig/till/mcp_server.py"]
    }
  }
}
```

Starta om MCP-klienten så ansluter den till servern automatiskt.

## Tillgängliga verktyg

| Verktyg | Beskrivning |
|---|---|
| `rd_search` | Söker dokument på fritext, typ, år eller riksmöte |
| `rd_get_document` | Hämtar och cachar ett dokument med inledning och metadata |
| `rd_search_in_document` | Semantisk sökning inom ett specifikt dokument |
| `rd_get_context` | Hämtar kontextpaket: relaterade dokument grupperade per relationstyp |
| `rd_get_anforanden` | Hämtar debattinlägg med fulltext |
| `rd_get_voteringar` | Hämtar voteringsdata |
| `rd_list_riksmoten` | Listar tillgängliga riksmöten |
| `rd_resolve_sfs` | Slår upp SFS-nummer för en lag givet dess namn |
| `rd_search_ledamoter` | Söker ledamöter på namn, parti, valkrets eller status |
| `rd_get_ledamot` | Hämtar fullständig profil med uppdragshistorik för en ledamot |
| `rd_get_ledamot_aktivitet` | Hämtar en ledamots senaste anföranden, motioner och interpellationer |

## Dokumenttyper

| Kod | Typ | Täckning |
|---|---|---|
| `prop` | Propositioner | 1867–idag |
| `mot` | Motioner | 1867–idag |
| `bet` | Betänkanden | 1867–idag |
| `prot` | Protokoll | 1867–idag |
| `sou` | Statens offentliga utredningar (SOU) | 1867–idag |
| `ds` | Departementsserien (Ds) | 1986–idag |
| `dir` | Kommittédirektiv | 1834–idag |

## Verifieringsskript

Två hjälpskript ingår för att utforska och verifiera API:et:

```bash
python 01_explore_api.py      # Utforskar API-struktur och svarsformat
python 02_verify_coverage.py  # Verifierar täckning och API-kapabilitet
```

## Licens

[AGPLv3](../LICENSE)

Riksdagens öppna data är licensierade under
[CC0](https://creativecommons.org/publicdomain/zero/1.0/) och får
användas och distribueras fritt.
