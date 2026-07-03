import logging

from mpt_extension_sdk.api import APIResponse
from mpt_extension_sdk.api.context import APIContext
from mpt_extension_sdk.api.errors import NotFoundError
from mpt_extension_sdk.routing import APIRouter

from mpt_usage_reporting_extension.persistence.sqlite.database import (
    SqliteDatabase,
    resolve_db_path,
)
from mpt_usage_reporting_extension.utils import sanitize_id  # noqa: WPS347

logger = logging.getLogger(__name__)

executions_router = APIRouter(prefix="/api/v2/executions")


@executions_router.get(path="/{execution_id}", name="executions-get")
async def get_execution(execution_id: str, ctx: APIContext) -> APIResponse:
    """Return one command execution by id, for status polling."""
    safe_id = sanitize_id(execution_id)
    logger.info("API get execution %s", safe_id)
    try:
        row_id = int(execution_id)
    except ValueError:
        raise NotFoundError(f"Execution {execution_id} not found") from None
    async with SqliteDatabase(resolve_db_path()) as db:
        detail = await db.execution_repository().get(row_id)
    if detail is None:
        raise NotFoundError(f"Execution {execution_id} not found")
    return APIResponse.ok(payload=detail.to_payload())
