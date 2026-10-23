"""Offline trusted anchors are test fixtures only; production has no bypass setting."""
from datetime import datetime, timezone

import pytest

from app import clock


@pytest.fixture(autouse=True)
def offline_business_clock(request, monkeypatch):
    if request.module.__name__ == 'test_clock':
        yield
        return
    reference = datetime.now(timezone.utc)
    instance = clock.TrustedClock(fetcher=lambda peer: clock.TimeSample(peer, reference, clock._monotonic()))
    instance.synchronize()
    monkeypatch.setattr(clock, 'CLOCK', instance)
    monkeypatch.setattr(clock, 'initialize', lambda: instance.ensure_writable())
    yield
    instance.shutdown()


def pytest_configure(config):
    config.addinivalue_line('markers', 'push_gate: exercise the real push freshness/closed/body-only gate')


@pytest.fixture(autouse=True)
def neutral_push_gate(request, monkeypatch):
    """Earlier delivery tests use fixed historical dates; the gate has dedicated tests."""
    if request.node.get_closest_marker('push_gate'):
        yield
        return
    from app import subscriptions
    monkeypatch.setattr(subscriptions, 'push_decision', lambda item, topic, decision=None, now=None:
                        {'push': True, 'reason': None} if decision is None or decision['allowed'] else {'push': False, 'reason': decision['reason']})
    yield
