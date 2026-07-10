"""Create the monthly accumulation tables."""

from contextlib import closing
from typing import override

from mpt_tool.migration import SchemaBaseMigration

from mpt_usage_reporting_extension.persistence.sqlite.database import connect_sync
from mpt_usage_reporting_extension.persistence.sqlite.retry import retry_on_busy_sync

_CREATE_SUBSCRIPTION_TABLE = """
CREATE TABLE IF NOT EXISTS subscription_monthly_accumulation (
    subscription_id TEXT NOT NULL,
    agreement_id TEXT NOT NULL,
    year INTEGER NOT NULL CHECK (year BETWEEN 1000 AND 9999),
    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    ppx1 DECIMAL NOT NULL DEFAULT '0',
    spx1 DECIMAL NOT NULL DEFAULT '0',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (subscription_id, year, month, agreement_id)
)
"""

_CREATE_AGREEMENT_TABLE = """
CREATE TABLE IF NOT EXISTS agreement_monthly_accumulation (
    agreement_id TEXT NOT NULL,
    year INTEGER NOT NULL CHECK (year BETWEEN 1000 AND 9999),
    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    ppx1 DECIMAL NOT NULL DEFAULT '0',
    spx1 DECIMAL NOT NULL DEFAULT '0',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (agreement_id, year, month)
)
"""


class Migration(SchemaBaseMigration):
    """Create subscription and agreement monthly accumulation tables."""

    @override
    def run(self) -> None:
        """Open the SQLite database and create both accumulation tables."""
        self.log.info("Creating monthly accumulation tables")
        self._create_tables()

    @retry_on_busy_sync
    def _create_tables(self) -> None:
        with closing(connect_sync()) as connection:
            connection.execute(_CREATE_SUBSCRIPTION_TABLE)
            connection.execute(_CREATE_AGREEMENT_TABLE)
            connection.commit()
