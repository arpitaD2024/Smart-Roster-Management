from collections import defaultdict
from datetime import timedelta

from sqlalchemy.orm import Session

from .models import (
    Allocation,
    CompanyHoliday,
    CoverageRule,
    Employee,
    RosterEntry,
    Roster,
    TimeOff,
)
from .scheduler import _dates, _error_key, _week_groups, _workday, validate_roster


def _new_errors(before: list[dict], after: list[dict]) -> list[dict]:
    remaining = defaultdict(int)
    for issue in before:
        remaining[_error_key(issue)] += 1
    added = []
    for issue in after:
        key = _error_key(issue)
        if remaining[key]:
            remaining[key] -= 1
        else:
            added.append(issue)
    return added


def suggest_backups(db: Session, roster_id: int) -> list[dict]:
    baseline = validate_roster(db, roster_id)
    roster = db.get(Roster, roster_id)
    if roster is None:
        raise ValueError("Roster not found.")
    roster_entries = db.query(RosterEntry).filter_by(roster_id=roster_id).all()
    rules = db.query(CoverageRule).all()
    employees = {row.id: row for row in db.query(Employee).filter(Employee.active.is_(True)).all()}
    allocations = db.query(Allocation).filter_by(role="BACKUP").all()
    backup_by_bp = defaultdict(set)
    for allocation in allocations:
        backup_by_bp[(allocation.brand_id, allocation.process_id)].add(allocation.employee_id)

    assigned_count = defaultdict(int)
    assigned_days = defaultdict(set)
    for entry in roster_entries:
        if entry.status == "WORK":
            assigned_days[entry.employee_id].add(entry.day)
            if entry.role == "PRIMARY":
                assigned_count[(entry.brand_id, entry.process_id, entry.day)] += 1

    start, end = roster.start, roster.end
    time_off = defaultdict(set)
    for row in db.query(TimeOff).filter(TimeOff.day >= start, TimeOff.day <= end).all():
        time_off[row.employee_id].add(row.day)
    holidays = db.query(CompanyHoliday).filter(
        CompanyHoliday.day >= start, CompanyHoliday.day <= end
    ).all()
    closed_days = {row.day for row in holidays if row.full_closure}
    holiday_days = {row.day for row in holidays}

    suggestions = []
    for rule in rules:
        bp = (rule.brand_id, rule.process_id)
        candidates = sorted(backup_by_bp[bp])
        for day in _dates(start, end):
            if day in closed_days or not _workday(rule, day):
                continue
            missing = rule.min_staff - assigned_count[(bp[0], bp[1], day)]
            for _ in range(max(0, missing)):
                for employee_id in candidates:
                    employee = employees.get(employee_id)
                    if employee is None or day in time_off[employee_id]:
                        continue
                    if day in assigned_days[employee_id]:
                        continue
                    week = next(group for group in _week_groups(start, end) if day in group)
                    week_days = assigned_days[employee_id].intersection(week)
                    unavailable = time_off[employee_id] | closed_days
                    work_eligible = [week_day for week_day in week if week_day not in unavailable]
                    off_eligible = [
                        week_day for week_day in work_eligible if week_day not in holiday_days
                    ]
                    required_offs = (employee.min_offs * len(week) + 6) // 7
                    if len(week_days) + 1 > min(employee.max_work_days, len(work_eligible)):
                        continue
                    work_on_off_eligible = len(week_days.intersection(off_eligible))
                    if day in off_eligible:
                        work_on_off_eligible += 1
                    if work_on_off_eligible > len(off_eligible) - required_offs:
                        continue
                    consecutive = 1
                    cursor = day - timedelta(days=1)
                    while cursor in assigned_days[employee_id]:
                        consecutive += 1
                        cursor -= timedelta(days=1)
                    cursor = day + timedelta(days=1)
                    while cursor in assigned_days[employee_id]:
                        consecutive += 1
                        cursor += timedelta(days=1)
                    if consecutive > employee.max_days:
                        continue
                    candidate = RosterEntry(
                        id=-1, roster_id=roster_id, employee_id=employee_id,
                        brand_id=bp[0], process_id=bp[1], day=day,
                        role="PRIMARY", status="WORK", source="BACKUP_SUGGESTION",
                        locked=False,
                    )
                    simulated = validate_roster(db, roster_id, roster_entries + [candidate])
                    if _new_errors(baseline["errors"], simulated["errors"]):
                        continue
                    suggestions.append({
                        "employee_id": employee_id, "brand_id": bp[0],
                        "process_id": bp[1], "day": str(day), "role": "PRIMARY",
                        "source": "BACKUP_SUGGESTION",
                    })
                    assigned_days[employee_id].add(day)
                    assigned_count[(bp[0], bp[1], day)] += 1
                    break
    return suggestions
