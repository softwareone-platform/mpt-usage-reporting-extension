from mpt_extension_sdk import ExtensionApp

from mpt_usage_reporting_extension.routers.api.executions import (
    executions_router as api_executions_router,
)
from mpt_usage_reporting_extension.routers.api.subscriptions import (
    subscriptions_router as api_subscriptions_router,
)
from mpt_usage_reporting_extension.routers.events.statement import (
    statements_router as events_statements_router,
)
from mpt_usage_reporting_extension.routers.plugs.subscriptions import (
    subscriptions_router as plug_subscriptions_router,
)

ext_app = ExtensionApp(prefix="", version="6.0.0")
ext_app.include_router(events_statements_router)
ext_app.include_router(api_subscriptions_router)
ext_app.include_router(api_executions_router)
ext_app.include_router(plug_subscriptions_router)
