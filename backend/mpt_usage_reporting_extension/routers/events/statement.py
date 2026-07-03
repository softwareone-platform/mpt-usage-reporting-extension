import logging

from mpt_extension_sdk.api.models.events import TaskEvent
from mpt_extension_sdk.pipeline import EventBaseContext
from mpt_extension_sdk.routing import EventRouter

from mpt_usage_reporting_extension.flows.pipelines.statement import StatementPipeline

logger = logging.getLogger(__name__)

statements_router = EventRouter(prefix="/events/v2/statements")


@statements_router.task(
    path="/status-changed",
    name="statements-status-changed",
    event="platform.billing.statement.status_changed",
    condition="and(eq(billingType,Automated),or(eq(status,Cancelled),eq(status,Issued)))",
)
async def handle_statement_status_changed(event: TaskEvent, context: EventBaseContext) -> None:
    """Process automated statement Issued/Cancelled status changes."""
    logger.info("Processing statement event id=%s object_id=%s", event.id, event.object.id)
    await StatementPipeline().execute(context)
