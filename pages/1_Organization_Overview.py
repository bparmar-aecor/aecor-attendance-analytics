"""
pages/1_Organization_Overview.py
-----------------------------------------------------------
Organisation-wide attendance analytics — BRD v3.2 compliant.

Data source: device_logs (raw punches + regularizations), NOT attendance_logs.
Every metric is computed by processing.py — no reliance on eSSL's own numbers.

Per BRD v3.2 §12.1. Key rules:
  • No grace period — any clock-in after shift start = late (§7.1)
  • Incomplete = hours short, regardless of arrival time (§7.3)
  • Strict hours — no ±10 min variance (§8.4)
  • Extended-break note — break > cap but hours met = compliant (§8.3)
  • Hard break violations — only >30 min before 12:00 or after 15:00 (§8.3)
  • Weekends — Sat/Sun excluded from score; hours in total/avg displays (§6.2)
  • Today excluded from stats/score (§11.3)
  • Excluded employees omitted from all org metrics (§5)
"""
from __future__ import annotations
from datetime import date, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import SHIFTS, INCLUDED_CATEGORIES
from data_loader import (
    load_employees,
    load_employees_in_org,
    load_device_logs_with_regularizations,
    load_leaves,
    get_shift_label,
)
from processing import (
    process_employee_period,
    compute_productivity_score,
    org_productivity_score,
)


# ─────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Organization Overview — Aecor",
    page_icon="🏢",
    layout="wide",
)
st.title("🏢 Organization Overview")


# ─────────────────────────────────────────────────────────────────
# Date filters
# ─────────────────────────────────────────────────────────────────
today = date.today()
month_start = today.replace(day=1)

filter_col1, filter_col2 = st.columns([1, 2])

with filter_col1:
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

with filter_col2:
    date_range = st.date_input(
        "Date range",
        value=default_range,
        max_value=today,
        key=f"org_range_{preset}",
    )

if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
    start_date, end_date = date_range
elif isinstance(date_range, date):
    start_date = end_date = date_range
else:
    start_date, end_date = default_range

st.caption(
    f"Period: **{start_date.strftime('%d %b %Y')}** → "
    f"**{end_date.strftime('%d %b %Y')}** "
    f"({(end_date - start_date).days + 1} days)"
)
st.divider()


# ─────────────────────────────────────────────────────────────────
# Load data & run engine (cached)
# ─────────────────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def _compute_org_data(start: date, end: date, _today: date):
    """
    Load all data and run the processing engine for all in-org employees.
    Cached by (start, end, today) — TTL handles new sync data.
    """
    org_emps = load_employees_in_org()
    if org_emps.empty:
        return org_emps, pd.DataFrame()

    device_logs = load_device_logs_with_regularizations(start, end)
    leaves = load_leaves(start, end)

    # Normalize leaves — may return empty with no columns
    if leaves.empty or "employee_id" not in leaves.columns:
        leaves = pd.DataFrame(columns=["employee_id", "leave_date", "leave_type"])

    daily = process_employee_period(
        device_logs=device_logs,
        employees=org_emps,
        leaves=leaves,
        start=start,
        end=end,
        today=_today,
    )
    return org_emps, daily


with st.spinner("Loading data & computing metrics…"):
    org_emps, daily = _compute_org_data(start_date, end_date, today)

if org_emps.empty:
    st.warning(
        "No employees assigned to Normal or Custom shift yet. "
        "Visit **Settings** to assign categories."
    )
    st.stop()

if daily.empty:
    st.info("No data to display for this period.")
    st.stop()


# ─────────────────────────────────────────────────────────────────
# Prepare filtered DataFrames
# ─────────────────────────────────────────────────────────────────
# Drop in-progress days (today) from all stats — BRD §11.3
finished = daily[~daily["is_in_progress"]]

# Weekday working days only (for score-related KPIs)
weekday_finished = finished[finished["is_working_day"]]

# Present on weekdays (non-leave)
present_weekday = weekday_finished[
    weekday_finished["is_present"] & ~weekday_finished["is_leave"]
]

# All present days including weekends (for avg hours display — BRD §12.5)
all_present = finished[finished["is_present"] & ~finished["is_leave"]]


# ─────────────────────────────────────────────────────────────────
# KPI calculations
# ─────────────────────────────────────────────────────────────────

# --- Attendance rate ---
# Per employee: present weekdays / working weekdays (excl. leave)
def _avg_attendance_rate():
    if weekday_finished.empty:
        return 0.0
    rates = []
    for emp_id, grp in weekday_finished.groupby("employee_id"):
        working = grp[~grp["is_leave"]]
        if working.empty:
            continue
        present = working["is_present"].sum()
        rates.append(present / len(working) * 100)
    return float(np.mean(rates)) if rates else 0.0


avg_attendance = _avg_attendance_rate()

# --- Average productive hours (BRD §12.5) ---
# Total hours (weekday + weekend) / working days (weekdays only)
def _avg_productive_hours():
    if all_present.empty:
        return 0.0
    total_hours = float(all_present["productive_hours"].sum())
    # Denominator = unique (employee, weekday working day) pairs where present
    weekday_present_count = len(present_weekday)
    if weekday_present_count == 0:
        return 0.0
    return total_hours / weekday_present_count


avg_prod_hours = _avg_productive_hours()

# --- Break compliance % ---
# Days within policy / present weekdays
break_compliance_pct = (
    float(present_weekday["break_within_policy"].sum() / len(present_weekday) * 100)
    if len(present_weekday) > 0 else 0.0
)

# --- Organisation productivity score (BRD §11.2) ---
org_score = org_productivity_score(daily)
org_score_val = org_score.get("score", 0.0)

# --- Late arrivals count (informational) ---
total_late = int(present_weekday["is_late"].sum()) if len(present_weekday) > 0 else 0

# --- Long-break violations (hard violations only — BRD §8.3) ---
total_break_violations = (
    int((~present_weekday["break_within_policy"]).sum())
    if len(present_weekday) > 0 else 0
)

# --- Incomplete hours count (BRD §7.3 — hours short, any arrival time) ---
total_incomplete = (
    int(present_weekday["is_incomplete"].sum())
    if len(present_weekday) > 0 else 0
)


# ─────────────────────────────────────────────────────────────────
# KPI Cards — Row 1 (4 cards)
# ─────────────────────────────────────────────────────────────────
st.subheader("Key metrics")
r1c1, r1c2, r1c3, r1c4 = st.columns(4)

r1c1.metric(
    "Avg attendance rate",
    f"{avg_attendance:.1f}%",
    help="Average of per-employee (present weekdays / working weekdays) × 100",
)
r1c2.metric(
    "Avg productive hours",
    f"{avg_prod_hours:.1f}h",
    help="Total hours (incl. weekend work) ÷ present weekdays — per BRD §12.5",
)
r1c3.metric(
    "Break compliance",
    f"{break_compliance_pct:.1f}%",
    help="% of present weekdays within break policy (§8.3)",
)

# Colour the org score
if org_score_val >= 85:
    _score_colour = "#16a34a"
elif org_score_val >= 70:
    _score_colour = "#2563eb"
elif org_score_val >= 50:
    _score_colour = "#f59e0b"
else:
    _score_colour = "#dc2626"

r1c4.metric(
    "Org productivity score",
    f"{org_score_val:.1f}",
    help=(
        f"Average of {org_score.get('n_employees', 0)} employee scores. "
        f"Median: {org_score.get('median', 0):.1f} · "
        f"Std dev: {org_score.get('stdev', 0):.1f}"
    ),
)


# ─────────────────────────────────────────────────────────────────
# KPI Cards — Row 2 (3 cards)
# ─────────────────────────────────────────────────────────────────
r2c1, r2c2, r2c3 = st.columns(3)

r2c1.metric(
    "Late arrivals",
    total_late,
    help="Total late-arrival instances across all employees (informational — not in score)",
)
r2c2.metric(
    "Break violations",
    total_break_violations,
    help="Days with long breaks outside lunch window (>30 min before 12:00 or after 15:00)",
)
r2c3.metric(
    "Incomplete hours",
    total_incomplete,
    help="Days where productive hours fell short of requirement (strict — no tolerance)",
)

st.divider()


# ─────────────────────────────────────────────────────────────────
# Daily trend chart
# ─────────────────────────────────────────────────────────────────
st.subheader("Daily attendance trend")

# Build per-day aggregation (weekdays only for present/absent bars)
trend_dates = pd.date_range(start_date, end_date, freq="D")
n_org_employees = len(org_emps)

trend_rows = []
for d in trend_dates:
    d_date = d.date()
    is_weekend = d_date.weekday() >= 5

    day_data = finished[finished["work_date"] == d_date]
    if day_data.empty and not is_weekend:
        trend_rows.append({
            "date": d_date,
            "is_weekend": is_weekend,
            "present": 0,
            "absent": n_org_employees,
            "weekend_workers": 0,
            "avg_productive": 0.0,
        })
        continue

    day_present = day_data[day_data["is_present"] & ~day_data["is_leave"]]

    if is_weekend:
        trend_rows.append({
            "date": d_date,
            "is_weekend": True,
            "present": 0,
            "absent": 0,
            "weekend_workers": len(day_present),
            "avg_productive": (
                float(day_present["productive_hours"].mean())
                if len(day_present) > 0 else 0.0
            ),
        })
    else:
        day_leave = day_data[day_data["is_leave"]]
        present_count = len(day_present)
        leave_count = len(day_leave)
        absent_count = max(0, n_org_employees - present_count - leave_count)
        trend_rows.append({
            "date": d_date,
            "is_weekend": False,
            "present": present_count,
            "absent": absent_count,
            "weekend_workers": 0,
            "avg_productive": (
                float(day_present["productive_hours"].mean())
                if present_count > 0 else 0.0
            ),
        })

trend_df = pd.DataFrame(trend_rows)

if trend_df.empty:
    st.caption("No trend data for this period.")
else:
    fig = go.Figure()

    # Separate weekday and weekend data
    weekday_trend = trend_df[~trend_df["is_weekend"]]
    weekend_trend = trend_df[trend_df["is_weekend"]]

    # Stacked bar: present (green) + absent (red) — weekdays only
    if not weekday_trend.empty:
        fig.add_bar(
            x=pd.to_datetime(weekday_trend["date"]),
            y=weekday_trend["present"],
            name="Present",
            marker_color="#16a34a",
            hovertemplate="%{x|%d %b %a}<br>Present: %{y}<extra></extra>",
        )
        fig.add_bar(
            x=pd.to_datetime(weekday_trend["date"]),
            y=weekday_trend["absent"],
            name="Absent",
            marker_color="#dc2626",
            opacity=0.6,
            hovertemplate="%{x|%d %b %a}<br>Absent: %{y}<extra></extra>",
        )

    # Weekend workers as separate subtle markers
    weekend_with_work = weekend_trend[weekend_trend["weekend_workers"] > 0]
    if not weekend_with_work.empty:
        fig.add_bar(
            x=pd.to_datetime(weekend_with_work["date"]),
            y=weekend_with_work["weekend_workers"],
            name="Weekend work",
            marker_color="#8b5cf6",
            opacity=0.5,
            hovertemplate="%{x|%d %b %a}<br>Weekend workers: %{y}<extra></extra>",
        )

    # Line: avg productive hours (weekdays only, secondary y-axis)
    if not weekday_trend.empty:
        weekday_with_data = weekday_trend[weekday_trend["avg_productive"] > 0]
        if not weekday_with_data.empty:
            fig.add_scatter(
                x=pd.to_datetime(weekday_with_data["date"]),
                y=weekday_with_data["avg_productive"],
                name="Avg productive hrs",
                mode="lines+markers",
                line=dict(color="#3b82f6", width=2),
                marker=dict(size=5),
                yaxis="y2",
                hovertemplate="%{x|%d %b %a}<br>Avg: %{y:.1f}h<extra></extra>",
            )

    fig.update_layout(
        barmode="stack",
        height=380,
        margin=dict(l=10, r=10, t=30, b=10),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        xaxis=dict(showgrid=False),
        yaxis=dict(title="Employees", gridcolor="#252f42", side="left"),
        yaxis2=dict(
            title="Avg hours",
            overlaying="y",
            side="right",
            showgrid=False,
            rangemode="tozero",
        ),
    )
    st.plotly_chart(fig, use_container_width=True)

st.divider()


# ─────────────────────────────────────────────────────────────────
# Two-column section: Shift distribution + Break analysis
# ─────────────────────────────────────────────────────────────────
chart_left, chart_right = st.columns(2)

# --- Shift distribution ---
with chart_left:
    st.subheader("Shift distribution")
    all_emps = load_employees()
    if not all_emps.empty:
        cat_counts = all_emps["category"].value_counts()
        labels = [get_shift_label(c) for c in cat_counts.index]
        colours = []
        for c in cat_counts.index:
            if c == "normal":
                colours.append("#3b82f6")
            elif c == "custom":
                colours.append("#8b5cf6")
            else:
                colours.append("#6b7280")

        fig_shift = go.Figure(go.Bar(
            y=labels,
            x=cat_counts.values,
            orientation="h",
            marker_color=colours,
            text=cat_counts.values,
            textposition="auto",
        ))
        fig_shift.update_layout(
            height=250,
            margin=dict(l=10, r=10, t=10, b=10),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(showgrid=False, showticklabels=False),
            yaxis=dict(showgrid=False),
        )
        st.plotly_chart(fig_shift, use_container_width=True)
    else:
        st.caption("No employee data available.")


# --- Break analysis ---
with chart_right:
    st.subheader("Break analysis")
    if len(present_weekday) > 0:
        within_policy = int(present_weekday["break_within_policy"].sum())
        violations = int((~present_weekday["break_within_policy"]).sum())

        # Further break down violations
        long_pre = int(present_weekday.apply(
            lambda r: r.get("long_breaks_outside_lunch", 0) > 0
            and any(kw in str(r.get("break_violation_reason", "")) for kw in ["pre-lunch", "pre_lunch"]),
            axis=1
        ).sum()) if "break_violation_reason" in present_weekday.columns else 0

        fig_break = go.Figure(go.Bar(
            y=["Within policy", "Violations"],
            x=[within_policy, violations],
            orientation="h",
            marker_color=["#16a34a", "#dc2626"],
            text=[within_policy, violations],
            textposition="auto",
        ))
        fig_break.update_layout(
            height=250,
            margin=dict(l=10, r=10, t=10, b=10),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(showgrid=False, showticklabels=False),
            yaxis=dict(showgrid=False),
        )
        st.plotly_chart(fig_break, use_container_width=True)

        # Show break violation rate as caption
        total_days = within_policy + violations
        if total_days > 0:
            st.caption(
                f"{within_policy} of {total_days} employee-days within policy "
                f"({within_policy/total_days*100:.1f}%). "
                f"{violations} violation(s)."
            )
    else:
        st.caption("No break data for this period.")

st.divider()


# ─────────────────────────────────────────────────────────────────
# Frequent late arrivals — top 10
# ─────────────────────────────────────────────────────────────────
st.subheader("Frequent late arrivals")
st.caption("Informational only — late arrivals do not affect the productivity score.")

if len(present_weekday) > 0:
    late_data = present_weekday[present_weekday["is_late"]]
    if late_data.empty:
        st.success("No late arrivals in the selected period.")
    else:
        late_summary = late_data.groupby("employee_id").agg(
            late_count=("is_late", "sum"),
            avg_minutes_late=("minutes_late", "mean"),
            employee_name=("employee_name", "first"),
            category=("category", "first"),
        ).sort_values("late_count", ascending=False).reset_index()

        late_summary["avg_minutes_late"] = late_summary["avg_minutes_late"].round(0).astype(int)
        late_summary["shift"] = late_summary["category"].apply(get_shift_label)

        # Show top 10 by default
        top_n = min(10, len(late_summary))
        display_late = late_summary.head(top_n).copy()

        display_late_table = display_late[["employee_name", "shift", "late_count", "avg_minutes_late"]].copy()
        display_late_table.columns = ["Employee", "Shift", "Late days", "Avg minutes late"]

        st.dataframe(display_late_table, use_container_width=True, hide_index=True)

        if len(late_summary) > 10:
            with st.expander(f"Show all {len(late_summary)} employees"):
                all_late_table = late_summary[["employee_name", "shift", "late_count", "avg_minutes_late"]].copy()
                all_late_table.columns = ["Employee", "Shift", "Late days", "Avg minutes late"]
                st.dataframe(all_late_table, use_container_width=True, hide_index=True)
else:
    st.caption("No data available.")

st.divider()


# ─────────────────────────────────────────────────────────────────
# Incomplete hours list
# ─────────────────────────────────────────────────────────────────
st.subheader("Incomplete hours")
st.caption("Employees whose productive hours fell short of the requirement (strict — no tolerance).")

if len(present_weekday) > 0:
    incomplete_data = present_weekday[present_weekday["is_incomplete"]]
    if incomplete_data.empty:
        st.success("All employees completed their required hours in the selected period.")
    else:
        incomplete_summary = incomplete_data.groupby("employee_id").agg(
            incomplete_count=("is_incomplete", "sum"),
            avg_productive=("productive_hours", "mean"),
            employee_name=("employee_name", "first"),
            category=("category", "first"),
        ).sort_values("incomplete_count", ascending=False).reset_index()

        incomplete_summary["avg_productive"] = incomplete_summary["avg_productive"].round(2)
        incomplete_summary["shift"] = incomplete_summary["category"].apply(get_shift_label)

        # Add required hours for context
        incomplete_summary["required"] = incomplete_summary["category"].map(
            lambda c: SHIFTS[c].required_productive_hours if c in SHIFTS else "—"
        )

        top_n = min(10, len(incomplete_summary))
        display_incomplete = incomplete_summary.head(top_n).copy()

        display_table = display_incomplete[[
            "employee_name", "shift", "incomplete_count", "avg_productive", "required"
        ]].copy()
        display_table.columns = [
            "Employee", "Shift", "Incomplete days", "Avg productive hrs", "Required hrs"
        ]

        st.dataframe(display_table, use_container_width=True, hide_index=True)

        if len(incomplete_summary) > 10:
            with st.expander(f"Show all {len(incomplete_summary)} employees"):
                all_table = incomplete_summary[[
                    "employee_name", "shift", "incomplete_count", "avg_productive", "required"
                ]].copy()
                all_table.columns = [
                    "Employee", "Shift", "Incomplete days", "Avg productive hrs", "Required hrs"
                ]
                st.dataframe(all_table, use_container_width=True, hide_index=True)
else:
    st.caption("No data available.")
