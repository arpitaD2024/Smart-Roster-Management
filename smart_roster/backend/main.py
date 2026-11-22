import logging
import os
from collections import Counter
from datetime import date, datetime
from io import BytesIO

import pandas as pd
import requests
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from .backup import suggest_backups
from .db import engine, get_db
from .etl import import_allocations, import_employees
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
from .scheduler import _error_key, build_roster, validate_roster
from .schemas import (
    AllocationIn,
    BackupAcceptIn,
    BrandIn,
    CoverageIn,
    EmployeeIn,
    EmployeePatch,
    EntryPatch,
    GenerateIn,
    HolidayIn,
    ProcessIn,
    TimeOffIn,
)

app = FastAPI(
    title="Smart Roster Management API",
    version="1.0.0",
    description="Python API for workforce roster planning and validation.",
)

logger = logging.getLogger(__name__)
frontend_url = os.getenv("FRONTEND_URL", "").strip().rstrip("/")
cors_origins = [
    origin.strip().rstrip("/")
    for origin in os.getenv("CORS_ORIGINS", "").split(",")
    if origin.strip()
]
if frontend_url and frontend_url not in cors_origins:
    cors_origins.append(frontend_url)
if not cors_origins and os.getenv("APP_ENV", "development").strip().lower() != "production":
    cors_origins = ["http://localhost:8501", "http://127.0.0.1:8501"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(request: Request, exc: SQLAlchemyError):
    logger.exception("Database request failed.")
    return JSONResponse(
        status_code=503,
        content={"detail": "Database unavailable; verify DATABASE_URL and database network access."},
    )


def event(name, data):
    url = os.getenv("N8N_WEBHOOK_URL", "").strip()
    if url:
        try:
            requests.post(url, json={"event": name, "data": data}, timeout=4)
        except requests.RequestException:
            pass


def _commit_or_conflict(db: Session, message: str):
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, message) from exc


def _employee_dict(row: Employee) -> dict:
    return {
        "id": row.id, "name": row.name, "email": row.email,
        "employee_id": row.employee_id, "title": row.title, "pod": row.pod,
        "active": row.active, "max_days": row.max_days,
        "max_work_days": row.max_work_days, "min_offs": row.min_offs,
    }


def _roster_entries(db: Session, roster_id: int, date_from, date_to, brand_id, process_id, pod, employee_id):
    query = db.query(RosterEntry, Employee, Brand, Process).join(
        Employee, Employee.id == RosterEntry.employee_id
    ).join(Brand, Brand.id == RosterEntry.brand_id).join(
        Process, Process.id == RosterEntry.process_id
    ).filter(RosterEntry.roster_id == roster_id)
    if date_from:
        query = query.filter(RosterEntry.day >= date_from)
    if date_to:
        query = query.filter(RosterEntry.day <= date_to)
    if brand_id is not None:
        query = query.filter(RosterEntry.brand_id == brand_id)
    if process_id is not None:
        query = query.filter(RosterEntry.process_id == process_id)
    if employee_id is not None:
        query = query.filter(RosterEntry.employee_id == employee_id)
    if pod:
        query = query.filter(Employee.pod == pod)
    return [
        {
            "entry_id": entry.id, "employee_id": employee.id, "employee": employee.name,
            "brand_id": brand.id, "brand": brand.name,
            "process_id": process.id, "process": process.name,
            "pod": employee.pod, "day": str(entry.day), "role": entry.role,
            "source": entry.source, "status": entry.status, "locked": entry.locked,
        }
        for entry, employee, brand, process in query.order_by(
            RosterEntry.day, Brand.name, Process.name, Employee.name
        ).all()
    ]


def _new_issues(before: list[dict], after: list[dict]) -> list[dict]:
    remaining = Counter(_error_key(issue) for issue in before)
    added = []
    for issue in after:
        key = _error_key(issue)
        if remaining[key]:
            remaining[key] -= 1
        else:
            added.append(issue)
    return added


@app.get("/")
def home():
    return {"app": "Smart Roster Management", "docs": "/docs", "health": "/health"}


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.exception("Database health check failed.")
        raise HTTPException(
            status_code=503,
            detail="Database unavailable; verify DATABASE_URL and database network access.",
        ) from exc
    return {"status": "ok", "database": "connected"}


@app.post("/employees", status_code=201)
def add_employee(item: EmployeeIn, db: Session = Depends(get_db)):
    duplicate = db.query(Employee.id).filter(
        (func.lower(Employee.email) == item.email.lower())
        | (func.lower(Employee.employee_id) == item.employee_id.lower())
    ).first()
    if duplicate:
        raise HTTPException(409, "Email or employee_id already exists.")
    row = Employee(**item.model_dump())
    db.add(row)
    _commit_or_conflict(db, "Email or employee_id already exists.")
    db.refresh(row)
    return _employee_dict(row)


@app.get("/employees")
def get_employees(db: Session = Depends(get_db)):
    return [_employee_dict(row) for row in db.query(Employee).order_by(Employee.name).all()]


@app.get("/employees/{employee_id}")
def get_employee(employee_id: int, db: Session = Depends(get_db)):
    row = db.get(Employee, employee_id)
    if not row:
        raise HTTPException(404, "Employee not found.")
    return _employee_dict(row)


@app.patch("/employees/{employee_id}")
def update_employee(employee_id: int, item: EmployeePatch, db: Session = Depends(get_db)):
    row = db.get(Employee, employee_id)
    if not row:
        raise HTTPException(404, "Employee not found.")
    values = item.model_dump(exclude_unset=True)
    for field in ("email", "employee_id"):
        value = values.get(field)
        if value is not None:
            query = db.query(Employee.id).filter(func.lower(getattr(Employee, field)) == value.lower())
            if query.filter(Employee.id != employee_id).first():
                raise HTTPException(409, f"{field} already exists.")
    non_nullable = {"name", "email", "employee_id", "pod", "max_days", "max_work_days", "min_offs", "active"}
    if any(field in values and values[field] is None for field in non_nullable):
        raise HTTPException(422, "Required employee fields cannot be null.")
    for key, value in values.items():
        setattr(row, key, value)
    _commit_or_conflict(db, "Email or employee_id already exists.")
    db.refresh(row)
    return _employee_dict(row)


@app.post("/brands", status_code=201)
def add_brand(item: BrandIn, db: Session = Depends(get_db)):
    name = item.name.strip()
    if db.query(Brand.id).filter(func.lower(Brand.name) == name.lower()).first():
        raise HTTPException(409, "Brand already exists.")
    row = Brand(name=name)
    db.add(row)
    _commit_or_conflict(db, "Brand already exists.")
    db.refresh(row)
    return {"id": row.id, "name": row.name}


@app.get("/brands")
def get_brands(db: Session = Depends(get_db)):
    return [{"id": row.id, "name": row.name} for row in db.query(Brand).order_by(Brand.name).all()]


@app.patch("/brands/{brand_id}")
def update_brand(brand_id: int, item: BrandIn, db: Session = Depends(get_db)):
    row = db.get(Brand, brand_id)
    if not row:
        raise HTTPException(404, "Brand not found.")
    name = item.name.strip()
    duplicate = db.query(Brand.id).filter(
        func.lower(Brand.name) == name.lower(), Brand.id != brand_id
    ).first()
    if duplicate:
        raise HTTPException(409, "Brand already exists.")
    row.name = name
    _commit_or_conflict(db, "Brand already exists.")
    return {"id": row.id, "name": row.name}


@app.post("/processes", status_code=201)
def add_process(item: ProcessIn, db: Session = Depends(get_db)):
    name = item.name.strip()
    if db.query(Process.id).filter(func.lower(Process.name) == name.lower()).first():
        raise HTTPException(409, "Process already exists.")
    row = Process(name=name)
    db.add(row)
    _commit_or_conflict(db, "Process already exists.")
    db.refresh(row)
    return {"id": row.id, "name": row.name}


@app.get("/processes")
def get_processes(db: Session = Depends(get_db)):
    return [{"id": row.id, "name": row.name} for row in db.query(Process).order_by(Process.name).all()]


@app.patch("/processes/{process_id}")
def update_process(process_id: int, item: ProcessIn, db: Session = Depends(get_db)):
    row = db.get(Process, process_id)
    if not row:
        raise HTTPException(404, "Process not found.")
    name = item.name.strip()
    duplicate = db.query(Process.id).filter(
        func.lower(Process.name) == name.lower(), Process.id != process_id
    ).first()
    if duplicate:
        raise HTTPException(409, "Process already exists.")
    row.name = name
    _commit_or_conflict(db, "Process already exists.")
    return {"id": row.id, "name": row.name}


@app.post("/allocations", status_code=201)
def add_allocation(item: AllocationIn, db: Session = Depends(get_db)):
    if item.role.upper() not in {"PRIMARY", "BACKUP"}:
        raise HTTPException(422, "Role must be PRIMARY or BACKUP.")
    if not db.get(Employee, item.employee_id) or not db.get(Brand, item.brand_id) or not db.get(Process, item.process_id):
        raise HTTPException(404, "Employee, brand or process not found.")
    role = item.role.upper()
    duplicate = db.query(Allocation.id).filter_by(
        employee_id=item.employee_id, brand_id=item.brand_id,
        process_id=item.process_id, role=role,
    ).first()
    if duplicate:
        raise HTTPException(409, "Allocation already exists.")
    row = Allocation(**{**item.model_dump(), "role": role})
    db.add(row)
    _commit_or_conflict(db, "Allocation already exists.")
    db.refresh(row)
    return {"id": row.id, "employee_id": row.employee_id, "brand_id": row.brand_id,
            "process_id": row.process_id, "role": row.role}


@app.get("/allocations")
def get_allocations(db: Session = Depends(get_db)):
    return [{"id": row.id, "employee_id": row.employee_id, "brand_id": row.brand_id,
             "process_id": row.process_id, "role": row.role}
            for row in db.query(Allocation).order_by(Allocation.id).all()]


@app.delete("/allocations/{allocation_id}", status_code=204)
def delete_allocation(allocation_id: int, db: Session = Depends(get_db)):
    row = db.get(Allocation, allocation_id)
    if not row:
        raise HTTPException(404, "Allocation not found.")
    db.delete(row)
    db.commit()


@app.post("/coverage", status_code=201)
def add_coverage(item: CoverageIn, db: Session = Depends(get_db)):
    if item.calendar not in {"DAILY", "MON_SAT", "CUSTOM"}:
        raise HTTPException(422, "Calendar must be DAILY, MON_SAT or CUSTOM.")
    if item.calendar == "CUSTOM":
        try:
            weekdays = {int(value.strip()) for value in item.work_days.split(",") if value.strip()}
        except ValueError as exc:
            raise HTTPException(422, "Custom weekdays must be comma-separated numbers from 0 to 6.") from exc
        if not weekdays or any(day not in range(7) for day in weekdays):
            raise HTTPException(422, "Custom weekdays must be comma-separated numbers from 0 to 6.")
    if not db.get(Brand, item.brand_id) or not db.get(Process, item.process_id):
        raise HTTPException(404, "Brand or process not found.")
    if db.query(CoverageRule.id).filter_by(brand_id=item.brand_id, process_id=item.process_id).first():
        raise HTTPException(409, "Coverage rule already exists.")
    row = CoverageRule(**item.model_dump())
    db.add(row)
    _commit_or_conflict(db, "Coverage rule already exists.")
    db.refresh(row)
    return {"id": row.id, **item.model_dump()}


@app.get("/coverage")
def get_coverage(db: Session = Depends(get_db)):
    return [{"id": row.id, "brand_id": row.brand_id, "process_id": row.process_id,
             "min_staff": row.min_staff, "calendar": row.calendar, "work_days": row.work_days}
            for row in db.query(CoverageRule).order_by(CoverageRule.id).all()]


@app.patch("/coverage/{coverage_id}")
def update_coverage(coverage_id: int, item: CoverageIn, db: Session = Depends(get_db)):
    row = db.get(CoverageRule, coverage_id)
    if not row:
        raise HTTPException(404, "Coverage rule not found.")
    if item.calendar not in {"DAILY", "MON_SAT", "CUSTOM"}:
        raise HTTPException(422, "Calendar must be DAILY, MON_SAT or CUSTOM.")
    if item.calendar == "CUSTOM":
        try:
            weekdays = {int(value.strip()) for value in item.work_days.split(",") if value.strip()}
        except ValueError as exc:
            raise HTTPException(422, "Custom weekdays must be comma-separated numbers from 0 to 6.") from exc
        if not weekdays or any(day not in range(7) for day in weekdays):
            raise HTTPException(422, "Custom weekdays must be comma-separated numbers from 0 to 6.")
    if not db.get(Brand, item.brand_id) or not db.get(Process, item.process_id):
        raise HTTPException(404, "Brand or process not found.")
    duplicate = db.query(CoverageRule.id).filter(
        CoverageRule.brand_id == item.brand_id, CoverageRule.process_id == item.process_id,
        CoverageRule.id != coverage_id,
    ).first()
    if duplicate:
        raise HTTPException(409, "Coverage rule already exists.")
    for key, value in item.model_dump().items():
        setattr(row, key, value)
    _commit_or_conflict(db, "Coverage rule already exists.")
    return {"id": row.id, **item.model_dump()}


@app.post("/time-off", status_code=201)
def add_time_off(item: TimeOffIn, db: Session = Depends(get_db)):
    kind = item.kind.upper()
    if kind not in {"LEAVE", "COMP_OFF", "HOLIDAY"}:
        raise HTTPException(422, "Kind must be LEAVE, COMP_OFF or HOLIDAY.")
    if not db.get(Employee, item.employee_id):
        raise HTTPException(404, "Employee not found.")
    if db.query(TimeOff.id).filter_by(
        employee_id=item.employee_id, day=item.day, kind=kind
    ).first():
        raise HTTPException(409, "Time-off record already exists.")
    row = TimeOff(employee_id=item.employee_id, day=item.day, kind=kind)
    db.add(row)
    _commit_or_conflict(db, "Time-off record already exists.")
    event("availability.created", {"employee_id": row.employee_id, "day": str(row.day), "kind": row.kind})
    return {"id": row.id, "employee_id": row.employee_id, "day": str(row.day), "kind": row.kind}


@app.get("/time-off")
def get_time_off(db: Session = Depends(get_db)):
    return [{"id": row.id, "employee_id": row.employee_id, "day": str(row.day), "kind": row.kind}
            for row in db.query(TimeOff).order_by(TimeOff.day, TimeOff.employee_id).all()]


@app.delete("/time-off/{time_off_id}", status_code=204)
def delete_time_off(time_off_id: int, db: Session = Depends(get_db)):
    row = db.get(TimeOff, time_off_id)
    if not row:
        raise HTTPException(404, "Time-off record not found.")
    db.delete(row)
    db.commit()


@app.post("/holidays", status_code=201)
def add_holiday(item: HolidayIn, db: Session = Depends(get_db)):
    if db.query(CompanyHoliday.id).filter_by(day=item.day).first():
        raise HTTPException(409, "Company holiday already exists for this date.")
    row = CompanyHoliday(**item.model_dump())
    db.add(row)
    _commit_or_conflict(db, "Company holiday already exists for this date.")
    db.refresh(row)
    return {"id": row.id, "day": str(row.day), "description": row.description,
            "full_closure": row.full_closure}


@app.get("/holidays")
def get_holidays(db: Session = Depends(get_db)):
    return [{"id": row.id, "day": str(row.day), "description": row.description,
             "full_closure": row.full_closure}
            for row in db.query(CompanyHoliday).order_by(CompanyHoliday.day).all()]


@app.post("/rosters/generate")
def generate(item: GenerateIn, db: Session = Depends(get_db)):
    try:
        roster, info = build_roster(
            db, item.start, item.end, item.time_limit,
            allow_incomplete=item.allow_incomplete,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if roster is None:
        raise HTTPException(409, {"solver_status": info["solver_status"], "message": "No roster solution was found."})
    event("roster.generated", {"roster_id": roster.id, **info})
    return {"roster_id": roster.id, "status": roster.status, **info}


@app.get("/rosters")
def get_rosters(db: Session = Depends(get_db)):
    return [{"id": row.id, "start": str(row.start), "end": str(row.end), "status": row.status,
             "version": row.version}
            for row in db.query(Roster).order_by(Roster.id.desc()).all()]


@app.get("/rosters/{roster_id}")
def roster_detail(roster_id: int, db: Session = Depends(get_db)):
    roster = db.get(Roster, roster_id)
    if not roster:
        raise HTTPException(404, "Roster not found.")
    return {
        "id": roster.id, "start": str(roster.start), "end": str(roster.end),
        "status": roster.status, "version": roster.version,
        "entries": _roster_entries(db, roster_id, None, None, None, None, None, None),
    }


@app.get("/rosters/{roster_id}/validate")
def check(roster_id: int, db: Session = Depends(get_db)):
    try:
        return validate_roster(db, roster_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/rosters/{roster_id}/publish")
def publish(roster_id: int, db: Session = Depends(get_db)):
    roster = db.get(Roster, roster_id)
    if not roster:
        raise HTTPException(404, "Roster not found.")
    if roster.status == "PUBLISHED":
        raise HTTPException(409, "Roster is already published.")
    result = validate_roster(db, roster_id)
    if not result["valid"]:
        raise HTTPException(409, {"message": "Roster has validation errors.", "errors": result["errors"]})
    roster.status = "PUBLISHED"
    roster.published_at = datetime.now()
    db.commit()
    event("roster.published", {"roster_id": roster_id})
    return {"roster_id": roster_id, "status": roster.status}


@app.post("/rosters/{roster_id}/regenerate")
def regenerate(
    roster_id: int, time_limit: int = Query(default=10, ge=1, le=60),
    allow_incomplete: bool = False, db: Session = Depends(get_db),
):
    roster = db.get(Roster, roster_id)
    if not roster:
        raise HTTPException(404, "Roster not found.")
    if roster.status == "PUBLISHED":
        raise HTTPException(409, "Published rosters cannot be regenerated.")
    try:
        updated, info = build_roster(
            db, roster.start, roster.end, time_limit, allow_incomplete,
            existing_roster_id=roster_id,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    if updated is None:
        raise HTTPException(409, {"solver_status": info["solver_status"], "message": "No roster solution was found."})
    return {"roster_id": updated.id, "status": updated.status, **info}


@app.patch("/rosters/{roster_id}/entries/{entry_id}")
def edit_roster_entry(roster_id: int, entry_id: int, item: EntryPatch, db: Session = Depends(get_db)):
    roster = db.get(Roster, roster_id)
    entry = db.get(RosterEntry, entry_id)
    if not roster or not entry or entry.roster_id != roster_id:
        raise HTTPException(404, "Roster entry not found.")
    if roster.status == "PUBLISHED":
        raise HTTPException(409, "Published rosters cannot be edited.")
    if entry.locked:
        raise HTTPException(409, "Unlock the roster entry before editing it.")
    old_validation = validate_roster(db, roster_id)
    values = item.model_dump(exclude_unset=True)
    if "role" in values:
        values["role"] = values["role"].upper()
        if values["role"] != "PRIMARY":
            raise HTTPException(422, "Roster entries must be PRIMARY assignments; BACKUP is an allocation role.")
    for key, value in values.items():
        if value is not None:
            setattr(entry, key, value)
    if not roster.start <= entry.day <= roster.end:
        db.rollback()
        raise HTTPException(422, "Entry date must be within the roster range.")
    allocation = db.query(Allocation.id).filter_by(
        employee_id=entry.employee_id, brand_id=entry.brand_id,
        process_id=entry.process_id, role=entry.role,
    ).first()
    if not allocation:
        db.rollback()
        raise HTTPException(409, "Employee is not eligible for this assignment role.")
    new_validation = validate_roster(db, roster_id)
    new_errors = _new_issues(old_validation["errors"], new_validation["errors"])
    if new_errors:
        db.rollback()
        raise HTTPException(409, {"message": "Edit introduces roster conflicts.", "errors": new_errors})
    entry.source = "MANUAL"
    entry.updated_by = "manager"
    db.commit()
    return {"entry_id": entry.id, "roster_id": roster_id, "validation": validate_roster(db, roster_id)}


def _set_entry_lock(roster_id: int, entry_id: int, locked: bool, db: Session):
    roster = db.get(Roster, roster_id)
    entry = db.get(RosterEntry, entry_id)
    if not roster or not entry or entry.roster_id != roster_id:
        raise HTTPException(404, "Roster entry not found.")
    if roster.status == "PUBLISHED":
        raise HTTPException(409, "Published roster entries cannot be changed.")
    entry.locked = locked
    entry.updated_by = "manager"
    db.commit()
    return {"entry_id": entry.id, "locked": entry.locked}


@app.post("/rosters/{roster_id}/entries/{entry_id}/lock")
def lock_roster_entry(roster_id: int, entry_id: int, db: Session = Depends(get_db)):
    return _set_entry_lock(roster_id, entry_id, True, db)


@app.post("/rosters/{roster_id}/entries/{entry_id}/unlock")
def unlock_roster_entry(roster_id: int, entry_id: int, db: Session = Depends(get_db)):
    return _set_entry_lock(roster_id, entry_id, False, db)


@app.get("/rosters/{roster_id}/backup-suggestions")
def backup_suggestions(roster_id: int, db: Session = Depends(get_db)):
    try:
        return {"roster_id": roster_id, "suggestions": suggest_backups(db, roster_id)}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/rosters/{roster_id}/backup-suggestions/accept")
def accept_backup_suggestion(roster_id: int, item: BackupAcceptIn, db: Session = Depends(get_db)):
    roster = db.get(Roster, roster_id)
    if not roster:
        raise HTTPException(404, "Roster not found.")
    if roster.status == "PUBLISHED":
        raise HTTPException(409, "Published rosters cannot be changed.")
    suggestion = {
        "employee_id": item.employee_id, "brand_id": item.brand_id,
        "process_id": item.process_id, "day": str(item.day),
    }
    if suggestion not in [
        {key: value for key, value in row.items() if key in suggestion}
        for row in suggest_backups(db, roster_id)
    ]:
        raise HTTPException(409, "Backup suggestion is no longer available.")
    row = RosterEntry(
        roster_id=roster_id, employee_id=item.employee_id, brand_id=item.brand_id,
        process_id=item.process_id, day=item.day, role="PRIMARY",
        source="BACKUP_SUGGESTION", status="WORK",
    )
    db.add(row)
    try:
        db.flush()
        validation = validate_roster(db, roster_id)
        baseline = validate_roster(db, roster_id, [
            entry for entry in db.query(RosterEntry).filter_by(roster_id=roster_id).all()
            if entry.id != row.id
        ])
        if _new_issues(baseline["errors"], validation["errors"]):
            db.rollback()
            raise HTTPException(409, "Backup suggestion violates a hard roster constraint.")
        db.commit()
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(409, "Backup assignment conflicts with an existing entry.") from exc
    return {"entry_id": row.id, "validation": validate_roster(db, roster_id)}


@app.get("/rosters/{roster_id}/views/{view_type}")
def roster_view(
    roster_id: int, view_type: str, date_from: date | None = None, date_to: date | None = None,
    brand_id: int | None = None, process_id: int | None = None,
    pod: str | None = None, employee_id: int | None = None,
    db: Session = Depends(get_db),
):
    if not db.get(Roster, roster_id):
        raise HTTPException(404, "Roster not found.")
    if view_type not in {"complete", "brand", "process", "pod", "employee"}:
        raise HTTPException(422, "View must be complete, brand, process, pod or employee.")
    entries = _roster_entries(db, roster_id, date_from, date_to, brand_id, process_id, pod, employee_id)
    group_key = {
        "complete": None, "brand": "brand", "process": "process",
        "pod": "pod", "employee": "employee",
    }[view_type]
    if group_key is None:
        return {"roster_id": roster_id, "view": view_type, "entries": entries}
    groups = {}
    for entry in entries:
        groups.setdefault(entry[group_key], []).append(entry)
    return {"roster_id": roster_id, "view": view_type, "groups": groups}


@app.get("/rosters/{roster_id}/export")
def export_roster(
    roster_id: int, format: str = Query(default="csv", pattern="^(csv|xlsx)$"),
    date_from: date | None = None, date_to: date | None = None, brand_id: int | None = None,
    process_id: int | None = None, pod: str | None = None,
    employee_id: int | None = None, db: Session = Depends(get_db),
):
    if not db.get(Roster, roster_id):
        raise HTTPException(404, "Roster not found.")
    entries = _roster_entries(db, roster_id, date_from, date_to, brand_id, process_id, pod, employee_id)
    columns = ["employee", "brand", "process", "date", "role", "source"]
    dataframe = pd.DataFrame(
        entries,
        columns=["entry_id", "employee_id", "employee", "brand_id", "brand", "process_id",
                 "process", "pod", "day", "role", "source", "status", "locked"],
    ).rename(columns={"day": "date"})[columns]
    output = BytesIO()
    if format == "xlsx":
        dataframe.to_excel(output, index=False, engine="openpyxl")
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"roster-{roster_id}.xlsx"
    else:
        output.write(dataframe.to_csv(index=False).encode("utf-8"))
        media_type = "text/csv"
        filename = f"roster-{roster_id}.csv"
    output.seek(0)
    return StreamingResponse(
        output, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/imports/employees")
async def upload_employees(file: UploadFile = File(...), db: Session = Depends(get_db)):
    try:
        return import_employees(db, await file.read(), file.filename or "employees.csv")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(409, "Employee import failed; no rows were committed.") from exc


@app.post("/imports/allocations")
async def upload_allocations(file: UploadFile = File(...), db: Session = Depends(get_db)):
    try:
        return import_allocations(db, await file.read(), file.filename or "allocations.csv")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(409, "Allocation import failed; no rows were committed.") from exc
