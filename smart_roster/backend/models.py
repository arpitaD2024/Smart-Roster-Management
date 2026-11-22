from datetime import date, datetime
from sqlalchemy import String, Integer, Boolean, Date, DateTime, ForeignKey, UniqueConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base

class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    employee_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pod: Mapped[str] = mapped_column(String(60), default="General")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    max_days: Mapped[int] = mapped_column(Integer, default=6)
    max_work_days: Mapped[int] = mapped_column(Integer, default=6)
    min_offs: Mapped[int] = mapped_column(Integer, default=1)

class Brand(Base):
    __tablename__ = "brands"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)

class Process(Base):
    __tablename__ = "processes"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)

class Allocation(Base):
    __tablename__ = "allocations"
    __table_args__ = (UniqueConstraint("employee_id", "brand_id", "process_id", "role", name="uq_alloc_role"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id"))
    process_id: Mapped[int] = mapped_column(ForeignKey("processes.id"))
    role: Mapped[str] = mapped_column(String(12), default="PRIMARY")  # PRIMARY or BACKUP

class CoverageRule(Base):
    __tablename__ = "coverage_rules"
    __table_args__ = (UniqueConstraint("brand_id", "process_id", name="uq_coverage"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id"))
    process_id: Mapped[int] = mapped_column(ForeignKey("processes.id"))
    min_staff: Mapped[int] = mapped_column(Integer, default=1)
    calendar: Mapped[str] = mapped_column(String(20), default="DAILY") # DAILY, MON_SAT, CUSTOM
    work_days: Mapped[str] = mapped_column(String(30), default="0,1,2,3,4,5")

class TimeOff(Base):
    __tablename__ = "time_off"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    day: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(20)) # LEAVE, COMP_OFF, HOLIDAY

class CompanyHoliday(Base):
    __tablename__ = "company_holidays"
    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, unique=True)
    description: Mapped[str] = mapped_column(String(200))
    full_closure: Mapped[bool] = mapped_column(Boolean, default=True)

class Roster(Base):
    __tablename__ = "rosters"
    id: Mapped[int] = mapped_column(primary_key=True)
    start: Mapped[date] = mapped_column(Date)
    end: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

class RosterEntry(Base):
    __tablename__ = "roster_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    roster_id: Mapped[int] = mapped_column(ForeignKey("rosters.id"))
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id"))
    process_id: Mapped[int] = mapped_column(ForeignKey("processes.id"))
    day: Mapped[date] = mapped_column(Date)
    role: Mapped[str] = mapped_column(String(12), default="PRIMARY")
    source: Mapped[str] = mapped_column(String(12), default="SOLVER")  # SOLVER, BACKUP_SUGGESTION, MANUAL
    status: Mapped[str] = mapped_column(String(20), default="WORK")
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

class ImportLog(Base):
    __tablename__ = "import_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    file_name: Mapped[str] = mapped_column(String(255))
    accepted: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
