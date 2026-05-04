"""
pages/2_Individual_Employee.py
-----------------------------------------------------------
Individual Employee analytics — BRD v3.3 compliant.

Data source: device_logs (raw punches + regularizations), NOT attendance_logs.
Every metric is computed by processing.py — no reliance on eSSL's own numbers.

Per BRD v3.3:
  • Late threshold per shift — Normal: 11:00, Custom: 12:30 (§7.1)
  • Score weights: Attendance 10%, Hours 50%, Break 30%, Consistency 10% (§11.1)
  • Quick stats show X / Y (Z%) ratio format (§12.2)
  • Avg hours formula: total hours (weekday + weekend) / present weekday count (§12.5)
  • Date picker disabled unless Custom Range selected (§12.4)
  • Supports nav from Org Overview with employee ID + date range (§12.3)
  • All-Employee Summary Table moved to Org Overview page (§12.3)
  • In-progress days (today) shown but excluded from stats and score (§11.3)
  • Late is informational, NEVER penalises the score.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta, time as dtime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import SHIFTS
from data_loader import (
    load_employees_in_org,
    load_device_logs_with_regularizations,
    load_leaves,
    get_shift_label,
)
from processing import process_employee_period, compute_productivity_score


# ─────────────────────────────────────────────────────────────────
# Page config & header
# ─────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Individual Employee — Aecor",
                   page_icon="👤", layout="wide")
st.title("👤 Individual Employee")

# ─────────────────────────────────────────────────────────────────
# Check for navigation from Org Overview (click-through)
# ─────────────────────────────────────────────────────────────────
nav_eid = st.session_state.pop("nav_employee_id", None)
nav_start = st.session_state.pop("nav_date_start", None)
nav_end = st.session_state.pop("nav_date_end", None)

# ─────────────────────────────────────────────────────────────────
# Employee picker — only Normal + Custom (Excluded are hidden per §4.2)
# ─────────────────────────────────────────────────────────────────
emps = load_employees_in_org()
if emps.empty:
    st.warning(
        "No employees assigned to Normal or Custom shift yet. "
        "Visit **Settings** to assign categories."
    )
    st.stop()

# If navigated from Org Overview, use that employee; otherwise use session state
if nav_eid is not None:
    st.session_state.indiv_selected_eid = int(nav_eid)
elif "indiv_selected_eid" not in st.session_state:
    st.session_state.indiv_selected_eid = int(emps.iloc[0]["employee_id"])

emp_options = list(zip(emps["employee_id"].tolist(), emps["name"].tolist()))
emp_labels = [
    f"{name}  ·  {code}  ·  {get_shift_label(cat)}"
    for name, code, cat in zip(emps["name"], emps["code"], emps["category"])
]

try:
    default_idx = next(
        i for i, (eid, _) in enumerate(emp_options)
        if int(eid) == int(st.session_state.indiv_selected_eid)
    )
except StopIteration:
    default_idx = 0

# ─────────────────────────────────────────────────────────────────
# Filters row (BRD v3.3 §12.4 — disabled unless Custom Range)
# ─────────────────────────────────────────────────────────────────
today = date.today()
month_start = today.replace(day=1)

# Determine default preset based on navigation
if nav_start and nav_end:
    default_preset_idx = 3  # Custom Range
else:
    default_preset_idx = 2  # This month

c1, c2, c3 = st.columns([3, 1, 2])

with c1:
    pick_idx = st.selectbox(
        "Employee",
        options=range(len(emp_labels)),
        format_func=lambda i: emp_labels[i],
        index=default_idx,
        key="indiv_employee_picker",
    )
    selected_eid = int(emp_options[pick_idx][0])
    st.session_state.indiv_selected_eid = selected_eid

with c2:
    preset = st.selectbox(
        "Quick range",
        ["Today", "This week", "This month", "Custom Range"],
        index=default_preset_idx,
        key="indiv_preset",
    )

# Compute date range based on preset
if preset == "Today":
    start_date, end_date = today, today
elif preset == "This week":
    start_date = today - timedelta(days=today.weekday())
    end_date = today
elif preset == "This month":
    start_date, end_date = month_start, today
else:
    # Custom Range — use nav dates if available
    if nav_start and nav_end:
        start_date, end_date = nav_start, nav_end
    else:
        start_date, end_date = month_start, today

is_custom = preset == "Custom Range"

with c3:
    if is_custom:
        date_range = st.date_input(
            "Date range",
            value=(start_date, end_date),
            max_value=today,
            key="indiv_range_custom",
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
            key="indiv_range_preset",
        )

if start_date > end_date:
    st.error("Start date must be ≤ end date.")
    st.stop()

# Employee header
sel_row = emps.loc[emps["employee_id"] == selected_eid].iloc[0]
category = str(sel_row["category"]).lower()
shift_rule = SHIFTS.get(category)

st.markdown(
    f"### {sel_row['name']}  "
    f"<span style='font-size:14px;color:#888'>"
    f"&nbsp;·&nbsp;{sel_row['code']}&nbsp;·&nbsp;{get_shift_label(category)}"
    f"</span>",
    unsafe_allow_html=True,
)
st.caption(
    f"Period: **{start_date.strftime('%d %b %Y')}** → "
    f"**{end_date.strftime('%d %b %Y')}** "
    f"({(end_date - start_date).days + 1} days)"
)
st.divider()


# ─────────────────────────────────────────────────────────────────
# Load data & run engine
# ─────────────────────────────────────────────────────────────────
with st.spinner("Loading punches & computing metrics…"):
    device_logs = load_device_logs_with_regularizations(
        start_date, end_date, employee_ids=[selected_eid]
    )
    leaves = load_leaves(start_date, end_date)
    if not leaves.empty:
        leaves = leaves[leaves["employee_id"] == selected_eid]

    # Employee frame as the engine expects it
    emp_frame = emps.loc[emps["employee_id"] == selected_eid].copy()

    daily = process_employee_period(
        device_logs=device_logs,
        employees=emp_frame,
        leaves=leaves,
        start=start_date,
        end=end_date,
        today=today,
    )

if daily.empty:
    st.info("No data to display for this range.")
    st.stop()

# Stats only count fully-completed, non-leave days
finished = daily[~daily["is_in_progress"] & ~daily["is_leave"]]
present_finished = finished[finished["is_present"]]

# ─────────────────────────────────────────────────────────────────
# Quick stats (6 cards) — BRD v3.3 §12.2 ratio format
# ─────────────────────────────────────────────────────────────────
st.subheader("Quick stats")
col1, col2, col3, col4, col5, col6 = st.columns(6)

# Calculate denominators
present_weekday = present_finished[present_finished["is_working_day"]]
present_weekday_count = len(present_weekday)

# Working days = weekdays (non-leave, non-in-progress) in the period
working_days_count = len(finished[finished["is_working_day"] & ~finished["is_leave"]])

# Present days — X / Y (Z%)
present_pct = (present_weekday_count / working_days_count * 100) if working_days_count > 0 else 0
col1.metric("Present days", f"{present_weekday_count} / {working_days_count} ({present_pct:.0f}%)")

# Late arrivals — X / Y (Z%)
late_days = int(present_weekday["is_late"].sum()) if present_weekday_count > 0 else 0
late_pct = (late_days / present_weekday_count * 100) if present_weekday_count > 0 else 0
col2.metric("Late arrivals", f"{late_days} / {present_weekday_count} ({late_pct:.0f}%)",
            help="Informational only — not in score")

# Avg productive hours — BRD §12.5: total hours (weekday + weekend) / present weekday count
total_prod_hours = float(present_finished["productive_hours"].sum()) if len(present_finished) > 0 else 0.0
avg_hours = total_prod_hours / present_weekday_count if present_weekday_count > 0 else 0.0
col3.metric("Avg productive hrs", f"{avg_hours:.1f}h")

# Avg break — weekday breaks only / present weekday count
avg_break_min = (float(present_weekday["total_break_hours"].sum() * 60) / present_weekday_count) if present_weekday_count > 0 else 0.0
col4.metric("Avg break", f"{avg_break_min:.0f} min")

# Break violations — X / Y (Z%)
break_violation_days = int((~present_weekday["break_within_policy"]).sum()) if present_weekday_count > 0 else 0
bv_pct = (break_violation_days / present_weekday_count * 100) if present_weekday_count > 0 else 0
col5.metric("Break violations", f"{break_violation_days} / {present_weekday_count} ({bv_pct:.0f}%)",
            help="Days with long breaks outside lunch window")

# Incomplete days — X / Y (Z%)
incomplete_days = int(present_weekday["is_incomplete"].sum()) if present_weekday_count > 0 else 0
inc_pct = (incomplete_days / present_weekday_count * 100) if present_weekday_count > 0 else 0
col6.metric("Incomplete days", f"{incomplete_days} / {present_weekday_count} ({inc_pct:.0f}%)",
            help="Days where productive hours fell short of requirement")

st.divider()

# ─────────────────────────────────────────────────────────────────
# Productivity score
# ─────────────────────────────────────────────────────────────────
st.subheader("Productivity score")
score = compute_productivity_score(daily, shift_rule)

score_col, breakdown_col = st.columns([1, 3])
with score_col:
    total = score.get("total", 0)
    # Colour the score by band
    if total >= 85:
        colour, label = "#16a34a", "Excellent"
    elif total >= 70:
        colour, label = "#2563eb", "Good"
    elif total >= 50:
        colour, label = "#f59e0b", "Needs attention"
    else:
        colour, label = "#dc2626", "Poor"
    st.markdown(
        f"<div style='text-align:center;padding:1rem;border-radius:12px;"
        f"background:{colour}15;border:2px solid {colour};'>"
        f"<div style='font-size:48px;font-weight:700;color:{colour};'>"
        f"{total:.1f}</div>"
        f"<div style='color:{colour};font-weight:500;'>{label}</div>"
        f"<div style='color:#888;font-size:12px;'>out of 100</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

with breakdown_col:
    b1, b2, b3, b4 = st.columns(4)
    b1.metric("Attendance", f"{score.get('attendance', 0):.0f}%",
              help="Weight: 10% — (Present weekdays / Working days) × 100")
    b2.metric("Hours completion", f"{score.get('hours_completion', 0):.0f}%",
              help="Weight: 50% — days meeting full required hours (strict, no tolerance)")
    b3.metric("Break compliance", f"{score.get('break_compliance', 0):.0f}%",
              help="Weight: 30% — days within break policy")
    b4.metric("Consistency", f"{score.get('consistency', 0):.0f}%",
              help="Weight: 10% — 100 − std(daily hours) × 10")
    st.caption(
        f"Based on **{score.get('present_days', 0)} present** of "
        f"**{score.get('working_days', 0)} working** days. "
        f"In-progress days excluded."
    )

st.divider()


# ─────────────────────────────────────────────────────────────────
# Daily log
# ─────────────────────────────────────────────────────────────────
st.subheader("Daily log")

# Build display rows — keep raw date for sorting
disp = daily.copy()
disp = disp.sort_values("work_date", ascending=False)


def _fmt_time(dt):
    if pd.isna(dt) or dt is None:
        return "—"
    if isinstance(dt, str):
        try:
            dt = pd.to_datetime(dt)
        except Exception:
            return dt
    return dt.strftime("%H:%M")


def _status_badge(r):
    if r["is_leave"]:
        return f"🌴 Leave ({r.get('leave_type', '') or ''})"
    if r["is_in_progress"]:
        return "⏱️ In progress"
    if not r["is_present"]:
        return "❌ Absent"
    if r.get("missed_punch", False):
        return "⚠️ Missed punch"
    if r["is_incomplete"]:
        return "🔴 Incomplete"
    if not r["break_within_policy"]:
        return "🟠 Break violation"
    if r["is_compliant"]:
        return "✅ Compliant"
    return "⚪ —"


rows = []
for r in disp.itertuples(index=False):
    rows.append({
        "Date":           r.work_date.strftime("%a %d %b"),
        "First in":       _fmt_time(r.first_in),
        "Last out":       _fmt_time(r.last_out),
        "Total (h)":      f"{r.total_office_hours:.2f}" if r.total_office_hours else "—",
        "Break (min)":    f"{r.total_break_hours * 60:.0f}" if r.total_break_hours else "—",
        "Productive (h)": f"{r.productive_hours:.2f}" if r.productive_hours else "—",
        "Late by":        f"{r.minutes_late}m" if r.is_late else "—",
        "Status":         _status_badge(r._asdict() if hasattr(r, '_asdict') else r.__dict__),
        "Notes":          r.notes or "",
    })

st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, height=420)

st.divider()


# ─────────────────────────────────────────────────────────────────
# Break timeline (pick a day)
# ─────────────────────────────────────────────────────────────────
st.subheader("Break timeline")

# Only show days that had punches
days_with_data = daily[daily["is_present"]].sort_values("work_date", ascending=False)
if days_with_data.empty:
    st.caption("No punch data available to visualise in this period.")
else:
    tl_date = st.date_input(
        "Pick a day",
        value=days_with_data.iloc[0]["work_date"],
        min_value=days_with_data["work_date"].min(),
        max_value=days_with_data["work_date"].max(),
        key="tl_date",
    )

    row = daily[daily["work_date"] == tl_date]
    if row.empty or not row.iloc[0]["is_present"]:
        st.caption(f"No punches on {tl_date.strftime('%d %b %Y')}.")
    else:
        day_logs = device_logs[
            (device_logs["employee_id"] == selected_eid) &
            (device_logs["log_date"].dt.date == tl_date)
        ].sort_values("log_date")

        if day_logs.empty:
            st.caption("No punches.")
        else:
            punches = day_logs["log_date"].tolist()
            first_in, last_out = punches[0], punches[-1]

            # Build Gantt-style bar: work / break blocks between punches
            segments = []
            # Pair punches in/out/in/out...
            for i in range(len(punches) - 1):
                seg_start, seg_end = punches[i], punches[i + 1]
                duration_min = (seg_end - seg_start).total_seconds() / 60
                if i % 2 == 0:
                    # work block
                    segments.append({
                        "type": "Work", "start": seg_start, "end": seg_end,
                        "colour": "#16a34a", "duration": duration_min,
                    })
                else:
                    # break block — classify
                    lunch_start = datetime.combine(tl_date, dtime(13, 0),
                                                   tzinfo=seg_start.tzinfo)
                    lunch_end = datetime.combine(tl_date, dtime(14, 0),
                                                 tzinfo=seg_start.tzinfo)
                    is_lunch = not (seg_end <= lunch_start or seg_start >= lunch_end)
                    is_long = duration_min > 30 and not is_lunch
                    out_time = seg_start.time()
                    is_pre = is_long and out_time < dtime(12, 0)
                    is_post = is_long and out_time > dtime(15, 0)

                    if is_pre or is_post:
                        colour, typ = "#dc2626", "Long break (flagged)"
                    elif is_lunch:
                        colour, typ = "#f59e0b", "Lunch break"
                    else:
                        colour, typ = "#fbbf24", "Break"

                    segments.append({
                        "type": typ, "start": seg_start, "end": seg_end,
                        "colour": colour, "duration": duration_min,
                    })

            # If odd number of punches, add "in progress" marker for the tail
            if len(punches) % 2 == 1:
                st.caption(
                    "⚠️ Odd number of punches — missing a clock-out. "
                    "Timeline shows only paired segments."
                )

            # Build plotly horizontal bar
            fig = go.Figure()
            for seg in segments:
                fig.add_trace(go.Bar(
                    x=[(seg["end"] - seg["start"]).total_seconds() / 60],
                    y=[sel_row["name"]],
                    base=[(seg["start"] - first_in).total_seconds() / 60],
                    orientation="h",
                    marker=dict(color=seg["colour"]),
                    name=seg["type"],
                    hovertemplate=(
                        f"<b>{seg['type']}</b><br>"
                        f"{seg['start'].strftime('%H:%M')} → "
                        f"{seg['end'].strftime('%H:%M')}<br>"
                        f"{seg['duration']:.0f} min<extra></extra>"
                    ),
                    showlegend=False,
                ))
            fig.update_layout(
                barmode="stack",
                height=120,
                margin=dict(l=10, r=10, t=10, b=40),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(
                    title="Minutes from first punch",
                    showgrid=True, gridcolor="#252f42",
                ),
                yaxis=dict(showticklabels=False),
            )
            st.plotly_chart(fig, use_container_width=True)

            # Legend + stats
            lc1, lc2, lc3, lc4 = st.columns(4)
            lc1.markdown("🟢 **Work**")
            lc2.markdown("🟡 **Lunch / short break**")
            lc3.markdown("🔴 **Long break (flagged)**")
            total_break = sum(s["duration"] for s in segments if "break" in s["type"].lower() or "Lunch" in s["type"])
            total_work = sum(s["duration"] for s in segments if s["type"] == "Work")
            lc4.markdown(
                f"**{first_in.strftime('%H:%M')}** – **{last_out.strftime('%H:%M')}**  "
                f"· Work: {total_work:.0f}m · Break: {total_break:.0f}m"
            )

st.divider()


# ─────────────────────────────────────────────────────────────────
# Daily productive hours chart
# ─────────────────────────────────────────────────────────────────
st.subheader("Daily productive hours")

chart_df = daily[daily["is_present"] & ~daily["is_in_progress"]].copy()
if chart_df.empty:
    st.caption("No completed days with punches in this period.")
else:
    # Colour: red = incomplete, amber = break violation, green = compliant,
    # grey = missed punch / partial
    def _bar_colour(r):
        if r["missed_punch"]:
            return "#9ca3af"
        if r["is_incomplete"]:
            return "#dc2626"
        if not r["break_within_policy"]:
            return "#f59e0b"
        if r["is_compliant"]:
            return "#16a34a"
        return "#6b7280"

    chart_df["colour"] = chart_df.apply(_bar_colour, axis=1)
    chart_df = chart_df.sort_values("work_date")

    required = shift_rule.required_productive_hours if shift_rule else 8.0

    fig = go.Figure()
    fig.add_bar(
        x=pd.to_datetime(chart_df["work_date"]),
        y=chart_df["productive_hours"],
        marker_color=chart_df["colour"].tolist(),
        hovertemplate="%{x|%d %b %a}<br>Productive: %{y:.2f}h<extra></extra>",
    )
    fig.add_hline(
        y=required, line_dash="dash", line_color="#64748b",
        annotation_text=f"Required {required}h",
        annotation_position="top right",
    )
    fig.update_layout(
        height=320,
        margin=dict(l=10, r=10, t=10, b=10),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=False),
        yaxis=dict(title="Hours", gridcolor="#252f42"),
    )
    st.plotly_chart(fig, use_container_width=True)

    lc1, lc2, lc3, lc4 = st.columns(4)
    lc1.markdown("🟢 **Compliant**")
    lc2.markdown("🟠 **Break violation**")
    lc3.markdown("🔴 **Incomplete**")
    lc4.markdown("⚪ **Missed punch**")
