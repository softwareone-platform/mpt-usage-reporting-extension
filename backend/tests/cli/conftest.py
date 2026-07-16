import pytest
from typer.testing import CliRunner


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def mock_setup_observability(mocker):
    return mocker.patch("mpt_usage_reporting_extension.cli._app.setup_observability", autospec=True)
