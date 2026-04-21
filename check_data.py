"""
PHASE 1 — Quick database verification.

Run this from your Mac terminal to confirm whether 16th & 17th April data
exists in Supabase, and what date column is being used.

Usage:
    cd ~/Desktop/projects/Attendance/aecor_attendance
    python3 check_data.py
"""
import os
import sys
from datetime import date

# Try to load credentials from Streamlit secrets first, then env vars
SUPABASE_URL = None
SUPABASE_KEY = None

try:
    import toml
    secrets_path = os.path.expanduser("~/.streamlit/secrets.toml")
    if not os.path.exists(secrets_path):
        secrets_path = ".streamlit/secrets.toml"
    if os.path.exists(secrets_path):
        s = toml.load(secrets_path)
        # Try nested first (current format), then flat (legacy)
        SUPABASE_URL = s.get("supabase", {}).get("url") or s.get("SUPABASE_URL")
        SUPABASE_KEY = s.get("supabase", {}).get("key") or s.get("SUPABASE_KEY")
except Exception:
    pass

if not SUPABASE_URL:
    SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://voawhdyaxdivegcncoeq.supabase.co")
if not SUPABASE_KEY:
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

if not SUPABASE_KEY:
    print("ERROR: SUPABASE_KEY not found.")
    print("Either set it in ~/.streamlit/secrets.toml or export SUPABASE_KEY=...")
    sys.exit(1)

try:
    from supabase import create_client
except ImportError:
    print("Installing supabase client...")
    os.system(f"{sys.executable} -m pip install supabase")
    from supabase import create_client

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

print("=" * 70)
print("APRIL 16 & 17, 2026 — DATA CHECK")
print("=" * 70)

# attendance_logs uses attendance_date (per sync script)
print("\n[1] attendance_logs by attendance_date")
try:
    for d in ["2026-04-16", "2026-04-17"]:
        r = sb.table("attendance_logs").select("*", count="exact") \
            .eq("attendance_date", d).limit(3).execute()
        print(f"    {d}  →  {r.count} rows")
        if r.data:
            sample = r.data[0]
            print(f"      sample: employee_id={sample.get('employee_id')}, "
                  f"in={sample.get('in_time')}, out={sample.get('out_time')}, "
                  f"status={sample.get('status')}")
except Exception as e:
    print(f"    ERR: {e}")

# device_logs uses log_date (timestamp)
print("\n[2] device_logs by log_date (raw punches)")
try:
    for d in ["2026-04-16", "2026-04-17"]:
        r = sb.table("device_logs").select("*", count="exact") \
            .gte("log_date", f"{d}T00:00:00") \
            .lte("log_date", f"{d}T23:59:59").limit(3).execute()
        print(f"    {d}  →  {r.count} punches")
        if r.data:
            users = sorted({row.get('user_id') for row in r.data[:3]})
            print(f"      sample users: {users}")
except Exception as e:
    print(f"    ERR: {e}")

# Most recent dates we have
print("\n[3] Most recent dates in attendance_logs")
try:
    r = sb.table("attendance_logs").select("attendance_date") \
        .order("attendance_date", desc=True).limit(30).execute()
    if r.data:
        dates = sorted({row["attendance_date"] for row in r.data}, reverse=True)
        print(f"    Latest 10: {dates[:10]}")
except Exception as e:
    print(f"    ERR: {e}")

print("\n[4] Most recent dates in device_logs")
try:
    r = sb.table("device_logs").select("log_date") \
        .order("log_date", desc=True).limit(50).execute()
    if r.data:
        dates = sorted({(row["log_date"] or "")[:10] for row in r.data if row.get("log_date")}, reverse=True)
        print(f"    Latest 10: {dates[:10]}")
except Exception as e:
    print(f"    ERR: {e}")

# Categories sanity
print("\n[5] employee_categories distribution")
try:
    r = sb.table("employee_categories").select("category").execute()
    counts = {}
    for row in r.data:
        c = row.get("category", "?")
        counts[c] = counts.get(c, 0) + 1
    print(f"    {counts}")
except Exception as e:
    print(f"    ERR: {e}")

print("\n" + "=" * 70)
print("INTERPRETATION")
print("=" * 70)
print("""
If [1] shows 0 rows for 16th/17th but [2] shows punches → sync script
  hasn't processed those days into attendance_logs yet (the eSSL software
  needs to compute daily attendance, not just receive raw punches).

If both [1] and [2] show 0 → punches never reached Supabase.
  Check eSSL Online Downloader & sync script were running on those days.

If both show data but dashboard shows nothing → it's a date filter bug
  in the dashboard. Check the date range applied.
""")
