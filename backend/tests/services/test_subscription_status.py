import pytest
from mpt_api_client.exceptions import MPTError
from mpt_api_client.resources.commerce.subscriptions import Subscription

from mpt_usage_reporting_extension.services.subscription_status import SubscriptionStatusLookup


class _StubSubscriptionListing:
    """commerce.subscriptions stub: records the RQL query and streams the subscriptions set."""

    def __init__(self):
        self.subscriptions: list[dict[str, str]] = []
        self.error: Exception | None = None
        self.query = None
        self.fields: tuple[str, ...] = ()

    def filter(self, query):
        self.query = str(query)
        return self

    def select(self, *fields):
        self.fields = fields
        return self

    async def iterate(self):
        if self.error is not None:
            raise self.error
        for payload in self.subscriptions:
            yield Subscription(payload)


@pytest.fixture
def listing():
    return _StubSubscriptionListing()


async def test_statuses_maps_each_listed_id_to_its_status(listing):
    listing.subscriptions = [
        {"id": "SUB-1", "status": "Active"},
        {"id": "SUB-2", "status": "Terminated"},
    ]

    result = await SubscriptionStatusLookup(listing).statuses(["SUB-1", "SUB-2", "SUB-3"])

    assert result == {"SUB-1": "Active", "SUB-2": "Terminated"}


async def test_statuses_lists_the_ids_selecting_id_and_status(listing):
    await SubscriptionStatusLookup(listing).statuses(["SUB-1", "SUB-2"])  # act

    assert (listing.query, listing.fields) == ("in(id,('SUB-1','SUB-2'))", ("id", "status"))


@pytest.mark.parametrize("error", [MPTError("boom"), ValueError("malformed response")])
async def test_statuses_propagates_listing_errors(listing, error):
    listing.error = error

    with pytest.raises(type(error)):
        await SubscriptionStatusLookup(listing).statuses(["SUB-1"])
