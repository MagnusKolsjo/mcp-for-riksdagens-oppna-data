"""
document_store.py — Cachalager och semantisk sökning för riksdagsdokument

Hanterar hämtning av dokument från riksdagens API, HTML-strippning,
chunkning, embedding-generering och lagring i lokal databas.

Välj backend via DATABASE_URL i .env:
    postgresql://user:password@localhost:5432/riksdagstryck
    sqlite:///riksdag_api.db

Krav (PostgreSQL): psycopg2-binary, sentence-transformers, beautifulsoup4, httpx
Krav (SQLite):     sqlite-vec, sentence-transformers, beautifulsoup4, httpx

Användning:
    from document_store import DocumentStore
    store = DocumentStore()
    doc   = store.hamta_dokument("C09C516")
    hits  = store.sok_i_dokument("C09C516", "torghandelns frihet")
    ctx   = store.hamta_relaterade("HD03158")
"""

import json
import logging
import os
import re
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env')

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

API_BASE     = os.getenv("RIKSDAG_API_BASE", "https://data.riksdagen.se")
# DATABASE_URL utan default -- ett odefinierat val ger tydligt RuntimeError i _hamta_db.
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Projektidentifierande UA — samma sträng som mcp_server.py använder.
HEADERS = {
    "User-Agent": "mcp-for-riksdagens-oppna-data/1.0 (+https://github.com/MagnusKolsjo/mcp-for-riksdagens-oppna-data)",
}

# EMBEDDING_MODELL är det aktuella variabelnamnet.
# EMBEDDING_MODEL accepteras för bakåtkompatibilitet under migrering.
EMBEDDING_MODELL = os.getenv("EMBEDDING_MODELL", os.getenv("EMBEDDING_MODEL", "KBLab/sentence-bert-swedish-cased"))

CACHE_MAX_GB    = int(os.getenv("CACHE_MAX_SIZE_GB", 2))

# CACHE_TTL_AKTUELLT_RIKSMOTE_DAGAR är det aktuella variabelnamnet.
# CACHE_TTL_CURRENT_SESSION_DAYS accepteras för bakåtkompatibilitet under migrering.
CACHE_TTL_DAGAR = int(os.getenv("CACHE_TTL_AKTUELLT_RIKSMOTE_DAGAR",
                                os.getenv("CACHE_TTL_CURRENT_SESSION_DAYS", 7)))

CHUNK_STORLEK   = 800   # tecken per chunk
CHUNK_OVERLAPP  = 200   # överlapp mellan chunk i och i+1
EMBED_DIM       = 768   # KBLab/sentence-bert-swedish-cased → 768 dimensioner
INLEDNING_LEN   = 500   # tecken som returneras som förhandsvisning

# URL-segment på riksdagen.se per doktyp-kod
_URL_SEGMENT = {
    "prop": "proposition",
    "mot":  "motion",
    "bet":  "betankande",
    "prot": "protokoll",
    "sou":  "statens-offentliga-utredningar",
    "dir":  "kommittedirektiv",
    "ds":   "departementsserien",
}

# Relationstyper från dokumentstatus XML som är meningsfulla för dokumentförståelse.
# Typer som utesluts: föredragningslista, talarlista (administrativa dokument).
#
# Verifierade relationstyper (empiriskt kontrollerade mot live-API 2026-05-03):
#   behandlas_i, följdmotion, behandlar, protokollbeslut, protokolldebatt, beslut_id
#       → förekommer på prop/mot/bet-dokument
#   frågesvar  → förekommer på fr (skriftlig fråga); pekar på frs-dokument (formellt svar)
#   fråga      → förekommer på frs (frågesvar); pekar tillbaka på fr-dokument
#   GemensamtBesvarad, GemensamtSvar
#       → förekommer på ip (interpellation); pekar på andra ip som besvarats gemensamt
#
# OBS: Svaret på en interpellation ges muntligen i kammaren (kammarprotokoll),
# inte som ett separat dokument. Separata svars-dokument (doktyp ipv) existerar inte.
# Relationstypen "svar" och "interpellationssvar" förekommer inte i API:et.
#
# VIKTIGT: Jämförelsen är case-känslig och måste matcha API:ets exakta skiftläge.
# Ändra aldrig stavning utan ny empirisk verifiering mot live-API — en felaktig
# stavning ger tysta bortfall av relationer (inga fel, bara tomma svar).
_RELEVANTA_RELATIONSTYPER = {
    "behandlas_i",        # prop/mot → bet som behandlar dokumentet
    "följdmotion",        # prop → motioner som följer på propositionen
    "behandlar",          # bet → prop/mot som betänkandet behandlar
    "protokollbeslut",    # bet → prot med riksdagsbeslut
    "protokolldebatt",    # bet → prot med kammardebatten
    "beslut_id",          # bet → voteringsprotokoll
    "frågesvar",          # fr → frs: formellt skriftligt svar på skriftlig fråga
    "fråga",              # frs → fr: tillbaka till den ursprungliga skriftliga frågan
    "ipsvarid",           # ip → prot: protokoll där interpellationen besvarades i kammaren
    "GemensamtBesvarad",  # ip → ip: andra interpellationer besvarade gemensamt
    "GemensamtSvar",      # ip → ip: gemensamt svar på flera interpellationer
}


def _bygg_riksdagen_url(dok_id: str, doktyp: str) -> str:
    """Bygger URL till riksdagen.se för ett dokument."""
    seg = _URL_SEGMENT.get(doktyp.lower(), doktyp.lower())
    return f"https://www.riksdagen.se/sv/dokument-och-lagar/dokument/{seg}/_{dok_id}/"


def _strippa_html(html: str) -> str:
    """Strippar HTML-taggar och returnerar ren text."""
    soup = BeautifulSoup(html, "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(separator=" ")).strip()


def _dela_text_i_chunks(text: str) -> list[dict]:
    """Delar text i överlappande stycken för RAG-indexering."""
    chunks = []
    start  = 0
    while start < len(text):
        end = start + CHUNK_STORLEK
        chunks.append({
            "text":        text[start:end],
            "tecken_start": start,
            "tecken_slut":  min(end, len(text)),
        })
        if end >= len(text):
            break
        start += CHUNK_STORLEK - CHUNK_OVERLAPP
    return chunks


def _aktuellt_riksmote() -> str:
    """Returnerar innevarande riksmöte på formen ÅÅÅÅ/ÅÅ, t.ex. '2024/25'."""
    today = date.today()
    if today.month >= 9:
        return f"{today.year}/{str(today.year + 1)[-2:]}"
    return f"{today.year - 1}/{str(today.year)[-2:]}"


def _hamta_xml_text(element: ET.Element, tag: str) -> str:
    """Hämtar text från ett namngivet child-element, eller tom sträng."""
    el = element.find(tag)
    if el is not None and el.text:
        return el.text.strip()
    return ""


# ---------------------------------------------------------------------------
# DocumentStore
# ---------------------------------------------------------------------------

class DocumentStore:
    """
    Hanterar hämtning, indexering och semantisk sökning av riksdagsdokument.

    Väljer backend via DATABASE_URL i .env:
        postgresql://user:password@localhost:5432/riksdagstryck
        sqlite:///riksdag_api.db

    Varje databasoperation öppnar och stänger sin egen anslutning (per-call-mönster).
    """

    def __init__(self) -> None:
        self._model: Optional[object] = None

    # ------------------------------------------------------------------
    # Backend-hjälpare (per-call-mönster)
    # ------------------------------------------------------------------

    def _ar_postgres(self) -> bool:
        """Returnerar True om DATABASE_URL pekar på PostgreSQL."""
        return DATABASE_URL.startswith("postgresql://") or DATABASE_URL.startswith("postgres://")

    def _hamta_db(self):
        """Öppnar och returnerar en ny databasanslutning (stäng med conn.close())."""
        if not DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL saknas. Ange anslutningsstrang i .env:\n"
                "  postgresql://anvandare:losenord@localhost:5432/riksdagstryck\n"
                "  sqlite:///riksdag_api.db"
            )
        if self._ar_postgres():
            import psycopg2
            return psycopg2.connect(DATABASE_URL)
        else:
            import sqlite_vec
            db_path = DATABASE_URL.replace("sqlite:///", "")
            conn    = sqlite3.connect(db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            sqlite_vec.load(conn)
            return conn

    def _ph(self) -> str:
        """Platshållare för parameteriserade frågor: %s (Postgres) eller ? (SQLite)."""
        return "%s" if self._ar_postgres() else "?"

    def _prefix(self) -> str:
        """Tabellprefix: 'riksdag_api.' för Postgres, '' för SQLite."""
        return "riksdag_api." if self._ar_postgres() else ""

    # ------------------------------------------------------------------
    # Publika metoder
    # ------------------------------------------------------------------

    def hamta_dokument(self, dok_id: str) -> dict:
        """
        Returnerar metadata och inledning för ett dokument.

        Om dokumentet finns i cache och cachen är giltig returneras det direkt.
        Annars hämtas dokumentet från riksdagens API, indexeras och cachas.

        Returnerar dict med nycklarna:
            dok_id, doktyp, titel, datum, rm, status,
            url_riksdagen, inledning, cached, ocr_warning

        För relationsdata: använd hamta_relaterade — relationsdata hämtas alltid färsk.
        """
        if self._giltig_cache(dok_id):
            return self._las_fran_cache(dok_id)

        raw = self._hamta_fran_api(dok_id)
        self._indexera_och_lagra(raw)
        return self._las_fran_cache(dok_id)

    def sok_i_dokument(
        self,
        dok_id: str,
        query: str,
        top_k: int = 5,
    ) -> list[dict]:
        """
        Semantisk sökning inom ett specifikt dokument.

        Säkerställer att dokumentet är cachat, bäddar in frågan och returnerar
        de top_k mest relevanta styckena med position och kontext.

        Returnerar lista av dict med nycklarna:
            chunk_index, text, tecken_start, tecken_slut, score
        """
        self.hamta_dokument(dok_id)
        query_vec = self._badda_in([query])[0]
        return self._vektor_sok(dok_id, query_vec, top_k)

    def hamta_chunkar(
        self,
        dok_id: str,
        fran_index: int,
        till_index: int,
    ) -> list[dict]:
        """
        Hämtar textstycken ur ett cachat dokument på styckenummer.

        Motsvarigheten till sok_i_dokument när man vet var i dokumentet man vill
        läsa i stället för vad man söker efter — nödvändig för att kunna
        kontrollera ett ordagrant citat och för att läsa vidare förbi en träff.

        Returnerar lista av dict med nycklarna:
            chunk_index, text, tecken_start, tecken_slut
        Tom lista om dokumentet saknas eller intervallet ligger utanför.
        """
        self.hamta_dokument(dok_id)   # säkerställ att dokumentet är cachat

        ph     = self._ph()
        prefix = self._prefix()
        sql = (
            f"SELECT chunk_index, text, tecken_start, tecken_slut "
            f"FROM {prefix}chunks "
            f"WHERE dok_id = {ph} AND chunk_index BETWEEN {ph} AND {ph} "
            f"ORDER BY chunk_index"
        )
        conn = self._hamta_db()
        try:
            cur = conn.cursor()
            cur.execute(sql, (dok_id, fran_index, till_index))
            rader = cur.fetchall()
        finally:
            conn.close()

        return [
            {
                "chunk_index":  r[0],
                "text":         r[1],
                "tecken_start": r[2],
                "tecken_slut":  r[3],
            }
            for r in rader
        ]

    def antal_chunkar(self, dok_id: str) -> int:
        """Antal textstycken som dokumentet delats i. 0 om det inte är cachat."""
        ph     = self._ph()
        prefix = self._prefix()
        conn = self._hamta_db()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT count(*) FROM {prefix}chunks WHERE dok_id = {ph}",
                (dok_id,),
            )
            rad = cur.fetchone()
        finally:
            conn.close()
        return int(rad[0]) if rad else 0

    def hamta_relaterade(self, dok_id: str) -> dict:
        """
        Returnerar ett kontextpaket för ett dokument med alla relaterade dokument
        grupperade efter relationstyp.

        Hämtar alltid färsk data från dokumentstatus-endpointen för att fånga
        relationer som tillkommit sedan dokumentet cachades (t.ex. svar på
        interpellationer, nya följdmotioner). XML-storleken varierar kraftigt:
        enkla motioner kan vara ~10–50 KB, stora propositioner med många
        följdmotioner kan nå upp till 6 MB.

        Returnerar dict med:
            dok_id, doktyp, titel, rm,
            relaterade: dict grupperat per relationstyp,
            extra: motgrund, motkat, mottagare, besvaradav, stalldtill
        """
        # Hämta dokumentmetadata (för titel, doktyp etc.)
        doc = self.hamta_dokument(dok_id)

        # Hämta alltid färsk relationsdata — dokumentstatus är lättviktig
        status = self._hamta_dokumentstatus(dok_id)

        # Gruppera relationer per typ
        grouped: dict = {}
        for rel in status["relations"]:
            rtyp = rel["referenstyp"]
            grouped.setdefault(rtyp, []).append({
                "dok_id":   rel["dok_id"],
                "doktyp":   rel["doktyp"],
                "titel":    rel["titel"],
                "subtitel": rel.get("subtitel", ""),
                "url":      _bygg_riksdagen_url(rel["dok_id"], rel["doktyp"]),
            })

        return {
            "dok_id":     dok_id,
            "doktyp":     doc.get("doktyp", ""),
            "titel":      doc.get("titel", ""),
            "rm":         doc.get("rm", ""),
            "relaterade": grouped,
            "extra":      status["extra"],
        }

    # ------------------------------------------------------------------
    # Hämtning från API
    # ------------------------------------------------------------------

    def _hamta_fran_api(self, dok_id: str) -> dict:
        """
        Hämtar dokument och dokumentstatus från riksdagens API.
        Returnerar dict med metadata, ren text och relationsdata för lagring.
        """
        # Fulltext (XML med inbäddad HTML)
        text_url = f"{API_BASE}/dokument/{dok_id}/text"
        r = httpx.get(text_url, headers=HEADERS, timeout=60)
        r.raise_for_status()

        root = ET.fromstring(r.text)
        fields = {}
        for child in root:
            for sub in child:
                if sub.text:
                    fields[sub.tag] = sub.text.strip()

        html_content = fields.get("html", "")
        plain_text   = _strippa_html(html_content) if html_content else ""
        doktyp       = fields.get("doktyp", "").lower()

        # Normalisera OCR-status: 1971-1994-material har htmlformat='skanning2007'
        # men status='importerad'. Lagra alltid 'ocr' for inskannat material
        # så att cachen kan flagga det utan att ha tillgång till htmlformat.
        raw_status = fields.get("status")
        htmlformat = fields.get("htmlformat", "")
        if raw_status == "ocr" or htmlformat == "skanning2007":
            lagrad_status = "ocr"
        else:
            lagrad_status = raw_status

        # Extrahera relationsdata ur den redan-parsade XML:en (undviker dubbel-fetch).
        # /dokument/{id}/text och /dokumentstatus/{id} returnerar identisk XML
        # for moderna dokument -- verifierat 2026-05-21.
        try:
            status_intern  = self._extrahera_relationer_ur_xml(root)
            relaterat_tips = status_intern["relations"]
        except Exception:
            relaterat_tips = []

        return {
            "dok_id":         dok_id,
            "doktyp":         doktyp,
            "titel":          fields.get("titel", ""),
            "datum":          fields.get("datum", ""),
            "rm":             fields.get("rm", ""),
            "status":         lagrad_status,
            "url_riksdagen":  _bygg_riksdagen_url(dok_id, doktyp),
            "inledning":      plain_text[:INLEDNING_LEN],
            "plain_text":     plain_text,
            "relaterat_tips": json.dumps(relaterat_tips, ensure_ascii=False),
        }

    def _extrahera_relationer_ur_xml(self, root: ET.Element) -> dict:
        """
        Extraherar relationer och extra kontext ur en redan-parsad dokumentstatus XML.

        Delar parselogik med _hamta_dokumentstatus — separerad för att möjliggöra
        återanvändning av redan-hämtad XML i _hamta_fran_api (undviker dubbel-fetch).

        Returnerar dict med:
            relations: lista av dicts med referenstyp, dok_id, doktyp, titel, subtitel
            extra:     dict med motgrund, motkat, mottagare, besvaradav, stalldtill
        """
        relations = []
        for ref in root.iter("referens"):
            referenstyp = _hamta_xml_text(ref, "referenstyp")
            if referenstyp not in _RELEVANTA_RELATIONSTYPER:
                continue
            ref_dok_id = _hamta_xml_text(ref, "ref_dok_id")
            if not ref_dok_id:
                continue
            relations.append({
                "referenstyp": referenstyp,
                "dok_id":      ref_dok_id,
                "doktyp":      _hamta_xml_text(ref, "ref_dok_typ"),
                "titel":       _hamta_xml_text(ref, "ref_dok_titel"),
                "subtitel":    _hamta_xml_text(ref, "ref_dok_subtitel"),
            })

        extra: dict = {}
        for uppgift in root.iter("uppgift"):
            kod  = _hamta_xml_text(uppgift, "kod")
            text = _hamta_xml_text(uppgift, "text")
            if kod in ("motgrund", "motkat") and text:
                extra[kod] = text

        dok_el = root.find("dokument")
        if dok_el is not None:
            mottagare = _hamta_xml_text(dok_el, "mottagare")
            if mottagare:
                extra["mottagare"] = mottagare

        for intressent in root.iter("intressent"):
            roll = _hamta_xml_text(intressent, "roll")
            namn = _hamta_xml_text(intressent, "namn")
            if roll in ("besvaradav", "stalldtill") and namn:
                extra[roll] = namn

        return {"relations": relations, "extra": extra}

    def _hamta_dokumentstatus(self, dok_id: str) -> dict:
        """
        Hämtar dokumentstatus XML och extraherar relationer och extra kontext.

        Används av hamta_relaterade för att alltid hämta färsk relationsdata.
        OBS: /dokumentstatus/{id} och /dokument/{id}/text returnerar identisk XML
        för moderna dokument (verifierat 2026-05-21). _hamta_fran_api återanvänder
        därför den redan-parsade XML:en via _extrahera_relationer_ur_xml för att
        undvika ett extra HTTP-anrop per indexering.

        Returnerar dict med:
            relations: lista av dicts med referenstyp, dok_id, doktyp, titel, subtitel
            extra:     dict med motgrund, motkat, mottagare, besvaradav, stalldtill
        """
        url = f"{API_BASE}/dokumentstatus/{dok_id}"
        r   = httpx.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        root = ET.fromstring(r.text)
        return self._extrahera_relationer_ur_xml(root)

    # ------------------------------------------------------------------
    # Indexering
    # ------------------------------------------------------------------

    def _indexera_och_lagra(self, raw: dict) -> None:
        """Chunkar, embeddar och lagrar ett hämtat dokument. Kör LRU-städning efteråt."""
        chunks = _dela_text_i_chunks(raw["plain_text"])
        if not chunks:
            return

        texts      = [c["text"] for c in chunks]
        embeddings = self._badda_in(texts)

        ar_aktuellt = raw.get("rm") == _aktuellt_riksmote()

        if self._ar_postgres():
            self._lagra_postgres(raw, chunks, embeddings, ar_aktuellt)
        else:
            self._lagra_sqlite(raw, chunks, embeddings, ar_aktuellt)

        # LRU-eviction om cachen blivit för stor
        self._stada_cache()

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def _hamta_modell(self):
        """Lazy-laddar embeddingmodellen vid första anrop."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            print(
                f"Laddar embeddingmodell: {EMBEDDING_MODELL} (tar ~30 s första gången)...",
                file=sys.stderr
            )
            self._model = SentenceTransformer(EMBEDDING_MODELL)
        return self._model

    def _badda_in(self, texts: list[str]) -> list:
        """Returnerar lista av numpy-vektorer för given lista av texter."""
        model = self._hamta_modell()
        return model.encode(texts, show_progress_bar=False, normalize_embeddings=True)

    # ------------------------------------------------------------------
    # Cache-validering
    # ------------------------------------------------------------------

    def _giltig_cache(self, dok_id: str) -> bool:
        """Returnerar True om dokumentet finns i cachen och inte är utgånget."""
        row = self._hamta_cache_metadata(dok_id)
        if row is None:
            return False
        is_current, cachad_vid = row[0], row[1]
        if not is_current:
            return True   # historiska dokument cachas permanent

        if self._ar_postgres():
            age_days = (datetime.now(timezone.utc) - cachad_vid).days
        else:
            age_days = (time.time() - cachad_vid) / 86_400
        return age_days < CACHE_TTL_DAGAR

    # ------------------------------------------------------------------
    # Databasoperationer — läs/skriv
    # ------------------------------------------------------------------

    def _hamta_cache_metadata(self, dok_id: str):
        """Hämtar (aktuellt_riksmote, cachad_vid) ur cachen, eller None."""
        ph     = self._ph()
        prefix = self._prefix()
        conn   = self._hamta_db()
        try:
            if self._ar_postgres():
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT aktuellt_riksmote, cachad_vid "
                        f"FROM {prefix}dokument WHERE dok_id = {ph}",
                        (dok_id,)
                    )
                    return cur.fetchone()
            else:
                cur = conn.execute(
                    f"SELECT aktuellt_riksmote, cachad_vid "
                    f"FROM {prefix}dokument WHERE dok_id = {ph}",
                    (dok_id,)
                )
                return cur.fetchone()
        finally:
            conn.close()

    def _las_fran_cache(self, dok_id: str) -> dict:
        """Läser dokumentmetadata ur cachen och returnerar som dict."""
        # relaterat_tips läses inte — relationsdata är alltid färsk via hamta_relaterade.
        cols   = "dok_id, doktyp, titel, datum, rm, status, url_riksdagen, inledning"
        ph     = self._ph()
        prefix = self._prefix()
        conn   = self._hamta_db()
        try:
            if self._ar_postgres():
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT {cols} FROM {prefix}dokument WHERE dok_id = {ph}",
                        (dok_id,)
                    )
                    row = cur.fetchone()
            else:
                cur = conn.execute(
                    f"SELECT {cols} FROM {prefix}dokument WHERE dok_id = {ph}",
                    (dok_id,)
                )
                row = cur.fetchone()
        finally:
            conn.close()

        if row is None:
            raise ValueError(f"Dokument {dok_id!r} saknas i cachen trots förväntat närvaro.")

        keys   = ["dok_id", "doktyp", "titel", "datum", "rm", "status", "url_riksdagen", "inledning"]
        result = dict(zip(keys, row))
        result["cached"]      = True
        result["ocr_warning"] = result.get("status") == "ocr"

        # Uppdatera senast_anvand för LRU-tracking (icke-kritisk, tystas vid fel)
        self._uppdatera_senast_anvand(dok_id)

        return result

    def _lagra_postgres(self, raw: dict, chunks: list, embeddings, ar_aktuellt: bool) -> None:
        """Lagrar dokument, chunks och embeddings i PostgreSQL."""
        import psycopg2.extras
        conn = self._hamta_db()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO riksdag_api.dokument
                        (dok_id, doktyp, titel, datum, rm, status,
                         url_riksdagen, inledning, relaterat_tips,
                         cachad_vid, aktuellt_riksmote, senast_anvand)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, NOW())
                    ON CONFLICT (dok_id) DO UPDATE SET
                        cachad_vid        = EXCLUDED.cachad_vid,
                        aktuellt_riksmote = EXCLUDED.aktuellt_riksmote,
                        inledning         = EXCLUDED.inledning,
                        relaterat_tips    = EXCLUDED.relaterat_tips,
                        senast_anvand     = EXCLUDED.senast_anvand
                """, (
                    raw["dok_id"], raw["doktyp"], raw["titel"], raw["datum"],
                    raw["rm"], raw["status"], raw["url_riksdagen"],
                    raw["inledning"], raw.get("relaterat_tips"), ar_aktuellt,
                ))

                cur.execute(
                    "DELETE FROM riksdag_api.chunks WHERE dok_id = %s",
                    (raw["dok_id"],)
                )

                psycopg2.extras.execute_values(cur, """
                    INSERT INTO riksdag_api.chunks
                        (dok_id, chunk_index, text, tecken_start, tecken_slut, embedding)
                    VALUES %s
                """, [
                    (raw["dok_id"], i, c["text"], c["tecken_start"], c["tecken_slut"],
                     embeddings[i].tolist())
                    for i, c in enumerate(chunks)
                ])
            conn.commit()
        finally:
            conn.close()

    def _lagra_sqlite(self, raw: dict, chunks: list, embeddings, ar_aktuellt: bool) -> None:
        """Lagrar dokument, chunks och embeddings i SQLite."""
        conn = self._hamta_db()
        now  = int(time.time())
        try:
            conn.execute("""
                INSERT OR REPLACE INTO dokument
                    (dok_id, doktyp, titel, datum, rm, status,
                     url_riksdagen, inledning, relaterat_tips,
                     cachad_vid, aktuellt_riksmote, senast_anvand)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                raw["dok_id"], raw["doktyp"], raw["titel"], raw["datum"],
                raw["rm"], raw["status"], raw["url_riksdagen"],
                raw["inledning"], raw.get("relaterat_tips"), now, int(ar_aktuellt), now,
            ))

            # Ta bort orphan-embeddings innan chunks raderas (vec0-tabellen har ingen FK).
            conn.execute(
                "DELETE FROM chunk_embeddings WHERE chunk_id IN "
                "(SELECT id FROM chunks WHERE dok_id = ?)",
                (raw["dok_id"],)
            )
            conn.execute("DELETE FROM chunks WHERE dok_id = ?", (raw["dok_id"],))

            for i, c in enumerate(chunks):
                conn.execute("""
                    INSERT INTO chunks (dok_id, chunk_index, text, tecken_start, tecken_slut)
                    VALUES (?, ?, ?, ?, ?)
                """, (raw["dok_id"], i, c["text"], c["tecken_start"], c["tecken_slut"]))
                chunk_id  = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                vec_bytes = embeddings[i].astype("float32").tobytes()
                conn.execute(
                    "INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (?, ?)",
                    (chunk_id, vec_bytes)
                )
            conn.commit()
        finally:
            conn.close()

    def _vektor_sok(self, dok_id: str, query_vec, top_k: int) -> list[dict]:
        """Utför vektorsökning och returnerar de top_k mest relevanta chunks."""
        conn = self._hamta_db()
        try:
            if self._ar_postgres():
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT chunk_index, text, tecken_start, tecken_slut,
                               1 - (embedding <=> %s::vector) AS score
                        FROM   riksdag_api.chunks
                        WHERE  dok_id = %s
                        ORDER  BY embedding <=> %s::vector
                        LIMIT  %s
                    """, (query_vec.tolist(), dok_id, query_vec.tolist(), top_k))
                    rows = cur.fetchall()
            else:
                vec_bytes = query_vec.astype("float32").tobytes()
                rows = conn.execute("""
                    SELECT c.chunk_index, c.text, c.tecken_start, c.tecken_slut,
                           1 - vec_distance_cosine(ce.embedding, ?) AS score
                    FROM   chunk_embeddings ce
                    JOIN   chunks c ON c.id = ce.chunk_id
                    WHERE  c.dok_id = ?
                    ORDER  BY vec_distance_cosine(ce.embedding, ?)
                    LIMIT  ?
                """, (vec_bytes, dok_id, vec_bytes, top_k)).fetchall()
        finally:
            conn.close()

        return [
            {
                "chunk_index":  r[0],
                "text":         r[1],
                "tecken_start": r[2],
                "tecken_slut":  r[3],
                "score":        float(r[4]),
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # LRU-cache
    # ------------------------------------------------------------------

    def _uppdatera_senast_anvand(self, dok_id: str) -> None:
        """Uppdaterar senast_anvand-tidstämpeln (LRU-tracking). Icke-kritisk."""
        try:
            conn = self._hamta_db()
            try:
                if self._ar_postgres():
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE riksdag_api.dokument SET senast_anvand = NOW() "
                            "WHERE dok_id = %s",
                            (dok_id,)
                        )
                    conn.commit()
                else:
                    conn.execute(
                        "UPDATE dokument SET senast_anvand = ? WHERE dok_id = ?",
                        (int(time.time()), dok_id)
                    )
                    conn.commit()
            finally:
                conn.close()
        except Exception as e:
            # LRU-uppdatering är icke-kritisk — tyst fel
            pass  # noqa: S110

    def _hamta_cache_storlek_gb(self) -> float:
        """Returnerar cachens aktuella storlek i GB."""
        try:
            if self._ar_postgres():
                conn = self._hamta_db()
                try:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT (
                                pg_total_relation_size('riksdag_api.dokument') +
                                pg_total_relation_size('riksdag_api.chunks')
                            ) AS bytes
                        """)
                        row = cur.fetchone()
                        return (row[0] or 0) / 1_000_000_000
                finally:
                    conn.close()
            else:
                db_path = DATABASE_URL.replace("sqlite:///", "")
                return os.path.getsize(db_path) / 1_000_000_000
        except Exception as e:
            log.warning("Kunde inte mäta cachestorlek: %s", e)
            return 0.0

    def _stada_cache(self) -> None:
        """
        Kör LRU-eviction (Least Recently Used) om cachen överstiger CACHE_MAX_GB.

        Tar bort 20 äldst-använda dokument per omgång. PostgreSQL: chunks raderas
        via ON DELETE CASCADE. SQLite: chunk_embeddings rensas manuellt (vec0-tabellen
        har ingen FK) och VACUUM körs efteråt så att filstorleken faktiskt minskar
        (SQLite frigör inte disk förrän VACUUM körs — utan detta triggar eviction i loop).

        Avsiktligt beteende: rader med senast_anvand IS NULL hoppas över. Direkt
        efter migration v3.0.0 har alla befintliga rader NULL och eviction har ingen
        effekt tills de lästs ut (då sätts senast_anvand). Cachen kan därför tillfälligt
        överstiga CACHE_MAX_GB under en övergångsperiod.

        Icke-kritisk — fel loggas men stoppar inte servern.
        """
        storlek_gb = self._hamta_cache_storlek_gb()
        if storlek_gb <= CACHE_MAX_GB:
            return

        log.info("Cache är %.1f GB (gräns %d GB) — kör LRU-städning.", storlek_gb, CACHE_MAX_GB)

        try:
            conn = self._hamta_db()
            try:
                if self._ar_postgres():
                    with conn.cursor() as cur:
                        cur.execute("""
                            DELETE FROM riksdag_api.dokument
                            WHERE dok_id IN (
                                SELECT dok_id FROM riksdag_api.dokument
                                WHERE senast_anvand IS NOT NULL
                                ORDER BY senast_anvand ASC
                                LIMIT 20
                            )
                        """)
                    conn.commit()
                else:
                    # Rensa chunk_embeddings manuellt (vec0 saknar FK-kaskad).
                    conn.execute("""
                        DELETE FROM chunk_embeddings WHERE chunk_id IN (
                            SELECT id FROM chunks WHERE dok_id IN (
                                SELECT dok_id FROM dokument
                                WHERE senast_anvand IS NOT NULL
                                ORDER BY senast_anvand ASC
                                LIMIT 20
                            )
                        )
                    """)
                    conn.execute("""
                        DELETE FROM dokument
                        WHERE dok_id IN (
                            SELECT dok_id FROM dokument
                            WHERE senast_anvand IS NOT NULL
                            ORDER BY senast_anvand ASC
                            LIMIT 20
                        )
                    """)
                    conn.commit()
                    # VACUUM frigör diskutrymme -- utan detta minskar inte filstorleken
                    # och nästa write triggar eviction igen (oändlig loop).
                    conn.execute("VACUUM")
            finally:
                conn.close()
        except Exception as e:
            log.warning("LRU-städning misslyckades: %s", e)
