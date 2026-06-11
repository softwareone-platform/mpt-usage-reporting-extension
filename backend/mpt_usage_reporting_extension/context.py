from dataclasses import dataclass, field

from mpt_api_client.resources.billing.statements import Statement
from mpt_extension_sdk.services.mpt_api_service import MPTAPIService

from mpt_usage_reporting_extension.accumulation import ChargeTotals
from mpt_usage_reporting_extension.window import RunWindow


@dataclass
class RunContext:
    """Mutable context for a statement-selection run.

    Carries the API client and run inputs, accumulates the selected statements, and
    holds the accumulated charge totals so downstream persistence and reporting read
    everything from a single object.
    """

    api_service: MPTAPIService
    window: RunWindow
    product_ids: tuple[str, ...]
    seller_id: str = ""
    statements: list[Statement] = field(default_factory=list)
    charge_totals: ChargeTotals | None = None
