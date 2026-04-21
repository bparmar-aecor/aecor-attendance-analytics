"""Manual data upload — CSV or PDF fallback ingestion."""
import streamlit as st
import pandas as pd

from config import APP_TITLE
from ingestion import normalize_csv, normalize_pdf, insert_punches

st.set_page_config(page_title=f"{APP_TITLE} — Upload", layout="wide")
st.title("📤 Upload Attendance Data")
st.caption(
    "Use this when the auto-sync is down, or to backfill historical data. "
    "Supports CSV and PDF exports from eSSL eTimeTrackLite."
)

upl = st.file_uploader(
    "Choose a file", type=["csv", "pdf"], accept_multiple_files=False,
)
if not upl:
    st.info("Upload a CSV or PDF to begin.")
    st.stop()

with st.spinner(f"Parsing {upl.name}..."):
    try:
        if upl.name.lower().endswith(".pdf"):
            df = normalize_pdf(upl.read())
        else:
            df = normalize_csv(upl.read())
    except Exception as e:
        st.error(f"❌ Could not parse file: {e}")
        st.stop()

st.success(f"Parsed **{len(df):,}** punches.")
st.subheader("Preview (first 50 rows)")
st.dataframe(df.head(50), use_container_width=True, hide_index=True)

st.subheader("Summary")
c1, c2, c3 = st.columns(3)
c1.metric("Unique employees", df["employee_id"].nunique())
c2.metric("Date range", f"{df['log_time'].min().date()} → {df['log_time'].max().date()}")
c3.metric("Total punches", len(df))

st.divider()
if st.button("⬆️ Insert into Supabase", type="primary"):
    with st.spinner("Uploading..."):
        result = insert_punches(df)
    st.success(
        f"✅ Inserted {result['inserted']} new punches "
        f"({result['skipped']} were duplicates and skipped)."
    )
    st.cache_data.clear()
