from mpt_usage_reporting_extension.flows.pipelines.statement import StatementPipeline
from mpt_usage_reporting_extension.flows.steps.log_statement import LogStatementStep


def test_steps_contains_the_log_statement_step():
    result = StatementPipeline().steps

    assert [type(step) for step in result] == [LogStatementStep]


async def test_execute_logs_the_statement(mocker):
    ctx = mocker.Mock()
    ctx.meta.object_id = "BIL-1234-5678"

    await StatementPipeline().execute(ctx)  # act

    ctx.logger.info.assert_any_call("%s - Statement queued for processing.", "BIL-1234-5678")
