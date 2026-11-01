"""Statements (v1.6): statements table, extraction cache, transaction traceability columns.

Revision ID: b4e2c7d91a30
Revises: 593168213649
Create Date: 2026-11-01 12:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "b4e2c7d91a30"
down_revision: Union[str, None] = "593168213649"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _transaction_columns() -> list[
    sa.Column
]:  # fresh objects: a Column belongs to one table
    return [
        sa.Column("statement_id", sa.String(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("row_index", sa.Integer(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("amount_raw", sa.String(), nullable=True),
        sa.Column("balance_after", sa.Float(), nullable=True),
        sa.Column("sign_source", sa.String(), nullable=True),
        sa.Column("installment_n", sa.Integer(), nullable=True),
        sa.Column("installment_m", sa.Integer(), nullable=True),
    ]


def upgrade() -> None:
    """Create statements tables and add traceability columns to transactions, if missing."""
    inspector = inspect(op.get_bind())
    existing = set(inspector.get_table_names())

    if "statements" not in existing:
        op.create_table(
            "statements",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("source_file", sa.String(), nullable=False),
            sa.Column("file_sha256", sa.String(), nullable=False),
            sa.Column("institution", sa.String(), nullable=True),
            sa.Column("account", sa.String(), nullable=True),
            sa.Column("account_type", sa.String(), nullable=True),
            sa.Column("currency", sa.String(), nullable=True),
            sa.Column("period_start", sa.String(), nullable=True),
            sa.Column("period_end", sa.String(), nullable=True),
            sa.Column("opening_balance", sa.Float(), nullable=True),
            sa.Column("opening_balance_raw", sa.String(), nullable=True),
            sa.Column("closing_balance", sa.Float(), nullable=True),
            sa.Column("closing_balance_raw", sa.String(), nullable=True),
            sa.Column("printed_totals_json", sa.Text(), nullable=False),
            sa.Column("row_count", sa.Integer(), nullable=False),
            sa.Column("extractor", sa.String(), nullable=True),
            sa.Column("extractor_version", sa.String(), nullable=True),
            sa.Column("balance_status", sa.String(), nullable=True),
            sa.Column("balance_detail", sa.Text(), nullable=True),
            sa.Column("breaks_json", sa.Text(), nullable=False),
            sa.Column("diagnostics_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["runs_history.run_id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("idx_statements_run_id", "statements", ["run_id"])
        op.create_index("idx_statements_file_sha256", "statements", ["file_sha256"])
        op.create_index(
            "idx_statements_account_period", "statements", ["account", "period_start"]
        )

    if "statement_extraction_cache" not in existing:
        op.create_table(
            "statement_extraction_cache",
            sa.Column("cache_key", sa.String(), nullable=False),
            sa.Column("file_sha256", sa.String(), nullable=False),
            sa.Column("statements_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.PrimaryKeyConstraint("cache_key"),
        )

    present = {c["name"] for c in inspector.get_columns("transactions")}
    for column in _transaction_columns():
        if column.name not in present:
            op.add_column("transactions", column)
    indexes = {i["name"] for i in inspector.get_indexes("transactions")}
    if "idx_transactions_statement_id" not in indexes:
        op.create_index(
            "idx_transactions_statement_id", "transactions", ["statement_id"]
        )


def downgrade() -> None:
    """Drop statements tables and the transaction traceability columns."""
    op.drop_index("idx_transactions_statement_id", table_name="transactions")
    with op.batch_alter_table("transactions") as batch:
        for column in reversed(_transaction_columns()):
            batch.drop_column(column.name)
    op.drop_table("statement_extraction_cache")
    op.drop_index("idx_statements_account_period", table_name="statements")
    op.drop_index("idx_statements_file_sha256", table_name="statements")
    op.drop_index("idx_statements_run_id", table_name="statements")
    op.drop_table("statements")
