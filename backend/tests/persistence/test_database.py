import datetime as dt
from decimal import Decimal

from mpt_usage_reporting_extension.persistence import database


def test_resolve_db_path_default(monkeypatch):
    monkeypatch.delenv("USAGE_REPORTING_DB_PATH", raising=False)

    path = database.resolve_db_path()  # act

    assert path.name == "storage.db"
    assert path.parent.name == "backend"


def test_resolve_db_path_env_override(monkeypatch, tmp_path):
    target = tmp_path / "custom.db"
    monkeypatch.setenv("USAGE_REPORTING_DB_PATH", str(target))

    path = database.resolve_db_path()  # act

    assert path == target


def test_create_schema_creates_both_tables(connection):
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'",
    ).fetchall()  # act

    names = {row["name"] for row in rows}
    assert "subscription_monthly_accumulation" in names
    assert "agreement_monthly_accumulation" in names


def test_decimal_roundtrip_preserves_precision(connection):
    connection.execute(
        "INSERT INTO agreement_monthly_accumulation "
        "(agreement_id, year, month, ppx1, spx1, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("AGR-1", 2026, 5, Decimal("0.1"), Decimal("0.2"), "2026-05-07T08:05:00Z"),
    )

    row = connection.execute(
        "SELECT ppx1, spx1 FROM agreement_monthly_accumulation",
    ).fetchone()  # act

    assert isinstance(row["ppx1"], Decimal)
    assert row["ppx1"] == Decimal("0.1")
    assert row["spx1"] == Decimal("0.2")


def test_utc_now_iso_is_z_suffixed_utc():
    stamp = database.utc_now_iso()  # act

    assert stamp.endswith("Z")
    parsed = dt.datetime.fromisoformat(stamp)
    assert parsed.utcoffset() == dt.timedelta(0)
