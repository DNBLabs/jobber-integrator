# Deployment and Marketplace Plan

This document outlines the steps to **deploy** the Price Sync app and **list it on the Jobber App Marketplace**. The app is already production-ready (see [archive/PRODUCTION_READINESS_PLAN.md](archive/PRODUCTION_READINESS_PLAN.md)); this plan covers hosting, go-live, and submission.

---

## Overview

| Phase | Goal |
|-------|------|
| **A. Deployment** | Run the app in production with HTTPS, persistent storage, and correct config so Jobber can call your callback and users can open the Manage App URL. |
| **B. Marketplace** | Complete the Developer Center listing, enable 2FA, subscribe to webhooks, and submit for review so the app appears on the [App Marketplace](https://apps.getjobber.com/app_marketplace). |
| **C. Monetization (optional)** | Add your own billing (e.g. Stripe) and link it from the app. |

---

## Part A: Deployment

**Goal:** The app is live at a public HTTPS URL, environment variables are set, the database persists across restarts, and `/health` and OAuth/sync work end-to-end.

### A.1 Choose a host and get a URL

- **Options (examples):**
  - **Railway / Render / Fly.io:** Good for Python apps; minimal server config, env in dashboard, automatic HTTPS.
  - **VPS (e.g. DigitalOcean, Linode):** You manage nginx, systemd, and SSL (e.g. Let’s Encrypt).
  - **Other PaaS:** Any platform that can run a Python process and expose HTTPS.
- **Outcome:** A base URL for the app (e.g. `https://pricesync.yourdomain.com` or `https://your-app.up.railway.app`). This will be your **BASE_URL**.

### A.2 Set environment variables

Set these in your host’s config (e.g. Railway/Render “Environment”, or `.env` on a VPS). **Never** commit secrets to the repo.

| Variable | Required | Notes |
|---------|----------|--------|
| `BASE_URL` | Yes | Full public URL with no trailing slash, e.g. `https://pricesync.example.com`. Must be HTTPS in production so cookies use `secure`. |
| `SECRET_KEY` | Yes | Random string (e.g. 32+ chars). Not the default `dev-secret-change-in-production` or the app will refuse to start on HTTPS. |
| `JOBBER_CLIENT_ID` | Yes | From [Developer Center](https://developer.getjobber.com/apps) → your app. |
| `JOBBER_CLIENT_SECRET` | Yes | Same place; keep secret. |
| `DATABASE_URL` | Yes | SQLite: `sqlite:///./app.db` (ensure the path is on a **persistent volume** so data survives restarts). For Postgres later: `postgresql://...`. |
| `CSV_MAX_UPLOAD_BYTES` | No | Default 10 MB. |
| `CSV_MAX_ROWS` | No | Default 1000. |
| `RATE_LIMIT` | No | Default `60/minute`. |
| `LOG_LEVEL` | No | Default INFO. |

### A.3 Run the app with HTTPS in front

- **If using a PaaS:** Point the service at your repo or a built image; set the start command to something like:
  ```bash
  uvicorn app.main:app --host 0.0.0.0 --port $PORT
  ```
  (Use the port the platform assigns, e.g. `PORT` on Railway/Render.)
- **If using a VPS:** Run uvicorn (or gunicorn + uvicorn workers) behind a reverse proxy (nginx or Caddy). The proxy terminates HTTPS and forwards to the app. Ensure the proxy sets `X-Forwarded-Proto` (and optionally `X-Forwarded-Host`) if you need them; the app uses `BASE_URL` for redirects and cookies, so this is optional.

### A.4 Persist the database

- **SQLite:** The app writes to the path derived from `DATABASE_URL` (e.g. `./app.db`). On PaaS, use a **persistent volume** or the DB will be lost on each deploy. On a VPS, put the file on a persistent disk.
- **Backups (recommended):** Periodically back up the SQLite file (or run `pg_dump` if you switch to Postgres) so you can restore tokens and connections.

### A.5 Verify deployment

1. **Health:** Open `https://your-base-url/health`. Expect `200` and `{"status":"ok","db":"ok"}`.
2. **Dashboard:** Open `https://your-base-url/dashboard`. You should see the “Connect to Jobber” (or connected) UI.
3. **OAuth:** Click Connect, complete the Jobber authorization; you should land back on the dashboard with the account connected.
4. **Sync:** Upload a small CSV and run a preview or sync; confirm it completes without 500s.
5. **Webhook (after Part B.3):** After you set the webhook URL in Developer Center, trigger a disconnect and confirm your app receives the webhook and removes the connection.

Once this is done, you have a **live production app** and can point Jobber’s Callback URL and Manage App URL at it.

---

## Part B: Marketplace

**Goal:** The app is listed in the Jobber Developer Center with all required fields, webhook subscribed, 2FA on, and submitted for review so it can appear on the App Marketplace.

### B.1 Developer Center: App details and branding

In [Developer Center → Manage Apps](https://developer.getjobber.com/apps), open your app and complete:

| Field | Requirement |
|-------|-------------|
| **App name** | e.g. “Price Sync” or “Wholesale Cost Sync”. |
| **Developer name** | You or your company. |
| **Short description** | One or two sentences: sync CSV product costs to Jobber. |
| **Features / benefits** | Bullet list (e.g. “Upload a CSV and update product costs in one go”, “Match by product name”, “No manual entry”). |
| **App logo** | Square, **384×384 or larger**, .PNG or .SVG, max 1 MB. **Required to submit for review.** |
| **Gallery images** | Screenshots of the app (e.g. Connect flow, dashboard, upload CSV, success screen). |

### B.2 Developer Center: URLs (production)

Set these to your **deployed** app (from Part A):

| Field | Value |
|-------|--------|
| **OAuth Callback URL** | `https://your-base-url/oauth/callback` (must match `BASE_URL` + `/oauth/callback`). |
| **Manage App URL** | `https://your-base-url/dashboard` (or `/` if you prefer; this is where users land after “Open app” in Jobber). |

Ensure **Scopes** include the Products & Services read/edit scopes your sync uses.

### B.3 Webhook subscription

- In Developer Center, open your app’s **Webhooks** (or equivalent) section.
- Subscribe to the **disconnect** topic (e.g. `APP_DISCONNECT`).
- Set the **Webhook URL** to: `https://your-base-url/webhooks/jobber`.
- Your app already verifies `X-Jobber-Hmac-SHA256` and handles the event; ensure `JOBBER_CLIENT_SECRET` is set in production so verification succeeds.

### B.4 Two-Factor Authentication (2FA)

- Enable **Two-Factor Authentication** on your **Jobber Developer Center account** (account settings).
- Jobber requires 2FA before you can submit an app for review.

### B.5 Submit for review

- In Developer Center, open your app and use **“Request review”** (or equivalent).
- Jobber will test: connect → use the app (e.g. sync) → disconnect, and confirm webhook/disconnect behavior.
- Fix any feedback they provide; once approved, the app becomes **Published** and appears on the [App Marketplace](https://apps.getjobber.com/app_marketplace). Only Jobber **admin users** can see and connect apps.

---

## Part C: Monetization (optional)

Jobber does **not** process payments for you. If you want to charge:

- **Options:** Your own subscription (e.g. Stripe), one-time purchase (Gumroad, Stripe), or freemium (e.g. 1 free sync/month, paid for more).
- **Where:** Your own site or a “Upgrade” / “Buy a license” link inside the Manage App (e.g. on the dashboard). Comply with [Jobber’s Terms of Service](https://developer.getjobber.com/docs/terms_of_service/) and marketplace policies.

You can add monetization before or after approval; it’s independent of listing.

---

## Order of operations (summary)

1. **Deploy (Part A):** Host → env (BASE_URL, SECRET_KEY, Jobber credentials, DATABASE_URL) → run app with HTTPS → persist DB → verify health, OAuth, sync.
2. **Listing (Part B.1–B.3):** Fill app details, logo, gallery; set Callback and Manage App URLs; subscribe webhook to `https://your-base-url/webhooks/jobber`.
3. **2FA (Part B.4):** Enable on your Developer Center account.
4. **Submit (Part B.5):** Request review; address feedback until published.
5. **Monetization (Part C):** Optional; add billing and link from the app when you’re ready.

---

## Checklist

| Step | Done |
|------|------|
| **A.1** Choose host and get HTTPS base URL | |
| **A.2** Set BASE_URL, SECRET_KEY, JOBBER_*, DATABASE_URL (and optional env) | |
| **A.3** Run app (uvicorn/gunicorn) behind HTTPS | |
| **A.4** Persist database (volume/backups) | |
| **A.5** Verify /health, dashboard, OAuth, sync (and webhook after B.3) | |
| **B.1** App name, description, logo, gallery in Developer Center | |
| **B.2** OAuth Callback URL and Manage App URL set to production | |
| **B.3** Webhook subscribed; URL = production /webhooks/jobber | |
| **B.4** 2FA enabled on Developer Center account | |
| **B.5** Request review; fix feedback; published | |
| **C** (optional) Add billing and “Upgrade” in app | |

---

## References

- **App behavior and config:** [README.md](../README.md)
- **Production readiness (historical):** [archive/PRODUCTION_READINESS_PLAN.md](archive/PRODUCTION_READINESS_PLAN.md)
- **Marketplace context:** [MARKETPLACE_ROADMAP.md](MARKETPLACE_ROADMAP.md)
- **Jobber Developer Center:** [developer.getjobber.com](https://developer.getjobber.com)
- **App Marketplace:** [apps.getjobber.com/app_marketplace](https://apps.getjobber.com/app_marketplace)
