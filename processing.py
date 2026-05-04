"""
processing.py
------------------------------------------------------------
Pure-function attendance processing engine.

All BRD §6–§12 logic lives here. No database, no Streamlit —
just pandas + datetime. Easy to unit-test, easy to swap.

Public API:
    - process_day(punches, shift_rule, leave_type=None) -> DayResult
    - process_employee_period(...)                     -> pd.DataFrame
    - compute_productivity_score(daily_df, shift_rule) -> dict
    - org_score(per_employee_scores)                   -> float
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, date, time
from typing import Iterable

import numpy as np
import pandas as pd

from config import (
    SHIFTS, SCORE_WEIGHTS, WORKING_WEEKDAYS,
    LUNCH_WINDOW_START, LUNCH_WINDOW_END,
    LONG_BREAK_THRESHOLD_MINUTES, PRE_LUNCH_CUTOFF, POST_LUNCH_CUTOFF,
    INCLUDED_CATEGORIES, ShiftRule,
)


# =============================================================
# Data containers
# =============================================================
@dataclass
class BreakSegment:
    start: datetime
    end:   datetime
    minutes: float
    is_lunch: bool
    is_long_pre_lunch:  bool = False
    is_long_post_lunch: bool = False


@dataclass
class DayResult:
    employee_id:        int
    work_date:          date
    shift_code:         str | None
    first_in:           datetime | None = None
    last_out:           datetime | None = None
    total_office_hours: float = 0.0
    total_break_hours:  float = 0.0
    productive_hours:   float = 0.0
    breaks:             list[BreakSegment] = field(default_factory=list)
    punch_count:        int = 0
    missed_punch:       bool = False
    is_present:         bool = False
    is_leave:           bool = False
    leave_type:         str | None = None
    is_late:            bool = False
    minutes_late:       int = 0
    is_compliant:       bool = False     # productive hrs >= required - variance
    is_incomplete:      bool = False     # late AND not compliant
    break_within_policy: bool = True
    break_violation_reason: str | None = None
    long_breaks_outside_lunch: int = 0
    is_in_progress:     bool = False     # today, or odd-punch day still open
    notes:              list[str] = field(default_factory=list)

    def to_row(self) -> dict:
        d = asdict(self)
        d["breaks"] = len(self.breaks)
        d["notes"] = "; ".join(self.notes) if self.notes else ""
        return d


# =============================================================
# Single-day processing
# =============================================================
def _detect_breaks(punches_sorted: list[datetime]) -> list[BreakSegment]:
    """
    Pair punches as in/out/in/out... Every gap between an out
    and the next in is a break (no minimum threshold). Per BRD §8.1.
    """
    breaks: list[BreakSegment] = []
    # Iterate in pairs: (out_i, in_i+1)  — i.e. punches[1]/[2], [3]/[4]...
    # punches: [in0, out0, in1, out1, in2, out2, ...]
    # gaps:    out0-in1, out1-in2, ...
    for i in range(1, len(punches_sorted) - 1, 2):
        out_t = punches_sorted[i]
        in_t  = punches_sorted[i + 1]
        if in_t <= out_t:
            continue   # ignore degenerate
        minutes = (in_t - out_t).total_seconds() / 60.0
        is_lunch = _overlaps_lunch(out_t.time(), in_t.time())
        seg = BreakSegment(
            start=out_t, end=in_t, minutes=minutes, is_lunch=is_lunch,
        )
        # Long-break flagging (BRD §8.3)
        if minutes > LONG_BREAK_THRESHOLD_MINUTES and not is_lunch:
            if out_t.time() < PRE_LUNCH_CUTOFF:
                seg.is_long_pre_lunch = True
            if out_t.time() > POST_LUNCH_CUTOFF:
                seg.is_long_post_lunch = True
        breaks.append(seg)
    return breaks


def _overlaps_lunch(start_t: time, end_t: time) -> bool:
    """True if the break overlaps the official lunch window."""
    return not (end_t <= LUNCH_WINDOW_START or start_t >= LUNCH_WINDOW_END)


def process_day(
    employee_id: int,
    work_date: date,
    punches: Iterable[datetime],
    shift_rule: ShiftRule | None,
    leave_type: str | None = None,
    today: date | None = None,
) -> DayResult:
    """
    Compute one employee's day from raw punch timestamps.
    `punches` may be empty (absent or leave).
    `shift_rule` is None for EXCLUDED employees.
    `today` — used to mark today's in-progress rows; defaults to date.today()
    """
    if today is None:
        today = date.today()

    res = DayResult(
        employee_id=employee_id,
        work_date=work_date,
        shift_code=shift_rule.code if shift_rule else None,
        leave_type=leave_type,
        is_leave=leave_type is not None,
    )

    # Leave day — short-circuit, don't compute
    if leave_type:
        res.notes.append(f"Leave: {leave_type}")
        return res

    punches_sorted = sorted(p for p in punches if p is not None)
    res.punch_count = len(punches_sorted)
    if not punches_sorted:
        return res   # absent

    res.is_present = True
    res.first_in = punches_sorted[0]
    res.last_out = punches_sorted[-1]
    res.total_office_hours = (res.last_out - res.first_in).total_seconds() / 3600.0

    # Missed-punch / in-progress detection
    # Today with any number of punches = in progress (not yet finalised)
    # Other days with odd punch count = missed punch, flagged
    if work_date >= today:
        res.is_in_progress = True
        res.notes.append("In progress — day not yet complete")

    if res.punch_count == 1:
        res.missed_punch = True
        res.notes.append("Single punch — missing clock-out")
        if not res.is_in_progress:
            # Fully-past day with one punch: broken data, skip further calc
            return res
    elif res.punch_count % 2 != 0:
        res.missed_punch = True
        res.notes.append("Odd number of punches — missing one")

    # Breaks
    res.breaks = _detect_breaks(punches_sorted)
    res.total_break_hours = sum(b.minutes for b in res.breaks) / 60.0
    res.long_breaks_outside_lunch = sum(
        1 for b in res.breaks
        if b.is_long_pre_lunch or b.is_long_post_lunch
    )

    # Productive hours (BRD §8.4)
    res.productive_hours = max(0.0, res.total_office_hours - res.total_break_hours)

    # Late detection (BRD v3.3 §7.1) — per-shift late threshold
    # Late only if clock-in is AFTER the late_threshold (not shift start).
    # Clock-in at or before threshold = not late. Informational only.
    if shift_rule:
        threshold_dt = datetime.combine(work_date, shift_rule.late_threshold, tzinfo=res.first_in.tzinfo)
        if res.first_in > threshold_dt:
            res.is_late = True
            res.minutes_late = int((res.first_in - threshold_dt).total_seconds() / 60)

    # In-progress days skip compliance & break-policy checks entirely.
    # Partial-day data would produce misleading "incomplete" flags.
    if res.is_in_progress:
        return res

    # Compliance (BRD §6 + §7.2)
    if shift_rule:
        res.is_compliant = res.productive_hours >= shift_rule.required_productive_hours
        if not res.is_compliant:
            res.is_incomplete = True
            if res.is_late:
                res.notes.append(
                    f"Incomplete: late by {res.minutes_late}m, "
                    f"productive {res.productive_hours:.2f}h "
                    f"< required {shift_rule.required_productive_hours}h"
                )
            else:
                res.notes.append(
                    f"Incomplete: productive {res.productive_hours:.2f}h "
                    f"< required {shift_rule.required_productive_hours}h"
                )

        # Break-policy violations (BRD §8.2 / §8.3)
        if res.total_break_hours > shift_rule.break_allowed_hours:
            if res.is_compliant:  # ← If hours are completed
                # Extended break but hours met = OK (new v3.2 rule)
                res.break_within_policy = True
                excess_min = (res.total_break_hours - shift_rule.break_allowed_hours) * 60
                res.notes.append(
                    f"Extended break taken ({int(excess_min//60)}h {int(excess_min%60)}m total)"
                )
            else:
                # Break exceeded AND hours short = violation
                res.break_within_policy = False
                res.break_violation_reason = (
                    f"Total break {res.total_break_hours*60:.0f}m exceeds policy"
                )
        if res.long_breaks_outside_lunch > 0:
            res.break_within_policy = False
            extra = (
                f"{res.long_breaks_outside_lunch} long break(s) outside lunch window"
            )
            res.break_violation_reason = (
                (res.break_violation_reason + "; " + extra)
                if res.break_violation_reason else extra
            )

    return res


# =============================================================
# Multi-day, multi-employee batch
# =============================================================
def process_employee_period(
    device_logs: pd.DataFrame,        # cols: employee_id, log_time
    employees:   pd.DataFrame,        # cols: employee_id, name, code, dept, category
    leaves:      pd.DataFrame,        # cols: employee_id, leave_date, leave_type
    start:       date,
    end:         date,
    today:       date | None = None,
) -> pd.DataFrame:
    """
    Build one row per (employee, calendar-day) for the period.
    Returns a flat DataFrame ready for KPI calculations.
    `today` — days >= today are marked in_progress; defaults to date.today()
    """
    if today is None:
        today = date.today()

    # Index leaves for O(1) lookup
    leave_lookup: dict[tuple[int, date], str] = {
        (int(r.employee_id), r.leave_date): r.leave_type
        for r in leaves.itertuples(index=False)
    } if not leaves.empty else {}

    # Pre-group device logs
    if not device_logs.empty:
        device_logs = device_logs.copy()
        device_logs["log_time"] = pd.to_datetime(device_logs["log_time"])
        device_logs["day"] = device_logs["log_time"].dt.date
        grouped = device_logs.groupby(["employee_id", "day"])["log_time"].apply(list)
    else:
        grouped = pd.Series(dtype=object)

    out_rows = []
    all_dates = pd.date_range(start, end, freq="D").date

    for emp in employees.itertuples(index=False):
        emp_id = int(emp.employee_id)
        category_raw = getattr(emp, "category", None) or "excluded"
        category = str(category_raw).lower()
        shift_rule = SHIFTS.get(category)   # None if EXCLUDED

        for d in all_dates:
            leave_type = leave_lookup.get((emp_id, d))
            try:
                punches = grouped.loc[(emp_id, d)] if (emp_id, d) in grouped.index else []
            except KeyError:
                punches = []
            res = process_day(
                employee_id=emp_id,
                work_date=d,
                punches=punches,
                shift_rule=shift_rule,
                leave_type=leave_type,
                today=today,
            )
            row = res.to_row()
            row["employee_name"] = getattr(emp, "name", "")
            row["employee_code"] = getattr(emp, "code", "")
            row["department"]    = getattr(emp, "department", "")
            row["category"]      = category
            row["weekday"]       = d.weekday()
            row["is_working_day"] = d.weekday() in WORKING_WEEKDAYS
            out_rows.append(row)

    df = pd.DataFrame(out_rows)
    return df


# =============================================================
# Productivity scoring  (BRD §12.1)
# =============================================================
def compute_productivity_score(emp_daily: pd.DataFrame, shift_rule: ShiftRule | None) -> dict:
    """
    Compute the four sub-scores + total for one employee over a period.
    `emp_daily` is the slice of the daily df for one employee.
    Excluded employees (shift_rule=None) return zeros.
    In-progress days (today, partial data) are filtered out.
    """
    if shift_rule is None or emp_daily.empty:
        return {"attendance": 0, "hours_completion": 0,
                "break_compliance": 0, "consistency": 0, "total": 0,
                "working_days": 0, "present_days": 0}

    # Drop in-progress days — BRD answer A: today is not computed
    df = emp_daily[~emp_daily.get("is_in_progress", False)] \
         if "is_in_progress" in emp_daily.columns else emp_daily

    working = df[df["is_working_day"] & ~df["is_leave"]]
    working_days = len(working)
    if working_days == 0:
        return {"attendance": 0, "hours_completion": 0,
                "break_compliance": 0, "consistency": 0, "total": 0,
                "working_days": 0, "present_days": 0}

    present = working[working["is_present"]]
    present_days = len(present)

    # Attendance
    attendance = (present_days / working_days) * 100 if working_days else 0

    # Hours completion (PRIMARY)
    hours_completion = (present["is_compliant"].sum() / present_days * 100) \
        if present_days else 0

    # Break compliance
    break_compliance = (present["break_within_policy"].sum() / present_days * 100) \
        if present_days else 0

    # Consistency (BRD §12.1: 100 − std(daily hours) × 10)
    if present_days >= 2:
        std = float(present["productive_hours"].std(ddof=0))
        consistency = max(0.0, 100.0 - std * 10.0)
    else:
        consistency = 100.0 if present_days == 1 else 0.0

    total = (
        attendance       * SCORE_WEIGHTS["attendance"]       +
        hours_completion * SCORE_WEIGHTS["hours_completion"] +
        break_compliance * SCORE_WEIGHTS["break_compliance"] +
        consistency      * SCORE_WEIGHTS["consistency"]
    )

    return {
        "attendance":       round(attendance, 1),
        "hours_completion": round(hours_completion, 1),
        "break_compliance": round(break_compliance, 1),
        "consistency":      round(consistency, 1),
        "total":            round(total, 1),
        "working_days":     working_days,
        "present_days":     present_days,
    }


def org_productivity_score(daily_df: pd.DataFrame) -> dict:
    """
    Average individual scores across all included employees (BRD §12.2).
    """
    included = daily_df[daily_df["category"].isin(INCLUDED_CATEGORIES)]
    if included.empty:
        return {"score": 0.0, "n_employees": 0}

    per_emp = []
    for emp_id, slice_ in included.groupby("employee_id"):
        cat = str(slice_["category"].iloc[0]).lower()
        s = compute_productivity_score(slice_, SHIFTS.get(cat))
        per_emp.append(s["total"])
    if not per_emp:
        return {"score": 0.0, "n_employees": 0}
    return {
        "score":       round(float(np.mean(per_emp)), 1),
        "n_employees": len(per_emp),
        "median":      round(float(np.median(per_emp)), 1),
        "stdev":       round(float(np.std(per_emp)), 1),
    }


# =============================================================
# Pattern / outlier helpers  (BRD §12.3)
# =============================================================
def weekday_attendance(daily_df: pd.DataFrame) -> pd.DataFrame:
    """Attendance % and avg productive hours by day of week."""
    df = daily_df[daily_df["category"].isin(INCLUDED_CATEGORIES)
                  & daily_df["is_working_day"]
                  & ~daily_df["is_leave"]]
    if df.empty:
        return pd.DataFrame()
    g = df.groupby("weekday").agg(
        attendance_rate=("is_present", "mean"),
        avg_productive=("productive_hours", "mean"),
        late_count=("is_late", "sum"),
    ).reset_index()
    g["attendance_rate"] *= 100
    weekday_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu",
                     4: "Fri", 5: "Sat", 6: "Sun"}
    g["weekday_name"] = g["weekday"].map(weekday_names)
    return g


def detect_outliers(daily_df: pd.DataFrame, sigma: float = 2.0) -> pd.DataFrame:
    """Days where productive hours deviate >sigma std from employee's mean."""
    df = daily_df[daily_df["is_present"] & ~daily_df["is_leave"]].copy()
    if df.empty:
        return df
    stats = df.groupby("employee_id")["productive_hours"].agg(["mean", "std"])
    df = df.merge(stats, on="employee_id", how="left")
    df["z"] = (df["productive_hours"] - df["mean"]) / df["std"].replace(0, np.nan)
    return df[df["z"].abs() >= sigma].sort_values("z")
