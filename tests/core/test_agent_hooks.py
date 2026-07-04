"""Unit tests for ProviderHookDelegate: the fail-open ProviderHooks wrapper."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from core.agent.provider_hooks import ProviderHookDelegate
from core.messages import MessageFormatter, UserRuntimeMessage
from core.provider import ProviderHooks, ProviderRequest


def _request() -> ProviderRequest:
    return ProviderRequest(messages=[], system="sys", tools=None)


def test_transform_messages_passes_through_by_default() -> None:
    delegate = ProviderHookDelegate(ProviderHooks())
    messages = [UserRuntimeMessage(content="hi")]
    assert delegate.transform_messages(messages) == messages


def test_transform_messages_applies_hook() -> None:
    extra = UserRuntimeMessage(content="injected")
    hooks = ProviderHooks(transform_messages=lambda messages: [*messages, extra])
    delegate = ProviderHookDelegate(hooks)
    result = delegate.transform_messages([UserRuntimeMessage(content="hi")])
    assert result[-1] is extra


def test_transform_messages_swallows_hook_exception() -> None:
    def boom(messages: Any) -> Any:
        raise RuntimeError("hook broke")

    delegate = ProviderHookDelegate(ProviderHooks(transform_messages=boom))
    messages = [UserRuntimeMessage(content="hi")]
    assert delegate.transform_messages(messages) == messages


def test_convert_to_llm_falls_back_to_message_formatter() -> None:
    delegate = ProviderHookDelegate(ProviderHooks())
    message = UserRuntimeMessage(content="hi")
    result = delegate.convert_to_llm(object(), [message])
    assert result == MessageFormatter(object()).to_provider_messages([message])


def test_convert_to_llm_swallows_hook_exception() -> None:
    def boom(llm: Any, messages: Any) -> Any:
        raise RuntimeError("hook broke")

    delegate = ProviderHookDelegate(ProviderHooks(convert_to_llm=boom))
    message = UserRuntimeMessage(content="hi")
    result = delegate.convert_to_llm(object(), [message])
    assert result == MessageFormatter(object()).to_provider_messages([message])


def test_before_request_passes_through_by_default() -> None:
    delegate = ProviderHookDelegate(ProviderHooks())
    request = _request()
    assert delegate.before_request(request) is request


def test_before_request_applies_hook() -> None:
    hooks = ProviderHooks(
        before_provider_request=lambda request: replace(request, system=request.system + " [x]")
    )
    delegate = ProviderHookDelegate(hooks)
    result = delegate.before_request(_request())
    assert result.system == "sys [x]"


def test_before_request_swallows_hook_exception() -> None:
    def boom(request: ProviderRequest) -> ProviderRequest:
        raise RuntimeError("hook broke")

    delegate = ProviderHookDelegate(ProviderHooks(before_provider_request=boom))
    request = _request()
    assert delegate.before_request(request) is request


def test_after_response_passes_through_by_default() -> None:
    delegate = ProviderHookDelegate(ProviderHooks())
    response = object()
    assert delegate.after_response(_request(), response) is response


def test_after_response_applies_hook() -> None:
    hooks = ProviderHooks(after_provider_response=lambda _request, _response: "edited")
    delegate = ProviderHookDelegate(hooks)
    assert delegate.after_response(_request(), "original") == "edited"


def test_after_response_swallows_hook_exception() -> None:
    def boom(request: ProviderRequest, response: Any) -> Any:
        raise RuntimeError("hook broke")

    delegate = ProviderHookDelegate(ProviderHooks(after_provider_response=boom))
    response = object()
    assert delegate.after_response(_request(), response) is response


def test_delegate_stores_the_wrapped_hooks() -> None:
    hooks = ProviderHooks()
    delegate = ProviderHookDelegate(hooks)
    assert delegate.hooks is hooks
