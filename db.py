"""
db.py
------------------------------------------------------------
Single source of truth for the Supabase client.

Credentials are read from Streamlit secrets in this priority:
  1. st.secrets["supabase"]["url"] / ["key"]   (nested — preferred)
  2. st.secrets["SUPABASE_URL"]    / ["SUPABASE_KEY"]   (flat — fallback)
  3. Environment variables SUPABASE_URL / SUPABASE_KEY  (fallback)
  4. Streamlit error guiding the user to fix their config

Use the SERVICE-ROLE key — the dashboard writes to leaves,
regularisations, and categories. RLS will block writes if the
anon key is used.
"""
from __future__ import annotations
import os
import streamlit as st
from supabase import create_client, Client


def _read_secret() -> tuple[str | None, str | None]:
    """Try nested → flat → env, in that order."""
    url = key = None

    # 1. Nested format
    try:
        sec = st.secrets
        if "supabase" in sec:
            url = sec["supabase"].get("url")
            key = sec["supabase"].get("key")
    except (FileNotFoundError, KeyError, AttributeError):
        pass

    # 2. Flat format
    if not (url and key):
        try:
            url = url or st.secrets.get("SUPABASE_URL")
            key = key or st.secrets.get("SUPABASE_KEY")
        except (FileNotFoundError, KeyError, AttributeError):
            pass

    # 3. Environment
    url = url or os.environ.get("SUPABASE_URL")
    key = key or os.environ.get("SUPABASE_KEY")

    return url, key


@st.cache_resource
def get_client() -> Client:
    """Cached Supabase client — one per Streamlit session."""
    url, key = _read_secret()
    if not url or not key:
        st.error(
            "Supabase credentials missing. Add them to "
            "`.streamlit/secrets.toml`:\n\n"
            "```toml\n[supabase]\n"
            'url = "https://voawhdyaxdivegcncoeq.supabase.co"\n'
            'key = "<service-role-key>"\n```'
        )
        st.stop()
    return create_client(url, key)


def healthcheck() -> tuple[bool, str]:
    """Quick connectivity probe used in the sidebar."""
    try:
        client = get_client()
        client.table("employees").select("employee_id").limit(1).execute()
        return True, "Connected"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
