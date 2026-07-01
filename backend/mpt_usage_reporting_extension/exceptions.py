class UpstreamAPIError(Exception):
    """A Marketplace API call made as an API client failed upstream."""


class UpstreamStatementError(UpstreamAPIError):
    """Selecting statements or streaming their charges failed upstream."""


class UpstreamSubscriptionError(UpstreamAPIError):
    """Querying commerce subscriptions failed upstream."""
