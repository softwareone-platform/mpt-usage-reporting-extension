import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

import mpt_usage_reporting_extension
from mpt_usage_reporting_extension.persistence.sqlite import database


def test_resolve_db_path_default(monkeypatch):
    monkeypatch.delenv("DEFAULT_DB_PATH", raising=False)

    path = database.resolve_db_path()  # act

    assert path.name == "storage.db"
    assert path.parent == Path(mpt_usage_reporting_extension.__file__).parent.parent


def test_resolve_db_path_env_override(monkeypatch, tmp_path):
    target = tmp_path / "custom.db"
    monkeypatch.setenv("MPT_DB_PATH", str(target))

    path = database.resolve_db_path()  # act

    assert path == target


def test_connection_exposes_both_tables(db):
    rows = db.connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'",
    ).fetchall()  # act

    names = {row["name"] for row in rows}
    assert "subscription_monthly_accumulation" in names
    assert "agreement_monthly_accumulation" in names


def test_decimal_roundtrip_preserves_precision(db):
    db.connection.execute(
        "INSERT INTO agreement_monthly_accumulation "
        "(agreement_id, year, month, ppx1, spx1, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("AGR-1", 2026, 5, Decimal("0.1"), Decimal("0.2"), "2026-05-07T08:05:00Z"),
    )

    row = db.connection.execute(
        "SELECT ppx1, spx1 FROM agreement_monthly_accumulation",
    ).fetchone()  # act

    assert isinstance(row["ppx1"], Decimal)
    assert row["ppx1"] == Decimal("0.1")
    assert row["spx1"] == Decimal("0.2")


def test_context_manager_closes_connection(tmp_path):
    with database.SqliteDatabase(tmp_path / "storage.db") as db:
        connection = db.connection

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")  # act
