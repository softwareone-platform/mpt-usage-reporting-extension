from dataclasses import dataclass

from mpt_extension_sdk.services.mpt_api_service import MPTAPIService

from mpt_usage_reporting_extension.window import RunWindow


@dataclass
class RunContext:
    """The inputs for a single statement-selection run."""

    api_service: MPTAPIService
    window: RunWindow | None
    product_ids: tuple[str, ...]
    seller_id: str = ""
    subscription_ids: tuple[str, ...] | None = None
