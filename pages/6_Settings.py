"""
pages/6_Settings.py — Employee category assignment

Assign employees to shift categories. Excluded employees are omitted
from organisation analytics.

Notes:
  • Selected employee persists across reruns (session_state)
  • Notes field intentionally removed
  • Uses normalised column names (`name`, `code`) from data_loader
"""
import streamlit as st
import pandas as pd
from datetime import date

from data_loader import load_employees, upsert_category, get_shift_label

st.set_page_config(page_title="Settings — Aecor", page_icon="⚙️", layout="wide")
st.title("⚙️ Settings — Employee Categories")
st.caption(
    "Assign employees to shift categories. "
    "Excluded employees are omitted from organisation analytics."
)

# ─────────────────────────────────────────────────────────────────
# Load
# ─────────────────────────────────────────────────────────────────
emps = load_employees()
if emps.empty:
    st.warning("No active employees found in the database.")
    st.stop()

CATEGORY_OPTIONS = [
    ("normal",   "Normal Shift (10–7)"),
    ("custom",   "Custom Shift (12–6)"),
    ("excluded", "Excluded — not in org analytics"),
]
CAT_VALUES = [c[0] for c in CATEGORY_OPTIONS]
CAT_LABELS = [c[1] for c in CATEGORY_OPTIONS]

# ─────────────────────────────────────────────────────────────────
# Persist selected employee across reruns
# ─────────────────────────────────────────────────────────────────
emp_options = list(zip(emps["employee_id"].tolist(), emps["name"].tolist()))
emp_labels  = [f"{name}  ·  {code}" for name, code in
               zip(emps["name"], emps["code"])]

if "settings_selected_eid" not in st.session_state:
    st.session_state.settings_selected_eid = int(emp_options[0][0])

try:
    default_idx = next(i for i, (eid, _) in enumerate(emp_options)
                       if int(eid) == int(st.session_state.settings_selected_eid))
except StopIteration:
    default_idx = 0

st.subheader("Edit employee assignment")
col_pick, col_cat = st.columns([2, 2])

with col_pick:
    pick_idx = st.selectbox(
        "Employee",
        options=range(len(emp_labels)),
        format_func=lambda i: emp_labels[i],
        index=default_idx,
        key="settings_employee_picker",
    )
    selected_eid  = int(emp_options[pick_idx][0])
    selected_name = emp_options[pick_idx][1]
    st.session_state.settings_selected_eid = selected_eid

current_cat = (emps.loc[emps["employee_id"] == selected_eid, "category"]
                   .iloc[0] if not emps.empty else "excluded")
try:
    cat_default_idx = CAT_VALUES.index(current_cat)
except ValueError:
    cat_default_idx = 2  # excluded

with col_cat:
    new_cat_idx = st.selectbox(
        "Shift category",
        options=range(len(CAT_LABELS)),
        format_func=lambda i: CAT_LABELS[i],
        index=cat_default_idx,
        key=f"settings_cat_picker_{selected_eid}",
    )
    new_category = CAT_VALUES[new_cat_idx]

eff_date = st.date_input(
    "Effective from", value=date.today(),
    key=f"settings_eff_{selected_eid}",
)

if st.button("💾 Save assignment", type="primary"):
    try:
        upsert_category(selected_eid, new_category, eff_date)
        # Toast persists across the rerun; a plain success message would be wiped.
        st.toast(
            f"✅ Saved: {selected_name} → {get_shift_label(new_category)} "
            f"(effective {eff_date.isoformat()})",
            icon="💾",
        )
        # Force a clean rerun so the table below reflects the new assignment.
        st.rerun()
    except Exception as e:
        st.error(f"Save failed: {e}")

st.divider()

# ─────────────────────────────────────────────────────────────────
# Current assignments table
# ─────────────────────────────────────────────────────────────────
st.subheader("Current assignments")

# Refresh after a possible save
emps = load_employees()
display_df = pd.DataFrame({
    "Employee": emps["name"] + "  (" + emps["code"].astype(str) + ")",
    "Shift":    emps["category"].apply(get_shift_label),
})

flt = st.radio(
    "Filter",
    ["All", "Normal Shift", "Custom Shift", "Excluded"],
    horizontal=True, key="settings_filter",
)
filter_map = {
    "Normal Shift": "Normal Shift (10–7)",
    "Custom Shift": "Custom Shift (12–6)",
    "Excluded":     "Excluded",
}
if flt in filter_map:
    display_df = display_df[display_df["Shift"] == filter_map[flt]]

st.dataframe(
    display_df.reset_index(drop=True),
    use_container_width=True,
    hide_index=True,
    height=min(560, 60 + 35 * len(display_df)),
)

# Distribution recap
counts = emps["category"].value_counts().to_dict()
c1, c2, c3 = st.columns(3)
c1.metric("Normal Shift", counts.get("normal", 0))
c2.metric("Custom Shift", counts.get("custom", 0))
c3.metric("Excluded",     counts.get("excluded", 0))
