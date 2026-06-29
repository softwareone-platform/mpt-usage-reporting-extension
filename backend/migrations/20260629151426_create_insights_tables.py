"""Create the command-execution and statement-processing insights tables."""

import sqlite3
from contextlib import closing
from typing import override

from mpt_tool.migration import SchemaBaseMigration

from mpt_usage_reporting_extension.persistence.sqlite.database import resolve_db_path

_CREATE_COMMAND_EXECUTION_TABLE = """
CREATE TABLE IF NOT EXISTS command_execution (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    parameters TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    result TEXT
)
"""

_CREATE_STATEMENT_PROCESSING_TABLE = """
CREATE TABLE IF NOT EXISTS statement_processing (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id INTEGER NOT NULL REFERENCES command_execution(id),
    statement_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL,
    failure_message TEXT
)
"""

_CREATE_STATEMENT_PROCESSING_INDEX = """
CREATE INDEX IF NOT EXISTS idx_statement_processing_execution
ON statement_processing (execution_id)
"""


class Migration(SchemaBaseMigration):
    """Create the command-execution and statement-processing insights tables."""

    @override
    def run(self) -> None:
        """Open the SQLite database and create both insights tables."""
        self.log.info("Creating insights tables")
        with closing(sqlite3.connect(resolve_db_path())) as connection:
            connection.execute(_CREATE_COMMAND_EXECUTION_TABLE)
            connection.execute(_CREATE_STATEMENT_PROCESSING_TABLE)
            connection.execute(_CREATE_STATEMENT_PROCESSING_INDEX)
            connection.commit()
