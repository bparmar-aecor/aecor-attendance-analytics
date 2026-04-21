# Aecor — Attendance Analytics Dashboard

Streamlit dashboard for the **Aecor Employee Attendance Analytics System**, built per BRD v1.0 (`AECOR-ATT-BRD-001`). Reads biometric punch data from Supabase (synced from eSSL eTimeTrackLite 12.0) and provides interactive analytics for management.

---

## What changed in this restoration (April 2026)

The previous build had drifted across multiple sessions and had import errors that prevented it from running. Fixed in this version:

| # | Fix |
|---|-----|
| 1 | `secrets.toml` switched to nested `[supabase]` format with the **service-role key** (anon key was silently blocking writes under RLS) |
| 2 | `db.py` rewritten as the single source of truth for the Supabase client — tolerates both nested and flat secret formats |
| 3 | `data_loader.py` no longer creates its own client — imports from `db.get_client` |
| 4 | Added missing functions to `data_loader.py`: `add_leave`, `delete_leave`, `add_regularization`, `load_regularizations`, `load_device_logs_with_regularizations` (the regularization replay layer per BRD §9.2) |
| 5 | Employee column names normalized — pages can now use `name`/`code`/`department` directly while the verbose eSSL-style names remain available |
| 6 | All categories switched to **lowercase** (`normal`/`custom`/`excluded`) consistently across `config.py`, `schema.sql`, `processing.py`, and pages — fixes the silent `SHIFTS.get()` returning `None` for everyone |
| 7 | `processing.py` defensively lowercases category strings so legacy uppercase rows in the DB don't break the engine |
| 8 | `schema.sql` now ships a safe migration block that lowercases existing uppercase rows and updates the CHECK constraint — idempotent, safe to re-run |
| 9 | Renamed `pages/3_User_Management.py` → `pages/6_Settings.py` (matches `app.py` navigation, removes the duplicate `3_` prefix) |
| 10 | `check_data.py` (the diagnostic from your morning debugging session) updated to read the new nested secret format |
| 11 | All 14 unit tests still pass after the case change |

---

## Quick start

### 1. Install
```bash
pip install -r requirements.txt
```

### 2. Configure Supabase credentials
Copy the template and fill in your service-role key:
```bash
mkdir -p .streamlit
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# then edit .streamlit/secrets.toml
```

Or set environment variables:
```bash
export SUPABASE_URL="https://voawhdyaxdivegcncoeq.supabase.co"
export SUPABASE_KEY="<your-service-role-key>"
```

> Use the **service-role key** (not the anon key) — the dashboard needs to write to `employee_categories`, `employee_leaves`, and `punch_regularizations`. Never deploy publicly without authentication in front.

### 3. Create dashboard tables
Run `schema.sql` in the Supabase SQL editor (or via `psql`). It is idempotent — safe to run on an existing project. It creates only the dashboard-owned tables; eSSL-synced tables (`employees`, `device_logs`, etc.) are left untouched.

### 4. Run
```bash
streamlit run app.py
```

### 5. Verify (optional)
```bash
python test_processing.py
# expected: 14/14 passed
```

---

## What's in the box

| File | Purpose |
|------|---------|
| `app.py` | Streamlit entry, sidebar with global date filter |
| `config.py` | **Single source of truth** — shifts, break rules, score weights |
| `db.py` | Supabase client (cached) |
| `schema.sql` | Dashboard-only DDL: categories, leaves, regularizations |
| `processing.py` | Pure-function attendance engine (no I/O, fully testable) |
| `data_loader.py` | Cached Supabase reads + write-back helpers |
| `ingestion.py` | CSV / PDF fallback uploader |
| `pages/` | Streamlit multi-page app |
| `test_processing.py` | 14 unit tests covering BRD examples |
| `check_data.py` | Diagnostic script — run from terminal to verify what's in Supabase for any date |

### Pages
1. **🏢 Organization Overview** — KPIs, daily trend, shift distribution, break analysis, frequent late list, incomplete-hours list, weekday patterns, productivity leaderboard
2. **👤 Individual Employee** — quick stats, daily log, break timeline for a chosen day, productive-hours trend
3. **📅 Leave Management** — mark / view / delete leaves
4. **✏️ Punch Regularization** — add / edit / delete punches with mandatory reason; non-destructive, audit-logged
5. **📤 Upload Data** — CSV / PDF fallback ingestion
6. **⚙️ Settings** — assign employee categories, view active rules

---

## Architecture decisions (and why)

### Pure-function processing engine
`processing.py` has zero I/O and zero Streamlit imports. Everything is functions over DataFrames. This means:
- Easy to unit-test (and we do — 14 tests, all green)
- Trivial to swap the data source later (Postgres → DuckDB → CSVs all work)
- Logic changes never break the UI

### Non-destructive regularizations
Per BRD §9.2, the original `device_logs` data is preserved. Regularizations are stored separately and **replayed at read time** in `data_loader._apply_regularizations`. Pros: full audit trail, easy rollback. Cons: small read-time cost (negligible at this data scale).

### Latest-wins category history
`employee_categories` is append-only with `effective_from`. The `v_employee_current_category` view (in `schema.sql`) returns the latest active category per employee. Historical rows aren't overwritten — they're just shadowed.

### Cached Supabase reads
`@st.cache_data(ttl=60)` on `load_device_logs` and `load_leaves` (300s for `load_employees`). New sync data appears within 60 seconds; mutations call `.clear()` on the relevant cache to force a refresh.

### Late ≠ penalty
The BRD is explicit (§7, §12.1) that lateness is informational only. The score formula uses Attendance (20%) + Hours Completion (40%) + Break Compliance (30%) + Consistency (10%). `is_late` shows up in displays but never in the score. The test `test_late_does_not_penalise_score` enforces this — if anyone tries to add lateness to the score, the test will fail loudly.

---

## Extending the system

### Add a new shift (e.g., Shift C)
Edit `config.py`:
```python
SHIFTS["EVENING"] = ShiftRule(
    name="Evening Shift (C)",
    code="EVENING",
    start=time(15, 0),
    end=time(23, 0),
    required_productive_hours=7.0,
    break_allowed_hours=1.0,
)
```
Then update the CHECK constraint in `schema.sql`:
```sql
ALTER TABLE employee_categories DROP CONSTRAINT employee_categories_category_check;
ALTER TABLE employee_categories ADD CONSTRAINT employee_categories_category_check
    CHECK (category IN ('NORMAL', 'CUSTOM', 'EVENING', 'EXCLUDED'));
```
That's it. All pages, KPIs, and the score automatically pick up the new shift.

### Change break rules
Edit `config.py` constants: `LUNCH_WINDOW_START`, `LONG_BREAK_THRESHOLD_MINUTES`, `PRE_LUNCH_CUTOFF`, etc. Run `python test_processing.py` to confirm nothing broke.

### Change score weights
Edit `SCORE_WEIGHTS` in `config.py`. They must sum to 1.0 — there's an `assert` that enforces it on import.

### Add a new analytics page
Drop a new `pages/N_My_New_Page.py` file. Streamlit auto-discovers it. Use the existing pages as templates — they all follow the same pattern: read sidebar dates → load data → process → render.

### Add a new metric
1. Add the field to `DayResult` in `processing.py`
2. Compute it in `process_day()`
3. Add the column to the display table in whichever page needs it

---

## eSSL column-name compatibility

The data loader tolerates both PascalCase (eSSL-native) and snake_case (Python-native) column names, so it works whether your sync script preserves the original casing or normalizes it:

| eSSL | Snake_case | Used as |
|------|-----------|---------|
| `EmployeeId` | `employee_id` | primary key |
| `EmployeeName` | `name` | display |
| `EmployeeCode` | `code` | display |
| `DepartmentId` | `department_id` | join key |
| `RecordStatus=1` | `record_status=1` | active filter |
| `LogDate` | `log_time` / `log_date` | punch timestamp |

If your sync uses different names, edit `_normalize_employee_cols()` in `data_loader.py`.

---

## Performance notes

- Date filters are applied at the SQL level (`gte`/`lte`) — only relevant rows are fetched
- The processing pass is O(employees × days) with O(punches per day) inside — fast enough for ~200 employees over a year on a laptop
- For very large windows (>6 months × 200 employees), consider materialising a daily aggregate table and querying that instead. The architecture supports this — add a `daily_attendance_aggregate` table, run a nightly job that calls `process_employee_period` and stores the results, and have `data_loader` read from it for date ranges > 30 days.

---

## Troubleshooting

**"Supabase credentials missing"** — secrets file or env vars not set. See step 2 above.

**"No employees found"** — the `employees` table is empty or the column-name normalization in `_normalize_employee_cols` doesn't match your sync output. Check the actual column names in Supabase and adjust the `rename` dict.

**Upload says "0 inserted, N skipped"** — duplicate detection working. The same `(employee_id, log_time)` pair already exists. To force re-insert, delete the existing rows first or change the `on_conflict` strategy in `ingestion.insert_punches`.

**Tests fail after editing `processing.py`** — that's the test suite doing its job. Read the failure message, decide if your change is intentional, and update the test if so.

---

## What's not built yet (Phase 2/3 from BRD §14)

- Overtime calculation
- Email / push alerts for incomplete hours
- Bulk leave import via CSV
- ML-based predictive analytics
- Mobile-native UI
- Payroll integration

The architecture won't fight you on any of these — drop new modules in, reference the same `processing.py` engine, add a page if needed.
