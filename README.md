# Veille Corporate — Transport, Logistique, Infrastructures

Système automatisé de veille quotidienne sur ~100 entreprises cotées du secteur transport / logistique / infrastructures. Récupère les publications réglementaires (earnings, M&A, rapports annuels...) et les analyse via LLM pour ne livrer par email que les signaux pertinents.

## Stack

- **Sources** : SEC EDGAR (US/Bermuda/Marshall Islands) — extensible aux régulateurs FR/EU
- **Analyse** : Gemini Flash Lite (gratuit)
- **Livraison** : email Gmail SMTP (gratuit)
- **Orchestration** : cron macOS

**Coût total : 0€/mois.**

## Architecture

```
veille-corpo-free-version/
├── companies.json              ← 88 entreprises (34 US actives)
├── config.json                 ← clés API + email (NON commité)
├── run_daily.py                ← orchestrateur principal
├── fetchers/
│   └── sec_fetcher.py          ← SEC EDGAR API
├── analyzers/
│   ├── llm_analyzer.py         ← analyse Gemini
│   └── report_builder.py       ← digest Markdown
├── delivery/
│   ├── email_sender.py         ← mail quotidien
│   └── weekly_recap.py         ← récap hebdo vendredi
└── outputs/                    ← données générées (NON commité)
```

## Pipeline

1. **Fetch** SEC EDGAR pour les 34 entreprises US (CIK validés)
2. **Téléchargement** des documents (8-K, 10-K, 10-Q, 6-K, 20-F)
3. **Analyse Gemini** : extraction `NATURE` + `RESUME` + `TAG`
4. **Filtrage** : seuls les tags ≠ `ADMIN` sont retenus
5. **Email quotidien** : groupé par entreprise, M&A en tête
6. **Email récap hebdo** (vendredi) : synthèse 7 jours

## Tags

| Tag | Couleur | Sens |
|---|---|---|
| `M&A_STRATEGIE` | 🔴 Rouge | Acquisitions, cessions, partenariats, spin-offs |
| `EARNINGS` | 🟠 Orange | Résultats financiers significatifs |
| `GUIDANCE` | 🔵 Bleu | Prévisions, outlook |
| `FINANCIER` | 🟢 Teal | Dividende, émission, rachat actions |
| `MANAGEMENT` | 🟣 Violet | Changement dirigeant, gouvernance |
| `ADMIN` | ⚫ Gris | Formalité — filtré, pas envoyé |

## Setup

```bash
# 1. Cloner
git clone <repo>
cd veille-corpo-free-version

# 2. Dépendances
pip3 install requests google-genai

# 3. Config
cp config.example.json config.json
# Éditer config.json avec :
#   - clé Gemini : https://aistudio.google.com/apikey
#   - app password Gmail : Mon compte → Sécurité → Mots de passe d'application

# 4. Test
python3 run_daily.py --lookback 7 --no-email

# 5. Premier vrai envoi
python3 run_daily.py --lookback 7 --force-weekly

# 6. Cron quotidien (lun-ven 9h00)
crontab -e
# Ajouter :
# 0 9 * * 1-5 cd /chemin/vers/repo && /opt/homebrew/bin/python3 run_daily.py >> cron.log 2>&1
```

## Limites connues

- Le cron local ne tourne **que si le Mac est allumé** à 9h. Migration vers GitHub Actions possible pour 24/7.
- Free tier Gemini : 1500 req/jour (largement suffisant pour ~30 docs/jour).
- SEC EDGAR : 10 req/sec max (gestion automatique via throttle).
- Couverture actuelle : 34 entreprises US. Phase 2 prévue pour les ~54 entreprises FR/EU (AMF + scraping IR).
