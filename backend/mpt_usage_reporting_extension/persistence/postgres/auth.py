"""Entra ID (Azure AD) token acquisition for PostgreSQL connections.

Azure Database for PostgreSQL flexible server maps managed identities to database
roles that authenticate with a short-lived Entra access token supplied as the
connection password. The token is only needed at connect time; an open session
keeps working after the token expires, so callers fetch a fresh token per connect.
"""

import asyncio
import os
from contextlib import closing

from azure.identity import DefaultAzureCredential

_ENTRA_AUTH_ENV_VAR = "MPT_DATABASE_ENTRA_AUTH"
# OAuth scope for Azure Database for PostgreSQL flexible server Entra authentication.
_AAD_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"
_TRUTHY_VALUES = frozenset(("1", "true", "yes", "on"))


def entra_auth_enabled() -> bool:
    """Return whether connections should authenticate with an Entra ID token."""
    return os.environ.get(_ENTRA_AUTH_ENV_VAR, "").strip().lower() in _TRUTHY_VALUES


def fetch_access_token() -> str:
    """Acquire an Entra ID access token for PostgreSQL using the ambient credential."""
    with closing(DefaultAzureCredential()) as credential:
        return credential.get_token(_AAD_SCOPE).token


async def fetch_access_token_async() -> str:
    """Acquire the Entra ID token off the event loop so async connects don't block."""
    return await asyncio.to_thread(fetch_access_token)
