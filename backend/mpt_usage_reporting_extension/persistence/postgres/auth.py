import asyncio
import os
from contextlib import closing
from dataclasses import replace
from typing import Protocol, override

from azure.identity import DefaultAzureCredential

from mpt_usage_reporting_extension.persistence.postgres.connection import ConnectionOptions

_ENTRA_AUTH_ENV_VAR = "MPT_DATABASE_ENTRA_AUTH"
_TRUTHY_VALUES = frozenset(("1", "true", "yes", "on"))
_DEFAULT_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"
_TLS_SSLMODES = frozenset(("require", "verify-ca", "verify-full"))


class DatabaseAuth(Protocol):
    """Connection-time authentication strategy for PostgreSQL connects."""

    def apply(self, options: ConnectionOptions) -> ConnectionOptions:
        """Return options authenticated for a synchronous connect (schema migrations)."""

    async def apply_async(self, options: ConnectionOptions) -> ConnectionOptions:
        """Return authenticated options without blocking the event loop."""


class DsnAuth(DatabaseAuth):
    """Credentials come from the DSN itself; options pass through unchanged."""

    @override
    def apply(self, options: ConnectionOptions) -> ConnectionOptions:
        """Return the options unchanged."""
        return options

    @override
    async def apply_async(self, options: ConnectionOptions) -> ConnectionOptions:
        """Return the options unchanged."""
        return options


class AzureCredentialAuth(DatabaseAuth):
    """Entra ID token set as the connection password, fetched per connect.

    Azure Database for PostgreSQL flexible server maps managed identities to database
    roles that authenticate with a short-lived Entra access token supplied as the
    connection password. The token is only needed at connect time; an open session
    keeps working after the token expires, so a fresh token is fetched per connect.
    """

    def __init__(self, scope: str = _DEFAULT_SCOPE) -> None:
        self._scope = scope

    @override
    def apply(self, options: ConnectionOptions) -> ConnectionOptions:
        """Set a freshly acquired Entra token as the connection password."""
        return replace(_enforce_tls(options), password=self._fetch_token())

    @override
    async def apply_async(self, options: ConnectionOptions) -> ConnectionOptions:
        """Acquire the token off the event loop, then set it as the password."""
        options = _enforce_tls(options)
        return replace(options, password=await asyncio.to_thread(self._fetch_token))

    def _fetch_token(self) -> str:
        with closing(DefaultAzureCredential()) as credential:
            return str(credential.get_token(self._scope).token)


def _enforce_tls(options: ConnectionOptions) -> ConnectionOptions:
    """Require a TLS sslmode so the Entra token never travels over plaintext."""
    if options.sslmode is None:
        return replace(options, sslmode="require")
    if options.sslmode not in _TLS_SSLMODES:
        raise RuntimeError(f"Entra ID database auth requires a TLS sslmode, got: {options.sslmode}")
    return options


def resolve_auth() -> DatabaseAuth:
    """Pick the auth strategy from MPT_DATABASE_ENTRA_AUTH (truthy -> AzureCredentialAuth)."""
    if _entra_auth_enabled():
        return AzureCredentialAuth()
    return DsnAuth()


def _entra_auth_enabled() -> bool:
    return os.environ.get(_ENTRA_AUTH_ENV_VAR, "").strip().lower() in _TRUTHY_VALUES
