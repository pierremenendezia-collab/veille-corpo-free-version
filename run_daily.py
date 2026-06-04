"""
Point d'entrée principal de la routine quotidienne.
- Récupère les dépôts SEC des derniers jours
- Les analyse via Gemini
- Envoie un email UNIQUEMENT si au moins 1 publication a un intérêt >= seuil
- Le vendredi : envoie en plus un récap hebdomadaire
Usage : python3 run_daily.py [--lookback 2] [--no-download] [--no-email] [--force-weekly]
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fetchers.sec_fetcher import run as sec_run
from analyzers.report_builder import save_digest
from analyzers.llm_analyzer import analyze_all, deduplicate_for_email
from delivery.email_sender import send as send_daily
from delivery.weekly_recap import send_weekly

CONFIG_FILE = Path(__file__).parent / "config.json"


def load_schedule_config() -> dict:
    # Defaults — utilisés tels quels en cloud (pas de config.json sur GitHub Actions)
    cfg = {}
    if CONFIG_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text()).get("schedule", {})
    return {
        "lookback_days": cfg.get("lookback_days", 2),
        "min_interest_daily": cfg.get("min_interest_daily", 2),
        "min_interest_weekly": cfg.get("min_interest_weekly", 2),
        "weekly_recap_weekday": cfg.get("weekly_recap_weekday", 4),  # vendredi
    }


def main():
    parser = argparse.ArgumentParser(description="Veille corporate quotidienne")
    parser.add_argument("--lookback", type=int, default=None,
                        help="Nombre de jours à regarder en arrière (défaut: config.json)")
    parser.add_argument("--no-download", action="store_true",
                        help="Ne pas télécharger les documents (plus rapide, pas d'analyse)")
    parser.add_argument("--no-email", action="store_true",
                        help="Ne pas envoyer d'email (test local)")
    parser.add_argument("--force-weekly", action="store_true",
                        help="Force l'envoi du récap hebdo (sinon uniquement le vendredi)")
    args = parser.parse_args()

    sched = load_schedule_config()
    lookback = args.lookback if args.lookback is not None else sched["lookback_days"]

    print("=" * 60)
    print(f"VEILLE CORPORATE — {date.today().isoformat()}")
    print(f"Lookback : {lookback} jour(s) · Filtre : tag != ADMIN")
    print("=" * 60)

    # ÉTAPE 1 — Fetch
    print("\n[1/3] Fetch SEC EDGAR...")
    results = sec_run(lookback_days=lookback, download_docs=not args.no_download)

    # ÉTAPE 2 — Analyse
    if results and not args.no_download:
        print("\n[2/3] Analyse des documents par Gemini...")
        results = analyze_all(results)
    else:
        print("\n[2/3] Analyse ignorée")

    print("\n[3/3] Sauvegarde locale + décision d'envoi...")
    digest_path = save_digest(results)

    # ÉTAPE 3 — Décision d'envoi quotidien : on envoie uniquement les non-ADMIN
    interesting = [r for r in results if r.get("tag") and r["tag"] != "ADMIN"]
    n_ma = sum(1 for r in interesting if r.get("tag") == "M&A_STRATEGIE")
    print(f"  {len(results)} publication(s) totales · {len(interesting)} retenue(s) pour l'email · {n_ma} M&A")

    if args.no_email:
        print("  Email ignoré (--no-email actif)")
    elif interesting:
        # Relecture globale finale : retire les doublons (même news redéposée).
        deduped = deduplicate_for_email(interesting)
        if len(deduped) < len(interesting):
            print(f"  Dédup : {len(interesting)} → {len(deduped)} publication(s) après relecture")
        send_daily("", deduped)
    else:
        print("  Aucune publication notable → pas d'email quotidien envoyé.")

    # ÉTAPE 4 — Récap hebdomadaire le vendredi
    is_friday = date.today().weekday() == sched["weekly_recap_weekday"]
    if (is_friday or args.force_weekly) and not args.no_email:
        print(f"\n[Récap hebdo {'(vendredi)' if is_friday else '(forcé)'}]")
        send_weekly(lookback_days=7)

    print("\n" + "=" * 60)
    print(f"Terminé. {len(results)} dépôt(s) traité(s) · rapport local : {digest_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
