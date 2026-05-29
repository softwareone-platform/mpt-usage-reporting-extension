from mpt_usage_reporting_extension.flows.pipelines.purchase import PurchasePipeline
from mpt_usage_reporting_extension.flows.steps.log_order import LogOrderStep


def test_purchase():
    result = PurchasePipeline().steps

    assert len(result) == 1
    assert isinstance(result[0], LogOrderStep) is True
