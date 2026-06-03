"""Create the monthly accumulation tables."""

from contextlib import closing
from typing import override

from mpt_tool.migration import SchemaBaseMigration

from mpt_usage_reporting_extension.persistence import database


class Migration(SchemaBaseMigration):
    """Create subscription and agreement monthly accumulation tables."""

    @override
    def run(self) -> None:
        """Open the SQLite database and create both accumulation tables."""
        self.log.info("Creating monthly accumulation tables")
        with closing(database.connect()) as connection:
            database.create_schema(connection)
