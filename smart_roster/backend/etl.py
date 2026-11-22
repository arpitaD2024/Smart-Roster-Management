from io import BytesIO
import re

import pandas as pd
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .models import Allocation, Brand, Employee, ImportLog, Process


def read_table(raw: bytes, name: str, dtype: dict | None = None):
    if name.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(BytesIO(raw), dtype=dtype)
    return pd.read_csv(BytesIO(raw), dtype=dtype)


def _blank(value) -> bool:
    return pd.isna(value) or not str(value).strip()


def _integer(value, default: int) -> int:
    if _blank(value):
        return default
    numeric = float(value)
    if not numeric.is_integer():
        raise ValueError
    return int(numeric)


def import_employees(db: Session, raw: bytes, name: str):
    df = read_table(raw, name, dtype={"email": "string", "employee_id": "string"})
    df.columns = [str(column).strip().lower().replace(" ", "_") for column in df.columns]
    required = {"name", "email", "employee_id"}
    if not required.issubset(df.columns):
        raise ValueError("Employee file must contain name, email and employee_id columns.")

    existing_emails = {
        row[0].strip().lower() for row in db.query(Employee.email).all() if row[0]
    }
    existing_ids = {
        row[0].strip().casefold() for row in db.query(Employee.employee_id).all() if row[0]
    }
    seen_emails, seen_ids = set(), set()
    accepted_rows, errors = [], []
    for idx, row in df.iterrows():
        line = idx + 2
        name_value = "" if _blank(row.get("name")) else str(row.get("name")).strip()
        email = "" if _blank(row.get("email")) else str(row.get("email")).strip().lower()
        employee_code = "" if _blank(row.get("employee_id")) else str(row.get("employee_id")).strip()
        problems = []
        if not name_value:
            problems.append("missing name")
        if not email:
            problems.append("missing email")
        elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            problems.append("invalid email")
        if not employee_code:
            problems.append("missing employee_id")
        if email and (email in existing_emails or email in seen_emails):
            problems.append("duplicate email")
        if employee_code and (employee_code.casefold() in existing_ids or employee_code.casefold() in seen_ids):
            problems.append("duplicate employee_id")
        try:
            max_days = _integer(row.get("max_days"), 6)
            max_work_days = _integer(row.get("max_work_days"), 6)
            min_offs = _integer(row.get("min_offs"), 1)
            if not 1 <= max_days <= 14:
                problems.append("max_days must be between 1 and 14")
            if not 1 <= max_work_days <= 7:
                problems.append("max_work_days must be between 1 and 7")
            if not 0 <= min_offs <= 7:
                problems.append("min_offs must be between 0 and 7")
        except (TypeError, ValueError):
            problems.append("max_days, max_work_days and min_offs must be whole numbers")
            max_days, max_work_days, min_offs = 6, 6, 1
        if problems:
            errors.append(f"Row {line}: {', '.join(problems)}")
            continue

        seen_emails.add(email)
        seen_ids.add(employee_code.casefold())
        pod_value = row.get("pod", "General")
        pod = "General" if _blank(pod_value) else str(pod_value).strip()
        title_value = row.get("title")
        title = None if _blank(title_value) else str(title_value).strip()
        accepted_rows.append(Employee(
            name=name_value, email=email, employee_id=employee_code, title=title,
            pod=pod, max_days=max_days, max_work_days=max_work_days, min_offs=min_offs,
        ))

    rejected = len(df.index) - len(accepted_rows)
    try:
        db.add_all(accepted_rows)
        db.add(ImportLog(
            file_name=name, accepted=len(accepted_rows), rejected=rejected,
            details="; ".join(errors),
        ))
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise
    return {"accepted": len(accepted_rows), "rejected": rejected, "errors": errors}


def import_allocations(db: Session, raw: bytes, name: str):
    df = read_table(raw, name)
    df.columns = [str(column).strip().lower().replace(" ", "_") for column in df.columns]
    needed = {"employee_id", "brand", "process"}
    if not needed.issubset(df.columns):
        raise ValueError("Allocation file needs employee_id, brand and process columns.")

    accepted_rows, errors = [], []
    seen = set()
    for idx, row in df.iterrows():
        try:
            employee_id = _integer(row.get("employee_id"), 0)
        except (TypeError, ValueError):
            employee_id = 0
        employee = db.get(Employee, employee_id) if employee_id else None
        brand_name = "" if _blank(row.get("brand")) else str(row.get("brand")).strip()
        process_name = "" if _blank(row.get("process")) else str(row.get("process")).strip()
        brand = db.query(Brand).filter_by(name=brand_name).first() if brand_name else None
        process = db.query(Process).filter_by(name=process_name).first() if process_name else None
        role = "PRIMARY" if _blank(row.get("role")) else str(row.get("role")).strip().upper()
        issues = []
        if not employee or not brand or not process:
            issues.append("unknown employee, brand or process")
        if role not in {"PRIMARY", "BACKUP"}:
            issues.append("role must be PRIMARY or BACKUP")
        key = (employee.id, brand.id, process.id, role) if employee and brand and process else None
        if key and (key in seen or db.query(Allocation.id).filter_by(
            employee_id=employee.id, brand_id=brand.id, process_id=process.id, role=role
        ).first()):
            issues.append("duplicate allocation")
        if issues:
            errors.append(f"Row {idx + 2}: {', '.join(issues)}")
            continue
        seen.add(key)
        accepted_rows.append(Allocation(
            employee_id=employee.id, brand_id=brand.id, process_id=process.id, role=role,
        ))

    rejected = len(df.index) - len(accepted_rows)
    try:
        db.add_all(accepted_rows)
        db.add(ImportLog(
            file_name=name, accepted=len(accepted_rows), rejected=rejected,
            details="; ".join(errors),
        ))
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise
    return {"accepted": len(accepted_rows), "rejected": rejected, "errors": errors}
