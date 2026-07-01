import logging
from http import HTTPStatus

from mpt_api_client.exceptions import MPTError, MPTHttpError
from mpt_extension_sdk.api import APIResponse
from mpt_extension_sdk.api.context import APIContext
from mpt_extension_sdk.api.errors import (
    APIError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    UpstreamServiceError,
)
from mpt_extension_sdk.routing import APIRouter

from mpt_usage_reporting_extension.utils import sanitize_id  # noqa: WPS347

logger = logging.getLogger(__name__)

agreements_router = APIRouter(prefix="/api/v2/agreements")


def _as_api_error(exc: MPTError, *, resource: str) -> APIError:
    """Translate an upstream MPT client error into an SDK API error."""
    if isinstance(exc, MPTHttpError):
        if exc.status_code == HTTPStatus.NOT_FOUND:
            return NotFoundError(f"{resource} not found")
        if exc.status_code == HTTPStatus.UNAUTHORIZED:
            return UnauthorizedError()
        if exc.status_code == HTTPStatus.FORBIDDEN:
            return ForbiddenError()
    return UpstreamServiceError()


@agreements_router.get(path="/{agreement_id}", name="agreements-get")
async def get_agreement(agreement_id: str, ctx: APIContext) -> APIResponse:
    """Return the current Marketplace data for a single agreement."""
    safe_id = sanitize_id(agreement_id)
    logger.info("API get agreement %s", safe_id)
    try:
        agreement = await ctx.mpt_api_service.agreements.get_by_id(agreement_id)
    except MPTError as exc:
        logger.warning("Upstream error fetching agreement %s: %s", safe_id, exc)
        raise _as_api_error(exc, resource=f"Agreement {agreement_id}") from exc
    logger.info("API get agreement %s succeeded", safe_id)
    return APIResponse.ok(payload=agreement.to_dict())


@agreements_router.post(path="/{agreement_id}/sync", name="agreements-sync")
async def sync_agreement(agreement_id: str, ctx: APIContext) -> APIResponse:
    """Synchronize an agreement view with the current Marketplace data."""
    safe_id = sanitize_id(agreement_id)
    logger.info("API sync agreement %s", safe_id)
    try:
        agreement = await ctx.mpt_api_service.agreements.get_by_id(agreement_id)
    except MPTError as exc:
        logger.warning("Upstream error syncing agreement %s: %s", safe_id, exc)
        raise _as_api_error(exc, resource=f"Agreement {agreement_id}") from exc
    logger.info("API sync agreement %s succeeded", safe_id)
    return APIResponse.ok(payload=agreement.to_dict())
