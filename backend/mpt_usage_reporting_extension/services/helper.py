from collections.abc import AsyncIterable, AsyncIterator, Iterable


async def as_async_iterator(source: AsyncIterable[str] | Iterable[str]) -> AsyncIterator[str]:
    """Adapt a sync or async iterable of subscription ids to a single async iterator, lazily."""
    if isinstance(source, AsyncIterable):
        async for subscription_id in source:
            yield subscription_id
    else:
        for subscription_id in source:
            yield subscription_id
