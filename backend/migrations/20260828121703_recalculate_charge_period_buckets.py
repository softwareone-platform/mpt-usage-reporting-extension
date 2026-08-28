"""Re-bucket the usage accumulations by charge period via a full recalculate."""

import asyncio
from typing import override

from mpt_tool.migration import DataBaseMigration

from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.mpt_client import build_service
from mpt_usage_reporting_extension.pipeline import UsageReportingPipeline
from mpt_usage_reporting_extension.services.execution_notifier import build_execution_notifier
from mpt_usage_reporting_extension.settings import ExtensionSettings


class Migration(DataBaseMigration):
    """Rebuild the accumulation buckets after the charge-period bucketing change (MPT-24709).

    Historical buckets were keyed by the statement's issued/cancelled month; the accumulation
    month now comes from the charge's billing ``period.end``. A full recalculate deletes the
    stale buckets, re-fills them under the new keying, and re-pushes the estimates.
    """

    @override
    def run(self) -> None:
        """Recalculate every configured product with no date window (full rebuild)."""
        self.log.info("Re-bucketing usage accumulations via full recalculate")
        settings = ExtensionSettings.load()
        ctx = RunContext(
            api_service=build_service(),
            window=None,
            product_ids=settings.product_ids,
            notifier=build_execution_notifier(settings),
        )
        parameters = {"product_id": None, "seller_id": None}
        asyncio.run(UsageReportingPipeline(ctx).recalculate(None, parameters))
