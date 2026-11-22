"""Add per-week maximum work days."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "6e5f4a8d1c20"
down_revision: Union[str, Sequence[str], None] = "0d04cbb8f2c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "employees",
        sa.Column("max_work_days", sa.Integer(), nullable=False, server_default="6"),
    )


def downgrade() -> None:
    op.drop_column("employees", "max_work_days")
