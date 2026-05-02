"""
document_store.py — Cachalager och semantisk sökning för riksdagsdokument

Hanterar hämtning av dokument från riksdagens API, HTML-strippning,
chunkning, embedding-generering och lagring i lokal databas.

Stöder PostgreSQL + pgvector (primär) och SQLite + sqlite-vec (fallback).
Databasens typ styrs av DATABASE_URL i .env.

Krav (PostgreSQL): psycopg2-binary, sentence-transformers, beautifulsoup4, httpx
Krav (SQLite):     sqlite-vec, sentence-transformers, beautifulsoup4, httpx

Körning:
    from document_store import DocumentStore
    store = DocumentStore()
    doc   = store.get_document("C09C516")
    hits  = store.search_in_document("C09C516", "torghandelns frihet")
    ctx   = store.get_related("HD03158")
"""

import json
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
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

API_BASE        = os.getenv("RIKSDAG_API_BASE", "https://data.riksdagen.se")
DATABASE_URL    = os.getenv("DATABASE_URL", "sqlite:///riksdag_rag.db")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "KBLab/sentence-bert-swedish-cased")
CACHE_MAX_GB    = int(os.getenv("CACHE_MAX_SIZE_GB", 2))
CACHE_TTL_DAYS  = int(os.getenv("CACHE_TTL_CURRENT_SESSION_DAYS", 7))
CHUNK_SIZE      = 800   # tecken per chunk
CHUNK_OVERLAP   = 200   # överlapp mellan chunk i och i+1
EMBED_DIM       = 768   # KBLab/sentence-bert-swedish-cased → 768 dimensioner
INLEDNING_LEN   = 500   # tecken som returneras som förhandsvisning

# URL-segment på riksdagen.se per doktyp-kod
_URL_SEGMENTS = {
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
_RELEVANTA_RELATIONSTYPER = {
    "behandlas_i",       # prop/mot → bet som behandlar dokumentet
    "följdmotion",       # prop → motioner som följer på propositionen
    "behandlar",         # bet → prop/mot som betänkandet behandlar
    "protokollbeslut",   # bet → prot med riksdagsbeslut
    "protokolldebatt",   # bet → prot med kammardebatten
    "beslut_id",         # bet → voteringsprotokoll
    "svar",              # fr/ip → svar på frågan/interpellationen
    "fraga",             # svar → ursprunglig fråga
    "interpellationssvar",  # ip → interpellationssvar
}


def _build_riksdagen_url(dok_id: str, doktyp: str) -> str:
    """Bygger URL till riksdagen.se för ett dokument."""
    seg = _URL_SEGMENTS.get(doktyp.lower(), doktyp.lower())
    return f"https://www.riksdagen.se/sv/dokument-och-lagar/dokument/{seg}/_{dok_id}/"


def _strip_html(html: str) -> str:
    """Strippar HTML-taggar och returnerar ren text."""
    soup = BeautifulSoup(html, "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(separator=" ")).strip()


def _chunk_text(text: str) -> list[dict]:
    """Delar text i överlappande stycken för RAG-indexering."""
    chunks = []
    start  = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunks.append({
            "text":       text[start:end],
            "char_start": start,
            "char_end":   min(end, len(text)),
        })
        if end >= len(text):
            break
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def _current_riksmote() -> str:
    """Returnerar innevarande riksmöte på formen ÅÅÅÅ/ÅÅ, t.ex. '2024/25'."""
    today = date.today()
    if today.month >= 9:
        return f"{today.year}/{str(today.year + 1)[-2:]}"
    return f"{today.year - 1}/{str(today.year)[-2:]}"


def _xml_text(element: ET.Element, tag: str) -> str:
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

    Stöder PostgreSQL (primär) och SQLite (fallback) som lagringsbackend.
    Välj backend via DATABASE_URL i .env:
        postgresql://user:password@localhost/riksdag_rag
        sqlite:///riksdag_rag.db
    """

    def __init__(self) -> None:
        self._db_type = self._detect_db_type()
        self._conn    = None
        self._model   = None

    # ------------------------------------------------------------------
    # Publika metoder
    # ------------------------------------------------------------------

    def get_document(self, dok_id: str) -> dict:
        """
        Returnerar metadata, inledning och related_hints för ett dokument.

        Om dokumentet finns i cache och cachen är giltig returneras det direkt.
        Annars hämtas dokumentet från riksdagens API, indexeras och cachas.

        Returnerar dict med nycklarna:
            dok_id, doktyp, titel, datum, rm, status,
            url_riksdagen, inledning, cached, ocr_warning,
            related_hints (lista med relaterade dokument)
        """
        if self._is_valid_cache(dok_id):
            return self._load_from_cache(dok_id)

        raw = self._fetch_from_api(dok_id)
        self._index_and_store(raw)
        return self._load_from_cache(dok_id)

    def search_in_document(
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
            chunk_index, text, char_start, char_end, score
        """
        self.get_document(dok_id)
        query_vec = self._embed([query])[0]
        return self._vector_search(dok_id, query_vec, top_k)

    def get_related(self, dok_id: str) -> dict:
        """
        Returnerar ett kontextpaket för ett dokument med alla relaterade dokument
        grupperade efter relationstyp.

        Hämtar alltid färsk data från dokumentstatus-endpointen (lättviktig XML,
        ~10–50 KB) för att fånga relationer som tillkommit sedan dokumentet cachades
        (t.ex. svar på interpellationer, nya följdmotioner).

        Returnerar dict med:
            dok_id, doktyp, titel, rm,
            relaterade: dict grupperat per relationstyp,
            extra: motgrund, motkat, mottagare, besvaradav, stalldtill
        """
        # Hämta dokumentmetadata (för titel, doktyp etc.)
        doc = self.get_document(dok_id)

        # Hämta alltid färsk relationsdata — dokumentstatus är lättviktig
        status = self._fetch_dokumentstatus(dok_id)

        # Gruppera relationer per typ
        grouped: dict = {}
        for rel in status["relations"]:
            rtyp = rel["referenstyp"]
            grouped.setdefault(rtyp, []).append({
                "dok_id":  rel["dok_id"],
                "doktyp":  rel["doktyp"],
                "titel":   rel["titel"],
                "subtitel": rel.get("subtitel", ""),
                "url":     _build_riksdagen_url(rel["dok_id"], rel["doktyp"]),
            })

        return {
            "dok_id":    dok_id,
            "doktyp":    doc.get("doktyp", ""),
            "titel":     doc.get("titel", ""),
            "rm":        doc.get("rm", ""),
            "relaterade": grouped,
            "extra":      status["extra"],
        }

    # ------------------------------------------------------------------
    # Hämtning från API
    # ------------------------------------------------------------------

    def _fetch_from_api(self, dok_id: str) -> dict:
        """
        Hämtar dokument och dokumentstatus från riksdagens API.
        Returnerar dict med metadata, ren text och related_hints.
        """
        # Fulltext (XML med inbäddad HTML)
        text_url = f"{API_BASE}/dokument/{dok_id}/text"
        r = httpx.get(text_url, timeout=60)
        r.raise_for_status()

        root = ET.fromstring(r.text)
        fields = {}
        for child in root:
            for sub in child:
                if sub.text:
                    fields[sub.tag] = sub.text.strip()

        html_content = fields.get("html", "")
        plain_text   = _strip_html(html_content) if html_content else ""
        doktyp       = fields.get("doktyp", "").lower()

        # Dokumentstatus XML för relationsdata
        try:
            status = self._fetch_dokumentstatus(dok_id)
            related_hints = status["relations"]
        except Exception:
            related_hints = []

        return {
            "dok_id":        dok_id,
            "doktyp":        doktyp,
            "titel":         fields.get("titel", ""),
            "datum":         fields.get("datum", ""),
            "rm":            fields.get("rm", ""),
            "status":        fields.get("status"),
            "url_riksdagen": _build_riksdagen_url(dok_id, doktyp),
            "inledning":     plain_text[:INLEDNING_LEN],
            "plain_text":    plain_text,
            "related_hints": json.dumps(related_hints, ensure_ascii=False),
        }

    def _fetch_dokumentstatus(self, dok_id: str) -> dict:
        """
        Hämtar dokumentstatus XML och extraherar relationer och extra kontext.

        Returnerar dict med:
            relations: lista av dicts med referenstyp, dok_id, doktyp, titel, subtitel
            extra:     dict med motgrund, motkat, mottagare, besvaradav, stalldtill
        """
        url = f"{API_BASE}/dokumentstatus/{dok_id}"
        r   = httpx.get(url, timeout=30)
        r.raise_for_status()
        root = ET.fromstring(r.text)

        # Extrahera relationer från dokreferens
        relations = []
        for ref in root.iter("referens"):
            referenstyp = _xml_text(ref, "referenstyp")
            if referenstyp not in _RELEVANTA_RELATIONSTYPER:
                continue
            ref_dok_id = _xml_text(ref, "ref_dok_id")
            if not ref_dok_id:
                continue
            relations.append({
                "referenstyp": referenstyp,
                "dok_id":      ref_dok_id,
                "doktyp":      _xml_text(ref, "ref_dok_typ"),
                "titel":       _xml_text(ref, "ref_dok_titel"),
                "subtitel":    _xml_text(ref, "ref_dok_subtitel"),
            })

        # Extrahera extra kontext: motgrund, motkat
        extra: dict = {}
        for uppgift in root.iter("uppgift"):
            kod  = _xml_text(uppgift, "kod")
            text = _xml_text(uppgift, "text")
            if kod in ("motgrund", "motkat") and text:
                extra[kod] = text

        # mottagare och intressentroller (besvaradav, stalldtill)
        dok_el = root.find("dokument")
        if dok_el is not None:
            mottagare = _xml_text(dok_el, "mottagare")
            if mottagare:
                extra["mottagare"] = mottagare

        for intressent in root.iter("intressent"):
            roll = _xml_text(intressent, "roll")
            namn = _xml_text(intressent, "namn")
            if roll in ("besvaradav", "stalldtill") and namn:
                extra[roll] = namn

        return {"relations": relations, "extra": extra}

    # ------------------------------------------------------------------
    # Indexering
    # ------------------------------------------------------------------

    def _index_and_store(self, raw: dict) -> None:
        """Chunkar, embeddar och lagrar ett hämtat dokument."""
        chunks = _chunk_text(raw["plain_text"])
        if not chunks:
            return

        texts      = [c["text"] for c in chunks]
        embeddings = self._embed(texts)

        is_current = raw.get("rm") == _current_riksmote()

        if self._db_type == "postgres":
            self._store_postgres(raw, chunks, embeddings, is_current)
        else:
            self._store_sqlite(raw, chunks, embeddings, is_current)

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def _get_model(self):
        """Lazy-laddar embeddingmodellen vid första anrop."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            print(f"Laddar embeddingmodell: {EMBEDDING_MODEL} (tar ~30 s första gången)...", file=sys.stderr)
            self._model = SentenceTransformer(EMBEDDING_MODEL)
        return self._model

    def _embed(self, texts: list[str]) -> list:
        """Returnerar lista av numpy-vektorer för given lista av texter."""
        model = self._get_model()
        return model.encode(texts, show_progress_bar=False, normalize_embeddings=True)

    # ------------------------------------------------------------------
    # Cache-validering
    # ------------------------------------------------------------------

    def _is_valid_cache(self, dok_id: str) -> bool:
        """Returnerar True om dokumentet finns i cachen och inte är utgånget."""
        row = self._fetch_cache_meta(dok_id)
        if row is None:
            return False
        is_current, cached_at = row
        if not is_current:
            return True   # historiska dokument cachas permanent

        if self._db_type == "postgres":
            age_days = (datetime.now(timezone.utc) - cached_at).days
        else:
            age_days = (time.time() - cached_at) / 86_400
        return age_days < CACHE_TTL_DAYS

    # ------------------------------------------------------------------
    # Databasoperationer — PostgreSQL
    # ------------------------------------------------------------------

    def _pg_conn(self):
        """Returnerar en aktiv PostgreSQL-anslutning (återansluter vid behov)."""
        import psycopg2
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(DATABASE_URL)
        return self._conn

    def _fetch_cache_meta(self, dok_id: str):
        """Hämtar (is_current_session, cached_at) ur cachen, eller None."""
        if self._db_type == "postgres":
            conn = self._pg_conn()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT is_current_session, cached_at FROM documents WHERE dok_id = %s",
                    (dok_id,)
                )
                return cur.fetchone()
        else:
            conn = self._sqlite_conn()
            cur  = conn.execute(
                "SELECT is_current_session, cached_at FROM documents WHERE dok_id = ?",
                (dok_id,)
            )
            return cur.fetchone()

    def _load_from_cache(self, dok_id: str) -> dict:
        """Läser dokumentmetadata ur cachen och returnerar som dict."""
        cols = "dok_id, doktyp, titel, datum, rm, status, url_riksdagen, inledning, related_hints"
        if self._db_type == "postgres":
            conn = self._pg_conn()
            with conn.cursor() as cur:
                cur.execute(f"SELECT {cols} FROM documents WHERE dok_id = %s", (dok_id,))
                row = cur.fetchone()
        else:
            conn = self._sqlite_conn()
            cur  = conn.execute(f"SELECT {cols} FROM documents WHERE dok_id = ?", (dok_id,))
            row  = cur.fetchone()

        if row is None:
            raise ValueError(f"Dokument {dok_id!r} saknas i cachen trots förväntat närvaro.")

        keys = ["dok_id", "doktyp", "titel", "datum", "rm",
                "status", "url_riksdagen", "inledning", "related_hints"]
        result = dict(zip(keys, row))

        # Deserialisera related_hints från JSON
        raw_hints = result.pop("related_hints", None)
        try:
            result["related_hints"] = json.loads(raw_hints) if raw_hints else []
        except (json.JSONDecodeError, TypeError):
            result["related_hints"] = []

        result["cached"]      = True
        result["ocr_warning"] = result.get("status") == "ocr"
        return result

    def _store_postgres(self, raw: dict, chunks: list, embeddings, is_current: bool) -> None:
        """Lagrar dokument, chunks och embeddings i PostgreSQL."""
        import psycopg2.extras
        conn = self._pg_conn()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO documents
                    (dok_id, doktyp, titel, datum, rm, status,
                     url_riksdagen, inledning, related_hints, cached_at, is_current_session)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
                ON CONFLICT (dok_id) DO UPDATE SET
                    cached_at          = EXCLUDED.cached_at,
                    is_current_session = EXCLUDED.is_current_session,
                    inledning          = EXCLUDED.inledning,
                    related_hints      = EXCLUDED.related_hints
            """, (
                raw["dok_id"], raw["doktyp"], raw["titel"], raw["datum"],
                raw["rm"], raw["status"], raw["url_riksdagen"],
                raw["inledning"], raw.get("related_hints"), is_current,
            ))

            cur.execute("DELETE FROM chunks WHERE dok_id = %s", (raw["dok_id"],))

            psycopg2.extras.execute_values(cur, """
                INSERT INTO chunks (dok_id, chunk_index, text, char_start, char_end, embedding)
                VALUES %s
            """, [
                (raw["dok_id"], i, c["text"], c["char_start"], c["char_end"],
                 embeddings[i].tolist())
                for i, c in enumerate(chunks)
            ])
        conn.commit()

    def _vector_search(self, dok_id: str, query_vec, top_k: int) -> list[dict]:
        """Utför vektorsökning och returnerar de top_k mest relevanta chunks."""
        if self._db_type == "postgres":
            conn = self._pg_conn()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT chunk_index, text, char_start, char_end,
                           1 - (embedding <=> %s::vector) AS score
                    FROM   chunks
                    WHERE  dok_id = %s
                    ORDER  BY embedding <=> %s::vector
                    LIMIT  %s
                """, (query_vec.tolist(), dok_id, query_vec.tolist(), top_k))
                rows = cur.fetchall()
        else:
            import sqlite_vec, numpy as np
            conn     = self._sqlite_conn()
            vec_bytes = query_vec.astype("float32").tobytes()
            rows = conn.execute("""
                SELECT c.chunk_index, c.text, c.char_start, c.char_end,
                       1 - vec_distance_cosine(ce.embedding, ?) AS score
                FROM   chunk_embeddings ce
                JOIN   chunks c ON c.id = ce.chunk_id
                WHERE  c.dok_id = ?
                ORDER  BY vec_distance_cosine(ce.embedding, ?)
                LIMIT  ?
            """, (vec_bytes, dok_id, vec_bytes, top_k)).fetchall()

        return [
            {"chunk_index": r[0], "text": r[1],
             "char_start": r[2], "char_end": r[3], "score": float(r[4])}
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Databasoperationer — SQLite
    # ------------------------------------------------------------------

    def _sqlite_conn(self):
        """Returnerar en aktiv SQLite-anslutning med sqlite-vec laddat."""
        import sqlite_vec
        if self._conn is None:
            db_path = DATABASE_URL.replace("sqlite:///", "")
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            sqlite_vec.load(self._conn)
        return self._conn

    def _store_sqlite(self, raw: dict, chunks: list, embeddings, is_current: bool) -> None:
        """Lagrar dokument, chunks och embeddings i SQLite."""
        conn = self._sqlite_conn()
        now  = int(time.time())

        conn.execute("""
            INSERT OR REPLACE INTO documents
                (dok_id, doktyp, titel, datum, rm, status,
                 url_riksdagen, inledning, related_hints, cached_at, is_current_session)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            raw["dok_id"], raw["doktyp"], raw["titel"], raw["datum"],
            raw["rm"], raw["status"], raw["url_riksdagen"],
            raw["inledning"], raw.get("related_hints"), now, int(is_current),
        ))

        conn.execute("DELETE FROM chunks WHERE dok_id = ?", (raw["dok_id"],))

        for i, c in enumerate(chunks):
            conn.execute("""
                INSERT INTO chunks (dok_id, chunk_index, text, char_start, char_end)
                VALUES (?, ?, ?, ?, ?)
            """, (raw["dok_id"], i, c["text"], c["char_start"], c["char_end"]))
            chunk_id  = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            vec_bytes = embeddings[i].astype("float32").tobytes()
            conn.execute(
                "INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (?, ?)",
                (chunk_id, vec_bytes)
            )

        conn.commit()

    # ------------------------------------------------------------------
    # Intern hjälpmetod
    # ------------------------------------------------------------------

    def _detect_db_type(self) -> str:
        if DATABASE_URL.startswith("postgresql://") or DATABASE_URL.startswith("postgres://"):
            return "postgres"
        return "sqlite"
