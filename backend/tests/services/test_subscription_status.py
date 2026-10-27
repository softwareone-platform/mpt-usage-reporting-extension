from types import SimpleNamespace

import pytest
from mpt_api_client.exceptions import MPTError

from mpt_usage_reporting_extension.services.subscription_status import (
    StatusCheck,
    WritableSubscriptionFilter,
)


class _StubSubscriptionListing:
    """commerce.subscriptions stub: records each RQL query and streams the ids set on it."""

    def __init__(self):
        self.ids: list[str] = []
        self.error: Exception | None = None
        self.queries: list[str] = []
        self.fields: tuple[str, ...] = ()

    def filter(self, query):
        self.queries.append(str(query))
        return self

    def select(self, *fields):
        self.fields = fields
        return self

    async def iterate(self):
        if self.error is not None:
            raise self.error
        for subscription_id in self.ids:
            yield SimpleNamespace(id=subscription_id)


@pytest.fixture
def listing():
    return _StubSubscriptionListing()


async def _checks(writable_filter, subscription_ids):
    return [check async for check in writable_filter.check(subscription_ids)]


async def test_check_marks_only_the_listed_ids_writable(listing):
    listing.ids = ["SUB-1"]

    result = await _checks(WritableSubscriptionFilter(listing), ["SUB-1", "SUB-2"])

    assert result == [StatusCheck("SUB-1", writable=True), StatusCheck("SUB-2", writable=False)]


async def test_check_lists_the_chunk_in_active_or_suspended_status(listing):
    await _checks(WritableSubscriptionFilter(listing), ["SUB-1", "SUB-2"])  # act

    assert (listing.queries, listing.fields) == (
        ["and(in(id,('SUB-1','SUB-2')),in(status,('Active','Suspended')))"],
        ("id",),
    )


async def test_check_lists_one_chunk_at_a_time(listing):
    writable_filter = WritableSubscriptionFilter(listing, chunk_size=2)

    await _checks(writable_filter, ["SUB-1", "SUB-2", "SUB-3"])  # act

    assert listing.queries == [
        "and(in(id,('SUB-1','SUB-2')),in(status,('Active','Suspended')))",
        "and(in(id,('SUB-3')),in(status,('Active','Suspended')))",
    ]


async def test_check_carries_the_listing_error_on_each_id_of_the_chunk(listing):
    error = MPTError("boom")
    listing.error = error

    result = await _checks(WritableSubscriptionFilter(listing), ["SUB-1", "SUB-2"])

    assert result == [
        StatusCheck("SUB-1", writable=False, error=error),
        StatusCheck("SUB-2", writable=False, error=error),
    ]
