# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Magnus Kolsjö
# Se LICENSE-filen i repots rot för fullständig licenstext.

"""
mcp_server.py — MCP-server för riksdagens öppna data (1867–idag)

Exponerar följande verktyg till MCP-kompatibla AI-verktyg:
  rd_search                 — Söker dokument via riksdagens API
  rd_get_document           — Hämtar och cachar ett dokument (RAG)
  rd_search_in_document     — Semantisk sökning inom ett dokument
  rd_get_context            — Kontextpaket: relaterade dokument per relationstyp
  rd_get_anforanden         — Hämtar debattinlägg med fulltext
  rd_get_voteringar         — Hämtar voteringsdata
  rd_list_riksmoten         — Listar tillgängliga riksmöten
  rd_resolve_sfs            — Slår upp SFS-nummer för en lag via namn
  rd_search_ledamoter       — Söker riksdagsledamöter på namn, parti eller valkrets
  rd_get_ledamot            — Hämtar fullständig profil för en ledamot
  rd_get_ledamot_aktivitet  — Hämtar en ledamots senaste anföranden och motioner

Transport-lägen (styrs via MCP_TRANSPORT i .env):

  stdio (standard, lokal användning):
    python3 mcp_server.py
    MCP-klienten startar och hanterar processen direkt.

  http (hostad driftsättning):
    MCP_TRANSPORT=http python3 mcp_server.py
    Servern lyssnar på MCP_HOST:MCP_PORT (standard 127.0.0.1:8000).
    Sätt MCP_API_KEY för Bearer-token-autentisering.
    I produktion: lägg en reverse proxy (t.ex. Nginx) framför servern.

Konfiguration via .env (se config.example.env).
"""

import logging
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from document_store import DocumentStore

load_dotenv(Path(__file__).parent / '.env')

# ── Konfiguration ──────────────────────────────────────────────────────────────

API_BASE  = os.getenv("RIKSDAG_API_BASE", "https://data.riksdagen.se")
PAGE_SIZE = int(os.getenv("RIKSDAG_PAGE_SIZE", 10))

# Transport och autentisering (regel 8 i projektstandarden)
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "stdio").lower()
MCP_HOST      = os.getenv("MCP_HOST",      "127.0.0.1")
MCP_PORT      = int(os.getenv("MCP_PORT",  "8000"))
MCP_API_KEY   = os.getenv("MCP_API_KEY",   "")

# SOU-flaggor — styr om SOU-sökning resp. SOU-hämtning/lagring exponeras.
# Standard: true (fullt funktionell som fristående server).
# Satt till false i installationer där liu-sou-servern (ström 4) hanterar SOU
# för att undvika att SOU-fulltext lagras i två databaser.
SOU_SOKNING_AKTIV  = os.getenv("SOU_SOKNING_AKTIV",  "true").lower() == "true"
SOU_HAMTNING_AKTIV = os.getenv("SOU_HAMTNING_AKTIV", "true").lower() == "true"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_store: Optional[DocumentStore] = None


def get_store() -> DocumentStore:
    global _store
    if _store is None:
        _store = DocumentStore()
    return _store


_URL_SEGMENTS = {
    "prop": "proposition",
    "mot":  "motion",
    "bet":  "betankande",
    "prot": "protokoll",
    "sou":  "statens-offentliga-utredningar",
    "dir":  "kommittedirektiv",
    "ds":   "departementsserien",
}


def _riksdagen_url(dok_id: str, doktyp: str) -> str:
    seg = _URL_SEGMENTS.get(doktyp.lower(), doktyp.lower())
    return f"https://www.riksdagen.se/sv/dokument-och-lagar/dokument/{seg}/_{dok_id}/"


def _get_json(path: str, params: dict) -> dict:
    params["utformat"] = "json"
    r = httpx.get(f"{API_BASE}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _normalize_docs(dl: dict) -> list:
    docs = dl.get("dokument", [])
    if isinstance(docs, dict):
        docs = [docs]
    return docs or []


# Prefix som anvands nar en formell dokumentreferens byggs av rm + beteckning.
# Exempel: ("sou", "2025", "106") -> "SOU 2025:106".
_REFERENS_PREFIX = {
    "prop": "prop.",
    "mot":  "mot.",
    "bet":  "bet.",
    "prot": "prot.",
    "sou":  "SOU",
    "ds":   "Ds",
    "dir":  "dir.",
}


def _formatera_referens(doktyp: str, rm: str, beteckning: str) -> str:
    """Bygger formell dokumentreferens, t.ex. 'SOU 2025:106' eller 'prop. 2024/25:158'."""
    if not rm or not beteckning:
        return ""
    prefix = _REFERENS_PREFIX.get((doktyp or "").lower(), (doktyp or "").lower())
    return f"{prefix} {rm}:{beteckning}"


def _dela_beteckning(beteckning: str) -> tuple[str, str]:
    """
    Splittrar en formell dokumentreferens till (rm, nummer).

    Hanterar formaten:
        "2025:106"          -> ("2025",    "106")
        "2024/25:158"       -> ("2024/25", "158")
        "2024/25:FiU6"      -> ("2024/25", "FiU6")
        "SOU 2025:106"      -> ("2025",    "106")     -- prefix tas bort
        "prop. 2024/25:158" -> ("2024/25", "158")

    Om input inte kan tolkas returneras ("", "").
    """
    if not beteckning:
        return "", ""
    s = beteckning.strip()
    # Ta bort vanliga prefix (skiftlagesokansligt) -- "SOU ", "prop. ", "Ds ", osv.
    for prefix in ("SOU ", "Ds ", "Dir ", "dir. ", "prop. ", "prop ",
                   "mot. ", "mot ", "bet. ", "bet ", "prot. ", "prot "):
        if s.lower().startswith(prefix.lower()):
            s = s[len(prefix):].strip()
            break
    if ":" not in s:
        return "", ""
    rm, _, nummer = s.partition(":")
    return rm.strip(), nummer.strip()


def _format_doc(doc: dict) -> dict:
    dok_id     = doc.get("dok_id", "")
    doktyp     = doc.get("doktyp", "")
    rm         = doc.get("rm", "")
    beteckning = doc.get("beteckning", "")
    nummer     = doc.get("nummer", "") or beteckning
    result = {
        "dok_id":     dok_id,
        "doktyp":     doktyp,
        "titel":      doc.get("titel", ""),
        "datum":      doc.get("datum", ""),
        "rm":         rm,
        "beteckning": beteckning,
        "nummer":     nummer,
        "referens":   _formatera_referens(doktyp, rm, beteckning),
        "url":        _riksdagen_url(dok_id, doktyp),
    }
    if doc.get("status") == "ocr":
        result["ocr_varning"] = (
            "Detta dokument ar inskannat material med OCR-text. "
            "Kvaliteten kan vara begransad. Se PDF-originalet via url-faltet."
        )
    return result


def _extract_sfs_nr(titel: str) -> str:
    import re as _re
    m = _re.search(r"\((\d{4}:\d+)\)", titel)
    return m.group(1) if m else ""


def _normalize_persons(pl: dict) -> list:
    """Normaliserar personlista-svaret till en lista."""
    persons = pl.get("person", [])
    if isinstance(persons, dict):
        persons = [persons]
    return persons or []


def _format_person(p: dict) -> dict:
    """Formaterar ett person-objekt till ett konsekvent svarformat."""
    iid = p.get("intressent_id", "")
    return {
        "iid":       iid,
        "fornamn":   p.get("tilltalsnamn", ""),
        "efternamn": p.get("efternamn", ""),
        "parti":     p.get("parti", ""),
        "valkrets":  p.get("valkrets", ""),
        "status":    p.get("status", ""),
        "url":       f"https://www.riksdagen.se/sv/ledamoter-och-partier/ledamot/{iid}/",
    }


mcp = FastMCP("riksdag-oppna-data")


@mcp.tool()
def rd_search(
    query: str = "",
    doktyp: str = "",
    beteckning: str = "",
    year_from: int = 0,
    year_to: int = 0,
    rm: str = "",
    nummer: str = "",
    sz: int = 10,
) -> list[dict]:
    """
    Soker i riksdagens oppna data efter propositioner, motioner, betankanden,
    protokoll, SOU, Ds och kommittedirektiv.

    Parametrar:
        query      -- Fritext (t.ex. "klimatlag" eller "ordningslag")
        doktyp     -- Filtrera pa dokumenttyp: prop | mot | bet | prot | sou | ds | dir
        beteckning -- Exakt formell dokumentreferens, t.ex. "2025:106",
                      "2024/25:158", "2024/25:FiU6". Aven prefixade former
                      ("SOU 2025:106", "prop. 2024/25:158") accepteras.
                      Splittras internt till rm + nummer/bet.
        year_from  -- Tidigaste ar (t.ex. 1990)
        year_to    -- Senaste ar (t.ex. 2024)
        rm         -- Riksmote/ar. OBS olika format per dokumenttyp:
                        prop, mot, bet, prot -> "2024/25" (riksmotesformat)
                        sou, ds, dir         -> "2025"    (kalenderar)
        nummer     -- Exakt nummer/beteckning inom ett rm. Anvands tillsammans
                      med rm. For doktyp=bet (alfanumeriska beteckningar som
                      "FiU6") skickas det som API-fel `bet`, annars som `nr`.
        sz         -- Antal traffar (max 100)

    Returnerar lista med dok_id, doktyp, titel, datum, rm, beteckning, nummer,
    referens (formaterad citering t.ex. "SOU 2025:106") och lank till
    riksdagen.se. Dokument med status ocr_varning ar inskannat material --
    se PDF-originalet.

    Tips:
      * For exakt uppslag pa SOU 2025:106 skriv beteckning="2025:106" och
        doktyp="sou" -- API:et returnerar exakt 1 traff. Detta ar mycket
        robustare an fritextsok som kan ge ihopblandade nummer.
      * For att hitta alla foljdmotioner till en proposition, sok med
        propositionens beteckning som query, t.ex. query="prop 2025/26:158"
        med doktyp="mot".
      * For fullstandig relationsoversikt: anvand rd_get_context.
    """
    if not SOU_SOKNING_AKTIV and doktyp.lower() == "sou":
        return [{"fel": "SOU-sokning ar inaktiverad pa denna server "
                        "(SOU_SOKNING_AKTIV=false i .env). "
                        "Anvand liu-sou-servern (strom 4) for SOU-sokning."}]

    params: dict = {"sz": min(sz, 100)}
    if query:     params["sok"]    = query
    if doktyp:    params["doktyp"] = doktyp

    # Beteckning kan ange bade rm och nummer pa en gang ("2025:106").
    # Direkta parametrar (rm, nummer) overskrider beteckningens delar.
    bet_rm, bet_nr = _dela_beteckning(beteckning)
    effektivt_rm  = rm or bet_rm
    effektivt_nr  = nummer or bet_nr

    if effektivt_rm:
        params["rm"] = effektivt_rm

    if effektivt_nr:
        # Betankanden har alfanumeriska beteckningar (FiU6, KU1) som maste
        # skickas via API-faltet `bet`. Ovriga doktyper anvander `nr`.
        if (doktyp or "").lower() == "bet" or not effektivt_nr.isdigit():
            params["bet"] = effektivt_nr
        else:
            params["nr"]  = effektivt_nr

    if year_from: params["from"] = f"{year_from}-01-01"
    if year_to:   params["tom"]  = f"{year_to}-12-31"

    data = _get_json("/dokumentlista/", params)
    dl   = data["dokumentlista"]
    docs = _normalize_docs(dl)
    return [_format_doc(d) for d in docs]


@mcp.tool()
def rd_get_document(dok_id: str) -> dict:
    """
    Hamtar ett riksdagsdokument och cachar det lokalt for semantisk sokning.

    Returnerar metadata, de forsta 500 tecknen av fulltexten (inledning),
    en lank till riksdagen.se samt relaterat_tips -- en lista med direkt
    relaterade dokument (foljdmotioner, behandlande betankande, protokoll m.m.)
    hamtad fran riksdagens dokumentstatus-endpoint.

    For djupsokning i fulltexten: anvand rd_search_in_document.
    For fullstandig relationsoversikt: anvand rd_get_context.
    Dokument aldre an ca 1960 kan vara OCR-skannade -- se ocr_varning i svaret.
    """
    if not SOU_HAMTNING_AKTIV:
        # Lättviktskoll: hämta bara metadata för att se om det är en SOU.
        try:
            meta_data = _get_json("/dokumentlista/", {"id": dok_id, "sz": 1})
            docs = _normalize_docs(meta_data.get("dokumentlista", {}))
            if docs and docs[0].get("doktyp", "").lower() == "sou":
                return {"fel": "SOU-hamtning ar inaktiverad pa denna server "
                                "(SOU_HAMTNING_AKTIV=false i .env). "
                                "Anvand liu-sou-servern (strom 4) for SOU-fulltext."}
        except Exception:
            pass  # Om metadatakollen misslyckas, fall igenom till vanlig hamtning

    return get_store().get_document(dok_id)


@mcp.tool()
def rd_search_in_document(
    dok_id: str,
    query: str,
    top_k: int = 5,
) -> list[dict]:
    """
    Semantisk sokning inom ett specifikt riksdagsdokument.

    Anvands nar ett dokument ar for stort for att lasa i sin helhet.
    Returnerar de stycken som ar semantiskt mest relevanta for fragan.

    Parametrar:
        dok_id -- Dokumentets ID (hamtas via rd_search)
        query  -- Sokning pa naturligt sprak, t.ex. "torghandelns frihet"
        top_k  -- Antal stycken att returnera (standard 5)
    """
    return get_store().search_in_document(dok_id, query, top_k)


@mcp.tool()
def rd_get_context(dok_id: str) -> dict:
    """
    Hamtar ett fullstandigt kontextpaket for ett riksdagsdokument.

    Returnerar dokumentets direkta sammanhang: alla relaterade dokument
    grupperade efter relationstyp samt extra metadata om dokumentet.

    Relationstyper som returneras (nar tillgangliga):
        foljdmotion      -- Motioner inlamnade med anledning av en proposition
        behandlas_i      -- Betankande som behandlar detta dokument
        behandlar        -- Dokument (prop/mot) som detta betankande behandlar
        protokolldebatt  -- Kammarprotokoll med debatten
        protokollbeslut  -- Kammarprotokoll med riksdagsbeslutet
        beslut_id        -- Voteringsprotokoll
        frågesvar       -- Formellt skriftligt svar pa en skriftlig fraga (fr -> frs)
        fråga            -- Ursprunglig skriftlig fraga (frs -> fr)
        ipsvarid         -- Protokoll dar interpellationen besvarades i kammaren
        GemensamtBesvarad -- Andra interpellationer besvarade vid samma tillfalle
        GemensamtSvar    -- Gemensamt svar pa flera interpellationer

    Extra metadata (nar tillgangligt):
        motgrund     -- Propositionsbeteckning som en motion svarar pa
        motkat       -- Motionskategori (Foljdmotion / Fristaende)
        mottagare    -- Vem en fraga/interpellation ar stalld till
        besvaradav   -- Statsrad som besvarar fragan/interpellationen
        stalldtill   -- Statsrad som fragan/interpellationen ar stalld till

    OBS: Svaret pa en interpellation ges muntligen i kammaren -- det finns
    inget separat svars-dokument. Anvand ipsvarid-relationen for att hitta
    protokollet dar debatten agde rum, och rd_search_in_document for att
    hitta relevanta stycken i protokollet.
    Nyligen inlamnade fragor saknar svar tills de besvarats.
    """
    return get_store().get_related(dok_id)


@mcp.tool()
def rd_get_anforanden(
    rm: str,
    talare: str = "",
    parti: str = "",
    sz: int = 20,
) -> list[dict]:
    """
    Hamtar debattinlagg (anforanden) fran riksdagen med fulltext.

    Parametrar:
        rm     -- Riksmote, t.ex. "2024/25" (obligatorisk)
        talare -- Filtrera pa talarens namn
        parti  -- Filtrera pa parti (t.ex. "S", "M", "SD")
        sz     -- Antal anforanden att hamta (standard 20)
    """
    params: dict = {"rm": rm, "sz": min(sz, 75)}
    if talare: params["talare"] = talare
    if parti:  params["parti"]  = parti

    data     = _get_json("/anforandelista/", params)
    al       = data["anforandelista"]
    anf_list = al.get("anforande", [])
    if isinstance(anf_list, dict):
        anf_list = [anf_list]

    results = []
    for anf in anf_list:
        dok_id = anf.get("dok_id", "")
        nr     = anf.get("anforande_nummer", "")
        anf_id = f"{dok_id}-{nr}"
        fulltext = ""
        try:
            r = httpx.get(f"{API_BASE}/anforande/{anf_id}", timeout=30)
            if r.status_code == 200:
                root = ET.fromstring(r.text)
                for child in root:
                    if child.tag == "anforandetext" and child.text:
                        fulltext = child.text.strip()
                        break
        except Exception:
            pass
        results.append({
            "anforande_id":  anf.get("anforande_id", ""),
            "talare":        anf.get("talare", ""),
            "parti":         anf.get("parti", ""),
            "datum":         anf.get("dok_datum", ""),
            "rubrik":        anf.get("avsnittsrubrik", ""),
            "anforandetext": fulltext,
            "protokoll_url": anf.get("protokoll_url_www", ""),
        })
    return results


@mcp.tool()
def rd_get_voteringar(
    rm: str,
    bet: str = "",
) -> list[dict]:
    """
    Hamtar voteringsdata fran riksdagen.

    Parametrar:
        rm  -- Riksmote, t.ex. "2024/25" (obligatorisk)
        bet -- Filtrera pa betankandebeteckning, t.ex. "JuU10"
    """
    params: dict = {"rm": rm, "sz": 100}
    if bet: params["bet"] = bet

    data       = _get_json("/voteringlista/", params)
    vl         = data["voteringlista"]
    voteringar = vl.get("votering", [])
    if isinstance(voteringar, dict):
        voteringar = [voteringar]

    return [
        {
            "votering_id": v.get("votering_id", ""),
            "namn":        v.get("namn", ""),
            "parti":       v.get("parti", ""),
            "rost":        v.get("rost", ""),
            "beteckning":  v.get("beteckning", ""),
            "punkt":       v.get("punkt", ""),
            "avser":       v.get("avser", ""),
        }
        for v in voteringar
    ]


@mcp.tool()
def rd_list_riksmoten() -> list[str]:
    """Returnerar en lista med tillgangliga riksmoten fran det senaste och bakat."""
    data2     = _get_json("/dokumentlista/", {"doktyp": "prop", "sz": 200})
    docs      = _normalize_docs(data2["dokumentlista"])
    riksmoten = sorted({d["rm"] for d in docs if d.get("rm")}, reverse=True)
    return riksmoten


@mcp.tool()
def rd_resolve_sfs(query: str) -> list[dict]:
    """
    Slar upp SFS-nummer for en lag via namn eller sokterm.

    Anvands nar SFS-numret ar okant eller nar en proposition foreslar en ny lag.
    Returnerar matchande lagar med SFS-nummer, titel och datum.
    """
    data  = _get_json("/dokumentlista/", {"doktyp": "sfs", "sok": query, "sz": 20})
    docs  = _normalize_docs(data["dokumentlista"])

    results = []
    for doc in docs:
        titel  = doc.get("titel", "")
        sfs_nr = _extract_sfs_nr(titel)
        if not sfs_nr:
            continue
        results.append({
            "sfs_nr": sfs_nr,
            "titel":  titel,
            "datum":  doc.get("datum", ""),
            "dok_id": doc.get("dok_id", ""),
        })
    return results


@mcp.tool()
def rd_search_ledamoter(
    efternamn: str = "",
    fornamn: str = "",
    parti: str = "",
    valkrets: str = "",
    status: str = "",
    sz: int = 30,
) -> list[dict]:
    """
    Soker riksdagsledamoter pa namn, parti, valkrets eller status.

    Parametrar:
        efternamn -- Efternamn att soka pa (t.ex. "Andersson")
        fornamn   -- Fornamn att soka pa (t.ex. "Anna")
        parti     -- Parti: S | M | SD | V | MP | C | L | KD
        valkrets  -- Valkrets (t.ex. "Stockholms kommun")
        status    -- Filtrera pa status: Tjanstgorande | Tjanstledig | Ersattare
        sz        -- Antal resultat (max 100, standard 30)

    Returnerar lista med iid, for- och efternamn, parti, valkrets, status
    samt lanken till ledamotens profilsida pa riksdagen.se.

    Tips: iid (intressent_id) fran resultatet anvands i rd_get_ledamot och
    rd_get_ledamot_aktivitet for att hamta mer information om en specifik ledamot.
    """
    params: dict = {"sz": min(sz, 100)}
    if efternamn: params["enamn"]     = efternamn
    if fornamn:   params["fnamn"]     = fornamn
    if parti:     params["parti"]     = parti
    if valkrets:  params["valkrets"]  = valkrets
    if status:    params["rdlstatus"] = status

    data    = _get_json("/personlista/", params)
    pl      = data.get("personlista", {})
    persons = _normalize_persons(pl)
    return [_format_person(p) for p in persons]


@mcp.tool()
def rd_get_ledamot(iid: str) -> dict:
    """
    Hamtar fullstandig profil for en riksdagsledamot.

    Parametrar:
        iid -- Ledamotens intressent_id (hamtas via rd_search_ledamoter)

    Returnerar personuppgifter, nuvarande och tidigare uppdrag (utskott,
    kommitteer, delegationer m.m.) samt lank till profilsidan pa riksdagen.se.

    Uppdragslistan ar grupperad efter uppdragstyp och inkluderar tidsperioder
    sa att man kan se nar ledamoten suttit i vilka organ.
    """
    data    = _get_json("/personlista/", {"iid": iid})
    pl      = data.get("personlista", {})
    persons = _normalize_persons(pl)
    if not persons:
        return {"fel": f"Hittade ingen ledamot med iid={iid}"}

    p      = persons[0]
    result = _format_person(p)

    # Hamta uppdragslistan om den finns i svaret
    uppdrag_raw = p.get("personuppdrag", {})
    if isinstance(uppdrag_raw, dict):
        uppdrag_lista = uppdrag_raw.get("uppdrag", [])
        if isinstance(uppdrag_lista, dict):
            uppdrag_lista = [uppdrag_lista]
        # Gruppera per typ
        per_typ: dict = {}
        for u in (uppdrag_lista or []):
            typ = u.get("typ", "okand")
            per_typ.setdefault(typ, []).append({
                "organ":   u.get("organ_kod", ""),
                "roll":    u.get("roll_kod", ""),
                "from_ar": u.get("from", ""),
                "tom_ar":  u.get("tom", ""),
                "status":  u.get("status", ""),
            })
        result["uppdrag"] = per_typ

    result["fodd_ar"] = p.get("fodd_ar", "")
    result["kon"]     = p.get("kon", "")

    return result


@mcp.tool()
def rd_get_ledamot_aktivitet(
    iid: str,
    rm: str = "",
    sz: int = 20,
) -> dict:
    """
    Hamtar en riksdagsledamots senaste parlamentariska aktivitet.

    Parametrar:
        iid -- Ledamotens intressent_id (hamtas via rd_search_ledamoter)
        rm  -- Filtrera pa riksmote, t.ex. "2024/25" (valfri)
        sz  -- Antal poster per kategori (standard 20, max 50)

    Returnerar tre kategorier:
        anforanden       -- Senaste debattinlagg med rubrik, datum och protokolllank
        motioner         -- Senaste inlamnade motioner med titel och lank
        interpellationer -- Senaste interpellationer med titel och lank

    Anvands for att snabbt bilda sig en uppfattning om vad en ledamot
    har arbetat med under ett riksmote eller over tid.
    """
    sz = min(sz, 50)
    params_base: dict = {"iid": iid, "sz": sz}
    if rm:
        params_base["rm"] = rm

    # Anforanden
    try:
        anf_data = _get_json("/anforandelista/", dict(params_base))
        al       = anf_data.get("anforandelista", {})
        anf_list = al.get("anforande", [])
        if isinstance(anf_list, dict):
            anf_list = [anf_list]
        anforanden = [
            {
                "datum":  a.get("dok_datum", ""),
                "rubrik": a.get("avsnittsrubrik", ""),
                "url":    a.get("protokoll_url_www", ""),
            }
            for a in (anf_list or [])
        ]
    except Exception:
        anforanden = []

    # Motioner
    try:
        mot_params = dict(params_base)
        mot_params["doktyp"] = "mot"
        mot_data   = _get_json("/dokumentlista/", mot_params)
        motioner   = [_format_doc(d) for d in _normalize_docs(mot_data["dokumentlista"])]
    except Exception:
        motioner = []

    # Interpellationer
    try:
        ip_params        = dict(params_base)
        ip_params["doktyp"] = "ip"
        ip_data          = _get_json("/dokumentlista/", ip_params)
        interpellationer = [_format_doc(d) for d in _normalize_docs(ip_data["dokumentlista"])]
    except Exception:
        interpellationer = []

    return {
        "iid":              iid,
        "rm":               rm or "alla",
        "anforanden":       anforanden,
        "motioner":         motioner,
        "interpellationer": interpellationer,
    }


# ── HTTP-autentisering ────────────────────────────────────────────────────────

def _make_auth_app(asgi_app, api_key: str):
    """
    Wrappa en ASGI-app med enkel Bearer-token-autentisering.
    Alla anrop utan korrekt Authorization-header avvisas med HTTP 401.
    """
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import PlainTextResponse
    from starlette.routing import Mount

    class ApiKeyMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            token = (
                request.headers.get("Authorization", "")
                .removeprefix("Bearer ")
                .strip()
            )
            if token != api_key:
                return PlainTextResponse(
                    "Obehörig: ogiltig eller saknad API-nyckel.", status_code=401
                )
            return await call_next(request)

    return Starlette(
        routes=[Mount("/", app=asgi_app)],
        middleware=[Middleware(ApiKeyMiddleware)],
    )


# ── Startpunkt ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if MCP_TRANSPORT == "http":
        import uvicorn

        # Preladda embedding-modellen vid uppstart så att första anropet svarar
        # lika snabbt som efterföljande. Misslyckas modellen att laddas syns det
        # direkt i loggarna — inte vid det första användaranropet.
        log.info("Preladdar embedding-modell...")
        get_store()._get_model()
        log.info("Embedding-modell redo")

        # Hämta ASGI-appen från FastMCP
        try:
            asgi_app = mcp.streamable_http_app()
        except AttributeError:
            # Äldre version av mcp-biblioteket
            log.warning(
                "mcp.streamable_http_app() saknas — försöker med sse_app(). "
                "Uppgradera mcp-paketet om problem uppstår."
            )
            asgi_app = mcp.sse_app()

        if MCP_API_KEY:
            log.info("API-nyckelautentisering aktiverad")
            app = _make_auth_app(asgi_app, MCP_API_KEY)
        else:
            log.warning(
                "MCP_API_KEY är inte satt — servern körs utan autentisering. "
                "Bind enbart till loopback (MCP_HOST=127.0.0.1) eller "
                "skydda via reverse proxy."
            )
            app = asgi_app

        log.info("Startar HTTP-transport på %s:%s", MCP_HOST, MCP_PORT)
        uvicorn.run(app, host=MCP_HOST, port=MCP_PORT, log_level="info")
    else:
        log.info("Startar stdio-transport (lokal användning)")
        mcp.run()
