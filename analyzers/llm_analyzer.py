"""
Analyse chaque document SEC téléchargé via l'API Gemini (free tier).
Extrait : résultats financiers, signaux M&A, guidance, changements stratégiques.
"""

import re
import os
import json
from pathlib import Path
from google import genai

BASE_DIR = Path(__file__).parent.parent


def _load_gemini_config() -> dict:
    cfg_file = BASE_DIR / "config.json"
    if cfg_file.exists():
        return json.loads(cfg_file.read_text()).get("gemini", {})
    return {}


def _get_api_key() -> str | None:
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    cfg = _load_gemini_config()
    key = cfg.get("api_key")
    if key and key != "TON_API_KEY_GEMINI":
        return key
    return None


_api_key = _get_api_key()
_cfg = _load_gemini_config()
MODEL = os.environ.get("GEMINI_MODEL") or _cfg.get("model") or "gemini-flash-lite-latest"
client = genai.Client(api_key=_api_key) if _api_key else None


# Contexte injecté dans le prompt pour les formulaires structurellement M&A,
# afin que Gemini ne les sous-classe pas en ADMIN.
FORM_CONTEXT = {
    "SC 13D":   "Déclaration de prise de participation >5% avec intention active — signal M&A/activiste FORT.",
    "SC 13D/A": "Évolution d'une prise de participation active — signal M&A/activiste.",
    "S-4":      "Enregistrement d'une fusion par échange d'actions — signal M&A.",
    "F-4":      "Enregistrement d'une fusion par échange d'actions (émetteur étranger) — signal M&A.",
    "PREM14A":  "Projet de convocation d'AG pour approuver une fusion — signal M&A.",
    "DEFM14A":  "Convocation définitive d'AG pour approuver une fusion — signal M&A.",
}


ANALYSIS_PROMPT = """Document SEC ({form_type}) de {company} ({ticker}, {sector}).
{form_context}
{text}

Réponds en français avec EXACTEMENT cette structure (3 lignes maximum) :

NATURE: [3-6 mots décrivant le document. Ex: "Résultats trimestriels Q1", "Annonce dividende", "Acquisition navire", "Changement CFO"]
RESUME: [2-3 phrases concises résumant l'essentiel. Chiffres clés si présents.]
TAG: [Choisis UN seul tag parmi : M&A_STRATEGIE (acquisitions, cessions, partenariats, fusions, strategic review), EARNINGS (résultats financiers significatifs), GUIDANCE (prévisions, outlook), FINANCIER (dividende, émission, rachat actions), MANAGEMENT (changement dirigeant, gouvernance), ADMIN (formalité administrative sans contenu notable))]"""


def clean_html(html: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def truncate_for_analysis(text: str, max_chars: int = 50000) -> str:
    """Gemini gère 1M tokens, on peut être généreux avec le contexte."""
    return text[:max_chars] if len(text) > max_chars else text


def analyze_filing(result: dict) -> str:
    import time as _time

    local_file = result.get("local_file", "")
    if not local_file or not Path(local_file).exists():
        return "Document non disponible localement."

    raw_html = Path(local_file).read_text(encoding="utf-8", errors="ignore")
    text = clean_html(raw_html)
    text = truncate_for_analysis(text)

    if len(text) < 100:
        return "Document trop court ou vide."

    if client is None:
        return "Analyse indisponible — configure GEMINI_API_KEY ou gemini.api_key dans config.json"

    form = result.get("form", "")
    form_context = f"Contexte : {FORM_CONTEXT[form]}" if form in FORM_CONTEXT else ""
    prompt = ANALYSIS_PROMPT.format(
        form_type=form,
        form_context=form_context,
        company=result.get("company", ""),
        ticker=result.get("ticker", ""),
        sector=result.get("sector", ""),
        text=text,
    )

    # Retry avec backoff pour gérer les 503/429 (surcharge Gemini)
    last_error = None
    for attempt, delay in enumerate([0, 3, 8, 20]):
        if delay:
            _time.sleep(delay)
        try:
            response = client.models.generate_content(model=MODEL, contents=prompt)
            return response.text
        except Exception as e:
            last_error = e
            msg = str(e)
            if "503" in msg or "429" in msg or "UNAVAILABLE" in msg or "RESOURCE_EXHAUSTED" in msg:
                print(f"    retry {attempt + 1}/4 après {delay}s ({msg[:60]}...)")
                continue
            raise

    raise last_error


VALID_TAGS = {
    "M&A_STRATEGIE", "EARNINGS", "GUIDANCE",
    "FINANCIER", "MANAGEMENT", "ADMIN"
}

TAG_PRIORITY = {
    "M&A_STRATEGIE": 1,
    "EARNINGS": 2,
    "GUIDANCE": 3,
    "FINANCIER": 4,
    "MANAGEMENT": 5,
    "ADMIN": 9,
}


def parse_analysis(analysis: str) -> tuple[str, str, str]:
    """Extrait NATURE, RESUME et TAG depuis la réponse Gemini."""
    nature = ""
    resume = ""
    tag = "ADMIN"

    nature_match = re.search(r"NATURE\s*:\s*(.+?)(?:\n|$)", analysis, re.IGNORECASE)
    if nature_match:
        nature = nature_match.group(1).strip().strip("[]").strip()

    resume_match = re.search(r"RESUME\s*:\s*(.+?)(?=\nTAG\s*:|\Z)", analysis, re.IGNORECASE | re.DOTALL)
    if resume_match:
        resume = resume_match.group(1).strip().strip("[]").strip()

    tag_match = re.search(r"TAG\s*:\s*\[?([A-Z_&]+)\]?", analysis, re.IGNORECASE)
    if tag_match:
        candidate = tag_match.group(1).upper().strip()
        # Normalisation
        if "M" in candidate and "A" in candidate and "STRAT" in candidate:
            candidate = "M&A_STRATEGIE"
        if candidate in VALID_TAGS:
            tag = candidate

    return nature, resume, tag


def analyze_all(results: list[dict]) -> list[dict]:
    to_analyze = [r for r in results if r.get("local_file")]
    print(f"Analyse de {len(to_analyze)} document(s) via Gemini ({MODEL})...")

    import time as _t

    for i, result in enumerate(results):
        if not result.get("local_file"):
            result["nature"] = ""
            result["resume"] = ""
            result["tag"] = ""
            result["priority"] = 99
            continue

        print(f"  [{i+1}/{len(to_analyze)}] {result['company']} — {result['form']}...", flush=True)
        try:
            _t.sleep(0.5)
            raw = analyze_filing(result)
            nature, resume, tag = parse_analysis(raw)
            # Filet de sécurité : un formulaire structurellement M&A (SC 13D, S-4…)
            # ne doit jamais finir en ADMIN et être filtré de l'email.
            if result.get("is_ma_signal") and tag in ("ADMIN", ""):
                tag = "M&A_STRATEGIE"
            result["nature"] = nature
            result["resume"] = resume
            result["tag"] = tag
            result["priority"] = TAG_PRIORITY.get(tag, 9)
            print(f"    → [{tag}] {nature}", flush=True)
        except Exception as e:
            print(f"  [ERREUR] {e}", flush=True)
            result["nature"] = "Erreur d'analyse"
            result["resume"] = ""
            result["tag"] = "ADMIN"
            result["priority"] = 99

    return results
