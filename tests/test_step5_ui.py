"""
Stress tests for Step 5: Manage App UI — Atlantis, CSV upload, Sync now, results.
Asserts dashboard HTML and base template include Atlantis CSS, sync form when connected,
and sync script; not connected state has no sync form.
"""
import os
from unittest.mock import patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_db.sqlite")
os.environ.setdefault("JOBBER_CLIENT_ID", "test-client-id")
os.environ.setdefault("JOBBER_CLIENT_SECRET", "test-secret")
os.environ.setdefault("BASE_URL", "http://localhost:8000")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from fastapi.testclient import TestClient
from app.main import app
from app.database import init_db


@pytest.fixture
def client():
    init_db()
    return TestClient(app)


def _get_connected_cookie(client):
    """Perform OAuth flow and return account cookie for connected session."""
    r0 = client.get("/connect", follow_redirects=False)
    state = r0.cookies.get("price_sync_oauth_state")
    assert state
    with patch("app.main.exchange_code_for_tokens") as mock_tokens:
        mock_tokens.return_value = {"access_token": "at", "refresh_token": "rt"}
        with patch("app.main.get_account_info") as mock_account:
            mock_account.return_value = {"id": "acc-step5", "name": "Step5_Test"}
            r1 = client.get(
                f"/oauth/callback?code=ok&state={state}",
                cookies={"price_sync_oauth_state": state},
                follow_redirects=False,
            )
    return r1.cookies.get("price_sync_account")


# ---- Atlantis (base template) ----
def test_base_includes_atlantis_css(client):
    """Step 5: base template loads Atlantis foundation and semantic CSS from unpkg."""
    r = client.get("/dashboard")
    assert r.status_code == 200
    text = r.text
    assert "foundation.css" in text
    assert "semantic.css" in text
    assert "@jobber/design" in text
    assert "unpkg.com" in text


def test_base_has_light_theme_and_container(client):
    """Step 5: base uses data-theme=light and container layout."""
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert 'data-theme="light"' in r.text
    assert 'container' in r.text and 'class="' in r.text


# ---- Dashboard when not connected ----
def test_dashboard_not_connected_no_sync_form(client):
    """Step 5: when not connected, sync form and Sync now are not present."""
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "Connect to Jobber" in r.text
    assert "Connect your Jobber account" in r.text
    assert 'id="sync-form"' not in r.text
    assert "Sync now" not in r.text
    assert 'id="sync-result"' not in r.text


def test_dashboard_not_connected_has_csv_format_card(client):
    """Step 5: CSV format card (Part_Num, Trade_Cost) is always visible."""
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "CSV format" in r.text
    assert "Part_Num" in r.text
    assert "Trade_Cost" in r.text


# ---- Dashboard when connected ----
def test_dashboard_connected_has_sync_form(client):
    """Step 5: when connected, dashboard shows file input and Sync now button."""
    cookie = _get_connected_cookie(client)
    r = client.get("/dashboard", cookies={"price_sync_account": cookie})
    assert r.status_code == 200
    assert "Connected" in r.text
    assert 'id="sync-form"' in r.text
    assert 'id="csv-file"' in r.text
    assert 'name="file"' in r.text
    assert 'accept=".csv"' in r.text or "accept=.csv" in r.text
    assert "Sync now" in r.text
    assert 'id="sync-btn"' in r.text
    assert 'id="sync-status"' in r.text
    assert 'id="sync-result"' in r.text


def test_dashboard_connected_has_preview_and_preview_result(client):
    """Enhancement 3: when connected, dashboard has Preview button and preview-result area."""
    cookie = _get_connected_cookie(client)
    r = client.get("/dashboard", cookies={"price_sync_account": cookie})
    assert r.status_code == 200
    assert "Preview" in r.text
    assert 'id="preview-btn"' in r.text
    assert 'id="preview-result"' in r.text
    assert "/api/sync/preview" in r.text


def test_dashboard_connected_has_disconnect(client):
    """Step 5: when connected, Disconnect link is present."""
    cookie = _get_connected_cookie(client)
    r = client.get("/dashboard", cookies={"price_sync_account": cookie})
    assert r.status_code == 200
    assert "Disconnect" in r.text
    assert 'href="/disconnect"' in r.text


def test_dashboard_connected_has_sync_script(client):
    """Step 5: when connected, inline script POSTs file to /api/sync with FormData."""
    cookie = _get_connected_cookie(client)
    r = client.get("/dashboard", cookies={"price_sync_account": cookie})
    assert r.status_code == 200
    assert "fetch(" in r.text or "fetch (" in r.text
    assert "/api/sync" in r.text
    assert "FormData" in r.text
    assert "credentials" in r.text
    assert "same-origin" in r.text


def test_dashboard_connected_sync_result_classes(client):
    """Step 5: sync result area uses success/error CSS classes for feedback."""
    cookie = _get_connected_cookie(client)
    r = client.get("/dashboard", cookies={"price_sync_account": cookie})
    assert r.status_code == 200
    assert "sync-result" in r.text
    assert "sync-result success" in r.text or "sync-result.success" in r.text
    assert "sync-result error" in r.text or "sync-result.error" in r.text


# ---- API response shape (UI contract) ----
def test_api_sync_json_has_updated_skus_not_found_error(client):
    """Step 5: /api/sync JSON includes keys the dashboard JS expects."""
    from app.cookies import make_account_cookie_value
    from app.database import save_connection
    save_connection("acc-ui", "UI Test", "at", "rt")
    cookie = make_account_cookie_value("acc-ui")
    with patch("app.main.run_sync") as mock_run:
        mock_run.return_value = {"updated": 2, "skus_not_found": ["X"], "skipped_protected": 0, "error": None}
        r = client.post(
            "/api/sync",
            files={"file": ("p.csv", b"Part_Num,Trade_Cost\nA,1\nB,2")},
            cookies={"price_sync_account": cookie},
        )
    assert r.status_code == 200
    data = r.json()
    assert "updated" in data
    assert "skus_not_found" in data
    assert "error" in data
    assert "skipped_protected" in data
    assert data["updated"] == 2
    assert data["skus_not_found"] == ["X"]
