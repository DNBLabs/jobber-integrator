# Test CSV: test_sync_scenarios.csv

The file is in the **project root:** `../test_sync_scenarios.csv` (relative to this doc).

Use this file to manually test all sync and preview behaviours. The app matches **Part_Num** to Jobber product **name** (or **code** if your Jobber schema supports it).

## Setup in Jobber first

Create **Products & Services** in Jobber with these **exact names** (or codes, if you use code matching) and set their **unit cost** as below. Leave one “not found” name out so it appears in `skus_not_found`.

| Part_Num in CSV | Purpose | Suggested current cost in Jobber | CSV cost | What to expect |
|-----------------|---------|----------------------------------|----------|----------------|
| `TEST_INCREASE` | Price goes up | 5.00 | 20.00 | **Preview:** 1 increase. **Sync:** 1 updated (or 1 skipped if “only increase” is on and current is already 20). |
| `TEST_DECREASE` | Price goes down | 20.00 | 5.00 | **Preview:** 1 decrease. **Sync:** 1 updated. With “Only update when new cost is higher”: 1 **skipped (price protection)**. |
| `TEST_UNCHANGED` | Same price | 10.00 | 10.00 | **Preview:** 1 unchanged. **Sync:** 1 updated. With “only increase”: 1 **skipped (price protection)**. |
| `TEST_NOT_FOUND` | No match in Jobber | *(do not create)* | 99.99 | **Preview:** 1 in “not found”. **Sync:** in `skus_not_found`, 0 updated for this row. |
| `TEST_DECIMAL` | Decimal cost | e.g. 10.00 | 12.50 | Parsed and synced as 12.50. |
| `TEST_INTEGER` | Whole number cost | e.g. 80 | 100 | Parsed and synced as 100. |
| `TEST_ZERO_COST` | Zero cost | e.g. 1.00 | 0.00 | **Preview:** decrease (or unchanged if current 0). **Sync:** updates cost to 0. |
| `EXTRA_NOT_IN_JOBBER` | Second “not found” | *(do not create)* | 1.50 | Same as TEST_NOT_FOUND: preview shows 2 not found total; sync reports 2 in `skus_not_found`. |

Two rows are **skipped by the parser**: empty Part_Num (`,10.00`) and invalid Trade_Cost (`SKIP_BAD_COST,not-a-number`). They never appear in preview or sync counts.

So in Jobber, create products named: **TEST_INCREASE**, **TEST_DECREASE**, **TEST_UNCHANGED**, **TEST_DECIMAL**, **TEST_INTEGER**, **TEST_ZERO_COST**. Do **not** create **TEST_NOT_FOUND** or **EXTRA_NOT_IN_JOBBER**.

## What to test

1. **Preview**
   - Upload `test_sync_scenarios.csv`, click **Preview**.
   - Check: increases, decreases, unchanged, and “N product(s) not found in Jobber” match the table above (e.g. 2 not found, rest split by your current costs).

2. **Sync now (no options)**
   - Click **Sync now** (leave “Only update when new cost is higher” **unchecked**).
   - Expect: “Updated 6 product(s)” (the 6 that exist in Jobber), “Not found: TEST_NOT_FOUND, EXTRA_NOT_IN_JOBBER”.

3. **Price protection**
   - Set Jobber costs as in the table (e.g. TEST_DECREASE = 20, TEST_UNCHANGED = 10).
   - Check **Only update when new cost is higher than current cost**.
   - Sync with the same CSV.
   - Expect: fewer updated (e.g. only TEST_INCREASE, TEST_DECIMAL, TEST_INTEGER if they go up), and “Skipped (price protection): 2” (or 3 if TEST_ZERO_COST is also skipped when current &gt; 0).

4. **Apply after Preview**
   - Preview first, then click **Apply changes** (same file is sent).
   - Result should match a normal **Sync now** with the same options.

5. **CSV format and skipped rows**
   - File has only **Part_Num** and **Trade_Cost**; extra columns are ignored.
   - Rows with **empty Part_Num** are skipped (e.g. row with `,10.00`).
   - Rows where **Trade_Cost** is not a number are skipped (e.g. `SKIP_BAD_COST,not-a-number`). So you should see 8 rows processed (6 found + 2 not found), not 10.

## Optional: code/SKU matching

If your Jobber products use a **code** (SKU) field and the app uses it (Enhancement 1), you can name products in Jobber anything you like but set their **code** to the Part_Num values above (e.g. code `TEST_INCREASE`). Matching will then be by code first, then name.
