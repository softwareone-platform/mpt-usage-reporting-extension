"""Create the command-execution and statement-processing insights tables in PostgreSQL."""

from typing import override

from mpt_tool.migration import SchemaBaseMigration

from mpt_usage_reporting_extension.persistence.postgres.database import connect_sync

_CREATE_COMMAND_EXECUTION_TABLE = """
CREATE TABLE IF NOT EXISTS command_execution (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    command TEXT NOT NULL,
    parameters TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    result TEXT
)
"""

_CREATE_STATEMENT_PROCESSING_TABLE = """
CREATE TABLE IF NOT EXISTS statement_processing (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    execution_id BIGINT NOT NULL REFERENCES command_execution(id),
    statement_id TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    status TEXT NOT NULL,
    failure_message TEXT
)
"""

_CREATE_STATEMENT_PROCESSING_INDEX = """
CREATE INDEX IF NOT EXISTS idx_statement_processing_execution
ON statement_processing (execution_id)
"""


class Migration(SchemaBaseMigration):
    """Create the command-execution and statement-processing insights tables in PostgreSQL."""

    @override
    def run(self) -> None:
        """Open the PostgreSQL database and create both insights tables."""
        self.log.info("Creating PostgreSQL insights tables")
        with connect_sync() as connection:
            connection.execute(_CREATE_COMMAND_EXECUTION_TABLE)
            connection.execute(_CREATE_STATEMENT_PROCESSING_TABLE)
            connection.execute(_CREATE_STATEMENT_PROCESSING_INDEX)
