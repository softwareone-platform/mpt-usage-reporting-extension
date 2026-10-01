class ExtensionError(Exception):
    """Base exception for this extension."""


class ConfigurationError(ExtensionError):
    """Raised when required configuration is missing or invalid."""


class DatabaseError(ExtensionError):
    """A database operation failed or the persistence layer was misused."""


class UpstreamAPIError(ExtensionError):
    """A Marketplace API call made as an API client failed upstream."""


class UpstreamStatementError(UpstreamAPIError):
    """Selecting statements or streaming their charges failed upstream."""


class UpstreamSubscriptionError(UpstreamAPIError):
    """Querying commerce subscriptions failed upstream."""


class ChargePriceError(ExtensionError):
    """A statement charge's price cannot be accumulated without guessing its amount."""


class EstimateCurrencyError(ExtensionError):
    """An estimate sums charges priced in more than one currency, so it must not be uploaded."""
