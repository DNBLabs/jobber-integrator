# Jobber Integrator

**Portfolio project:** Integrate wholesaler **CSV pricing** with [Jobber](https://www.getjobber.com/) **Products & Services** via the **GraphQL API** — multi-tenant **OAuth** web app plus an optional **CLI** for single-account use.

This repository is maintained as a **demonstration of backend and reliability practices** (not a hosted SaaS). Run it **locally** with your own [Jobber Developer Center](https://developer.getjobber.com/) app credentials.

---

## Highlights

| Area | What this repo shows |
|------|---------------------|
| **API integration** | OAuth2 (authorize + callback + refresh), GraphQL queries/mutations, webhooks with HMAC verification |
| **Product logic** | Robust CSV parsing (encoding, column aliases, international cost formats), sync + preview, row/upload limits |
| **Reliability** | Structured JSON logging, request correlation IDs, deep `/health` (includes DB check), global error handling (no stack traces to clients) |
| **Security-minded** | Env-based secrets, session cookies, rate limiting (`slowapi`), webhook idempotency window |
| **Engineering hygiene** | Pinned dependencies, `pip-audit` in CI, pytest suite |

**Stack:** Python 3.11+ · FastAPI · SQLite (default) · Jinja2 templates · Jobber Design CSS (CDN)

---

## Repository layout

```
.
├── .github/
│   └── workflows/
│       └── tests.yml          # CI: pytest + pip-audit
├── app/
│   ├── main.py                # FastAPI: OAuth, dashboard, sync API, webhooks
│   ├── sync.py                # CSV parse + Jobber GraphQL sync/preview
│   ├── jobber_oauth.py        # Token exchange & refresh
│   ├── database.py            # Per-account token storage (SQLite)
│   ├── logging_config.py      # JSON log formatter
│   ├── config.py              # Environment configuration
│   ├── cookies.py             # Signed session cookies
│   └── templates/             # Dashboard & dev test-sync UI
├── docs/
│   ├── README.md              # Index of documentation
│   ├── DEPLOYMENT_AND_MARKETPLACE_PLAN.md  # Optional: host + marketplace steps
│   ├── MARKETPLACE_ROADMAP.md
│   ├── JOBBER_SCHEMA_NOTES.md
│   ├── TEST_CSV_README.md
│   └── archive/               # Historical plans & completed checklists
├── tests/                     # Pytest: API, OAuth, sync, UI, webhooks
├── sync_prices_to_jobber.py   # CLI (single token in .env)
├── run_sync_check.py          # Helper: run sync from sample CSV
├── run_webapp.ps1             # Windows helper to start uvicorn
├── requirements.txt
├── .env.example
└── README.md
```

Sample CSVs at repo root: `wholesaler_prices.csv`, `test_sync_scenarios.csv` (see `docs/TEST_CSV_README.md`).

---

## Quick start (web app, local)

1. **Python 3.11+** and a virtualenv (recommended).

   ```bash
   python -m venv .venv
   source .venv/bin/activate    # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Copy **`.env.example`** → **`.env`** and set at least `JOBBER_CLIENT_ID`, `JOBBER_CLIENT_SECRET`, and a non-default **`SECRET_KEY`**. In Jobber Developer Center, set the OAuth callback to match **`BASE_URL`** (e.g. `http://localhost:8000/oauth/callback`).

3. Start the app:

   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

   Open **http://localhost:8000** → connect to Jobber → use the dashboard to upload a CSV. **Health:** http://localhost:8000/health

---

## CLI (single account)

With `JOBBER_ACCESS_TOKEN` in `.env`:

```bash
python sync_prices_to_jobber.py --dry-run
python sync_prices_to_jobber.py
```

---

## Tests & CI

```bash
pytest
```

GitHub Actions (`.github/workflows/tests.yml`) runs the test matrix and **`pip-audit`** on pinned dependencies.

---

## Documentation

- **[docs/README.md](docs/README.md)** — doc index  
- **[docs/DEPLOYMENT_AND_MARKETPLACE_PLAN.md](docs/DEPLOYMENT_AND_MARKETPLACE_PLAN.md)** — optional path to production hosting & Jobber App Marketplace  
- **[docs/JOBBER_SCHEMA_NOTES.md](docs/JOBBER_SCHEMA_NOTES.md)** — GraphQL notes  

---

## License

[MIT](LICENSE)
