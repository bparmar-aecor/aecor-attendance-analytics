"""
app.py — Aecor Attendance Analytics Dashboard
------------------------------------------------------------
Landing page / navigation hub only (BRD v3.3 §12.6).
No date range picker — each sub-page manages its own dates.

Run:  streamlit run app.py
Pages live in /pages and are auto-discovered by Streamlit.
"""
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
# Sidebar — connection status only (no date filters per v3.3)
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
    with st.expander("ℹ️ Active shifts"):
        for code, s in SHIFTS.items():
            st.markdown(
                f"**{s.name}**  \n"
                f"`{s.start.strftime('%H:%M')}–{s.end.strftime('%H:%M')}` · "
                f"{s.required_productive_hours}h productive + "
                f"{s.break_allowed_hours}h break  \n"
                f"Late threshold: `{s.late_threshold.strftime('%H:%M')}`"
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
- **Organization Overview** → company-wide KPIs, productivity score, trends, all-employee summary
- **Individual Employee** → per-employee daily log, break timeline, score breakdown
- **Leave Management** → mark / un-mark leaves
- **Punch Regularization** → fix missing or wrong punches (audit-logged)
- **Upload Data** → manual CSV / PDF ingestion when auto-sync is down
- **Settings** → assign employee categories (Normal / Custom / Excluded)

All business rules (shift timings, break limits, scoring weights) live in `config.py`.
Change them once and every page updates automatically.
""")
