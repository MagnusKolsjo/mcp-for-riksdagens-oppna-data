# mcp-for-riksdagens-oppna-data

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
- **Hämtar debattinlägg** med fulltext (HTML-strippad)
- **Hämtar voteringsdata** per riksmöte och betänkande
- **Slår upp SFS-nummer** — hittar författningsnumret för en lag givet dess namn
- **Listar alla riksmöten** 1867–idag (1867–1975: kalenderår, 1975/76: övergångssession, 1976/77–: brutet format)

Äldre dokument (före ca 1960) och inskannat material från perioden ca 1971–1994
innehåller OCR-behandlad text. Servern flaggar dessa dokument och inkluderar
alltid en länk till PDF-originalet.

## Krav

- Python 3.11 eller senare
- PostgreSQL med pgvector-tillägget, eller SQLite med sqlite-vec
- Beroenden enligt `requirements.txt`

## Installation

Klona repot och installera beroenden:

```bash
git clone https://github.com/MagnusKolsjo/mcp-for-riksdagens-oppna-data.git
cd mcp-for-riksdagens-oppna-data
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

Databasen initieras även automatiskt vid serveruppstart.

## Konfiguration

All konfiguration sker via `.env`-filen. Kopiera `config.example.env` till `.env` och justera:

| Variabel | Standardvärde | Beskrivning |
|---|---|---|
| `RIKSDAG_API_BASE` | `https://data.riksdagen.se` | API-bas-URL |
| `RIKSDAG_PAGE_SIZE` | `20` | Träffar per API-anrop (max 100) |
| `DATABASE_URL` | *(måste sättas)* | PostgreSQL- eller SQLite-anslutning |
| `CACHE_MAX_SIZE_GB` | `2` | Maximal cachestorlek i GB (LRU-eviction vid överskridande) |
| `CACHE_TTL_AKTUELLT_RIKSMOTE_DAGAR` | `7` | Cachetid för dokument från innevarande riksmöte |
| `EMBEDDING_MODELL` | `KBLab/sentence-bert-swedish-cased` | Embeddingmodell |

**PostgreSQL** med pgvector:
```env
DATABASE_URL=postgresql://mitt_db_anvandare:byt_till_eget_starkt_losenord@localhost:5432/riksdagstryck
```

Tabellerna placeras i PostgreSQL-schemat `riksdag_api`, isolerat från andra
MCP-servrar som delar samma databasinstans. Schemat skapas automatiskt
av `db/init_db.py`.

**SQLite** med sqlite-vec:
```env
DATABASE_URL=sqlite:///riksdag_api.db
```
Avkommentera `sqlite-vec` i `requirements.txt` (se kommentaren i den filen).
SQLite-filer ger naturlig isolation — ingen schemalogik behövs.

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
| `rd_search` | Söker dokument på fritext, typ, år eller riksmöte. Returnerar `antal_traffar`, `antal_returnerade` och `traffar`-lista med `notis`, `organ` och `pdf_url`. |
| `rd_get_document` | Hämtar och cachar ett dokument med inledning och metadata |
| `rd_search_in_document` | Semantisk sökning inom ett specifikt dokument |
| `rd_get_chunk` | Hämtar ett textstycke på position, valfritt med omgivande stycken |
| `rd_get_context` | Hämtar kontextpaket: relaterade dokument grupperade per relationstyp (alltid färsk data) |
| `rd_get_anforanden` | Hämtar debattinlägg med fulltext (HTML-strippad). Inkluderar `iid`, `rel_dok_id`, `kammaraktivitet`. |
| `rd_get_voteringar` | Hämtar voteringsdata med `antal_traffar` |
| `rd_list_riksmoten` | Listar alla riksmöten 1867–idag |
| `rd_resolve_sfs` | Slår upp SFS-nummer för en lag givet dess namn |
| `rd_search_ledamoter` | Söker ledamöter på namn, parti, valkrets eller status |
| `rd_get_ledamot` | Hämtar fullständig profil med uppdragshistorik för en ledamot |
| `rd_get_ledamot_aktivitet` | Hämtar en ledamots senaste anföranden, motioner och interpellationer |

### Att läsa ett stort dokument

En proposition kan vara flera hundra sidor. `rd_get_document` returnerar därför
metadata och en **inledning** på 500 tecken — inte dokumentets text. Svaret anger
`antal_stycken` så att omfattningen framgår, och pekar ut de två vägarna vidare:

```
rd_search_in_document(dok_id, query)   # hitta det du söker
rd_get_chunk(dok_id, chunk_index)      # läs på position
```

Texten delas i stycken om 800 tecken med 200 teckens överlapp, så att ingen mening
kan falla mellan två stycken. Varje sökträff bär sin adress (`chunk_index` samt
`tecken_start`/`tecken_slut`), vilket gör att en träff kan hämtas tillbaka med
omgivning: `rd_get_chunk(dok_id, chunk_index, kontext=1)`.

**Vid ordagranna citat:** citera aldrig ur ett utdrag som är markerat `trunkerad`.
Hämta hela stycket först. Textreturnerande verktyg tar `max_tecken` och
`fran_tecken`, och ett kapat svar bär `tecken_totalt` och `fortsatt_fran_tecken`.

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

## Verifieringsskript (utvecklingsverktyg)

Två hjälpskript ingår för att utforska och verifiera API:et under utveckling.
De behövs inte för att köra servern i produktion.

```bash
python 01_explore_api.py      # Utforskar API-struktur och svarsformat
python 02_verify_coverage.py  # Verifierar täckning och API-kapabilitet
```

## Licens

[AGPL-3.0](LICENSE.md)

Riksdagens öppna data är licensierade under
[CC0](https://creativecommons.org/publicdomain/zero/1.0/) och får
användas och distribueras fritt.
