from datetime import date, timedelta
from io import BytesIO

import pandas as pd
from smart_roster.backend.models import Roster, RosterEntry
from smart_roster.backend.scheduler import validate_roster


WEEK_START = date(2026, 11, 23)


def create_setup(client, employee_count=2, process_count=1, weekly_max=6):
    employees = []
    for index in range(employee_count):
        response = client.post("/employees", json={
            "name": f"Employee {index}", "email": f"employee{index}@example.com",
            "employee_id": f"EMP-{index}", "pod": "Pod A", "max_days": 6,
            "max_work_days": weekly_max, "min_offs": 1,
        })
        assert response.status_code == 201
        employees.append(response.json())
    brand = client.post("/brands", json={"name": "Brand A"}).json()
    processes = [
        client.post("/processes", json={"name": f"Process {index}"}).json()
        for index in range(process_count)
    ]
    for employee in employees:
        for process in processes:
            assert client.post("/allocations", json={
                "employee_id": employee["id"], "brand_id": brand["id"],
                "process_id": process["id"], "role": "PRIMARY",
            }).status_code == 201
    for process in processes:
        assert client.post("/coverage", json={
            "brand_id": brand["id"], "process_id": process["id"],
            "min_staff": 1, "calendar": "DAILY",
        }).status_code == 201
    return employees, brand, processes


def generate(client, start=WEEK_START, days=7, allow_incomplete=False):
    return client.post("/rosters/generate", json={
        "start": str(start), "end": str(start + timedelta(days=days - 1)),
        "time_limit": 5, "allow_incomplete": allow_incomplete,
    })


def test_health_checks_database_and_allows_local_streamlit_origin(client):
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "database": "connected"}

    preflight = client.options("/health", headers={
        "Origin": "http://localhost:8501",
        "Access-Control-Request-Method": "GET",
    })
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:8501"


def test_employee_creation_duplicate_and_deactivation(client):
    payload = {
        "name": "Asha", "email": "Asha@example.com", "employee_id": "EMP-1",
        "title": None, "pod": "Pod A",
    }
    created = client.post("/employees", json=payload)
    assert created.status_code == 201
    assert created.json()["email"] == "asha@example.com"
    assert created.json()["employee_id"] == "EMP-1"
    assert client.post("/employees", json={**payload, "email": "asha@example.com"}).status_code == 409
    assert client.post("/employees", json={**payload, "email": "other@example.com"}).status_code == 409
    assert client.patch(f'/employees/{created.json()["id"]}', json={"active": False}).json()["active"] is False
    assert client.get(f'/employees/{created.json()["id"]}').status_code == 200


def test_setup_crud_endpoints(client):
    employee = client.post("/employees", json={
        "name": "Asha", "email": "asha@example.com", "employee_id": "E-1",
    }).json()
    brand = client.post("/brands", json={"name": "Brand A"}).json()
    process = client.post("/processes", json={"name": "Process A"}).json()
    assert client.patch(f'/employees/{employee["id"]}', json={"name": "Asha Updated"}).json()["name"] == "Asha Updated"
    assert client.patch(f'/brands/{brand["id"]}', json={"name": "Brand Updated"}).status_code == 200
    assert client.patch(f'/processes/{process["id"]}', json={"name": "Process Updated"}).status_code == 200

    allocation = client.post("/allocations", json={
        "employee_id": employee["id"], "brand_id": brand["id"],
        "process_id": process["id"], "role": "PRIMARY",
    }).json()
    assert client.get("/allocations").json()[0]["id"] == allocation["id"]
    assert client.delete(f'/allocations/{allocation["id"]}').status_code == 204
    assert client.get("/allocations").json() == []

    coverage = client.post("/coverage", json={
        "brand_id": brand["id"], "process_id": process["id"], "min_staff": 1,
    }).json()
    assert client.get("/coverage").json()[0]["min_staff"] == 1
    updated = client.patch(f'/coverage/{coverage["id"]}', json={
        "brand_id": brand["id"], "process_id": process["id"], "min_staff": 2,
        "calendar": "DAILY", "work_days": "0,1,2,3,4,5",
    })
    assert updated.json()["min_staff"] == 2

    time_off = client.post("/time-off", json={
        "employee_id": employee["id"], "day": str(WEEK_START), "kind": "COMP_OFF",
    }).json()
    assert client.get("/time-off").json()[0]["id"] == time_off["id"]
    assert client.delete(f'/time-off/{time_off["id"]}').status_code == 204
    assert client.get("/time-off").json() == []


def test_employee_csv_import_validates_and_counts_rows(client):
    content = (
        "name,email,employee_id,pod\n"
        "Asha,asha@example.com,E-1,Pod A\n"
        "Duplicate,ASHA@example.com,E-2,Pod A\n"
        "Missing Email,,E-3,Pod B\n"
        "Invalid,not-an-email,E-4,Pod B\n"
        "Duplicate ID,other@example.com,E-1,Pod B\n"
        "Leading Zero ID,zero@example.com,0007,Pod C\n"
    )
    response = client.post(
        "/imports/employees",
        files={"file": ("employees.csv", content.encode(), "text/csv")},
    )
    assert response.status_code == 200
    assert response.json()["accepted"] == 2
    assert response.json()["rejected"] == 4
    imported = client.get("/employees").json()
    assert len(imported) == 2
    assert next(row for row in imported if row["name"] == "Leading Zero ID")["employee_id"] == "0007"


def test_employee_and_allocation_xlsx_imports(client):
    employee_buffer = BytesIO()
    pd.DataFrame([{
        "name": "Excel Employee", "email": "excel@example.com",
        "employee_id": "XLS-01", "pod": "Pod X",
    }]).to_excel(employee_buffer, index=False)
    employee_import = client.post(
        "/imports/employees",
        files={"file": ("employees.xlsx", employee_buffer.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert employee_import.status_code == 200
    assert employee_import.json()["accepted"] == 1
    employee = client.get("/employees").json()[0]

    brand = client.post("/brands", json={"name": "Excel Brand"}).json()
    process = client.post("/processes", json={"name": "Excel Process"}).json()
    allocation_buffer = BytesIO()
    pd.DataFrame([
        {"employee_id": employee["id"], "brand": brand["name"],
         "process": process["name"], "role": "BACKUP"},
        {"employee_id": employee["id"], "brand": brand["name"],
         "process": process["name"], "role": "BACKUP"},
        {"employee_id": employee["id"], "brand": brand["name"],
         "process": process["name"], "role": "INVALID"},
        {"employee_id": 9999, "brand": brand["name"],
         "process": process["name"], "role": "PRIMARY"},
    ]).to_excel(allocation_buffer, index=False)
    allocation_import = client.post(
        "/imports/allocations",
        files={"file": ("allocations.xlsx", allocation_buffer.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert allocation_import.status_code == 200
    assert allocation_import.json()["accepted"] == 1
    assert allocation_import.json()["rejected"] == 3
    assert client.get("/allocations").json()[0]["role"] == "BACKUP"


def test_basic_generation_multi_process_and_views(client):
    _, _, processes = create_setup(client, employee_count=4, process_count=2)
    response = generate(client)
    assert response.status_code == 200
    result = response.json()
    assert result["solver_status"] in {"OPTIMAL", "FEASIBLE"}
    assert result["valid"] is True
    assert len(client.get(f'/rosters/{result["roster_id"]}/views/complete').json()["entries"]) >= 14
    process_view = client.get(f'/rosters/{result["roster_id"]}/views/process').json()
    assert set(process_view["groups"]) == {"Process 0", "Process 1"}
    filtered = client.get(
        f'/rosters/{result["roster_id"]}/views/complete',
        params={"process_id": processes[0]["id"], "date_from": str(WEEK_START)},
    ).json()["entries"]
    assert filtered and all(row["process_id"] == processes[0]["id"] for row in filtered)


def test_leave_and_full_closure_holiday_are_respected(client):
    employees, brand, processes = create_setup(client)
    leave_day = WEEK_START + timedelta(days=2)
    assert client.post("/time-off", json={
        "employee_id": employees[0]["id"], "day": str(leave_day), "kind": "LEAVE",
    }).status_code == 201
    closure_day = WEEK_START + timedelta(days=4)
    holiday_response = client.post("/holidays", json={
        "day": str(closure_day), "description": "Company shutdown", "full_closure": True,
    })
    assert holiday_response.status_code == 201
    assert client.get("/holidays").json()[0]["full_closure"] is True
    result = generate(client).json()
    entries = client.get(f'/rosters/{result["roster_id"]}').json()["entries"]
    assert all(not (row["employee_id"] == employees[0]["id"] and row["day"] == str(leave_day)) for row in entries)
    assert all(row["day"] != str(closure_day) for row in entries)
    assert client.get(f'/rosters/{result["roster_id"]}/validate').json()["valid"] is True


def test_nonclosure_holiday_does_not_count_as_weekly_off(client, db_session):
    employees, brand, processes = create_setup(client, employee_count=2)
    holiday_day = WEEK_START + timedelta(days=3)
    assert client.post("/holidays", json={
        "day": str(holiday_day), "description": "Optional workday", "full_closure": False,
    }).status_code == 201
    roster = Roster(start=WEEK_START, end=WEEK_START + timedelta(days=6), status="DRAFT")
    db_session.add(roster)
    db_session.flush()
    for offset in range(7):
        day = WEEK_START + timedelta(days=offset)
        if day != holiday_day:
            db_session.add(RosterEntry(
                roster_id=roster.id, employee_id=employees[0]["id"], brand_id=brand["id"],
                process_id=processes[0]["id"], day=day,
                role="PRIMARY", status="WORK", source="MANUAL",
            ))
    db_session.commit()
    result = validate_roster(db_session, roster.id)
    assert any(
        issue["type"] == "MINIMUM_WEEKLY_OFFS"
        and issue["employee_id"] == employees[0]["id"]
        for issue in result["errors"]
    )


def test_weekly_off_entitlement_crosses_month_boundary(client):
    create_setup(client, employee_count=2)
    start = date(2026, 11, 30)
    result = generate(client, start=start, days=7).json()
    roster = client.get(f'/rosters/{result["roster_id"]}').json()
    assert result["valid"] is True
    assert len(roster["entries"]) >= 7
    for employee in client.get("/employees").json():
        worked = {row["day"] for row in roster["entries"] if row["employee_id"] == employee["id"]}
        assert len(worked) <= 6
    assert client.get(f'/rosters/{result["roster_id"]}/validate').json()["valid"] is True


def test_insufficient_staffing_requires_explicit_incomplete_draft(client):
    create_setup(client, employee_count=1)
    strict = generate(client)
    assert strict.status_code == 409
    assert strict.json()["detail"]["solver_status"] == "INFEASIBLE"
    draft = generate(client, allow_incomplete=True)
    assert draft.status_code == 200
    result = draft.json()
    assert result["coverage_gaps"] > 0
    assert result["valid"] is False
    assert client.get(f'/rosters/{result["roster_id"]}/validate').json()["valid"] is False
    assert client.post(f'/rosters/{result["roster_id"]}/publish').status_code == 409


def test_backup_suggestions_are_eligible_and_revalidated(client):
    employees, brand, processes = create_setup(client, employee_count=2)
    backup = employees[1]
    allocation = client.get("/allocations").json()[-1]
    assert client.delete(f'/allocations/{allocation["id"]}').status_code == 204
    assert client.post("/allocations", json={
        "employee_id": backup["id"], "brand_id": brand["id"],
        "process_id": processes[0]["id"], "role": "BACKUP",
    }).status_code == 201
    result = generate(client, allow_incomplete=True).json()
    suggestions = client.get(f'/rosters/{result["roster_id"]}/backup-suggestions').json()["suggestions"]
    assert suggestions
    suggestion = suggestions[0]
    accepted = client.post(
        f'/rosters/{result["roster_id"]}/backup-suggestions/accept',
        json={key: suggestion[key] for key in ("employee_id", "brand_id", "process_id", "day")},
    )
    assert accepted.status_code == 200
    assert accepted.json()["validation"]["valid"] is True


def test_backup_suggestions_do_not_overload_backup_employee(client):
    employees, brand, processes = create_setup(client, employee_count=2, weekly_max=6)
    backup = employees[1]
    allocation = client.get("/allocations").json()[-1]
    client.delete(f'/allocations/{allocation["id"]}')
    client.patch(f'/employees/{backup["id"]}', json={"max_work_days": 1})
    client.post("/allocations", json={
        "employee_id": backup["id"], "brand_id": brand["id"],
        "process_id": processes[0]["id"], "role": "BACKUP",
    })
    roster = generate(client, allow_incomplete=True).json()
    suggestions = client.get(f'/rosters/{roster["roster_id"]}/backup-suggestions').json()["suggestions"]
    assert len(suggestions) <= 1


def test_manual_edit_conflict_lock_regeneration_and_publish(client):
    create_setup(client, employee_count=4, process_count=2)
    result = generate(client).json()
    roster_id = result["roster_id"]
    entries = client.get(f"/rosters/{roster_id}").json()["entries"]
    first = entries[0]
    same_day_different_employee = next(
        row for row in entries
        if row["day"] == first["day"] and row["employee_id"] != first["employee_id"]
    )
    conflict = client.patch(
        f'/rosters/{roster_id}/entries/{first["entry_id"]}',
        json={"employee_id": same_day_different_employee["employee_id"]},
    )
    assert conflict.status_code == 409

    assigned_on_day = {row["employee_id"] for row in entries if row["day"] == first["day"]}
    spare_employee = next(
        row for row in client.get("/employees").json() if row["id"] not in assigned_on_day
    )
    edited = client.patch(
        f'/rosters/{roster_id}/entries/{first["entry_id"]}',
        json={"employee_id": spare_employee["id"]},
    )
    assert edited.status_code == 200
    assert any(issue["type"] == "ROSTER_NOT_PUBLISHED" for issue in edited.json()["validation"]["warnings"])
    first["employee_id"] = spare_employee["id"]

    locked = client.post(f'/rosters/{roster_id}/entries/{first["entry_id"]}/lock')
    assert locked.json()["locked"] is True
    assert client.post(f'/rosters/{roster_id}/regenerate').status_code == 200
    after = client.get(f"/rosters/{roster_id}").json()["entries"]
    assert any(row["entry_id"] == first["entry_id"] and row["locked"] for row in after)
    assert client.post(f"/rosters/{roster_id}/entries/{first['entry_id']}/unlock").json()["locked"] is False
    assert client.post(f"/rosters/{roster_id}/publish").status_code == 200
    assert client.post(f"/rosters/{roster_id}/regenerate").status_code == 409


def test_validator_flags_maximum_consecutive_days(client, db_session):
    employees, brand, processes = create_setup(client, employee_count=2, process_count=2)
    roster = Roster(start=WEEK_START, end=WEEK_START + timedelta(days=6), status="DRAFT")
    db_session.add(roster)
    db_session.flush()
    employee = employees[0]
    for offset in range(7):
        db_session.add(RosterEntry(
            roster_id=roster.id, employee_id=employee["id"], brand_id=brand["id"],
            process_id=processes[0]["id"], day=WEEK_START + timedelta(days=offset),
            role="PRIMARY", status="WORK", source="MANUAL",
        ))
    db_session.commit()
    result = validate_roster(db_session, roster.id)
    error_types = {issue["type"] for issue in result["errors"]}
    assert "MAXIMUM_CONSECUTIVE_DAYS" in error_types


def test_validator_flags_double_booking(client, db_session):
    employees, brand, processes = create_setup(client, employee_count=2, process_count=2)
    roster = Roster(start=WEEK_START, end=WEEK_START + timedelta(days=6), status="DRAFT")
    db_session.add(roster)
    db_session.flush()
    for process in processes:
        db_session.add(RosterEntry(
            roster_id=roster.id, employee_id=employees[0]["id"], brand_id=brand["id"],
            process_id=process["id"], day=WEEK_START,
            role="PRIMARY", status="WORK", source="MANUAL",
        ))
    db_session.commit()
    result = validate_roster(db_session, roster.id)
    assert "DOUBLE_BOOKING" in {issue["type"] for issue in result["errors"]}


def test_validator_flags_maximum_weekly_work_days(client, db_session):
    employees, brand, processes = create_setup(client, employee_count=2, weekly_max=3)
    roster = Roster(start=WEEK_START, end=WEEK_START + timedelta(days=6), status="DRAFT")
    db_session.add(roster)
    db_session.flush()
    for offset in range(4):
        db_session.add(RosterEntry(
            roster_id=roster.id, employee_id=employees[0]["id"], brand_id=brand["id"],
            process_id=processes[0]["id"], day=WEEK_START + timedelta(days=offset),
            role="PRIMARY", status="WORK", source="MANUAL",
        ))
    db_session.commit()
    result = validate_roster(db_session, roster.id)
    assert any(
        issue["type"] == "MAXIMUM_WORK_DAYS" and issue["employee_id"] == employees[0]["id"]
        for issue in result["errors"]
    )


def test_csv_and_excel_export(client):
    create_setup(client, employee_count=2)
    roster_id = generate(client).json()["roster_id"]
    csv_response = client.get(f"/rosters/{roster_id}/export?format=csv")
    assert csv_response.status_code == 200
    assert "employee,brand,process,date,role,source" in csv_response.text
    excel_response = client.get(f"/rosters/{roster_id}/export?format=xlsx")
    assert excel_response.status_code == 200
    exported = pd.read_excel(BytesIO(excel_response.content))
    assert list(exported.columns) == ["employee", "brand", "process", "date", "role", "source"]
    assert len(exported) >= 7


def test_end_to_end_roster_workflow(client):
    employees, brand, processes = create_setup(client, employee_count=3)
    backup = employees[-1]
    primary_allocation = client.get("/allocations").json()[-1]
    assert client.delete(f'/allocations/{primary_allocation["id"]}').status_code == 204
    assert client.post("/allocations", json={
        "employee_id": backup["id"], "brand_id": brand["id"],
        "process_id": processes[0]["id"], "role": "BACKUP",
    }).status_code == 201
    leave_day = WEEK_START
    for employee in employees[:2]:
        assert client.post("/time-off", json={
            "employee_id": employee["id"], "day": str(leave_day), "kind": "LEAVE",
        }).status_code == 201

    generated = generate(client, allow_incomplete=True)
    assert generated.status_code == 200
    roster_id = generated.json()["roster_id"]
    assert client.get(f"/rosters/{roster_id}/validate").status_code == 200
    suggestions = client.get(f"/rosters/{roster_id}/backup-suggestions").json()["suggestions"]
    assert suggestions
    suggestion = next(item for item in suggestions if item["day"] == str(leave_day))
    accepted = client.post(
        f"/rosters/{roster_id}/backup-suggestions/accept",
        json={key: suggestion[key] for key in ("employee_id", "brand_id", "process_id", "day")},
    )
    assert accepted.status_code == 200
    assert accepted.json()["validation"]["valid"] is True
    backup_entry_id = accepted.json()["entry_id"]
    assert client.post(f"/rosters/{roster_id}/entries/{backup_entry_id}/lock").status_code == 200

    entries = client.get(f"/rosters/{roster_id}").json()["entries"]
    manual = next(row for row in entries if row["source"] == "SOLVER")
    edited = client.patch(f'/rosters/{roster_id}/entries/{manual["entry_id"]}', json={
        "employee_id": manual["employee_id"], "brand_id": manual["brand_id"],
        "process_id": manual["process_id"], "day": manual["day"],
    })
    assert edited.status_code == 200
    assert client.post(f'/rosters/{roster_id}/entries/{manual["entry_id"]}/lock').status_code == 200
    assert client.post(f"/rosters/{roster_id}/regenerate").status_code == 200
    assert client.get(f"/rosters/{roster_id}/validate").json()["valid"] is True
    assert client.post(f"/rosters/{roster_id}/publish").status_code == 200
    assert client.get(f"/rosters/{roster_id}/export?format=csv").status_code == 200
