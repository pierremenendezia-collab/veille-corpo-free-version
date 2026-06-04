"""
Construit le digest quotidien Markdown à partir des résultats bruts des fetchers.
"""

import json
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "outputs"

MA_KEYWORDS = [
    "acqui", "merger", "takeover", "transaction", "divestiture", "divest",
    "strategic review", "combination", "joint venture", "partnership",
    "acquisition", "cession", "rapprochement", "consolidation",
]

STRATEGY_KEYWORDS = [
    "guidance", "outlook", "forecast", "target", "strategy", "growth",
    "fleet", "order", "backlog", "pipeline", "expansion",
]


def score_filing(result: dict) -> int:
    """Score de priorité : plus c'est haut, plus c'est important."""
    score = 0
    text = (result.get("text_preview", "") + " " + result.get("description", "")).lower()

    if result.get("is_ma_signal"):
        score += 12
    if result.get("is_earnings"):
        score += 10
    if result.get("form") in ("10-K", "20-F"):
        score += 8
    if result.get("form") in ("10-Q", "6-K"):
        score += 5

    for kw in MA_KEYWORDS:
        if kw in text:
            score += 3
            break

    for kw in STRATEGY_KEYWORDS:
        if kw in text:
            score += 1
            break

    return score


def build_digest(results: list[dict]) -> str:
    today = date.today().isoformat()

    if not results:
        return f"# Veille Corporate — {today}\n\nAucune publication détectée.\n"

    # Tri par score décroissant
    sorted_results = sorted(results, key=score_filing, reverse=True)

    ma_signals = [r for r in sorted_results if r.get("is_ma_signal")]
    earnings = [r for r in sorted_results if r.get("is_earnings") and not r.get("is_ma_signal")]
    annual = [r for r in sorted_results if r.get("form") in ("10-K", "20-F")]
    quarterly = [r for r in sorted_results if r.get("form") in ("10-Q", "6-K") and not r.get("is_earnings")]
    other_8k = [r for r in sorted_results if r.get("form") == "8-K" and not r.get("is_earnings")]

    lines = [
        f"# Veille Corporate — {today}",
        "",
        f"**{len(results)} publication(s) détectée(s)** sur {len(set(r['company'] for r in results))} entreprise(s)",
        "",
    ]

    def format_filing(r: dict) -> list[str]:
        items_str = ""
        if r.get("8k_items"):
            items_str = f" *(Items: {', '.join(r['8k_items'])})*"
        preview = r.get("text_preview", "")[:200].replace("\n", " ")
        return [
            f"### {r['company']} (`{r['ticker']}`)",
            f"- **Type** : {r['form']}{items_str}",
            f"- **Date** : {r['date']}",
            f"- **Secteur** : {r.get('sector', 'N/A')}",
            f"- **Document** : [{r['url']}]({r['url']})",
            f"- **Aperçu** : {preview}..." if preview else "",
            "",
        ]

    if ma_signals:
        lines += [f"## Signaux M&A / Prises de participation ({len(ma_signals)})", ""]
        for r in ma_signals:
            lines += format_filing(r)

    if earnings:
        lines += [f"## Earnings Calls / Résultats ({len(earnings)})", ""]
        for r in earnings:
            lines += format_filing(r)

    if annual:
        lines += [f"## Rapports Annuels ({len(annual)})", ""]
        for r in annual:
            lines += format_filing(r)

    if quarterly:
        lines += [f"## Rapports Trimestriels / Semestriels ({len(quarterly)})", ""]
        for r in quarterly:
            lines += format_filing(r)

    if other_8k:
        lines += [f"## Autres 8-K / Événements Corporate ({len(other_8k)})", ""]
        for r in other_8k:
            lines += format_filing(r)

    return "\n".join(lines)


def save_digest(results: list[dict]) -> Path:
    today = date.today().isoformat()
    out_dir = OUTPUT_DIR / today
    out_dir.mkdir(parents=True, exist_ok=True)

    digest_path = out_dir / "digest.md"
    json_path = out_dir / "results.json"

    digest_path.write_text(build_digest(results), encoding="utf-8")
    json_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Digest sauvegardé : {digest_path}")
    return digest_path
