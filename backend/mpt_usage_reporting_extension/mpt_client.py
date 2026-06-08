import os

from mpt_api_client import MPTClient


def build_client() -> MPTClient:
    """Build a synchronous MPT API client from the MPT API token and base URL.

    Uses ``MPT_API_TOKEN`` (an operations-scoped token with billing access), not the
    extension runtime key, which is not authorized for the billing-statements endpoint.
    """
    api_token = os.getenv("MPT_API_TOKEN")
    base_url = os.getenv("MPT_API_BASE_URL")
    if not api_token or not base_url:
        raise RuntimeError("MPT_API_TOKEN and MPT_API_BASE_URL must be set")
    return MPTClient.from_config(api_token=api_token, base_url=base_url)
