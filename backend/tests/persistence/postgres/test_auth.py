from dataclasses import replace

import pytest

from mpt_usage_reporting_extension.persistence.postgres import auth
from mpt_usage_reporting_extension.persistence.postgres.connection import ConnectionOptions


@pytest.fixture
def options():
    return ConnectionOptions(host="host", dbname="db", user="user")


@pytest.fixture
def credential(mocker):
    stub = mocker.MagicMock()
    stub.get_token.return_value.token = "token-value"
    mocker.patch.object(auth, "DefaultAzureCredential", return_value=stub)
    return stub


@pytest.mark.parametrize("flag", ["1", "true", "TRUE", "Yes", "on", "  true  "])
def test_resolve_auth_truthy_flag_selects_azure(monkeypatch, flag):
    monkeypatch.setenv("MPT_DATABASE_ENTRA_AUTH", flag)

    result = auth.resolve_auth()

    assert isinstance(result, auth.AzureCredentialAuth)


@pytest.mark.parametrize("flag", ["", "0", "false", "no", "off", "banana"])
def test_resolve_auth_falsy_flag_selects_dsn(monkeypatch, flag):
    monkeypatch.setenv("MPT_DATABASE_ENTRA_AUTH", flag)

    result = auth.resolve_auth()

    assert isinstance(result, auth.DsnAuth)


def test_resolve_auth_unset_selects_dsn(monkeypatch):
    monkeypatch.delenv("MPT_DATABASE_ENTRA_AUTH", raising=False)

    result = auth.resolve_auth()

    assert isinstance(result, auth.DsnAuth)


def test_dsn_auth_passes_options_through(options):
    result = auth.DsnAuth().apply(options)

    assert result is options


async def test_dsn_auth_async_passes_options_through(options):
    result = await auth.DsnAuth().apply_async(options)

    assert result is options


def test_azure_auth_sets_token_password(options, credential):
    result = auth.AzureCredentialAuth().apply(options)

    assert result == replace(options, password="token-value", sslmode="require")
    credential.get_token.assert_called_once_with(
        "https://ossrdbms-aad.database.windows.net/.default",
    )
    credential.close.assert_called_once_with()


async def test_azure_auth_async_sets_token_password(options, credential):
    result = await auth.AzureCredentialAuth().apply_async(options)

    assert result == replace(options, password="token-value", sslmode="require")
    credential.close.assert_called_once_with()


@pytest.mark.parametrize("sslmode", ["require", "verify-ca", "verify-full"])
def test_azure_auth_keeps_tls_sslmode(options, credential, sslmode):
    result = auth.AzureCredentialAuth().apply(replace(options, sslmode=sslmode))

    assert result.sslmode == sslmode


@pytest.mark.parametrize("sslmode", ["disable", "allow", "prefer"])
def test_azure_auth_rejects_non_tls_sslmode(options, credential, sslmode):
    with pytest.raises(RuntimeError, match=sslmode):
        auth.AzureCredentialAuth().apply(replace(options, sslmode=sslmode))


@pytest.mark.parametrize("sslmode", ["disable", "allow", "prefer"])
async def test_azure_auth_async_rejects_non_tls_sslmode(options, credential, sslmode):
    with pytest.raises(RuntimeError, match=sslmode):
        await auth.AzureCredentialAuth().apply_async(replace(options, sslmode=sslmode))
