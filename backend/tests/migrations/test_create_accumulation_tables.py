from contextlib import closing
from importlib import util
from pathlib import Path

from mpt_usage_reporting_extension.persistence import database

_MIGRATION_PATH = next(
    (Path(__file__).parents[2] / "migrations").glob("*_create_accumulation_tables.py"),
)


def _load_migration():
    spec = util.spec_from_file_location("create_accumulation_tables", _MIGRATION_PATH)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _table_names() -> set[str]:
    with closing(database.connect()) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'",
        ).fetchall()
    return {row["name"] for row in rows}


def test_migration_run_creates_both_tables(tmp_path, monkeypatch):
    monkeypatch.setenv("USAGE_REPORTING_DB_PATH", str(tmp_path / "storage.db"))
    migration = _load_migration().Migration()

    migration.run()  # act

    assert "subscription_monthly_accumulation" in _table_names()
    assert "agreement_monthly_accumulation" in _table_names()
