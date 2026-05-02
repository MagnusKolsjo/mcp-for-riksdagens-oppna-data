"""
03_explore_dokumentstatus.py — Kartlägger dokumentstatus XML-endpointens relationsdata

Hämtar dokumentstatus XML för representativa dokument av typerna:
  prop, mot, bet, ip, fr

och skriver ut alla relationsfält som finns.

Körning:
    cd ~/MCP-Servers/riksdag-oppna-data
    source .venv/bin/activate
    python 03_explore_dokumentstatus.py

Krav: httpx (ingår i requirements.txt)
"""

import json
import os
import sys
import xml.etree.ElementTree as ET

import httpx
from dotenv import load_dotenv

load_dotenv()

API_BASE = os.getenv("RIKSDAG_API_BASE", "https://data.riksdagen.se")

# Representativa dokument att undersöka
TESTDOKUMENT = {
    "prop":  "HD03158",    # Hela Sverige ska fungera 2025/26:158
    "mot":   "HD024055",   # Följdmotion till ovan
    "bet":   "HD01TU14",   # Betänkande TU14 2025/26
    "ip":    "HD10460",    # Interpellation om kulturarv
    "fr":    None,         # Fylls i automatiskt nedan
}


def hämta_dokumentstatus_xml(dok_id: str) -> ET.Element:
    """Hämtar dokumentstatus XML för ett dokument."""
    url = f"{API_BASE}/dokumentstatus/{dok_id}"
    r = httpx.get(url, timeout=30)
    r.raise_for_status()
    return ET.fromstring(r.text)


def skriv_fält_rekursivt(element: ET.Element, djup: int = 0, max_djup: int = 6) -> None:
    """Skriver ut XML-trädet med fokus på intressanta relationsfält."""
    if djup > max_djup:
        return
    indent = "  " * djup
    text = (element.text or "").strip()[:120]
    if text:
        print(f"{indent}<{element.tag}> = {repr(text)}")
    else:
        print(f"{indent}<{element.tag}>")
    for child in element:
        skriv_fält_rekursivt(child, djup + 1, max_djup)


def extrahera_relationer(root: ET.Element) -> dict:
    """
    Plockar ut alla fält som ser ut att handla om relationer:
    relaterat_id, relaterade_dokument, uppföljning, behandlas_i,
    hangar_id, relaterat_dok, ref, etc.
    """
    intressanta_taggar = {
        "relaterat_id", "relaterade", "relaterat_dok", "hangar_id",
        "relaterat", "uppfoljning", "behandlas_i", "referens",
        "ref_dok_id", "relaterat_dokument", "rel_dok",
        # Anföranden och protokoll
        "protokoll_url_www", "debatt_typ",
        # Svar på frågor och interpellationer
        "svar_dok_id", "fraga_dok_id", "interpellation_dok_id",
        # Betänkande-kopplingar
        "bet_beteckning", "prop_beteckning",
    }

    funna = {}
    for el in root.iter():
        if el.tag.lower() in intressanta_taggar or "relat" in el.tag.lower():
            text = (el.text or "").strip()
            if text:
                funna.setdefault(el.tag, []).append(text)

    return funna


def hitta_skriftlig_fraga() -> str | None:
    """Hämtar dok_id för en nylig skriftlig fråga."""
    r = httpx.get(
        f"{API_BASE}/dokumentlista/",
        params={"doktyp": "fr", "sz": 1, "utformat": "json"},
        timeout=30,
    )
    data = r.json()
    docs = data.get("dokumentlista", {}).get("dokument", [])
    if isinstance(docs, dict):
        docs = [docs]
    return docs[0]["dok_id"] if docs else None


def sektion(titel: str) -> None:
    print()
    print("=" * 65)
    print(f"  {titel}")
    print("=" * 65)


if __name__ == "__main__":
    print("Dokumentstatus XML — kartläggning av relationsfält")
    print(f"API: {API_BASE}")

    # Hitta en skriftlig fråga automatiskt
    print("\nHämtar dok_id för skriftlig fråga...")
    TESTDOKUMENT["fr"] = hitta_skriftlig_fraga()
    print(f"  fr = {TESTDOKUMENT['fr']}")

    for doktyp, dok_id in TESTDOKUMENT.items():
        if not dok_id:
            print(f"\nHoppar över {doktyp} (inget dok_id)")
            continue

        sektion(f"[{doktyp.upper()}] {dok_id}")

        try:
            root = hämta_dokumentstatus_xml(dok_id)

            # 1. Skriv ut hela trädet (max 4 nivåer)
            print("\n--- Fullständig XML-struktur (max 4 nivåer) ---")
            skriv_fält_rekursivt(root, max_djup=4)

            # 2. Extrahera relationsfält specifikt
            relationer = extrahera_relationer(root)
            if relationer:
                print("\n--- Relationsfält (alla fält med 'relat' i taggen) ---")
                for tag, värden in relationer.items():
                    for v in värden:
                        print(f"  {tag}: {v}")
            else:
                print("\n  (inga explicita relationsfält hittade)")

            # 3. Visa råa XML-noder för dokumentreferenser
            print("\n--- Alla dok_id-liknande värden i dokumentet ---")
            for el in root.iter():
                text = (el.text or "").strip()
                # Riksdags-dok_id är typiskt 5-10 tecken, alfanumeriska
                if (text and len(text) >= 5 and len(text) <= 12
                        and text.replace("/", "").isalnum()
                        and el.tag != "dok_id"):
                    print(f"  <{el.tag}>: {text}")

        except Exception as e:
            print(f"  FEL: {e}")

    print()
    print("Klar.")
