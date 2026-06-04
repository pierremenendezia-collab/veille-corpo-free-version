"""
Envoie le digest quotidien par email via Gmail SMTP.
Groupé par entreprise. Entreprises avec tag M&A/STRATEGIE en haut.
"""

import json
import re
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
CONFIG_FILE = BASE_DIR / "config.json"

TAG_COLORS = {
    "M&A_STRATEGIE": "#c53030",
    "EARNINGS":      "#dd6b20",
    "GUIDANCE":      "#2b6cb0",
    "FINANCIER":     "#319795",
    "MANAGEMENT":    "#6b46c1",
    "ADMIN":         "#718096",
}

TAG_LABELS = {
    "M&A_STRATEGIE": "M&A / Stratégie",
    "EARNINGS":      "Résultats",
    "GUIDANCE":      "Guidance",
    "FINANCIER":     "Financier",
    "MANAGEMENT":    "Management",
    "ADMIN":         "Administratif",
}


def load_config() -> dict:
    """Charge la config email. Priorité aux variables d'environnement (GitHub Actions)."""
    import os
    # Si env vars présentes (GitHub Actions), on les utilise
    if os.environ.get("GMAIL_SENDER") and os.environ.get("GMAIL_APP_PASSWORD"):
        return {
            "smtp_server": "smtp.gmail.com",
            "smtp_port": 587,
            "sender": os.environ["GMAIL_SENDER"],
            "app_password": os.environ["GMAIL_APP_PASSWORD"],
            "recipient": os.environ.get("GMAIL_RECIPIENT", os.environ["GMAIL_SENDER"]),
        }
    # Sinon, on lit config.json (local)
    return json.loads(CONFIG_FILE.read_text())["email"]


def group_by_company(results: list[dict]) -> list[dict]:
    """Groupe les publications par entreprise et calcule la priorité du groupe."""
    grouped: dict[str, dict] = {}
    for r in results:
        company = r["company"]
        if company not in grouped:
            grouped[company] = {
                "company": company,
                "ticker": r.get("ticker", ""),
                "sector": r.get("sector", ""),
                "items": [],
                "best_priority": 99,
                "tags": set(),
            }
        grouped[company]["items"].append(r)
        prio = r.get("priority", 99)
        if prio < grouped[company]["best_priority"]:
            grouped[company]["best_priority"] = prio
        if r.get("tag"):
            grouped[company]["tags"].add(r["tag"])

    # Tri intra-groupe par priorité
    for g in grouped.values():
        g["items"].sort(key=lambda r: r.get("priority", 99))

    # Tri global par best_priority
    return sorted(grouped.values(), key=lambda g: g["best_priority"])


def _clean_inline(s: str) -> str:
    """Texte issu de Gemini → sûr en HTML : enlève le gras markdown, échappe < > &."""
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s).replace("*", "")
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s.strip()


def _parse_strategic(text: str) -> tuple[list[str], str, str]:
    """Découpe la réponse stratégique en (signaux, anticipation, conviction)."""
    signals: list[str] = []
    m = re.search(r"SIGNAUX_PROSPECTIFS\s*:\s*(.*?)(?=ANTICIPATION\s*:|CONVICTION_MANAGEMENT\s*:|\Z)",
                  text, re.IGNORECASE | re.DOTALL)
    if m:
        for line in m.group(1).splitlines():
            line = line.strip().lstrip("-•*").strip()
            if line:
                signals.append(line)

    anticipation = ""
    m = re.search(r"ANTICIPATION\s*:\s*(.*?)(?=CONVICTION_MANAGEMENT\s*:|\Z)", text, re.IGNORECASE | re.DOTALL)
    if m:
        anticipation = m.group(1).strip()
        if anticipation.upper().strip("[]. ") == "N/A":
            anticipation = ""

    conviction = ""
    m = re.search(r"CONVICTION_MANAGEMENT\s*:\s*(.+?)(?:\n|$)", text, re.IGNORECASE)
    if m:
        conviction = m.group(1).strip().strip("[]").strip()
        if conviction.upper() == "N/A":
            conviction = ""

    return signals, anticipation, conviction


def render_strategic(text: str) -> str:
    """Encart 'Lecture stratégique' (signaux prospectifs + anticipation + conviction).
    Renvoie "" si rien d'exploitable, pour ne pas alourdir l'email."""
    if not text or not text.strip():
        return ""

    signals, anticipation, conviction = _parse_strategic(text)
    if signals and "pas de signal" in signals[0].lower() and not anticipation:
        return ""
    if not signals and not anticipation and not conviction:
        return ""

    bullets = "".join(f'<li style="margin:3px 0">{_clean_inline(s)}</li>' for s in signals)
    parts = []
    if bullets:
        parts.append(f'<ul style="margin:4px 0;padding-left:18px;color:#2d3748;font-size:13px;line-height:1.5">{bullets}</ul>')
    if anticipation:
        parts.append(f'<div style="font-size:12px;color:#4a5568;margin-top:6px"><strong style="color:#1a365d">Anticipation :</strong> {_clean_inline(anticipation)}</div>')
    if conviction:
        cv_first = conviction.split()[0].lower().strip("():,.") if conviction.split() else ""
        cv_color = {"haute": "#2f855a", "moyenne": "#dd6b20", "faible": "#a0aec0"}.get(cv_first, "#718096")
        parts.append(f'<div style="font-size:12px;color:#4a5568;margin-top:3px"><strong style="color:#1a365d">Conviction management :</strong> <span style="color:{cv_color};font-weight:600">{_clean_inline(conviction)}</span></div>')

    return f"""
      <div style="margin-top:10px;padding:12px 14px;background:#f0f5fa;border-radius:6px;border:1px dashed #bcccdc">
        <div style="font-size:11px;color:#1a365d;text-transform:uppercase;letter-spacing:0.5px;font-weight:700;margin-bottom:4px">Lecture stratégique</div>
        {''.join(parts)}
      </div>"""


def build_publication_item(r: dict) -> str:
    """Sous-élément à l'intérieur d'une carte entreprise."""
    nature = r.get("nature", "—")
    resume = r.get("resume", "")
    tag = r.get("tag", "ADMIN")
    color = TAG_COLORS.get(tag, "#718096")
    label = TAG_LABELS.get(tag, tag)
    form = r.get("form", "")
    items_8k = r.get("8k_items", [])
    items_str = f" · Items {', '.join(items_8k)}" if items_8k else ""
    strategic_html = render_strategic(r.get("strategic", ""))

    link_style = "color:#3182ce;font-size:11px;text-decoration:none"
    links = f'<a href="{r["url"]}" style="{link_style}">→ Document SEC</a>'
    if r.get("exhibit_url"):
        links += f'&nbsp;&nbsp;·&nbsp;&nbsp;<a href="{r["exhibit_url"]}" style="{link_style}">→ Communiqué complet</a>'

    return f"""
    <div style="border-left:3px solid {color};padding:14px 18px;margin:12px 0;background:#fafafa;border-radius:0 6px 6px 0">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px">
        <span style="background:{color};color:white;padding:2px 9px;border-radius:3px;font-size:11px;font-weight:600;letter-spacing:0.3px">{label}</span>
        <span style="color:#a0aec0;font-size:12px">{r['date']} · {form}{items_str}</span>
      </div>
      <div style="font-size:14px;color:#2d3748;font-weight:600;margin-bottom:6px">{nature}</div>
      <div style="color:#4a5568;font-size:13px;line-height:1.6">{resume}</div>
      {strategic_html}
      <div style="margin-top:8px">{links}</div>
    </div>"""


def build_company_card(group: dict) -> str:
    """Une carte par entreprise, contenant toutes ses publications."""
    sub_items = "\n".join(build_publication_item(r) for r in group["items"])
    tags_str = " · ".join(sorted(TAG_LABELS.get(t, t) for t in group["tags"]))
    highlight = group["best_priority"] <= 3  # M&A, EARNINGS, GUIDANCE
    border = "2px solid #c53030" if group["best_priority"] == 1 else "1px solid #e2e8f0"
    bg = "#fffaf0" if highlight else "white"

    return f"""
    <div style="border:{border};border-radius:8px;padding:22px 26px;margin:20px 0;background:{bg}">
      <div style="font-size:20px;font-weight:700;color:#1a202c;margin-bottom:2px">{group['company']}</div>
      <div style="font-size:12px;color:#718096;margin-bottom:14px">
        {group['ticker']} · {group['sector'].replace('_', ' ')} · {len(group['items'])} publication(s) · {tags_str}
      </div>
      {sub_items}
    </div>"""


def build_summary(groups: list[dict]) -> str:
    """Récap explicite en tête : liste des entreprises avec leurs tags."""
    if not groups:
        return ""

    rows = []
    for g in groups:
        tags_html = " ".join(
            f'<span style="background:{TAG_COLORS[t]};color:white;padding:1px 7px;border-radius:3px;font-size:10px;font-weight:600;margin-right:4px">{TAG_LABELS[t]}</span>'
            for t in sorted(g["tags"], key=lambda x: TAG_PRIORITY_LOCAL.get(x, 9))
        )
        rows.append(f"""
        <tr>
          <td style="padding:8px 14px 8px 0;font-weight:600;color:#2d3748;white-space:nowrap;vertical-align:top">{g['company']}</td>
          <td style="padding:8px 14px;color:#a0aec0;font-size:12px;vertical-align:top;white-space:nowrap">{len(g['items'])} pub.</td>
          <td style="padding:8px 0;vertical-align:top">{tags_html}</td>
        </tr>""")

    return f"""
    <div style="background:#f7fafc;border-radius:8px;padding:22px 26px;margin-bottom:28px">
      <div style="font-size:13px;color:#718096;text-transform:uppercase;letter-spacing:1px;margin-bottom:14px;font-weight:600">
        Rapports publiés · {sum(len(g['items']) for g in groups)} doc(s) sur {len(groups)} entreprise(s)
      </div>
      <table style="border-collapse:collapse;width:100%;font-size:13px">
        {''.join(rows)}
      </table>
    </div>"""


TAG_PRIORITY_LOCAL = {
    "M&A_STRATEGIE": 1, "EARNINGS": 2, "GUIDANCE": 3,
    "FINANCIER": 4, "MANAGEMENT": 5, "ADMIN": 9,
}


def build_html_email(results: list[dict]) -> str:
    today = date.today().strftime("%d %B %Y")

    if not results:
        body = '<p style="color:#718096;text-align:center;padding:40px">Aucune publication notable détectée.</p>'
    else:
        groups = group_by_company(results)
        cards = "\n".join(build_company_card(g) for g in groups)
        body = build_summary(groups) + cards

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,sans-serif;max-width:780px;margin:0 auto;padding:24px;background:#fafafa;color:#1a202c">

  <div style="background:linear-gradient(135deg,#1a365d,#2b6cb0);color:white;padding:28px;border-radius:8px;margin-bottom:28px">
    <div style="font-size:13px;opacity:0.8;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">Veille Corporate</div>
    <div style="font-size:22px;font-weight:600">{today}</div>
  </div>

  {body}

  <div style="margin-top:32px;padding-top:20px;border-top:1px solid #e2e8f0;color:#a0aec0;font-size:11px;text-align:center">
    Sources : SEC EDGAR · Analyses : Gemini · Routine quotidienne à 9h00
  </div>
</body>
</html>"""


def md_to_html(text: str) -> str:
    """Conserve compatibilité avec weekly_recap qui importe md_to_html."""
    if not text:
        return ""
    text = text.strip()
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\w)\*(?!\w)", "", text)
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return "".join(f'<p style="margin:6px 0;line-height:1.6">{p}</p>' for p in paragraphs)


def send(digest_md: str, results: list[dict]) -> bool:
    cfg = load_config()

    if cfg["sender"] == "TON_EMAIL@gmail.com":
        print("  [SKIP email] config.json non configuré")
        return False

    today = date.today().strftime("%d/%m/%Y")
    n = len(results)
    n_companies = len(set(r["company"] for r in results))
    n_ma = sum(1 for r in results if r.get("tag") == "M&A_STRATEGIE")

    subject_parts = [f"Veille Corporate · {today}"]
    if n_ma:
        subject_parts.append(f"🔴 {n_ma} M&A")
    subject_parts.append(f"{n_companies} entreprise(s)")
    subject = " · ".join(subject_parts)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg["sender"]
    msg["To"] = cfg["recipient"]
    msg.attach(MIMEText(build_html_email(results), "html"))

    try:
        with smtplib.SMTP(cfg["smtp_server"], cfg["smtp_port"]) as server:
            server.starttls()
            server.login(cfg["sender"], cfg["app_password"])
            server.sendmail(cfg["sender"], cfg["recipient"], msg.as_string())
        print(f"  Email envoyé à {cfg['recipient']}")
        return True
    except Exception as e:
        print(f"  [ERREUR email] {e}")
        return False
