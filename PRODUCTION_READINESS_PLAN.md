# Production Readiness Plan

This document outlines a clear path to make the Price Sync app robust and production-ready (excluding deployment and hosting). No code—implementation detail and order of operations only.

**Prerequisite:** Complete the **[parser refactor](PARSER_IMPROVEMENT_PLAN.md)** before starting the phases below. The parser plan (encoding, skipped-row visibility, column aliases, cost formats, row limit, parse errors) is implemented first; then Phase 1 adds config and file-size limit, Phase 2 adds logging (using parser result), and Phase 3 maps parser errors to user messages.

---

## Principles

- **Incremental:** Each phase can be done and verified on its own.
- **Low-risk first:** Config and limits before changing request/error flow.
- **Observability before hardening:** Logging and health so you can see the effect of later changes.

---

## Phase 1: Configuration and safety rails — **DONE**

**Goal:** Fail fast on bad config and cap resource use so the app doesn’t fall over on edge inputs.

### 1.1 Config validation at startup — **DONE**

- **Current state:** App starts even when `JOBBER_CLIENT_ID` or `JOBBER_CLIENT_SECRET` are missing or empty. Users only discover this when they click Connect. `SECRET_KEY` has a weak default and is not checked.
- **Implementation path:**
  - **When:** During app startup (e.g. in FastAPI `lifespan` or a small startup module that runs before the app serves).
  - **OAuth credentials:** If the app will use OAuth in this run (e.g. `JOBBER_CLIENT_ID` is non-empty), require `JOBBER_CLIENT_SECRET` to be non-empty and log a clear error or exit if missing. Optionally require both to be set together, or document “leave both unset for read-only / health-only mode” if you ever support that.
  - **SECRET_KEY:** If `SECRET_KEY` is still the literal default (e.g. `"dev-secret-change-in-production"`), log a **warning** at startup. If you can infer “production” (e.g. `BASE_URL` starts with `https://`), require a non-default `SECRET_KEY` and refuse to start (or warn loudly) when it’s default.
  - **BASE_URL:** When non-empty, validate it’s a sensible URL (starts with `http://` or `https://`); log a warning if it looks wrong (e.g. still `http://localhost:8000` while `SECRET_KEY` is production-like).
- **Success:** Misconfiguration is caught at startup or clearly warned; no silent failures when users hit Connect.
- **Implemented:** `_validate_config()` in `app/main.py` at lifespan startup: exit if JOBBER_CLIENT_ID set but JOBBER_CLIENT_SECRET empty; exit if BASE_URL is HTTPS and SECRET_KEY is default; warnings for default SECRET_KEY (HTTP), invalid BASE_URL, localhost + non-default SECRET_KEY.

### 1.2 File upload and CSV row limits — **DONE**

- **Current state:** CSV upload uses `await file.read()` with no size limit. Parse has no row cap. Very large files can exhaust memory or cause long-running syncs and timeouts.
- **Implementation path:**
  - **Max upload size:** Define a limit (e.g. 5–10 MB). Read the file in chunks or use framework support (e.g. FastAPI/Starlette body size limit) so that oversized uploads are rejected before full read. Return 413 or 400 with a clear message (“CSV must be under X MB”).
  - **Max rows:** The parser refactor (done first) already enforces a row limit and returns a structured result; the route or parser uses a configurable limit (e.g. env `CSV_MAX_ROWS`). When over limit, reject with a clear message (“CSV has N rows; maximum is M”). Apply the same limit to both sync and preview. See [PARSER_IMPROVEMENT_PLAN.md](PARSER_IMPROVEMENT_PLAN.md) sections 7 and 8.
  - **Docs:** In CSV format / README, state the max file size and max rows so users know what to expect.
- **Success:** Oversized or over-long CSVs are rejected with a clear error; no unbounded memory or runtimes.
- **Implemented:** File size limit (10 MB, `CSV_MAX_UPLOAD_BYTES`), chunked read, 413; row limit in parser (`CSV_MAX_ROWS`, 1000); README documents both.

### 1.3 Cookie `secure` flag — **DONE**

- **Current state:** Cookies use `httponly=True` and `samesite="lax"` but not `secure`. On HTTPS, cookies should not be sent over HTTP.
- **Implementation path:**
  - **When:** Every place that sets the account or OAuth state cookie.
  - **Logic:** Set `secure=True` when the app is served over HTTPS. Derive this from config (e.g. `BASE_URL.startswith("https://")`) so it’s correct in production without code changes. In local dev with `http://localhost`, keep `secure=False`.
  - **Docs:** Note in deployment docs that `BASE_URL` must be correct so cookie security is applied in production.
- **Success:** In production (HTTPS), session cookies are not sent on HTTP; in dev, behavior unchanged.
- **Implemented:** `secure=BASE_URL.startswith("https")` on set_cookie (connect, callback) and delete_cookie (disconnect). README notes production BASE_URL and SECRET_KEY.

---

## Phase 2: Observability — **DONE**

**Goal:** You can see what the app is doing and whether it’s healthy, without guessing.

### 2.1 Structured logging — **DONE**

- **Current state:** No logging. Failures and important events leave no trace except user-visible errors.
- **Implementation path:**
  - **What to log (events):** Connect (account id, no secrets); disconnect (account id); sync start (account id, row count, options summary); sync end (account id, updated/skipped/not_found counts, duration); preview (same idea); webhook received (topic, account id, no body). Log at INFO or equivalent.
  - **What to log (errors):** Any unhandled or handled-but-significant failure: OAuth errors, Jobber API errors (e.g. 5xx, 401 after refresh), DB errors, parse errors. Include a short message and, for server errors, stack trace or exception type. Log at ERROR or WARNING.
  - **Format:** Prefer a single format (e.g. JSON per line) with timestamp, level, message, and optional fields (e.g. `account_id`, `path`, `error_type`). Avoid logging secrets (tokens, full request bodies, client_secret).
  - **Where:** Use the standard `logging` library; configure once at startup (e.g. in lifespan or a small logging module). Optionally allow log level and output (stdout vs file) via env so production can send logs to a file or collector.
- **Success:** A support or ops person can answer “did this user’s sync run?” and “why did it fail?” from logs alone.

### 2.2 Request or correlation context (optional but recommended) — **DONE**

- **Goal:** Tie log lines for a single request (e.g. one sync or one webhook) together.
- **Implementation path:**
  - **When:** At the start of each request (middleware or dependency), generate or read a request id (e.g. UUID). Store it in request state or context.
  - **Use:** Include this id in every log line for that request. If you ever return an error page or JSON with an “error reference”, use this id so users can report it and you can find the logs.
  - **Scope:** At minimum, use it for sync, preview, and webhook; optionally for OAuth callback and disconnect.
- **Success:** One sync’s logs can be grepped by a single id; user-reported “error ref X” maps to one request.

### 2.3 Deep health check — **DONE**

- **Current state:** `/health` returns a static “ok” with no dependency checks.
- **Implementation path:**
  - **DB check:** Open a DB connection (or run a trivial query, e.g. `SELECT 1`) and close it. If it fails, return 503 and optionally a body like `{"status": "unhealthy", "db": "error"}`. If it succeeds, include something like `"db": "ok"` in the body.
  - **Optional Jobber check:** One option is to call a cheap Jobber endpoint (e.g. a minimal GraphQL query) and treat failure as “Jobber unreachable”. This can be a separate endpoint (e.g. `/health/ready`) or a query param (e.g. `?deep=1`) so normal load balancers still hit a cheap `/health`. Document that the deep check may be slower and can fail when Jobber is down.
  - **Response:** Keep 200 for “all good”; 503 for “unhealthy” so orchestrators can stop sending traffic. Keep the body small and consistent (e.g. JSON with `status` and component status).
- **Success:** A load balancer or operator can distinguish “app process is up” from “app can talk to DB (and optionally Jobber)”.

---

## Phase 3: Error handling and resilience — **DONE**

**Goal:** External failures are handled predictably and logged; users see clear messages without leaking internals.

### 3.1 Centralized error handling and user messages — **DONE**

- **Current state:** Errors are handled in each route or in sync; messages are ad hoc. Some failures may surface stack traces or raw exceptions to the client.
- **Implementation path:**
  - **Boundary:** Define a small set of “user-facing” error types or messages (e.g. “Not connected”, “Session expired”, “Jobber is temporarily unavailable”, “Invalid CSV”, “File too large”). In routes and sync, catch known cases and map them to these messages and appropriate HTTP status codes (403, 400, 502, etc.). **CSV/parse errors:** The parser (refactored first) raises specific errors; map those (missing columns, no valid rows, encoding, too many rows) to “Invalid CSV” or “File too large” per [PARSER_IMPROVEMENT_PLAN.md](PARSER_IMPROVEMENT_PLAN.md) (section 8).
  - **Unknown errors:** For unhandled or unexpected exceptions, log the full error (and request id) at ERROR, then return a generic message (“Something went wrong; please try again”) and 500. Never send stack traces or internal details to the client in production.
  - **Consistency:** Prefer a single helper or middleware that turns “known error” vs “unknown exception” into a consistent JSON or HTML response so the dashboard and API behave the same.
- **Success:** Users never see stack traces; they see clear, safe messages; ops see full details in logs.

- **Implemented:** Global exception handler for `Exception`: logs at ERROR with request_id and exc_info, returns 500 with "Something went wrong; please try again." TokenExpiredError from sync/preview/test-run mapped to 403 "Session expired; please reconnect to Jobber." with WARNING log. ParseError and FileTooLargeError already return 400/413 with safe messages.

### 3.2 Timeouts and retries (review only) — **DONE**

- **Current state:** GraphQL and OAuth calls already use timeouts; sync retries once on 401 after refresh.
- **Implementation path:**
  - **Review:** Confirm all outbound HTTP calls (Jobber GraphQL, OAuth token, account info, app disconnect) have explicit timeouts. Document the values (e.g. in JOBBER_SCHEMA_NOTES or a runbook).
  - **Optional:** If Jobber often returns 5xx or timeouts, consider one retry with backoff for idempotent operations (e.g. product fetch). Keep the current “single retry on 401” for auth. Do not add retries without logging so you can see flakiness.
- **Success:** No call runs forever; retry policy is documented and visible in logs if you add more.
- **Implemented:** Reviewed; OAuth 15s, GraphQL 30s. Documented in README. Single retry on 401 after refresh in sync (unchanged).

---

## Phase 4: Dependencies and maintenance — **DONE**

**Goal:** Upgrades and security issues are manageable and predictable.

### 4.1 Dependency pinning and audit — **DONE**

- **Current state:** `requirements.txt` used minimum versions (e.g. `>=`) and no upper bounds.
- **Implementation path:**
  - **Pinning:** After testing a known-good set, pin exact versions (e.g. `==`) or use a lockfile (e.g. `pip-tools` with `requirements.in` → `requirements.txt`). Document in README that deploys should install from the locked file.
  - **Audit:** Run `pip-audit` or `safety` (or your platform’s dependency scan) in CI or before release. Fix or document any known vulnerabilities; for optional or dev-only deps, document why a finding is accepted if so.
  - **Updates:** Periodically bump deps in a branch, run tests, and update the lockfile. Document the cadence (e.g. “every quarter” or “before each release”).
- **Success:** Builds are reproducible; known vulnerable versions are not shipped; upgrade path is clear.
- **Implemented:** requirements.txt pinned to exact versions (==). CI runs pip-audit after install. README: deploy from requirements.txt; update cadence (before release or quarterly), run pytest and pip-audit after bumps.

---

## Phase 5: Optional hardening — **DONE**

**Goal:** Extra protection for multi-tenant or higher-risk environments; skip or defer if not needed.

### 5.1 Rate limiting — **DONE**

- **Current state:** No application-level rate limiting. A misbehaving client or script could hammer sync or webhooks.
- **Implementation path:**
  - **Where:** Consider limits on POST `/api/sync`, POST `/api/sync/preview`, and POST `/webhooks/jobber`. Optionally on POST OAuth callback. Leave GET and health unrestricted.
  - **How:** Either in-app (e.g. slowapi or a small middleware with an in-memory or Redis store) or at the reverse proxy (e.g. nginx rate_limit). Per-IP or per-account-id (when authenticated) are both reasonable.
  - **Defaults:** Start with generous limits (e.g. 60 syncs per minute per account or per IP) and tune from logs. Document the limits and how to change them.
- **Success:** A single client cannot indefinitely starve others or trigger abuse alerts from Jobber.
- **Implemented:** slowapi applied to POST `/api/sync`, `/api/sync/preview`, `/api/sync/test-run`, `/webhooks/jobber`. Key: account_id from cookie when present, else IP. Default `60/minute`; override via `RATE_LIMIT` env. 429 JSON when exceeded. Documented in README.

### 5.2 Webhook idempotency or replay protection (optional) — **DONE**

- **Current state:** Webhook verifies HMAC and processes APP_DISCONNECT. Duplicate deliveries could cause duplicate delete_connection calls (likely harmless).
- **Implementation path:**
  - **If needed:** Store the last processed webhook id or (topic, accountId, timestamp) and ignore duplicates within a short window. Requires a small table or cache and cleanup of old entries. Only add if you see duplicate deliveries or need strict idempotency.
- **Success:** Duplicate webhook payloads don’t cause duplicate side effects (or you’ve documented that they’re safe as-is).
- **Implemented:** In-memory dedupe by (topic, account_id, body SHA-256 hash); TTL 5 min. Duplicate payloads within window return 200 without reprocessing. Prune on each request. Documented in README.

---

## Implementation order (concise path)

0. **Parser refactor (do first):** Complete [PARSER_IMPROVEMENT_PLAN.md](PARSER_IMPROVEMENT_PLAN.md). The parser then returns a structured result (rows + skipped counts), supports encoding fallback and row limit, and raises clear parse errors. Phases 1–5 below assume this is done.
1. **Phase 1 (safety rails):** Config validation and SECRET_KEY warning → file size limit in route (row limit already in parser) → cookie `secure`. Each step is independent; do in this order so config is correct before you rely on it in production.
2. **Phase 2 (observability):** Logging (events + errors) → optional request id → deep health. When adding sync/preview logging, include parse outcome (rows accepted, skipped counts) from the parser’s structured result.
3. **Phase 3 (errors):** Centralized error mapping and “never leak internals” → quick review of timeouts/retries. Map parser errors to user messages per parser plan section 8.
4. **Phase 4 (deps):** Pin and audit; document update process. Can run in parallel with Phase 3.
5. **Phase 5 (optional):** Rate limiting and webhook idempotency only if your environment or Jobber’s policies require them.

---

## Definition of “production ready” (for this app)

- **Safe:** No default secrets in production; uploads and rows capped; cookies secure over HTTPS.
- **Observable:** Logs and health allow you to confirm behavior and debug failures without repro in production.
- **Resilient:** Known errors are handled and logged; users see clear messages; dependencies are pinned and audited.
- **Optional:** Rate limiting and idempotency only where your threat model or SLAs need them.

After Phases 1–4 you have a clear, maintainable path to production; Phase 5 is there when you need it.

---

## Parser and CSV handling (prerequisite)

**Complete the parser refactor before starting Phase 1.** The refactor is described in **[PARSER_IMPROVEMENT_PLAN.md](PARSER_IMPROVEMENT_PLAN.md)** and covers:

- **Visibility:** Skipped-row counts and reasons in sync/preview responses (no silent skips).
- **Robustness:** Encoding fallback (e.g. Windows-1252), optional column aliases, and safer cost parsing.
- **Limits and errors:** Row limit and clear user-facing parse-error messages.

Once the parser refactor is done:

- **Phase 1.2:** Add file size limit in the route; row limit is already enforced by the parser (or by the route using the parser result).
- **Phase 2.1:** Logging uses the parser’s structured result (rows accepted, skipped by reason).
- **Phase 3.1:** Routes map the parser’s specific errors to the same user-facing messages and 400 responses.
