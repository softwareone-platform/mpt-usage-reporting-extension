import logging
from collections.abc import AsyncIterator
from typing import override

from mpt_api_client import RQLQuery
from mpt_extension_sdk.observability import trace_span

from mpt_usage_reporting_extension.persistence.protocols import (
    AgreementAccumulationRepository,
    SubscriptionAccumulationRepository,
)
from mpt_usage_reporting_extension.selectors import (
    AgreementSelector,
    ProductSelector,
    Selector,
    SellerSelector,
    SubscriptionSelector,
)
from mpt_usage_reporting_extension.services.scope_resolver import ScopeResolver

logger = logging.getLogger(__name__)


class DeleteOutcome:
    """Ordered ids deleted by the scope reset, plus the statement re-selection targets.

    ``statement_agreements`` carries the scope's agreement ids whose statements should be
    re-selected — registered even when the delete removed no rows, so an empty scope can
    be rebuilt (bootstrapped).
    """

    __slots__ = ("agreements", "statement_agreements", "subscriptions")

    def __init__(
        self,
        subscriptions: list[str] | None = None,
        agreements: list[str] | None = None,
        statement_agreements: frozenset[str] | None = None,
    ) -> None:
        self.subscriptions = [] if subscriptions is None else list(subscriptions)
        self.agreements = [] if agreements is None else list(agreements)
        self.statement_agreements = (
            frozenset() if statement_agreements is None else statement_agreements
        )

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DeleteOutcome):
            return NotImplemented
        return (
            self.subscriptions == other.subscriptions
            and self.agreements == other.agreements
            and self.statement_agreements == other.statement_agreements
        )

    @override
    def __hash__(self) -> int:
        return hash((
            tuple(self.subscriptions),
            tuple(self.agreements),
            self.statement_agreements,
        ))

    @override
    def __repr__(self) -> str:
        return (
            f"DeleteOutcome(subscriptions={self.subscriptions!r}, "
            f"agreements={self.agreements!r}, "
            f"statement_agreements={self.statement_agreements!r})"
        )

    @classmethod
    async def from_subscriptions(
        cls,
        subscriptions: AsyncIterator[str],
        agreements: list[str] | None = None,
    ) -> "DeleteOutcome":
        """Build an outcome by consuming a stream of deleted subscription ids."""
        return cls(
            subscriptions=[subscription_id async for subscription_id in subscriptions],
            agreements=[] if agreements is None else agreements,
        )


class DeleteReport:
    """Render the outcome of a bucket delete as a one-line summary."""

    def __init__(self, outcome: DeleteOutcome) -> None:
        self._outcome = outcome

    def render(self) -> None:
        """Log each deleted bucket id, then the summary line."""
        for subscription_id in self._outcome.subscriptions:
            logger.info("Deleted subscription: %s", subscription_id)
        for agreement_id in self._outcome.agreements:
            logger.info("Deleted agreement: %s", agreement_id)
        logger.info(self._summary())

    def _summary(self) -> str:
        subscriptions = len(self._outcome.subscriptions)
        agreements = len(self._outcome.agreements)
        total = subscriptions + agreements
        return f"Deleted {total} bucket(s) ({subscriptions} subscription, {agreements} agreement)"


class BucketDeleter:
    """Delete stored accumulation buckets, one delete action per target kind.

    Product and seller scopes are resolved to their agreement ids through the scope
    resolver (an agreement belongs to one product and one seller, so deleting by
    ``agreement_id`` covers every subscription of each matched agreement). Deleting a
    subscription keeps its shared agreement bucket, since that bucket aggregates its
    sibling subscriptions.

    Each returned ``DeleteOutcome`` reports what was actually deleted and carries the
    target's ``statement_agreements`` re-selection targets.
    """

    def __init__(
        self,
        subscription_repo: SubscriptionAccumulationRepository,
        agreement_repo: AgreementAccumulationRepository,
        resolver: ScopeResolver,
        *,
        dry_run: bool = False,
    ) -> None:
        self._subscription_repo = subscription_repo
        self._agreement_repo = agreement_repo
        self._resolver = resolver
        self._dry_run = dry_run

    @trace_span(
        "usage_reporting.delete_subscription",
        attributes={
            "usage_reporting.subscription_id": lambda deleter, subscription_id: subscription_id,
        },
    )
    async def delete_subscription(self, subscription_id: str) -> DeleteOutcome:
        """Delete one subscription bucket, keeping its shared agreement bucket."""
        agreements = [
            agreement_id
            async for agreement_id in self._resolver.subscription_agreements(subscription_id)
        ]
        if self._dry_run:
            deleted = int(await self._has_subscription(subscription_id))
        else:
            deleted = await self._subscription_repo.delete(subscription_id=subscription_id)
        subscriptions = [subscription_id] if deleted else []
        return DeleteOutcome(
            subscriptions=subscriptions, statement_agreements=frozenset(agreements)
        )

    @trace_span(
        "usage_reporting.delete_agreement",
        attributes={"usage_reporting.agreement_id": lambda deleter, agreement_id: agreement_id},
    )
    async def delete_agreement(self, agreement_id: str) -> DeleteOutcome:
        """Delete the agreement bucket and every stored subscription bucket under it."""
        outcome = await DeleteOutcome.from_subscriptions(
            self._delete_subscription_ids(
                self._subscription_repo.subscriptions_by_agreement(agreement_id)
            )
        )
        if self._dry_run:
            agreement_deleted = 0
        else:
            agreement_deleted = await self._agreement_repo.delete(agreement_id=agreement_id)
        agreements = [agreement_id] if agreement_deleted > 0 else []
        return DeleteOutcome(
            subscriptions=outcome.subscriptions,
            agreements=agreements,
            statement_agreements=frozenset((agreement_id,)),
        )

    @trace_span(
        "usage_reporting.delete_agreements_by_query",
        attributes={"usage_reporting.query": lambda deleter, query: str(query)},
    )
    async def delete_agreements_by_query(self, query: RQLQuery) -> DeleteOutcome:
        """Delete the buckets of every agreement whose subscriptions match the query."""
        subscriptions: list[str] = []
        agreements: list[str] = []
        statement_agreements: set[str] = set()
        async for agreement_id in self._resolver.agreement_ids(query):
            outcome = await self.delete_agreement(agreement_id)
            subscriptions.extend(outcome.subscriptions)
            agreements.extend(outcome.agreements)
            statement_agreements |= outcome.statement_agreements
        return DeleteOutcome(
            subscriptions=subscriptions,
            agreements=agreements,
            statement_agreements=frozenset(statement_agreements),
        )

    async def delete_all(self) -> DeleteOutcome:
        """Delete every stored bucket in both tables."""
        outcome = await DeleteOutcome.from_subscriptions(
            self._delete_subscription_ids(self._subscription_repo.subscriptions_by_agreement())
        )
        if not self._dry_run:
            await self._agreement_repo.delete()
        return outcome

    async def _delete_subscription_ids(
        self, subscriptions: AsyncIterator[str]
    ) -> AsyncIterator[str]:
        async for subscription_id in subscriptions:
            if self._dry_run or await self._subscription_repo.delete(
                subscription_id=subscription_id
            ):
                yield subscription_id

    async def _has_subscription(self, subscription_id: str) -> bool:
        async for stored_subscription_id in self._subscription_repo.subscriptions_by_agreement():
            if stored_subscription_id == subscription_id:
                return True
        return False


class ScopeBucketDeleter:
    """Convert a selector scope into bucket delete actions and report the outcome.

    Dispatches each scope kind to the matching ``BucketDeleter`` action; a ``None``
    scope deletes every bucket in both tables.
    """

    def __init__(self, deleter: BucketDeleter, resolver: ScopeResolver) -> None:
        self._deleter = deleter
        self._resolver = resolver

    @trace_span(
        "usage_reporting.delete_buckets",
        attributes={"usage_reporting.scope": lambda scope_deleter, scope: type(scope).__name__},
    )
    async def delete(self, scope: Selector | None) -> DeleteOutcome:
        """Delete the scope's buckets from both tables, then report."""
        outcome = await self._delete_scope(scope)
        logger.info(
            "Deleted %d bucket(s)",
            len(outcome.subscriptions) + len(outcome.agreements),
        )
        DeleteReport(outcome).render()
        return outcome

    async def _delete_scope(self, scope: Selector | None) -> DeleteOutcome:
        if scope is None:
            return await self._deleter.delete_all()
        outcome: DeleteOutcome
        match scope:
            case SubscriptionSelector(subscription_id):
                outcome = await self._deleter.delete_subscription(subscription_id)
            case AgreementSelector(agreement_id):
                outcome = await self._deleter.delete_agreement(agreement_id)
            case ProductSelector() | SellerSelector():
                outcome = await self._deleter.delete_agreements_by_query(
                    self._resolver.query_for(scope)
                )
        return outcome
