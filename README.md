# Veille Corporate — Transport, Logistique, Infrastructures

Système automatisé de veille quotidienne sur ~90 entreprises cotées du secteur transport / logistique / infrastructures. Récupère les publications réglementaires (earnings, M&A, rapports annuels...) et les analyse via LLM pour ne livrer par email que les signaux pertinents.

L'analyse se fait à **deux niveaux** : d'abord classer et résumer chaque document, puis — sur les documents qui le méritent — **lire entre les lignes** le discours des dirigeants pour en tirer des signaux tournés vers l'avenir. Une relecture finale du mail élimine les rares doublons avant envoi.

## Stack

- **Sources** : SEC EDGAR (US/Bermuda/Marshall Islands) — extensible aux régulateurs FR/EU
- **Analyse** : Gemini Flash Lite (gratuit)
- **Livraison** : email Gmail SMTP (gratuit)
- **Orchestration** : GitHub Actions (cron quotidien, gratuit) — tourne dans le cloud, sans dépendre d'un PC allumé

**Coût total : 0€/mois.**

## Architecture

```
veille-corpo-free-version/
├── companies.json              ← 91 entreprises (34 cotées SEC, actives)
├── config.json                 ← clés API + email (NON commité)
├── run_daily.py                ← orchestrateur principal
├── fetchers/
│   └── sec_fetcher.py          ← SEC EDGAR API
├── analyzers/
│   ├── llm_analyzer.py         ← analyse Gemini (2 passes + anti-doublons)
│   └── report_builder.py       ← digest Markdown
├── delivery/
│   ├── email_sender.py         ← mail quotidien
│   └── weekly_recap.py         ← récap hebdo vendredi
├── outputs/                    ← données générées (NON commité)
└── .github/workflows/
    └── daily.yml               ← planification GitHub Actions (cron quotidien)
```

## Pipeline

1. **Fetch** SEC EDGAR pour les 34 entreprises US (CIK validés)
2. **Téléchargement** des documents — et pour les communiqués (6-K/8-K), récupération de la pièce jointe **EX-99.1**, c'est-à-dire le vrai communiqué de résultats (voir plus bas)
3. **Analyse Gemini — passe 1** : classe et résume chaque document (`NATURE` + `RESUME` + `TAG`)
4. **Analyse Gemini — passe 2 (stratégique)** : sur les documents notables, lecture entre les lignes du discours des dirigeants → signaux prospectifs
5. **Filtrage** : seuls les tags ≠ `ADMIN` sont retenus
6. **Relecture anti-doublons** : Gemini relit l'ensemble du mail et retire les rares news déposées en double
7. **Email quotidien** : groupé par entreprise, M&A en tête
8. **Email récap hebdo** (vendredi) : synthèse 7 jours

## Quels documents SEC on surveille

La SEC (le « gendarme » de la bourse américaine) oblige les entreprises cotées à publier certains formulaires. On surveille ceux qui portent de l'information utile :

| Formulaire | En clair |
|---|---|
| `8-K` | Événement important ponctuel (US) : acquisition, changement de dirigeant, communiqué de résultats |
| `10-K` / `10-Q` | Rapport annuel / trimestriel (US) |
| `6-K` | Communiqué d'un émetteur **étranger** (beaucoup d'armateurs grecs cotés à New York) — souvent les résultats |
| `20-F` | Rapport annuel d'un émetteur étranger |
| `SC 13D`, `S-4`/`F-4`, `PREM14A`/`DEFM14A` | **Signaux M&A** : prise de participation >5 %, fusion par échange d'actions, convocation d'AG pour approuver une fusion |

## Comment l'outil « lit » les documents

**Récupérer le vrai communiqué (EX-99.1).** Pour un communiqué de résultats, le document principal déposé est souvent une simple page de garde de quelques lignes — le contenu réel (chiffres, citations du PDG) est dans une pièce jointe nommée `EX-99.1`. L'outil va la chercher automatiquement : sans elle, l'IA manque de matière et risque d'inventer.

**Passe 1 — Classer et résumer.** Gemini lit le document et renvoie une nature (« Résultats trimestriels Q1 », « Acquisition navire »…), un résumé de 2-3 phrases avec les chiffres clés, et un tag (voir tableau ci-dessous). Les formulaires structurellement M&A sont protégés pour ne jamais être classés à tort en « formalité ».

**Passe 2 — Lire entre les lignes.** Sur les documents qui ont du fond, une seconde lecture façon analyste cherche les **signaux tournés vers l'avenir** dans le discours des dirigeants : ton de la guidance, allocation du capital (dividende, rachat d'actions, désendettement, investissements), flotte/capacité, M&A évoquée, vision du cycle. Elle en tire une **anticipation** pour les 2-3 prochains trimestres et un **niveau de conviction** du management. Garde-fou : si le document n'a pas de matière prospective, l'IA le dit au lieu d'inventer.

**Relecture finale — Anti-doublons.** Juste avant l'envoi, Gemini relit tout le mail. Il arrive (rarement) qu'une même news soit déposée plusieurs fois quasi à l'identique ; dans ce cas l'outil ne garde que la version la plus complète. Prudent par principe : au moindre doute, il ne retire rien.

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

# 6. Déploiement : GitHub Actions (recommandé — tourne dans le cloud)
# Le workflow .github/workflows/daily.yml lance la veille chaque jour ouvré.
# Renseigner les secrets du repo (Settings → Secrets and variables → Actions) :
#   GEMINI_API_KEY · GMAIL_SENDER · GMAIL_APP_PASSWORD · GMAIL_RECIPIENT

# Alternative locale (le Mac doit être allumé à l'heure prévue) :
crontab -e
# Ajouter :
# 0 9 * * 1-5 cd /chemin/vers/repo && /opt/homebrew/bin/python3 run_daily.py >> cron.log 2>&1
```

## Limites connues

- Planification via **GitHub Actions** (cron `0 7 * * 1-5` = 9h Paris en été). Le cron GitHub n'est pas garanti à la minute (retards possibles) et se désactive après ~60 jours sans activité sur le repo — un déclencheur plus fiable est une piste d'amélioration.
- Free tier Gemini : 1500 req/jour (largement suffisant pour ~30 docs/jour).
- SEC EDGAR : 10 req/sec max (gestion automatique via throttle).
- Couverture actuelle : 34 entreprises cotées SEC. Phase 2 prévue pour les ~57 entreprises restantes, majoritairement FR/EU (AMF + scraping IR).
