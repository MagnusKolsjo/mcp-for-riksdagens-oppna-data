"""
01_explore_api.py — Utforskningsskript för Riksdagens öppna data API

Kör detta skript för att verifiera API-parametrar, svarsstruktur och täckning
innan MCP-servern implementeras. Skriptet gör inga skrivoperationer — det
är ett rent lässkript avsett för manuell körning.

Krav:
    pip install httpx python-dotenv

Körning:
    python 01_explore_api.py
"""

import json
import os
import sys
import xml.etree.ElementTree as ET
from pprint import pprint

import httpx
from dotenv import load_dotenv

load_dotenv()

API_BASE = os.getenv("RIKSDAG_API_BASE", "https://data.riksdagen.se")


# ---------------------------------------------------------------------------
# Hjälpfunktioner
# ---------------------------------------------------------------------------

def get_json(path: str, params: dict) -> dict:
    """Gör ett GET-anrop och returnerar JSON-svar."""
    params["utformat"] = "json"
    url = f"{API_BASE}{path}"
    r = httpx.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def get_xml_text(dok_id: str) -> str:
    """Hämtar dokumentets textformat (XML med inbäddad HTML)."""
    url = f"{API_BASE}/dokument/{dok_id}/text"
    r = httpx.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def print_section(title: str) -> None:
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Test 1: Dokumentlista — grundläggande parametrar
# ---------------------------------------------------------------------------

def test_dokumentlista():
    print_section("Test 1: Dokumentlista (prop, senaste 2 st)")
    data = get_json("/dokumentlista/", {"doktyp": "prop", "sz": 2})
    dl = data["dokumentlista"]

    print(f"  API-version : {dl['@version']}")
    print(f"  Totalt      : {dl['@traffar']} traffar")
    print(f"  Sida        : {dl['@sida']} av {dl['@sidor']}")
    print(f"  Träff       : {dl['@traff_fran']}–{dl['@traff_till']}")
    print(f"  Nästa sida  : {dl.get('@nasta_sida', '—')}")

    docs = dl["dokument"]
    if isinstance(docs, dict):
        docs = [docs]
    for doc in docs:
        print(f"\n  [{doc['doktyp']}] {doc['datum']} — {doc['titel'][:70]}")
        print(f"    dok_id  : {doc['dok_id']}")
        print(f"    status  : {doc.get('status', '(ingen)')}")
        print(f"    rm      : {doc['rm']}")
        print(f"    organ   : {doc.get('organ', '—')}")
        print(f"    text_url: {doc.get('dokument_url_text', '—')}")


# ---------------------------------------------------------------------------
# Test 2: Täckning per dokumenttyp
# ---------------------------------------------------------------------------

DOKUMENTTYPER = {
    "prop": "Propositioner",
    "mot":  "Motioner",
    "bet":  "Betänkanden",
    "prot": "Protokoll/debatter",
    "sou":  "SOU",
    "ds":   "Ds",
    "dir":  "Kommittédirektiv",
}

def test_tackningskarta():
    print_section("Test 2: Täckning per dokumenttyp")
    for kod, namn in DOKUMENTTYPER.items():
        data = get_json("/dokumentlista/", {
            "doktyp": kod, "sz": 1, "sort": "datum", "sortorder": "asc"
        })
        dl = data["dokumentlista"]
        traffar = dl.get("@traffar", "?")
        docs = dl.get("dokument")
        if not docs:
            print(f"  {namn:30s} ({kod}): 0 dokument")
            continue
        if isinstance(docs, dict):
            docs = [docs]
        aldst = docs[0]
        ocr = " [OCR]" if aldst.get("status") == "ocr" else ""
        print(f"  {namn:30s} ({kod}): {traffar:>7} dokument, äldst: {aldst['datum']}{ocr}")


# ---------------------------------------------------------------------------
# Test 3: Historisk täckning — protokoll 1867
# ---------------------------------------------------------------------------

def test_protokoll_1867():
    print_section("Test 3: Protokoll från 1867")
    data = get_json("/dokumentlista/", {
        "doktyp": "prot",
        "from":   "1867-01-01",
        "tom":    "1867-12-31",
        "sz":     3,
    })
    dl = data["dokumentlista"]
    print(f"  Protokoll 1867: {dl['@traffar']} traffar")

    docs = dl.get("dokument", [])
    if isinstance(docs, dict):
        docs = [docs]
    for doc in docs:
        print(f"\n  {doc['datum']} — {doc['titel']}")
        print(f"    dok_id   : {doc['dok_id']}")
        print(f"    kall_id  : {doc.get('kall_id', '—')}")
        print(f"    status   : {doc.get('status', '—')}  ← 'ocr' = inskannat material")
        print(f"    undertitel: {doc.get('undertitel', '—')}")


# ---------------------------------------------------------------------------
# Test 4: Hämta enskilt dokument (fulltext)
# ---------------------------------------------------------------------------

def test_enskilt_dokument(dok_id: str = "C09C516"):
    print_section(f"Test 4: Enskilt dokument — {dok_id}")
    xml_text = get_xml_text(dok_id)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"  XML-parsningsfel: {e}")
        print(f"  Råsvar (500 tecken): {xml_text[:500]}")
        return

    fields = {}
    for child in root:
        for sub in child:
            if sub.text and sub.text.strip():
                fields[sub.tag] = sub.text.strip()

    for key in ["dok_id", "rm", "datum", "titel", "subtitel", "status", "source"]:
        print(f"  {key:20s}: {fields.get(key, '—')}")

    html_content = fields.get("html", "")
    if html_content:
        print(f"\n  Fulltext (HTML): {len(html_content)} tecken")
        print(f"  Utdrag: {html_content[:200]}…")
    else:
        print("  Ingen fulltext hittades i 'html'-fältet")

    # URL-fält
    for child in root:
        for sub in child:
            if sub.tag in ("dokument_url_text", "dokument_url_html", "dokumentstatus_url_xml"):
                print(f"  {sub.tag}: {sub.text}")


# ---------------------------------------------------------------------------
# Test 5: Paginering
# ---------------------------------------------------------------------------

def test_paginering():
    print_section("Test 5: Paginering")
    for sida in [1, 2, 3]:
        data = get_json("/dokumentlista/", {"doktyp": "mot", "sz": 5, "p": sida})
        dl = data["dokumentlista"]
        print(f"  Sida {sida}: träff {dl['@traff_fran']}–{dl['@traff_till']} "
              f"av {dl['@traffar']} | nästa → {dl.get('@nasta_sida', '—')[:60]}")


# ---------------------------------------------------------------------------
# Test 6: Fritextsökning
# ---------------------------------------------------------------------------

def test_fritextsokning(query: str = "klimatlag"):
    print_section(f"Test 6: Fritextsökning — '{query}'")
    data = get_json("/dokumentlista/", {"sok": query, "sz": 5})
    dl = data["dokumentlista"]
    print(f"  Träffar: {dl['@traffar']}")

    docs = dl.get("dokument", [])
    if isinstance(docs, dict):
        docs = [docs]
    for doc in docs:
        print(f"  [{doc['doktyp']}] {doc['datum']} — {doc['titel'][:70]}")


# ---------------------------------------------------------------------------
# Test 7: Anföranden
# ---------------------------------------------------------------------------

def test_anforanden():
    print_section("Test 7: Anföranden (lista + enskilt)")
    data = get_json("/anforandelista/", {"rm": "2024/25", "sz": 2})
    al = data["anforandelista"]

    anf_list = al.get("anforande", [])
    if isinstance(anf_list, dict):
        anf_list = [anf_list]

    print(f"  Antal i svaret: {al.get('@antal', '?')}")
    for anf in anf_list:
        print(f"\n  {anf['dok_datum']} — {anf['talare']} ({anf['parti']})")
        print(f"    anforande_id : {anf['anforande_id']}")
        print(f"    avsnittsrubrik: {anf['avsnittsrubrik'][:60]}")
        text = anf.get("anforandetext", "")
        print(f"    anforandetext : {'(tom i lista — hämta enskilt)' if not text else text[:80]}")

    # Hämta enskilt anförande för fulltext
    if anf_list:
        first = anf_list[0]
        anf_id = f"{first['dok_id']}-{first['anforande_nummer']}"
        print(f"\n  Hämtar enskilt: /anforande/{anf_id}")
        r = httpx.get(f"{API_BASE}/anforande/{anf_id}", timeout=30)
        root = ET.fromstring(r.text)
        for child in root:
            if child.tag == "anforandetext" and child.text:
                print(f"  Fulltext (utdrag): {child.text[:200]}")


# ---------------------------------------------------------------------------
# Test 8: Voteringar
# ---------------------------------------------------------------------------

def test_voteringar():
    print_section("Test 8: Voteringar")
    data = get_json("/voteringlista/", {"rm": "2024/25", "sz": 3})
    vl = data["voteringlista"]

    voteringar = vl.get("votering", [])
    if isinstance(voteringar, dict):
        voteringar = [voteringar]

    print(f"  Antal i svaret: {len(voteringar)}")
    for v in voteringar[:2]:
        print(f"\n  {v['namn']} ({v['parti']}) — {v['rost']}")
        print(f"    votering_id: {v['votering_id']}")
        print(f"    beteckning : {v['beteckning']}, punkt {v['punkt']}")
        print(f"    avser      : {v['avser']}")


# ---------------------------------------------------------------------------
# Huvudprogram
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Riksdagens öppna data API — Utforskningsskript")
    print(f"API-bas: {API_BASE}")

    try:
        test_dokumentlista()
        test_tackningskarta()
        test_protokoll_1867()
        test_enskilt_dokument("C09C516")   # Protokoll Första kammaren, 16 maj 1867
        test_paginering()
        test_fritextsokning("klimatlag")
        test_anforanden()
        test_voteringar()

        print()
        print("=" * 60)
        print("  Alla tester klara ✓")
        print("=" * 60)

    except httpx.HTTPError as e:
        print(f"\nHTTP-fel: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nAvbruten.", file=sys.stderr)
        sys.exit(0)
