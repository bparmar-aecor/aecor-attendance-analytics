"""
test_processing.py
------------------------------------------------------------
Unit tests for the core processing engine. Run with:
    python -m pytest test_processing.py -v
or just:
    python test_processing.py
"""
from datetime import datetime, date, time
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from config import SHIFTS
from processing import process_day, compute_productivity_score
import pandas as pd


def _t(h, m, s=0):
    return datetime(2026, 4, 15, h, m, s)


# =============================================================
# BRD §7.2 examples (adjusted for 30-min grace period)
# =============================================================
def test_brd_example_1_late_incomplete():
    """Shift A, arrives 10:45 (past grace), 1h break, leaves 18:00 → 6h15m → INCOMPLETE."""
    punches = [_t(10, 45), _t(13, 0), _t(14, 0), _t(18, 0)]
    res = process_day(1, date(2026, 4, 15), punches, SHIFTS["normal"],
                      today=date(2026, 4, 16))
    assert res.is_present
    assert res.is_late, "Should be late — past 30-min grace"
    assert res.minutes_late == 45
    assert abs(res.productive_hours - 6.25) < 0.01
    assert not res.is_compliant
    assert res.is_incomplete, "Should be flagged incomplete"


def test_brd_example_2_late_but_compliant():
    """Shift A, arrives 10:45, 45m break, leaves 19:30 → 8h → NOT incomplete."""
    punches = [_t(10, 45), _t(13, 0), _t(13, 45), _t(19, 30)]
    res = process_day(1, date(2026, 4, 15), punches, SHIFTS["normal"],
                      today=date(2026, 4, 16))
    assert res.is_late
    assert abs(res.productive_hours - 8.0) < 0.01
    assert res.is_compliant
    assert not res.is_incomplete


def test_grace_period_not_late():
    """Shift A, arrives at 10:29 → within grace, NOT late."""
    punches = [_t(10, 29), _t(13, 0), _t(14, 0), _t(19, 0)]
    res = process_day(1, date(2026, 4, 15), punches, SHIFTS["normal"],
                      today=date(2026, 4, 16))
    assert not res.is_late, "10:29 is within 30-min grace"


def test_grace_period_boundary_not_late():
    """Shift A, arrives exactly at 10:30 (grace boundary) → NOT late."""
    punches = [_t(10, 30), _t(13, 0), _t(14, 0), _t(19, 0)]
    res = process_day(1, date(2026, 4, 15), punches, SHIFTS["normal"],
                      today=date(2026, 4, 16))
    assert not res.is_late, "10:30 exactly is still within grace"


def test_past_grace_is_late():
    """Shift A, arrives at 10:31 → past grace, IS late."""
    punches = [_t(10, 31), _t(13, 0), _t(14, 0), _t(19, 30)]
    res = process_day(1, date(2026, 4, 15), punches, SHIFTS["normal"],
                      today=date(2026, 4, 16))
    assert res.is_late
    assert res.minutes_late == 31


# =============================================================
# Break detection (BRD §8.1)
# =============================================================
def test_breaks_no_minimum_threshold():
    """Even a 1-minute gap counts as a break."""
    punches = [_t(10, 0), _t(12, 0), _t(12, 1), _t(19, 0)]
    res = process_day(1, date(2026, 4, 15), punches, SHIFTS["normal"], today=date(2026, 4, 16))
    assert len(res.breaks) == 1
    assert abs(res.breaks[0].minutes - 1.0) < 0.01


def test_multiple_breaks_summed():
    punches = [_t(10, 0), _t(12, 30), _t(12, 45),
               _t(15, 0), _t(15, 15), _t(19, 0)]
    res = process_day(1, date(2026, 4, 15), punches, SHIFTS["normal"], today=date(2026, 4, 16))
    assert len(res.breaks) == 2
    assert abs(res.total_break_hours - 0.5) < 0.01   # 15m + 15m


def test_long_break_pre_lunch_flagged():
    punches = [_t(10, 0), _t(11, 0), _t(11, 45), _t(19, 0)]
    res = process_day(1, date(2026, 4, 15), punches, SHIFTS["normal"], today=date(2026, 4, 16))
    assert any(b.is_long_pre_lunch for b in res.breaks)


def test_lunch_break_not_flagged_as_long():
    punches = [_t(10, 0), _t(13, 0), _t(13, 50), _t(19, 0)]
    res = process_day(1, date(2026, 4, 15), punches, SHIFTS["normal"], today=date(2026, 4, 16))
    assert any(b.is_lunch for b in res.breaks)
    assert not any(b.is_long_pre_lunch or b.is_long_post_lunch
                   for b in res.breaks)


# =============================================================
# Missed-punch detection (BRD §9.3)
# =============================================================
def test_odd_punches_flagged():
    punches = [_t(10, 0), _t(13, 0), _t(14, 0)]   # 3 punches
    res = process_day(1, date(2026, 4, 15), punches, SHIFTS["normal"], today=date(2026, 4, 16))
    assert res.missed_punch


def test_single_punch_flagged():
    res = process_day(1, date(2026, 4, 15), [_t(10, 0)], SHIFTS["normal"], today=date(2026, 4, 16))
    assert res.missed_punch


# =============================================================
# Variance compliance (BRD §6.1)
# =============================================================
def test_within_variance_compliant():
    """7h 50m should be compliant for 8h required (variance 10m)."""
    punches = [_t(10, 0), _t(13, 0), _t(14, 0), _t(18, 50)]
    res = process_day(1, date(2026, 4, 15), punches, SHIFTS["normal"], today=date(2026, 4, 16))
    # Office = 8h50m, break = 1h, productive = 7h50m
    assert abs(res.productive_hours - 7.833333) < 0.01
    assert res.is_compliant   # within 10m variance


# =============================================================
# Excluded employees
# =============================================================
def test_excluded_no_compliance_calc():
    res = process_day(1, date(2026, 4, 15),
                      [_t(10, 0), _t(13, 0), _t(14, 0), _t(19, 0)],
                      shift_rule=None, today=date(2026, 4, 16))
    assert not res.is_compliant
    assert not res.is_late


# =============================================================
# Leave handling
# =============================================================
def test_leave_short_circuits():
    res = process_day(1, date(2026, 4, 15), [], SHIFTS["normal"],
                      leave_type="Casual Leave", today=date(2026, 4, 16))
    assert res.is_leave
    assert res.leave_type == "Casual Leave"
    assert not res.is_present


# =============================================================
# Productivity score
# =============================================================
def test_score_perfect_attendance():
    """5 perfect days → score should be 100."""
    rows = []
    for i, d in enumerate([date(2026, 4, 13), date(2026, 4, 14),
                           date(2026, 4, 15), date(2026, 4, 16),
                           date(2026, 4, 17)]):
        rows.append({
            "is_working_day": True, "is_leave": False, "is_present": True,
            "is_compliant": True, "break_within_policy": True,
            "productive_hours": 8.0, "is_late": False,
        })
    df = pd.DataFrame(rows)
    s = compute_productivity_score(df, SHIFTS["normal"])
    assert s["total"] == 100.0


def test_late_does_not_penalise_score():
    """Late but compliant days should still score 100."""
    rows = []
    for d in range(5):
        rows.append({
            "is_working_day": True, "is_leave": False, "is_present": True,
            "is_compliant": True, "break_within_policy": True,
            "productive_hours": 8.0, "is_late": True,    # late!
        })
    df = pd.DataFrame(rows)
    s = compute_productivity_score(df, SHIFTS["normal"])
    assert s["total"] == 100.0, "Late arrivals must not affect score"


# =============================================================
# Custom Shift B
# =============================================================
def test_custom_shift_5h_required():
    """Shift B: 12:00–18:00, 5h productive required."""
    punches = [_t(12, 0), _t(13, 0), _t(14, 0), _t(18, 0)]
    res = process_day(1, date(2026, 4, 15), punches, SHIFTS["custom"], today=date(2026, 4, 16))
    assert abs(res.productive_hours - 5.0) < 0.01
    assert res.is_compliant
    assert not res.is_late


# =============================================================
# Driver
# =============================================================
if __name__ == "__main__":
    import inspect
    tests = [(n, f) for n, f in globals().items()
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {name}  →  {e}")
            failed += 1
        except Exception as e:
            print(f"  💥 {name}  →  {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{passed+failed} passed")
    sys.exit(0 if failed == 0 else 1)
