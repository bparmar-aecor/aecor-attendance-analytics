"""Punch Regularization (BRD §9)"""
from datetime import date, datetime, time
import streamlit as st
import pandas as pd

from config import APP_TITLE
from data_loader import load_employees, load_device_logs, add_regularization

st.set_page_config(page_title=f"{APP_TITLE} — Regularize", layout="wide")
st.title("✏️ Punch Regularization")
st.caption("Add, edit, or delete biometric punches. All actions are audit-logged. (BRD §9)")

employees = load_employees()
if employees.empty:
    st.warning("No employees in Supabase.")
    st.stop()

employees["label"] = (
    employees["name"].fillna("?") + " (" + employees["code"].astype(str) + ")"
)

c1, c2 = st.columns(2)
sel = c1.selectbox("Employee", employees["label"])
day = c2.date_input("Date", date.today())

emp = employees[employees["label"] == sel].iloc[0]
emp_id = int(emp["employee_id"])

# Show existing punches for this day
day_logs = load_device_logs(day, day)
day_logs = day_logs[day_logs["employee_id"] == emp_id].sort_values("log_time")

st.subheader(f"Existing punches — {emp['name']}, {day.isoformat()}")
if day_logs.empty:
    st.info("No punches recorded for this day.")
else:
    show = day_logs.copy()
    show["log_time"] = pd.to_datetime(show["log_time"]).dt.strftime("%H:%M:%S")
    st.dataframe(show[["log_time"]].rename(columns={"log_time": "Punch time"}),
                 use_container_width=True, hide_index=True)

if len(day_logs) % 2 != 0 and len(day_logs) > 0:
    st.warning(f"⚠️ Odd number of punches ({len(day_logs)}) — likely a missed punch.")

st.divider()

# ----- Action selector --------------------------------------------------
action = st.radio("Action", ["Add a punch", "Edit a punch", "Delete a punch"],
                  horizontal=True)

with st.form("reg_form"):
    if action == "Add a punch":
        new_time = st.time_input("New punch time", value=time(10, 0))
        reason = st.text_area("Reason (required)")
        approved_by = st.text_input("Approved by", "admin")
        submit = st.form_submit_button("Add punch", type="primary")
        if submit:
            if not reason.strip():
                st.error("Reason is mandatory.")
            else:
                ts = datetime.combine(day, new_time)
                add_regularization(emp_id, day, "add",
                                   original_time=None, corrected_time=ts,
                                   reason=reason, approved_by=approved_by)
                st.success(f"✅ Added punch at {ts.strftime('%H:%M')}")
                st.rerun()

    elif action == "Edit a punch":
        if day_logs.empty:
            st.info("No punches to edit.")
        else:
            existing_times = pd.to_datetime(day_logs["log_time"]).tolist()
            choice = st.selectbox(
                "Punch to edit",
                existing_times,
                format_func=lambda t: t.strftime("%H:%M:%S"),
            )
            new_time = st.time_input("Corrected time", value=choice.time())
            reason = st.text_area("Reason (required)")
            approved_by = st.text_input("Approved by", "admin")
            submit = st.form_submit_button("Save edit", type="primary")
            if submit:
                if not reason.strip():
                    st.error("Reason is mandatory.")
                else:
                    new_ts = datetime.combine(day, new_time)
                    add_regularization(emp_id, day, "edit",
                                       original_time=choice, corrected_time=new_ts,
                                       reason=reason, approved_by=approved_by)
                    st.success(f"✅ Edited punch {choice.strftime('%H:%M')} → "
                               f"{new_ts.strftime('%H:%M')}")
                    st.rerun()

    else:  # Delete
        if day_logs.empty:
            st.info("No punches to delete.")
        else:
            existing_times = pd.to_datetime(day_logs["log_time"]).tolist()
            choice = st.selectbox(
                "Punch to delete",
                existing_times,
                format_func=lambda t: t.strftime("%H:%M:%S"),
            )
            reason = st.text_area("Reason (required)")
            approved_by = st.text_input("Approved by", "admin")
            submit = st.form_submit_button("Delete punch", type="secondary")
            if submit:
                if not reason.strip():
                    st.error("Reason is mandatory.")
                else:
                    add_regularization(emp_id, day, "delete",
                                       original_time=choice, corrected_time=None,
                                       reason=reason, approved_by=approved_by)
                    st.success(f"✅ Deleted punch {choice.strftime('%H:%M')}")
                    st.rerun()

st.caption("All regularisations are stored in `punch_regularizations` and "
           "applied at read-time, so the original `device_logs` data is preserved.")
