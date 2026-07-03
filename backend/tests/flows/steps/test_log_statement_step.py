from mpt_usage_reporting_extension.flows.steps.log_statement import LogStatementStep


async def test_process_logs_the_statement_id(mocker):
    ctx = mocker.Mock()
    ctx.meta.object_id = "BIL-1234-5678"

    await LogStatementStep().process(ctx)  # act

    ctx.logger.info.assert_called_once_with(
        "%s - Statement queued for processing.", "BIL-1234-5678"
    )
