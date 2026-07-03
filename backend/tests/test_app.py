from mpt_extension_sdk.routing import (
    APIRouteDefinition,
    EventRouteDefinition,
    PlugRouteDefinition,
)

from mpt_usage_reporting_extension.app import ext_app


def test_app_registers_event_routes():
    result = {route.path: route for route in ext_app.routes}

    route = result["/events/v2/statements/status-changed"]
    assert isinstance(route, EventRouteDefinition)
    assert route.event == "platform.billing.statement.status_changed"
    assert route.condition == (
        "and(eq(billingType,Automated),or(eq(status,Cancelled),eq(status,Issued)))"
    )
    assert any(isinstance(route, PlugRouteDefinition) for route in result.values())


def test_app_registers_subscription_recalculate_routes():
    result = {route.name: route for route in ext_app.routes}

    assert isinstance(result["subscriptions-recalculate"], APIRouteDefinition)
    assert isinstance(result["executions-get"], APIRouteDefinition)
    assert (
        result["subscriptions-recalculate"].path,
        result["subscriptions-recalculate"].method,
    ) == (
        "/api/v2/subscriptions/{subscription_id}/recalculate",
        "POST",
    )
    assert (result["executions-get"].path, result["executions-get"].method) == (
        "/api/v2/executions/{execution_id}",
        "GET",
    )


def test_app_registers_subscription_accumulations_route():
    result = {route.name: route for route in ext_app.routes}

    assert isinstance(result["subscriptions-accumulations"], APIRouteDefinition)
    assert (
        result["subscriptions-accumulations"].path,
        result["subscriptions-accumulations"].method,
    ) == ("/api/v2/subscriptions/{subscription_id}/accumulations", "GET")


def test_app_generates_subscription_plug_metadata():
    result = ext_app.to_meta_config()

    assert result.plugs is not None
    plugs_by_id = {plug.id: plug.model_dump() for plug in result.plugs}
    assert plugs_by_id["subscriptions-subscription-actions"] == {
        "id": "subscriptions-subscription-actions",
        "name": "Recalculate usage",
        "description": (
            "Recalculate the subscription's usage accumulations and show the run status."
        ),
        "icon": None,
        "socket": "portal.commerce.subscriptions.subscription.actions",
        "condition": None,
        "href": "/static/subscriptions-subscription-actions/index.js",
    }
