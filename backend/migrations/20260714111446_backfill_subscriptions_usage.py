"""Backfill the subscriptions usage buckets by running a full recalculate."""

import asyncio
from typing import override

from mpt_tool.migration import DataBaseMigration

from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.mpt_client import build_service
from mpt_usage_reporting_extension.pipeline import UsageReportingPipeline
from mpt_usage_reporting_extension.services.execution_notifier import build_execution_notifier
from mpt_usage_reporting_extension.settings import ExtensionSettings


class Migration(DataBaseMigration):
    """Run the initial full recalculate to backfill the subscriptions usage data."""

    @override
    def run(self) -> None:
        """Recalculate every configured product with no date window (full rebuild)."""
        self.log.info("Backfilling subscriptions usage via full recalculate")
        settings = ExtensionSettings.load()
        ctx = RunContext(
            api_service=build_service(),
            window=None,
            product_ids=settings.product_ids,
            notifier=build_execution_notifier(settings),
        )
        parameters = {"product_id": None, "seller_id": None}
        asyncio.run(UsageReportingPipeline(ctx).recalculate(None, parameters))
