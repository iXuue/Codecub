import threading

import pytest

from codecub.spine import (
    DeliveryBackpressure,
    DeliveryError,
    DeliveryHub,
    DeliveryMessage,
)


class FlakyOutlet:
    def __init__(self, failures=0):
        self.failures = failures
        self.events = []

    def deliver(self, message):
        self.events.append(message.event_type)
        if self.failures:
            self.failures -= 1
            raise RuntimeError("temporary outlet failure")


def test_delivery_hub_serializes_events_and_retries_terminal_outlet_failures():
    outlet = FlakyOutlet(failures=1)
    hub = DeliveryHub(
        {"app": outlet},
        max_queue_size=4,
        max_retries=1,
        retry_base_seconds=0,
    )
    first = hub.publish(DeliveryMessage("app", "one"))
    second = hub.publish(DeliveryMessage("app", "two"))
    hub.close()

    assert first.attempts == 2
    assert second.attempts == 1
    assert outlet.events == ["one", "one", "two"]
    assert hub.stats()["app"] == {
        "published": 2,
        "delivered": 2,
        "retried": 1,
        "failed": 0,
        "rejected": 0,
    }
    with pytest.raises(DeliveryError, match="closed"):
        hub.publish(DeliveryMessage("app", "after-close"))


def test_delivery_hub_reports_bounded_channel_backpressure():
    started = threading.Event()
    release = threading.Event()

    class BlockingOutlet:
        def deliver(self, message):
            if message.event_type == "one":
                started.set()
                release.wait(1)

    hub = DeliveryHub(
        {"app": BlockingOutlet()},
        max_queue_size=1,
        max_retries=0,
    )
    first = hub.publish(DeliveryMessage("app", "one"), wait=False)
    assert started.wait(1)
    hub.publish(DeliveryMessage("app", "two"), wait=False)
    with pytest.raises(DeliveryBackpressure):
        hub.publish(DeliveryMessage("app", "three"), wait=False)
    release.set()
    first.result(timeout=1)
    hub.close()
    assert hub.stats()["app"]["rejected"] == 1
