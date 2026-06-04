"""
SEC EDGAR fetcher — récupère les nouveaux dépôts pour les entreprises US/BM/MH
listées sur NYSE/NASDAQ qui ont un CIK SEC.

Types de documents surveillés :
  8-K  item 2.02 → earnings results (le plus important)
  8-K  autre     → événements corporate (M&A, management, etc.)
  10-K           → rapport annuel
  10-Q           → rapport trimestriel
  6-K            → équivalent 8-K pour foreign private issuers
  20-F           → rapport annuel pour foreign private issuers
"""

import json
import time
import re
import os
from datetime import date, timedelta
from pathlib import Path

import requests

# SEC exige un User-Agent identifiable
HEADERS = {
    "User-Agent": "veille-corpo-research contact@example.com",
    "Accept-Encoding": "gzip, deflate",
}

FILING_TYPES_WATCHED = {
    # Rapports périodiques
    "8-K", "10-K", "10-Q", "6-K", "20-F",
    # Signaux M&A / opérations stratégiques
    "SC 13D", "SC 13D/A",   # prise de participation >5% avec intention active
    "S-4", "F-4",           # opération par échange d'actions (fusion)
    "PREM14A", "DEFM14A",   # convocation d'AG pour approuver une fusion
}

# Formulaires qui sont, par nature, des signaux M&A / stratégiques
MA_SIGNAL_FORMS = {"SC 13D", "SC 13D/A", "S-4", "F-4", "PREM14A", "DEFM14A"}

# Mots-clés pour repérer un communiqué de résultats dans un 6-K
# (les Foreign Private Issuers n'ont pas de système d'items comme les 8-K)
EARNINGS_KEYWORDS_6K = [
    "net income", "net profit", "net loss", "revenue", "ebitda",
    "earnings per share", "per share", "operating income",
    "first quarter", "second quarter", "third quarter", "fourth quarter",
    "interim results", "financial results", "quarterly results",
    "half-year", "full year results",
]

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "outputs"
COMPANIES_FILE = BASE_DIR / "companies.json"


def load_sec_companies() -> list[dict]:
    companies = json.loads(COMPANIES_FILE.read_text())
    return [c for c in companies if "sec_edgar" in c.get("fetch_strategy", [])]


def get_recent_filings(cik: str, lookback_days: int = 2) -> list[dict]:
    """
    Interroge l'API EDGAR pour un CIK et retourne les dépôts récents.
    lookback_days=2 pour ne pas rater un dépôt fait tard vendredi soir.
    """
    cik_padded = cik.zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"

    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  [ERREUR] EDGAR CIK {cik}: {e}")
        return []

    data = r.json()
    recent = data.get("filings", {}).get("recent", {})

    if not recent:
        return []

    cutoff = date.today() - timedelta(days=lookback_days)

    filings = []
    dates = recent.get("filingDate", [])
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    descriptions = recent.get("primaryDocument", [])
    doc_descriptions = recent.get("primaryDocDescription", [])

    for i, filing_date_str in enumerate(dates):
        filing_date = date.fromisoformat(filing_date_str)
        if filing_date < cutoff:
            # Les filings sont triés du plus récent au plus ancien
            break

        form_type = forms[i] if i < len(forms) else ""
        if form_type not in FILING_TYPES_WATCHED:
            continue

        accession = accessions[i] if i < len(accessions) else ""
        primary_doc = descriptions[i] if i < len(descriptions) else ""
        doc_desc = doc_descriptions[i] if i < len(doc_descriptions) else ""

        filings.append({
            "date": filing_date_str,
            "form": form_type,
            "accession": accession,
            "primary_doc": primary_doc,
            "description": doc_desc,
            "url": build_filing_url(cik_padded, accession, primary_doc),
            "index_url": build_index_url(cik_padded, accession),
        })

    return filings


def build_filing_url(cik_padded: str, accession: str, primary_doc: str) -> str:
    accession_clean = accession.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik_padded)}/"
        f"{accession_clean}/{primary_doc}"
    )


def build_index_url(cik_padded: str, accession: str) -> str:
    accession_clean = accession.replace("-", "")
    return (
        f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
        f"&CIK={int(cik_padded)}&type=&dateb=&owner=include&count=10"
        f"&search_text=#filing-{accession}"
    )


def fetch_document_text(url: str) -> str | None:
    """Télécharge le contenu texte d'un document SEC (HTML ou TXT)."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        content_type = r.headers.get("Content-Type", "")
        if "html" in content_type or "text" in content_type:
            return r.text
        return None
    except requests.RequestException as e:
        print(f"  [ERREUR] téléchargement {url}: {e}")
        return None


def get_8k_items(text: str) -> list[str]:
    """Extrait les numéros d'items d'un 8-K pour identifier les earnings (2.02)."""
    items = re.findall(r"Item\s+(\d+\.\d+)", text, re.IGNORECASE)
    return list(set(items))


def looks_like_earnings_6k(text: str) -> bool:
    """
    6-K : les Foreign Private Issuers (shipping MH/BM, etc.) déposent leurs
    résultats trimestriels en 6-K, sans système d'items. On détecte donc le
    communiqué de résultats par mots-clés (>= 2 occurrences pour limiter le bruit).
    """
    low = text.lower()
    hits = sum(1 for kw in EARNINGS_KEYWORDS_6K if kw in low)
    return hits >= 2


def run(lookback_days: int = 2, download_docs: bool = True) -> list[dict]:
    """
    Point d'entrée principal.
    Retourne une liste de résultats structurés pour le rapport.
    """
    companies = load_sec_companies()
    print(f"Surveillance SEC EDGAR : {len(companies)} entreprises")

    today_str = date.today().isoformat()
    out_dir = OUTPUT_DIR / today_str / "raw" / "sec"
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []

    for company in companies:
        cik = company.get("sec_cik", "").lstrip("0")
        if not cik:
            print(f"  [SKIP] {company['name']} — pas de CIK")
            continue

        print(f"  Fetching {company['name']} (CIK {cik})...")
        filings = get_recent_filings(cik, lookback_days)

        for filing in filings:
            result = {
                "company": company["name"],
                "ticker": company["ticker"],
                "sector": company.get("sector", ""),
                "date": filing["date"],
                "form": filing["form"],
                "description": filing["description"],
                "url": filing["url"],
                "index_url": filing["index_url"],
                "is_earnings": False,
                "is_ma_signal": filing["form"] in MA_SIGNAL_FORMS,
                "8k_items": [],
                "text_preview": "",
                "local_file": "",
            }

            if download_docs and filing["primary_doc"].endswith((".htm", ".html", ".txt")):
                text = fetch_document_text(filing["url"])
                if text:
                    # Détection earnings selon le type d'émetteur
                    if filing["form"] == "8-K":
                        # Domestic : earnings = 8-K item 2.02
                        items = get_8k_items(text)
                        result["8k_items"] = items
                        result["is_earnings"] = "2.02" in items
                    elif filing["form"] == "6-K":
                        # FPI : pas d'items, détection par contenu
                        result["is_earnings"] = looks_like_earnings_6k(text)

                    # Extrait un aperçu des 500 premiers caractères de texte brut
                    clean = re.sub(r"<[^>]+>", " ", text)
                    clean = re.sub(r"\s+", " ", clean).strip()
                    result["text_preview"] = clean[:500]

                    # Sauvegarde locale
                    safe_name = re.sub(r"[^\w]", "_", company["name"])
                    filename = f"{filing['date']}_{filing['form']}_{safe_name}.html"
                    local_path = out_dir / filename
                    local_path.write_text(text, encoding="utf-8")
                    result["local_file"] = str(local_path)

            results.append(result)

        # Respect du rate limit SEC (10 req/s max)
        time.sleep(0.15)

    print(f"\nTotal nouveaux dépôts trouvés : {len(results)}")
    return results


if __name__ == "__main__":
    results = run(lookback_days=2, download_docs=True)
    for r in results:
        marker = " *** EARNINGS ***" if r["is_earnings"] else ""
        print(f"  [{r['date']}] {r['company']} — {r['form']}{marker}")
        if r["8k_items"]:
            print(f"    Items 8-K : {', '.join(r['8k_items'])}")
        print(f"    {r['url']}")
