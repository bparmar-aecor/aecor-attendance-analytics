"""
config.py
------------------------------------------------------------
Single source of truth for all business rules. Change rules
here; the rest of the system reads from this module.

Per BRD v3.3 (AECOR-ATT-BRD-003).
"""
from datetime import time
from dataclasses import dataclass


# -----------------------------------------------------------
# Shift definitions (BRD §6)
# -----------------------------------------------------------
@dataclass(frozen=True)
class ShiftRule:
    name: str
    code: str
    start: time
    end: time
    required_productive_hours: float   # hours
    break_allowed_hours: float         # hours
    late_threshold: time               # Clock-in after this = late (BRD v3.3 §7.1)
    variance_minutes: int = 0          # No tolerance in v3.2 (was: 10)


SHIFTS: dict[str, ShiftRule] = {
    "normal": ShiftRule(
        name="Normal Shift (A)",
        code="normal",
        start=time(10, 0),
        end=time(19, 0),
        required_productive_hours=8.0,
        break_allowed_hours=1.0,
        late_threshold=time(11, 0),    # Late only after 11:00 AM
    ),
    "custom": ShiftRule(
        name="Custom Shift (B)",
        code="custom",
        start=time(12, 0),
        end=time(18, 0),
        required_productive_hours=5.0,
        break_allowed_hours=1.0,
        late_threshold=time(12, 30),   # Late only after 12:30 PM
    ),
    # Future: add SHIFT_C here when timings are finalised.
}

EXCLUDED_CATEGORY = "excluded"   # not in org analytics
DEFAULT_CATEGORY  = "excluded"   # new employees default to this
INCLUDED_CATEGORIES = ("normal", "custom")


# -----------------------------------------------------------
# Break-time rules (BRD §8)
# -----------------------------------------------------------
LUNCH_WINDOW_START = time(13, 0)
LUNCH_WINDOW_END   = time(14, 0)

LONG_BREAK_THRESHOLD_MINUTES = 30
PRE_LUNCH_CUTOFF  = time(12, 0)   # >30m break before this = flagged
POST_LUNCH_CUTOFF = time(15, 0)   # >30m break after this  = flagged


# -----------------------------------------------------------
# Productivity scoring weights (BRD v3.3 §11.1)
# -----------------------------------------------------------
SCORE_WEIGHTS = {
    "attendance":        0.10,   # Was 0.20 in v3.2
    "hours_completion":  0.50,   # PRIMARY metric (was 0.40 in v3.2)
    "break_compliance":  0.30,
    "consistency":       0.10,
}
assert abs(sum(SCORE_WEIGHTS.values()) - 1.0) < 1e-9, "Weights must sum to 1.0"


# -----------------------------------------------------------
# Leave types (BRD §9.1)
# -----------------------------------------------------------
LEAVE_TYPES = (
    "Casual Leave",
    "Sick Leave",
    "Earned Leave",
    "Half Day",
    "Work From Home",
    "Comp Off",
)


# -----------------------------------------------------------
# Working week (BRD §6.2 — Mon-Fri)
# Sunday = 6 in Python (Mon=0).
# Saturdays and Sundays are weekends per BRD v3.2 §6.2.
# -----------------------------------------------------------
WORKING_WEEKDAYS = (0, 1, 2, 3, 4)   # Mon-Fri only


# -----------------------------------------------------------
# Display
# -----------------------------------------------------------
APP_TITLE = "Aecor — Attendance Analytics"
ORG_NAME  = "Aecor Digital"
TIMEZONE  = "Asia/Kolkata"
