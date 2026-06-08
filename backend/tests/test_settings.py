from mpt_usage_reporting_extension.settings import ExtensionSettings


def test_load_reads_product_ids(monkeypatch):
    monkeypatch.setenv("MPT_PRODUCTS_IDS", "PRD-1,PRD-2")

    result = ExtensionSettings.load()

    assert result.product_ids == ("PRD-1", "PRD-2")
