"""
One-off script to run sync with markup and verify the defaultUnitCost mutation.
Uses the first connected account in the DB and wholesaler_prices.csv.
Respects CSV_MAX_ROWS env (default 1000) for row limit.
"""
import os
import sys
from pathlib import Path

# Project root
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import _get_connection, init_db
from app.sync import ParseError, parse_csv_from_bytes, run_sync

def main():
    init_db()
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT jobber_account_id FROM jobber_connections LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    if not row:
        print("No connected account. Connect via the app (http://localhost:8000) first.")
        return 1

    account_id = row["jobber_account_id"]
    csv_path = Path(__file__).parent / "wholesaler_prices.csv"
    if not csv_path.exists():
        print(f"CSV not found: {csv_path}")
        return 1

    content = csv_path.read_bytes()
    try:
        max_rows = int(os.getenv("CSV_MAX_ROWS", "1000"))
        max_rows = max(1, max_rows)
    except (TypeError, ValueError):
        max_rows = 1000
    try:
        parse_result = parse_csv_from_bytes(content, max_rows=max_rows)
    except ParseError as e:
        print(f"CSV error: {e.message}")
        return 1

    rows = parse_result.rows
    print(f"Account: {account_id}")
    print(f"Rows: {len(rows)}")
    if parse_result.skipped_total:
        print(f"Skipped rows: {parse_result.skipped_total} (reasons: {parse_result.skipped_reasons})")
    print("Running sync with markup 25%...")
    result = run_sync(
        account_id,
        rows,
        only_increase_cost=False,
        fuzzy_match=False,
        markup_percent=25.0,
    )
    print("Result:", result)
    if result.get("error"):
        print("ERROR:", result["error"])
        return 1
    if result.get("updated", 0) > 0:
        print("SUCCESS: Mutation succeeded. Updated", result["updated"], "product(s) (cost + defaultUnitCost).")
    else:
        print("No products updated (check skus_not_found or that Part_Num matches Jobber products).")
    return 0

if __name__ == "__main__":
    sys.exit(main())
