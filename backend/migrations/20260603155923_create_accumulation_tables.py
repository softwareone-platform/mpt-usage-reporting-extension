"""Create the monthly accumulation tables."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import override

from mpt_tool.migration import SchemaBaseMigration

_DB_ENV_VAR = "USAGE_REPORTING_DB_PATH"
_DEFAULT_DB_PATH = Path(__file__).parents[1] / "storage.db"

_CREATE_SUBSCRIPTION_TABLE = """
CREATE TABLE IF NOT EXISTS subscription_monthly_accumulation (
    subscription_id TEXT NOT NULL,
    agreement_id TEXT NOT NULL,
    year INTEGER NOT NULL CHECK (year BETWEEN 1000 AND 9999),
    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    ppx1 DECIMAL NOT NULL DEFAULT '0',
    spx1 DECIMAL NOT NULL DEFAULT '0',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (subscription_id, agreement_id, year, month)
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
        override_path = os.environ.get(_DB_ENV_VAR)
        db_path = Path(override_path) if override_path else _DEFAULT_DB_PATH
        with closing(sqlite3.connect(db_path)) as connection:
            connection.execute(_CREATE_SUBSCRIPTION_TABLE)
            connection.execute(_CREATE_AGREEMENT_TABLE)
            connection.commit()
