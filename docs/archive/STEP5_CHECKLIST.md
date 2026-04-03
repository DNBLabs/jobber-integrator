# Step 5: Manage App UI — Checklist

You’re ready for Step 5. Steps 1–4 (shell, OAuth, token refresh, sync API) are done. The dashboard already shows connected account and Disconnect; Step 5 adds upload + “Sync now” + results.

## Goal

- Build the **Manage App UI** so it feels like part of Jobber: use **Jobber’s Atlantis Design System** for layout, components, and styling.
- When connected: **upload a CSV**, click **“Sync now”**, and **see results** (count updated, SKUs not found, any error).
- Optionally: show **history** of past syncs (e.g. last run time and summary).

## Checklist

- [x] **Use Jobber’s Atlantis Design System**
  - **Atlantis** is Jobber’s design system for apps in the marketplace. Using it keeps your Manage App URL consistent with Jobber’s look and feel and improves approval/review.
  - In the [Jobber Developer Center](https://developer.getjobber.com/docs/building_your_app/atlantis_design_system/), read the Atlantis docs: components, tokens (colors, spacing, typography), and how to load the CSS/JS (e.g. CDN or package).
  - Apply Atlantis to the dashboard (and any other Manage App pages): use Atlantis layout, buttons, cards, form inputs, and feedback (success/error) instead of (or in addition to) custom CSS. Replace or align the current dark-theme styles with Atlantis so the page looks like a Jobber app.
  - **Done:** `base.html` loads Atlantis `foundation.css` and `semantic.css` from unpkg (`@jobber/design@0.90.1`). Page uses Atlantis tokens for colors, spacing, typography, radius, and shadow; light theme via `data-theme="light"`.

- [x] **Upload CSV on the dashboard**
  - When the user is connected, show a **file input** (accept `.csv`) and a **“Sync now”** button.
  - Keep the existing “CSV format” note (columns `Part_Num`, `Trade_Cost`).
  - If not connected, keep current behaviour (Connect to Jobber only; no upload yet).
  - **Done:** Dashboard shows file input + “Sync now” when connected; CSV format card unchanged.

- [x] **Submit to the sync API**
  - On “Sync now”: send the selected file to `POST /api/sync` (multipart form with field `file`). Use the same session (cookies) so the backend has the account.
  - Disable the button (and maybe show “Syncing…”) while the request is in progress so the user doesn’t double-submit.
  - **Done:** Form submits via `fetch` with `FormData` and `credentials: 'same-origin'`; button disabled and “Syncing…” shown during request.

- [x] **Show results**
  - After the API responds, show:
    - **Success:** e.g. “Updated N product(s).”
    - **SKUs not found:** list or count of product names that weren’t found in Jobber (from `skus_not_found`).
    - **Error:** if the API returns an `error` (e.g. “Not connected”, “Session expired; please reconnect”), show it clearly and optionally link back to Connect.
  - You can do this with a small result area on the same page (no redirect), or a simple “last result” section that updates after each sync.
  - **Done:** Result area shows updated count, skus_not_found (first 20 + “+N more” and scrollable list), and errors with link to Connect on 403.

- [ ] **Optional: sync history**
  - Store the last sync result (or last N) per account (e.g. in DB or in memory) and show “Last sync: …” or a short history list. If you skip this, the checklist is still complete with “show results” for the current run only.

## References

- **Jobber Atlantis Design System:** [developer.getjobber.com/docs/building_your_app/atlantis_design_system/](https://developer.getjobber.com/docs/building_your_app/atlantis_design_system/) — use for Manage App UI so it matches Jobber’s marketplace look.
- Backend: `POST /api/sync` — expects multipart `file` (CSV), returns `{ "updated", "skus_not_found", "error" }`. See `app/main.py` and `app/sync.py`.
- Dashboard template: `app/templates/dashboard.html` (connected block is the right place to add upload + Sync now + results).
- Roadmap: `MARKETPLACE_ROADMAP.md` § “Practical Roadmap (Order of Work)” item 5.
