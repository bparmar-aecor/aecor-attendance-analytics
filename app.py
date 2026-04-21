"""
app.py — Aecor Attendance Analytics Dashboard
------------------------------------------------------------
Run:  streamlit run app.py
Pages live in /pages and are auto-discovered by Streamlit.
"""
from datetime import date, timedelta
import streamlit as st

from config import APP_TITLE, ORG_NAME, SHIFTS
from db import healthcheck

st.set_page_config(
    page_title=APP_TITLE,
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --------------------------------------------------------------
# Sidebar — global filters & connection status
# --------------------------------------------------------------
with st.sidebar:
    st.title("📊 Aecor Attendance")
    st.caption(ORG_NAME)

    ok, msg = healthcheck()
    if ok:
        st.success(f"🟢 Supabase: {msg}")
    else:
        st.error(f"🔴 Supabase: {msg}")
    st.divider()

    st.markdown("### Date range")
    preset = st.radio(
        "Quick select",
        ["Today", "This week", "This month", "Last 30 days", "Custom"],
        index=2,
        label_visibility="collapsed",
    )
    today = date.today()
    if preset == "Today":
        start, end = today, today
    elif preset == "This week":
        start, end = today - timedelta(days=today.weekday()), today
    elif preset == "This month":
        start, end = today.replace(day=1), today
    elif preset == "Last 30 days":
        start, end = today - timedelta(days=30), today
    else:
        c1, c2 = st.columns(2)
        start = c1.date_input("From", today.replace(day=1))
        end   = c2.date_input("To", today)

    if start > end:
        st.error("Start date must be ≤ end date")
        st.stop()

    st.session_state["date_start"] = start
    st.session_state["date_end"]   = end
    st.caption(f"📅 {start.isoformat()} → {end.isoformat()}")

    st.divider()
    with st.expander("ℹ️ Active shifts"):
        for code, s in SHIFTS.items():
            st.markdown(
                f"**{s.name}**  \n"
                f"`{s.start.strftime('%H:%M')}–{s.end.strftime('%H:%M')}` · "
                f"{s.required_productive_hours}h productive + "
                f"{s.break_allowed_hours}h break"
            )

# --------------------------------------------------------------
# Landing
# --------------------------------------------------------------
st.title("📊 Attendance Analytics")
st.markdown(f"Welcome to the **{ORG_NAME}** attendance dashboard.")

c1, c2, c3, c4 = st.columns(4)
c1.page_link("pages/1_Organization_Overview.py", label="🏢 Organization", icon=None)
c2.page_link("pages/2_Individual_Employee.py",   label="👤 Individual",   icon=None)
c3.page_link("pages/3_Leave_Management.py",      label="📅 Leaves",       icon=None)
c4.page_link("pages/4_Punch_Regularization.py",  label="✏️ Regularize",   icon=None)

c5, c6, _, _ = st.columns(4)
c5.page_link("pages/5_Upload_Data.py",   label="📤 Upload",  icon=None)
c6.page_link("pages/6_Settings.py",      label="⚙️ Settings", icon=None)

st.divider()
st.markdown("""
### How this dashboard works
- **Organization Overview** → company-wide KPIs, productivity score, trends
- **Individual Employee** → per-employee daily log, break timeline, score breakdown
- **Leave Management** → mark / un-mark leaves
- **Punch Regularization** → fix missing or wrong punches (audit-logged)
- **Upload Data** → manual CSV / PDF ingestion when auto-sync is down
- **Settings** → assign employee categories (Normal / Custom / Excluded)

All business rules (shift timings, break limits, scoring weights) live in `config.py`.
Change them once and every page updates automatically.
""")
