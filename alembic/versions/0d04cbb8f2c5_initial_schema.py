"""Initial schema."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0d04cbb8f2c5"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "employees",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("email", sa.String(120), nullable=False, unique=True),
        sa.Column("employee_id", sa.String(50), nullable=False, unique=True),
        sa.Column("title", sa.String(100), nullable=True),
        sa.Column("pod", sa.String(60), nullable=False, server_default="General"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("max_days", sa.Integer, nullable=False, server_default="6"),
        sa.Column("min_offs", sa.Integer, nullable=False, server_default="1"),
    )
    op.create_table(
        "brands",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
    )
    op.create_table(
        "processes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
    )
    op.create_table(
        "allocations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("employee_id", sa.Integer, sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("brand_id", sa.Integer, sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("process_id", sa.Integer, sa.ForeignKey("processes.id"), nullable=False),
        sa.Column("role", sa.String(12), nullable=False, server_default="PRIMARY"),
        sa.UniqueConstraint("employee_id", "brand_id", "process_id", "role", name="uq_alloc_role"),
    )
    op.create_table(
        "coverage_rules",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("brand_id", sa.Integer, sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("process_id", sa.Integer, sa.ForeignKey("processes.id"), nullable=False),
        sa.Column("min_staff", sa.Integer, nullable=False, server_default="1"),
        sa.Column("calendar", sa.String(20), nullable=False, server_default="DAILY"),
        sa.Column("work_days", sa.String(30), nullable=False, server_default="0,1,2,3,4,5"),
        sa.UniqueConstraint("brand_id", "process_id", name="uq_coverage"),
    )
    op.create_table(
        "time_off",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("employee_id", sa.Integer, sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
    )
    op.create_table(
        "company_holidays",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("day", sa.Date, nullable=False, unique=True),
        sa.Column("description", sa.String(200), nullable=False),
        sa.Column("full_closure", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "rosters",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("start", sa.Date, nullable=False),
        sa.Column("end", sa.Date, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("published_at", sa.DateTime),
    )
    op.create_table(
        "roster_entries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("roster_id", sa.Integer, sa.ForeignKey("rosters.id"), nullable=False),
        sa.Column("employee_id", sa.Integer, sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("brand_id", sa.Integer, sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("process_id", sa.Integer, sa.ForeignKey("processes.id"), nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("role", sa.String(12), nullable=False, server_default="PRIMARY"),
        sa.Column("source", sa.String(12), nullable=False, server_default="SOLVER"),
        sa.Column("status", sa.String(20), nullable=False, server_default="WORK"),
        sa.Column("locked", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("updated_by", sa.String(100)),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.current_timestamp()),
    )
    op.create_table(
        "import_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("accepted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rejected", sa.Integer, nullable=False, server_default="0"),
        sa.Column("details", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.current_timestamp()),
    )


def downgrade() -> None:
    op.drop_table("import_logs")
    op.drop_table("roster_entries")
    op.drop_table("rosters")
    op.drop_table("company_holidays")
    op.drop_table("time_off")
    op.drop_table("coverage_rules")
    op.drop_table("allocations")
    op.drop_table("processes")
    op.drop_table("brands")
    op.drop_table("employees")
