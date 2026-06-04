import pytest

from mpt_usage_reporting_extension import mpt_client


def test_build_client_uses_mpt_api_token(monkeypatch, mocker):
    api_token = "token-1"
    base_url = "https://mpt.example"
    monkeypatch.setenv("MPT_API_TOKEN", api_token)
    monkeypatch.setenv("MPT_API_BASE_URL", base_url)
    from_config = mocker.patch.object(mpt_client.MPTClient, "from_config")

    mpt_client.build_client()  # act

    from_config.assert_called_once_with(api_token=api_token, base_url=base_url)


def test_build_client_requires_token_and_url(monkeypatch):
    monkeypatch.delenv("MPT_API_TOKEN", raising=False)
    monkeypatch.delenv("MPT_API_BASE_URL", raising=False)

    with pytest.raises(RuntimeError):
        mpt_client.build_client()  # act
