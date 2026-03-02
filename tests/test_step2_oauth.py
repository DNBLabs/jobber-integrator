"""
Stress tests for Step 2: OAuth connect, callback, dashboard, disconnect.
Uses in-memory SQLite and mocks Jobber API calls.
"""
import os
import pytest
from unittest.mock import patch

# Set env before importing app (conftest runs first, but be explicit for standalone)
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JOBBER_CLIENT_ID", "test-client-id")
os.environ.setdefault("JOBBER_CLIENT_SECRET", "test-secret")
os.environ.setdefault("BASE_URL", "http://localhost:8000")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from fastapi.testclient import TestClient
from app.main import app
from app.database import init_db


@pytest.fixture
def client():
    """FastAPI test client; DB table created so tests share same DB file."""
    init_db()  # ensure table exists (lifespan may not have run yet in test order)
    return TestClient(app)


# ---- Root and health ----
def test_root_redirects_to_dashboard(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/dashboard"


def test_health_ok(client):
    """Health returns 200 and status ok; Phase 2.3 includes db status."""
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data.get("status") == "ok"
    assert data.get("db") == "ok"


# ---- Connect: missing client id ----
def test_connect_without_client_id_redirects_with_error(client):
    with patch("app.main.JOBBER_CLIENT_ID", ""):
        r = client.get("/connect", follow_redirects=False)
    assert r.status_code == 302
    assert "error=missing_client_id" in r.headers["location"]


# ---- Connect: success path ----
def test_connect_sets_state_cookie_and_redirects_to_jobber(client):
    r = client.get("/connect", follow_redirects=False)
    assert r.status_code == 302
    location = r.headers["location"]
    assert location.startswith("https://api.getjobber.com/api/oauth/authorize")
    assert "client_id=test-client-id" in location or "client_id=" in location
    assert "state=" in location
    assert "response_type=code" in location
    assert "price_sync_oauth_state" in r.cookies
    set_cookie = r.headers.get("set-cookie", "")
    assert "price_sync_oauth_state" in set_cookie
    assert "HttpOnly" in set_cookie or "httponly" in set_cookie.lower()


# ---- Trailing slash ----
def test_connect_trailing_slash_redirects_to_connect(client):
    r = client.get("/connect/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/connect"


def test_oauth_callback_trailing_slash_preserves_query(client):
    r = client.get("/oauth/callback/?code=abc&state=xyz", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/oauth/callback?code=abc&state=xyz"


# ---- Callback: no code ----
def test_callback_no_code_redirects_with_error(client):
    r = client.get("/oauth/callback", follow_redirects=False)
    assert r.status_code == 302
    assert "error=no_code" in r.headers["location"]


# ---- Callback: state validation ----
def test_callback_code_but_no_state_cookie_invalid_state(client):
    r = client.get("/oauth/callback?code=somecode&state=somestate", follow_redirects=False)
    assert r.status_code == 302
    assert "error=invalid_state" in r.headers["location"]


def test_callback_code_but_no_state_param_invalid_state(client):
    r0 = client.get("/connect", follow_redirects=False)
    state_cookie = r0.cookies.get("price_sync_oauth_state")
    assert state_cookie
    r = client.get(
        "/oauth/callback?code=somecode",
        cookies={"price_sync_oauth_state": state_cookie},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "error=invalid_state" in r.headers["location"]


def test_callback_state_mismatch_invalid_state(client):
    r0 = client.get("/connect", follow_redirects=False)
    state_cookie = r0.cookies.get("price_sync_oauth_state")
    r = client.get(
        "/oauth/callback?code=somecode&state=different-state",
        cookies={"price_sync_oauth_state": state_cookie},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "error=invalid_state" in r.headers["location"]


# ---- Callback: token exchange failure ----
def test_callback_token_exchange_fails_redirects_with_error(client):
    r0 = client.get("/connect", follow_redirects=False)
    state = r0.cookies.get("price_sync_oauth_state")
    with patch("app.main.exchange_code_for_tokens") as mock_exchange:
        mock_exchange.side_effect = Exception("Token exchange failed")
        r = client.get(
            f"/oauth/callback?code=badcode&state={state}",
            cookies={"price_sync_oauth_state": state},
            follow_redirects=False,
        )
    assert r.status_code == 302
    assert "error=token_exchange" in r.headers["location"]


# ---- Callback: account query failure ----
def test_callback_account_query_fails_redirects_with_error(client):
    r0 = client.get("/connect", follow_redirects=False)
    state = r0.cookies.get("price_sync_oauth_state")
    with patch("app.main.exchange_code_for_tokens") as mock_tokens:
        mock_tokens.return_value = {"access_token": "at", "refresh_token": "rt"}
        with patch("app.main.get_account_info") as mock_account:
            mock_account.side_effect = Exception("GraphQL error")
            r = client.get(
                f"/oauth/callback?code=ok&state={state}",
                cookies={"price_sync_oauth_state": state},
                follow_redirects=False,
            )
    assert r.status_code == 302
    assert "error=account_query" in r.headers["location"]


# ---- Callback: empty account id ----
def test_callback_empty_account_id_redirects_with_error(client):
    r0 = client.get("/connect", follow_redirects=False)
    state = r0.cookies.get("price_sync_oauth_state")
    with patch("app.main.exchange_code_for_tokens") as mock_tokens:
        mock_tokens.return_value = {"access_token": "at", "refresh_token": "rt"}
        with patch("app.main.get_account_info") as mock_account:
            mock_account.return_value = {"id": "", "name": "Test"}
            r = client.get(
                f"/oauth/callback?code=ok&state={state}",
                cookies={"price_sync_oauth_state": state},
                follow_redirects=False,
            )
    assert r.status_code == 302
    assert "error=account_query" in r.headers["location"]
    assert "empty_account_id" in r.headers["location"]


# ---- Callback: full success ----
def test_callback_success_sets_cookie_and_redirects_to_dashboard(client):
    r0 = client.get("/connect", follow_redirects=False)
    state = r0.cookies.get("price_sync_oauth_state")
    with patch("app.main.exchange_code_for_tokens") as mock_tokens:
        mock_tokens.return_value = {"access_token": "at", "refresh_token": "rt"}
        with patch("app.main.get_account_info") as mock_account:
            mock_account.return_value = {"id": "acc-123", "name": "Test Account"}
            r = client.get(
                f"/oauth/callback?code=ok&state={state}",
                cookies={"price_sync_oauth_state": state},
                follow_redirects=False,
            )
    assert r.status_code == 302
    assert r.headers["location"] == "/dashboard"
    assert "price_sync_account" in r.headers.get("set-cookie", "")
    set_cookie = r.headers.get("set-cookie", "")
    assert "price_sync_oauth_state" in set_cookie


# ---- Dashboard ----
def test_dashboard_no_cookie_shows_not_connected(client):
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "Connect to Jobber" in r.text
    assert "Connect your Jobber account" in r.text


def test_dashboard_tampered_cookie_shows_not_connected(client):
    r = client.get("/dashboard", cookies={"price_sync_account": "tampered.invalid"})
    assert r.status_code == 200
    assert "Connect to Jobber" in r.text


def test_dashboard_valid_cookie_and_connection_shows_connected(client):
    r0 = client.get("/connect", follow_redirects=False)
    state = r0.cookies.get("price_sync_oauth_state")
    with patch("app.main.exchange_code_for_tokens") as mock_tokens:
        mock_tokens.return_value = {"access_token": "at", "refresh_token": "rt"}
        with patch("app.main.get_account_info") as mock_account:
            mock_account.return_value = {"id": "acc-456", "name": "Dray_Test"}
            r1 = client.get(
                f"/oauth/callback?code=ok&state={state}",
                cookies={"price_sync_oauth_state": state},
                follow_redirects=False,
            )
    account_cookie = r1.cookies.get("price_sync_account")
    assert account_cookie
    r2 = client.get("/dashboard", cookies={"price_sync_account": account_cookie})
    assert r2.status_code == 200
    assert "Connected" in r2.text
    assert "Dray_Test" in r2.text
    assert "Disconnect" in r2.text


# ---- Disconnect ----
def test_disconnect_clears_cookie_and_redirects(client):
    r = client.get("/disconnect", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/dashboard"
    set_cookie = r.headers.get("set-cookie", "")
    assert "price_sync_account" in set_cookie.lower() or "price_sync_account" in set_cookie


def test_disconnect_removes_connection_from_db(client):
    r0 = client.get("/connect", follow_redirects=False)
    state = r0.cookies.get("price_sync_oauth_state")
    with patch("app.main.exchange_code_for_tokens") as mock_tokens:
        mock_tokens.return_value = {"access_token": "at", "refresh_token": "rt"}
        with patch("app.main.get_account_info") as mock_account:
            mock_account.return_value = {"id": "acc-disconnect", "name": "ToRemove"}
            r1 = client.get(
                f"/oauth/callback?code=ok&state={state}",
                cookies={"price_sync_oauth_state": state},
                follow_redirects=False,
            )
    account_cookie = r1.cookies.get("price_sync_account")
    r2 = client.get("/dashboard", cookies={"price_sync_account": account_cookie})
    assert "Connected" in r2.text
    r3 = client.get("/disconnect", cookies={"price_sync_account": account_cookie}, follow_redirects=False)
    assert r3.status_code == 302
    r4 = client.get("/dashboard", cookies=dict(r3.cookies))
    assert "Connect to Jobber" in r4.text


# ---- Cookie signing ----
def test_cookie_tampering_returns_none_for_account_id():
    from app.cookies import get_account_id_from_cookie, make_account_cookie_value
    valid = make_account_cookie_value("acc-123")
    assert get_account_id_from_cookie(valid) == "acc-123"
    assert get_account_id_from_cookie("x.y") is None
    assert get_account_id_from_cookie("no-dot") is None
    assert get_account_id_from_cookie(None) is None
