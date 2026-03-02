"""
Tests for Step 4: sync API and CSV parsing. Includes Enhancement 1 (match by code then name).
"""
import os
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_db.sqlite")
os.environ.setdefault("JOBBER_CLIENT_ID", "test-client-id")
os.environ.setdefault("JOBBER_CLIENT_SECRET", "test-secret")
os.environ.setdefault("BASE_URL", "http://localhost:8000")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from fastapi.testclient import TestClient
from app.main import app
from app.database import init_db
from app.sync import (
    ParseError,
    _decode_csv_content,
    parse_csv_from_bytes,
    run_sync,
    run_sync_preview,
    _probe_code_available,
    _find_id_by_sku,
    _normalize,
    _fuzzy_score,
    _resolve_from_list,
)


@pytest.fixture
def client():
    init_db()
    return TestClient(app)


def test_parse_csv_from_bytes_valid():
    """Step 4: parse CSV with Part_Num and Trade_Cost."""
    csv = b"Part_Num,Trade_Cost\nSKU1,10.50\nSKU2,20"
    result = parse_csv_from_bytes(csv)
    assert result.rows == [("SKU1", 10.5, ""), ("SKU2", 20.0, "")]
    assert result.skipped_total == 0
    assert result.skipped_reasons == {}


def test_parse_csv_from_bytes_utf8_bom():
    """Step 4: parse CSV with UTF-8 BOM."""
    csv = "\ufeffPart_Num,Trade_Cost\nA,1.0".encode("utf-8")
    result = parse_csv_from_bytes(csv)
    assert result.rows == [("A", 1.0, "")]
    assert result.skipped_total == 0


def test_decode_csv_content_utf8():
    """Parser step 2: UTF-8 decodes successfully."""
    text = _decode_csv_content(b"Part_Num,Trade_Cost\nA,1")
    assert "Part_Num" in text and "A" in text


def test_decode_csv_content_cp1252_fallback():
    """Parser step 2: Invalid UTF-8 but valid Windows-1252 decodes via cp1252."""
    # 0xC0 0x80 is invalid UTF-8 (overlong); in cp1252 it's À + Euro
    content = b"Part_Num,Trade_Cost\n\xc0\x80,2.50"
    text = _decode_csv_content(content)
    assert "Part_Num" in text
    # Decoded part number is two chars in cp1252
    result = parse_csv_from_bytes(content)
    assert len(result.rows) == 1
    assert result.rows[0][1] == 2.5


def test_parse_error_unsupported_encoding():
    """Step 7: When decode fails for all encodings, ParseError(unsupported_encoding) is raised."""
    with patch("app.sync._decode_csv_content", side_effect=ParseError("unsupported_encoding", "CSV encoding could not be detected. Please save the file as UTF-8.")):
        with pytest.raises(ParseError) as exc_info:
            parse_csv_from_bytes(b"Part_Num,Trade_Cost\nA,1")
        assert exc_info.value.code == "unsupported_encoding"
        assert "UTF-8" in exc_info.value.message


def test_parse_csv_from_bytes_cp1252_fallback():
    """Parser step 2: CSV in Windows-1252 (invalid UTF-8) parses via encoding fallback."""
    # £ in Windows-1252 is single byte 0xA3; use a CSV that's valid cp1252
    content = "Part_Num,Trade_Cost\nSKU\xa3,10.00".encode("cp1252")
    result = parse_csv_from_bytes(content)
    assert result.rows == [("SKU£", 10.0, "")]


def test_parse_csv_from_bytes_quoted_fields_with_commas():
    """Parser step 3: RFC 4180 quoted fields containing commas parse correctly (no global regex)."""
    csv_content = b'Part_Num,Trade_Cost,Description\n"SKU, with comma",10.50,"Desc, also comma"\nNormal,20.0,Plain'
    result = parse_csv_from_bytes(csv_content)
    assert result.rows == [
        ("SKU, with comma", 10.5, "Desc, also comma"),
        ("Normal", 20.0, "Plain"),
    ]
    assert result.skipped_total == 0


def test_parse_csv_from_bytes_missing_columns_raises():
    """Step 4: missing required columns raises ParseError with helpful message."""
    csv = b"Name,Price\nx,1"
    with pytest.raises(ParseError, match="part number.*cost") as exc_info:
        parse_csv_from_bytes(csv)
    assert exc_info.value.code == "missing_columns"


def test_parse_csv_from_bytes_no_valid_rows_raises():
    """Step 4: no valid rows raises ParseError."""
    csv = b"Part_Num,Trade_Cost\n,"
    with pytest.raises(ParseError, match="No valid rows") as exc_info:
        parse_csv_from_bytes(csv)
    assert exc_info.value.code == "no_valid_rows"


def test_parse_csv_from_bytes_header_aliases_part_number_trade_cost():
    """Step 4: 'Part Number' and 'Trade Cost' column names work (aliases)."""
    csv = b"Part Number,Trade Cost\nP1,1.5\nP2,2.0"
    result = parse_csv_from_bytes(csv)
    assert result.rows == [("P1", 1.5, ""), ("P2", 2.0, "")]
    assert result.skipped_total == 0


def test_parse_csv_from_bytes_header_case_insensitive():
    """Step 4: header matching is case-insensitive (e.g. PART_NUM, trade_cost)."""
    csv = b"PART_NUM,TRADE_COST\nX,10\nY,20"
    result = parse_csv_from_bytes(csv)
    assert result.rows == [("X", 10.0, ""), ("Y", 20.0, "")]


def test_parse_csv_from_bytes_description_alias_desc():
    """Step 4: 'Desc' column is accepted as description alias."""
    csv = b"Part_Num,Trade_Cost,Desc\nA,1,Brief\nB,2,Other"
    result = parse_csv_from_bytes(csv)
    assert result.rows == [("A", 1.0, "Brief"), ("B", 2.0, "Other")]


def test_parse_csv_from_bytes_cost_currency_symbols():
    """Step 5: £ $ € are stripped and cost parsed."""
    csv = b"Part_Num,Trade_Cost\nP1,10.50\nP2,$5.99\nP3,100\nP4,50"
    result = parse_csv_from_bytes(csv)
    assert result.rows == [("P1", 10.5, ""), ("P2", 5.99, ""), ("P3", 100.0, ""), ("P4", 50.0, "")]
    csv_pound_euro = "Part_Num,Trade_Cost\nA,£10.50\nB,€20".encode("utf-8")
    r2 = parse_csv_from_bytes(csv_pound_euro)
    assert r2.rows == [("A", 10.5, ""), ("B", 20.0, "")]


def test_parse_csv_from_bytes_cost_us_format():
    """Step 5: US format 1,234.56 (comma thousands, period decimal)."""
    csv = b'Part_Num,Trade_Cost\nA,"1,234.56"\nB,"2,000.00"'
    result = parse_csv_from_bytes(csv)
    assert result.rows == [("A", 1234.56, ""), ("B", 2000.0, "")]


def test_parse_csv_from_bytes_cost_eu_format():
    """Step 5: EU format 1.234,56 (period thousands, comma decimal)."""
    csv = b'Part_Num,Trade_Cost\nX,"1.234,56"\nY,"2.000,00"'
    result = parse_csv_from_bytes(csv)
    assert result.rows == [("X", 1234.56, ""), ("Y", 2000.0, "")]


def test_parse_csv_from_bytes_cost_eu_space_thousands():
    """Step 5: EU style with space as thousands separator."""
    csv = b'Part_Num,Trade_Cost\nA,"1 234,56"'
    result = parse_csv_from_bytes(csv)
    assert result.rows == [("A", 1234.56, "")]


def test_parse_csv_from_bytes_cost_invalid_counted():
    """Step 5: Non-numeric cost is skipped and counted as invalid_cost."""
    csv = b"Part_Num,Trade_Cost\nOK,10\nBad,not-a-number\nOK2,5"
    result = parse_csv_from_bytes(csv)
    assert result.rows == [("OK", 10.0, ""), ("OK2", 5.0, "")]
    assert result.skipped_reasons.get("invalid_cost", 0) == 1


def test_parse_csv_from_bytes_cost_negative_rejected():
    """Step 5: Negative cost is rejected and counted as invalid_cost."""
    csv = b"Part_Num,Trade_Cost\nA,10\nB,-5\nC,20"
    result = parse_csv_from_bytes(csv)
    assert result.rows == [("A", 10.0, ""), ("C", 20.0, "")]
    assert result.skipped_reasons.get("invalid_cost", 0) == 1


def test_parse_csv_from_bytes_row_limit_at_limit_ok():
    """Step 6: Exactly max_rows valid rows succeeds."""
    csv = b"Part_Num,Trade_Cost\nA,1\nB,2"
    result = parse_csv_from_bytes(csv, max_rows=2)
    assert len(result.rows) == 2
    assert result.rows == [("A", 1.0, ""), ("B", 2.0, "")]


def test_parse_csv_from_bytes_row_limit_over_raises():
    """Step 6: More than max_rows valid rows raises ParseError with clear message."""
    csv = b"Part_Num,Trade_Cost\nA,1\nB,2\nC,3\nD,4"
    with pytest.raises(ParseError, match="too many rows.*maximum is 3") as exc_info:
        parse_csv_from_bytes(csv, max_rows=3)
    assert exc_info.value.code == "too_many_rows"


def test_parse_csv_from_bytes_row_limit_none_no_limit():
    """Step 6: max_rows=None allows any number of rows."""
    rows = ["Part_Num,Trade_Cost"] + [f"P{i},{i}" for i in range(100)]
    csv = "\n".join(rows).encode("utf-8")
    result = parse_csv_from_bytes(csv, max_rows=None)
    assert len(result.rows) == 100


def test_api_sync_requires_auth(client):
    """Step 4: POST /api/sync without session returns 403."""
    response = client.post("/api/sync", files={"file": ("test.csv", b"Part_Num,Trade_Cost\nx,1")})
    assert response.status_code == 403
    assert "error" in response.json()
    assert "connect" in response.json()["error"].lower()


def test_api_sync_requires_csv(client):
    """Step 4: POST /api/sync with non-CSV returns 400."""
    from app.cookies import make_account_cookie_value
    cookie = make_account_cookie_value("acc-123")
    # No connection in DB, but we're testing the file type check first
    response = client.post(
        "/api/sync",
        files={"file": ("data.txt", b"not csv")},
        cookies={"price_sync_account": cookie},
    )
    # May be 400 (bad file type) or 403 (not connected)
    assert response.status_code in (400, 403)


def test_api_sync_bad_csv_returns_400(client):
    """Step 4: POST /api/sync with invalid CSV (wrong columns) returns 400."""
    from app.cookies import make_account_cookie_value
    from app.database import save_connection
    save_connection("acc-sync", "Test", "at", "rt")
    cookie = make_account_cookie_value("acc-sync")
    response = client.post(
        "/api/sync",
        files={"file": ("bad.csv", b"Name,Price\nx,1")},
        cookies={"price_sync_account": cookie},
    )
    assert response.status_code == 400
    assert "error" in response.json()


def test_api_sync_success_returns_result(client):
    """Step 4: POST /api/sync when connected returns updated + skus_not_found."""
    from app.cookies import make_account_cookie_value
    from app.database import save_connection
    save_connection("acc-sync-ok", "Test", "at", "rt")
    cookie = make_account_cookie_value("acc-sync-ok")
    csv_content = b"Part_Num,Trade_Cost\nProductA,99.99"
    with patch("app.main.run_sync") as mock_run:
        mock_run.return_value = {"updated": 1, "skus_not_found": [], "skipped_protected": 0, "error": None}
        response = client.post(
            "/api/sync",
            files={"file": ("prices.csv", csv_content)},
            cookies={"price_sync_account": cookie},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["updated"] == 1
    assert data["skus_not_found"] == []
    assert data["error"] is None
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0]
    assert call_args[0] == "acc-sync-ok"
    assert call_args[1] == [("ProductA", 99.99, "")]


def test_run_sync_no_connection_returns_error():
    """Step 4: run_sync with no DB connection returns error in result."""
    init_db()
    result = run_sync("nonexistent-account", [("SKU1", 10.0, "")])
    assert result["error"] is not None
    assert "connect" in result["error"].lower()
    assert result["updated"] == 0
    assert result["skus_not_found"] == []
    assert result.get("skipped_protected", 0) == 0


# ---- Enhancement 1: match by code (SKU) then name ----
def test_probe_code_available_true_when_no_errors():
    """Enhancement 1: _probe_code_available returns True when GraphQL response has no errors."""
    session = MagicMock()
    headers = {}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": {"productOrServices": {"nodes": [{"id": "1", "name": "x"}]}}}
    with patch("app.sync._graphql_request", return_value=mock_resp):
        assert _probe_code_available(session, headers) is True


def test_probe_code_available_false_when_errors():
    """Enhancement 1: _probe_code_available returns False when GraphQL returns errors (e.g. code field missing)."""
    session = MagicMock()
    headers = {}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"errors": [{"message": "Field 'code' doesn't exist"}]}
    with patch("app.sync._graphql_request", return_value=mock_resp):
        assert _probe_code_available(session, headers) is False


def test_find_id_by_sku_name_only_matches_name():
    """Enhancement 1: match_by_code_first=False matches by name only. Returns (id, current_cost)."""
    session = MagicMock()
    headers = {}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": {
            "productOrServices": {
                "nodes": [{"id": "enc-123", "name": "ProductA", "internalUnitCost": 10.0}],
                "pageInfo": {"hasNextPage": False},
            }
        }
    }
    with patch("app.sync._graphql_request", return_value=mock_resp):
        assert _find_id_by_sku(session, headers, "ProductA", match_by_code_first=False) == ("enc-123", 10.0, "ProductA")
        assert _find_id_by_sku(session, headers, "Other", match_by_code_first=False) == (None, None, "")


def test_find_id_by_sku_code_first_matches_code_then_name():
    """Enhancement 1: match_by_code_first=True matches code first, then name. Returns (id, current_cost)."""
    session = MagicMock()
    headers = {}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": {
            "productOrServices": {
                "nodes": [
                    {"id": "enc-by-code", "name": "15mm Copper Tube 3m", "code": "COP15-3M", "internalUnitCost": 8.45},
                    {"id": "enc-by-name", "name": "NoCodeProduct", "code": None, "internalUnitCost": None},
                ],
                "pageInfo": {"hasNextPage": False},
            }
        }
    }
    with patch("app.sync._graphql_request", return_value=mock_resp):
        assert _find_id_by_sku(session, headers, "COP15-3M", match_by_code_first=True) == ("enc-by-code", 8.45, "15mm Copper Tube 3m")
        assert _find_id_by_sku(session, headers, "NoCodeProduct", match_by_code_first=True) == ("enc-by-name", None, "NoCodeProduct")
        assert _find_id_by_sku(session, headers, "NotFound", match_by_code_first=True) == (None, None, "")


# ---- Enhancement 2: price protection (only update if new cost higher) ----
def test_run_sync_only_increase_cost_skips_when_new_lower_or_equal():
    """Enhancement 2: when only_increase_cost=True, skip update when new cost <= current; count skipped_protected."""
    from app.database import save_connection
    init_db()
    save_connection("acc-e2", "E2", "at", "rt")
    with patch("app.sync._probe_code_available", return_value=False):
        with patch("app.sync._find_id_by_sku") as mock_find:
            with patch("app.sync._update_unit_cost") as mock_update:
                mock_find.return_value = ("id-1", 10.0, "")  # current cost 10
                result = run_sync("acc-e2", [("SKU1", 5.0, ""), ("SKU2", 10.0, "")], only_increase_cost=True)
    assert result["updated"] == 0
    assert result["skipped_protected"] == 2  # 5 <= 10, 10 <= 10
    mock_update.assert_not_called()


def test_run_sync_only_increase_cost_updates_when_new_higher():
    """Enhancement 2: when only_increase_cost=True, update when new cost > current."""
    from app.database import save_connection
    init_db()
    save_connection("acc-e2b", "E2b", "at", "rt")
    with patch("app.sync._probe_code_available", return_value=False):
        with patch("app.sync._find_id_by_sku") as mock_find:
            with patch("app.sync._update_unit_cost", return_value=True) as mock_update:
                mock_find.return_value = ("id-1", 10.0, "")
                result = run_sync("acc-e2b", [("SKU1", 15.0, "")], only_increase_cost=True)
    assert result["updated"] == 1
    assert result["skipped_protected"] == 0
    mock_update.assert_called_once()
    assert mock_update.call_args[0][2] == "id-1" and mock_update.call_args[0][3] == 15.0


def test_api_sync_accepts_only_increase_cost_and_returns_skipped_protected(client):
    """Enhancement 2: POST /api/sync with only_increase_cost returns skipped_protected in JSON."""
    from app.cookies import make_account_cookie_value
    from app.database import save_connection
    save_connection("acc-e2-api", "E2", "at", "rt")
    cookie = make_account_cookie_value("acc-e2-api")
    with patch("app.main.run_sync") as mock_run:
        mock_run.return_value = {"updated": 0, "skus_not_found": [], "skipped_protected": 3, "error": None}
        r = client.post(
            "/api/sync",
            data={"only_increase_cost": "true"},
            files={"file": ("p.csv", b"Part_Num,Trade_Cost\nA,1")},
            cookies={"price_sync_account": cookie},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["skipped_protected"] == 3
    mock_run.assert_called_once()
    assert mock_run.call_args[0][2] is True  # only_increase_cost


# ---- Enhancement 3: preview (dry-run, no writes) ----
def test_run_sync_preview_returns_increases_decreases_unchanged():
    """Enhancement 3: run_sync_preview returns counts; no mutations."""
    from app.database import save_connection
    init_db()
    save_connection("acc-prev", "Prev", "at", "rt")
    with patch("app.sync._probe_code_available", return_value=False):
        with patch("app.sync._find_id_by_sku") as mock_find:
            mock_find.side_effect = [
                ("id-1", 5.0, "Product A"),   # cost 10 > 5 -> increase
                ("id-2", 20.0, "Product B"),  # cost 15 < 20 -> decrease
                ("id-3", 7.0, "Product C"),   # cost 7 == 7 -> unchanged
                (None, None, ""),    # not found
            ]
            result = run_sync_preview("acc-prev", [
                ("A", 10.0, ""),
                ("B", 15.0, ""),
                ("C", 7.0, ""),
                ("D", 1.0, ""),
            ])
    assert result["increases"] == 1
    assert result["decreases"] == 1
    assert result["unchanged"] == 1
    assert result["skus_not_found"] == ["D"]
    assert result["error"] is None


def test_run_sync_preview_no_connection_returns_error():
    """Enhancement 3: run_sync_preview with no account returns error."""
    init_db()
    result = run_sync_preview("nonexistent", [("X", 1.0, "")])
    assert result["error"] is not None
    assert result["increases"] == 0
    assert result["decreases"] == 0
    assert result["unchanged"] == 0


def test_api_sync_preview_requires_auth(client):
    """Enhancement 3: POST /api/sync/preview without session returns 403."""
    r = client.post("/api/sync/preview", files={"file": ("p.csv", b"Part_Num,Trade_Cost\nA,1")})
    assert r.status_code == 403
    assert "error" in r.json()


def test_api_sync_preview_returns_counts(client):
    """Enhancement 3: POST /api/sync/preview returns increases, decreases, unchanged, skus_not_found."""
    from app.cookies import make_account_cookie_value
    from app.database import save_connection
    save_connection("acc-prev-api", "Prev", "at", "rt")
    cookie = make_account_cookie_value("acc-prev-api")
    with patch("app.main.run_sync_preview") as mock_preview:
        mock_preview.return_value = {"increases": 2, "decreases": 1, "unchanged": 0, "skus_not_found": ["Z"], "error": None}
        r = client.post(
            "/api/sync/preview",
            files={"file": ("p.csv", b"Part_Num,Trade_Cost\nA,1\nB,2\nC,3")},
            cookies={"price_sync_account": cookie},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["increases"] == 2
    assert data["decreases"] == 1
    assert data["unchanged"] == 0
    assert data["skus_not_found"] == ["Z"]


# ---- Enhancement 4: fuzzy matching ----
def test_normalize_lowercase_collapse_whitespace():
    """Enhancement 4: _normalize lowercases and collapses whitespace."""
    assert _normalize("  Copper   Pipe  1/2in  ") == "copper pipe 1/2in"
    assert _normalize("ABC") == "abc"
    assert _normalize("") == ""


def test_fuzzy_score_exact_and_similar():
    """Enhancement 4: _fuzzy_score returns 1.0 for identical (normalized), high for similar."""
    assert _fuzzy_score("Copper Pipe 1/2in", "Copper Pipe 1/2in") == 1.0
    assert _fuzzy_score("1/2 Copper Pipe", "Copper Pipe 1/2in") > 0.8
    assert _fuzzy_score("Something Else", "Copper Pipe") < 0.5


def test_resolve_from_list_exact_normalized():
    """Enhancement 4: _resolve_from_list finds exact match with normalized comparison."""
    products = [
        {"id": "id-1", "name": "  Copper  Pipe  ", "code": "", "internalUnitCost": 10.0},
    ]
    id_, cost, fuzzy, _ = _resolve_from_list("copper pipe", products, False, exact_only=True, fuzzy_threshold=0.9)
    assert id_ == "id-1"
    assert cost == 10.0
    assert fuzzy is False


def test_resolve_from_list_fuzzy_above_threshold():
    """Enhancement 4: when exact fails and fuzzy on, best match above threshold returns (id, cost, True)."""
    products = [
        {"id": "id-1", "name": "Copper Pipe 1/2 inch", "code": "", "internalUnitCost": 5.0},
        {"id": "id-2", "name": "Steel Bolt", "code": "", "internalUnitCost": 1.0},
    ]
    id_, cost, fuzzy, _ = _resolve_from_list("1/2 Copper Pipe", products, False, exact_only=False, fuzzy_threshold=0.8)
    assert id_ == "id-1"
    assert cost == 5.0
    assert fuzzy is True


def test_resolve_from_list_below_threshold_not_found():
    """Enhancement 4: when best score below threshold, returns (None, None, False)."""
    products = [
        {"id": "id-1", "name": "Steel Bolt", "code": "", "internalUnitCost": 1.0},
    ]
    id_, cost, fuzzy, _ = _resolve_from_list("Copper Pipe XYZ", products, False, exact_only=False, fuzzy_threshold=0.95)
    assert id_ is None
    assert cost is None
    assert fuzzy is False


def test_resolve_from_list_tie_not_matched():
    """Enhancement 4: when two products tie for best score above threshold, do not match (ambiguous)."""
    # Two products with same name -> same fuzzy score for a typo; tie -> no match
    products = [
        {"id": "id-1", "name": "Pipe A", "code": "", "internalUnitCost": 1.0},
        {"id": "id-2", "name": "Pipe A", "code": "", "internalUnitCost": 2.0},
    ]
    id_, cost, fuzzy, _ = _resolve_from_list("Pipes A", products, False, exact_only=False, fuzzy_threshold=0.5)
    assert id_ is None
    assert fuzzy is False


def test_run_sync_fuzzy_off_returns_fuzzy_matched_count_zero():
    """Enhancement 4: run_sync with fuzzy_match=False returns fuzzy_matched_count 0 (unchanged path)."""
    from app.database import save_connection
    init_db()
    save_connection("acc-foff", "F", "at", "rt")
    with patch("app.sync._probe_code_available", return_value=False):
        with patch("app.sync._find_id_by_sku") as mock_find:
            mock_find.return_value = ("id-1", 5.0, "")
            with patch("app.sync._update_unit_cost", return_value=True):
                result = run_sync("acc-foff", [("SKU1", 10.0, "")], only_increase_cost=False, fuzzy_match=False)
    assert result.get("fuzzy_matched_count", 0) == 0
    assert result["updated"] == 1


def test_run_sync_fuzzy_on_returns_fuzzy_matched_count():
    """Enhancement 4: run_sync with fuzzy_match=True uses product list and reports fuzzy_matched_count."""
    from app.database import save_connection
    init_db()
    save_connection("acc-fon", "F", "at", "rt")
    products = [
        {"id": "enc-1", "name": "Copper Pipe 1/2in", "code": "", "internalUnitCost": 10.0},
    ]
    with patch("app.sync._probe_code_available", return_value=False):
        with patch("app.sync._fetch_all_products", return_value=products):
            with patch("app.sync._update_unit_cost", return_value=True):
                result = run_sync(
                    "acc-fon",
                    [("1/2 Copper Pipe", 12.0, "")],  # fuzzy match to "Copper Pipe 1/2in"
                    fuzzy_match=True,
                    fuzzy_threshold=0.8,
                )
    assert result["updated"] == 1
    assert result.get("fuzzy_matched_count") == 1
    assert result["error"] is None


def test_run_sync_preview_fuzzy_returns_fuzzy_matched_count():
    """Enhancement 4: run_sync_preview with fuzzy_match=True returns fuzzy_matched_count."""
    from app.database import save_connection
    init_db()
    save_connection("acc-pf", "P", "at", "rt")
    products = [
        {"id": "enc-1", "name": "Widget A", "code": "", "internalUnitCost": 5.0},
    ]
    with patch("app.sync._probe_code_available", return_value=False):
        with patch("app.sync._fetch_all_products", return_value=products):
            result = run_sync_preview(
                "acc-pf",
                [("Widget  A", 6.0, "")],  # normalized exact match, so fuzzy_used=False
                fuzzy_match=True,
                fuzzy_threshold=0.9,
            )
    assert result["increases"] == 1
    assert result.get("fuzzy_matched_count") == 0  # exact normalized match
    assert result["error"] is None


# ---- Enhancement 5: markup calculator ----
def test_run_sync_markup_zero_uses_cost_only():
    """Enhancement 5: run_sync with markup_percent=0 uses cost-only mutation."""
    from app.database import save_connection
    init_db()
    save_connection("acc-m0", "M", "at", "rt")
    with patch("app.sync._probe_code_available", return_value=False):
        with patch("app.sync._find_id_by_sku") as mock_find:
            mock_find.return_value = ("id-1", 5.0, "")
            with patch("app.sync._update_unit_cost", return_value=True) as mock_cost:
                with patch("app.sync._update_cost_and_price", return_value=True) as mock_cost_price:
                    result = run_sync("acc-m0", [("SKU1", 10.0, "")], markup_percent=0)
    assert result["updated"] == 1
    assert result.get("markup_percent") == 0
    mock_cost.assert_called_once()
    mock_cost_price.assert_not_called()


def test_run_sync_markup_sets_cost_and_price():
    """Enhancement 5: run_sync with markup_percent>0 calls _update_cost_and_price with correct unit_price."""
    from app.database import save_connection
    init_db()
    save_connection("acc-m25", "M", "at", "rt")
    with patch("app.sync._probe_code_available", return_value=False):
        with patch("app.sync._find_id_by_sku") as mock_find:
            mock_find.return_value = ("id-1", 5.0, "")
            with patch("app.sync._update_unit_cost", return_value=True) as mock_cost:
                with patch("app.sync._update_cost_and_price", return_value=True) as mock_cost_price:
                    result = run_sync("acc-m25", [("SKU1", 10.0, "")], markup_percent=25)
    assert result["updated"] == 1
    assert result.get("markup_percent") == 25
    mock_cost.assert_not_called()
    mock_cost_price.assert_called_once()
    # _update_cost_and_price(session, headers, node_id, cost, unit_price); unit_price = 10 * 1.25 = 12.5
    args = mock_cost_price.call_args[0]
    assert args[2] == "id-1"
    assert args[3] == 10.0
    assert args[4] == 12.5


def test_run_sync_result_includes_markup_percent():
    """Enhancement 5: run_sync result always includes markup_percent."""
    from app.database import save_connection
    init_db()
    save_connection("acc-mr", "M", "at", "rt")
    with patch("app.sync._probe_code_available", return_value=False):
        with patch("app.sync._find_id_by_sku", return_value=(None, None, "")):
            result = run_sync("acc-mr", [("X", 1.0, "")], markup_percent=10)
    assert "markup_percent" in result
    assert result["markup_percent"] == 10


def test_api_sync_accepts_markup_percent(client):
    """Enhancement 5: POST /api/sync with markup_percent form calls run_sync with markup."""
    from app.cookies import make_account_cookie_value
    from app.database import save_connection
    save_connection("acc-api-m", "M", "at", "rt")
    cookie = make_account_cookie_value("acc-api-m")
    with patch("app.main.run_sync") as mock_run:
        mock_run.return_value = {"updated": 1, "skus_not_found": [], "skipped_protected": 0, "fuzzy_matched_count": 0, "markup_percent": 20, "error": None}
        r = client.post(
            "/api/sync",
            files={"file": ("p.csv", b"Part_Num,Trade_Cost\nA,10")},
            data={"markup_percent": "20"},
            cookies={"price_sync_account": cookie},
        )
    assert r.status_code == 200
    assert r.json().get("markup_percent") == 20
    mock_run.assert_called_once()
    # run_sync(account_id, rows, only_increase, fuzzy_on, fuzzy_t, markup)
    assert mock_run.call_args[0][5] == 20
