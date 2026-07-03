import datetime as dt
import logging
from http import HTTPStatus

from mpt_extension_sdk.api import APIResponse
from mpt_extension_sdk.api.context import APIContext
from mpt_extension_sdk.api.errors import APIError, NotFoundError
from mpt_extension_sdk.routing import APIRouter

from mpt_usage_reporting_extension.persistence.sqlite.database import (
    SqliteDatabase,
    resolve_db_path,
)
from mpt_usage_reporting_extension.services.recalculate_launcher import (
    RecalculateInProgressError,
    launcher,
)
from mpt_usage_reporting_extension.utils import sanitize_id  # noqa: WPS347
from mpt_usage_reporting_extension.window import RunWindow, resolve_window

logger = logging.getLogger(__name__)

subscriptions_router = APIRouter(prefix="/api/v2/subscriptions")

_WINDOW_MONTHS_BACK = 12
_MONTHS_PER_YEAR = 12


def _recalculate_window(today: dt.date) -> RunWindow:
    """Resolve the last-13-months window: first day of the month 12 months back to today."""
    months = today.year * _MONTHS_PER_YEAR + today.month - 1 - _WINDOW_MONTHS_BACK
    year, month_index = divmod(months, _MONTHS_PER_YEAR)
    return resolve_window(from_date=dt.date(year, month_index + 1, 1), till_date=today)


@subscriptions_router.get(
    path="/{subscription_id}/accumulations", name="subscriptions-accumulations"
)
async def get_accumulations(subscription_id: str, ctx: APIContext) -> APIResponse:
    """Return the subscription's accumulated months with their last-updated timestamps."""
    safe_id = sanitize_id(subscription_id)
    logger.info("API accumulations subscription %s", safe_id)
    async with SqliteDatabase(resolve_db_path()) as db:
        repository = db.subscription_repository()
        accumulations = [
            period.to_payload() async for period in repository.periods(subscription_id)
        ]
    return APIResponse.ok(payload={"accumulations": accumulations})


@subscriptions_router.post(path="/{subscription_id}/recalculate", name="subscriptions-recalculate")
async def recalculate_subscription(subscription_id: str, ctx: APIContext) -> APIResponse:
    """Launch a background recalculate for the subscription and return the created execution."""
    safe_id = sanitize_id(subscription_id)
    logger.info("API recalculate subscription %s", safe_id)
    window = _recalculate_window(dt.datetime.now(tz=dt.UTC).date())
    try:
        execution_id = await launcher.start(subscription_id, window)
    except RecalculateInProgressError as exc:
        logger.info("API recalculate subscription %s rejected: already running", safe_id)
        raise APIError(
            str(exc),
            status_code=HTTPStatus.CONFLICT,
            title="Conflict",
        ) from exc
    logger.info("API recalculate subscription %s started execution %s", safe_id, execution_id)
    async with SqliteDatabase(resolve_db_path()) as db:
        detail = await db.execution_repository().get(execution_id)
    if detail is None:  # pragma: no cover - the row was just inserted by the launcher
        raise NotFoundError(f"Execution {execution_id} not found")
    return APIResponse.ok(payload=detail.to_payload())
