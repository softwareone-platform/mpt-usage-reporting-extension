import asyncio

from mpt_usage_reporting_extension.steps import logged_step


def test_logged_step_keeps_the_wrapped_stage_identity():
    @logged_step("cleanup")
    async def stage():
        """Prune the retention window."""
        await asyncio.sleep(0)

    result = stage

    assert result.__name__ == "stage"
    assert result.__doc__ == "Prune the retention window."
