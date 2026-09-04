from codecub.channels import ChannelRegistry, InboundMessage, LoopbackChannel, OutboundMessage
from codecub.spine import Origin


def test_channel_registry_uses_bounded_delivery_and_routes_inbound_to_spine_submitter():
    channel = LoopbackChannel()
    requests = []
    registry = ChannelRegistry(submit=requests.append)
    registry.register(channel).start()
    try:
        delivery = registry.publish(
            OutboundMessage("loopback", "conversation-1", "hello", reply_to="m0"),
        )
        assert delivery.attempts == 1
        assert channel.received[0].text == "hello"

        result = channel.inject(
            InboundMessage(
                "loopback",
                "conversation-1",
                "please inspect",
                sender_id="user-1",
                message_id="m1",
            )
        )
        assert result is None
        assert requests[0].origin is Origin.USER
        assert requests[0].source.channel == "loopback"
        assert requests[0].source.sender_id == "user-1"
        assert requests[0].runtime_extensions["channel_message_id"] == "m1"
    finally:
        registry.close()


def test_channel_registry_rejects_unknown_outbound_channel():
    registry = ChannelRegistry()
    try:
        try:
            registry.publish(OutboundMessage("missing", "c", "text"))
        except KeyError as exc:
            assert "missing" in str(exc)
        else:
            raise AssertionError("unknown channel was accepted")
    finally:
        registry.close()
