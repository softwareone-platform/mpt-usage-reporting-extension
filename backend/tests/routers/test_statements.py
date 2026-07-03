from mpt_extension_sdk.api.models.events import TaskEvent
from mpt_extension_sdk.pipeline import EventBaseContext

from mpt_usage_reporting_extension.routers.events import statement


async def test_status_changed_executes_pipeline(mocker):
    event = mocker.Mock(spec=TaskEvent, id="EVT-1")
    event.object = mocker.Mock(id="BIL-1")
    context = mocker.Mock(spec=EventBaseContext)
    pipeline = mocker.patch.object(statement, "StatementPipeline", autospec=True)

    await statement.handle_statement_status_changed(event, context)  # act

    pipeline.return_value.execute.assert_awaited_once_with(context)
