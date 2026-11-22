from collections import Counter, defaultdict
from datetime import date, timedelta
from math import ceil

from ortools.sat.python import cp_model
from sqlalchemy.orm import Session

from .models import (
    Allocation,
    Brand,
    CompanyHoliday,
    CoverageRule,
    Employee,
    Process,
    Roster,
    RosterEntry,
    TimeOff,
)


def _dates(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _week_groups(start: date, end: date) -> list[list[date]]:
    monday = start - timedelta(days=start.weekday())
    groups = []
    while monday <= end:
        week_start, week_end = monday, monday + timedelta(days=6)
        groups.append([d for d in _dates(max(start, week_start), min(end, week_end))])
        monday += timedelta(days=7)
    return groups


def _workday(rule: CoverageRule, day: date) -> bool:
    if rule.calendar == "DAILY":
        return True
    if rule.calendar == "MON_SAT":
        return day.weekday() < 6
    if rule.calendar == "CUSTOM":
        try:
            weekdays = {int(x.strip()) for x in rule.work_days.split(",") if x.strip()}
        except ValueError:
            return False
        return all(0 <= weekday <= 6 for weekday in weekdays) and day.weekday() in weekdays
    return False


def _error_key(issue: dict) -> tuple:
    return (
        issue.get("type"),
        issue.get("employee_id"),
        issue.get("brand_id"),
        issue.get("process_id"),
        issue.get("day"),
        issue.get("start"),
        issue.get("end"),
    )


def validate_roster(db: Session, roster_id: int, entries: list[RosterEntry] | None = None) -> dict:
    roster = db.get(Roster, roster_id)
    if not roster:
        raise ValueError("Roster not found.")

    entries = entries if entries is not None else db.query(RosterEntry).filter_by(roster_id=roster_id).all()
    rules = db.query(CoverageRule).all()
    employees = {row.id: row for row in db.query(Employee).all()}
    brands = {row.id: row.name for row in db.query(Brand).all()}
    processes = {row.id: row.name for row in db.query(Process).all()}
    allocations = db.query(Allocation).all()
    allocation_roles = defaultdict(set)
    for allocation in allocations:
        allocation_roles[(allocation.employee_id, allocation.brand_id, allocation.process_id)].add(
            allocation.role.upper()
        )

    time_off = defaultdict(set)
    for row in db.query(TimeOff).filter(TimeOff.day >= roster.start, TimeOff.day <= roster.end).all():
        time_off[row.employee_id].add(row.day)
    holidays = {
        row.day: row for row in db.query(CompanyHoliday)
        .filter(CompanyHoliday.day >= roster.start, CompanyHoliday.day <= roster.end).all()
    }
    closed_days = {day for day, holiday in holidays.items() if holiday.full_closure}
    holiday_days = set(holidays)

    issues = []
    warnings = []
    counts = defaultdict(int)
    employee_days = defaultdict(list)
    duplicate_keys = Counter()
    active_entries = [row for row in entries if row.status == "WORK"]

    for row in active_entries:
        counts[(row.brand_id, row.process_id, row.day)] += int(row.role == "PRIMARY")
        employee_days[(row.employee_id, row.day)].append(row)
        duplicate_keys[(row.employee_id, row.brand_id, row.process_id, row.day)] += 1
        employee = employees.get(row.employee_id)
        eligible_roles = allocation_roles[(row.employee_id, row.brand_id, row.process_id)]
        if row.source == "BACKUP_SUGGESTION" and row.role == "PRIMARY":
            eligible = "BACKUP" in eligible_roles
        else:
            eligible = row.role.upper() in eligible_roles
        if not eligible:
            issues.append({
                "type": "INELIGIBLE_ASSIGNMENT", "employee_id": row.employee_id,
                "brand_id": row.brand_id, "process_id": row.process_id, "day": str(row.day),
            })
        if employee is None or not employee.active:
            issues.append({"type": "INACTIVE_EMPLOYEE", "employee_id": row.employee_id, "day": str(row.day)})
        if row.day in time_off[row.employee_id]:
            issues.append({
                "type": "EMPLOYEE_UNAVAILABLE", "employee_id": row.employee_id,
                "day": str(row.day), "reason": "TIME_OFF",
            })
        if row.day in closed_days:
            issues.append({
                "type": "COMPANY_CLOSURE", "employee_id": row.employee_id,
                "day": str(row.day), "description": holidays[row.day].description,
            })

    for (employee_id, brand_id, process_id, day), count in duplicate_keys.items():
        if count > 1:
            issues.append({
                "type": "DUPLICATE_ASSIGNMENT", "employee_id": employee_id,
                "brand_id": brand_id, "process_id": process_id, "day": str(day),
            })
    for (employee_id, day), assigned in employee_days.items():
        if len(assigned) > 1:
            issues.append({"type": "DOUBLE_BOOKING", "employee_id": employee_id, "day": str(day)})

    for rule in rules:
        for day in _dates(roster.start, roster.end):
            if day in closed_days or not _workday(rule, day):
                continue
            assigned = counts[(rule.brand_id, rule.process_id, day)]
            if assigned < rule.min_staff:
                issues.append({
                    "type": "MINIMUM_COVERAGE", "day": str(day), "brand_id": rule.brand_id,
                    "brand": brands.get(rule.brand_id), "process_id": rule.process_id,
                    "process": processes.get(rule.process_id), "required": rule.min_staff,
                    "assigned": assigned,
                })

    for employee_id, employee in employees.items():
        work_days = {
            day for (assigned_employee, day), assigned in employee_days.items()
            if assigned_employee == employee_id and assigned
        }
        for week in _week_groups(roster.start, roster.end):
            unavailable_days = time_off[employee_id] | closed_days
            work_eligible_days = [day for day in week if day not in unavailable_days]
            off_eligible_days = [
                day for day in week
                if day not in unavailable_days and day not in holiday_days
            ]
            worked = [day for day in week if day in work_days]
            required_offs = ceil(employee.min_offs * len(week) / 7)
            actual_offs = len(off_eligible_days) - len(set(worked).intersection(off_eligible_days))
            if actual_offs < required_offs:
                issues.append({
                    "type": "MINIMUM_WEEKLY_OFFS", "employee_id": employee_id,
                    "start": str(week[0]), "end": str(week[-1]),
                    "required": required_offs, "actual": actual_offs,
                })
            max_weekly = min(employee.max_work_days, len(work_eligible_days))
            if len(worked) > max_weekly:
                issues.append({
                    "type": "MAXIMUM_WORK_DAYS", "employee_id": employee_id,
                    "start": str(week[0]), "end": str(week[-1]),
                    "maximum": max_weekly, "assigned": len(worked),
                })

        consecutive = 0
        for day in _dates(roster.start, roster.end):
            if day in work_days:
                consecutive += 1
                if consecutive > employee.max_days:
                    issues.append({
                        "type": "MAXIMUM_CONSECUTIVE_DAYS", "employee_id": employee_id,
                        "day": str(day), "maximum": employee.max_days,
                    })
            else:
                consecutive = 0

    if roster.status != "PUBLISHED":
        warnings.append({"type": "ROSTER_NOT_PUBLISHED", "status": roster.status})
    return {
        "valid": not issues,
        "errors": issues,
        "warnings": warnings,
        "issues": issues,
        "entry_count": len(entries),
    }


def build_roster(
    db: Session,
    start: date,
    end: date,
    time_limit: int = 10,
    allow_incomplete: bool = False,
    existing_roster_id: int | None = None,
):
    if end < start:
        raise ValueError("End date must be on or after start date.")
    days = _dates(start, end)
    staff = db.query(Employee).filter(Employee.active.is_(True)).all()
    rules = db.query(CoverageRule).all()
    allocations = db.query(Allocation).all()
    time_off = db.query(TimeOff).filter(TimeOff.day >= start, TimeOff.day <= end).all()
    holidays = db.query(CompanyHoliday).filter(
        CompanyHoliday.day >= start, CompanyHoliday.day <= end
    ).all()
    if not staff or not rules:
        raise ValueError("Add active employees, allocations and coverage rules first.")

    unavailable = {(row.employee_id, row.day) for row in time_off}
    closed_days = {row.day for row in holidays if row.full_closure}
    holiday_days = {row.day for row in holidays}
    locked_entries = []
    if existing_roster_id is not None:
        locked_entries = db.query(RosterEntry).filter_by(
            roster_id=existing_roster_id, locked=True
        ).all()
    by_bp = defaultdict(list)
    for allocation in allocations:
        by_bp[(allocation.brand_id, allocation.process_id)].append(
            (allocation.employee_id, allocation.role.upper())
        )
    employees = {employee.id: employee for employee in staff}
    model = cp_model.CpModel()
    assignments = {}
    active = {}

    for employee in staff:
        for day in days:
            active[employee.id, day] = model.NewBoolVar(f"a_{employee.id}_{day.toordinal()}")
            if (employee.id, day) in unavailable or day in closed_days:
                model.Add(active[employee.id, day] == 0)

    for employee in staff:
        for day in days:
            slots = []
            for rule in rules:
                bp = (rule.brand_id, rule.process_id)
                primary_ids = {eid for eid, role in by_bp[bp] if role == "PRIMARY"}
                locked_for_slot = any(
                    entry.employee_id == employee.id and entry.brand_id == bp[0]
                    and entry.process_id == bp[1] and entry.day == day
                    and "BACKUP" in {role for eid, role in by_bp[bp] if eid == employee.id}
                    for entry in locked_entries
                )
                if (employee.id in primary_ids or locked_for_slot) and _workday(rule, day) and day not in closed_days:
                    var = model.NewBoolVar(f"x_{employee.id}_{bp[0]}_{bp[1]}_{day.toordinal()}")
                    assignments[(employee.id, bp, day)] = var
                    slots.append(var)
            if slots:
                model.Add(sum(slots) <= 1)
                model.Add(active[employee.id, day] == sum(slots))
            else:
                model.Add(active[employee.id, day] == 0)

    gaps = {}
    for rule in rules:
        bp = (rule.brand_id, rule.process_id)
        eligible_ids = {
            eid for eid, role in by_bp[bp] if role == "PRIMARY" and eid in employees
        } | {
            entry.employee_id for entry in locked_entries
            if entry.brand_id == bp[0] and entry.process_id == bp[1]
            and entry.role == "PRIMARY" and entry.employee_id in employees
            and "BACKUP" in {role for eid, role in by_bp[bp] if eid == entry.employee_id}
        }
        for day in days:
            if day in closed_days or not _workday(rule, day):
                continue
            assigned = [
                assignments[(eid, bp, day)] for eid in eligible_ids
                if (eid, bp, day) in assignments
            ]
            if allow_incomplete:
                gap = model.NewIntVar(0, rule.min_staff, f"gap_{bp[0]}_{bp[1]}_{day.toordinal()}")
                model.Add(sum(assigned) + gap >= rule.min_staff)
                gaps[(bp, day)] = gap
            else:
                model.Add(sum(assigned) >= rule.min_staff)

    for employee in staff:
        for window_start in range(max(0, len(days) - employee.max_days)):
            model.Add(
                sum(active[employee.id, day] for day in days[window_start:window_start + employee.max_days + 1])
                <= employee.max_days
            )

        employee_offs = {row.day for row in time_off if row.employee_id == employee.id}
        for week in _week_groups(start, end):
            work_eligible_days = [
                day for day in week if day not in employee_offs and day not in closed_days
            ]
            off_eligible_days = [
                day for day in work_eligible_days if day not in holiday_days
            ]
            required_offs = ceil(employee.min_offs * len(week) / 7)
            off_eligible_assignments = sum(active[employee.id, day] for day in off_eligible_days)
            work_assignments = sum(active[employee.id, day] for day in work_eligible_days)
            model.Add(off_eligible_assignments <= max(0, len(off_eligible_days) - required_offs))
            model.Add(work_assignments <= min(employee.max_work_days, len(work_eligible_days)))

    if existing_roster_id is not None:
        for entry in locked_entries:
            key = (entry.employee_id, (entry.brand_id, entry.process_id), entry.day)
            if entry.role != "PRIMARY" or key not in assignments:
                model.AddBoolOr([])
            else:
                model.Add(assignments[key] == 1)

    loads = []
    for employee in staff:
        load = model.NewIntVar(0, len(days), f"load_{employee.id}")
        model.Add(load == sum(active[employee.id, day] for day in days))
        loads.append(load)
    max_load = model.NewIntVar(0, len(days), "max_load")
    min_load = model.NewIntVar(0, len(days), "min_load")
    model.AddMaxEquality(max_load, loads)
    model.AddMinEquality(min_load, loads)
    total_gaps = sum(gaps.values()) if gaps else 0
    model.Minimize(total_gaps * 100000 + (max_load - min_load) * 100 + sum(loads))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)
    solver_status = solver.StatusName(status)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, {
            "solver_status": solver_status,
            "entries": 0,
            "coverage_gaps": 0,
            "gaps": [],
            "valid": False,
        }

    if existing_roster_id is None:
        roster = Roster(start=start, end=end, status="DRAFT")
        db.add(roster)
        db.flush()
    else:
        roster = db.get(Roster, existing_roster_id)
        if roster is None:
            raise ValueError("Roster not found.")
        if roster.status == "PUBLISHED":
            raise ValueError("Published rosters cannot be regenerated.")
        for entry in db.query(RosterEntry).filter_by(roster_id=roster.id, locked=False).all():
            db.delete(entry)
        roster.start, roster.end = start, end
        roster.version += 1

    preserved = {
        (entry.employee_id, entry.brand_id, entry.process_id, entry.day)
        for entry in locked_entries
    }
    new_entries = []
    for (employee_id, bp, day), variable in assignments.items():
        if solver.Value(variable):
            key = (employee_id, bp[0], bp[1], day)
            if key not in preserved:
                new_entries.append(RosterEntry(
                    roster_id=roster.id, employee_id=employee_id, brand_id=bp[0],
                    process_id=bp[1], day=day, role="PRIMARY", status="WORK", source="SOLVER",
                ))
    db.add_all(new_entries)
    db.commit()
    gap_details = []
    brand_names = {row.id: row.name for row in db.query(Brand).all()}
    process_names = {row.id: row.name for row in db.query(Process).all()}
    for (bp, day), variable in gaps.items():
        missing = solver.Value(variable)
        if missing:
            gap_details.append({
                "brand_id": bp[0], "brand": brand_names.get(bp[0]),
                "process_id": bp[1], "process": process_names.get(bp[1]),
                "day": str(day), "missing": missing,
            })
    validation = validate_roster(db, roster.id)
    return roster, {
        "solver_status": solver_status,
        "entries": len(new_entries) + len(preserved),
        "coverage_gaps": len(gap_details),
        "gaps": gap_details,
        "valid": validation["valid"],
    }
