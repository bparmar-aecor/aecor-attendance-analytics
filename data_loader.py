"""
data_loader.py
------------------------------------------------------------
Supabase data access layer.

Responsibilities
  • Fetch raw eSSL data from Supabase (cached)
  • Normalise column names (eSSL PascalCase → snake_case + short aliases
    so pages can use `name`, `code`, `department` instead of the verbose
    `employee_name`, `employee_code`, `department_name`)
  • Provide write-back helpers for categories, leaves, regularisations
  • Apply pending regularisations on top of raw device_logs at read time

This module is the ONLY place that talks to Supabase directly. Pages
should never `from supabase import` anything.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from db import get_client


# ─────────────────────────────────────────────────────────────────
# Display-name helpers
# ─────────────────────────────────────────────────────────────────
CATEGORY_LABELS = {
    "normal":   "Normal Shift (10–7)",
    "custom":   "Custom Shift (12–6)",
    "excluded": "Excluded",
}


def get_shift_label(category: str | None) -> str:
    """Map raw category code to friendly shift label."""
    if not category:
        return "— Unassigned —"
    return CATEGORY_LABELS.get(category.lower(), category.title())


# ─────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────
def _paginate(query, page_size: int = 1000) -> list[dict]:
    """Walk a Supabase query in pages so we never miss data."""
    rows: list[dict] = []
    offset = 0
    while True:
        r = query.range(offset, offset + page_size - 1).execute()
        if not r.data:
            break
        rows.extend(r.data)
        if len(r.data) < page_size:
            break
        offset += page_size
    return rows


def _normalize_employee_cols(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add short aliases so pages can use `name`, `code`, `department`.
    The original eSSL-style columns (`employee_name`, `employee_code`,
    `department_name`) are kept intact for any code that still uses them.
    """
    if df.empty:
        return df
    aliases = {
        "employee_name":   "name",
        "employee_code":   "code",
        "department_name": "department",
    }
    for src, dst in aliases.items():
        if src in df.columns and dst not in df.columns:
            df[dst] = df[src]
    # Make sure the alias columns always exist, even if the source is missing
    for dst in ("name", "code", "department"):
        if dst not in df.columns:
            df[dst] = ""
    return df


# ─────────────────────────────────────────────────────────────────
# Employees & categories
# ─────────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_employees() -> pd.DataFrame:
    """All active employees joined with their latest category assignment."""
    sb = get_client()
    emp_resp = sb.table("employees").select(
        "employee_id, employee_name, employee_code, department_id, designation, status"
    ).eq("status", "Working").execute()
    emp_df = pd.DataFrame(emp_resp.data)
    if emp_df.empty:
        return emp_df

    # Latest category per employee
    cat_resp = sb.table("employee_categories").select(
        "employee_id, category, effective_from"
    ).order("effective_from", desc=True).execute()
    cat_df = pd.DataFrame(cat_resp.data)
    if not cat_df.empty:
        cat_df = cat_df.drop_duplicates(subset=["employee_id"], keep="first")
        emp_df = emp_df.merge(cat_df[["employee_id", "category"]],
                              on="employee_id", how="left")
    else:
        emp_df["category"] = None

    emp_df["category"] = emp_df["category"].fillna("excluded").str.lower()
    emp_df["shift_label"] = emp_df["category"].apply(get_shift_label)

    # Department names
    try:
        dep_resp = sb.table("departments").select("department_id, department_name").execute()
        dep_df = pd.DataFrame(dep_resp.data)
        if not dep_df.empty:
            emp_df = emp_df.merge(dep_df, on="department_id", how="left")
    except Exception:
        emp_df["department_name"] = ""

    emp_df = _normalize_employee_cols(emp_df)
    return emp_df.sort_values("employee_name").reset_index(drop=True)


def load_employees_in_org() -> pd.DataFrame:
    """Employees included in organisation analytics — Normal + Custom only."""
    df = load_employees()
    if df.empty:
        return df
    return df[df["category"].isin(["normal", "custom"])].reset_index(drop=True)


def upsert_category(employee_id: int, category: str, effective_from: date):
    """Insert or update an employee's category (Notes field intentionally dropped)."""
    sb = get_client()
    sb.table("employee_categories").upsert({
        "employee_id":    int(employee_id),
        "category":       category.lower(),
        "effective_from": effective_from.isoformat(),
    }, on_conflict="employee_id,effective_from").execute()
    load_employees.clear()


# ─────────────────────────────────────────────────────────────────
# Attendance & punches
# ─────────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def load_attendance(start: date, end: date,
                    employee_ids: list[int] | None = None) -> pd.DataFrame:
    """
    Daily attendance rows from attendance_logs between [start, end] inclusive.

    NOTE: Supabase column is `attendance_date` (NOT `date`).
    """
    sb = get_client()
    q = sb.table("attendance_logs").select(
        "attendance_log_id, attendance_date, employee_id, in_time, out_time, "
        "duration, late_by, early_by, is_on_leave, leave_type, weekly_off, "
        "holiday, punch_records, shift_id, present, absent, status, status_code, "
        "overtime, missed_out_punch, missed_in_punch, remarks, loss_of_hours"
    ).gte("attendance_date", start.isoformat()) \
     .lte("attendance_date", end.isoformat())

    if employee_ids:
        q = q.in_("employee_id", [int(x) for x in employee_ids])

    rows = _paginate(q)
    df = pd.DataFrame(rows)
    if not df.empty:
        df["attendance_date"] = pd.to_datetime(df["attendance_date"]).dt.date
    return df


@st.cache_data(ttl=60)
def load_device_logs(start: date, end: date,
                     employee_ids: list[int] | None = None) -> pd.DataFrame:
    """
    Raw biometric punches between [start, end] inclusive.

    Returns a DataFrame with BOTH `log_date` (timestamp from eSSL) AND
    `log_time` (alias) so callers can use either name. Also resolves
    `user_id` (string) → `employee_id` (int) via the employees table.

    Pending regularisations are NOT applied here — call
    `load_device_logs_with_regularizations()` for that.
    """
    sb = get_client()
    q = sb.table("device_logs").select(
        "device_log_id, log_date, user_id, device_id, direction"
    ).gte("log_date", f"{start.isoformat()}T00:00:00") \
     .lte("log_date", f"{end.isoformat()}T23:59:59")

    rows = _paginate(q)
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # Convert to datetime and strip timezone (Supabase returns UTC+00:00, normalize to naive)
    df["log_date"] = pd.to_datetime(df["log_date"]).dt.tz_localize(None)
    df["log_time"] = df["log_date"]   # alias used by processing.py

    # Map user_id (string) → employee_id (int) via employees.employee_code
    emp = load_employees()[["employee_id", "employee_code"]]
    df["user_id"] = df["user_id"].astype(str).str.strip()
    emp = emp.copy()
    emp["employee_code"] = emp["employee_code"].astype(str).str.strip()
    df = df.merge(emp, left_on="user_id", right_on="employee_code", how="left")

    if employee_ids:
        df = df[df["employee_id"].isin([int(x) for x in employee_ids])]

    return df


def load_device_logs_with_regularizations(
    start: date, end: date,
    employee_ids: list[int] | None = None,
) -> pd.DataFrame:
    """
    Same as load_device_logs but applies pending punch_regularizations on
    top of the raw eSSL data. Per BRD §9.2 — original data is preserved,
    corrections are replayed at read time.
    """
    raw = load_device_logs(start, end, employee_ids).copy()
    regs = load_regularizations(start, end)

    if regs.empty:
        return raw

    # Apply in chronological order so later edits override earlier ones
    regs = regs.sort_values("created_at")
    out = raw.copy()
    for r in regs.itertuples(index=False):
        eid    = int(r.employee_id)
        action = r.action
        orig   = pd.to_datetime(r.original_time).tz_localize(None) if pd.notna(r.original_time) else None
        corr   = pd.to_datetime(r.corrected_time).tz_localize(None) if pd.notna(r.corrected_time) else None

        if action == "add" and corr is not None:
            new_row = {
                "device_log_id": -1, "log_date": corr, "log_time": corr,
                "user_id": "", "device_id": None, "direction": None,
                "employee_id": eid, "employee_code": "",
            }
            out = pd.concat([out, pd.DataFrame([new_row])], ignore_index=True)
        elif action == "delete" and orig is not None:
            mask = (out["employee_id"] == eid) & (out["log_date"] == orig)
            out = out[~mask].reset_index(drop=True)
        elif action == "edit" and orig is not None and corr is not None:
            mask = (out["employee_id"] == eid) & (out["log_date"] == orig)
            out.loc[mask, "log_date"] = corr
            out.loc[mask, "log_time"] = corr

    return out.sort_values(["employee_id", "log_date"]).reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────
# Leaves
# ─────────────────────────────────────────────────────────────────
@st.cache_data(ttl=30)
def load_leaves(start: date, end: date) -> pd.DataFrame:
    sb = get_client()
    r = sb.table("employee_leaves").select("*") \
        .gte("leave_date", start.isoformat()) \
        .lte("leave_date", end.isoformat()).execute()
    df = pd.DataFrame(r.data)
    if not df.empty:
        df["leave_date"] = pd.to_datetime(df["leave_date"]).dt.date
    return df


def add_leave(employee_id: int, leave_date: date, leave_type: str,
              reason: str = "", marked_by: str = "admin"):
    """Insert (or upsert if (emp,date) already exists) a leave record."""
    sb = get_client()
    sb.table("employee_leaves").upsert({
        "employee_id": int(employee_id),
        "leave_date":  leave_date.isoformat(),
        "leave_type":  leave_type,
        "reason":      reason,
        "marked_by":   marked_by,
    }, on_conflict="employee_id,leave_date").execute()
    load_leaves.clear()


# Backwards-compat alias — older code called it `insert_leave`
insert_leave = add_leave


def delete_leave(employee_id: int, leave_date: date):
    """Remove a leave record."""
    sb = get_client()
    sb.table("employee_leaves").delete() \
        .eq("employee_id", int(employee_id)) \
        .eq("leave_date", leave_date.isoformat()) \
        .execute()
    load_leaves.clear()


# ─────────────────────────────────────────────────────────────────
# Punch regularisations
# ─────────────────────────────────────────────────────────────────
@st.cache_data(ttl=30)
def load_regularizations(start: date, end: date) -> pd.DataFrame:
    sb = get_client()
    r = sb.table("punch_regularizations").select("*") \
        .gte("punch_date", start.isoformat()) \
        .lte("punch_date", end.isoformat()).execute()
    df = pd.DataFrame(r.data)
    if not df.empty:
        df["punch_date"] = pd.to_datetime(df["punch_date"]).dt.date
        if "created_at" in df.columns:
            # Strip timezone from created_at
            df["created_at"] = pd.to_datetime(df["created_at"]).dt.tz_localize(None)
        # Strip timezone from timestamp columns if present
        if "original_time" in df.columns:
            df["original_time"] = pd.to_datetime(df["original_time"], errors='coerce').dt.tz_localize(None)
        if "corrected_time" in df.columns:
            df["corrected_time"] = pd.to_datetime(df["corrected_time"], errors='coerce').dt.tz_localize(None)
    return df


def add_regularization(employee_id: int, punch_date: date, action: str,
                       original_time: datetime | None,
                       corrected_time: datetime | None,
                       reason: str, approved_by: str = "admin"):
    """Record a punch correction. Original device_logs row is preserved."""
    if action not in ("add", "edit", "delete"):
        raise ValueError(f"Invalid action: {action}")
    if not reason or not reason.strip():
        raise ValueError("Reason is mandatory for regularisation")

    sb = get_client()
    sb.table("punch_regularizations").insert({
        "employee_id":    int(employee_id),
        "punch_date":     punch_date.isoformat(),
        "action":         action,
        "original_time":  original_time.isoformat() if original_time else None,
        "corrected_time": corrected_time.isoformat() if corrected_time else None,
        "reason":         reason.strip(),
        "approved_by":    approved_by,
    }).execute()
    load_regularizations.clear()
    load_device_logs.clear()
