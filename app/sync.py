"""
Step 4: Sync CSV pricing to Jobber. Shared logic for web app (and optional CLI).
Uses get_valid_access_token(account_id); on 401 refreshes and retries once.
"""
import csv
import difflib
import io
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import requests

# GraphQL (same as sync_prices_to_jobber.py)
JOBBER_GRAPHQL_URL = "https://api.getjobber.com/api/graphql"
RATE_LIMIT_SLEEP_SEC = 0.5
GRAPHQL_VERSION = "2026-02-17"

# Enhancement 2: include internalUnitCost so we can compare (price protection: only update if new > current)
QUERY_PRODUCTS_PAGE = """
query GetProductsPage($first: Int!, $after: String) {
  productOrServices(first: $first, after: $after) {
    nodes {
      id
      name
      internalUnitCost
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""

# Enhancement 1 + 2: code (SKU) and internalUnitCost
QUERY_PRODUCTS_PAGE_WITH_CODE = """
query GetProductsPageWithCode($first: Int!, $after: String) {
  productOrServices(first: $first, after: $after) {
    nodes {
      id
      name
      code
      internalUnitCost
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""

MUTATION_UPDATE_COST = """
mutation UpdateProductCost($productOrServiceId: EncodedId!, $internalUnitCost: Float!) {
  productsAndServicesEdit(productOrServiceId: $productOrServiceId, input: { internalUnitCost: $internalUnitCost }) {
    productOrService {
      id
    }
    userErrors {
      message
      path
    }
  }
}
"""

# Enhancement 5: same mutation with selling price. ProductOrService schema uses defaultUnitCost for "default price".
MUTATION_UPDATE_COST_AND_PRICE = """
mutation UpdateProductCostAndPrice($productOrServiceId: EncodedId!, $internalUnitCost: Float!, $defaultUnitCost: Float!) {
  productsAndServicesEdit(productOrServiceId: $productOrServiceId, input: { internalUnitCost: $internalUnitCost, defaultUnitCost: $defaultUnitCost }) {
    productOrService {
      id
    }
    userErrors {
      message
      path
    }
  }
}
"""


def _normalize(s: str) -> str:
    """Enhancement 4: Lowercase, collapse whitespace to single space for matching."""
    if not s:
        return ""
    return " ".join(re.split(r"\s+", s.strip().lower()))


def _fuzzy_score(a: str, b: str, token_sort: bool = True) -> float:
    """Enhancement 4: Similarity 0..1. Optionally token-sort before comparing."""
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    if token_sort:
        na = " ".join(sorted(re.split(r"\s+", na)))
        nb = " ".join(sorted(re.split(r"\s+", nb)))
    return difflib.SequenceMatcher(None, na, nb).ratio()


class TokenExpiredError(Exception):
    """Raised when Jobber returns 401; caller should refresh and retry."""


def _decode_csv_content(content: bytes) -> str:
    """
    Decode CSV bytes to text. Tries utf-8-sig, then cp1252 (Windows-1252).
    Raises ValueError with a clear message if both fail.
    """
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(
        "CSV encoding could not be detected (tried UTF-8, Windows-1252). "
        "Please save the file as UTF-8."
    )


@dataclass
class ParseResult:
    """Result of parsing a CSV upload."""

    rows: List[Tuple[str, float, str]]
    skipped_total: int
    skipped_reasons: Dict[str, int]


def parse_csv_from_bytes(content: bytes) -> ParseResult:
    """
    Parse CSV from bytes per RFC 4180.

    Columns Part_Num and Trade_Cost required; optional Description.
    Returns ParseResult with rows = list of (part_num, cost, description). description is ""
    when no Description column. skipped_total / skipped_reasons report how many data rows were
    ignored and why.

    Raises ValueError if columns missing or no valid rows.
    """
    required = {"Part_Num", "Trade_Cost"}
    rows: List[Tuple[str, float, str]] = []
    skipped_reasons: Dict[str, int] = {}

    def _inc(reason: str) -> None:
        skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1

    text = _decode_csv_content(content)
    text = re.sub(r",\s*\"", ",\"", text)
    full_reader = csv.reader(io.StringIO(text))
    all_rows = list(full_reader)
    header_idx = None
    for i, row in enumerate(all_rows):
        cells = {c.strip() for c in row}
        if required.issubset(cells):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("CSV must contain columns Part_Num and Trade_Cost")
    fieldnames = [c.strip() for c in all_rows[header_idx]]
    if not required.issubset(set(fieldnames)):
        raise ValueError("CSV must contain columns Part_Num and Trade_Cost")
    part_num_idx = fieldnames.index("Part_Num")
    trade_cost_idx = fieldnames.index("Trade_Cost")
    desc_idx = fieldnames.index("Description") if "Description" in fieldnames else None
    for row in all_rows[header_idx + 1 :]:
        if len(row) <= max(part_num_idx, trade_cost_idx):
            _inc("row_too_short")
            continue
        part_num = (row[part_num_idx] or "").strip()
        trade_cost_raw = (row[trade_cost_idx] or "").strip()
        description = (row[desc_idx] or "").strip() if desc_idx is not None and len(row) > desc_idx else ""
        if not part_num and not trade_cost_raw:
            _inc("empty_part_and_cost")
            continue
        if not part_num:
            _inc("empty_part_num")
            continue
        trade_cost_clean = trade_cost_raw.replace("£", "").replace(",", "").strip()
        try:
            cost = float(trade_cost_clean)
        except ValueError:
            _inc("invalid_cost")
            continue
        rows.append((part_num, cost, description))
    if not rows:
        raise ValueError("No valid rows to process")
    skipped_total = sum(skipped_reasons.values())
    return ParseResult(rows=rows, skipped_total=skipped_total, skipped_reasons=skipped_reasons)


def _build_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-JOBBER-GRAPHQL-VERSION": GRAPHQL_VERSION,
    }


def _graphql_request(
    session: requests.Session,
    headers: dict[str, str],
    query: str,
    variables: dict[str, Any] | None = None,
) -> requests.Response:
    payload: dict[str, Any] = {"query": query}
    if variables is not None:
        payload["variables"] = variables
    return session.post(JOBBER_GRAPHQL_URL, headers=headers, json=payload, timeout=30)


def _fetch_all_products(
    session: requests.Session,
    headers: dict[str, str],
    match_by_code_first: bool,
) -> list[dict[str, Any]]:
    """Enhancement 4: Paginate and return all product nodes (id, name, code?, internalUnitCost)."""
    query = QUERY_PRODUCTS_PAGE_WITH_CODE if match_by_code_first else QUERY_PRODUCTS_PAGE
    after: str | None = None
    out: list[dict[str, Any]] = []
    while True:
        variables: dict[str, Any] = {"first": 100, "after": after}
        resp = _graphql_request(session, headers, query, variables)
        if resp.status_code == 401:
            raise TokenExpiredError()
        if resp.status_code != 200:
            return out
        try:
            data = resp.json()
        except json.JSONDecodeError:
            return out
        if data.get("errors"):
            return out
        conn = data.get("data", {}).get("productOrServices")
        if not conn:
            return out
        nodes = conn.get("nodes")
        if nodes is None and conn.get("edges") is not None:
            nodes = [e.get("node") for e in conn["edges"] if e.get("node")]
        nodes = nodes or []
        for node in nodes:
            if isinstance(node, dict) and node.get("id"):
                out.append(node)
        page_info = conn.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return out
        after = page_info.get("endCursor")
        if not after:
            return out
        time.sleep(RATE_LIMIT_SLEEP_SEC)


def _probe_code_available(session: requests.Session, headers: dict[str, str]) -> bool:
    """Enhancement 1: One request with code field. Returns True if schema supports it (no GraphQL errors)."""
    variables: dict[str, Any] = {"first": 1, "after": None}
    resp = _graphql_request(session, headers, QUERY_PRODUCTS_PAGE_WITH_CODE, variables)
    if resp.status_code == 401:
        raise TokenExpiredError()
    if resp.status_code != 200:
        return False
    try:
        data = resp.json()
    except json.JSONDecodeError:
        return False
    if data.get("errors"):
        return False
    return True


def _parse_current_cost(node: dict) -> float | None:
    """Enhancement 2: Extract internalUnitCost from node; None if missing or invalid."""
    val = node.get("internalUnitCost")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _resolve_from_list(
    sku: str,
    products: list[dict[str, Any]],
    match_by_code_first: bool,
    exact_only: bool,
    fuzzy_threshold: float,
) -> tuple[str | None, float | None, bool, str]:
    """
    Enhancement 4: Resolve CSV sku to (product id, current cost, fuzzy_used, jobber_name).
    """
    def _name(node: dict) -> str:
        return (node.get("name") or "").strip()

    sku_norm = _normalize(sku)
    for node in products:
        if match_by_code_first:
            code = (node.get("code") or "").strip()
            if _normalize(code) == sku_norm:
                return (node.get("id"), _parse_current_cost(node), False, _name(node))
        name = _name(node)
        if _normalize(name) == sku_norm:
            return (node.get("id"), _parse_current_cost(node), False, name)
    if exact_only or fuzzy_threshold <= 0:
        return (None, None, False, "")
    best_id: str | None = None
    best_cost: float | None = None
    best_name = ""
    best_score = -1.0
    tie = False
    for node in products:
        name = _name(node)
        code = (node.get("code") or "").strip() if match_by_code_first else ""
        scores = [_fuzzy_score(sku, name)]
        if code:
            scores.append(_fuzzy_score(sku, code))
        score = max(scores)
        if score >= fuzzy_threshold:
            if score > best_score:
                best_score = score
                best_id = node.get("id")
                best_cost = _parse_current_cost(node)
                best_name = name
                tie = False
            elif score == best_score:
                tie = True
    if best_id is not None and not tie:
        return (best_id, best_cost, True, best_name)
    return (None, None, False, "")


def _find_id_by_sku(
    session: requests.Session,
    headers: dict[str, str],
    sku: str,
    match_by_code_first: bool = False,
) -> tuple[str | None, float | None, str]:
    """Paginate through productOrServices; return (node id, current cost or None, jobber name). Raises TokenExpiredError on 401."""
    def _name(node: dict) -> str:
        return (node.get("name") or "").strip()

    query = QUERY_PRODUCTS_PAGE_WITH_CODE if match_by_code_first else QUERY_PRODUCTS_PAGE
    after: str | None = None
    while True:
        variables: dict[str, Any] = {"first": 100, "after": after}
        resp = _graphql_request(session, headers, query, variables)
        if resp.status_code == 401:
            raise TokenExpiredError()
        if resp.status_code != 200:
            return (None, None, "")
        try:
            data = resp.json()
        except json.JSONDecodeError:
            return (None, None, "")
        if data.get("errors"):
            return (None, None, "")
        conn = data.get("data", {}).get("productOrServices")
        if not conn:
            return (None, None, "")
        nodes = conn.get("nodes")
        if nodes is None and conn.get("edges") is not None:
            nodes = [e.get("node") for e in conn["edges"] if e.get("node")]
        nodes = nodes or []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if match_by_code_first and (node.get("code") or "").strip() == sku:
                return (node.get("id"), _parse_current_cost(node), _name(node))
            if _name(node) == sku:
                return (node.get("id"), _parse_current_cost(node), _name(node))
        page_info = conn.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return (None, None, "")
        after = page_info.get("endCursor")
        if not after:
            return (None, None, "")
        time.sleep(RATE_LIMIT_SLEEP_SEC)


def _update_unit_cost(
    session: requests.Session,
    headers: dict[str, str],
    node_id: str,
    cost: float,
) -> bool:
    """Run mutation to set internalUnitCost. Returns True on success. Raises TokenExpiredError on 401."""
    variables = {"productOrServiceId": node_id, "internalUnitCost": cost}
    resp = _graphql_request(session, headers, MUTATION_UPDATE_COST, variables)
    if resp.status_code == 401:
        raise TokenExpiredError()
    if resp.status_code != 200:
        return False
    try:
        data = resp.json()
    except json.JSONDecodeError:
        return False
    if data.get("errors"):
        return False
    payload = data.get("data", {}).get("productsAndServicesEdit") or {}
    user_errors = payload.get("userErrors") or []
    return len(user_errors) == 0


def _update_cost_and_price(
    session: requests.Session,
    headers: dict[str, str],
    node_id: str,
    cost: float,
    unit_price: float,
) -> bool:
    """Enhancement 5: Set internalUnitCost and defaultUnitCost (selling price) in one call. Raises TokenExpiredError on 401."""
    variables = {
        "productOrServiceId": node_id,
        "internalUnitCost": cost,
        "defaultUnitCost": round(unit_price, 2),
    }
    resp = _graphql_request(session, headers, MUTATION_UPDATE_COST_AND_PRICE, variables)
    if resp.status_code == 401:
        raise TokenExpiredError()
    if resp.status_code != 200:
        return False
    try:
        data = resp.json()
    except json.JSONDecodeError:
        return False
    if data.get("errors"):
        return False
    payload = data.get("data", {}).get("productsAndServicesEdit") or {}
    user_errors = payload.get("userErrors") or []
    return len(user_errors) == 0


def run_sync(
    account_id: str,
    rows: list[tuple[str, float]],
    only_increase_cost: bool = False,
    fuzzy_match: bool = False,
    fuzzy_threshold: float = 0.9,
    markup_percent: float = 0.0,
) -> dict[str, Any]:
    """
    Run sync for the given account_id and CSV rows. Uses get_valid_access_token;
    on 401 refreshes and retries that request once.
    Returns {"updated": int, "skus_not_found": list[str], "skipped_protected": int, "fuzzy_matched_count": int, "error": str | None}.
    Enhancement 2: when only_increase_cost=True, skip update when new cost <= current cost (count in skipped_protected).
    Enhancement 4: when fuzzy_match=True, resolve from full product list with fuzzy threshold; fuzzy_matched_count in result.
    Enhancement 5: when markup_percent > 0, set unit price = cost * (1 + markup_percent/100); only set when we update cost (skip if price protection skips).
    """
    from app.database import get_connection_by_account_id, update_tokens
    from app.jobber_oauth import get_valid_access_token, refresh_access_token

    markup_val = 0.0 if markup_percent is None else max(0.0, float(markup_percent))
    result: dict[str, Any] = {"updated": 0, "skus_not_found": [], "skipped_protected": 0, "fuzzy_matched_count": 0, "markup_percent": markup_val, "error": None}
    apply_markup = markup_val > 0
    conn_row = get_connection_by_account_id(account_id)
    if not conn_row:
        result["error"] = "Not connected; please connect to Jobber first."
        return result

    try:
        token = get_valid_access_token(account_id)
    except ValueError as e:
        result["error"] = str(e)
        return result

    headers = _build_headers(token)
    session = requests.Session()

    # Enhancement 1: probe whether Jobber schema supports product code (SKU); then match by code first or name only
    try:
        match_by_code_first = _probe_code_available(session, headers)
    except TokenExpiredError:
        result["error"] = "Session expired; please reconnect to Jobber."
        return result
    time.sleep(RATE_LIMIT_SLEEP_SEC)

    def refresh_and_retry():
        nonlocal token, headers
        try:
            data = refresh_access_token(conn_row["refresh_token"])
        except Exception as e:
            result["error"] = f"Session expired; please reconnect to Jobber. ({e})"
            return None
        import datetime
        new_access = data["access_token"]
        new_refresh = data["refresh_token"]
        expires_in = data.get("expires_in")
        new_expires_at = None
        if expires_in is not None:
            now = datetime.datetime.now(datetime.UTC)
            new_expires_at = (now + datetime.timedelta(seconds=int(expires_in))).isoformat().replace("+00:00", "Z")
        update_tokens(account_id, new_access, new_refresh, new_expires_at)
        conn_row["access_token"] = new_access
        conn_row["refresh_token"] = new_refresh
        token = new_access
        headers = _build_headers(token)
        return token

    threshold = max(0.0, min(1.0, float(fuzzy_threshold))) if fuzzy_match else 0.0

    if fuzzy_match:
        try:
            products = _fetch_all_products(session, headers, match_by_code_first)
        except TokenExpiredError:
            if refresh_and_retry() is None:
                return result
            try:
                products = _fetch_all_products(session, headers, match_by_code_first)
            except TokenExpiredError:
                result["error"] = "Session expired; please reconnect to Jobber."
                return result
        time.sleep(RATE_LIMIT_SLEEP_SEC)

        for sku, cost, _ in rows:
            if result["error"]:
                break
            node_id, current_cost, fuzzy_used, _ = _resolve_from_list(
                sku, products, match_by_code_first, exact_only=False, fuzzy_threshold=threshold
            )
            if fuzzy_used:
                result["fuzzy_matched_count"] += 1
            if node_id is None:
                result["skus_not_found"].append(sku)
                continue
            if only_increase_cost and current_cost is not None and cost <= current_cost:
                result["skipped_protected"] += 1
                continue
            if apply_markup:
                unit_price = round(cost * (1.0 + markup_val / 100.0), 2)
                update_fn = lambda: _update_cost_and_price(session, headers, node_id, cost, unit_price)
            else:
                update_fn = lambda: _update_unit_cost(session, headers, node_id, cost)
            try:
                ok = update_fn()
            except TokenExpiredError:
                if refresh_and_retry() is None:
                    break
                try:
                    ok = update_fn()
                except TokenExpiredError:
                    result["error"] = "Session expired; please reconnect to Jobber."
                    break
            time.sleep(RATE_LIMIT_SLEEP_SEC)
            if not ok:
                result["skus_not_found"].append(sku)
            else:
                result["updated"] += 1
        return result

    for sku, cost, _ in rows:
        if result["error"]:
            break
        try:
            node_id, current_cost, _ = _find_id_by_sku(session, headers, sku, match_by_code_first=match_by_code_first)
        except TokenExpiredError:
            if refresh_and_retry() is None:
                break
            try:
                node_id, current_cost, _ = _find_id_by_sku(session, headers, sku, match_by_code_first=match_by_code_first)
            except TokenExpiredError:
                result["error"] = "Session expired; please reconnect to Jobber."
                break
        time.sleep(RATE_LIMIT_SLEEP_SEC)

        if node_id is None:
            result["skus_not_found"].append(sku)
            continue

        if only_increase_cost and current_cost is not None and cost <= current_cost:
            result["skipped_protected"] += 1
            continue

        if apply_markup:
            unit_price = round(cost * (1.0 + markup_val / 100.0), 2)
            update_fn = lambda: _update_cost_and_price(session, headers, node_id, cost, unit_price)
        else:
            update_fn = lambda: _update_unit_cost(session, headers, node_id, cost)
        try:
            ok = update_fn()
        except TokenExpiredError:
            if refresh_and_retry() is None:
                break
            try:
                ok = update_fn()
            except TokenExpiredError:
                result["error"] = "Session expired; please reconnect to Jobber."
                break
        time.sleep(RATE_LIMIT_SLEEP_SEC)

        if not ok:
            result["skus_not_found"].append(sku)
        else:
            result["updated"] += 1

    return result


def run_sync_preview(
    account_id: str,
    rows: list[tuple[str, float]],
    fuzzy_match: bool = False,
    fuzzy_threshold: float = 0.9,
) -> dict[str, Any]:
    """
    Enhancement 3: Dry-run. Same matching as run_sync, no mutations. Returns counts of
    increases, decreases, unchanged, skus_not_found, and optional fuzzy_matched_count.
    Returns {"increases": int, "decreases": int, "unchanged": int, "skus_not_found": list[str], "fuzzy_matched_count": int, "error": str | None}.
    Enhancement 4: when fuzzy_match=True, resolve from full product list; fuzzy_matched_count in result.
    """
    from app.database import get_connection_by_account_id, update_tokens
    from app.jobber_oauth import get_valid_access_token, refresh_access_token

    result: dict[str, Any] = {
        "increases": 0,
        "decreases": 0,
        "unchanged": 0,
        "skus_not_found": [],
        "fuzzy_matched_count": 0,
        "increases_detail": [],
        "decreases_detail": [],
        "unchanged_detail": [],
        "error": None,
    }
    conn_row = get_connection_by_account_id(account_id)
    if not conn_row:
        result["error"] = "Not connected; please connect to Jobber first."
        return result

    try:
        token = get_valid_access_token(account_id)
    except ValueError as e:
        result["error"] = str(e)
        return result

    headers = _build_headers(token)
    session = requests.Session()

    try:
        match_by_code_first = _probe_code_available(session, headers)
    except TokenExpiredError:
        result["error"] = "Session expired; please reconnect to Jobber."
        return result
    time.sleep(RATE_LIMIT_SLEEP_SEC)

    def refresh_and_retry():
        nonlocal token, headers
        try:
            data = refresh_access_token(conn_row["refresh_token"])
        except Exception as e:
            result["error"] = f"Session expired; please reconnect to Jobber. ({e})"
            return None
        import datetime
        new_access = data["access_token"]
        new_refresh = data["refresh_token"]
        expires_in = data.get("expires_in")
        new_expires_at = None
        if expires_in is not None:
            now = datetime.datetime.now(datetime.UTC)
            new_expires_at = (now + datetime.timedelta(seconds=int(expires_in))).isoformat().replace("+00:00", "Z")
        update_tokens(account_id, new_access, new_refresh, new_expires_at)
        conn_row["access_token"] = new_access
        conn_row["refresh_token"] = new_refresh
        token = new_access
        headers = _build_headers(token)
        return token

    threshold = max(0.0, min(1.0, float(fuzzy_threshold))) if fuzzy_match else 0.0

    if fuzzy_match:
        try:
            products = _fetch_all_products(session, headers, match_by_code_first)
        except TokenExpiredError:
            if refresh_and_retry() is None:
                return result
            try:
                products = _fetch_all_products(session, headers, match_by_code_first)
            except TokenExpiredError:
                result["error"] = "Session expired; please reconnect to Jobber."
                return result
        time.sleep(RATE_LIMIT_SLEEP_SEC)

        for sku, cost, desc in rows:
            if result["error"]:
                break
            node_id, current_cost, fuzzy_used, jobber_name = _resolve_from_list(
                sku, products, match_by_code_first, exact_only=False, fuzzy_threshold=threshold
            )
            if fuzzy_used:
                result["fuzzy_matched_count"] += 1
            if node_id is None:
                result["skus_not_found"].append(sku)
                continue
            effective_current = current_cost if current_cost is not None else 0.0
            current_val = current_cost if current_cost is not None else 0.0
            detail = {"part_num": sku, "csv_cost": cost, "current_cost": current_val, "description": desc, "jobber_name": jobber_name}
            if cost > effective_current:
                result["increases"] += 1
                result["increases_detail"].append(detail)
            elif cost < effective_current:
                result["decreases"] += 1
                result["decreases_detail"].append(detail)
            else:
                result["unchanged"] += 1
                result["unchanged_detail"].append(detail)
        return result

    for sku, cost, desc in rows:
        if result["error"]:
            break
        try:
            node_id, current_cost, jobber_name = _find_id_by_sku(session, headers, sku, match_by_code_first=match_by_code_first)
        except TokenExpiredError:
            if refresh_and_retry() is None:
                break
            try:
                node_id, current_cost, jobber_name = _find_id_by_sku(session, headers, sku, match_by_code_first=match_by_code_first)
            except TokenExpiredError:
                result["error"] = "Session expired; please reconnect to Jobber."
                break
        time.sleep(RATE_LIMIT_SLEEP_SEC)

        if node_id is None:
            result["skus_not_found"].append(sku)
            continue

        effective_current = current_cost if current_cost is not None else 0.0
        current_val = current_cost if current_cost is not None else 0.0
        detail = {"part_num": sku, "csv_cost": cost, "current_cost": current_val, "description": desc, "jobber_name": jobber_name}
        if cost > effective_current:
            result["increases"] += 1
            result["increases_detail"].append(detail)
        elif cost < effective_current:
            result["decreases"] += 1
            result["decreases_detail"].append(detail)
        else:
            result["unchanged"] += 1
            result["unchanged_detail"].append(detail)

    return result
