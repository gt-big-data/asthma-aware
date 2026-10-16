"""Shared fixtures and history builders.

Every test runs against an in-memory store. The autouse fixture is the
important one: without it the tests would seed and rewrite
``app/data/wearable_store.json``, so running them would quietly replace
whatever a developer had synced locally.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, Iterable, Mapping, Optional

import pytest

from app.services import demo_history, ingest_service, sample_store

# A fixed "now" for tests that need one. 14:00 UTC is 10:00 EDT, an
# ordinary non-DST-change day, so the local date is unambiguous.
PINNED_NOW = dt.datetime(2026, 10, 15, 14, 0, tzinfo=dt.timezone.utc)
PINNED_TODAY = dt.date(2026, 10, 15)

TEST_USER = "test-user"


@pytest.fixture(autouse=True)
def isolated_store():
    """Give each test an empty in-memory store; never touch the real file."""
    sample_store.reset_store(history={})
    yield
    sample_store.reset_store(history={})


@pytest.fixture
def store_with(request):
    """Factory: load ``{user_id: {metric: {date: value}}}`` into the store."""

    def _load(history: Mapping[str, Mapping[str, Mapping[dt.date, float]]]):
        return sample_store.reset_store(history=history)

    return _load


@pytest.fixture
def demo_user_store():
    """The demo user's generated history, anchored on ``PINNED_TODAY``."""
    sample_store.reset_store(
        history={
            demo_history.DEMO_USER_ID: demo_history.generate_history(PINNED_TODAY)
        }
    )
    return demo_history.DEMO_USER_ID


@pytest.fixture
def live_demo_store():
    """The demo user's history anchored on the real current date.

    The endpoints read the clock themselves -- they take no ``now``
    argument, because nothing in production would pass one. So an API test
    has to seed history that is current *whenever the suite runs*, or it
    would pass today and fail tomorrow. Anchoring on
    ``ingest_service.today_local()`` rather than ``date.today()`` uses the
    service's own notion of today, which is the Atlanta calendar date.
    """
    today = ingest_service.today_local()
    sample_store.reset_store(
        history={demo_history.DEMO_USER_ID: demo_history.generate_history(today)}
    )
    return today


# --- history builders -----------------------------------------------------


def series(
    values: Iterable[float],
    end: dt.date = PINNED_TODAY,
) -> Dict[dt.date, float]:
    """A daily series ending on ``end``, oldest value first.

    ``series([1, 2, 3])`` is three consecutive days with 3 on ``end``.
    """
    values = list(values)
    return {
        end - dt.timedelta(days=len(values) - 1 - index): float(value)
        for index, value in enumerate(values)
    }


def flat_series(
    value: float,
    days: int,
    end: dt.date = PINNED_TODAY,
    last: Optional[float] = None,
) -> Dict[dt.date, float]:
    """``days`` days of ``value``, optionally with a different final day."""
    result = series([value] * days, end=end)
    if last is not None:
        result[end] = float(last)
    return result
