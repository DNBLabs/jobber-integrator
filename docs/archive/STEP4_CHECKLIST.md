# Step 4: Move Sync Logic into the Backend — Checklist

You’re ready for Step 4. Steps 1–3 (shell, OAuth, token refresh) are done.

## Goal

- Reuse the existing sync logic from `sync_prices_to_jobber.py` inside the web app.
- Input: CSV file + `account_id` (from the connected user’s session).
- Output: same as the CLI — count updated, list of “SKU not found”.
- Use **Step 3** token handling: `get_valid_access_token(account_id)` for every Jobber API call; on **401**, refresh and retry once.

## Checklist

- [x] **Extract or reuse sync building blocks**
  - CSV parsing: columns `Part_Num` (product name in Jobber) and `Trade_Cost` (unit cost). Reuse logic from `sync_prices_to_jobber.py` (`load_and_clean_csv` style) or move it into a shared module (e.g. `app/sync.py` or `app/csv_sync.py`).
  - GraphQL: same `productOrServices` query (paginate by name) and `productsAndServicesEdit` mutation (`internalUnitCost`). Reuse query/mutation strings and request helpers; keep `X-JOBBER-GRAPHQL-VERSION: 2026-02-17` (or current version).
  - Rate limiting: keep the same delay (e.g. 0.5s) between Jobber API calls.

- [x] **Use per-account token (Step 3)**
  - For the connected user, get `account_id` from the session cookie (same as dashboard).
  - Call `get_valid_access_token(account_id)` to get a valid access token before running sync (and, if you do multiple requests, use the same token for the run; refresh on 401 and retry that request once).

- [x] **Handle 401 during sync**
  - If a Jobber GraphQL request returns 401: call `refresh_access_token(refresh_token)` then `update_tokens(account_id, ...)`, then retry that request once with the new token. If refresh fails, return an error (e.g. “Session expired; please reconnect to Jobber”).

- [x] **Define sync result shape**
  - Return a simple result the UI (Step 5) can show: e.g. `{ "updated": number, "skus_not_found": ["SKU1", "SKU2", ...], "errors": optional_message }`. Match what the CLI effectively produces (count updated + list of SKUs not found).

- [x] **Expose sync in the backend**
  - Add an endpoint or function that accepts CSV contents (or an uploaded file) + `account_id`: e.g. `POST /api/sync` or `POST /sync` that reads the session cookie for `account_id`, parses the CSV, runs the sync loop using the token (with 401 refresh/retry), and returns the result as JSON. Require the user to be connected (valid session); otherwise return 401/403.

- [x] **Optional: keep CLI working**
  - The existing `sync_prices_to_jobber.py` can stay as-is for single-account CLI use. If you prefer one implementation, move the core sync logic into a shared module and have both the CLI and the web app call it (CLI still uses `JOBBER_ACCESS_TOKEN` from env; web app uses `get_valid_access_token(account_id)`).

## References

- Existing sync logic: `sync_prices_to_jobber.py` (CSV parsing, `find_id_by_sku`, `update_unit_cost`, rate limit, query/mutation strings).
- Token helper: `app/jobber_oauth.py` — `get_valid_access_token(account_id)`, `refresh_access_token`, and `app/database.update_tokens`.
- Roadmap: `MARKETPLACE_ROADMAP.md` § “Practical Roadmap (Order of Work)” item 4.
- Step 5 will add the Manage App UI: upload CSV, “Sync now”, show this result.
