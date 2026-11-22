import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

st.set_page_config(page_title="Smart Roster", page_icon="🗓️", layout="wide")
load_dotenv(Path(__file__).resolve().parent / ".env")
api_url = os.getenv("API_URL")
if not api_url:
    try:
        api_url = st.secrets.get("API_URL", "http://localhost:8000")
    except FileNotFoundError:
        api_url = "http://localhost:8000"
API = api_url.strip().rstrip("/")
st.title("Smart Roster Management")
st.caption("Set up your team, plan coverage, generate schedules and review roster quality.")

def api(method, path, **kwargs):
    try:
        response = requests.request(method, API + path, timeout=30, **kwargs)
        if response.status_code >= 400:
            try:
                message = response.json()
            except ValueError:
                message = response.text
            st.error(f"Could not {method} {path}: {message}")
            return None
        return response.json() if response.content else {}
    except requests.RequestException as exc:
        st.error(f"Cannot reach the API at {API}: {exc}")
        return None


def saved(message):
    st.success(message)


def show_empty(message):
    st.info(message)


def display_frame(rows, empty_message):
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        show_empty(empty_message)


def roster_filters(prefix, brands, processes, employees):
    columns = st.columns(5)
    date_from = columns[0].date_input("From date", value=None, key=f"{prefix}_from")
    date_to = columns[1].date_input("To date", value=None, key=f"{prefix}_to")
    brand = columns[2].selectbox(
        "Brand", [None] + brands, format_func=lambda value: "All brands" if value is None else value["name"],
        key=f"{prefix}_brand",
    )
    process = columns[3].selectbox(
        "Process", [None] + processes,
        format_func=lambda value: "All processes" if value is None else value["name"],
        key=f"{prefix}_process",
    )
    employee = columns[4].selectbox(
        "Employee", [None] + employees,
        format_func=lambda value: "All employees" if value is None else f'{value["name"]} ({value["employee_id"]})',
        key=f"{prefix}_employee",
    )
    pod = st.selectbox(
        "Pod", ["All pods"] + sorted({row["pod"] for row in employees}),
        key=f"{prefix}_pod",
    )
    filters = {}
    if date_from:
        filters["date_from"] = str(date_from)
    if date_to:
        filters["date_to"] = str(date_to)
    if date_from and date_to and date_from > date_to:
        st.error("The start date must be on or before the end date.")
        return None
    if brand:
        filters["brand_id"] = brand["id"]
    if process:
        filters["process_id"] = process["id"]
    if employee:
        filters["employee_id"] = employee["id"]
    if pod != "All pods":
        filters["pod"] = pod
    return filters


tabs = st.tabs(["Overview", "Employees", "Setup", "Availability", "Generate roster", "Imports"])

with tabs[0]:
    employees = api("GET", "/employees") or []
    active_employees = [row for row in employees if row["active"]]
    brands = api("GET", "/brands") or []
    processes = api("GET", "/processes") or []
    allocations = api("GET", "/allocations") or []
    coverage_rules = api("GET", "/coverage") or []
    time_off = api("GET", "/time-off") or []
    holidays = api("GET", "/holidays") or []
    rosters = api("GET", "/rosters") or []
    drafts = [row for row in rosters if row["status"] == "DRAFT"]
    published = [row for row in rosters if row["status"] == "PUBLISHED"]

    metrics = st.columns(5)
    metrics[0].metric("Active employees", len(active_employees))
    metrics[1].metric("Brands / processes", f"{len(brands)} / {len(processes)}")
    metrics[2].metric("Primary / backup allocations", f"{sum(x['role'] == 'PRIMARY' for x in allocations)} / {sum(x['role'] == 'BACKUP' for x in allocations)}")
    metrics[3].metric("Coverage rules", len(coverage_rules))
    metrics[4].metric("Drafts / published", f"{len(drafts)} / {len(published)}")

    incomplete_setup = []
    if not active_employees:
        incomplete_setup.append("active employees")
    if not brands:
        incomplete_setup.append("brands")
    if not processes:
        incomplete_setup.append("processes")
    if not allocations:
        incomplete_setup.append("allocations")
    if not coverage_rules:
        incomplete_setup.append("coverage rules")
    if incomplete_setup:
        st.warning("Setup still needs: " + ", ".join(incomplete_setup) + ".")
    if time_off or holidays:
        st.caption(f"Availability records: {len(time_off)} employee time-off entries and {len(holidays)} company holidays.")

    st.subheader("Roster calendar and assignments")
    if rosters:
        selected_roster = st.selectbox(
            "Roster", rosters,
            format_func=lambda row: f'#{row["id"]} · {row["start"]} to {row["end"]} · {row["status"]}',
            key="overview_roster",
        )
        filters = roster_filters("overview", brands, processes, employees)
        if filters is not None:
            view = api(
                "GET", f'/rosters/{selected_roster["id"]}/views/complete', params=filters,
            )
            if view is not None:
                display_frame(
                    view["entries"],
                    "No assignments match these filters. Clear filters or choose a roster with assignments.",
                )
    else:
        show_empty("No rosters yet. Complete setup, then generate a draft roster.")

    st.subheader("Recent rosters")
    display_frame(rosters[:10], "No roster activity yet.")

with tabs[1]:
    st.subheader("Add employee")
    with st.form("employee_create", clear_on_submit=True):
        name = st.text_input("Name")
        email = st.text_input("Email")
        employee_code = st.text_input("Employee ID")
        title = st.text_input("Title (optional)")
        pod = st.text_input("Pod", "General")
        max_days = st.number_input("Maximum consecutive working days", 1, 14, 6)
        max_work_days = st.number_input("Maximum working days per Monday-Sunday week", 1, 7, 6)
        min_offs = st.number_input("Minimum weekly offs per Monday-Sunday week", 0, 7, 1)
        create_employee = st.form_submit_button("Add employee")
    if create_employee:
        if not name.strip() or not email.strip() or not employee_code.strip() or not pod.strip():
            st.warning("Name, email, employee ID, and pod are required.")
        else:
            result = api("POST", "/employees", json={
                "name": name, "email": email, "employee_id": employee_code,
                "title": title or None, "pod": pod, "max_days": max_days,
                "max_work_days": max_work_days, "min_offs": min_offs,
            })
            if result is not None:
                saved(f'Employee {result["name"]} created.')

    employees = api("GET", "/employees") or []
    if employees:
        st.subheader("Edit or deactivate employee")
        employee_by_id = {row["id"]: row for row in employees}
        selected_employee_id = st.selectbox(
            "Employee to edit", list(employee_by_id),
            format_func=lambda employee_id: (
                f'{employee_by_id[employee_id]["name"]} '
                f'({employee_by_id[employee_id]["employee_id"]})'
            ),
            key="employee_edit_selection",
        )
        selected_employee = employee_by_id[selected_employee_id]
        with st.form("employee_edit"):
            edit_name = st.text_input(
                "Name", value=selected_employee["name"], key=f'employee_name_{selected_employee["id"]}',
            )
            edit_email = st.text_input(
                "Email", value=selected_employee["email"], key=f'employee_email_{selected_employee["id"]}',
            )
            edit_code = st.text_input(
                "Employee ID", value=selected_employee["employee_id"],
                key=f'employee_code_{selected_employee["id"]}',
            )
            edit_title = st.text_input(
                "Title", value=selected_employee["title"] or "",
                key=f'employee_title_{selected_employee["id"]}',
            )
            edit_pod = st.text_input(
                "Pod", value=selected_employee["pod"], key=f'employee_pod_{selected_employee["id"]}',
            )
            edit_max_days = st.number_input(
                "Maximum consecutive working days", 1, 14, selected_employee["max_days"],
                key=f'employee_max_days_{selected_employee["id"]}',
            )
            edit_max_work = st.number_input(
                "Maximum working days per week", 1, 7, selected_employee["max_work_days"],
                key=f'employee_max_work_{selected_employee["id"]}',
            )
            edit_min_offs = st.number_input(
                "Minimum weekly offs", 0, 7, selected_employee["min_offs"],
                key=f'employee_min_offs_{selected_employee["id"]}',
            )
            edit_active = st.checkbox(
                "Active", value=selected_employee["active"],
                key=f'employee_active_{selected_employee["id"]}',
            )
            save_employee = st.form_submit_button("Save employee changes")
        if save_employee:
            if not edit_name.strip() or not edit_email.strip() or not edit_code.strip() or not edit_pod.strip():
                st.warning("Name, email, employee ID, and pod are required.")
            else:
                result = api("PATCH", f'/employees/{selected_employee["id"]}', json={
                    "name": edit_name, "email": edit_email, "employee_id": edit_code,
                    "title": edit_title or None, "pod": edit_pod,
                    "max_days": edit_max_days, "max_work_days": edit_max_work,
                    "min_offs": edit_min_offs, "active": edit_active,
                })
                if result is not None:
                    saved(f'Employee {result["name"]} updated.')
        if selected_employee["active"]:
            if st.button("Deactivate selected employee"):
                result = api("PATCH", f'/employees/{selected_employee["id"]}', json={"active": False})
                if result is not None:
                    saved(f'Employee {result["name"]} deactivated.')
        else:
            if st.button("Reactivate selected employee"):
                result = api("PATCH", f'/employees/{selected_employee["id"]}', json={"active": True})
                if result is not None:
                    saved(f'Employee {result["name"]} reactivated.')
    refreshed_employees = api("GET", "/employees") or []
    display_frame(
        refreshed_employees,
        "No employees yet. Add an employee or import an employee CSV/XLSX file.",
    )

with tabs[2]:
    left, right = st.columns(2)
    with left:
        st.subheader("Brands")
        with st.form("brand_create", clear_on_submit=True):
            brand_name = st.text_input("Brand name")
            add_brand = st.form_submit_button("Add brand")
        if add_brand:
            if not brand_name.strip():
                st.warning("Enter a brand name.")
            else:
                result = api("POST", "/brands", json={"name": brand_name})
                if result is not None:
                    saved(f'Brand {result["name"]} created.')
        brands = api("GET", "/brands") or []
        display_frame(brands, "No brands yet.")
        if brands:
            selected_brand = st.selectbox("Brand to edit", brands, format_func=lambda row: row["name"])
            with st.form("brand_edit"):
                new_brand_name = st.text_input(
                    "Updated brand name", value=selected_brand["name"],
                    key=f'brand_name_{selected_brand["id"]}',
                )
                save_brand = st.form_submit_button("Save brand changes")
            if save_brand:
                if not new_brand_name.strip():
                    st.warning("Enter a brand name.")
                else:
                    result = api("PATCH", f'/brands/{selected_brand["id"]}', json={"name": new_brand_name})
                    if result is not None:
                        saved(f'Brand updated to {result["name"]}.')
    with right:
        st.subheader("Processes")
        with st.form("process_create", clear_on_submit=True):
            process_name = st.text_input("Process name")
            add_process = st.form_submit_button("Add process")
        if add_process:
            if not process_name.strip():
                st.warning("Enter a process name.")
            else:
                result = api("POST", "/processes", json={"name": process_name})
                if result is not None:
                    saved(f'Process {result["name"]} created.')
        processes = api("GET", "/processes") or []
        display_frame(processes, "No processes yet.")
        if processes:
            selected_process = st.selectbox("Process to edit", processes, format_func=lambda row: row["name"])
            with st.form("process_edit"):
                new_process_name = st.text_input(
                    "Updated process name", value=selected_process["name"],
                    key=f'process_name_{selected_process["id"]}',
                )
                save_process = st.form_submit_button("Save process changes")
            if save_process:
                if not new_process_name.strip():
                    st.warning("Enter a process name.")
                else:
                    result = api("PATCH", f'/processes/{selected_process["id"]}', json={"name": new_process_name})
                    if result is not None:
                        saved(f'Process updated to {result["name"]}.')

    st.divider()
    st.subheader("Employee allocations")
    employees = api("GET", "/employees") or []
    active_employees = [row for row in employees if row["active"]]
    brands = api("GET", "/brands") or []
    processes = api("GET", "/processes") or []
    if active_employees and brands and processes:
        with st.form("allocation_create", clear_on_submit=True):
            employee = st.selectbox(
                "Employee", active_employees, format_func=lambda row: f'{row["name"]} (#{row["id"]})',
            )
            allocation_brand = st.selectbox("Allocation brand", brands, format_func=lambda row: row["name"])
            allocation_process = st.selectbox("Allocation process", processes, format_func=lambda row: row["name"])
            role = st.selectbox("Allocation role", ["PRIMARY", "BACKUP"])
            add_allocation = st.form_submit_button("Save allocation")
        if add_allocation:
            result = api("POST", "/allocations", json={
                "employee_id": employee["id"], "brand_id": allocation_brand["id"],
                "process_id": allocation_process["id"], "role": role,
            })
            if result is not None:
                saved(f'{role.title()} allocation created for {employee["name"]}.')
    else:
        show_empty("Add an active employee, brand, and process before creating allocations.")

    allocations = api("GET", "/allocations") or []
    employee_by_id = {row["id"]: row for row in employees}
    brand_by_id = {row["id"]: row for row in brands}
    process_by_id = {row["id"]: row for row in processes}
    allocation_rows = [{
        **row,
        "employee": employee_by_id.get(row["employee_id"], {}).get("name", "Unknown employee"),
        "brand": brand_by_id.get(row["brand_id"], {}).get("name", "Unknown brand"),
        "process": process_by_id.get(row["process_id"], {}).get("name", "Unknown process"),
    } for row in allocations]
    display_frame(allocation_rows, "No allocations yet.")
    if allocations:
        allocation = st.selectbox(
            "Allocation to remove", allocations,
            format_func=lambda row: (
                f'{employee_by_id.get(row["employee_id"], {}).get("name", row["employee_id"])} · '
                f'{brand_by_id.get(row["brand_id"], {}).get("name", row["brand_id"])} / '
                f'{process_by_id.get(row["process_id"], {}).get("name", row["process_id"])} · {row["role"]}'
            ),
        )
        if st.button("Delete selected allocation"):
            result = api("DELETE", f'/allocations/{allocation["id"]}')
            if result is not None:
                saved("Allocation deleted.")

    st.subheader("Coverage rules")
    if brands and processes:
        with st.form("coverage_create", clear_on_submit=True):
            coverage_brand = st.selectbox("Coverage brand", brands, format_func=lambda row: row["name"])
            coverage_process = st.selectbox("Coverage process", processes, format_func=lambda row: row["name"])
            min_staff = st.number_input("Minimum primary staff", 1, 20, 1)
            calendar = st.selectbox("Coverage calendar", ["DAILY", "MON_SAT", "CUSTOM"])
            work_days = st.text_input("Custom weekdays (0=Mon ... 6=Sun)", "0,1,2,3,4,5")
            add_coverage = st.form_submit_button("Save coverage rule")
        if add_coverage:
            result = api("POST", "/coverage", json={
                "brand_id": coverage_brand["id"], "process_id": coverage_process["id"],
                "min_staff": min_staff, "calendar": calendar, "work_days": work_days,
            })
            if result is not None:
                saved("Coverage rule created.")
    else:
        show_empty("Add a brand and process before defining coverage.")
    coverage_rules = api("GET", "/coverage") or []
    display_frame(coverage_rules, "No coverage rules yet.")
    if coverage_rules:
        selected_rule = st.selectbox(
            "Coverage rule to edit", coverage_rules,
            format_func=lambda row: f'Brand #{row["brand_id"]} / Process #{row["process_id"]} · {row["min_staff"]} staff',
        )
        with st.form("coverage_edit"):
            edit_min_staff = st.number_input(
                "Updated minimum primary staff", 1, 20, selected_rule["min_staff"],
                key=f'coverage_min_staff_{selected_rule["id"]}',
            )
            edit_calendar = st.selectbox(
                "Updated calendar", ["DAILY", "MON_SAT", "CUSTOM"],
                index=["DAILY", "MON_SAT", "CUSTOM"].index(selected_rule["calendar"]),
                key=f'coverage_calendar_{selected_rule["id"]}',
            )
            edit_work_days = st.text_input(
                "Updated custom weekdays", value=selected_rule["work_days"],
                key=f'coverage_work_days_{selected_rule["id"]}',
            )
            save_rule = st.form_submit_button("Update coverage rule")
        if save_rule:
            result = api("PATCH", f'/coverage/{selected_rule["id"]}', json={
                "brand_id": selected_rule["brand_id"], "process_id": selected_rule["process_id"],
                "min_staff": edit_min_staff, "calendar": edit_calendar,
                "work_days": edit_work_days,
            })
            if result is not None:
                saved("Coverage rule updated.")

with tabs[3]:
    st.subheader("Employee leave and unavailability")
    employees = api("GET", "/employees") or []
    if employees:
        with st.form("timeoff_create", clear_on_submit=True):
            employee = st.selectbox(
                "Employee with time off", employees, format_func=lambda row: row["name"],
            )
            timeoff_day = st.date_input("Time-off date", date.today())
            kind = st.selectbox("Time-off type", ["LEAVE", "COMP_OFF", "HOLIDAY"])
            add_timeoff = st.form_submit_button("Add availability record")
        if add_timeoff:
            result = api("POST", "/time-off", json={
                "employee_id": employee["id"], "day": str(timeoff_day), "kind": kind,
            })
            if result is not None:
                saved(f"{kind.replace('_', ' ').title()} saved for {employee['name']}.")
    else:
        show_empty("Add an employee before recording time off.")
    time_off = api("GET", "/time-off") or []
    display_frame(time_off, "No employee time-off records.")
    if time_off:
        record = st.selectbox(
            "Time-off record to remove", time_off,
            format_func=lambda row: f'{row["day"]} · employee #{row["employee_id"]} · {row["kind"]}',
        )
        if st.button("Delete selected time-off record"):
            result = api("DELETE", f'/time-off/{record["id"]}')
            if result is not None:
                saved("Time-off record deleted.")

    st.subheader("Company holidays")
    with st.form("holiday_create", clear_on_submit=True):
        holiday_day = st.date_input("Holiday date", date.today())
        holiday_description = st.text_input("Holiday description")
        full_closure = st.checkbox("Full company closure", value=True)
        add_holiday = st.form_submit_button("Add company holiday")
    if add_holiday:
        if not holiday_description.strip():
            st.warning("Enter a holiday description.")
        else:
            result = api("POST", "/holidays", json={
                "day": str(holiday_day), "description": holiday_description,
                "full_closure": full_closure,
            })
            if result is not None:
                saved(f'Company holiday "{result["description"]}" saved.')
    holidays = api("GET", "/holidays") or []
    display_frame(holidays, "No company holidays configured.")

with tabs[4]:
    st.subheader("Generate a draft roster")
    with st.form("generate_roster"):
        start = st.date_input("Start date", date.today(), key="generate_start")
        end = st.date_input("End date", date.today() + timedelta(days=6), key="generate_end")
        time_limit = st.slider("Solver time limit (seconds)", 1, 60, 10)
        allow_incomplete = st.checkbox("Allow an explicitly incomplete draft", value=False)
        generate_roster = st.form_submit_button("Generate roster")
    if generate_roster:
        if end < start:
            st.error("The end date must be on or after the start date.")
        else:
            result = api("POST", "/rosters/generate", json={
                "start": str(start), "end": str(end), "time_limit": time_limit,
                "allow_incomplete": allow_incomplete,
            })
            if result is not None:
                if result["valid"]:
                    st.success(f'Roster #{result["roster_id"]} generated ({result["solver_status"]}) and passed validation.')
                else:
                    st.warning(
                        f'Roster #{result["roster_id"]} is an incomplete draft ({result["solver_status"]}); '
                        "coverage gaps prevent publishing."
                    )
                if result["coverage_gaps"]:
                    st.warning(f'{result["coverage_gaps"]} coverage gaps need attention.')
                st.json(result)

    rosters = api("GET", "/rosters") or []
    if not rosters:
        show_empty("No rosters to review. Generate a draft after completing setup.")
    else:
        roster = st.selectbox(
            "Review roster", rosters,
            format_func=lambda row: f'#{row["id"]} · {row["start"]} to {row["end"]} · {row["status"]}',
            key="review_roster",
        )
        roster_id = roster["id"]
        if st.button("Validate selected roster"):
            validation = api("GET", f"/rosters/{roster_id}/validate")
            if validation is not None:
                if validation["valid"]:
                    st.success("Roster is valid.")
                else:
                    st.error(f'{len(validation["errors"])} validation error(s) found.')
                st.json(validation)
        detail = api("GET", f"/rosters/{roster_id}")
        brands = api("GET", "/brands") or []
        processes = api("GET", "/processes") or []
        employees = api("GET", "/employees") or []
        if detail is not None:
            st.subheader("Complete roster")
            display_frame(detail["entries"], "This roster has no assignments.")
            st.subheader("Roster views")
            view_type = st.selectbox(
                "Group roster by", ["complete", "brand", "process", "pod", "employee"],
            )
            filters = roster_filters("review", brands, processes, employees)
            if filters is not None:
                view = api("GET", f"/rosters/{roster_id}/views/{view_type}", params=filters)
                if view is not None:
                    if "groups" in view:
                        if view["groups"]:
                            for group, rows in view["groups"].items():
                                st.markdown(f"**{group}**")
                                display_frame(rows, "No assignments in this group.")
                        else:
                            show_empty("No assignments match these filters.")
                    else:
                        display_frame(view["entries"], "No assignments match these filters.")

            st.subheader("Backup suggestions")
            backup_result = api("GET", f"/rosters/{roster_id}/backup-suggestions")
            suggestions = backup_result["suggestions"] if backup_result is not None else []
            if suggestions:
                employee_names = {row["id"]: row["name"] for row in employees}
                brand_names = {row["id"]: row["name"] for row in brands}
                process_names = {row["id"]: row["name"] for row in processes}
                suggestion_index = st.selectbox(
                    "Eligible backup assignment", list(range(len(suggestions))),
                    format_func=lambda index: (
                        f'{suggestions[index]["day"]} · {employee_names.get(suggestions[index]["employee_id"], "Employee")} · '
                        f'{brand_names.get(suggestions[index]["brand_id"], "Brand")} / '
                        f'{process_names.get(suggestions[index]["process_id"], "Process")}'
                    ),
                )
                if st.button("Accept backup suggestion"):
                    suggestion = suggestions[suggestion_index]
                    result = api(
                        "POST", f"/rosters/{roster_id}/backup-suggestions/accept",
                        json={key: suggestion[key] for key in ("employee_id", "brand_id", "process_id", "day")},
                    )
                    if result is not None:
                        saved("Backup assignment accepted and roster revalidated.")
            else:
                show_empty("No eligible backup suggestions. Check coverage gaps and BACKUP allocations.")

            st.subheader("Manual edit and locks")
            entries = detail["entries"]
            if entries:
                entry = st.selectbox(
                    "Assignment to edit or lock", entries,
                    format_func=lambda row: (
                        f'{row["day"]} · {row["employee"]} · {row["brand"]} / '
                        f'{row["process"]} · entry #{row["entry_id"]}'
                    ),
                )
                if employees and brands and processes:
                    with st.form("manual_entry"):
                        edit_employees = [row for row in employees if row["active"]]
                        selected_employee_index = next(
                            (i for i, row in enumerate(edit_employees) if row["id"] == entry["employee_id"]), 0,
                        )
                        edit_employee = st.selectbox(
                            "Assigned employee", edit_employees, index=selected_employee_index,
                            format_func=lambda row: row["name"],
                            key=f'entry_employee_{entry["entry_id"]}',
                        )
                        edit_brand = st.selectbox(
                            "Assigned brand", brands,
                            index=next((i for i, row in enumerate(brands) if row["id"] == entry["brand_id"]), 0),
                            format_func=lambda row: row["name"],
                            key=f'entry_brand_{entry["entry_id"]}',
                        )
                        edit_process = st.selectbox(
                            "Assigned process", processes,
                            index=next((i for i, row in enumerate(processes) if row["id"] == entry["process_id"]), 0),
                            format_func=lambda row: row["name"],
                            key=f'entry_process_{entry["entry_id"]}',
                        )
                        edit_day = st.date_input(
                            "Assignment date", date.fromisoformat(entry["day"]),
                            key=f'entry_day_{entry["entry_id"]}',
                        )
                        save_entry = st.form_submit_button("Save manual edit")
                    if save_entry:
                        result = api("PATCH", f'/rosters/{roster_id}/entries/{entry["entry_id"]}', json={
                            "employee_id": edit_employee["id"], "brand_id": edit_brand["id"],
                            "process_id": edit_process["id"], "day": str(edit_day),
                        })
                        if result is not None:
                            saved("Manual assignment saved and roster revalidated.")

                    lock_action = "unlock" if entry["locked"] else "lock"
                    if st.button(f"{lock_action.title()} selected assignment"):
                        result = api(
                            "POST", f'/rosters/{roster_id}/entries/{entry["entry_id"]}/{lock_action}',
                        )
                        if result is not None:
                            saved(f'Assignment {lock_action}ed.')
                else:
                    show_empty("Add employees, brands, and processes before editing a roster entry.")

            if roster["status"] == "PUBLISHED":
                st.info("Published rosters are read-only and cannot be regenerated.")
            elif st.button("Regenerate draft (preserve locked assignments)"):
                result = api("POST", f"/rosters/{roster_id}/regenerate")
                if result is not None:
                    saved(f'Roster #{roster_id} regenerated ({result["solver_status"]}).')

            st.subheader("Export roster")
            export_format = st.selectbox("File format", ["csv", "xlsx"], key="export_format")
            if st.button("Publish selected roster", disabled=roster["status"] == "PUBLISHED"):
                result = api("POST", f"/rosters/{roster_id}/publish")
                if result is not None:
                    saved(f"Roster #{roster_id} published.")

            if st.button("Prepare download"):
                try:
                    response = requests.get(
                        f"{API}/rosters/{roster_id}/export",
                        params={"format": export_format}, timeout=30,
                    )
                    if response.status_code >= 400:
                        try:
                            message = response.json()
                        except ValueError:
                            message = response.text
                        st.error(f"Export failed: {message}")
                    else:
                        st.success("Export ready.")
                        st.download_button(
                            "Download roster file", response.content,
                            file_name=f"roster-{roster_id}.{export_format}",
                            mime=(
                                "text/csv" if export_format == "csv" else
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                            ),
                        )
                except requests.RequestException as exc:
                    st.error(f"Could not download the roster: {exc}")

with tabs[5]:
    st.subheader("Import employee CSV/XLSX")
    employee_file = st.file_uploader("Employee file", type=["csv", "xlsx"], key="employee_file")
    if employee_file is not None and st.button("Import employees"):
        result = api(
            "POST", "/imports/employees",
            files={"file": (employee_file.name, employee_file.getvalue())},
        )
        if result is not None:
            st.success(f'Employee import finished: {result["accepted"]} accepted, {result["rejected"]} rejected.')
            if result["errors"]:
                st.warning("Some rows were rejected.")
                st.write(result["errors"])
            st.write("Refresh the Employees tab to see accepted records.")
    st.caption("Required columns: name, email, employee_id. Optional: title, pod, max_days, max_work_days, min_offs.")

    st.subheader("Import allocation CSV/XLSX")
    allocation_file = st.file_uploader("Allocation file", type=["csv", "xlsx"], key="allocation_file")
    if allocation_file is not None and st.button("Import allocations"):
        result = api(
            "POST", "/imports/allocations",
            files={"file": (allocation_file.name, allocation_file.getvalue())},
        )
        if result is not None:
            st.success(f'Allocation import finished: {result["accepted"]} accepted, {result["rejected"]} rejected.')
            if result["errors"]:
                st.warning("Some rows were rejected.")
                st.write(result["errors"])
            st.write("Refresh the Setup tab to see accepted allocations.")
    st.caption("Required columns: employee_id, brand, process. Optional: role.")
