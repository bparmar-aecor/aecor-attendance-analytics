"""Leave Management (BRD §10)"""
from datetime import date
import streamlit as st
import pandas as pd

from config import APP_TITLE, LEAVE_TYPES
from data_loader import load_employees, load_leaves, add_leave, delete_leave

st.set_page_config(page_title=f"{APP_TITLE} — Leaves", layout="wide")
st.title("📅 Leave Management")

employees = load_employees()
if employees.empty:
    st.warning("No employees in Supabase.")
    st.stop()

employees["label"] = (
    employees["name"].fillna("?") + " (" + employees["code"].astype(str) + ")"
)

tab_mark, tab_view = st.tabs(["✏️ Mark leave", "📋 View / delete leaves"])

# ---------------------------------------------------------------------
with tab_mark:
    with st.form("leave_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        sel = c1.selectbox("Employee", employees["label"])
        leave_type = c2.selectbox("Leave type", LEAVE_TYPES)

        c3, c4 = st.columns(2)
        start = c3.date_input("From", date.today())
        end   = c4.date_input("To",   date.today())

        reason = st.text_area("Reason (optional)")
        marked_by = st.text_input("Marked by", value="admin")

        submit = st.form_submit_button("Mark leave", type="primary")

        if submit:
            if start > end:
                st.error("Start date must be ≤ end date")
            else:
                emp = employees[employees["label"] == sel].iloc[0]
                inserted = 0
                for d in pd.date_range(start, end, freq="D").date:
                    add_leave(int(emp["employee_id"]), d, leave_type, reason, marked_by)
                    inserted += 1
                st.success(f"✅ Marked {inserted} day(s) of {leave_type} for {emp['name']}")

# ---------------------------------------------------------------------
with tab_view:
    start = st.session_state.get("date_start", date.today().replace(day=1))
    end   = st.session_state.get("date_end",   date.today())
    st.caption(f"Showing leaves for {start.isoformat()} → {end.isoformat()} "
               "(change in sidebar)")

    leaves = load_leaves(start, end)
    if leaves.empty:
        st.info("No leaves in this period.")
    else:
        joined = leaves.merge(
            employees[["employee_id", "name", "code", "department"]],
            on="employee_id", how="left",
        ).sort_values(["leave_date", "name"])

        st.dataframe(
            joined[["leave_date", "name", "code", "department",
                    "leave_type", "reason", "marked_by"]],
            use_container_width=True, hide_index=True,
        )

        st.divider()
        st.subheader("Delete a leave")
        with st.form("delete_form"):
            del_emp = st.selectbox("Employee", joined["name"].unique())
            available_dates = joined[joined["name"] == del_emp]["leave_date"].tolist()
            del_date = st.selectbox("Date", available_dates)
            del_submit = st.form_submit_button("Delete", type="secondary")
            if del_submit:
                emp_row = employees[employees["name"] == del_emp].iloc[0]
                delete_leave(int(emp_row["employee_id"]), del_date)
                st.success(f"Removed leave for {del_emp} on {del_date}")
                st.rerun()
