import asyncio
import datetime as dt
import logging
from collections.abc import Callable, Mapping

from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.mpt_client import build_service
from mpt_usage_reporting_extension.persistence.sqlite.database import (
    SqliteDatabase,
    resolve_db_path,
)
from mpt_usage_reporting_extension.pipeline import UsageReportingPipeline
from mpt_usage_reporting_extension.selectors import SubscriptionSelector
from mpt_usage_reporting_extension.settings import ExtensionSettings
from mpt_usage_reporting_extension.types import Command, ExecutionStatus
from mpt_usage_reporting_extension.utils import sanitize_id  # noqa: WPS347
from mpt_usage_reporting_extension.window import RunWindow

logger = logging.getLogger(__name__)


class RecalculateInProgressError(Exception):
    """Raised when a recalculate for the subscription is already in flight."""

    def __init__(self, subscription_id: str) -> None:
        super().__init__(f"A recalculate for subscription {subscription_id} is already running")
        self.subscription_id = subscription_id


class RecalculateLauncher:
    """Run subscription recalculates as background tasks, at most one per subscription."""

    def __init__(
        self,
        pipeline_factory: Callable[[RunContext], UsageReportingPipeline] = UsageReportingPipeline,
    ) -> None:
        self._pipeline_factory = pipeline_factory
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._pending: set[str] = set()

    def is_running(self, subscription_id: str) -> bool:
        """Return whether a recalculate for the subscription is pending or still in flight."""
        if subscription_id in self._pending:
            return True
        task = self._tasks.get(subscription_id)
        return task is not None and not task.done()

    async def start(self, subscription_id: str, window: RunWindow) -> int:
        """Open a running execution row, spawn the recalculate task, and return the row id.

        The subscription is reserved synchronously before the first await, so two concurrent
        ``start`` calls cannot both pass the guard; the loser raises
        ``RecalculateInProgressError``. The task keeps a strong reference in the
        per-subscription map until it finishes, which is what ``is_running`` reads.
        """
        if self.is_running(subscription_id):
            raise RecalculateInProgressError(subscription_id)
        self._pending.add(subscription_id)
        try:  # noqa: WPS501  # release the reservation even when the start fails
            return await self._start_reserved(subscription_id, window)
        finally:
            self._pending.discard(subscription_id)

    async def _start_reserved(self, subscription_id: str, window: RunWindow) -> int:
        """Insert the execution row and spawn the task for an already-reserved subscription.

        A startup failure after the row was inserted (e.g. missing API credentials) finalises
        the row as failed before re-raising, so it cannot linger as running and a retry does
        not stack duplicate rows on top of a stuck one.
        """
        parameters = {
            "subscription_id": subscription_id,
            "from_date": window.start.date().isoformat(),
            "till_date": (window.end - dt.timedelta(days=1)).date().isoformat(),
            "trigger": "api",
        }
        async with SqliteDatabase(resolve_db_path()) as db:
            executions = db.execution_repository()
            execution_id = await executions.start(Command.RECALCULATE, parameters)
            try:
                ctx = RunContext(
                    api_service=build_service(),
                    window=window,
                    product_ids=ExtensionSettings.load().product_ids,
                )
            except Exception as exc:
                await executions.finish(execution_id, ExecutionStatus.FAILED, {"error": str(exc)})
                raise
        self._tasks[subscription_id] = asyncio.create_task(
            self._run(subscription_id, ctx, parameters, execution_id)
        )
        return execution_id

    async def _run(
        self,
        subscription_id: str,
        ctx: RunContext,
        parameters: Mapping[str, object],
        execution_id: int,
    ) -> None:
        """Run the recalculate pipeline against the pre-started execution row.

        The pipeline's tracker finalises the row before any exception escapes (including the
        ``typer.Exit`` a partial failure raises), so failures are only logged here to keep the
        background task from surfacing "exception was never retrieved" noise.
        """
        safe_id = sanitize_id(subscription_id)
        pipeline = self._pipeline_factory(ctx)
        try:
            await pipeline.recalculate(
                SubscriptionSelector(subscription_id), parameters, execution_id=execution_id
            )
        except Exception as exc:
            # repr keeps the log meaningful for message-less exits (e.g. typer.Exit(1)).
            logger.warning(
                "Recalculate for subscription %s (execution %s) failed: %r",
                safe_id,
                execution_id,
                exc,
            )
        else:
            logger.info(
                "Recalculate for subscription %s (execution %s) finished", safe_id, execution_id
            )
        finally:
            if self._tasks.get(subscription_id) is asyncio.current_task():
                self._tasks.pop(subscription_id, None)


launcher = RecalculateLauncher()
