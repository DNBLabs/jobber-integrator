# Step 6: Webhook + Disconnect — Checklist

Steps 1–5 are done (shell, OAuth, token refresh, sync API, Manage App UI). The app already has a “Disconnect” link that clears the session and removes the connection from the DB. Step 6 makes disconnect **marketplace‑compliant**: call Jobber’s `appDisconnect` when the user disconnects, and handle Jobber’s disconnect webhook when they disconnect from the marketplace.

## Goal

- When a user clicks **“Disconnect”** in your app: call Jobber’s **`appDisconnect`** mutation (with that account’s access token), then clear the cookie and remove tokens from your DB so Jobber shows the app as disconnected.
- **Subscribe to Jobber’s disconnect webhook** (e.g. `APP_DISCONNECT`). When Jobber sends the webhook (user disconnected from the Jobber App Marketplace), your app removes that account’s tokens from the DB and stops using them.
- Jobber verifies webhook subscription and disconnect behavior before publishing your app.

## Checklist

- [x] **Call `appDisconnect` when user disconnects from your UI**
  - In the [Jobber GraphQL API](https://developer.getjobber.com/docs/api/), find the **`appDisconnect`** mutation. It tells Jobber to mark the app as disconnected for that account.
  - In your `GET /disconnect` (or equivalent) handler: **before** clearing the session cookie and deleting the connection from the DB, obtain a valid access token for the current account (e.g. via `get_valid_access_token(account_id)`), call `appDisconnect` with that token, then clear the cookie and call `delete_connection(account_id)`.
  - If the token is expired or the API call fails (e.g. user already disconnected in Jobber), still clear your local state (cookie + DB) so the user can reconnect if needed. Optionally log or ignore the API error.
  - **Done:** `app/jobber_oauth.py`: `call_app_disconnect(access_token)`; `app/main.py`: `GET /disconnect` calls `get_valid_access_token` then `call_app_disconnect`, then `delete_connection` and clear cookie; exceptions caught so local state is always cleared.

- [ ] **Subscribe to the disconnect webhook in Developer Center**
  - In the [Jobber Developer Center](https://developer.getjobber.com/), open your app and find **Webhooks** (or **Event subscriptions**). Subscribe to the **disconnect** topic (e.g. `APP_DISCONNECT` or as named in the docs).
  - Set the **Webhook URL** to your production endpoint (e.g. `https://yourapp.com/webhooks/jobber`). This must be HTTPS and publicly reachable.
  - The app verifies requests using **X-Jobber-Hmac-SHA256** (HMAC-SHA256 of raw body with your OAuth client secret, base64). Use `JOBBER_CLIENT_SECRET`.

- [x] **Implement the webhook endpoint**
  - Add a **POST** route (e.g. `POST /webhooks/jobber` or `POST /api/webhooks/jobber`) that receives Jobber’s webhook payload.
  - Verify the request (signature or secret per Jobber’s docs). If verification fails, return 401 or 400 and do not process.
  - Parse the payload to get the **account id** (or equivalent) that disconnected. Remove that account’s connection from your DB (`delete_connection(account_id)`). Do not call `appDisconnect` from the webhook—Jobber already recorded the disconnect.
  - Return **200** quickly so Jobber doesn’t retry; do heavy work asynchronously if needed.
  - **Done:** `POST /webhooks/jobber` in `app/main.py` verifies `X-Jobber-Hmac-SHA256`, parses `data.webHookEvent` for `topic` and `accountId`; when `topic == "APP_DISCONNECT"` calls `delete_connection(accountId)`; returns 200.

- [x] **Optional: idempotency and logging**
  - If the webhook can be delivered more than once, ensure deleting an already-deleted connection is safe (no error).
  - **Done:** `delete_connection` is a simple DELETE; calling it for a missing account is a no-op.

## References

- **Marketplace roadmap:** `MARKETPLACE_ROADMAP.md` § “Practical Roadmap (Order of Work)” item 6, and § 2.C (Disconnect handling).
- **Jobber API:** [developer.getjobber.com](https://developer.getjobber.com/docs/api/) — `appDisconnect` mutation and webhook/event docs.
- **App:** `GET /disconnect` and `POST /webhooks/jobber` in `app/main.py`; `call_app_disconnect` in `app/jobber_oauth.py`; `delete_connection` in `app/database.py`. Webhook verification: [Setting up Webhooks](https://developer.getjobber.com/docs/using_jobbers_api/setting_up_webhooks/).
