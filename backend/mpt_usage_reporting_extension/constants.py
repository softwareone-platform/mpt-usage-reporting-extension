from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parents[1] / "storage.db"

# Synthetic subscription-id prefix for agreement-level (non-subscription) accumulation buckets.
ADDITIONAL_AGREEMENT_PREFIX = "agreement_additional_"
