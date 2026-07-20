import pytest

from mpt_usage_reporting_extension.persistence.postgres import auth


@pytest.mark.parametrize("flag", ["1", "true", "TRUE", "Yes", "on", "  true  "])
def test_entra_auth_enabled_truthy(monkeypatch, flag):
    monkeypatch.setenv("MPT_DATABASE_ENTRA_AUTH", flag)

    result = auth.entra_auth_enabled()

    assert result is True


@pytest.mark.parametrize("flag", ["", "0", "false", "no", "off", "banana"])
def test_entra_auth_enabled_falsy(monkeypatch, flag):
    monkeypatch.setenv("MPT_DATABASE_ENTRA_AUTH", flag)

    result = auth.entra_auth_enabled()

    assert result is False


def test_entra_auth_enabled_unset(monkeypatch):
    monkeypatch.delenv("MPT_DATABASE_ENTRA_AUTH", raising=False)

    result = auth.entra_auth_enabled()

    assert result is False


def test_fetch_access_token_uses_ambient_credential(mocker):
    credential = mocker.MagicMock()
    credential.get_token.return_value.token = "token-value"
    mocker.patch.object(auth, "DefaultAzureCredential", return_value=credential)

    result = auth.fetch_access_token()

    assert result == "token-value"
    credential.get_token.assert_called_once_with(
        "https://ossrdbms-aad.database.windows.net/.default",
    )
    credential.close.assert_called_once_with()


async def test_fetch_access_token_async_delegates_off_loop(mocker):
    mocker.patch.object(auth, "fetch_access_token", return_value="async-token")

    result = await auth.fetch_access_token_async()

    assert result == "async-token"
