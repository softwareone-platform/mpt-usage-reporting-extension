from dataclasses import dataclass

import typer


@dataclass(frozen=True, slots=True)
class ProductSelector:
    """Target every stored subscription of one product."""

    product_id: str


@dataclass(frozen=True, slots=True)
class SellerSelector:
    """Target every stored subscription of one seller."""

    seller_id: str


@dataclass(frozen=True, slots=True)
class SubscriptionSelector:
    """Target one stored subscription."""

    subscription_id: str


@dataclass(frozen=True, slots=True)
class AgreementSelector:
    """Target every stored subscription of one agreement."""

    agreement_id: str


Selector = ProductSelector | SellerSelector | SubscriptionSelector | AgreementSelector

_EXACTLY_ONE = "Use exactly one of --product-id / --agreement-id / --subscription-id / --seller-id."
_AT_MOST_ONE = "Use at most one of --product-id / --agreement-id / --subscription-id / --seller-id."


def _selectors(
    *,
    product_id: str | None,
    agreement_id: str | None,
    subscription_id: str | None,
    seller_id: str | None,
) -> list[Selector]:
    factories = (
        (product_id, ProductSelector),
        (agreement_id, AgreementSelector),
        (subscription_id, SubscriptionSelector),
        (seller_id, SellerSelector),
    )
    return [build(flag) for flag, build in factories if flag is not None]


def build_selector(
    *,
    product_id: str | None,
    agreement_id: str | None,
    subscription_id: str | None,
    seller_id: str | None,
) -> Selector:
    """Build the single target selector from the flags, or fail when not exactly one is set."""
    selectors = _selectors(
        product_id=product_id,
        agreement_id=agreement_id,
        subscription_id=subscription_id,
        seller_id=seller_id,
    )
    if len(selectors) != 1:
        raise typer.BadParameter(_EXACTLY_ONE)
    return selectors[0]


def build_optional_selector(
    *,
    product_id: str | None,
    agreement_id: str | None,
    subscription_id: str | None,
    seller_id: str | None,
) -> Selector | None:
    """Build at most one selector; return None when no flag is set (whole-scope run)."""
    selectors = _selectors(
        product_id=product_id,
        agreement_id=agreement_id,
        subscription_id=subscription_id,
        seller_id=seller_id,
    )
    if len(selectors) > 1:
        raise typer.BadParameter(_AT_MOST_ONE)
    return selectors[0] if selectors else None
