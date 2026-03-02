# Jobber Integrator

Sync wholesaler CSV pricing to Jobber Products & Services via the GraphQL API.

- **CLI** (single account): run `sync_prices_to_jobber.py` with a token in `.env`.
- **Web app** (marketplace): multi-tenant app with OAuth and token refresh (Steps 2–3). Connect from dashboard, then sync CSV (Step 4+).

## Dependencies (Phase 4.1)

Dependencies are pinned in `requirements.txt` for reproducible installs. Deploy from this file. CI runs `pip-audit` to check for known vulnerabilities. To update: bump versions in `requirements.txt`, run `pytest` and `pip-audit` (e.g. before each release or quarterly).

## CLI

```bash
pip install -r requirements.txt
# Add JOBBER_ACCESS_TOKEN to .env
python sync_prices_to_jobber.py --dry-run   # preview
python sync_prices_to_jobber.py              # sync
```

## Web app (OAuth + token refresh)

1. Copy `.env.example` to `.env` and fill in your values.
2. In [Jobber Developer Center](https://developer.getjobber.com/apps), open your app and set **OAuth Callback URL** to `http://localhost:8000/oauth/callback` (local) or `https://your-domain.com/oauth/callback` (production). Must match `BASE_URL` + `/oauth/callback`.
3. In `.env` set `JOBBER_CLIENT_ID`, `JOBBER_CLIENT_SECRET`, and `BASE_URL=http://localhost:8000` (or your public URL). For production, set `BASE_URL` to your public HTTPS URL (e.g. `https://yourapp.com`) and set `SECRET_KEY` to a random string so session cookies use the `secure` flag.

**CSV limits (optional):** Maximum upload size defaults to 10 MB (`CSV_MAX_UPLOAD_BYTES`). Maximum number of valid rows per CSV defaults to 1000 (`CSV_MAX_ROWS`). Set these in `.env` if you need different limits.

### Run the web app locally (PowerShell)

From your project folder, run:

```powershell
cd "c:\Users\reggin\Random Cursor Shit\Jobber Integrator"
.\run_webapp.ps1
```

Or without the script (same port):

```powershell
cd "c:\Users\reggin\Random Cursor Shit\Jobber Integrator"
.\.venv\Scripts\uvicorn app.main:app --reload --port 8000
```

Then open [http://localhost:8000](http://localhost:8000) → **Connect to Jobber** → authorize → dashboard shows connected. Health: [http://localhost:8000/health](http://localhost:8000/health).

**Test sync (verify cost + selling price mutation):** After connecting, run `python run_sync_check.py` (uses `wholesaler_prices.csv` with 25% markup), or open [http://localhost:8000/test-sync](http://localhost:8000/test-sync) and click **Run test sync**.

## Tests

Run the test suite before merging to `master` or deploying:

```bash
pytest
```

With the project venv: `.venv\Scripts\pytest` (Windows) or `.venv/bin/pytest` (Unix).

**Timeouts (Phase 3.2):** Outbound HTTP calls use explicit timeouts: OAuth token exchange and account info 15s (`app/jobber_oauth.py`), Jobber GraphQL 30s (`app/sync.py`). Sync retries once on 401 after token refresh.

**Rate limiting (Phase 5.1):** POST `/api/sync`, `/api/sync/preview`, `/api/sync/test-run`, and `/webhooks/jobber` are limited per account (when authenticated) or per IP. Default 60 requests per minute. Set `RATE_LIMIT` in `.env` to change (e.g. `30/minute`). Exceeding the limit returns 429 with a JSON error.

**Webhook idempotency (Phase 5.2):** Duplicate webhook payloads (same topic, account, and body) within 5 minutes are ignored and return 200 without reprocessing, so duplicate deliveries do not cause duplicate side effects.

See [MARKETPLACE_ROADMAP.md](MARKETPLACE_ROADMAP.md) for the full path to the marketplace.
