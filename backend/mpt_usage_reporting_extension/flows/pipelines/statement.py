from typing import override

from mpt_extension_sdk.pipeline import BasePipeline, BaseStep

from mpt_usage_reporting_extension.flows.steps.log_statement import LogStatementStep


class StatementPipeline(BasePipeline):
    """Pipeline run for each automated statement status change."""

    @override
    @property
    def steps(self) -> list[BaseStep]:
        return [LogStatementStep()]
