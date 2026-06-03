"""
Récap hebdomadaire envoyé chaque vendredi.
Agrège tous les results.json des 7 derniers jours, garde uniquement les publications
dont le tag n'est pas ADMIN, groupe par entreprise.
"""

import json
from datetime import date, timedelta
from pathlib import Path

from delivery.email_sender import (
    load_config, group_by_company, build_company_card,
    build_summary, TAG_COLORS, TAG_LABELS
)

BASE_DIR = Path(__file__).parent.parent
OUTPUTS_DIR = BASE_DIR / "outputs"


def load_weekly_results(lookback_days: int = 7) -> list[dict]:
    today = date.today()
    all_results = []
    seen = set()  # déduplication par URL

    for i in range(lookback_days):
        d = today - timedelta(days=i)
        results_file = OUTPUTS_DIR / d.isoformat() / "results.json"
        if not results_file.exists():
            continue
        try:
            for r in json.loads(results_file.read_text()):
                url = r.get("url", "")
                if url and url not in seen:
                    seen.add(url)
                    all_results.append(r)
        except Exception as e:
            print(f"  [WARN] {results_file}: {e}")

    return all_results


def build_weekly_html(results: list[dict], start: date, end: date) -> str:
    if not results:
        body = '<p style="color:#718096;text-align:center;padding:40px">Aucune publication notable cette semaine.</p>'
    else:
        groups = group_by_company(results)
        n_companies = len(groups)
        n_ma = sum(1 for r in results if r.get("tag") == "M&A_STRATEGIE")

        intro = f"""
        <div style="background:#f7fafc;border-radius:8px;padding:24px 28px;margin-bottom:28px">
          <div style="font-size:13px;color:#718096;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;font-weight:600">Synthèse</div>
          <div style="font-size:15px;color:#2d3748;line-height:1.7">
            <strong>{len(results)} publication(s) notable(s)</strong> sur <strong>{n_companies} entreprise(s)</strong>
            {f"· <strong style='color:#c53030'>{n_ma} M&amp;A / Stratégie</strong>" if n_ma else ""}
          </div>
        </div>"""

        cards = "\n".join(build_company_card(g) for g in groups)
        body = intro + build_summary(groups) + cards

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,sans-serif;max-width:780px;margin:0 auto;padding:24px;background:#fafafa;color:#1a202c">

  <div style="background:linear-gradient(135deg,#5a2a82,#2b6cb0);color:white;padding:28px;border-radius:8px;margin-bottom:28px">
    <div style="font-size:13px;opacity:0.8;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">Récap hebdomadaire</div>
    <div style="font-size:22px;font-weight:600">Semaine du {start.strftime('%d/%m')} au {end.strftime('%d/%m/%Y')}</div>
  </div>

  {body}

  <div style="margin-top:32px;padding-top:20px;border-top:1px solid #e2e8f0;color:#a0aec0;font-size:11px;text-align:center">
    Sources : SEC EDGAR · Analyses : Gemini · Récap hebdo envoyé chaque vendredi
  </div>
</body>
</html>"""


def send_weekly(lookback_days: int = 7) -> bool:
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    today = date.today()
    start = today - timedelta(days=lookback_days - 1)

    all_results = load_weekly_results(lookback_days)
    interesting = [r for r in all_results if r.get("tag") and r["tag"] != "ADMIN"]

    print(f"  Récap hebdo : {len(all_results)} pub. totales · {len(interesting)} retenues (tag != ADMIN)")

    if not interesting:
        print("  Aucune publication intéressante cette semaine, pas de récap envoyé.")
        return False

    cfg = load_config()
    if cfg["sender"] == "TON_EMAIL@gmail.com":
        return False

    n_companies = len(set(r["company"] for r in interesting))
    n_ma = sum(1 for r in interesting if r.get("tag") == "M&A_STRATEGIE")

    subject_parts = [
        f"Récap semaine · {start.strftime('%d/%m')} → {today.strftime('%d/%m')}",
        f"{n_companies} entreprise(s)",
    ]
    if n_ma:
        subject_parts.append(f"🔴 {n_ma} M&A")
    subject = " · ".join(subject_parts)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg["sender"]
    msg["To"] = cfg["recipient"]
    msg.attach(MIMEText(build_weekly_html(interesting, start, today), "html"))

    try:
        with smtplib.SMTP(cfg["smtp_server"], cfg["smtp_port"]) as server:
            server.starttls()
            server.login(cfg["sender"], cfg["app_password"])
            server.sendmail(cfg["sender"], cfg["recipient"], msg.as_string())
        print(f"  Récap hebdo envoyé à {cfg['recipient']}")
        return True
    except Exception as e:
        print(f"  [ERREUR récap] {e}")
        return False
