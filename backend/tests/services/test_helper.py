import asyncio

import pytest

from mpt_usage_reporting_extension.services.helper import chunked


async def _stream(*subscription_ids):
    await asyncio.sleep(0)
    for subscription_id in subscription_ids:
        yield subscription_id


@pytest.mark.parametrize(
    ("subscription_ids", "expected"),
    [
        ((), []),
        (("SUB-1", "SUB-2"), [["SUB-1", "SUB-2"]]),
        (("SUB-1", "SUB-2", "SUB-3"), [["SUB-1", "SUB-2"], ["SUB-3"]]),
    ],
)
async def test_chunked_groups_ids_into_lists_of_at_most_size(subscription_ids, expected):
    result = [chunk async for chunk in chunked(_stream(*subscription_ids), 2)]

    assert result == expected
