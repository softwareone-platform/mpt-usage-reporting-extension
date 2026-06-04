"""Run context shared across the usage reporting ``run`` command."""

from dataclasses import dataclass, field

from mpt_api_client import MPTClient
from mpt_api_client.resources.billing.statements import Statement

from mpt_usage_reporting_extension.window import RunWindow


@dataclass
class RunContext:
    """Mutable context for a statement-selection run.

    Carries the API client and run inputs and accumulates the selected statements so
    downstream reporting reads everything from a single object.
    """

    api_client: MPTClient
    window: RunWindow
    product_ids: tuple[str, ...]
    seller_id: str = ""
    statements: list[Statement] = field(default_factory=list)
