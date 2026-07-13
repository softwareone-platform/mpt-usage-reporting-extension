"""Create the monthly accumulation tables in PostgreSQL."""

from typing import override

from mpt_tool.migration import SchemaBaseMigration

from mpt_usage_reporting_extension.persistence.postgres.database import connect_sync

_CREATE_SUBSCRIPTION_TABLE = """
CREATE TABLE IF NOT EXISTS subscription_monthly_accumulation (
    subscription_id TEXT NOT NULL,
    agreement_id TEXT NOT NULL,
    year INTEGER NOT NULL CHECK (year BETWEEN 1000 AND 9999),
    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    ppx1 NUMERIC NOT NULL DEFAULT 0,
    spx1 NUMERIC NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (subscription_id, year, month, agreement_id)
)
"""

_CREATE_AGREEMENT_TABLE = """
CREATE TABLE IF NOT EXISTS agreement_monthly_accumulation (
    agreement_id TEXT NOT NULL,
    year INTEGER NOT NULL CHECK (year BETWEEN 1000 AND 9999),
    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    ppx1 NUMERIC NOT NULL DEFAULT 0,
    spx1 NUMERIC NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (agreement_id, year, month)
)
"""


class Migration(SchemaBaseMigration):
    """Create subscription and agreement monthly accumulation tables in PostgreSQL."""

    @override
    def run(self) -> None:
        """Open the PostgreSQL database and create both accumulation tables."""
        self.log.info("Creating PostgreSQL monthly accumulation tables")
        with connect_sync() as connection:
            connection.execute(_CREATE_SUBSCRIPTION_TABLE)
            connection.execute(_CREATE_AGREEMENT_TABLE)
