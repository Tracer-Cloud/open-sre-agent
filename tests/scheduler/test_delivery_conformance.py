"""Which providers each delivery consumer actually supports, stated once.

``Provider`` offers six members to every caller, and no caller supports all
six. Each consumer picks a subset, and until now those subsets lived in prose:
the enum docstring says consumers "define their own documented subset rather
than exposing a choice that would silently fail".

This file is that documentation as an assertion. Each expectation below is
compared for exact equality against what the code can actually reach, so a new
provider, a removed branch, or a consumer quietly widening its support fails
here rather than at a user's delivery time.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from infrastructure.scheduling.scheduler import delivery, executor
from infrastructure.scheduling.scheduler.types import Provider
from tools.system.watch_dog import runner
from tools.system.watch_dog.config import WATCHDOG_SUPPORTED_PROVIDERS

#: Every member the vocabulary offers a caller.
ALL_PROVIDERS = frozenset(Provider)

#: What ``executor._deliver_single`` has an explicit branch for. Anything else
#: reaches the ``else`` and fails the task with "Unsupported provider".
EXECUTOR_DELIVERS = frozenset(
    {
        Provider.TELEGRAM,
        Provider.SLACK,
        Provider.DISCORD,
        Provider.ROCKETCHAT,
        Provider.INTERACTIVE_SHELL,
    }
)

#: What ``delivery._DELIVERY_SPECS`` knows how to check readiness and print
#: setup hints for. Narrower than what the executor can send to.
DELIVERY_SPECS_COVER = frozenset({Provider.TELEGRAM, Provider.SLACK, Provider.ROCKETCHAT})

#: What the watchdog's dispatcher distinguishes. Telegram is the documented
#: fallthrough, so it has no branch of its own.
WATCHDOG_BRANCHES = frozenset({Provider.ROCKETCHAT, Provider.BUZZ})


def _providers_branched_on(function: object) -> frozenset[Provider]:
    """``Provider.MEMBER`` values compared in ``if`` / ``elif`` tests.

    Mentions used only for logging, validation, or diagnostics are ignored:
    a reference is not a delivery branch.
    """
    source = textwrap.dedent(inspect.getsource(function))
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        for sub in ast.walk(node.test):
            if (
                isinstance(sub, ast.Attribute)
                and isinstance(sub.value, ast.Name)
                and sub.value.id == "Provider"
                and sub.attr in Provider.__members__
            ):
                names.add(sub.attr)
    return frozenset(Provider[name] for name in names)


def test_the_executor_delivers_to_exactly_these_providers() -> None:
    """The send path's branches are the real answer to "can this be delivered?"."""
    # Arrange / Act
    reachable = _providers_branched_on(executor._deliver_single)

    # Assert
    assert reachable == EXECUTOR_DELIVERS


def test_buzz_is_offered_by_the_vocabulary_and_refused_by_the_executor() -> None:
    """A scheduled task set to ``buzz`` fails at delivery, not at creation.

    ``Provider.BUZZ`` exists because the watchdog delivers to it. Cron delivery
    does not, so the enum offers a cron task a choice its executor will refuse
    with "Unsupported provider". Pinned so the gap stays visible; delete this
    test when the executor grows a buzz branch or the vocabularies split.
    """
    # Assert
    assert Provider.BUZZ in ALL_PROVIDERS
    assert Provider.BUZZ not in EXECUTOR_DELIVERS


def test_the_spec_list_is_narrower_than_what_the_executor_can_send_to() -> None:
    """Readiness and setup hints cover fewer providers than delivery reaches.

    ``interactive_shell`` is excluded on purpose — it is a local inbox, not a
    chat/webhook channel, so digest CLIs must not offer it. ``discord`` is not:
    the executor delivers to it while ``any_delivery_ready`` and the setup hints
    do not know it exists.
    """
    # Arrange / Act
    specs = frozenset(delivery.SUPPORTED_DELIVERY_PROVIDERS)

    # Assert
    assert specs == DELIVERY_SPECS_COVER
    assert Provider.INTERACTIVE_SHELL in EXECUTOR_DELIVERS - specs
    assert Provider.DISCORD in EXECUTOR_DELIVERS - specs


def test_the_watchdog_declares_what_its_dispatcher_can_build() -> None:
    """Declared support must match the dispatcher, or a provider routes silently.

    ``_build_dispatcher`` branches on Rocket.Chat and Buzz and falls through to
    Telegram. Adding a member to ``WATCHDOG_SUPPORTED_PROVIDERS`` without a
    branch would not raise — it would deliver that alarm to Telegram.
    """
    # Arrange / Act
    declared = frozenset(WATCHDOG_SUPPORTED_PROVIDERS)
    branched = _providers_branched_on(runner._build_dispatcher)

    # Assert
    assert branched == WATCHDOG_BRANCHES
    assert declared == branched | {Provider.TELEGRAM}
