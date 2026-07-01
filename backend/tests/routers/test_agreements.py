from http import HTTPStatus

import pytest
from mpt_api_client.exceptions import MPTError, MPTHttpError, MPTMaxRetryError
from mpt_api_client.resources.commerce.agreements import Agreement
from mpt_extension_sdk.api.context import APIContext
from mpt_extension_sdk.api.errors import (
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    UpstreamServiceError,
)

from mpt_usage_reporting_extension.routers.api.agreements import get_agreement, sync_agreement


@pytest.fixture
def agreement_ctx(mocker):
    get_by_id = mocker.AsyncMock()
    ctx = mocker.Mock(spec=APIContext)
    ctx.mpt_api_service = mocker.Mock(agreements=mocker.Mock(get_by_id=get_by_id))
    return ctx


async def test_get_reads_marketplace(agreement_ctx, agreement_payload, mocker):
    agreement = mocker.Mock(spec=Agreement)
    agreement.to_dict.return_value = agreement_payload
    agreement_ctx.mpt_api_service.agreements.get_by_id.return_value = agreement

    result = await get_agreement("AGR-1234-5678", agreement_ctx)

    agreement_ctx.mpt_api_service.agreements.get_by_id.assert_awaited_once_with("AGR-1234-5678")
    assert result.payload == agreement_payload


async def test_sync_reads_marketplace(agreement_ctx, agreement_payload, mocker):
    agreement = mocker.Mock(spec=Agreement)
    agreement.to_dict.return_value = agreement_payload
    agreement_ctx.mpt_api_service.agreements.get_by_id.return_value = agreement

    result = await sync_agreement("AGR-1234-5678", agreement_ctx)

    agreement_ctx.mpt_api_service.agreements.get_by_id.assert_awaited_once_with("AGR-1234-5678")
    assert result.payload == agreement_payload


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (HTTPStatus.NOT_FOUND, NotFoundError),
        (HTTPStatus.UNAUTHORIZED, UnauthorizedError),
        (HTTPStatus.FORBIDDEN, ForbiddenError),
        (HTTPStatus.INTERNAL_SERVER_ERROR, UpstreamServiceError),
        (HTTPStatus.BAD_REQUEST, UpstreamServiceError),
    ],
)
async def test_get_maps_upstream_http_errors(agreement_ctx, status_code, expected):
    agreement_ctx.mpt_api_service.agreements.get_by_id.side_effect = MPTHttpError(
        status_code=status_code, message="boom", body=""
    )

    with pytest.raises(expected):
        await get_agreement("AGR-1234-5678", agreement_ctx)


@pytest.mark.parametrize("exc", [MPTMaxRetryError("boom", 3), MPTError("boom")])
async def test_get_maps_network_errors_to_upstream(agreement_ctx, exc):
    agreement_ctx.mpt_api_service.agreements.get_by_id.side_effect = exc

    with pytest.raises(UpstreamServiceError):
        await get_agreement("AGR-1234-5678", agreement_ctx)


async def test_get_not_found_includes_agreement_id(agreement_ctx):
    agreement_ctx.mpt_api_service.agreements.get_by_id.side_effect = MPTHttpError(
        status_code=HTTPStatus.NOT_FOUND, message="missing", body=""
    )

    with pytest.raises(NotFoundError) as exc_info:
        await get_agreement("AGR-1234-5678", agreement_ctx)

    assert "AGR-1234-5678" in exc_info.value.detail


async def test_sync_maps_not_found(agreement_ctx):
    agreement_ctx.mpt_api_service.agreements.get_by_id.side_effect = MPTHttpError(
        status_code=HTTPStatus.NOT_FOUND, message="missing", body=""
    )

    with pytest.raises(NotFoundError):
        await sync_agreement("AGR-1234-5678", agreement_ctx)


async def test_sync_maps_upstream_failure(agreement_ctx):
    agreement_ctx.mpt_api_service.agreements.get_by_id.side_effect = MPTError("boom")

    with pytest.raises(UpstreamServiceError):
        await sync_agreement("AGR-1234-5678", agreement_ctx)
