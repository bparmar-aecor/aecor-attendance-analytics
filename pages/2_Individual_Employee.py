"""
pages/2_Individual_Employee.py
-----------------------------------------------------------
Individual Employee analytics — BRD-compliant, engine-driven.

Data source: device_logs (raw punches + regularizations), NOT attendance_logs.
Every metric is computed by processing.py — no reliance on eSSL's own numbers.

Per BRD §11.2. Policy choices:
  • In-progress days (today, or days with no last-out) are shown in the
    daily log with an "in progress" badge but excluded from stats and score.
  • Days with a single punch show "Missing clock-out" and are excluded from stats.
  • Grace period = 30 minutes (configurable in config.py).
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
# Employee picker — only Normal + Custom (Excluded are hidden per §4.2)
# ─────────────────────────────────────────────────────────────────
emps = load_employees_in_org()
if emps.empty:
    st.warning(
        "No employees assigned to Normal or Custom shift yet. "
        "Visit **Settings** to assign categories."
    )
    st.stop()

if "indiv_selected_eid" not in st.session_state:
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
# Filters row
# ─────────────────────────────────────────────────────────────────
today = date.today()
month_start = today.replace(day=1)

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
        ["This month", "Last 7 days", "Last 30 days", "Last 90 days", "Custom…"],
        index=0,
        key="indiv_preset",
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

with c3:
    date_range = st.date_input(
        "Date range",
        value=default_range,
        max_value=today,
        key=f"indiv_range_{preset}",
    )

if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
    start_date, end_date = date_range
elif isinstance(date_range, date):
    start_date = end_date = date_range
else:
    start_date, end_date = default_range

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
# Quick stats (6 cards)
# ─────────────────────────────────────────────────────────────────
st.subheader("Quick stats")
col1, col2, col3, col4, col5, col6 = st.columns(6)

present_days = int(present_finished.shape[0])
late_days = int(present_finished["is_late"].sum())
avg_hours = (
    float(present_finished["productive_hours"].mean())
    if present_days else 0.0
)
avg_break_min = (
    float(present_finished["total_break_hours"].mean() * 60)
    if present_days else 0.0
)
break_violation_days = int((~present_finished["break_within_policy"]).sum())
incomplete_days = int(present_finished["is_incomplete"].sum())

col1.metric("Present days", present_days)
col2.metric("Late arrivals", late_days,
            help="Informational only — not in score")
col3.metric("Avg productive hrs", f"{avg_hours:.1f}h")
col4.metric("Avg break", f"{avg_break_min:.0f} min")
col5.metric("Break violations", break_violation_days,
            help="Days where total break >1h or long break outside lunch")
col6.metric("Incomplete days", incomplete_days,
            help="Late AND did not complete required hours")

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
              help="Weight: 20% — (Present / Working) × 100")
    b2.metric("Hours completion", f"{score.get('hours_completion', 0):.0f}%",
              help="Weight: 40% — days within ±10min of required")
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


# ─────────────────────────────────────────────────────────────────
# ALL-EMPLOYEE SUMMARY TABLE (NEW in Phase 2)
# ─────────────────────────────────────────────────────────────────
st.divider()
st.subheader("📊 All Employees Summary")
st.caption("Compare all employees for the selected date range. Respects date filters above.")

# Load all employees (Normal + Custom only)
all_org_emps = load_employees_in_org()
if all_org_emps.empty:
    st.warning("No employees in org.")
else:
    # Build summary for each employee
    device_logs = load_device_logs_with_regularizations(start_date, end_date)
    leaves = load_leaves(start_date, end_date)
    
    summary_rows = []
    
    for _, emp in all_org_emps.iterrows():
        emp_id = int(emp["employee_id"])
        emp_logs = device_logs[device_logs["employee_id"] == emp_id]
        
        # Filter leaves if employee_id column exists
        if not leaves.empty and "employee_id" in leaves.columns:
            emp_leaves = leaves[leaves["employee_id"] == emp_id]
        else:
            emp_leaves = pd.DataFrame()  # empty dataframe if no leaves
        
        # Process period
        daily_df = process_employee_period(
            device_logs=emp_logs,
            employees=all_org_emps[all_org_emps["employee_id"] == emp_id],
            leaves=emp_leaves,
            start=start_date,
            end=end_date,
            today=date.today()
        )
        
        if daily_df.empty:
            continue
        
        # Filter to working days and present days
        working = daily_df[daily_df["is_working_day"] & ~daily_df["is_leave"]]
        present = daily_df[daily_df["is_present"]]
        
        if len(working) == 0:
            continue
        
        present_days = len(present)
        late_count = int(present["is_late"].sum()) if len(present) > 0 else 0
        violations = int(present[~present["break_within_policy"]].shape[0]) if len(present) > 0 else 0
        incomplete = int(present["is_incomplete"].sum()) if len(present) > 0 else 0
        
        avg_hours = present["productive_hours"].mean() if len(present) > 0 else 0.0
        avg_break = present["total_break_hours"].mean() if len(present) > 0 else 0.0
        
        # Compute score
        shift_code = emp["category"].lower()
        if "normal" in shift_code:
            sr = SHIFTS["normal"]
        elif "custom" in shift_code:
            sr = SHIFTS["custom"]
        else:
            sr = None
        
        score_data = compute_productivity_score(present, sr) if sr else {}
        score = score_data.get("total", 0) if score_data else 0
        
        summary_rows.append({
            "Employee Name": emp["name"],
            "Shift": get_shift_label(emp["category"]),
            "Present Days": present_days,
            "Late Arrivals": late_count,
            "Avg Productive Hours": f"{avg_hours:.2f}h",
            "Avg Break": f"{avg_break:.0f}m",
            "Break Violations": violations,
            "Incomplete Days": incomplete,
            "Productivity Score": f"{score:.1f}",
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
                key="summary_sort_by"
            )
        with sort_col2:
            ascending = st.checkbox("Ascending", value=False, key="summary_ascending")
        
        # Sort and display
        try:
            # Handle numeric columns
            if "Score" in sort_by:
                summary_df["_sort_key"] = summary_df[sort_by].str.extract(r"([\d.]+)").astype(float)
                summary_df_sorted = summary_df.sort_values("_sort_key", ascending=ascending).drop("_sort_key", axis=1)
            elif "Hours" in sort_by or "Days" in sort_by or "Violations" in sort_by:
                summary_df["_sort_key"] = summary_df[sort_by].apply(
                    lambda x: float(str(x).replace("h", "").replace("m", "")) if isinstance(x, str) else x
                )
                summary_df_sorted = summary_df.sort_values("_sort_key", ascending=ascending).drop("_sort_key", axis=1)
            else:
                summary_df_sorted = summary_df.sort_values(sort_by, ascending=ascending)
        except Exception:
            summary_df_sorted = summary_df
        
        st.dataframe(summary_df_sorted, use_container_width=True, hide_index=True)
        
        # CSV export
        csv_data = summary_df_sorted.to_csv(index=False)
        st.download_button(
            label="📥 Download as CSV",
            data=csv_data,
            file_name=f"all_employees_summary_{start_date}_{end_date}.csv",
            mime="text/csv",
            key="summary_csv_download"
        )
    else:
        st.info("No summary data available for the selected period.")
