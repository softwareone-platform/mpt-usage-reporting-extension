from mpt_extension_sdk.routing import PlugRouter
from mpt_extension_sdk.routing.plugs import Plug

subscriptions_router = PlugRouter()


@subscriptions_router.register()
def subscription_plugs() -> list[Plug]:
    """Declare subscription UI plugs served from the static asset bridge."""
    return [
        Plug(
            id="subscriptions-subscription-actions",
            name="Recalculate usage",
            description=(
                "Recalculate the subscription's usage accumulations and show the run status."
            ),
            socket="portal.commerce.subscriptions.subscription.actions",
            href="/static/subscriptions-subscription-actions/index.js",
        ),
    ]
