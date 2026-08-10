import json, os, sys
import httpx
from dotenv import load_dotenv

load_dotenv()

API_BASE = os.getenv("RIKSDAG_API_BASE", "https://data.riksdagen.se")

HEADERS = {"User-Agent": "mcp-for-riksdagens-oppna-data/1.0 (+https://github.com/MagnusKolsjo/mcp-for-riksdagens-oppna-data)"}
OK = "OK"; FAIL = "FEL"; WARN = "?"


def get_json(path, params):
    params["utformat"] = "json"
    r = httpx.get(f"{API_BASE}{path}", params=params, timeout=30, headers=HEADERS)
    r.raise_for_status()
    return r.json()


def sep(title):
    print(); print("=" * 65); print(f"  {title}"); print("=" * 65)


def normalize(dl):
    docs = dl.get("dokument", [])
    return [docs] if isinstance(docs, dict) else (docs or [])


# ---------------------------------------------------------------------------
# Test 1: Fulltext per dokumenttyp
# ---------------------------------------------------------------------------
TYPER = {"prop": "Propositioner", "mot": "Motioner", "bet": "Betänkanden",
         "prot": "Protokoll", "sou": "SOU", "ds": "Ds", "dir": "Kommittédirektiv"}

def test_fulltext():
    sep("Test 1: Fulltext per dokumenttyp")
    for kod, namn in TYPER.items():
        data = get_json("/dokumentlista/", {"doktyp": kod, "sz": 1})
        docs = normalize(data["dokumentlista"])
        if not docs:
            print(f"  {FAIL} {namn:30s} ({kod}): inga dokument")
            continue
        doc = docs[0]
        har = bool(doc.get("dokument_url_text"))
        ocr = " [OCR]" if doc.get("status") == "ocr" else ""
        print(f"  {OK if har else WARN} {namn:30s} ({kod}): text_url={'ja' if har else 'NEJ'}{ocr}")


# ---------------------------------------------------------------------------
# Test 2: Historisk täckning 1867
# ---------------------------------------------------------------------------
def test_historik():
    sep("Test 2: Historisk täckning (1867)")
    for kod in ["prop", "prot", "mot", "bet"]:
        data = get_json("/dokumentlista/", {"doktyp": kod, "sz": 1,
                                            "sort": "datum", "sortorder": "asc"})
        docs = normalize(data["dokumentlista"])
        if not docs:
            print(f"  {WARN} {kod}: inga dokument"); continue
        doc = docs[0]
        datum = doc.get("datum", "?")
        ocr = " [OCR]" if doc.get("status") == "ocr" else ""
        mark = OK if datum[:4] <= "1868" else WARN
        print(f"  {mark} {kod}: äldst {datum}{ocr}")


# ---------------------------------------------------------------------------
# Test 3: SFS-sökbarhet (kritiskt for rd_resolve_sfs)
# ---------------------------------------------------------------------------
TESTFALL = [
    ("Ordningslag",     "1993:1617"),
    ("Forvaltningslag", "2017:900"),
    ("Kommunallag",     "2017:725"),
]

def test_sfs():
    sep("Test 3: SFS-sökbarhet -- avgörande för rd_resolve_sfs")
    print("  Söker promulgerade lagar, kollar om SFS-nummer finns i API-svaret.")
    print()

    sfs_falt = set()
    hittade  = 0

    for namn, forvantat in TESTFALL:
        print(f"  [{namn} / SFS {forvantat}]")

        # Sok pa lagnamnet
        data = get_json("/dokumentlista/", {"sok": namn, "sz": 10})
        docs = normalize(data["dokumentlista"])
        traffar = data["dokumentlista"].get("@traffar", "?")
        print(f"    Sökning '{namn}': {traffar} traffar totalt")

        hittad = False
        for doc in docs:
            sfs_rel = {k: v for k, v in doc.items()
                       if any(x in k.lower() for x in ["sfs", "beteckning", "nummer"]) and v}
            if sfs_rel:
                sfs_falt.update(sfs_rel.keys())
                print(f"    {OK} SFS-relaterade fält: {sfs_rel}")
                hittad = True
                break
            if forvantat in json.dumps(doc, ensure_ascii=False):
                print(f"    {OK} SFS {forvantat} finns i ett svarsfalt")
                hittad = True
                break

        if not hittad:
            print(f"    {WARN} Inget SFS-nummer hittades i träffarna")
            if docs:
                print(f"    Tillgängliga fält: {sorted(docs[0].keys())}")

        # Sok direkt pa SFS-numret
        data2 = get_json("/dokumentlista/", {"sok": forvantat, "sz": 3})
        docs2 = normalize(data2["dokumentlista"])
        traffar2 = data2["dokumentlista"].get("@traffar", "?")
        print(f"    Sökning '{forvantat}': {traffar2} traffar")
        for d in docs2[:2]:
            print(f"      [{d.get('doktyp','?')}] {d.get('titel','?')[:70]}")
        print()

        if hittad:
            hittade += 1

    print("-" * 65)
    if hittade == len(TESTFALL):
        print(f"  {OK} SLUTSATS: rd_resolve_sfs KAN byggas.")
        print(f"     SFS-nummer ar sökbara. Relevanta fält: {sfs_falt}")
    elif hittade > 0:
        print(f"  {WARN} SLUTSATS: Delvis stod ({hittade}/{len(TESTFALL)}). Undersok manuellt.")
    else:
        print(f"  {FAIL} SLUTSATS: rd_resolve_sfs KAN INTE byggas via riksdagens API.")
        print(f"     Losning: titelsokning mot SFSR-servern istallet.")


# ---------------------------------------------------------------------------
# Test 4: Betänkanden och riksdagsskrivelser
# ---------------------------------------------------------------------------
def test_kedja():
    sep("Test 4: Betänkanden och riksdagsskrivelser")
    print("  Testar kedjan prop → bet → rskr som alternativ väg till SFS-nummer.")
    print()

    data = get_json("/dokumentlista/", {"sok": "1992/93:210", "doktyp": "bet", "sz": 5})
    docs = normalize(data["dokumentlista"])
    print(f"  Betänkanden som nämner '1992/93:210': {data['dokumentlista'].get('@traffar', 0)}")
    for doc in docs[:3]:
        print(f"    [{doc.get('doktyp','?')}] {doc.get('datum','')} -- {doc.get('titel','?')[:65]}")
        print(f"      dok_id: {doc.get('dok_id','?')}")

    data2 = get_json("/dokumentlista/", {"sok": "ordningslag", "doktyp": "rskr", "sz": 3})
    traffar2 = data2["dokumentlista"].get("@traffar", "?")
    print(f"  Riksdagsskrivelser om ordningslag: {traffar2} traffar")
    for doc in normalize(data2["dokumentlista"])[:2]:
        print(f"    {doc.get('datum','')} -- {doc.get('titel','?')[:65]}")


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("Riksdagens API -- Täcknings- och kapabilitetsverifiering")
    print(f"API-bas: {API_BASE}")
    try:
        test_fulltext()
        test_historik()
        test_sfs()
        test_kedja()
        print()
        sep("Klart — granska Test 3 for beslut om rd_resolve_sfs")
    except httpx.HTTPError as e:
        print(f"HTTP-fel: {e}", file=sys.stderr); sys.exit(1)
    except KeyboardInterrupt:
        print("Avbruten."); sys.exit(0)
