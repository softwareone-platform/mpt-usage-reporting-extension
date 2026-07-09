from mpt_usage_reporting_extension.settings import ExtensionSettings


def test_load_reads_product_ids(monkeypatch):
    monkeypatch.setenv("MPT_PRODUCTS_IDS", "PRD-1,PRD-2")

    result = ExtensionSettings.load()

    assert result.product_ids == ("PRD-1", "PRD-2")


def test_load_reads_database_url(monkeypatch):
    database_url = "postgresql://postgres:postgres@postgres:5432/usage_reporting"
    monkeypatch.setenv("MPT_PRODUCTS_IDS", "PRD-1")
    monkeypatch.setenv("MPT_DATABASE_URL", database_url)

    result = ExtensionSettings.load()

    assert result.database_url == database_url


def test_load_defaults_database_url_to_empty(monkeypatch):
    monkeypatch.setenv("MPT_PRODUCTS_IDS", "PRD-1")
    monkeypatch.delenv("MPT_DATABASE_URL", raising=False)

    result = ExtensionSettings.load()

    assert not result.database_url


def test_load_reads_teams_settings(monkeypatch):
    monkeypatch.setenv("MPT_PRODUCTS_IDS", "PRD-1")
    monkeypatch.setenv("MPT_MSTEAMS_WEBHOOK_URL", "https://hooks.example.com/workflow")
    monkeypatch.setenv("MPT_TEAMS_NOTIFICATIONS_ENABLED", "false")

    result = ExtensionSettings.load()

    assert result.teams_webhook_url == "https://hooks.example.com/workflow"
    assert result.teams_notifications_enabled is False


def test_load_defaults_teams_settings(monkeypatch):
    monkeypatch.setenv("MPT_PRODUCTS_IDS", "PRD-1")
    monkeypatch.delenv("MPT_MSTEAMS_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("MPT_TEAMS_NOTIFICATIONS_ENABLED", raising=False)

    result = ExtensionSettings.load()

    assert not result.teams_webhook_url
    assert result.teams_notifications_enabled is True
