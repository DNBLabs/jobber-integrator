# Step 3: Token Storage and Refresh — Checklist

You’re ready for Step 3. Steps 1 (hosting/app shell) and 2 (OAuth) are done.

## Goal

- Keep using the existing DB (tokens per account).
- Before each API call (or on 401): refresh the access token if needed and update stored tokens.
- Jobber uses **refresh token rotation**: after each refresh, save the new refresh token.

## Checklist

- [x] **Refresh grant in `app/jobber_oauth.py`**
  - Add `refresh_access_token(refresh_token: str) -> dict` that POSTs to `https://api.getjobber.com/api/oauth/token` with `grant_type=refresh_token`, `client_id`, `client_secret`, `refresh_token`.
  - Return dict with `access_token`, `refresh_token` (and optionally `expires_in` if Jobber sends it).

- [x] **Update DB after refresh (optional column)**
  - Optionally add `access_token_expires_at` to `jobber_connections` if you want to refresh before expiry.
  - Add `update_tokens(account_id, access_token, refresh_token, expires_at=None)` in `app/database.py` to update tokens without touching other columns.

- [x] **“Valid token” helper**
  - Add a helper (e.g. in `app/jobber_oauth.py` or `app/tokens.py`) that, for a given `account_id`:
    - Loads the connection from the DB.
    - Returns the current `access_token` (and optionally refreshes first if `expires_at` is in the past or within a short buffer).
  - When you use this token and get a **401** from Jobber: call `refresh_access_token`, then `update_tokens`, then retry the request once; if refresh fails, treat as “reconnect required”.

- [ ] **Use the helper for sync (Step 4)**
  - When moving sync logic into the backend (Step 4), use this “valid token” helper (or the refresh-on-401 pattern) so every API call uses a valid token.

## References

- Jobber: [App Authorization (OAuth 2.0)](https://developer.getjobber.com/docs/building_your_app/app_authorization/) — refresh flow and token endpoint.
- Jobber: [Refresh Token Rotation](https://developer.getjobber.com/docs/building_your_app/refresh_token_rotation/) — always save the new refresh token.
- Roadmap: `MARKETPLACE_ROADMAP.md` § “Practical Roadmap (Order of Work)” item 3.
