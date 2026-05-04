"""
pages/1_Organization_Overview.py
-----------------------------------------------------------
Organisation-wide attendance analytics — BRD v3.3 compliant.

Data source: device_logs (raw punches + regularizations), NOT attendance_logs.
Every metric is computed by processing.py — no reliance on eSSL's own numbers.

Per BRD v3.3:
  • Late threshold per shift — Normal: 11:00, Custom: 12:30 (§7.1)
  • Score weights: Attendance 10%, Hours 50%, Break 30%, Consistency 10% (§11.1)
  • Incomplete = hours short, regardless of arrival time (§7.3)
  • Strict hours — no ±10 min variance (§8.4)
  • Extended-break note — break > cap but hours met = compliant (§8.3)
  • Hard break violations — only >30 min before 12:00 or after 15:00 (§8.3)
  • Weekends — Sat/Sun excluded from score; hours in total/avg displays (§6.2)
  • Today excluded from stats/score (§11.3)
  • Excluded employees omitted from all org metrics (§5)
  • KPI row 2 shows X / Y (Z%) format (§12.1)
  • All-Employee Summary Table on this page (§12.3, moved from Individual)
  • Employee name click-through to Individual page (§12.3)
  • Date picker: disabled unless Custom Range selected (§12.4)
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
# Date filters (BRD v3.3 §12.4 — disabled unless Custom Range)
# ─────────────────────────────────────────────────────────────────
today = date.today()
month_start = today.replace(day=1)

# Check if navigated from another page with a specific date range
nav_start = st.session_state.pop("nav_date_start", None)
nav_end = st.session_state.pop("nav_date_end", None)

if nav_start and nav_end:
    default_preset_idx = 3  # Custom Range
else:
    default_preset_idx = 2  # This month

filter_col1, filter_col2 = st.columns([1, 2])

with filter_col1:
    preset = st.selectbox(
        "Quick range",
        ["Today", "This week", "This month", "Custom Range"],
        index=default_preset_idx,
        key="org_preset",
    )

if preset == "Today":
    start_date, end_date = today, today
elif preset == "This week":
    start_date = today - timedelta(days=today.weekday())
    end_date = today
elif preset == "This month":
    start_date, end_date = month_start, today
else:
    # Custom Range — use nav dates if available, else default
    if nav_start and nav_end:
        start_date, end_date = nav_start, nav_end
    else:
        start_date, end_date = month_start, today

is_custom = preset == "Custom Range"

with filter_col2:
    if is_custom:
        date_range = st.date_input(
            "Date range",
            value=(start_date, end_date),
            max_value=today,
            key="org_range_custom",
        )
        if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
            start_date, end_date = date_range
        elif isinstance(date_range, date):
            start_date = end_date = date_range
    else:
        st.date_input(
            "Date range",
            value=(start_date, end_date),
            disabled=True,
            key="org_range_preset",
        )

if start_date > end_date:
    st.error("Start date must be ≤ end date.")
    st.stop()

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

# Total present weekdays — denominator for row 2 KPIs
total_present_weekdays = len(present_weekday)


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
    weekday_present_count = len(present_weekday)
    if weekday_present_count == 0:
        return 0.0
    return total_hours / weekday_present_count


avg_prod_hours = _avg_productive_hours()

# --- Break compliance % ---
break_compliance_pct = (
    float(present_weekday["break_within_policy"].sum() / len(present_weekday) * 100)
    if len(present_weekday) > 0 else 0.0
)

# --- Organisation productivity score (BRD §11.2) ---
org_score = org_productivity_score(daily)
org_score_val = org_score.get("score", 0.0)

# --- Late arrivals count (informational) ---
total_late = int(present_weekday["is_late"].sum()) if total_present_weekdays > 0 else 0

# --- Long-break violations (hard violations only — BRD §8.3) ---
total_break_violations = (
    int((~present_weekday["break_within_policy"]).sum())
    if total_present_weekdays > 0 else 0
)

# --- Incomplete hours count (BRD §7.3 — hours short, any arrival time) ---
total_incomplete = (
    int(present_weekday["is_incomplete"].sum())
    if total_present_weekdays > 0 else 0
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
# KPI Cards — Row 2 (3 cards) — X / Y (Z%) format per v3.3
# ─────────────────────────────────────────────────────────────────
r2c1, r2c2, r2c3 = st.columns(3)

late_pct = (total_late / total_present_weekdays * 100) if total_present_weekdays > 0 else 0
r2c1.metric(
    "Late arrivals",
    f"{total_late} / {total_present_weekdays} ({late_pct:.1f}%)",
    help="Late-arrival instances / total present weekdays. Informational — not in score.",
)

violations_pct = (total_break_violations / total_present_weekdays * 100) if total_present_weekdays > 0 else 0
r2c2.metric(
    "Break violations",
    f"{total_break_violations} / {total_present_weekdays} ({violations_pct:.1f}%)",
    help="Hard break violation days / total present weekdays.",
)

incomplete_pct = (total_incomplete / total_present_weekdays * 100) if total_present_weekdays > 0 else 0
r2c3.metric(
    "Incomplete hours",
    f"{total_incomplete} / {total_present_weekdays} ({incomplete_pct:.1f}%)",
    help="Days with hours short of requirement / total present weekdays.",
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
# ALL-EMPLOYEE SUMMARY TABLE (BRD v3.3 §12.3 — moved from Individual page)
# ─────────────────────────────────────────────────────────────────
st.subheader("📊 All Employees Summary")
st.caption("Compare all employees for the selected period. Click an employee name to see their detailed page.")

if not daily.empty:
    summary_rows = []
    emp_id_map = {}  # For click-through navigation

    for emp_id, emp_df in daily.groupby("employee_id"):
        emp_row = org_emps[org_emps["employee_id"] == emp_id]
        if emp_row.empty:
            continue

        emp_info = emp_row.iloc[0]

        # Filter out in-progress days for stats (BRD §11.3)
        emp_finished = emp_df[~emp_df["is_in_progress"]]
        emp_present = emp_finished[emp_finished["is_present"] & ~emp_finished["is_leave"]]

        if emp_present.empty:
            continue

        # Weekday stats for KPI columns (BRD §6.2)
        emp_present_weekday = emp_present[emp_present["is_working_day"]]
        present_weekday_count = len(emp_present_weekday)

        if present_weekday_count == 0:
            continue

        late_count = int(emp_present_weekday["is_late"].sum())
        violations_count = int((~emp_present_weekday["break_within_policy"]).sum())
        incomplete_count = int(emp_present_weekday["is_incomplete"].sum())

        # Avg hours: BRD §12.5 — total hours (weekday + weekend) ÷ present weekday count
        total_hours = float(emp_present["productive_hours"].sum())
        avg_hours = total_hours / present_weekday_count

        # Avg break: sum of weekday break hours ÷ present weekday count
        avg_break = float(emp_present_weekday["total_break_hours"].sum() * 60) / present_weekday_count

        # Score
        shift_code = str(emp_info["category"]).lower()
        sr = SHIFTS.get(shift_code)
        score_data = compute_productivity_score(emp_df, sr) if sr else {}
        score_val = score_data.get("total", 0)

        emp_name = emp_info["name"]
        emp_id_map[emp_name] = int(emp_id)

        summary_rows.append({
            "Employee Name": emp_name,
            "Shift": get_shift_label(emp_info["category"]),
            "Present Days": present_weekday_count,
            "Late Arrivals": late_count,
            "Avg Productive Hours": f"{avg_hours:.2f}h",
            "Avg Break": f"{avg_break:.0f} min",
            "Break Violations": violations_count,
            "Incomplete Days": incomplete_count,
            "Productivity Score": f"{score_val:.1f}",
        })

    if summary_rows:
        summary_df = pd.DataFrame(summary_rows)

        # Sorting controls
        sort_col1, sort_col2 = st.columns([3, 1])
        with sort_col1:
            sort_by = st.selectbox(
                "Sort by",
                summary_df.columns.tolist(),
                index=0,
                key="org_summary_sort_by"
            )
        with sort_col2:
            ascending = st.checkbox("Ascending", value=False, key="org_summary_ascending")

        # Sort
        try:
            if "Score" in sort_by:
                summary_df["_sort_key"] = summary_df[sort_by].str.extract(r"([\d.]+)").astype(float)
                summary_df_sorted = summary_df.sort_values("_sort_key", ascending=ascending).drop("_sort_key", axis=1)
            elif "Hours" in sort_by or "Break" in sort_by:
                summary_df["_sort_key"] = summary_df[sort_by].apply(
                    lambda x: float(str(x).replace("h", "").replace(" min", "")) if isinstance(x, str) else x
                )
                summary_df_sorted = summary_df.sort_values("_sort_key", ascending=ascending).drop("_sort_key", axis=1)
            elif sort_by in ("Present Days", "Late Arrivals", "Break Violations", "Incomplete Days"):
                summary_df_sorted = summary_df.sort_values(sort_by, ascending=ascending)
            else:
                summary_df_sorted = summary_df.sort_values(sort_by, ascending=ascending)
        except Exception:
            summary_df_sorted = summary_df

        st.dataframe(summary_df_sorted, use_container_width=True, hide_index=True)

        # Employee click-through — show buttons for each employee
        st.caption("Click an employee to view their detailed page:")
        emp_names_sorted = summary_df_sorted["Employee Name"].tolist()
        cols_per_row = 6
        for i in range(0, len(emp_names_sorted), cols_per_row):
            chunk = emp_names_sorted[i:i + cols_per_row]
            cols = st.columns(cols_per_row)
            for j, emp_name in enumerate(chunk):
                eid = emp_id_map.get(emp_name)
                if eid and cols[j].button(emp_name, key=f"sum_nav_{eid}", use_container_width=True):
                    st.session_state["nav_employee_id"] = eid
                    st.session_state["nav_date_start"] = start_date
                    st.session_state["nav_date_end"] = end_date
                    st.switch_page("pages/2_Individual_Employee.py")

        # CSV export
        csv_data = summary_df_sorted.to_csv(index=False)
        st.download_button(
            label="📥 Download as CSV",
            data=csv_data,
            file_name=f"all_employees_summary_{start_date}_{end_date}.csv",
            mime="text/csv",
            key="org_summary_csv_download"
        )
    else:
        st.info("No summary data available for the selected period.")
else:
    st.info("No data to summarize.")

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

        # Click-through navigation for late arrivals
        st.caption("Click to view employee details:")
        late_names = display_late["employee_name"].tolist()
        late_ids = display_late["employee_id"].tolist()
        cols_per_row = 6
        for i in range(0, len(late_names), cols_per_row):
            chunk_names = late_names[i:i + cols_per_row]
            chunk_ids = late_ids[i:i + cols_per_row]
            cols = st.columns(cols_per_row)
            for j, (name, eid) in enumerate(zip(chunk_names, chunk_ids)):
                if cols[j].button(name, key=f"late_nav_{eid}", use_container_width=True):
                    st.session_state["nav_employee_id"] = int(eid)
                    st.session_state["nav_date_start"] = start_date
                    st.session_state["nav_date_end"] = end_date
                    st.switch_page("pages/2_Individual_Employee.py")

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

        # Click-through navigation for incomplete
        st.caption("Click to view employee details:")
        inc_names = display_incomplete["employee_name"].tolist()
        inc_ids = display_incomplete["employee_id"].tolist()
        cols_per_row = 6
        for i in range(0, len(inc_names), cols_per_row):
            chunk_names = inc_names[i:i + cols_per_row]
            chunk_ids = inc_ids[i:i + cols_per_row]
            cols = st.columns(cols_per_row)
            for j, (name, eid) in enumerate(zip(chunk_names, chunk_ids)):
                if cols[j].button(name, key=f"inc_nav_{eid}", use_container_width=True):
                    st.session_state["nav_employee_id"] = int(eid)
                    st.session_state["nav_date_start"] = start_date
                    st.session_state["nav_date_end"] = end_date
                    st.switch_page("pages/2_Individual_Employee.py")

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
