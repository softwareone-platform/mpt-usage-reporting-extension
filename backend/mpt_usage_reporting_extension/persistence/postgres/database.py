import os

import psycopg

_DATABASE_URL_ENV_VAR = "MPT_DATABASE_URL"


def resolve_database_url() -> str:
    """Resolve the PostgreSQL connection URL from the MPT_DATABASE_URL env var."""
    database_url = os.environ.get(_DATABASE_URL_ENV_VAR)
    if not database_url:
        raise RuntimeError("PostgreSQL connection URL is not configured; set MPT_DATABASE_URL.")
    return database_url


def connect_sync() -> psycopg.Connection:
    """Open a synchronous connection for schema migrations."""
    return psycopg.connect(resolve_database_url())
