import sqlite3
from contextlib import closing
from importlib import util
from pathlib import Path

_MIGRATION_PATH = next(
    (Path(__file__).parents[2] / "migrations").glob("*_create_accumulation_tables.py"),
)


def _load_migration():
    spec = util.spec_from_file_location("create_accumulation_tables", _MIGRATION_PATH)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _table_names(db_path) -> set[str]:
    with closing(sqlite3.connect(db_path)) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'",
        ).fetchall()
    return {row[0] for row in rows}


def test_migration_run_creates_both_tables(tmp_path, monkeypatch):
    db_path = tmp_path / "storage.db"
    migration_module = _load_migration()
    monkeypatch.setattr(migration_module, "_DB_PATH", db_path)
    migration = migration_module.Migration()

    migration.run()  # act

    assert "subscription_monthly_accumulation" in _table_names(db_path)
    assert "agreement_monthly_accumulation" in _table_names(db_path)
