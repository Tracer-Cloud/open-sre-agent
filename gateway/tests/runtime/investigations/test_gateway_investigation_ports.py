"""When a chat turn is offered ``investigation``, and what it does with it."""

from __future__ import annotations

from typing import Any

from gateway.core.chat import bound_delivery_target
from gateway.core.investigations.launch_ports import gateway_investigation_launch_ports
from gateway.core.runtime.capability_policy import (
    UNSUPPORTED_GATEWAY_CAPABILITIES,
    detached_investigations_available,
    ensure_gateway_capability_policy,
)


class _Session:
    """The one attribute the capability policy reads."""

    def __init__(self) -> None:
        self.available_capabilities: dict[str, Any] = {}


class TestCapabilityGate:
    def test_offered_when_a_notifier_can_deliver(
        self, delivery_target, notifier, register_notifier
    ):
        """The tool is only worth offering where the answer has somewhere to land."""
        register_notifier(notifier)
        session = _Session()

        with bound_delivery_target(delivery_target):
            assert detached_investigations_available() is True
            ensure_gateway_capability_policy(session)

        assert session.available_capabilities.get("investigation") != ()

    def test_withheld_when_the_platform_has_no_notifier(self, delivery_target):
        """Discord and Telegram register none.

        Gating on the notifier rather than a platform name is what stops them
        falling through to the synchronous pipeline this slice exists to avoid —
        a 277s run against a 240s turn budget.
        """
        session = _Session()

        with bound_delivery_target(delivery_target):
            assert detached_investigations_available() is False
            ensure_gateway_capability_policy(session)

        assert session.available_capabilities["investigation"] == ()

    def test_withheld_when_no_turn_is_bound(self, notifier, register_notifier):
        """The resolver runs before the delivery target is bound."""
        register_notifier(notifier)
        session = _Session()

        ensure_gateway_capability_policy(session)

        assert session.available_capabilities["investigation"] == ()

    def test_a_later_enable_clears_an_earlier_disable(
        self, delivery_target, notifier, register_notifier
    ):
        """Policy is applied twice per turn, resolver first.

        The second pass has to *remove* the first pass's disable, not merely skip
        adding one, or the capability could never switch on.
        """
        register_notifier(notifier)
        session = _Session()

        ensure_gateway_capability_policy(session)
        assert session.available_capabilities["investigation"] == ()

        with bound_delivery_target(delivery_target):
            ensure_gateway_capability_policy(session)

        assert "investigation" not in session.available_capabilities

    def test_the_unconditional_denials_survive_an_enable(
        self, delivery_target, notifier, register_notifier
    ):
        """Enabling investigation must not reopen the surfaces chat never gets."""
        register_notifier(notifier)
        session = _Session()

        with bound_delivery_target(delivery_target):
            ensure_gateway_capability_policy(session)

        for name in UNSUPPORTED_GATEWAY_CAPABILITIES:
            assert session.available_capabilities[name] == ()

    def test_a_session_without_capabilities_is_left_alone(self):
        """Tests inject bare session doubles; the policy must not raise on one."""

        class _Bare:
            pass

        ensure_gateway_capability_policy(_Bare())


class TestGatewayLaunchPorts:
    def test_background_mode_is_never_entered(self):
        """Background mode writes to a terminal that does not exist here."""
        ports = gateway_investigation_launch_ports()

        assert ports.background_mode_enabled(session=None) is False

    def test_a_text_request_is_queued_not_run(self, delivery_target, notifier, register_notifier):
        """The ports return a queued receipt; the pipeline runs elsewhere."""
        register_notifier(notifier)
        ports = gateway_investigation_launch_ports()

        with bound_delivery_target(delivery_target):
            result = ports.run_text_investigation(
                alert_text="checkout latency is up",
                context_overrides=None,
                cancel_requested=None,
                console=None,
            )

        assert result["status"] == "queued"
        assert result["investigation_id"]
        assert len(notifier.acks) == 1

    def test_an_unroutable_request_is_refused_without_a_record(self, delivery_target):
        """No notifier means refuse, rather than queue work nobody will read."""
        ports = gateway_investigation_launch_ports()

        with bound_delivery_target(delivery_target):
            result = ports.run_text_investigation(
                alert_text="checkout latency is up",
                context_overrides=None,
                cancel_requested=None,
                console=None,
            )

        assert result["status"] == "refused"
        assert result["investigation_id"] == ""
