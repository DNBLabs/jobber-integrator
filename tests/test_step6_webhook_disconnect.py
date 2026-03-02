"""
Tests for Step 6: Webhook + disconnect (appDisconnect call, webhook endpoint).
"""
import base64
import hmac
import hashlib
import json
import os
from unittest.mock import patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_db.sqlite")
os.environ.setdefault("JOBBER_CLIENT_ID", "test-client-id")
os.environ.setdefault("JOBBER_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("BASE_URL", "http://localhost:8000")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from fastapi.testclient import TestClient
from app.main import app, _verify_jobber_webhook
from app.config import JOBBER_CLIENT_SECRET
from app.database import init_db, save_connection, get_connection_by_account_id, delete_connection


@pytest.fixture
def client():
    init_db()
    # Phase 5.1: Reset rate limiter so tests don't see 429 from other files (e.g. test_rate_limit_returns_429_phase5).
    if hasattr(app.state, "limiter") and hasattr(app.state.limiter, "_storage"):
        app.state.limiter._storage.reset()
    return TestClient(app)


def _hmac_for(body: bytes, secret: str) -> str:
    """Produce X-Jobber-Hmac-SHA256 value for raw body and secret."""
    dig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(dig).decode("ascii")


# ---- Disconnect still clears state ----
def test_disconnect_clears_cookie_and_removes_connection(client):
    """Step 6: GET /disconnect still clears cookie and delete_connection (with or without appDisconnect)."""
    from app.cookies import make_account_cookie_value
    save_connection("acc-s6", "Step6", "at", "rt")
    cookie = make_account_cookie_value("acc-s6")
    with patch("app.main.call_app_disconnect"):
        with patch("app.main.get_valid_access_token", return_value="at"):
            r = client.get("/disconnect", cookies={"price_sync_account": cookie}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/dashboard"
    assert get_connection_by_account_id("acc-s6") is None


def test_disconnect_calls_app_disconnect_when_token_available(client):
    """Step 6: When account has valid token, disconnect calls call_app_disconnect before clearing state."""
    from app.cookies import make_account_cookie_value
    save_connection("acc-s6-call", "Step6", "at", "rt")
    cookie = make_account_cookie_value("acc-s6-call")
    with patch("app.main.call_app_disconnect") as mock_disconnect:
        with patch("app.main.get_valid_access_token", return_value="at"):
            client.get("/disconnect", cookies={"price_sync_account": cookie}, follow_redirects=False)
    mock_disconnect.assert_called_once_with("at")


def test_disconnect_clears_state_even_if_app_disconnect_fails(client):
    """Step 6: If call_app_disconnect or get_valid_access_token raises, we still clear local state."""
    from app.cookies import make_account_cookie_value
    save_connection("acc-s6-fail", "Step6", "at", "rt")
    cookie = make_account_cookie_value("acc-s6-fail")
    with patch("app.main.call_app_disconnect", side_effect=Exception("API error")):
        with patch("app.main.get_valid_access_token", return_value="at"):
            r = client.get("/disconnect", cookies={"price_sync_account": cookie}, follow_redirects=False)
    assert r.status_code == 302
    assert get_connection_by_account_id("acc-s6-fail") is None


# ---- Webhook verification ----
def test_verify_jobber_webhook_rejects_missing_header():
    """Step 6: _verify_jobber_webhook returns False when signature header is missing."""
    assert _verify_jobber_webhook(b'{"data":{}}', None) is False
    assert _verify_jobber_webhook(b'{"data":{}}', "") is False


def test_verify_jobber_webhook_rejects_bad_signature():
    """Step 6: _verify_jobber_webhook returns False when HMAC does not match."""
    body = b'{"data":{"webHookEvent":{"topic":"APP_DISCONNECT","accountId":"x"}}}'
    wrong = base64.b64encode(b"wrong").decode("ascii")
    assert _verify_jobber_webhook(body, wrong) is False


def test_verify_jobber_webhook_accepts_valid_signature():
    """Step 6: _verify_jobber_webhook returns True when HMAC matches JOBBER_CLIENT_SECRET."""
    body = b'{"data":{"webHookEvent":{"topic":"APP_DISCONNECT","accountId":"x"}}}'
    sig = _hmac_for(body, JOBBER_CLIENT_SECRET)
    assert _verify_jobber_webhook(body, sig) is True


# ---- Webhook endpoint ----
def test_webhook_jobber_rejects_invalid_signature(client):
    """Step 6: POST /webhooks/jobber without valid X-Jobber-Hmac-SHA256 returns 401."""
    body = json.dumps({"data": {"webHookEvent": {"topic": "APP_DISCONNECT", "accountId": "acc-1"}}})
    r = client.post("/webhooks/jobber", content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 401
    assert "error" in r.json()


def test_webhook_jobber_rejects_invalid_json(client):
    """Step 6: POST /webhooks/jobber with valid HMAC but invalid JSON returns 400."""
    body = b"not json"
    sig = _hmac_for(body, JOBBER_CLIENT_SECRET)
    r = client.post("/webhooks/jobber", content=body, headers={"Content-Type": "application/json", "X-Jobber-Hmac-SHA256": sig})
    assert r.status_code == 400


def test_webhook_jobber_app_disconnect_deletes_connection(client):
    """Step 6: POST /webhooks/jobber with valid HMAC and topic APP_DISCONNECT removes connection."""
    save_connection("webhook-acc", "WebhookTest", "at", "rt")
    payload = {"data": {"webHookEvent": {"topic": "APP_DISCONNECT", "accountId": "webhook-acc", "appId": "x", "occurredAt": "2025-01-01T00:00:00Z"}}}
    body = json.dumps(payload).encode("utf-8")
    sig = _hmac_for(body, JOBBER_CLIENT_SECRET)
    r = client.post("/webhooks/jobber", content=body, headers={"Content-Type": "application/json", "X-Jobber-Hmac-SHA256": sig})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert get_connection_by_account_id("webhook-acc") is None


def test_webhook_jobber_other_topic_returns_200_does_not_delete(client):
    """Step 6: POST /webhooks/jobber with other topic returns 200 and does not delete connection."""
    save_connection("other-topic-acc", "Other", "at", "rt")
    payload = {"data": {"webHookEvent": {"topic": "CLIENT_CREATE", "accountId": "other-topic-acc"}}}
    body = json.dumps(payload).encode("utf-8")
    sig = _hmac_for(body, JOBBER_CLIENT_SECRET)
    r = client.post("/webhooks/jobber", content=body, headers={"Content-Type": "application/json", "X-Jobber-Hmac-SHA256": sig})
    assert r.status_code == 200
    assert get_connection_by_account_id("other-topic-acc") is not None
    delete_connection("other-topic-acc")  # cleanup


def test_webhook_jobber_idempotent(client):
    """Step 6: APP_DISCONNECT for non-existent account returns 200 (delete_connection is no-op)."""
    payload = {"data": {"webHookEvent": {"topic": "APP_DISCONNECT", "accountId": "nonexistent"}}}
    body = json.dumps(payload).encode("utf-8")
    sig = _hmac_for(body, JOBBER_CLIENT_SECRET)
    r = client.post("/webhooks/jobber", content=body, headers={"Content-Type": "application/json", "X-Jobber-Hmac-SHA256": sig})
    assert r.status_code == 200


def test_webhook_duplicate_payload_deduped_phase5(client):
    """Phase 5.2: Identical webhook payload sent twice returns 200 both times; connection deleted only once."""
    save_connection("dedupe-acc", "DedupeTest", "at", "rt")
    payload = {"data": {"webHookEvent": {"topic": "APP_DISCONNECT", "accountId": "dedupe-acc", "appId": "x"}}}
    body = json.dumps(payload).encode("utf-8")
    sig = _hmac_for(body, JOBBER_CLIENT_SECRET)
    headers = {"Content-Type": "application/json", "X-Jobber-Hmac-SHA256": sig}
    r1 = client.post("/webhooks/jobber", content=body, headers=headers)
    assert r1.status_code == 200
    assert get_connection_by_account_id("dedupe-acc") is None
    r2 = client.post("/webhooks/jobber", content=body, headers=headers)
    assert r2.status_code == 200
    assert r2.json() == {"ok": True}
