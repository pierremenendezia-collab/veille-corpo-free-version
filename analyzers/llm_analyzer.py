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


# Seconde passe : « lecture entre les lignes » du discours management. Le premier
# prompt classe et résume ; celui-ci cherche les signaux PROSPECTIFS qu'un analyste
# sell-side lirait dans les propos du CEO/CFO. Garde anti-hallucination en fin.
STRATEGIC_PROMPT = """Tu es analyste sell-side spécialisé transport maritime / logistique / infrastructures.
Document SEC ({form_type}) de {company} ({ticker}, {sector}).

{text}

Au-delà des chiffres bruts, lis ENTRE LES LIGNES les propos du management (CEO/CFO).
Réponds en français, UNIQUEMENT dans ce format :

SIGNAUX_PROSPECTIFS:
- [3 à 5 puces. Chaque puce = un signal concret tourné vers l'avenir : guidance/outlook (et son ton), allocation du capital (dividende, rachat, désendettement, capex), flotte/capacité (commandes, ventes d'actifs, expansion), M&A/consolidation évoquée, vision du cycle/marché. Cite un chiffre ou une formulation du document quand c'est possible.]
ANTICIPATION: [1-2 phrases : qu'est-ce que ça laisse présager pour les 2-3 prochains trimestres ?]
CONVICTION_MANAGEMENT: [Haute / Moyenne / Faible — degré d'engagement du discours]

IMPÉRATIF : appuie-toi UNIQUEMENT sur le contenu du document ci-dessus. N'invente aucun chiffre ni citation. Si le document ne contient pas de matière prospective exploitable (page de garde, simple avis, formalité), réponds EXACTEMENT :
SIGNAUX_PROSPECTIFS:
- Pas de signal prospectif exploitable dans ce document.
ANTICIPATION: N/A
CONVICTION_MANAGEMENT: N/A
"""


def clean_html(html: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def truncate_for_analysis(text: str, max_chars: int = 50000) -> str:
    """Gemini gère 1M tokens, on peut être généreux avec le contexte."""
    return text[:max_chars] if len(text) > max_chars else text


def _generate_with_retry(prompt: str) -> str:
    """Appel Gemini avec backoff pour absorber les 503/429 (surcharge du free tier)."""
    import time as _time

    last_error = None
    for attempt, delay in enumerate([0, 3, 8, 20]):
        if delay:
            _time.sleep(delay)
        try:
            return client.models.generate_content(model=MODEL, contents=prompt).text
        except Exception as e:
            last_error = e
            msg = str(e)
            if "503" in msg or "429" in msg or "UNAVAILABLE" in msg or "RESOURCE_EXHAUSTED" in msg:
                print(f"    retry {attempt + 1}/4 après {delay}s ({msg[:60]}...)")
                continue
            raise
    raise last_error


def _load_doc_text(result: dict) -> str | None:
    """Lit le document local, nettoie le HTML et tronque. None si absent/trop court."""
    local_file = result.get("local_file", "")
    if not local_file or not Path(local_file).exists():
        return None
    raw_html = Path(local_file).read_text(encoding="utf-8", errors="ignore")
    text = truncate_for_analysis(clean_html(raw_html))
    return text if len(text) >= 100 else None


def analyze_filing(result: dict) -> str:
    text = _load_doc_text(result)
    if text is None:
        return "Document non disponible ou trop court."
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
    return _generate_with_retry(prompt)


def strategic_analysis(result: dict) -> str:
    """
    Seconde passe « lecture entre les lignes » : signaux prospectifs du discours
    management (guidance et son ton, allocation du capital, flotte/capacité, M&A
    évoquée, vision du cycle). Renvoie "" en cas d'échec ou de document
    inexploitable — c'est une passe d'appoint qui ne doit jamais casser le pipeline.
    """
    if client is None:
        return ""
    text = _load_doc_text(result)
    if text is None:
        return ""
    prompt = STRATEGIC_PROMPT.format(
        form_type=result.get("form", ""),
        company=result.get("company", ""),
        ticker=result.get("ticker", ""),
        sector=result.get("sector", ""),
        text=text,
    )
    try:
        return _generate_with_retry(prompt).strip()
    except Exception as e:
        print(f"    [strat ERREUR] {str(e)[:60]}", flush=True)
        return ""


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
            result["strategic"] = ""
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
            # Lecture stratégique « entre les lignes » sur tout document substantiel.
            result["strategic"] = strategic_analysis(result) if tag and tag != "ADMIN" else ""
            extra = " +strat" if result["strategic"] else ""
            print(f"    → [{tag}]{extra} {nature}", flush=True)
        except Exception as e:
            print(f"  [ERREUR] {e}", flush=True)
            result["nature"] = "Erreur d'analyse"
            result["resume"] = ""
            result["tag"] = "ADMIN"
            result["priority"] = 99
            result["strategic"] = ""

    return results


# Relecture globale finale du mail. Il arrive (rarement) qu'une même news soit
# déclinée par l'entreprise en plusieurs dépôts quasi identiques (ex. annonce de
# cotation déposée 2-3 fois). Gemini relit l'ensemble et signale les doublons à
# retirer. Conservateur : au moindre doute, on ne retire rien.
DEDUP_PROMPT = """Voici les publications retenues pour l'e-mail de veille du jour, numérotées.
Certaines peuvent décrire EXACTEMENT le même événement sous-jacent (même entreprise, même news redéposée en plusieurs exemplaires quasi identiques). Ton rôle : repérer ces doublons pour n'en garder qu'un seul par événement.

{listing}

Règles STRICTES :
- Ne regroupe QUE des publications de la MÊME entreprise décrivant le MÊME événement.
- En cas de doute, NE RETIRE RIEN. Mieux vaut un doublon qu'une information perdue.
- Dans un groupe de doublons, GARDE la plus complète (résumé le plus riche, signaux présents) et retire les autres.
- Ne retire JAMAIS une publication qui apporte une information distincte.

Réponds UNIQUEMENT par les numéros entre crochets à RETIRER, séparés par des virgules (ex : [2], [5]).
S'il n'y a aucun doublon, réponds EXACTEMENT : AUCUN"""


def deduplicate_for_email(results: list[dict]) -> list[dict]:
    """
    Relecture globale finale : Gemini repère les publications décrivant le MÊME
    événement (même entreprise, news redéposée en plusieurs exemplaires) et on
    retire les redondantes en gardant la plus complète. Conservateur par
    construction (au moindre doute, rien n'est retiré). S'applique à l'e-mail
    uniquement — results.json conserve la trace complète.
    """
    if client is None or len(results) < 2:
        return results

    from collections import Counter
    counts = Counter(r.get("company", "") for r in results)
    if not any(n >= 2 for n in counts.values()):
        return results  # aucune entreprise en double → rien à dédupliquer

    lines = []
    for i, r in enumerate(results):
        resume = (r.get("resume") or "").replace("\n", " ")[:200]
        lines.append(
            f"[{i}] {r.get('company','')} — {r.get('form','')} {r.get('date','')}"
            f" · {r.get('tag','')} · {r.get('nature','')} — {resume}"
        )
    listing = "\n".join(lines)

    try:
        raw = _generate_with_retry(DEDUP_PROMPT.format(listing=listing)).strip()
    except Exception as e:
        print(f"  [dedup ERREUR] {str(e)[:60]} — aucun retrait", flush=True)
        return results

    if "AUCUN" in raw.upper():
        return results

    # Parsing STRICT : on n'accepte que des indices entre crochets.
    to_remove = {int(m) for m in re.findall(r"\[(\d+)\]", raw)}
    valid = {i for i in to_remove if 0 <= i < len(results)}
    if not valid or len(valid) >= len(results):
        return results  # rien d'exploitable, ou tentative de tout retirer

    kept = [r for i, r in enumerate(results) if i not in valid]
    # Garde-fou : ne jamais faire disparaître complètement une entreprise du mail.
    if {r.get("company", "") for r in kept} != {r.get("company", "") for r in results}:
        print("  [dedup] retrait annulé (ferait disparaître une entreprise)", flush=True)
        return results

    for i in sorted(valid):
        r = results[i]
        print(f"  [dedup] doublon retiré : {r.get('company','')} — {r.get('form','')} · {r.get('nature','')}", flush=True)

    return kept
