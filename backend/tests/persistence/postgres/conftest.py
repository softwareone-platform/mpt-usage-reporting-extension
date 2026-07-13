from decimal import Decimal
from types import MappingProxyType

import pytest

from mpt_usage_reporting_extension.persistence.models import Charge


@pytest.fixture
def year():
    return 2026


@pytest.fixture
def prev_year():
    return 2025


@pytest.fixture
def month():
    return 5


@pytest.fixture
def last_month():
    return 12


@pytest.fixture
def first_month():
    return 1


@pytest.fixture
def other_month():
    return 6


@pytest.fixture
def subscription_id():
    return "SUB-1234-5678"


@pytest.fixture
def agreement_id():
    return "AGR-1234-5678"


@pytest.fixture
def decimal_zero():
    return Decimal(0)


@pytest.fixture
def decimal_ppx1():
    return Decimal("1543.13")


@pytest.fixture
def decimal_spx1():
    return Decimal("1697.45")


@pytest.fixture
def decimal_first():
    return Decimal("0.1")


@pytest.fixture
def decimal_second():
    return Decimal("0.2")


@pytest.fixture
def decimal_total():
    return Decimal("0.3")


@pytest.fixture
def sub_key(subscription_id, year, month):
    return MappingProxyType({
        "subscription_id": subscription_id,
        "year": year,
        "month": month,
    })


@pytest.fixture
def charge_factory(subscription_id, agreement_id, year, month):
    def factory(ppx1, spx1, **overrides):
        fields: dict[str, object] = {
            "subscription_id": subscription_id,
            "agreement_id": agreement_id,
            "year": year,
            "month": month,
            "ppx1": ppx1,
            "spx1": spx1,
        }
        fields.update(overrides)
        return Charge(**fields)  # type: ignore[arg-type]

    return factory


@pytest.fixture
def subscription_repo(db):
    return db.subscription_repository()
