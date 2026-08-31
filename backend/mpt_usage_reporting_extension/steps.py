import datetime as dt
import functools
import logging
import time
from collections.abc import Awaitable, Callable

from mpt_usage_reporting_extension.utils import format_duration

logger = logging.getLogger(__name__)

type AsyncStep[**Signature, Outcome] = Callable[Signature, Awaitable[Outcome]]


def logged_step[**Signature, Outcome](
    name: str,
) -> Callable[[AsyncStep[Signature, Outcome]], AsyncStep[Signature, Outcome]]:
    """Log an async pipeline stage's start and end, with the time it took.

    Pairs with the ``@trace_span`` already on each stage: the span records the step for
    tracing, this records it on the run's log, so a cronjob's output shows which stage is
    running and how long each one took. A stage that raises logs its end as ``FAILED`` with the
    exception's type and message at ``ERROR`` — so a severity-filtered query finds it — and the
    exception propagates untouched: the pipeline's own boundary owns the traceback.
    """

    def decorate(  # noqa: WPS430  # a decorator factory nests by construction
        step: AsyncStep[Signature, Outcome],
    ) -> AsyncStep[Signature, Outcome]:
        @functools.wraps(step)
        async def wrapper(  # noqa: WPS430
            *step_args: Signature.args, **step_kwargs: Signature.kwargs
        ) -> Outcome:
            logger.info("START %s", name)
            started = time.monotonic()
            try:
                stepped = await step(*step_args, **step_kwargs)
            except BaseException as exc:
                # BaseException: a cancelled or killed step (the cronjob's deadline) must still
                # mark its end, or the log stops mid-stage with no way to tell hung from reaped.
                logger.error(  # noqa: TRY400  # the pipeline boundary owns the traceback
                    "END   %s FAILED (%s): %s: %s",
                    name,
                    _elapsed(started),
                    type(exc).__name__,
                    exc,
                )
                raise
            logger.info("END   %s (%s)", name, _elapsed(started))
            return stepped

        return wrapper

    return decorate


def _elapsed(started: float) -> str:
    """Render the time since a monotonic start marker."""
    return format_duration(dt.timedelta(seconds=time.monotonic() - started))
