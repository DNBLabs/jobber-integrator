# Parser improvement plan

This document defines the best-practice, real-world–viable fixes for each limitation identified in the CSV parser deep-dive. The **parser refactor is completed before starting the [Production Readiness Plan](PRODUCTION_READINESS_PLAN.md)**. Implement this plan first; then Phase 1 (config, file/row limits, cookies), Phase 2 (logging), and Phase 3 (error handling) can assume the parser already returns a structured result, clear parse errors, and supports the row limit.

---

## Principles

- **Real-world viability:** Fixes must work with typical wholesaler/ERP exports (encoding, column names, number formats) and scale to thousands of rows without silent data loss.
- **Visibility over silence:** Replace silent skips with countable, user-visible outcomes so support and ops can explain “why did my file process fewer rows?”
- **Incremental and testable:** Each improvement should be verifiable with unit tests and optional integration tests; avoid big-bang rewrites.

---

## Relationship to Production Readiness Plan (parser first)

The parser refactor is done **before** Phases 1–5. When you start the Production Readiness Plan, the parser already provides structured results and clear errors.

| Production plan item | Parser delivers (before Phase 1) | When you do Phase 1+ |
|----------------------|----------------------------------|-----------------------|
| **Phase 1.2** File upload and CSV row limits | Parser enforces row limit and returns structured result (rows + skipped counts). | Route adds **file size** limit before calling parser; row limit is already in parser (or route checks parser result). |
| **Phase 2.1** Structured logging | Parser returns `ParseResult` (rows, skipped_total, skipped_reasons). | Logging includes parse outcome from that result. |
| **Phase 3.1** Centralized error handling | Parser raises specific errors (e.g. `ParseError`) with clear messages. | Routes map those to the same user-facing messages and 400. |

---

## 1. Encoding (UTF-8 only)

**Current limitation:** Only `utf-8-sig` is used; Windows-1252 / ISO-8859-1 exports cause `UnicodeDecodeError` and a generic 500.

**Best-practice fix:**

- **Try UTF-8 first.** If decode fails, try a small set of fallbacks in order: `cp1252` (Windows-1252), then `latin-1` (never fails). Use a clear error only if all fail.
- **Real-world viability:** Most UK/wholesaler exports are UTF-8 or Windows-1252; supporting both covers the vast majority of cases without requiring user re-saves.
- **Implementation:**
  - Add a helper that tries `utf-8-sig`, then `cp1252`, then `latin-1`; return `(str, encoding_used)` or raise a single `ValueError` with message like “CSV encoding could not be detected (tried UTF-8, Windows-1252). Please save the file as UTF-8.”
  - Do **not** auto-detect from BOM only; explicit try/fallback is predictable and testable.
  - Document supported encodings in README or CSV format docs.

**Success:** Users with Windows-1252 or Latin-1 exports can upload without re-saving; others get a clear “save as UTF-8” message instead of a generic error.

---

## 2. Global regex `,\s*"` rewrite

**Current limitation:** `re.sub(r",\s*\"", ",\"", text)` rewrites the whole file and can theoretically alter quoted content; it’s a heuristic.

**Best-practice fix:**

- **Remove the regex.** Rely on `csv.reader` for RFC 4180 parsing; it already handles quoted fields and commas inside quotes. If a specific wholesaler file has “comma then space then quote” in the header row only, that’s rare; accepting it as-is or documenting “no space between comma and quote in header” is safer than rewriting.
- **Real-world viability:** No known production format requires this rewrite; removing it simplifies the parser and avoids edge-case corruption.
- **Implementation:** Delete the `re.sub` line; add a test that parses a CSV with quoted fields containing commas to confirm behavior. If a real file later fails, add a targeted fix (e.g. normalize header row only) rather than a global replace.

**Success:** Parser behavior is predictable and RFC 4180–aligned; no risk of altering valid quoted content.

---

## 3. Header detection and column names

**Current limitation:** Header is found by first row whose set of cells contains both `Part_Num` and `Trade_Cost`; column names are case-sensitive and exact. No support for common variants (`Part Number`, `PART_NUM`, `TradeCost`).

**Best-practice fix:**

- **Keep “first row containing required columns”** for robustness (metadata above header).
- **Add case-insensitive, normalized matching for the required columns:** Normalize header cell by stripping and lowercasing (or collapsing underscores and spaces). Map “Part_Num”, “Part Number”, “part_num”, “PART_NUM” to a single canonical key. Same for “Trade_Cost” / “Trade Cost” / “trade_cost”. Use a fixed mapping table (e.g. `Part_Num` → `["part_num", "part number", "partnumber"]`) so behavior is explicit and testable.
- **Optional “Description”:** Allow one or two common variants (e.g. `Description`, `Desc`, `Product Description`) and map to the same description column.
- **Real-world viability:** Covers most naming variations without requiring config UI; avoids “CSV must contain Part_Num and Trade_Cost” for users whose files say “Part Number” and “Trade Cost”.
- **Implementation:**
  - Define canonical names: `part_num`, `trade_cost`, `description`. Define allowed aliases per canonical.
  - After finding the header row, normalize each header cell (strip, lower, replace spaces with underscore or similar) and match against aliases; set column indices by canonical name. If required columns are missing after alias match, raise the same `ValueError` but with message like “CSV must contain a column for part number (e.g. Part_Num, Part Number) and for cost (e.g. Trade_Cost, Trade Cost).”
  - No duplicate canonical columns (e.g. two columns mapping to part_num); reject or use first.

**Success:** Users with “Part Number” and “Trade Cost” (or common variants) succeed without renaming columns; header detection remains tolerant of leading rows.

---

## 4. Silent skips and visibility

**Current limitation:** Rows are skipped (short row, empty part number, non-numeric cost) with no count or reason; users and support cannot tell how many rows were dropped or why.

**Best-practice fix:**

- **Parser returns a structured result** instead of a bare list: e.g. `ParseResult(rows=list[tuple], skipped_total=int, skipped_reasons=dict[str, int])`. Count by category: `empty_part_num`, `empty_both`, `invalid_cost`, `row_too_short`. Optionally cap per-reason detail (e.g. first 10 skipped part numbers for “invalid_cost”) for API responses.
- **Expose in API and UI:** Preview and sync responses include `parse_skipped_total` and `parse_skipped_reasons` (e.g. `{"empty_part_num": 2, "invalid_cost": 1}`). Dashboard shows a line like “8 rows processed; 2 skipped (empty part number), 1 skipped (invalid cost).”
- **Real-world viability:** Support can say “your file had 3 rows skipped because the cost wasn’t a number” without asking for the file; users can correct their export and re-upload.
- **Implementation:**
  - Change `parse_csv_from_bytes` to return a small dataclass or named tuple: `rows`, `skipped_total`, `skipped_reasons` (and optionally `encoding_used`). Callers (sync, preview, test script) adapt to the new shape.
  - In the parser loop, instead of `continue`, increment the appropriate reason bucket and then continue. After the loop, if `rows` is empty and `skipped_total > 0`, raise ValueError with message that includes “No valid rows; N row(s) skipped (reasons: …).”
  - API: add fields to the JSON response for sync and preview. UI: show the summary in the same result block as “updated / not found”.

**Success:** Every sync/preview shows how many rows were parsed and how many skipped and why; no silent data loss from the user’s perspective.

---

## 5. Cost parsing (formats and symbols)

**Current limitation:** Only `£` and `,` (as thousands separator) are stripped; European style (`1.234,56`) and other symbols (`$`, `€`) break or are skipped.

**Best-practice fix:**

- **Strip common currency symbols** from the raw string before numeric parse: `£`, `$`, `€`, and optionally `USD`, `GBP`, `EUR` as trailing suffixes (with space). Remove commas and spaces used as thousands separators; then decide on decimal separator.
- **Decimal separator:** Assume either “.” or “,” as decimal. Heuristic: if there is exactly one comma and one period, treat the last one as decimal (e.g. `1,234.56` → 1234.56; `1.234,56` → 1234.56). If only one type appears, use it as decimal and strip the other as thousands. If neither, use `float()` on the cleaned string.
- **Real-world viability:** UK/US format (`1,234.56`) and EU format (`1.234,56` or `1 234,56`) are both common; a single heuristic covers most files. Avoid locale configuration for v1; keep logic in code and documented.
- **Implementation:**
  - Normalize: strip currency symbols and suffixes, then strip spaces. Replace “,” used as thousands (e.g. when followed by 3 digits) or “ “ as thousands; then treat remaining “,” or “.” as decimal according to the heuristic above. Fall back to `float(s)` for “simple” forms.
  - Reject or skip (and count under `invalid_cost`) values that are negative if your business rule is “costs must be non-negative”; optionally allow zero. Document that negative costs are rejected.
  - **Optional:** Use `Decimal` for the parsed value and convert to float only at the Jobber API boundary, to avoid float precision issues in future calculations.

**Success:** Most international number formats parse correctly; invalid or negative costs are counted under skipped reasons and never silently dropped without explanation.

---

## 6. Description column

**Current limitation:** Only the exact column name `Description` is used; `Desc` or `Product Description` are ignored.

**Best-practice fix:**

- **Handled by the same alias mapping as in section 3.** Add `Description`, `Desc`, `Product Description` (and optionally `Product_Description`) as aliases for the description column. No separate logic needed beyond the canonical header mapping.

**Success:** Common description column names work without code changes for each new variant.

---

## 7. Performance and scale (row and file limits)

**Current limitation:** Entire file is read into memory and parsed into a full list; no row cap. Large files can exhaust memory or time out.

**Best-practice fix:**

- **File size limit:** Enforced **before** calling the parser (in the route): reject request body above N MB (e.g. 5–10) with 413 or 400 and message “CSV must be under X MB.” This is **Phase 1.2** in the Production Readiness Plan; implement there.
- **Row limit:** After parsing (or during parsing), enforce a maximum number of **accepted** rows (e.g. 500 or 1000, configurable via env). If the file would produce more valid rows, **fail** the request with 400 and message “CSV has more than M rows (N rows found); maximum is M.” Optionally include `parse_skipped_total` so the user knows the file had more data. Parser can either: (a) parse fully and then truncate/fail, or (b) count rows while parsing and stop when limit is reached (then fail if there are more rows in the file). (a) is simpler and consistent with “reject over-long CSV”; (b) saves memory for huge files but is more complex.
- **Real-world viability:** Most wholesaler syncs are hundreds to low thousands of rows; a 1000-row cap is acceptable for v1 and protects the app. Document the limit clearly.
- **Implementation:** Parser receives an optional `max_rows` (default from env or constant). After parsing, if `len(rows) > max_rows`, raise ValueError with a clear message. Alternatively, the route that applies the limit (Phase 1.2) can call the parser and then check `len(rows)`; parser returns `ParseResult` with `rows` so the route can enforce. Prefer single place for limit (route or parser) and document it.

**Success:** Oversized files are rejected before or after parse with a clear message; no unbounded memory use. Aligns with Phase 1.2.

---

## 8. Error messages and API contract

**Current limitation:** Only two hard errors are raised (“CSV must contain columns Part_Num and Trade_Cost” and “No valid rows to process”); encoding and other failures surface as generic server errors.

**Best-practice fix:**

- **Define a small set of parse errors** that the routes map to HTTP 400 and a single user-facing message:
  - `MissingRequiredColumns` → “CSV must contain columns for part number and cost. See [format docs].”
  - `NoValidRows` → “No valid rows to process. [If skipped_reasons is present:] N row(s) were skipped (e.g. empty part number, invalid cost).”
  - `UnsupportedEncoding` → “CSV encoding could not be detected. Please save the file as UTF-8.”
  - `FileTooLarge` / `TooManyRows` → “CSV has too many rows or is too large. Maximum: …” (align with Phase 1.2 and 3.1).
- **Implementation:** Parser raises a custom exception (e.g. `ParseError` with code and message) or returns a `Result` type that indicates error; routes catch and map to the same user-facing messages and 400. Never expose stack traces or raw encoding names to the client.

**Success:** All parse failures result in 400 and a clear, safe message; Phase 3.1 centralized error handling can use the same messages.

---

## 9. Implementation order (recommended)

Implement in this order so that each step is testable and the next builds on it:

1. **Skipped-row reporting (section 4)** — **DONE**  
   `ParseResult` and reason counts implemented in `app/sync.py`; `/api/sync`, `/api/sync/preview`, and `run_sync_check.py` now surface `parse_skipped_total` and `parse_skipped_reasons` in their results/output. Tests updated to assert the new return type.

2. **Encoding fallback (section 1)** — **DONE**  
   `_decode_csv_content()` in `app/sync.py` tries `utf-8-sig` then `cp1252`; raises `ValueError` with a clear message if both fail. Parser uses it before CSV parse. Tests: UTF-8, UTF-8 BOM, Windows-1252 fallback (`test_decode_csv_content_utf8`, `test_decode_csv_content_cp1252_fallback`, `test_parse_csv_from_bytes_cp1252_fallback`).

3. **Remove global regex (section 2)** — **DONE**  
   Removed `re.sub(r",\s*\"", ",\"", text)` from `parse_csv_from_bytes`; parsing relies on `csv.reader` only. Added `test_parse_csv_from_bytes_quoted_fields_with_commas` for RFC 4180 quoted fields containing commas.

4. **Header aliases and case-insensitive match (section 3 + 6)** — **DONE**  
   _normalize_header_cell() and alias sets in app/sync.py; header row is first row with both part-number and cost columns (by normalized alias). Missing-column error message updated. Tests: exact names, “Part Number”/“Trade Cost”, “Desc”, wrong case.

5. **Cost parsing (section 5)** — **DONE**  
   `_parse_cost()` in app/sync.py: strips £ $ € and trailing USD/GBP/EUR, removes space thousands; decimal heuristic (both comma and period → last is decimal; only one → that is decimal). Returns None for invalid or negative; parser counts under invalid_cost. Tests: currency symbols, US (1,234.56), EU (1.234,56 and 1 234,56), invalid, negative.

6. **Row limit (section 7)** — **DONE**  
   `parse_csv_from_bytes(content, max_rows=None)` accepts optional `max_rows`; when set and `len(rows) > max_rows` raises ValueError with message "CSV has more than M rows (N rows found); maximum is M." App reads `CSV_MAX_ROWS` (default 1000) and passes to parser for sync, preview, and test-run; `run_sync_check.py` uses same env. Documented in README.

7. **Parse error types and API (section 8)** — **DONE**  
   `ParseError(code, message)` in app/sync.py with codes: missing_columns, no_valid_rows, unsupported_encoding, too_many_rows. Parser and _decode_csv_content raise ParseError with user-facing messages (no raw encoding names or stack traces). Routes in main.py catch ParseError and return 400 with `e.message`; run_sync_check.py prints `e.message`. Tests assert codes and messages; test_parse_error_unsupported_encoding added.

File size limit (section 7) is implemented in the **route** as part of Phase 1.2, not inside the parser.

---

## Definition of “parser production ready”

- **Robust:** Supports UTF-8 and Windows-1252; optional Description aliases; header tolerant of metadata rows.
- **Transparent:** Every sync/preview reports how many rows were parsed and how many skipped and why.
- **Bounded:** File size and row count enforced (with Phase 1.2); no unbounded memory or runtimes.
- **Clear:** All parse failures result in 400 and a user-facing message; no silent skips or generic 500s for encoding/format.

After this plan is implemented, the parser is suitable for production use and maintainable; further improvements (e.g. configurable column mapping, streaming parse) can be added later if needed.
