from typing import override

from mpt_extension_sdk.pipeline import BaseStep, EventBaseContext


class LogStatementStep(BaseStep):
    """Log the statement selected by the runtime context."""

    @override
    async def process(self, ctx: EventBaseContext) -> None:
        """Log the statement id from the runtime context."""
        ctx.logger.info("%s - Statement queued for processing.", ctx.meta.object_id)
