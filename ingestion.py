"""
ingestion.py
------------------------------------------------------------
Manual fallback ingestion: CSV and PDF uploads.

eSSL exports come in many shapes. We auto-detect the column
schema, normalise to (employee_id, log_time), de-duplicate
against existing rows, and bulk-insert into device_logs.

Used when the auto-sync is down or for backfills.
"""
from __future__ import annotations
from datetime import datetime
import io
import re

import pandas as pd

from db import get_client


# -----------------------------------------------------------
# Column-name aliases observed in eSSL exports
# -----------------------------------------------------------
EMP_ID_ALIASES = ("employee_id", "employeeid", "user_id", "userid",
                  "emp id", "emp_id", "employee code", "employeecode")
TIME_ALIASES   = ("log_time", "logtime", "log_date", "logdate",
                  "punch time", "punchtime", "datetime", "timestamp",
                  "in time", "intime", "out time", "outtime")
DATE_ALIASES   = ("date", "log date", "logdate", "punchdate", "attendance date")


def _find_col(df_cols: list[str], aliases: tuple[str, ...]) -> str | None:
    cols_lower = {c.lower().strip(): c for c in df_cols}
    for a in aliases:
        if a in cols_lower:
            return cols_lower[a]
    return None


def normalize_csv(file_bytes: bytes) -> pd.DataFrame:
    """
    Accept a CSV in any common eSSL format. Return a DataFrame
    with exactly two columns: employee_id (int), log_time (datetime).
    """
    # Tolerate excel-style separators
    for sep in (",", ";", "\t", "|"):
        try:
            df = pd.read_csv(io.BytesIO(file_bytes), sep=sep, engine="python")
            if df.shape[1] >= 2:
                break
        except Exception:
            continue
    else:
        raise ValueError("Could not parse CSV — unknown delimiter")

    emp_col  = _find_col(df.columns.tolist(), EMP_ID_ALIASES)
    time_col = _find_col(df.columns.tolist(), TIME_ALIASES)
    date_col = _find_col(df.columns.tolist(), DATE_ALIASES)

    if not emp_col:
        raise ValueError(f"Could not find employee-id column. Columns: {list(df.columns)}")

    # Time column: combine date + time if separate
    if time_col:
        df["_log_time"] = pd.to_datetime(df[time_col], errors="coerce", dayfirst=True)
    elif date_col:
        df["_log_time"] = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)
    else:
        raise ValueError(f"Could not find time/date column. Columns: {list(df.columns)}")

    out = pd.DataFrame({
        "employee_id": pd.to_numeric(df[emp_col], errors="coerce"),
        "log_time":    df["_log_time"],
    })
    out = out.dropna()
    out["employee_id"] = out["employee_id"].astype(int)
    out = out.drop_duplicates(["employee_id", "log_time"])
    return out


def normalize_pdf(file_bytes: bytes) -> pd.DataFrame:
    """
    Best-effort PDF table extraction. Tries pdfplumber first,
    falls back to a regex line-scanner for typical eSSL printouts:
      "<emp_id>  <name>  <date> <time>"
    """
    try:
        import pdfplumber
    except ImportError as e:
        raise RuntimeError(
            "pdfplumber not installed. Run: pip install pdfplumber"
        ) from e

    rows = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        # Try structured tables
        for page in pdf.pages:
            tables = page.extract_tables() or []
            for tbl in tables:
                if not tbl or len(tbl) < 2:
                    continue
                header = [str(c or "").strip().lower() for c in tbl[0]]
                emp_idx  = next((i for i, h in enumerate(header)
                                 if any(a in h for a in EMP_ID_ALIASES)), None)
                time_idx = next((i for i, h in enumerate(header)
                                 if any(a in h for a in TIME_ALIASES)), None)
                if emp_idx is None or time_idx is None:
                    continue
                for r in tbl[1:]:
                    try:
                        rows.append({
                            "employee_id": int(re.sub(r"\D", "", str(r[emp_idx]) or "0") or 0),
                            "log_time": pd.to_datetime(r[time_idx], errors="coerce", dayfirst=True),
                        })
                    except Exception:
                        continue

        # Fallback: regex-scan plain text
        if not rows:
            line_re = re.compile(
                r"(\d{2,6})\s+.*?(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\s+\d{1,2}:\d{2}(?::\d{2})?)"
            )
            for page in pdf.pages:
                text = page.extract_text() or ""
                for line in text.splitlines():
                    m = line_re.search(line)
                    if m:
                        rows.append({
                            "employee_id": int(m.group(1)),
                            "log_time":    pd.to_datetime(m.group(2), errors="coerce",
                                                          dayfirst=True),
                        })

    if not rows:
        raise ValueError("No punch data could be extracted from PDF")

    df = pd.DataFrame(rows).dropna()
    df["employee_id"] = df["employee_id"].astype(int)
    return df.drop_duplicates(["employee_id", "log_time"])


def insert_punches(df: pd.DataFrame) -> dict:
    """
    Upsert into device_logs. Returns counts.
    Idempotency relies on a unique (employee_id, log_time) constraint
    on device_logs in Supabase — add it if missing.
    """
    if df.empty:
        return {"inserted": 0, "skipped": 0}

    sb = get_client()
    payload = [
        {"employee_id": int(r.employee_id),
         "log_time":   r.log_time.isoformat(),
         "log_date":   r.log_time.date().isoformat()}
        for r in df.itertuples(index=False)
    ]
    # Batch in chunks of 500 to stay within request limits
    inserted = 0
    for i in range(0, len(payload), 500):
        chunk = payload[i:i+500]
        try:
            res = sb.table("device_logs").upsert(
                chunk, on_conflict="employee_id,log_time").execute()
            inserted += len(res.data or [])
        except Exception:
            # Try plain insert if no unique constraint exists
            res = sb.table("device_logs").insert(chunk).execute()
            inserted += len(res.data or [])

    return {"inserted": inserted, "skipped": len(payload) - inserted}
