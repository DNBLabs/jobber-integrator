# From Script to Jobber App Marketplace (and Making Money)

Your current project is a **single-account CLI script**: one token in `.env`, one Jobber account. To list on the [Jobber App Marketplace](https://apps.getjobber.com/app_marketplace) and charge for it, you need a **multi-tenant app** that any business can connect and use. Here’s what that involves.

---

## 1. What You Have vs What the Marketplace Expects

| Today (v1.0) | Marketplace app |
|--------------|------------------|
| One token in `.env` | **OAuth 2.0**: each customer connects their Jobber account; you get an access + refresh token per account |
| Run script locally | **Hosted app**: a URL where users go to use the app after clicking “Connect” in Jobber |
| No UI | **Manage App URL**: web UI where users upload CSV, run sync, see status |
| Single user (you) | **Multi-tenant**: store tokens per Jobber account; sync runs in the cloud for each customer |

---

## 2. What You Must Build (Technical)

### A. OAuth 2.0 flow (required)

- **Developer Center**: Your app already has Client ID and Client Secret. You need an **OAuth Callback URL** (e.g. `https://yourapp.com/oauth/callback`).
- **When a user clicks “Connect”** in the Jobber App Marketplace, Jobber sends them to your callback with an authorization `code`. Your server must:
  1. Exchange `code` + client secret for **access_token** and **refresh_token**.
  2. Store those tokens **per Jobber account** (e.g. by `account_id` from Jobber’s `account` query).
- **Refresh tokens**: Access tokens expire (~60 min). Before each sync (or on 401), use the refresh token to get a new access token. Store the new refresh token if Jobber uses refresh token rotation.

So you need a **backend** (e.g. Python/Flask or Node) that:

- Serves the callback URL.
- Talks to `https://api.getjobber.com/api/oauth/token` (code exchange and refresh).
- Saves and loads tokens per customer (database or secure storage).

### B. Hosted “Manage App” experience

- **Manage App URL**: The link users see in Jobber after they connect (e.g. “Open app” or “Manage”). It should:
  - Identify the connected Jobber account (session or stored token).
  - Let them **upload a CSV** (same format as `wholesaler_prices.csv`).
  - Trigger the **same sync logic** you have now (query by name, `productsAndServicesEdit` with `internalUnitCost`), using that account’s access token.
  - Show success / errors (e.g. “Updated 10 products”, “SKU X not found”).
- So: a small **web app** (frontend + backend) that wraps your current sync logic and uses the token for the logged-in account.

### C. Disconnect handling (required for approval)

- When a user clicks **“Disconnect”** in the Jobber App Marketplace, Jobber can send you a **webhook** (e.g. `APP_DISCONNECT`). You must:
  - Subscribe to that webhook in Developer Center.
  - In your app: when you receive it (or when the user disconnects from your UI), call Jobber’s **`appDisconnect`** mutation and stop using that account’s tokens.
- If your app also lets users “disconnect” from your side, you must call **`appDisconnect`** so Jobber shows the app as disconnected.  
Jobber states they will verify webhook subscription and disconnect behavior before publishing.

### D. Security and ops

- **Client secret**: Only ever used on the server; never in the browser or in the repo.
- **Tokens**: Stored encrypted or in a secure store (e.g. DB with encryption at rest), keyed by Jobber account id.
- **HTTPS** for callback and Manage App URL.
- **Rate limiting**: You already have `time.sleep(0.5)`; in a multi-tenant app, respect Jobber’s limits per account and avoid one customer’s usage affecting others.

---

## 3. What You Must Provide in the Developer Center (Listing)

Before you can submit for review:

- **App name** (e.g. “Price Sync” or “Wholesale Cost Sync”).
- **Developer name** (you or your company).
- **App description** (short, clear: sync CSV product costs to Jobber).
- **Features & benefits** (bullets: e.g. “Upload a CSV and update all product costs in one go”, “Match by product name”, “No manual entry”).
- **App logo** (square, 384×384 or larger, .PNG or .SVG, max 1 MB).
- **Gallery images** (screenshots of your app; e.g. “Connect” flow, upload CSV, success screen).
- **OAuth Callback URL** (your production callback, e.g. `https://yourapp.com/oauth/callback`).
- **Manage App URL** (optional but recommended: e.g. `https://yourapp.com/dashboard` or `/sync`).
- **Scopes**: Keep the Products & Services scopes you use today (read + edit for product/service costs).

You must also have **Two-Factor Authentication** enabled on your Developer Center account. Your app must have an **app logo** uploaded to be able to submit for review.

---

## 4. App Review and Going Live

- In [Manage Apps](https://developer.getjobber.com/apps), open your app and use **“Request review”** (or equivalent).
- Jobber will test: connect, use the app, disconnect, and confirm webhook/disconnect behavior.
- After approval, the app becomes **Published** and appears on the [App Marketplace](https://apps.getjobber.com/app_marketplace). Only Jobber **admin users** can see and connect apps.

---

## 5. Making Money From It

- **Jobber does not process app payments** for you. You charge customers yourself.
- Options:
  - **Your own subscription** (e.g. Stripe): “Price Sync Pro – $X/month” for unlimited syncs or per-seat.
  - **One-time purchase** (e.g. Gumroad, Stripe): pay once, use for 1 year or lifetime.
  - **Freemium**: free for 1 sync per month, paid for more or for scheduled syncs.
- **Where to charge**: Your own website or “Manage App” area (e.g. “Upgrade” or “Buy a license” link after they connect). You don’t need Jobber’s permission to charge; you do need to comply with their [Terms of Service](https://developer.getjobber.com/docs/terms_of_service/) and any marketplace policies.

---

## 6. Practical Roadmap (Order of Work)

1. **Hosting and app shell**  
   Choose stack (e.g. Python/Flask or FastAPI + simple frontend, or Next.js). Deploy so you have:
   - A public base URL (e.g. `https://pricesync.yourdomain.com`).
   - HTTPS and a place to store env (client secret, DB URL, etc.).

2. **OAuth implementation**  
   - Callback route: receive `code`, exchange for tokens, store by `account_id`, redirect to your “Manage” or dashboard.
   - Optional: “Connect to Jobber” button that sends users to Jobber’s authorize URL.

3. **Token storage and refresh**  
   - DB (or secure store) per account: `account_id`, `access_token`, `refresh_token`, (optional) `expires_at`.
   - Before each API call (or on 401): refresh if needed and update stored tokens.

4. **Move sync logic into the backend**  
   - Reuse your existing logic (CSV parsing, `productOrServices` query, `productsAndServicesEdit` mutation, rate limiting).
   - Input: CSV file + `account_id` (or current token). Output: same as now (count updated, list of “SKU not found”).

5. **Manage App UI**  
   - Page at your Manage App URL: show connected account, upload CSV, “Sync now”, show results (and optionally history).

6. **Webhook + disconnect**  
   - Subscribe to Jobber webhook (e.g. disconnect topic).
   - On disconnect event (or user clicking “Disconnect” in your UI): call `appDisconnect`, remove tokens for that account.

7. **Developer Center listing**  
   - Fill all required fields, upload logo and gallery images, set Callback and Manage App URLs, save.

8. **Submit for review**  
   - Enable 2FA on your Developer Center account, then submit. Fix any feedback from Jobber.

9. **Monetization**  
   - Add pricing (e.g. Stripe) on your site or inside the Manage App flow; link “Upgrade” or “Buy” from the app.

---

## 7. Summary Checklist

| Item | Status |
|------|--------|
| Sync logic (query + mutation, CSV, rate limit) | Done (v1.0) |
| OAuth 2.0 (authorize, callback, token exchange) | To do |
| Token storage per account + refresh | To do |
| Hosted backend (callback + sync API) | To do |
| Manage App URL + CSV upload UI | To do |
| Webhook subscription (disconnect) + `appDisconnect` | To do |
| App logo + gallery images | To do |
| Developer Center listing (all fields) | To do |
| 2FA on Developer Center | To do |
| Submit for review | To do |
| Your own billing (Stripe, etc.) | To do |

Once OAuth, hosting, disconnect handling, and listing are done, you’re in a position to submit for the marketplace and charge for the app through your own billing.
