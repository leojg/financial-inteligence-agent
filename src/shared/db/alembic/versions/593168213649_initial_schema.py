"""Initial schema.

Revision ID: 593168213649
Revises:
Create Date: 2026-03-16 15:36:26.033784

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = '593168213649'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    """Create all application tables if they do not already exist."""
    existing = _existing_tables()

    if 'exchange_rates' not in existing:
        op.create_table('exchange_rates',
            sa.Column('date', sa.String(), nullable=False),
            sa.Column('from_currency', sa.String(), nullable=False),
            sa.Column('to_currency', sa.String(), nullable=False),
            sa.Column('rate', sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint('date', 'from_currency', 'to_currency'),
        )

    if 'normalized_document_cache' not in existing:
        op.create_table('normalized_document_cache',
            sa.Column('content_hash', sa.String(), nullable=False),
            sa.Column('source_file', sa.String(), nullable=False),
            sa.Column('transactions_json', sa.Text(), nullable=False),
            sa.PrimaryKeyConstraint('content_hash'),
        )

    if 'runs_history' not in existing:
        op.create_table('runs_history',
            sa.Column('run_id', sa.String(), nullable=False),
            sa.Column('thread_id', sa.String(), nullable=False),
            sa.Column('created_at', sa.String(), nullable=False),
            sa.Column('completed_at', sa.String(), nullable=True),
            sa.Column('source_paths', sa.Text(), nullable=False),
            sa.Column('status', sa.String(), nullable=False),
            sa.Column('total_transactions', sa.Integer(), nullable=True),
            sa.Column('total_duplicates', sa.Integer(), nullable=True),
            sa.Column('total_suspicious', sa.Integer(), nullable=True),
            sa.Column('base_currency', sa.String(), nullable=False),
            sa.PrimaryKeyConstraint('run_id'),
        )

    if 'merchant_categories' not in existing:
        op.create_table('merchant_categories',
            sa.Column('merchant_normalized', sa.String(), nullable=False),
            sa.Column('category', sa.String(), nullable=False),
            sa.Column('source', sa.String(), nullable=False),
            sa.Column('updated_at', sa.String(), nullable=False),
            sa.PrimaryKeyConstraint('merchant_normalized'),
        )

    if 'duplicate_pairs' not in existing:
        op.create_table('duplicate_pairs',
            sa.Column('fingerprint_a', sa.String(), nullable=False),
            sa.Column('fingerprint_b', sa.String(), nullable=False),
            sa.Column('is_duplicate', sa.Integer(), nullable=False),
            sa.Column('reason', sa.String(), nullable=True),
            sa.PrimaryKeyConstraint('fingerprint_a', 'fingerprint_b'),
        )

    if 'transactions' not in existing:
        op.create_table('transactions',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('run_id', sa.String(), nullable=False),
            sa.Column('fingerprint', sa.String(), nullable=False),
            sa.Column('date', sa.String(), nullable=False),
            sa.Column('amount_original', sa.Float(), nullable=False),
            sa.Column('amount_base', sa.Float(), nullable=True),
            sa.Column('currency', sa.String(), nullable=False),
            sa.Column('merchant', sa.String(), nullable=False),
            sa.Column('merchant_normalized', sa.String(), nullable=False),
            sa.Column('account', sa.String(), nullable=False),
            sa.Column('source_file', sa.String(), nullable=False),
            sa.Column('category', sa.String(), nullable=True),
            sa.Column('duplicate_of', sa.String(), nullable=True),
            sa.Column('suspicious', sa.Integer(), nullable=False),
            sa.Column('suspicious_reason', sa.String(), nullable=True),
            sa.Column('needs_review', sa.Integer(), nullable=False),
            sa.Column('review_reason', sa.String(), nullable=True),
            sa.Column('review_status', sa.String(), nullable=True),
            sa.Column('confidence', sa.Float(), nullable=True),
            sa.ForeignKeyConstraint(['run_id'], ['runs_history.run_id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('idx_transactions_run_id', 'transactions', ['run_id'])
        op.create_index('idx_transactions_fingerprint', 'transactions', ['fingerprint'])
        op.create_index('idx_transactions_date', 'transactions', ['date'])
        op.create_index('idx_transactions_account', 'transactions', ['account'])
        op.create_index('idx_transactions_category', 'transactions', ['category'])
        op.create_index('idx_transactions_run_date', 'transactions', ['run_id', 'date'])

    if 'user_goals' not in existing:
        op.create_table('user_goals',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('active', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.String(), nullable=False),
            sa.Column('updated_at', sa.String(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )

    if 'insights_cache' not in existing:
        op.create_table('insights_cache',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('date_from', sa.String(), nullable=False),
            sa.Column('date_to', sa.String(), nullable=False),
            sa.Column('accounts_hash', sa.String(), nullable=False),
            sa.Column('aggregations_json', sa.Text(), nullable=False),
            sa.Column('habits_json', sa.Text(), nullable=False),
            sa.Column('suggestions_json', sa.Text(), nullable=False),
            sa.Column('created_at', sa.String(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('uq_insights_cache', 'insights_cache', ['date_from', 'date_to', 'accounts_hash'], unique=True)

    if 'receipts' not in existing:
        op.create_table('receipts',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('run_id', sa.String(), nullable=False),
            sa.Column('transaction_id', sa.String(), nullable=True),
            sa.Column('source_file', sa.String(), nullable=False),
            sa.Column('merchant', sa.String(), nullable=False),
            sa.Column('merchant_normalized', sa.String(), nullable=False),
            sa.Column('date', sa.String(), nullable=True),
            sa.Column('currency', sa.String(), nullable=True),
            sa.Column('subtotal', sa.Float(), nullable=True),
            sa.Column('tax_amount', sa.Float(), nullable=True),
            sa.Column('tax_rate', sa.Float(), nullable=True),
            sa.Column('total', sa.Float(), nullable=False),
            sa.Column('receipt_number', sa.String(), nullable=True),
            sa.Column('confidence', sa.Float(), nullable=True),
            sa.Column('raw_content', sa.Text(), nullable=True),
            sa.Column('created_at', sa.String(), nullable=False),
            sa.ForeignKeyConstraint(['run_id'], ['runs_history.run_id']),
            sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('idx_receipts_run_id', 'receipts', ['run_id'])
        op.create_index('idx_receipts_transaction_id', 'receipts', ['transaction_id'])

    if 'receipt_lines' not in existing:
        op.create_table('receipt_lines',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('receipt_id', sa.String(), nullable=False),
            sa.Column('line_number', sa.Integer(), nullable=False),
            sa.Column('description', sa.String(), nullable=False),
            sa.Column('quantity', sa.Float(), nullable=True),
            sa.Column('unit_price', sa.Float(), nullable=True),
            sa.Column('amount', sa.Float(), nullable=False),
            sa.Column('category', sa.String(), nullable=True),
            sa.ForeignKeyConstraint(['receipt_id'], ['receipts.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('idx_receipt_lines_receipt_id', 'receipt_lines', ['receipt_id'])

    # Remove the old schema_version table (replaced by alembic_version)
    if 'schema_version' in existing:
        op.drop_table('schema_version')


def downgrade() -> None:
    """Drop all application tables."""
    op.drop_index('idx_receipt_lines_receipt_id', table_name='receipt_lines')
    op.drop_table('receipt_lines')
    op.drop_index('idx_receipts_transaction_id', table_name='receipts')
    op.drop_index('idx_receipts_run_id', table_name='receipts')
    op.drop_table('receipts')
    op.drop_index('uq_insights_cache', table_name='insights_cache')
    op.drop_table('insights_cache')
    op.drop_table('user_goals')
    op.drop_index('idx_transactions_run_date', table_name='transactions')
    op.drop_index('idx_transactions_category', table_name='transactions')
    op.drop_index('idx_transactions_account', table_name='transactions')
    op.drop_index('idx_transactions_date', table_name='transactions')
    op.drop_index('idx_transactions_fingerprint', table_name='transactions')
    op.drop_index('idx_transactions_run_id', table_name='transactions')
    op.drop_table('transactions')
    op.drop_table('duplicate_pairs')
    op.drop_table('merchant_categories')
    op.drop_table('runs_history')
    op.drop_table('normalized_document_cache')
    op.drop_table('exchange_rates')
