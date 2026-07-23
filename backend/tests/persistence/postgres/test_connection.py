import pytest

from mpt_usage_reporting_extension.persistence.postgres.connection import ConnectionOptions


def test_from_dsn_url_populates_all_fields():
    result = ConnectionOptions.from_dsn(
        "postgresql://user:pass@host:5433/db?sslmode=require&connect_timeout=5"
    )

    assert result == ConnectionOptions(
        host="host",
        port=5433,
        dbname="db",
        user="user",
        password="pass",
        sslmode="require",
        connect_timeout=5,
    )


def test_from_dsn_minimal_url_uses_defaults():
    result = ConnectionOptions.from_dsn("postgresql://host/db")

    assert result == ConnectionOptions(host="host", dbname="db")
    assert result.port == 5432
    assert result.connect_timeout == 10
    assert result.password is None


def test_from_dsn_keyword_format():
    result = ConnectionOptions.from_dsn("host=localhost dbname=usage user=postgres")

    assert result == ConnectionOptions(host="localhost", dbname="usage", user="postgres")


def test_repr_excludes_password():
    options = ConnectionOptions(host="host", dbname="db", user="user", password="secret")

    result = repr(options)

    assert "secret" not in result
    assert "password" not in result
    assert "host='host'" in result
    assert "dbname='db'" in result
    assert "user='user'" in result


def test_from_dsn_unsupported_parameter_raises():
    with pytest.raises(RuntimeError, match="application_name"):
        ConnectionOptions.from_dsn("postgresql://host/db?application_name=reporting")  # act
