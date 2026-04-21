"""
pages/1_Organization_Overview.py — PATCHED

PHASE 2 fix:
  ✓ Date range picker is now ON the page header (not buried in sidebar)
  ✓ Default range = first-of-current-month → today
  ✓ Quick presets: This month / Last 7 days / Last 30 days / Custom

Other behaviours preserved:
  • Only Normal + Custom employees included in metrics (Excluded ignored)
  • All Supabase queries use `attendance_date` column (NOT `date`)
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import date, timedelta
from data_loader import (
    load_employees, load_employees_in_org,
    load_attendance, load_leaves,
)

st.set_page_config(page_title="Organisation Overview — Aecor", page_icon="📊", layout="wide")
st.title("📊 Organisation Overview")

# ─────────────────────────────────────────────────────────────────
# PHASE 2: in-page date filter (top of page, not sidebar)
# ─────────────────────────────────────────────────────────────────
today = date.today()
month_start = today.replace(day=1)

with st.container():
    fcol1, fcol2 = st.columns([1, 3])

    with fcol1:
        preset = st.selectbox(
            "Quick range",
            ["This month", "Last 7 days", "Last 30 days", "Last 90 days", "Custom…"],
            index=0,
            key="org_preset",
        )

    if preset == "This month":
        default_range = (month_start, today)
    elif preset == "Last 7 days":
        default_range = (today - timedelta(days=6), today)
    elif preset == "Last 30 days":
        default_range = (today - timedelta(days=29), today)
    elif preset == "Last 90 days":
        default_range = (today - timedelta(days=89), today)
    else:
        default_range = (month_start, today)

    with fcol2:
        date_range = st.date_input(
            "Date range",
            value=default_range,
            max_value=today,
            key=f"org_range_{preset}",  # rebuilds when preset changes
            help="Pick any custom range. Both dates inclusive.",
        )

# Normalise output (single date vs tuple)
if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
    start_date, end_date = date_range
elif isinstance(date_range, date):
    start_date = end_date = date_range
else:
    start_date, end_date = default_range

st.caption(f"Showing **{start_date.strftime('%d %b %Y')}** → **{end_date.strftime('%d %b %Y')}** "
           f"({(end_date - start_date).days + 1} days)")

# ─────────────────────────────────────────────────────────────────
# Data
# ─────────────────────────────────────────────────────────────────
all_emps  = load_employees()
org_emps  = load_employees_in_org()         # Normal + Custom only
org_eids  = org_emps["employee_id"].tolist()

if not org_eids:
    st.warning("No employees assigned to Normal or Custom shift yet. "
               "Go to **User Management** to assign categories.")
    st.stop()

with st.spinner("Loading attendance…"):
    att = load_attendance(start_date, end_date, employee_ids=org_eids)
    leaves = load_leaves(start_date, end_date)

# ─────────────────────────────────────────────────────────────────
# KPI cards
# ─────────────────────────────────────────────────────────────────
working_days = max(1, len([d for d in pd.date_range(start_date, end_date)
                          if d.weekday() < 5]))

if att.empty:
    st.info(f"No attendance data found in this range. "
            f"Either employees were absent every day, or the sync hasn't pulled this period yet. "
            f"Run `python3 check_data.py` to verify what's in Supabase.")
    present_days = 0
    avg_dur_hrs  = 0.0
    late_count   = 0
else:
    present_days = int(att["present"].sum()) if "present" in att.columns else len(att)
    # Duration is stored as HH:MM string by eSSL — best-effort parse
    def _to_hours(v):
        if pd.isna(v) or v in ("", None):
            return 0.0
        s = str(v)
        try:
            if ":" in s:
                h, m, *_ = s.split(":")
                return int(h) + int(m) / 60
            return float(s)
        except Exception:
            return 0.0
    att["_hours"] = att["duration"].apply(_to_hours) if "duration" in att.columns else 0
    avg_dur_hrs = att.loc[att["_hours"] > 0, "_hours"].mean() if (att["_hours"] > 0).any() else 0
    late_count = int((att["late_by"].fillna("00:00") != "00:00").sum()) if "late_by" in att.columns else 0

leave_today = 0 if leaves.empty else int((leaves["leave_date"] == today).sum())

k1, k2, k3, k4 = st.columns(4)
k1.metric("Active employees (in org)", len(org_emps))
k2.metric("Avg productive hours", f"{avg_dur_hrs:.1f} h")
k3.metric("Late arrivals (period)", late_count, help="Informational — not penalised in scoring")
k4.metric("On leave today", leave_today)

st.divider()

# ─────────────────────────────────────────────────────────────────
# Daily trend chart
# ─────────────────────────────────────────────────────────────────
st.subheader("Daily attendance trend")

if att.empty:
    st.caption("No data to plot for this range.")
else:
    daily = att.groupby("attendance_date").agg(
        present=("present", "sum") if "present" in att.columns else ("employee_id", "count"),
    ).reset_index()
    daily["attendance_date"] = pd.to_datetime(daily["attendance_date"])

    fig = go.Figure()
    fig.add_bar(x=daily["attendance_date"], y=daily["present"],
                name="Present", marker_color="#3b82f6")
    fig.update_layout(
        height=320, margin=dict(l=10, r=10, t=10, b=10),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=False), yaxis=dict(gridcolor="#252f42"),
    )
    st.plotly_chart(fig, use_container_width=True)

# ─────────────────────────────────────────────────────────────────
# Shift distribution
# ─────────────────────────────────────────────────────────────────
st.subheader("Shift distribution")
dist = all_emps["category"].value_counts()
dcol1, dcol2, dcol3 = st.columns(3)
dcol1.metric("Normal Shift (10–7)",  int(dist.get("normal", 0)))
dcol2.metric("Custom Shift (12–6)",  int(dist.get("custom", 0)))
dcol3.metric("Excluded",             int(dist.get("excluded", 0)),
             help="Office staff, security, external entries — not in org metrics")
