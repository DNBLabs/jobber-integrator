"""
FastAPI app for Price Sync (marketplace). Step 2: OAuth. Step 3: token refresh. Step 4: sync API. Step 6: webhook + disconnect.
- GET /connect → redirect to Jobber authorize URL (state in cookie)
- GET /oauth/callback → exchange code, store tokens, set account cookie, redirect dashboard
- GET /dashboard → Manage App; shows connected state or Connect button
- GET /disconnect → call appDisconnect, clear account cookie, remove connection (Step 6)
- POST /webhooks/jobber → Jobber disconnect webhook; verify HMAC, delete_connection (Step 6)
- POST /api/sync → upload CSV, sync costs to Jobber (requires connected session)
"""
import asyncio
import base64
import datetime
import hmac
import hashlib
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import BASE_URL, JOBBER_CLIENT_ID, PROJECT_ROOT
from app.cookies import (
    COOKIE_ACCOUNT,
    COOKIE_OAUTH_STATE,
    COOKIE_MAX_AGE,
    make_account_cookie_value,
    get_account_id_from_cookie,
    generate_state,
)
from app.database import init_db, get_connection_by_account_id, save_connection, delete_connection
from app.config import JOBBER_CLIENT_SECRET
from app.jobber_oauth import (
    build_authorize_url,
    call_app_disconnect,
    exchange_code_for_tokens,
    get_account_info,
    get_valid_access_token,
)
from app.sync import parse_csv_from_bytes, run_sync, run_sync_preview

templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")


# Max CSV upload size in bytes (default 10 MB). Can be overridden via env CSV_MAX_UPLOAD_BYTES.
_MAX_CSV_UPLOAD_BYTES_DEFAULT = 10 * 1024 * 1024
try:
    MAX_CSV_UPLOAD_BYTES = int(os.getenv("CSV_MAX_UPLOAD_BYTES", str(_MAX_CSV_UPLOAD_BYTES_DEFAULT)))
except ValueError:
    MAX_CSV_UPLOAD_BYTES = _MAX_CSV_UPLOAD_BYTES_DEFAULT


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Price Sync",
    description="Sync wholesaler CSV pricing to Jobber Products & Services",
    lifespan=lifespan,
)


def _callback_uri() -> str:
    return f"{BASE_URL.rstrip('/')}/oauth/callback"


@app.get("/", response_class=RedirectResponse)
async def root():
    return RedirectResponse(url="/dashboard", status_code=302)


@app.get("/connect/", response_class=RedirectResponse)
async def connect_trailing_slash(request: Request):
    """Redirect /connect/ to /connect so both work."""
    return RedirectResponse(url="/connect", status_code=302)


@app.get("/connect", response_class=RedirectResponse)
async def connect(request: Request):
    """Redirect to Jobber OAuth authorize URL. State stored in cookie."""
    if not JOBBER_CLIENT_ID:
        return RedirectResponse(url="/dashboard?error=missing_client_id", status_code=302)
    state = generate_state()
    redirect_uri = _callback_uri()
    url = build_authorize_url(redirect_uri, state)
    response = RedirectResponse(url=url, status_code=302)
    _secure = BASE_URL.strip().lower().startswith("https")
    response.set_cookie(
        COOKIE_OAUTH_STATE,
        state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=_secure,
    )
    return response


@app.get("/oauth/callback/", response_class=RedirectResponse)
async def oauth_callback_trailing_slash(request: Request):
    """Redirect trailing-slash callback to canonical path (with query string) so Jobber redirects don't 404."""
    path = "/oauth/callback"
    if request.url.query:
        path = path + "?" + request.url.query
    return RedirectResponse(url=path, status_code=302)


@app.get("/oauth/callback")
async def oauth_callback(request: Request):
    """Exchange code for tokens, fetch account, store connection, set session cookie."""
    code = request.query_params.get("code")
    state_param = request.query_params.get("state")
    state_cookie = request.cookies.get(COOKIE_OAUTH_STATE)

    if not code:
        return RedirectResponse(url="/dashboard?error=no_code", status_code=302)
    # Require both state cookie and param to match (CSRF protection)
    if not state_cookie or not state_param or state_cookie != state_param:
        return RedirectResponse(url="/dashboard?error=invalid_state", status_code=302)

    redirect_uri = _callback_uri()
    try:
        tokens = exchange_code_for_tokens(code, redirect_uri)
    except Exception as e:
        msg = quote(str(e)[:80], safe="")
        return RedirectResponse(
            url=f"/dashboard?error=token_exchange&message={msg}",
            status_code=302,
        )

    access_token = tokens["access_token"]
    refresh_token = tokens["refresh_token"]

    try:
        account = get_account_info(access_token)
    except Exception as e:
        msg = quote(str(e)[:80], safe="")
        return RedirectResponse(
            url=f"/dashboard?error=account_query&message={msg}",
            status_code=302,
        )

    account_id = (account.get("id") or "").strip()
    account_name = (account.get("name") or "").strip()
    if not account_id:
        return RedirectResponse(
            url="/dashboard?error=account_query&message=empty_account_id",
            status_code=302,
        )

    # Step 3: store expires_at if Jobber returns expires_in (for proactive refresh)
    expires_at = None
    if tokens.get("expires_in") is not None:
        try:
            expires_at = (
                datetime.datetime.now(datetime.UTC)
                + datetime.timedelta(seconds=int(tokens["expires_in"]))
            ).isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError):
            pass

    save_connection(
        jobber_account_id=account_id,
        jobber_account_name=account_name,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
    )

    response = RedirectResponse(url="/dashboard", status_code=302)
    _secure = BASE_URL.strip().lower().startswith("https")
    response.set_cookie(
        COOKIE_OAUTH_STATE,
        "",
        max_age=0,
        httponly=True,
        samesite="lax",
        secure=_secure,
    )
    response.set_cookie(
        COOKIE_ACCOUNT,
        make_account_cookie_value(account_id),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=_secure,
    )
    return response


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Manage App URL: show connected state or Connect to Jobber."""
    account_cookie = request.cookies.get(COOKIE_ACCOUNT)
    account_id = get_account_id_from_cookie(account_cookie)
    connection = get_connection_by_account_id(account_id) if account_id else None

    connected = connection is not None
    jobber_account_name = connection.get("jobber_account_name") if connection else None

    error = request.query_params.get("error")
    message = request.query_params.get("message", "")

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "base_url": BASE_URL,
            "connected": connected,
            "jobber_account_name": jobber_account_name,
            "error": error,
            "error_message": message,
        },
    )


@app.get("/disconnect", response_class=RedirectResponse)
async def disconnect(request: Request):
    """Step 6: Call Jobber appDisconnect, then clear session and remove connection. Always clear local state even if API fails."""
    account_cookie = request.cookies.get(COOKIE_ACCOUNT)
    account_id = get_account_id_from_cookie(account_cookie)
    if account_id:
        try:
            token = await asyncio.to_thread(get_valid_access_token, account_id)
            await asyncio.to_thread(call_app_disconnect, token)
        except Exception:
            pass  # e.g. token expired, already disconnected in Jobber; still clear local state
        delete_connection(account_id)
    response = RedirectResponse(url="/dashboard", status_code=302)
    response.delete_cookie(COOKIE_ACCOUNT)
    return response


def _verify_jobber_webhook(body: bytes, signature_header: str | None) -> bool:
    """Verify X-Jobber-Hmac-SHA256: HMAC-SHA256(client_secret, body) base64. Constant-time compare."""
    if not JOBBER_CLIENT_SECRET or not signature_header:
        return False
    expected = base64.b64encode(
        hmac.new(
            JOBBER_CLIENT_SECRET.encode("utf-8"),
            body,
            hashlib.sha256,
        ).digest()
    ).decode("ascii")
    return hmac.compare_digest(expected, signature_header.strip())


@app.post("/webhooks/jobber")
async def webhook_jobber(request: Request):
    """Step 6: Jobber disconnect webhook. Verify HMAC, parse topic/accountId, delete_connection. Return 200 quickly."""
    body = await request.body()
    signature = request.headers.get("X-Jobber-Hmac-SHA256")
    if not _verify_jobber_webhook(body, signature):
        return JSONResponse(status_code=401, content={"error": "Invalid signature"})
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
    event = (data.get("data") or {}).get("webHookEvent") or {}
    topic = event.get("topic") or ""
    account_id = event.get("accountId")
    if topic.upper() == "APP_DISCONNECT" and account_id:
        delete_connection(str(account_id))
    return JSONResponse(status_code=200, content={"ok": True})


def _parse_fuzzy_form(fuzzy_match: str | None, fuzzy_threshold: str | None) -> tuple[bool, float]:
    """Enhancement 4: Parse fuzzy_match and fuzzy_threshold from form."""
    on = fuzzy_match and str(fuzzy_match).strip().lower() in ("true", "1", "yes")
    try:
        t = float(fuzzy_threshold or "0.9") if fuzzy_threshold is not None else 0.9
    except (TypeError, ValueError):
        t = 0.9
    return (on, max(0.0, min(1.0, t)))


class FileTooLargeError(Exception):
    """Raised when an uploaded CSV exceeds MAX_CSV_UPLOAD_BYTES."""


async def _read_upload_file_with_limit(file: UploadFile, max_bytes: int) -> bytes:
    """Read uploaded file in chunks and enforce a maximum size."""
    total = 0
    chunks: list[bytes] = []
    chunk_size = 1024 * 1024  # 1 MB
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            mb = max_bytes // (1024 * 1024)
            raise FileTooLargeError(f"CSV must be under {mb} MB")
        chunks.append(chunk)
    return b"".join(chunks)


@app.post("/api/sync/preview")
async def api_sync_preview(
    request: Request,
    file: UploadFile = File(...),
    fuzzy_match: str | None = Form(None),
    fuzzy_threshold: str | None = Form(None),
):
    """Enhancement 3: Preview only. Enhancement 4: fuzzy_match / fuzzy_threshold."""
    account_cookie = request.cookies.get(COOKIE_ACCOUNT)
    account_id = get_account_id_from_cookie(account_cookie)
    if not account_id:
        return JSONResponse(
            status_code=403,
            content={"error": "Not connected; please connect to Jobber first."},
        )
    if not file.filename or not file.filename.lower().endswith(".csv"):
        return JSONResponse(
            status_code=400,
            content={"error": "Please upload a CSV file."},
        )
    try:
        content = await _read_upload_file_with_limit(file, MAX_CSV_UPLOAD_BYTES)
    except FileTooLargeError as e:
        return JSONResponse(status_code=413, content={"error": str(e)})
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Unable to read uploaded file."})
    try:
        parse_result = parse_csv_from_bytes(content)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    fuzzy_on, fuzzy_t = _parse_fuzzy_form(fuzzy_match, fuzzy_threshold)
    rows = parse_result.rows
    result = await asyncio.to_thread(run_sync_preview, account_id, rows, fuzzy_on, fuzzy_t)
    # Include parse summary for visibility (no silent skips)
    result["parse_skipped_total"] = parse_result.skipped_total
    result["parse_skipped_reasons"] = parse_result.skipped_reasons
    if result.get("error") and not result.get("skus_not_found") and result.get("increases", 0) == 0 and result.get("decreases", 0) == 0 and result.get("unchanged", 0) == 0:
        return JSONResponse(status_code=403, content=result)
    return result


def _parse_markup_percent(markup_percent: str | None) -> float:
    """Enhancement 5: Parse markup_percent from form; 0 = off."""
    if markup_percent is None or not str(markup_percent).strip():
        return 0.0
    try:
        return max(0.0, float(markup_percent))
    except (TypeError, ValueError):
        return 0.0


@app.post("/api/sync")
async def api_sync(
    request: Request,
    file: UploadFile = File(...),
    only_increase_cost: str | None = Form(None),
    fuzzy_match: str | None = Form(None),
    fuzzy_threshold: str | None = Form(None),
    markup_percent: str | None = Form(None),
):
    """Step 4: Sync CSV to Jobber. Enhancement 2: only_increase_cost. Enhancement 4: fuzzy. Enhancement 5: markup_percent."""
    account_cookie = request.cookies.get(COOKIE_ACCOUNT)
    account_id = get_account_id_from_cookie(account_cookie)
    if not account_id:
        return JSONResponse(
            status_code=403,
            content={"error": "Not connected; please connect to Jobber first."},
        )
    if not file.filename or not file.filename.lower().endswith(".csv"):
        return JSONResponse(
            status_code=400,
            content={"error": "Please upload a CSV file."},
        )
    try:
        content = await _read_upload_file_with_limit(file, MAX_CSV_UPLOAD_BYTES)
    except FileTooLargeError as e:
        return JSONResponse(status_code=413, content={"error": str(e)})
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Unable to read uploaded file."})
    try:
        parse_result = parse_csv_from_bytes(content)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    only_increase = only_increase_cost and str(only_increase_cost).strip().lower() in ("true", "1", "yes")
    fuzzy_on, fuzzy_t = _parse_fuzzy_form(fuzzy_match, fuzzy_threshold)
    rows = parse_result.rows
    markup = _parse_markup_percent(markup_percent)
    result = await asyncio.to_thread(run_sync, account_id, rows, only_increase, fuzzy_on, fuzzy_t, markup)
    result["parse_skipped_total"] = parse_result.skipped_total
    result["parse_skipped_reasons"] = parse_result.skipped_reasons
    if result.get("error") and result["updated"] == 0 and not result.get("skus_not_found"):
        return JSONResponse(status_code=403, content=result)
    return result


def _is_dev_server() -> bool:
    """True when BASE_URL is localhost (test sync routes enabled only in dev)."""
    return "localhost" in BASE_URL.lower()


@app.post("/api/sync/test-run")
async def api_sync_test_run(request: Request):
    """Test sync using wholesaler_prices.csv from project root, 25%% markup. Enabled only when BASE_URL contains localhost."""
    if not _is_dev_server():
        return JSONResponse(status_code=404, content={"error": "Not available in production."})
    account_cookie = request.cookies.get(COOKIE_ACCOUNT)
    account_id = get_account_id_from_cookie(account_cookie)
    if not account_id:
        return JSONResponse(status_code=403, content={"error": "Not connected; please connect to Jobber first."})
    csv_path = PROJECT_ROOT / "wholesaler_prices.csv"
    if not csv_path.is_file():
        return JSONResponse(status_code=400, content={"error": f"CSV not found: {csv_path}"})
    try:
        parse_result = parse_csv_from_bytes(csv_path.read_bytes())
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    rows = parse_result.rows
    result = await asyncio.to_thread(
        run_sync, account_id, rows, only_increase_cost=False, fuzzy_match=False, markup_percent=25.0
    )
    return result


@app.get("/test-sync", response_class=HTMLResponse)
async def test_sync_page(request: Request):
    """Page with one button to run test sync (wholesaler_prices.csv, 25%% markup). Enabled only when BASE_URL contains localhost."""
    if not _is_dev_server():
        return RedirectResponse(url="/dashboard", status_code=302)
    account_cookie = request.cookies.get(COOKIE_ACCOUNT)
    if not get_account_id_from_cookie(account_cookie):
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse("test_sync.html", {"request": request})


@app.get("/health")
async def health():
    """For deploy health checks (e.g. Render, Railway)."""
    return {"status": "ok"}
